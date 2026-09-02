use super::*;
use std::io::Read;
use std::os::unix::ffi::OsStrExt;
use std::process;
use std::sync::atomic::AtomicUsize;
use std::sync::mpsc;
use std::time::{Duration, Instant};

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn task_temp(_name: &str) -> PathBuf {
    let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
    let path = PathBuf::from(root).join(format!(
        "m-{}-{}",
        process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
    ));
    fs::create_dir(&path).unwrap();
    path
}

fn lifecycle_service(name: &str) -> (PathBuf, Arc<MotionService>) {
    let root = task_temp(name);
    let service = MotionService::with_sink(
        CameraPaths {
            prudynt_config: root.join("missing-prudynt.json"),
            recorder_ch0_active: root.join("run/prudynt/mp4ctl-ch0.active"),
            recorder_ch1_active: root.join("run/prudynt/mp4ctl-ch1.active"),
            motion_detected: root.join("run/prudynt/motion_detected.active"),
            motion_alarm: root.join("run/motion/motion_alarm"),
            motion_event_socket: root.join("run/control/motion.sock"),
            motion_clip_manifest: root.join("run/control/motion-clips-v1.json"),
            ..CameraPaths::default()
        },
        Arc::new(FakeSink::default()),
    );
    (root, service)
}

fn wait_for_lifecycle(service: &MotionService, expected: MotionLifecycleState) {
    let deadline = Instant::now() + Duration::from_secs(2);
    while lifecycle_state(service) != expected {
        assert!(
            Instant::now() < deadline,
            "motion lifecycle did not reach {expected:?}"
        );
        thread::sleep(Duration::from_millis(5));
    }
}

fn lifecycle_state(service: &MotionService) -> MotionLifecycleState {
    service
        .lifecycle
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .state
}

#[derive(Debug, Default)]
struct FakeSink {
    events: Mutex<Vec<MotionEvent>>,
}

impl MotionEventSink for FakeSink {
    fn try_send_motion(&self, event: MotionEvent) -> MotionEventDisposition {
        self.events
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .push(event);
        MotionEventDisposition::Queued
    }
}

fn fixture_u64(value: &Value, path: &str) -> u64 {
    value.get_path(path).and_then(number_u64).unwrap()
}

fn fixture_config(value: &Value) -> MotionConfig {
    MotionConfig {
        debounce_samples: fixture_u64(value, "config.debounce_samples"),
        cooldown_ms: fixture_u64(value, "config.cooldown_ms"),
        init_ms: fixture_u64(value, "config.init_ms"),
        min_active_ms: fixture_u64(value, "config.min_active_ms"),
        post_ms: fixture_u64(value, "config.post_ms"),
    }
}

fn fixture_observation(value: &Value) -> MotionObservation {
    MotionObservation {
        version: 1,
        state: match value.get_path("state").and_then(Value::as_str).unwrap() {
            "monitoring" => ObservationState::Monitoring,
            "detected" => ObservationState::Detected,
            "clear" => ObservationState::Clear,
            "suppressed" => ObservationState::Suppressed,
            "stopped" => ObservationState::Stopped,
            state => panic!("unknown fixture state {state}"),
        },
        channel: 0,
        monotonic_ms: fixture_u64(value, "at_ms"),
        monitoring_since_ms: fixture_u64(value, "monitoring_since_ms"),
        sequence: fixture_u64(value, "sequence"),
        producer_pid: 100,
        roi_mask: fixture_u64(value, "roi_mask"),
        initial_grace: value
            .get_path("initial_grace")
            .and_then(Value::as_bool)
            .unwrap_or(true),
    }
}

