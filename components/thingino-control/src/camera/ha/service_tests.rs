use super::super::motion::MotionUpdate;
use super::lifecycle::{WorkerState, enqueue_to};
use super::session::{SessionEnd, SessionRequests, reconnect_delay, session_control, wait_backoff};
use super::*;
use crate::camera::CameraPaths;
use crate::camera::motion_events::{
    MOTION_EVENT_VERSION, MediaArtifact, MediaArtifactKind, MotionEvent, MotionEventDisposition,
    MotionEventSink, MotionState,
};
use std::fs;
use std::io;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::AtomicUsize;
use std::sync::mpsc::TryRecvError;
use std::thread;
use std::time::Duration;

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
) -> (PathBuf, PathBuf, Arc<crate::RaptorBackend>) {
    let root = task_temp(name);
    let config = root.join("thingino.json");
    let hostname = root.join("hostname");
    write_ha_config(&config, enabled, host);
    fs::write(&hostname, "camera\n").unwrap();
    let backend = Arc::new(crate::RaptorBackend::ha_fixture(
        &root,
        "127.0.0.1:9".parse().unwrap(),
    ));
    (root, config, backend)
}

#[test]
fn disabled_integration_rejects_actions_without_starting_a_worker() {
    let (root, config, backend) = lifecycle_backend("disabled-actions", false, "127.0.0.1");
    backend.start_ha();
    for action in ["reconnect", "republish_discovery", "publish_state"] {
        let body = format!(r#"{{"action":"{action}"}}"#);
        assert_eq!(
            backend.ha_service().action(body.as_bytes()),
            Err(BackendError::Unsupported(
                "Home Assistant integration is disabled"
            ))
        );
    }
    assert!(backend.ha_service().worker.lock().unwrap().is_none());
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    assert!(
        !super::super::config::HaConfig::load(&CameraPaths {
            thingino_config: config,
            ..CameraPaths::default()
        })
        .unwrap()
        .enabled
    );
    backend.shutdown_ha().unwrap();
    fs::remove_dir_all(root).unwrap();
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
    for (attempt, millis) in [1240, 2086, 4183, 8029, 16126, 32223, 60000, 60000]
        .into_iter()
        .enumerate()
    {
        assert_eq!(
            reconnect_delay("camera", attempt as u32),
            Duration::from_millis(millis)
        );
    }
}

#[test]
fn queue_failure_preserves_depth_and_high_water() {
    let (sender, receiver) = mpsc::sync_channel(1);
    let depth = AtomicUsize::new(0);
    let high_water = AtomicUsize::new(0);
    assert_eq!(
        enqueue_to(&sender, &depth, &high_water, Request::PublishState),
        Ok(())
    );
    assert_eq!(
        enqueue_to(&sender, &depth, &high_water, Request::Reconnect),
        Err(BackendError::Unavailable)
    );
    assert_eq!(depth.load(Ordering::Acquire), 1);
    assert_eq!(high_water.load(Ordering::Acquire), 1);
    assert_eq!(receiver.try_recv(), Ok(Request::PublishState));
    depth.fetch_sub(1, Ordering::AcqRel);
    drop(receiver);
    assert_eq!(
        enqueue_to(&sender, &depth, &high_water, Request::Motion),
        Err(BackendError::Unavailable)
    );
    assert_eq!(depth.load(Ordering::Acquire), 0);
    assert_eq!(high_water.load(Ordering::Acquire), 1);
}

#[test]
fn disconnected_motion_wakeup_rolls_back_the_slot_and_queue() {
    let (service, receiver) = motion_service();
    drop(receiver);
    assert_eq!(
        service.try_send_motion(motion_event(MotionState::Active, 1)),
        MotionEventDisposition::Dropped
    );
    assert_eq!(service.queue_depth.load(Ordering::Acquire), 0);
    assert_eq!(service.queue_high_water.load(Ordering::Acquire), 0);
    assert_eq!(service.motion_slot.take_next(), None);
}

#[test]
fn session_shutdown_precedes_generation_drain_and_reconnect() {
    let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
    let depth = AtomicUsize::new(2);
    let generation = AtomicUsize::new(8);
    let shutdown = AtomicBool::new(true);
    sender.send(Request::Reconnect).unwrap();
    sender.send(Request::PublishState).unwrap();
    let mut requests = SessionRequests::default();
    assert!(matches!(
        session_control(
            &receiver,
            &depth,
            &generation,
            7,
            &shutdown,
            Some(&mut requests)
        ),
        Some(SessionEnd::Shutdown)
    ));
    assert_eq!(depth.load(Ordering::Acquire), 2);
    shutdown.store(false, Ordering::Release);
    assert!(matches!(
        session_control(
            &receiver,
            &depth,
            &generation,
            7,
            &shutdown,
            Some(&mut requests)
        ),
        Some(SessionEnd::Reconfigure(8))
    ));
    assert_eq!(depth.load(Ordering::Acquire), 0);
    assert!(!requests.discovery && !requests.state && !requests.motion);
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
}

#[test]
fn session_reconnect_leaves_later_requests_for_the_next_session() {
    let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
    let depth = AtomicUsize::new(4);
    let generation = AtomicUsize::new(7);
    let shutdown = AtomicBool::new(false);
    for request in [
        Request::RepublishDiscovery,
        Request::Motion,
        Request::Reconnect,
        Request::PublishState,
    ] {
        sender.send(request).unwrap();
    }
    let mut requests = SessionRequests::default();
    assert!(matches!(
        session_control(
            &receiver,
            &depth,
            &generation,
            7,
            &shutdown,
            Some(&mut requests)
        ),
        Some(SessionEnd::Reconnect)
    ));
    assert!(requests.discovery && requests.motion && !requests.state);
    assert_eq!(depth.load(Ordering::Acquire), 1);
    assert_eq!(receiver.try_recv(), Ok(Request::PublishState));
}

#[test]
fn disconnected_motion_does_not_end_backoff_until_a_publish_wakeup() {
    let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
    let depth = AtomicUsize::new(2);
    let generation = AtomicUsize::new(7);
    let shutdown = AtomicBool::new(false);
    sender.send(Request::Motion).unwrap();
    sender.send(Request::PublishState).unwrap();
    assert!(matches!(
        wait_backoff(
            Instant::now() + Duration::from_secs(1),
            &receiver,
            &depth,
            &generation,
            7,
            &shutdown
        ),
        Some(SessionEnd::Reconnect)
    ));
    assert_eq!(depth.load(Ordering::Acquire), 0);
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
}

#[test]
fn concurrent_shutdown_rejects_a_second_join_without_holding_the_worker_lock() {
    let (root, _config, backend) = lifecycle_backend("double-shutdown", true, "127.0.0.1");
    let (started, release_start) = install_barrier(&backend.ha_service().test_hooks.start_barrier);
    backend.start_ha();
    started.recv_timeout(Duration::from_secs(1)).unwrap();
    let joining = Arc::clone(&backend);
    let first_shutdown = thread::spawn(move || joining.shutdown_ha());
    let deadline = Instant::now() + Duration::from_secs(1);
    loop {
        let joining = backend.ha_service().worker.try_lock().is_ok_and(|worker| {
            worker.as_ref().is_some_and(|active| {
                active.state == WorkerState::Stopping
                    && active.handle.is_none()
                    && active.sender.is_none()
            })
        });
        if joining {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "shutdown did not enter the join phase"
        );
        thread::yield_now();
    }
    assert_eq!(
        backend.shutdown_ha().unwrap_err().kind(),
        io::ErrorKind::WouldBlock
    );
    assert!(matches!(
        backend
            .ha_service()
            .action(br#"{"action":"publish_state"}"#),
        Err(BackendError::Unavailable)
    ));
    release_start.send(()).unwrap();
    first_shutdown.join().unwrap().unwrap();
    assert!(backend.ha_service().worker.lock().unwrap().is_none());
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    fs::remove_dir_all(root).unwrap();
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
        .ha_service()
        .test_hooks
        .fail_next_spawn
        .store(true, Ordering::Release);

    backend.start_ha();

    assert!(backend.ha_service().worker.lock().unwrap().is_none());
    assert!(!backend.ha_service().accept_motion.load(Ordering::Acquire));
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    let runtime = backend.ha_service().runtime.lock().unwrap().clone();
    assert_eq!(runtime.state, "error");
    assert_eq!(runtime.last_error, Some("HA worker spawn failed"));

    let (started, release) = install_barrier(&backend.ha_service().test_hooks.start_barrier);
    backend.ha_service().reconfigure();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not restart after the injected spawn failure");
    assert!(
        backend
            .ha_service()
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
    let (exiting, release_exit) = install_barrier(&backend.ha_service().test_hooks.exit_barrier);
    backend.start_ha();
    exiting
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach the exit transition");

    let service = Arc::clone(backend.ha_service());
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
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn reconfigure_wins_the_generation_exit_race() {
    let (root, config, backend) = lifecycle_backend("generation-exit", true, "");
    let (pre_exit, release_exit) =
        install_barrier(&backend.ha_service().test_hooks.pre_exit_barrier);
    backend.start_ha();
    pre_exit
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its pre-exit barrier");

    let depth_before_action = backend.ha_service().queue_depth.load(Ordering::Acquire);
    backend
        .ha_service()
        .action(br#"{"action":"publish_state"}"#)
        .unwrap();
    assert_eq!(
        backend.ha_service().queue_depth.load(Ordering::Acquire),
        depth_before_action + 1
    );
    let previous_generation = backend
        .ha_service()
        .config_generation
        .load(Ordering::Acquire);
    write_ha_config(&config, true, "127.0.0.1");
    let (continued, release_continue) =
        install_barrier(&backend.ha_service().test_hooks.start_barrier);
    backend.ha_service().reconfigure();
    assert!(
        backend
            .ha_service()
            .config_generation
            .load(Ordering::Acquire)
            > previous_generation
    );
    release_exit.send(()).unwrap();
    continued
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker exited instead of observing the newer generation");
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    assert!(
        backend
            .ha_service()
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
    let (started, release_start) = install_barrier(&backend.ha_service().test_hooks.start_barrier);
    let (exiting, release_exit) = install_barrier(&backend.ha_service().test_hooks.exit_barrier);
    backend.start_ha();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its start barrier");

    let initial_depth = backend.ha_service().queue_depth.load(Ordering::Acquire);
    for _ in 0..3 {
        backend
            .ha_service()
            .action(br#"{"action":"publish_state"}"#)
            .unwrap();
    }
    assert_eq!(
        backend.ha_service().queue_depth.load(Ordering::Acquire),
        initial_depth + 3
    );
    let previous_generation = backend
        .ha_service()
        .config_generation
        .load(Ordering::Acquire);
    write_ha_config(&config, false, "127.0.0.1");
    backend.ha_service().reconfigure();
    assert!(
        backend
            .ha_service()
            .config_generation
            .load(Ordering::Acquire)
            > previous_generation
    );
    release_start.send(()).unwrap();
    exiting
        .recv_timeout(Duration::from_secs(1))
        .expect("reconfigured HA worker did not reach its exit transition");
    assert_eq!(
        backend.ha_service().queue_depth.load(Ordering::Acquire),
        initial_depth + 3
    );

    release_exit.send(()).unwrap();
    backend.shutdown_ha().unwrap();
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    assert!(backend.ha_service().worker.lock().unwrap().is_none());
    assert_eq!(
        backend.ha_service().runtime.lock().unwrap().state,
        "disabled"
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn clean_shutdown_joins_before_publishing_stopped() {
    let (root, _config, backend) = lifecycle_backend("clean-shutdown", true, "127.0.0.1");
    let (started, release_start) = install_barrier(&backend.ha_service().test_hooks.start_barrier);
    backend.start_ha();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its start barrier");
    assert!(
        backend
            .ha_service()
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
    assert!(backend.ha_service().worker.lock().unwrap().is_none());
    assert_eq!(backend.ha_service().queue_depth.load(Ordering::Acquire), 0);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn naturally_exited_worker_is_joined_before_restart() {
    let (root, config, backend) = lifecycle_backend("natural-restart", true, "");
    let (exiting, release_exit) = install_barrier(&backend.ha_service().test_hooks.exit_barrier);
    backend.start_ha();
    exiting
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not reach its natural exit transition");
    release_exit.send(()).unwrap();
    {
        let worker = backend.ha_service().worker.lock().unwrap();
        let worker = worker.as_ref().expect("exited worker lost its JoinHandle");
        assert_eq!(worker.state, WorkerState::Stopping);
        assert!(worker.handle.is_some());
    }

    write_ha_config(&config, true, "127.0.0.1");
    let (started, release_start) = install_barrier(&backend.ha_service().test_hooks.start_barrier);
    backend.ha_service().reconfigure();
    started
        .recv_timeout(Duration::from_secs(1))
        .expect("HA worker did not restart after its prior JoinHandle was reaped");
    assert!(
        backend
            .ha_service()
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
    let backend = Arc::new(crate::RaptorBackend::ha_fixture(
        &root,
        "127.0.0.1:9".parse().unwrap(),
    ));
    backend.start_ha();
    assert!(backend.ha_service().worker.lock().unwrap().is_none());

    fs::write(
        &config,
        br#"{"ha":{"enabled":true,"mqtt":{"host":"127.0.0.1","port":1}}}"#,
    )
    .unwrap();
    backend.ha_service().reconfigure();
    assert!(
        backend
            .ha_service()
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
    backend.ha_service().reconfigure();
    backend.shutdown_ha().unwrap();
    assert!(backend.ha_service().worker.lock().unwrap().is_none());
    fs::remove_dir_all(root).unwrap();
}
