use super::*;
use crate::camera::motion_events::{
    MOTION_EVENT_VERSION, MediaArtifact, MediaArtifactKind, MotionEvent, MotionEventSink,
    MotionState,
};
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::AtomicUsize;

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn task_temp(name: &str) -> PathBuf {
    let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
    let path = PathBuf::from(root).join(format!(
        "ha-service-{}-{}-{name}",
        process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
    ));
    fs::create_dir(&path).unwrap();
    path
}

fn install_barrier(slot: &Mutex<Option<TestBarrier>>) -> (Receiver<()>, mpsc::Sender<()>) {
    let (reached, observed) = mpsc::channel();
    let (release, released) = mpsc::channel();
    *slot.lock().unwrap_or_else(|error| error.into_inner()) = Some(TestBarrier {
        reached,
        release: released,
    });
    (observed, release)
}

fn write_ha_config(path: &Path, enabled: bool, host: &str) {
    fs::write(
        path,
        format!("{{\"ha\":{{\"enabled\":{enabled},\"mqtt\":{{\"host\":\"{host}\",\"port\":1}}}}}}"),
    )
    .unwrap();
}

fn lifecycle_backend(
    name: &str,
    enabled: bool,
    host: &str,
) -> (PathBuf, PathBuf, Arc<PrudyntBackend>) {
    let root = task_temp(name);
    let config = root.join("thingino.json");
    let hostname = root.join("hostname");
    write_ha_config(&config, enabled, host);
    fs::write(&hostname, "camera\n").unwrap();
    let backend = Arc::new(PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        hostname,
        ..CameraPaths::default()
    }));
    (root, config, backend)
}

fn snapshot_fixture(socket: &Path) -> (Arc<AtomicBool>, thread::JoinHandle<()>) {
    let listener = UnixListener::bind(socket).unwrap();
    listener.set_nonblocking(true).unwrap();
    let running = Arc::new(AtomicBool::new(true));
    let server_running = Arc::clone(&running);
    let server = thread::spawn(move || {
        while server_running.load(Ordering::Acquire) {
            let (mut stream, _) = match listener.accept() {
                Ok(connection) => connection,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => panic!("snapshot fixture accept failed: {error}"),
            };
            let mut request = Vec::new();
            stream.read_to_end(&mut request).unwrap();
            let channel = match request.as_slice() {
                b"SNAPSHOT ch=0\n" => 0,
                b"SNAPSHOT ch=1\n" => 1,
                other => panic!("unexpected Prudynt request: {other:?}"),
            };
            let jpeg = [0xff, 0xd8, channel, 0xff, 0xd9];
            writeln!(stream, "OK {}", jpeg.len()).unwrap();
            stream.write_all(&jpeg).unwrap();
        }
    });
    (running, server)
}

#[test]
fn successful_command_clears_only_command_scoped_errors() {
    for error in [
        "camera command failed",
        "camera command state readback failed",
    ] {
        let mut runtime = RuntimeState {
            last_error: Some(error),
            ..RuntimeState::default()
        };
        clear_command_error(&mut runtime);
        assert_eq!(runtime.last_error, None);
    }

    let mut runtime = RuntimeState {
        last_error: Some("MQTT connection lost"),
        ..RuntimeState::default()
    };
    clear_command_error(&mut runtime);
    assert_eq!(runtime.last_error, Some("MQTT connection lost"));
}

fn motion_event(state: MotionState, sequence: u64) -> MotionEvent {
    MotionEvent {
        version: MOTION_EVENT_VERSION,
        sequence,
        state,
        channel: 0,
        monotonic_ms: sequence,
        occurred_unix_ms: None,
        snapshot: None,
        clip: None,
    }
}