#[test]
fn machine_matches_locked_motion_semantics_fixture() {
    let fixture = json::parse(include_bytes!(
        "../../fixtures/motion-state-machine-v1.json"
    ))
    .unwrap();
    assert_eq!(fixture_u64(&fixture, "schema_version"), 1);
    for case in fixture.get_path("cases").and_then(Value::as_array).unwrap() {
        let name = case.get_path("name").and_then(Value::as_str).unwrap();
        let recovered_active = case
            .get_path("recovered_active")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let mut machine = MotionStateMachine::new(fixture_config(case), recovered_active);
        let actual: Vec<(u64, &'static str)> = case
            .get_path("observations")
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .filter_map(|value| {
                let observation = fixture_observation(value);
                machine.apply(observation).map(|transition| {
                    (
                        observation.monotonic_ms,
                        match transition {
                            MotionTransition::Active => "active",
                            MotionTransition::Inactive => "inactive",
                        },
                    )
                })
            })
            .collect();
        let expected: Vec<(u64, &str)> = case
            .get_path("expected_transitions")
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .map(|value| {
                (
                    fixture_u64(value, "at_ms"),
                    value.get_path("state").and_then(Value::as_str).unwrap(),
                )
            })
            .collect();
        assert_eq!(actual, expected, "fixture case {name}");
    }
}

#[test]
fn ingress_parser_is_versioned_strict_and_bounded() {
    let valid = br#"{"version":1,"event":"motion","state":"detected","channel":1,"monotonic_ms":123456,"monitoring_since_ms":120000,"sequence":7,"producer_pid":42,"roi_mask":3,"initial_grace":false}"#;
    let parsed = parse_observation(valid).unwrap();
    assert_eq!(parsed.version, PROTOCOL_VERSION);
    assert_eq!(parsed.state, ObservationState::Detected);
    assert_eq!(parsed.channel, 1);
    assert_eq!(parsed.monotonic_ms, 123_456);
    assert_eq!(parsed.monitoring_since_ms, 120_000);
    assert_eq!(parsed.sequence, 7);
    assert_eq!(parsed.producer_pid, 42);
    assert_eq!(parsed.roi_mask, 3);
    assert!(!parsed.initial_grace);

    let mut at_limit = valid.to_vec();
    at_limit.resize(MAX_INGRESS_BYTES, b' ');
    assert_eq!(at_limit.len(), MAX_INGRESS_BYTES);
    assert!(parse_observation(&at_limit).is_ok());

    assert!(parse_observation(b"").is_err());
    assert!(parse_observation(&valid[..valid.len() - 1]).is_err());
    let mut trailing = valid.to_vec();
    trailing.push(b'x');
    assert!(parse_observation(&trailing).is_err());
    assert!(parse_observation(br#"{"version":1,"event":"motion","state":"detected","channel":1,"monotonic_ms":123456,"monitoring_since_ms":120000,"sequence":7,"producer_pid":42,"roi_mask":3,"initial_grace":false,"extra":true}"#).is_err());
    assert!(parse_observation(br#"{"version":1,"event":"noise","state":"detected","channel":1,"monotonic_ms":123456,"monitoring_since_ms":120000,"sequence":7,"producer_pid":42,"roi_mask":3,"initial_grace":false}"#).is_err());
    assert!(parse_observation(br#"{"version":2,"event":"motion","state":"detected","channel":1,"monotonic_ms":123456,"monitoring_since_ms":120000,"sequence":7,"producer_pid":42,"roi_mask":3,"initial_grace":false}"#).is_err());
    assert!(parse_observation(&vec![b'x'; MAX_INGRESS_BYTES + 1]).is_err());
}

#[test]
fn ingress_parser_enforces_numeric_edges_and_state_roi_consistency() {
    let maximum = format!(
        r#"{{"version":1,"event":"motion","state":"detected","channel":1,"monotonic_ms":{},"monitoring_since_ms":{},"sequence":{},"producer_pid":{},"roi_mask":{},"initial_grace":true}}"#,
        u64::MAX,
        u64::MAX,
        u64::MAX,
        u32::MAX,
        u64::MAX
    );
    let parsed = parse_observation(maximum.as_bytes()).unwrap();
    assert_eq!(parsed.monotonic_ms, u64::MAX);
    assert_eq!(parsed.monitoring_since_ms, u64::MAX);
    assert_eq!(parsed.sequence, u64::MAX);
    assert_eq!(parsed.producer_pid, u32::MAX);
    assert_eq!(parsed.roi_mask, u64::MAX);
    assert!(parsed.initial_grace);

    let minimum = br#"{"version":1,"event":"motion","state":"clear","channel":0,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":1,"producer_pid":1,"roi_mask":0,"initial_grace":false}"#;
    assert!(parse_observation(minimum).is_ok());
    for invalid in [
        br#"{"version":1,"event":"motion","state":"detected","channel":2,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":1,"producer_pid":1,"roi_mask":1,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"detected","channel":0,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":0,"producer_pid":1,"roi_mask":1,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"detected","channel":0,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":1,"producer_pid":0,"roi_mask":1,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"detected","channel":0,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":1,"producer_pid":4294967296,"roi_mask":1,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"detected","channel":0,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":1,"producer_pid":1,"roi_mask":0,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"clear","channel":0,"monotonic_ms":0,"monitoring_since_ms":0,"sequence":1,"producer_pid":1,"roi_mask":1,"initial_grace":false}"#.as_slice(),
    ] {
        assert!(parse_observation(invalid).is_err(), "accepted {invalid:?}");
    }
}

#[test]
fn replayed_sequence_is_ignored_but_new_producer_session_is_accepted() {
    let mut machine = MotionStateMachine::new(
        MotionConfig {
            init_ms: 0,
            min_active_ms: 0,
            ..MotionConfig::default()
        },
        false,
    );
    let detected = MotionObservation {
        version: 1,
        state: ObservationState::Detected,
        channel: 0,
        monotonic_ms: 10,
        monitoring_since_ms: 1,
        sequence: 1,
        producer_pid: 10,
        roi_mask: 1,
        initial_grace: false,
    };
    assert_eq!(machine.apply(detected), Some(MotionTransition::Active));
    assert_eq!(machine.apply(detected), None);
    assert_eq!(machine.counters().duplicate_or_replayed, 1);
    let restarted = MotionObservation {
        producer_pid: 11,
        monitoring_since_ms: 20,
        monotonic_ms: 21,
        ..detected
    };
    assert_eq!(machine.apply(restarted), None);
    assert_eq!(machine.counters().producer_restarts, 1);
    assert!(machine.active());
    assert!(machine.monitoring());
}

#[test]
fn retiring_producer_ignores_old_datagrams_and_counts_one_new_session() {
    let mut machine = MotionStateMachine::new(
        MotionConfig {
            init_ms: 0,
            min_active_ms: 0,
            ..MotionConfig::default()
        },
        false,
    );
    let detected = MotionObservation {
        version: 1,
        state: ObservationState::Detected,
        channel: 0,
        monotonic_ms: 10,
        monitoring_since_ms: 1,
        sequence: 1,
        producer_pid: 10,
        roi_mask: 1,
        initial_grace: false,
    };
    assert_eq!(machine.apply(detected), Some(MotionTransition::Active));
    assert_eq!(
        machine.retire_producer(11),
        Some(MotionTransition::Inactive)
    );
    assert!(!machine.monitoring());
    assert!(!machine.active());

    let stale = MotionObservation {
        state: ObservationState::Monitoring,
        monotonic_ms: 12,
        sequence: 2,
        roi_mask: 0,
        ..detected
    };
    assert_eq!(machine.apply(stale), None);
    assert!(!machine.monitoring());
    assert_eq!(machine.counters().producer_restarts, 0);

    let restarted = MotionObservation {
        monitoring_since_ms: 20,
        monotonic_ms: 21,
        sequence: 1,
        ..stale
    };
    assert_eq!(machine.apply(restarted), None);
    assert!(machine.monitoring());
    assert_eq!(machine.counters().producer_restarts, 1);
}

#[test]
fn queue_is_bounded_and_only_coalesces_safe_level_observations() {
    let queue = MotionQueue::new(2);
    let observation = MotionObservation {
        version: 1,
        state: ObservationState::Detected,
        channel: 0,
        monotonic_ms: 1,
        monitoring_since_ms: 1,
        sequence: 1,
        producer_pid: 10,
        roi_mask: 1,
        initial_grace: false,
    };
    assert_eq!(queue.push(observation), QueuePushResult::Queued);
    assert_eq!(
        queue.push(MotionObservation {
            sequence: 2,
            monotonic_ms: 2,
            ..observation
        }),
        QueuePushResult::Queued
    );
    assert_eq!(
        queue.push(MotionObservation {
            sequence: 3,
            monotonic_ms: 3,
            ..observation
        }),
        QueuePushResult::Dropped
    );
    assert_eq!(queue.snapshot(), (2, 2, 1, 0));

    let clear_queue = MotionQueue::new(2);
    let clear = MotionObservation {
        state: ObservationState::Clear,
        roi_mask: 0,
        ..observation
    };
    assert_eq!(clear_queue.push(clear), QueuePushResult::Queued);
    assert_eq!(
        clear_queue.push(MotionObservation {
            sequence: 2,
            monotonic_ms: 99,
            ..clear
        }),
        QueuePushResult::Coalesced
    );
    assert_eq!(clear_queue.snapshot(), (1, 1, 0, 1));
    assert_eq!(clear_queue.pop().unwrap().monotonic_ms, 99);

    let ordered_queue = MotionQueue::new(3);
    assert_eq!(ordered_queue.push(clear), QueuePushResult::Queued);
    assert_eq!(
        ordered_queue.push(MotionObservation {
            sequence: 2,
            monotonic_ms: 2,
            ..observation
        }),
        QueuePushResult::Queued
    );
    assert_eq!(
        ordered_queue.push(MotionObservation {
            sequence: 3,
            monotonic_ms: 3,
            ..clear
        }),
        QueuePushResult::Queued
    );
    assert_eq!(ordered_queue.snapshot(), (3, 3, 0, 0));
    assert_eq!(ordered_queue.pop().unwrap().sequence, 1);
    assert_eq!(ordered_queue.pop().unwrap().sequence, 2);
    assert_eq!(ordered_queue.pop().unwrap().sequence, 3);
}

#[test]
fn queue_shutdown_drops_input_until_explicit_restart() {
    let queue = MotionQueue::new(2);
    let observation = MotionObservation {
        version: 1,
        state: ObservationState::Monitoring,
        channel: 0,
        monotonic_ms: 1,
        monitoring_since_ms: 1,
        sequence: 1,
        producer_pid: 10,
        roi_mask: 0,
        initial_grace: false,
    };

    assert_eq!(queue.push(observation), QueuePushResult::Queued);
    queue.shutdown();
    assert_eq!(queue.push(observation), QueuePushResult::Dropped);
    assert_eq!(queue.snapshot(), (0, 1, 1, 0));

    queue.reset_for_start();
    assert_eq!(queue.push(observation), QueuePushResult::Queued);
    assert_eq!(queue.pop(), Some(observation));
    assert_eq!(queue.snapshot(), (0, 1, 1, 0));
}

#[test]
fn second_spawn_failure_rolls_back_and_joins_receiver() {
    let (root, service) = lifecycle_service("spawn-rollback");
    service
        .test_hooks
        .fail_next_worker_spawn
        .store(true, Ordering::Release);

    let error = service.start().unwrap_err();

    assert!(
        error
            .to_string()
            .contains("injected motion worker spawn failure")
    );
    assert_eq!(lifecycle_state(&service), MotionLifecycleState::Stopped);
    assert!(!service.snapshot().ingress_ready);
    assert_eq!(Arc::strong_count(&service), 1);
    let lifecycle = service
        .lifecycle
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    assert!(lifecycle.receiver.is_none());
    assert!(lifecycle.worker.is_none());
    drop(lifecycle);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn fatal_receive_exit_stops_workers_and_allows_restart() {
    let (root, service) = lifecycle_service("fatal-receive");
    service
        .test_hooks
        .fail_next_receive
        .store(true, Ordering::Release);

    service.start().unwrap();
    wait_for_lifecycle(&service, MotionLifecycleState::Stopping);

    let runtime = service.snapshot();
    assert!(!runtime.ingress_ready);
    assert_eq!(runtime.ingress_fatal_exits, 1);
    let response = service.runtime_response().unwrap();
    let document = json::parse(&response.body).unwrap();
    assert_eq!(
        document
            .get_path("ingress_fatal_exits")
            .and_then(number_u64),
        Some(1)
    );
    service.start().unwrap();
    assert_eq!(lifecycle_state(&service), MotionLifecycleState::Running);
    assert!(service.snapshot().ingress_ready);
    service.shutdown().unwrap();
    assert_eq!(lifecycle_state(&service), MotionLifecycleState::Stopped);
    assert_eq!(Arc::strong_count(&service), 1);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn waiting_worker_shutdown_joins_threads_and_allows_restart() {
    let (root, service) = lifecycle_service("condvar-shutdown");
    let (waiting_sender, waiting_receiver) = mpsc::channel();
    service.queue.observe_next_wait(waiting_sender);

    service.start().unwrap();
    {
        let lifecycle = service
            .lifecycle
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        assert!(lifecycle.receiver.is_some());
        assert!(lifecycle.worker.is_some());
    }
    waiting_receiver
        .recv_timeout(Duration::from_secs(1))
        .expect("motion worker did not wait on the queue condvar");
    service.shutdown().unwrap();

    assert_eq!(lifecycle_state(&service), MotionLifecycleState::Stopped);
    assert!(!service.snapshot().ingress_ready);
    assert_eq!(Arc::strong_count(&service), 1);
    let lifecycle = service
        .lifecycle
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    assert!(lifecycle.receiver.is_none());
    assert!(lifecycle.worker.is_none());
    drop(lifecycle);

    service.start().unwrap();
    assert_eq!(lifecycle_state(&service), MotionLifecycleState::Running);
    service.shutdown().unwrap();
    assert_eq!(Arc::strong_count(&service), 1);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn datagram_ingress_drives_markers_runtime_and_neutral_sink() {
    let root = task_temp("ingress");
    let socket_path = root.join("run/control/motion.sock");
    let alarm = root.join("run/motion/motion_alarm");
    let detected = root.join("run/prudynt/motion_detected.active");
    let config = root.join("prudynt.json");
    fs::write(
        &config,
        br#"{"motion":{"debounce_time":0,"cooldown_time":1,"init_time":0,"min_time":0,"post_time":0,"playonspeaker":false}}"#,
    )
    .unwrap();
    let sink = Arc::new(FakeSink::default());
    let service = MotionService::with_sink(
        CameraPaths {
            prudynt_config: config,
            motion_event_socket: socket_path.clone(),
            motion_alarm: alarm.clone(),
            motion_detected: detected.clone(),
            ..CameraPaths::default()
        },
        sink.clone(),
    );
    service.start().unwrap();

    let sender = UnixDatagram::unbound().unwrap();
    sender.connect(&socket_path).unwrap();
    for message in [
        br#"{"version":1,"event":"motion","state":"monitoring","channel":0,"monotonic_ms":100,"monitoring_since_ms":100,"sequence":1,"producer_pid":42,"roi_mask":0,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"detected","channel":0,"monotonic_ms":101,"monitoring_since_ms":100,"sequence":2,"producer_pid":42,"roi_mask":1,"initial_grace":false}"#.as_slice(),
    ] {
        sender.send(message).unwrap();
    }
    let deadline = Instant::now() + Duration::from_secs(1);
    while (!alarm.is_file() || !detected.is_file()) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(alarm.is_file());
    assert!(detected.is_file());
    assert!(service.snapshot().active);

    sender
        .send(br#"{"version":1,"event":"motion","state":"clear","channel":0,"monotonic_ms":102,"monitoring_since_ms":100,"sequence":3,"producer_pid":42,"roi_mask":0,"initial_grace":false}"#)
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(1);
    while (alarm.exists() || detected.exists()) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(5));
    }
    assert!(!alarm.exists());
    assert!(!detected.exists());
    assert!(!service.snapshot().active);
    let events = sink
        .events
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    assert!(
        events
            .iter()
            .any(|event| event.state == MotionState::Active)
    );
    assert!(
        events
            .iter()
            .any(|event| event.state == MotionState::Inactive)
    );
    assert!(events.iter().all(MotionEvent::has_bounded_metadata));
    drop(events);
    service.shutdown().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn replacing_motion_sink_publishes_current_level_and_resync() {
    let root = task_temp("replace-sink");
    let alarm = root.join("run/motion/motion_alarm");
    fs::create_dir_all(alarm.parent().unwrap()).unwrap();
    fs::write(&alarm, b"active\n").unwrap();
    let service = MotionService::with_sink(
        CameraPaths {
            motion_alarm: alarm,
            prudynt_config: root.join("missing-prudynt.json"),
            ..CameraPaths::default()
        },
        Arc::new(FakeSink::default()),
    );
    let replacement = Arc::new(FakeSink::default());

    service.set_sink(replacement.clone());

    let events = replacement
        .events
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    assert_eq!(events.len(), 2);
    assert_eq!(events[0].state, MotionState::Active);
    assert_eq!(events[1].state, MotionState::Resync);
    drop(events);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn completed_clip_is_atomically_activated_and_sent_as_bounded_metadata() {
    let root = task_temp("clip-lifecycle");
    let partial = root.join(".motion-1.partial");
    let final_path = root.join("motion-1.mp4");
    fs::write(&partial, b"bounded-mp4-fixture").unwrap();
    let sink = Arc::new(FakeSink::default());
    let service = MotionService::with_sink(
        CameraPaths {
            prudynt_config: root.join("missing-prudynt.json"),
            recorder_ch0_active: root.join("inactive-recorder"),
            ..CameraPaths::default()
        },
        sink.clone(),
    );
    service
        .pending_clips
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .push_back(PendingClip {
            id: "motion-1".to_owned(),
            partial_path: partial.clone(),
            final_path: final_path.clone(),
            channel: 0,
            created_unix_ms: 1,
            deadline_unix_ms: u64::MAX,
        });

    service.poll_media_lifecycle();

    assert!(!partial.exists());
    assert_eq!(fs::read(&final_path).unwrap(), b"bounded-mp4-fixture");
    assert_eq!(service.snapshot().clips_ready, 1);
    let events = sink
        .events
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let artifact = events.last().and_then(|event| event.clip.as_ref()).unwrap();
    assert_eq!(artifact.local_path.as_deref(), Some(final_path.as_path()));
    assert_eq!(artifact.size_bytes, 19);
    assert!(events.last().unwrap().has_bounded_metadata());
    drop(events);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn control_restart_recovers_owned_partial_clip_from_bounded_manifest() {
    let root = task_temp("clip-restart");
    let partial = root.join(".motion-100-7.partial");
    let final_path = root.join("motion-100-7.mp4");
    let active = root.join("mp4ctl-ch0.active");
    let manifest = root.join("motion-clips-v1.json");
    fs::write(&partial, b"recoverable-mp4").unwrap();
    fs::write(
        &active,
        format!("path={}\nduration=10\n", partial.display()),
    )
    .unwrap();
    let paths = CameraPaths {
        prudynt_config: root.join("missing-prudynt.json"),
        recorder_ch0_active: active.clone(),
        motion_clip_manifest: manifest.clone(),
        ..CameraPaths::default()
    };
    let first = MotionService::with_sink(paths.clone(), Arc::new(FakeSink::default()));
    let clip = PendingClip {
        id: "motion-100-7".to_owned(),
        partial_path: partial.clone(),
        final_path: final_path.clone(),
        channel: 0,
        created_unix_ms: 100,
        deadline_unix_ms: unix_milliseconds().saturating_add(40_000),
    };
    first
        .persist_pending_clips(&VecDeque::from([clip]))
        .unwrap();
    assert!(manifest.is_file());

    let sink = Arc::new(FakeSink::default());
    let restarted = MotionService::with_sink(paths, sink.clone());
    restarted.recover_pending_clips_from(&root);
    assert_eq!(restarted.snapshot().clips_recovered, 1);
    assert_eq!(restarted.pending_clips.lock().unwrap().len(), 1);

    fs::remove_file(active).unwrap();
    restarted.poll_media_lifecycle();
    assert!(!partial.exists());
    assert!(!manifest.exists());
    assert_eq!(fs::read(&final_path).unwrap(), b"recoverable-mp4");
    assert_eq!(restarted.snapshot().clips_ready, 1);
    assert!(
        sink.events
            .lock()
            .unwrap()
            .last()
            .and_then(|event| event.clip.as_ref())
            .is_some()
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn speaker_action_writes_the_existing_audio_fifo_without_a_child() {
    let root = task_temp("speaker");
    let fifo = root.join("audio_out");
    let sound = root.join("alert.opus");
    let send = root.join("send2.json");
    fs::write(&sound, b"opus").unwrap();
    fs::write(
        &send,
        format!(
            "{{\"speaker\":{{\"file\":\"{}\",\"volume\":77,\"gain\":12,\"loop\":2}}}}",
            sound.display()
        ),
    )
    .unwrap();
    let fifo_c = std::ffi::CString::new(fifo.as_os_str().as_bytes()).unwrap();
    unsafe extern "C" {
        fn mkfifo(path: *const std::os::raw::c_char, mode: u32) -> std::os::raw::c_int;
    }
    // SAFETY: fifo_c is a valid NUL-terminated path inside the task temp directory.
    assert_eq!(unsafe { mkfifo(fifo_c.as_ptr(), 0o600) }, 0);
    let expected = format!("PLAY url={} vol=77 gain=12 loop=2\n", sound.display());
    let reader_path = fifo.clone();
    let expected_len = expected.len();
    let (ready_tx, ready_rx) = std::sync::mpsc::sync_channel(0);
    let reader = thread::spawn(move || {
        let mut fifo = OpenOptions::new()
            .read(true)
            .write(true)
            .open(reader_path)
            .unwrap();
        ready_tx.send(()).unwrap();
        let mut bytes = vec![0_u8; expected_len];
        fifo.read_exact(&mut bytes).unwrap();
        bytes
    });
    ready_rx.recv().unwrap();
    let service = MotionService::with_sink(
        CameraPaths {
            send2_config: send,
            audio_output: fifo,
            prudynt_config: root.join("missing-prudynt.json"),
            ..CameraPaths::default()
        },
        Arc::new(FakeSink::default()),
    );
    service.dispatch_speaker();
    let command = String::from_utf8(reader.join().unwrap()).unwrap();
    assert_eq!(command, expected);
    assert_eq!(service.snapshot().speaker_queued, 1);
    fs::remove_dir_all(root).unwrap();
}