fn motion_service() -> (HaService, Receiver<Request>) {
    let service = HaService::new();
    service.accept_motion.store(true, Ordering::Release);
    let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
    *service.worker.lock().unwrap() = Some(WorkerControl {
        launch_generation: 1,
        state: WorkerState::Running,
        sender: Some(sender),
        shutdown_requested: Arc::new(AtomicBool::new(false)),
        handle: None,
    });
    (service, receiver)
}

#[test]
fn reconnect_backoff_is_bounded_and_has_stable_jitter() {
    assert!(reconnect_delay("camera", 0) >= Duration::from_secs(1));
    assert_eq!(reconnect_delay("camera", 3), reconnect_delay("camera", 3));
    assert!(reconnect_delay("camera", 99) <= Duration::from_secs(60));
}

#[test]
fn publish_action_wakes_backoff_for_initial_republish() {
    let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
    let queue_depth = AtomicUsize::new(1);
    let generation = AtomicUsize::new(7);
    let shutdown_requested = AtomicBool::new(false);
    sender.send(Request::RepublishDiscovery).unwrap();
    assert!(matches!(
        wait_backoff(
            Instant::now() + Duration::from_secs(1),
            &receiver,
            &queue_depth,
            &generation,
            7,
            &shutdown_requested,
        ),
        Some(SessionEnd::Reconnect)
    ));
    assert_eq!(queue_depth.load(Ordering::Acquire), 0);
}

#[test]
fn action_queue_is_bounded_and_payloads_are_exact() {
    let service = HaService::new();
    let (sender, _receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
    *service.worker.lock().unwrap() = Some(WorkerControl {
        launch_generation: 1,
        state: WorkerState::Running,
        sender: Some(sender),
        shutdown_requested: Arc::new(AtomicBool::new(false)),
        handle: None,
    });
    for _ in 0..EVENT_QUEUE_CAPACITY {
        service.action(br#"{"action":"publish_state"}"#).unwrap();
    }
    assert_eq!(
        service.queue_depth.load(Ordering::Acquire),
        EVENT_QUEUE_CAPACITY
    );
    assert!(service.action(br#"{"action":"publish_state"}"#).is_err());
    for invalid in [
        br#"{}"#.as_slice(),
        br#"{"action":"unknown"}"#,
        br#"{"action":"reconnect","extra":true}"#,
    ] {
        assert!(service.action(invalid).is_err());
    }
}

#[test]
fn disabled_motion_sink_never_queues() {
    let service = HaService::new();
    let mut event = motion_event(MotionState::Active, 1);
    event.snapshot = Some(MediaArtifact {
        kind: MediaArtifactKind::Snapshot,
        id: "disabled".to_owned(),
        local_path: Some(PathBuf::from("/path/that/must/not/be-inspected")),
        content_type: "image/jpeg".to_owned(),
        size_bytes: 1,
        created_unix_ms: 1,
    });
    assert_eq!(
        service.try_send_motion(event),
        MotionEventDisposition::Disabled
    );
    assert_eq!(service.queue_depth.load(Ordering::Acquire), 0);
}

#[test]
fn invalid_motion_metadata_is_dropped() {
    let (service, receiver) = motion_service();
    let mut event = motion_event(MotionState::Active, 1);
    event.version = MOTION_EVENT_VERSION.saturating_add(1);
    assert_eq!(
        service.try_send_motion(event),
        MotionEventDisposition::Dropped
    );
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
    assert_eq!(service.queue_depth.load(Ordering::Acquire), 0);
}

#[test]
fn full_action_queue_drops_motion_without_overcommit() {
    let (service, _receiver) = motion_service();
    for _ in 0..EVENT_QUEUE_CAPACITY {
        service.enqueue(Request::PublishState).unwrap();
    }
    assert_eq!(
        service.try_send_motion(motion_event(MotionState::Active, 1)),
        MotionEventDisposition::Dropped
    );
    assert_eq!(
        service.queue_depth.load(Ordering::Acquire),
        EVENT_QUEUE_CAPACITY
    );
    assert_eq!(service.motion_slot.take_next(), None);
}

#[test]
fn active_then_inactive_coalesces_to_one_motion_wakeup() {
    let (service, receiver) = motion_service();
    assert_eq!(
        service.try_send_motion(motion_event(MotionState::Active, 1)),
        MotionEventDisposition::Queued
    );
    assert_eq!(
        service.try_send_motion(motion_event(MotionState::Inactive, 2)),
        MotionEventDisposition::Coalesced
    );
    assert_eq!(receiver.try_recv(), Ok(Request::Motion));
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
    assert_eq!(
        service.motion_slot.take_next(),
        Some(MotionUpdate {
            state: MotionState::Inactive
        })
    );
    assert_eq!(service.motion_slot.take_next(), None);
}

#[test]
fn generation_change_drains_a_stale_motion_wakeup() {
    let (service, receiver) = motion_service();
    assert_eq!(
        service.try_send_motion(motion_event(MotionState::Active, 1)),
        MotionEventDisposition::Queued
    );
    {
        let _worker = service.worker.lock().unwrap();
        service.accept_motion.store(false, Ordering::Release);
        service.motion_slot.reset();
        service.config_generation.store(2, Ordering::Release);
    }
    let mut requests = SessionRequests::default();
    let shutdown_requested = AtomicBool::new(false);
    assert!(matches!(
        session_control(
            &receiver,
            &service.queue_depth,
            &service.config_generation,
            1,
            &shutdown_requested,
            Some(&mut requests),
        ),
        Some(SessionEnd::Reconfigure(2))
    ));
    assert_eq!(service.queue_depth.load(Ordering::Acquire), 0);
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
    assert_eq!(
        service.try_send_motion(motion_event(MotionState::Inactive, 2)),
        MotionEventDisposition::Disabled
    );
}

#[test]
fn adapter_discards_artifact_path_without_opening_it() {
    let root = task_temp("artifact-path");
    let path = root.join("motion.jpg");
    fs::write(&path, b"bounded jpeg metadata").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).unwrap();
    let (service, _receiver) = motion_service();
    let mut event = motion_event(MotionState::Active, 1);
    event.snapshot = Some(MediaArtifact {
        kind: MediaArtifactKind::Snapshot,
        id: "motion-1".to_owned(),
        local_path: Some(path.clone()),
        content_type: "image/jpeg".to_owned(),
        size_bytes: fs::symlink_metadata(&path).unwrap().len(),
        created_unix_ms: 1,
    });
    assert_eq!(
        service.try_send_motion(event),
        MotionEventDisposition::Queued
    );
    fs::remove_file(&path).unwrap();
    assert_eq!(
        service.motion_slot.take_next(),
        Some(MotionUpdate {
            state: MotionState::Active
        })
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn worker_spawn_failure_is_a_restartable_runtime_error() {
    let (root, _config, backend) = lifecycle_backend("spawn-failure", true, "127.0.0.1");
    backend
        .ha
        .test_hooks
        .fail_next_spawn
        .store(true, Ordering::Release);

    backend.start_ha();

    assert!(backend.ha.worker.lock().unwrap().is_none());
    assert!(!backend.ha.accept_motion.load(Ordering::Acquire));
    assert_eq!(backend.ha.queue_depth.load(Ordering::Acquire), 0);
    let runtime = backend.ha.runtime.lock().unwrap().clone();
    assert_eq!(runtime.state, "error");
    assert_eq!(runtime.last_error, Some("HA worker spawn failed"));

    let (started, release) = install_barrier(&backend.ha.test_hooks.start_barrier);
    backend.ha.reconfigure();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not restart after the injected spawn failure");
    assert!(
        backend
            .ha
            .worker
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|worker| worker.state == WorkerState::Running && worker.handle.is_some())
    );
    release.send(()).unwrap();
    backend.shutdown_ha().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn worker_exit_serializes_before_enqueue_can_observe_the_dead_receiver() {
    let (root, _config, backend) = lifecycle_backend("exit-enqueue", true, "");
    let (exiting, release_exit) = install_barrier(&backend.ha.test_hooks.exit_barrier);
    backend.start_ha();
    exiting
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach the exit transition");

    let service = Arc::clone(&backend.ha);
    let (result_sender, result_receiver) = mpsc::channel();
    let enqueue = thread::spawn(move || {
        result_sender
            .send(service.action(br#"{"action":"publish_state"}"#).is_ok())
            .unwrap();
    });
    assert!(matches!(
        result_receiver.recv_timeout(Duration::from_millis(50)),
        Err(mpsc::RecvTimeoutError::Timeout)
    ));

    release_exit.send(()).unwrap();
    assert!(
        !result_receiver
            .recv_timeout(Duration::from_secs(1))
            .expect("enqueue remained blocked after the worker exit transition")
    );
    enqueue.join().unwrap();
    backend.shutdown_ha().unwrap();
    assert_eq!(backend.ha.queue_depth.load(Ordering::Acquire), 0);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn reconfigure_wins_the_generation_exit_race() {
    let (root, config, backend) = lifecycle_backend("generation-exit", true, "");
    let (pre_exit, release_exit) = install_barrier(&backend.ha.test_hooks.pre_exit_barrier);
    backend.start_ha();
    pre_exit
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its pre-exit barrier");

    let depth_before_action = backend.ha.queue_depth.load(Ordering::Acquire);
    backend.ha.action(br#"{"action":"publish_state"}"#).unwrap();
    assert_eq!(
        backend.ha.queue_depth.load(Ordering::Acquire),
        depth_before_action + 1
    );
    let previous_generation = backend.ha.config_generation.load(Ordering::Acquire);
    write_ha_config(&config, true, "127.0.0.1");
    let (continued, release_continue) = install_barrier(&backend.ha.test_hooks.start_barrier);
    backend.ha.reconfigure();
    assert!(backend.ha.config_generation.load(Ordering::Acquire) > previous_generation);
    release_exit.send(()).unwrap();
    continued
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker exited instead of observing the newer generation");
    assert_eq!(backend.ha.queue_depth.load(Ordering::Acquire), 0);
    assert!(
        backend
            .ha
            .worker
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|worker| worker.state == WorkerState::Running && worker.sender.is_some())
    );

    release_continue.send(()).unwrap();
    backend.shutdown_ha().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn reconfigure_generation_drains_depth_before_joined_stop() {
    let (root, config, backend) = lifecycle_backend("generation-drain", true, "127.0.0.1");
    let (started, release_start) = install_barrier(&backend.ha.test_hooks.start_barrier);
    let (exiting, release_exit) = install_barrier(&backend.ha.test_hooks.exit_barrier);
    backend.start_ha();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its start barrier");

    let initial_depth = backend.ha.queue_depth.load(Ordering::Acquire);
    for _ in 0..3 {
        backend.ha.action(br#"{"action":"publish_state"}"#).unwrap();
    }
    assert_eq!(
        backend.ha.queue_depth.load(Ordering::Acquire),
        initial_depth + 3
    );
    let previous_generation = backend.ha.config_generation.load(Ordering::Acquire);
    write_ha_config(&config, false, "127.0.0.1");
    backend.ha.reconfigure();
    assert!(backend.ha.config_generation.load(Ordering::Acquire) > previous_generation);
    release_start.send(()).unwrap();
    exiting
        .recv_timeout(Duration::from_secs(1))
        .expect("reconfigured HA worker did not reach its exit transition");
    assert_eq!(
        backend.ha.queue_depth.load(Ordering::Acquire),
        initial_depth + 3
    );

    release_exit.send(()).unwrap();
    backend.shutdown_ha().unwrap();
    assert_eq!(backend.ha.queue_depth.load(Ordering::Acquire), 0);
    assert!(backend.ha.worker.lock().unwrap().is_none());
    assert_eq!(backend.ha.runtime.lock().unwrap().state, "disabled");
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn clean_shutdown_joins_before_publishing_stopped() {
    let (root, _config, backend) = lifecycle_backend("clean-shutdown", true, "127.0.0.1");
    let (started, release_start) = install_barrier(&backend.ha.test_hooks.start_barrier);
    backend.start_ha();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its start barrier");
    assert!(
        backend
            .ha
            .worker
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|worker| worker.state == WorkerState::Running && worker.handle.is_some())
    );
    release_start.send(()).unwrap();

    let shutdown_backend = Arc::clone(&backend);
    let (finished, completion) = mpsc::channel();
    let shutdown = thread::spawn(move || {
        finished.send(shutdown_backend.shutdown_ha()).unwrap();
    });
    completion
        .recv_timeout(Duration::from_secs(2))
        .expect("HA shutdown did not complete by its test deadline")
        .unwrap();
    shutdown.join().unwrap();
    assert!(backend.ha.worker.lock().unwrap().is_none());
    assert_eq!(backend.ha.queue_depth.load(Ordering::Acquire), 0);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn naturally_exited_worker_is_joined_before_restart() {
    let (root, config, backend) = lifecycle_backend("natural-restart", true, "");
    let (exiting, release_exit) = install_barrier(&backend.ha.test_hooks.exit_barrier);
    backend.start_ha();
    exiting
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its natural exit transition");
    release_exit.send(()).unwrap();
    {
        let worker = backend.ha.worker.lock().unwrap();
        let worker = worker.as_ref().expect("exited worker lost its JoinHandle");
        assert_eq!(worker.state, WorkerState::Stopping);
        assert!(worker.handle.is_some());
    }

    write_ha_config(&config, true, "127.0.0.1");
    let (started, release_start) = install_barrier(&backend.ha.test_hooks.start_barrier);
    backend.ha.reconfigure();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not restart after its prior JoinHandle was reaped");
    assert!(
        backend
            .ha
            .worker
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|worker| worker.state == WorkerState::Running && worker.handle.is_some())
    );
    release_start.send(()).unwrap();
    backend.shutdown_ha().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn worker_thread_tracks_enabled_configuration() {
    let root = task_temp("lifecycle");
    let config = root.join("thingino.json");
    fs::write(
        &config,
        br#"{"ha":{"enabled":false,"mqtt":{"host":"127.0.0.1","port":1}}}"#,
    )
    .unwrap();
    let backend = Arc::new(PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        hostname: root.join("hostname"),
        ..CameraPaths::default()
    }));
    backend.start_ha();
    assert!(backend.ha.worker.lock().unwrap().is_none());

    fs::write(
        &config,
        br#"{"ha":{"enabled":true,"mqtt":{"host":"127.0.0.1","port":1}}}"#,
    )
    .unwrap();
    backend.ha.reconfigure();
    assert!(
        backend
            .ha
            .worker
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|worker| worker.state == WorkerState::Running)
    );

    fs::write(
        &config,
        br#"{"ha":{"enabled":false,"mqtt":{"host":"127.0.0.1","port":1}}}"#,
    )
    .unwrap();
    backend.ha.reconfigure();
    backend.shutdown_ha().unwrap();
    assert!(backend.ha.worker.lock().unwrap().is_none());
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn native_worker_publishes_and_reconfigures_against_local_broker() {
    let Some(port) = std::env::var("THINGINO_HA_TEST_BROKER_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
    else {
        return;
    };
    let root = task_temp("native-worker");
    let config = root.join("thingino.json");
    let prudynt = root.join("prudynt.json");
    let hostname = root.join("hostname");
    let release = root.join("os-release");
    let motion_socket = root.join("run/control/motion.sock");
    let motion_alarm = root.join("run/motion/motion_alarm");
    let motion_detected = root.join("run/prudynt/motion_detected.active");
    let proc_net_wireless = root.join("proc-net-wireless");
    let prudynt_socket = root.join("prudynt.sock");
    let (snapshot_server_running, snapshot_server) = snapshot_fixture(&prudynt_socket);
    fs::write(
        &prudynt,
        br#"{"image":{"running_mode":0},"motion":{"debounce_time":0,"cooldown_time":1,"init_time":0,"min_time":0,"post_time":0,"playonspeaker":false}}"#,
    )
    .unwrap();
    fs::write(&hostname, "camera\n").unwrap();
    fs::write(&release, "IMAGE_ID=a1\nCOMMIT_ID=r5\n").unwrap();
    fs::write(
        &proc_net_wireless,
        "Inter-| sta\n face | quality\nwlan0: 0000   70.  -41.  -256        0      0\n",
    )
    .unwrap();

    let document = |enabled: bool, name: &str| {
        format!(
            "{{\"ha\":{{\"enabled\":{enabled},\"device_name\":\"{name}\",\"discovery_prefix\":\"homeassistant\",\"camera_interval\":5,\"mqtt\":{{\"host\":\"127.0.0.1\",\"port\":{port},\"client_id_prefix\":\"thingino-ha\"}}}},\"daynight\":{{\"enabled\":false}},\"gpio\":{{}}}}"
        )
    };
    fs::write(&config, document(true, "Initial camera")).unwrap();

    let api = mqtt::Api::load().unwrap();
    let mut observer = mqtt::Client::new(api, "thingino-ha-worker-observer").unwrap();
    observer.set_test_inbound_payload_limit(mqtt::MAX_PAYLOAD_BYTES);
    observer.connect("127.0.0.1", port, 5).unwrap();
    let connect_deadline = Instant::now() + Duration::from_secs(3);
    while observer.take_connect_result() != Some(0) {
        assert!(Instant::now() < connect_deadline);
        observer.loop_once(20).unwrap();
    }
    let discovery_topic = "homeassistant/switch/thingino_camera/ircut/config";
    let availability_topic = "cameras/camera/status";
    let state_topic = "cameras/camera/camera_config/state";
    let motion_topic = "cameras/camera/motion/state";
    let rssi_topic = "cameras/camera/rssi/state";
    let camera_topic = "cameras/camera/live_view/image";
    for topic in [
        discovery_topic,
        availability_topic,
        state_topic,
        motion_topic,
        rssi_topic,
        camera_topic,
    ] {
        observer.subscribe(topic).unwrap();
    }
    observer.loop_once(20).unwrap();

    let backend = Arc::new(PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        prudynt_config: prudynt,
        prudynt_socket,
        hostname,
        os_release: release,
        motion_event_socket: motion_socket.clone(),
        motion_alarm: motion_alarm.clone(),
        motion_detected: motion_detected.clone(),
        proc_net_wireless: proc_net_wireless.clone(),
        wpa_control: root.join("missing-wpa-control"),
        ..CameraPaths::default()
    }));
    backend.start_ha();
    backend.motion.start().unwrap();

    let deadline = Instant::now() + Duration::from_secs(5);
    let mut retained = BTreeMap::new();
    while retained.len() < 6 && Instant::now() < deadline {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            if [
                discovery_topic,
                availability_topic,
                state_topic,
                motion_topic,
                rssi_topic,
                camera_topic,
            ]
            .contains(&message.topic.as_str())
            {
                retained.insert(message.topic, message.payload);
            }
        }
    }
    assert_eq!(retained.get(availability_topic), Some(&b"online".to_vec()));
    assert_eq!(retained.get(state_topic), Some(&b"a1".to_vec()));
    assert_eq!(retained.get(motion_topic), Some(&b"OFF".to_vec()));
    assert_eq!(retained.get(rssi_topic), Some(&b"-41".to_vec()));
    assert_eq!(
        retained.get(camera_topic),
        Some(&vec![0xff, 0xd8, 1, 0xff, 0xd9])
    );
    assert!(
        retained
            .get(discovery_topic)
            .is_some_and(|payload| payload.windows(14).any(|part| part == b"Initial camera"))
    );

    observer
        .publish("cameras/camera/snapshot/set", b"1", false)
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut snapshot_received = false;
    while !snapshot_received && Instant::now() < deadline {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            snapshot_received |=
                message.topic == camera_topic && message.payload == [0xff, 0xd8, 0, 0xff, 0xd9];
        }
    }
    assert!(
        snapshot_received,
        "HA Snapshot did not publish stream 0 JPEG"
    );

    fs::write(&proc_net_wireless, "unavailable\n").unwrap();
    backend.ha.action(br#"{"action":"publish_state"}"#).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut rssi_cleared = false;
    while !rssi_cleared && Instant::now() < deadline {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            rssi_cleared |= message.topic == rssi_topic && message.payload.is_empty();
        }
    }
    assert!(
        rssi_cleared,
        "missing RSSI did not clear its retained state"
    );

    let producer = std::os::unix::net::UnixDatagram::unbound().unwrap();
    producer.connect(&motion_socket).unwrap();
    for observation in [
        br#"{"version":1,"event":"motion","state":"monitoring","channel":0,"monotonic_ms":100,"monitoring_since_ms":100,"sequence":1,"producer_pid":42,"roi_mask":0,"initial_grace":false}"#.as_slice(),
        br#"{"version":1,"event":"motion","state":"detected","channel":0,"monotonic_ms":101,"monitoring_since_ms":100,"sequence":2,"producer_pid":42,"roi_mask":1,"initial_grace":false}"#.as_slice(),
    ] {
        producer.send(observation).unwrap();
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut motion_active = false;
    while !motion_active && Instant::now() < deadline {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            motion_active |= message.topic == motion_topic && message.payload == b"ON";
        }
    }
    assert!(motion_active, "native Motion event was not published");
    assert!(backend.motion.snapshot().active);
    assert!(motion_alarm.is_file());
    assert!(motion_detected.is_file());

    producer
        .send(br#"{"version":1,"event":"motion","state":"clear","channel":0,"monotonic_ms":102,"monitoring_since_ms":100,"sequence":3,"producer_pid":42,"roi_mask":0,"initial_grace":false}"#)
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut motion_inactive = false;
    while !motion_inactive && Instant::now() < deadline {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            motion_inactive |= message.topic == motion_topic && message.payload == b"OFF";
        }
    }
    assert!(motion_inactive, "native Motion clear was not published");
    assert!(!backend.motion.snapshot().active);
    assert!(!motion_alarm.exists());
    assert!(!motion_detected.exists());

    fs::write(&config, document(true, "Changed camera")).unwrap();
    backend.ha.reconfigure();
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut changed = false;
    while !changed && Instant::now() < deadline {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            changed |= message.topic == discovery_topic
                && message
                    .payload
                    .windows(14)
                    .any(|part| part == b"Changed camera");
        }
    }
    assert!(changed, "live HA reconfigure did not republish discovery");

    observer
        .publish("cameras/camera/ircut/set", b"invalid", false)
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        observer.loop_once(20).unwrap();
        let runtime = backend.ha.runtime().unwrap();
        let runtime = json::parse(&runtime.body).unwrap();
        if runtime
            .get_path("rejected_commands")
            .and_then(value_u64)
            .unwrap_or(0)
            > 0
        {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "worker did not reject MQTT payload"
        );
    }

    fs::write(&config, document(false, "Changed camera")).unwrap();
    backend.ha.reconfigure();
    backend.shutdown_ha().unwrap();
    snapshot_server_running.store(false, Ordering::Release);
    snapshot_server.join().unwrap();
    drop(observer);
    fs::remove_dir_all(root).unwrap();
}
