use super::super::tests::{backend, framed, read_request, task_temp};
use super::*;
use std::net::TcpListener;
use std::os::unix::net::UnixListener;

const JPEG: &[u8] = b"\xff\xd8\xff\xd9";
const VIDEO: &[u8] = br#"{"status":"ok","configured":[true,false],"streams":[{"chn":0,"stream_id":0,"jpeg":false,"available":true,"w":1920,"h":1080,"codec":0,"bitrate":750000,"avg_bitrate":0,"gop":25,"fps":15},{"chn":2,"stream_id":0,"jpeg":true,"available":false,"fps_recovery_required":false,"fps_persistence_pending":false,"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":0,"gop":0,"fps":1}]}"#;
const RHD: &[u8] = br#"{"status":"ok","clients":0,"mjpeg":0,"audio":0,"port":8080,"jpeg_rings":2,"jpeg_available":[true,true],"exif_timestamp":false,"sign_snapshots":false,"privacy":false,"tls":false}"#;

type Hook = Arc<dyn Fn() + Send + Sync>;

struct Fixture {
    root: PathBuf,
    backend: Arc<RaptorBackend>,
    stop: Arc<AtomicBool>,
    threads: Vec<thread::JoinHandle<()>>,
    outputs: Arc<Mutex<[bool; 3]>>,
    http_hook: Arc<Mutex<Option<Hook>>>,
    privacy: Arc<AtomicU32>,
}
impl Fixture {
    fn new() -> Self {
        let root = task_temp("timelapse");
        let mount = root.join("sd");
        fs::create_dir(&mount).unwrap();
        let http = TcpListener::bind("127.0.0.1:0").unwrap();
        http.set_nonblocking(true).unwrap();
        let mut backend = backend(&root, http.local_addr().unwrap());
        backend.timelapse = Arc::new(Service::new(
            root.join("config.json"),
            storage::Store {
                mount,
                fixture: true,
                ..storage::Store::default()
            },
        ));
        let backend = Arc::new(backend);
        let stop = Arc::new(AtomicBool::new(false));
        let mut threads = Vec::new();
        let outputs = Arc::new(Mutex::new([false; 3]));
        let privacy = Arc::new(AtomicU32::new(0));
        for socket in ["rvd.sock", "rhd.sock", "ric.sock"] {
            let listener = UnixListener::bind(root.join(socket)).unwrap();
            listener.set_nonblocking(true).unwrap();
            let done = Arc::clone(&stop);
            let state = Arc::clone(&outputs);
            let privacy_state = Arc::clone(&privacy);
            threads.push(thread::spawn(move || {
                while !done.load(Ordering::Acquire) {
                    let Ok((mut stream, _)) = listener.accept() else { thread::sleep(Duration::from_millis(1)); continue; };
                    let request = read_request(&mut stream);
                    let parsed = crate::json::parse(&request).unwrap();
                    let mut outputs = state.lock().unwrap();
                    let response = match parsed.get_path("cmd").and_then(Value::as_str).unwrap() {
                        "privacy-status" => match privacy_state.load(Ordering::Acquire) {
                            0 => br#"{"status":"ok","supported":true,"video":[false,false],"jpeg_required":false,"audio_required":false}"#.to_vec(),
                            1 => br#"{"status":"ok","supported":true,"video":[true,true],"jpeg_required":false,"audio_required":false}"#.to_vec(),
                            _ => br#"{"status":"ok","supported":false}"#.to_vec(),
                        },
                        "status" => if socket == "rvd.sock" { VIDEO.to_vec() } else { RHD.to_vec() },
                        "mode" => br#"{"status":"ok","mode":"day","state":"day"}"#.to_vec(),
                        "get-running-mode" => format!(r#"{{"status":"ok","mode":"{}"}}"#, if outputs[2] { "day" } else { "night" }).into_bytes(),
                        "set-running-mode" => { outputs[2] = parsed.get_path("value").and_then(Value::as_str) == Some("day"); br#"{"status":"ok"}"#.to_vec() },
                        "get-output-state" | "set-output" => {
                            if parsed.get_path("cmd").and_then(Value::as_str) == Some("set-output") {
                                let index = if parsed.get_path("output").and_then(Value::as_str) == Some("ircut") { 0 } else { 1 };
                                outputs[index] = parsed.get_path("enabled").and_then(Value::as_bool).unwrap();
                            }
                            format!(r#"{{"status":"ok","ircut":{{"supported":true,"available":true,"enabled":{}}},"ir850":{{"supported":true,"available":true,"enabled":{}}}}}"#, outputs[0], outputs[1]).into_bytes()
                        },
                        other => panic!("Unexpected daemon command: {other}"),
                    };
                    stream.write_all(&framed(&response)).unwrap();

                }
            }));
        }
        let done = Arc::clone(&stop);
        let http_hook: Arc<Mutex<Option<Hook>>> = Arc::new(Mutex::new(None));
        let capture_hook = Arc::clone(&http_hook);
        threads.push(thread::spawn(move || {
            while !done.load(Ordering::Acquire) {
                let Ok((mut stream, _)) = http.accept() else { thread::sleep(Duration::from_millis(1)); continue; };
                stream.set_nonblocking(false).unwrap();
                stream.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                let mut request = [0; 1024];
                let count = stream.read(&mut request).unwrap();
                assert!(request[..count].starts_with(b"GET /snap.jpg?stream=0 "));
                if let Some(hook) = capture_hook.lock().unwrap().clone() { hook(); }
                stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n\xff\xd8\xff\xd9").unwrap();
            }
        }));
        Self {
            root,
            backend,
            stop,
            threads,
            outputs,
            http_hook,
            privacy,
        }
    }
    fn dir(&self) -> PathBuf {
        self.root.join("sd/raptor/timelapse")
    }
    fn image(&self, captured: u64, serial: u32) -> storage::Image {
        storage::Image::stage(
            self.backend.timelapse.store.directory().unwrap(),
            captured,
            serial,
            JPEG,
            || {},
        )
        .unwrap_or_else(|_| panic!("stage failed"))
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.backend.timelapse.stop.store(true, Ordering::Release);
        self.stop.store(true, Ordering::Release);
        for thread in self.threads.drain(..) {
            thread.join().unwrap();
        }
        fs::remove_dir_all(&self.root).unwrap();
    }
}

#[test]
fn policy_rejects_path_changes_unknown_fields_and_invalid_ranges() {
    let policy = Policy::default();
    assert_eq!(Policy::parse(&policy.value()).unwrap(), policy);
    for body in [
        r#"{"timelapse":{"mount":"/tmp"}}"#,
        r#"{"timelapse":{"filepath":"../recordings"}}"#,
        r#"{"timelapse":{"interval":0}}"#,
        r#"{"timelapse":{"interval":1441}}"#,
        r#"{"timelapse":{"keep_days":366}}"#,
        r#"{"timelapse":{"extra":true}}"#,
        r#"{"timelapse":{"presets":{"white":true}}}"#,
    ] {
        assert!(policy.update(body.as_bytes()).is_err(), "{body}");
    }
    let next = policy
        .update(br#"{"timelapse":{"enabled":true,"keep_days":0,"presets":{"color":true}}}"#)
        .unwrap();
    assert!(next.enabled && next.presets[2]);
    assert_eq!(next.keep_days, 0);
}

#[test]
fn config_readback_and_restart_restore_are_checked() {
    let fixture = Fixture::new();
    let service = &fixture.backend.timelapse;
    let policy = Policy {
        enabled: true,
        interval: 17,
        ..Policy::default()
    };
    service.persist(&policy).unwrap();
    service.load();
    assert_eq!(service.state.lock().unwrap().policy, policy);
    assert_eq!(service.state.lock().unwrap().saved, Some(policy));
    fs::write(&service.config, b"{}").unwrap();
    let fresh = Service::new(service.config.clone(), storage::Store::default());
    fresh.load();
    let state = fresh.state.lock().unwrap();
    assert!(!state.policy.enabled);
    assert_eq!(state.last_error, Some("invalid_saved_config"));
}

#[test]
fn retention_converges_and_preserves_unowned_altered_and_staging_files() {
    let fixture = Fixture::new();
    for serial in 0..70 {
        let mut image = fixture.image(1, serial);
        image.publish(serial).unwrap();
        image.complete().unwrap();
    }
    fs::write(fixture.dir().join("user.jpg"), JPEG).unwrap();
    fs::write(fixture.dir().join("1-0.jpg"), b"user modification").unwrap();
    let mut pending = fixture.image(1, 90);
    let dir = fixture.backend.timelapse.store.directory().unwrap();
    assert_eq!(storage::cleanup(&dir, 0, 200000, || false).unwrap(), 0);
    assert_eq!(storage::cleanup(&dir, 1, 200000, || true).unwrap(), 0);
    assert_eq!(storage::cleanup(&dir, 1, 200000, || false).unwrap(), 32);
    assert_eq!(storage::cleanup(&dir, 1, 200000, || false).unwrap(), 32);
    assert_eq!(storage::cleanup(&dir, 1, 200000, || false).unwrap(), 5);
    assert_eq!(
        fs::read(fixture.dir().join("1-0.jpg")).unwrap(),
        b"user modification"
    );
    assert!(fixture.dir().join("user.jpg").exists());
    assert!(fixture.dir().join(".pending-1-90").exists());
    pending.discard().unwrap();
}

#[test]
fn exclusive_publish_and_rollback_preserve_user_changes() {
    let fixture = Fixture::new();
    let mut image = fixture.image(1, 0);
    fs::write(fixture.dir().join("1-0.jpg"), b"existing").unwrap();
    assert!(image.publish(0).is_err());
    image.discard().unwrap();
    assert_eq!(
        fs::read(fixture.dir().join("1-0.jpg")).unwrap(),
        b"existing"
    );
    let mut image = fixture.image(2, 1);
    image.publish(1).unwrap();
    fs::write(fixture.dir().join("2-1.jpg"), b"changed after publication").unwrap();
    assert!(image.discard().is_err());
    assert_eq!(
        fs::read(fixture.dir().join("2-1.jpg")).unwrap(),
        b"changed after publication"
    );
}

#[test]
fn fixed_storage_root_rejects_symlinks_and_missing_mount_proof() {
    let fixture = Fixture::new();
    std::os::unix::fs::symlink(&fixture.root, fixture.root.join("sd/raptor")).unwrap();
    assert!(fixture.backend.timelapse.store.directory().is_err());
    let mount = std::path::Path::new("/mnt/mmcblk0p1");
    let device = std::path::Path::new("/dev/mmcblk0p1");
    let valid = b"1 2 179:1 / /mnt/mmcblk0p1 rw - vfat /dev/mmcblk0p1 rw\n";
    assert!(storage::valid_mount(valid, mount, device));
    assert!(!storage::valid_mount(b"", mount, device));
    assert!(!storage::valid_mount(
        &[valid.as_slice(), valid.as_slice()].concat(),
        mount,
        device
    ));
}

#[test]
fn capture_publishes_main_jpeg_without_holding_preview_lock() {
    let fixture = Fixture::new();
    let weak = Arc::downgrade(&fixture.backend);
    *fixture.backend.timelapse.hook.lock().unwrap() = Some(Arc::new(move |_| {
        let backend = weak.upgrade().unwrap();
        assert!(backend.lock_mutation().is_ok());
    }));
    fixture
        .backend
        .timelapse
        .capture(&fixture.backend, &Policy::default())
        .unwrap();
    assert_eq!(fs::read_dir(fixture.dir()).unwrap().count(), 2);
}

#[test]
fn published_image_stays_hidden_until_final_acceptance() {
    let fixture = Fixture::new();
    let mut old = fixture.image(1, 999);
    old.publish(999).unwrap();
    old.complete().unwrap();
    let weak = Arc::downgrade(&fixture.backend);
    let directory = fixture.dir();
    *fixture.backend.timelapse.hook.lock().unwrap() = Some(Arc::new(move |phase| {
        if phase != "published" {
            return;
        }
        let backend = weak.upgrade().unwrap();
        for entry in fs::read_dir(&directory).unwrap() {
            let name = entry.unwrap().file_name().to_string_lossy().into_owned();
            if name.ends_with(".jpg") {
                let target = format!("/media/v1/file?path=/mnt/mmcblk0p1/raptor/timelapse/{name}");
                assert_eq!(
                    backend
                        .recording_identity(&target, Instant::now() + Duration::from_secs(1))
                        .unwrap()
                        .is_some(),
                    name == "1-999.jpg"
                );
            }
        }
    }));
    fixture
        .backend
        .timelapse
        .capture(&fixture.backend, &Policy::default())
        .unwrap();
    let mut completed = 0;
    for entry in fs::read_dir(fixture.dir()).unwrap() {
        let name = entry.unwrap().file_name().to_string_lossy().into_owned();
        assert!(!name.starts_with(".pending-"));
        if name.ends_with(".jpg") {
            let target = format!("/media/v1/file?path=/mnt/mmcblk0p1/raptor/timelapse/{name}");
            assert!(
                fixture
                    .backend
                    .recording_identity(&target, Instant::now() + Duration::from_secs(1))
                    .unwrap()
                    .is_some()
            );
            completed += 1;
        }
    }
    assert_eq!(completed, 2);
}

#[test]
fn privacy_epoch_changes_at_capture_stage_and_publication_invalidate_only_new_image() {
    for phase in ["capturing", "staging_io", "published"] {
        let fixture = Fixture::new();
        let mut old = fixture.image(1, 999);
        old.publish(999).unwrap();
        old.complete().unwrap();
        let weak = Arc::downgrade(&fixture.backend);
        *fixture.backend.timelapse.hook.lock().unwrap() = Some(Arc::new(move |observed| {
            if observed == phase {
                let backend = weak.upgrade().unwrap();
                let _guard = backend.lock_mutation().unwrap();
                backend.ha_privacy_generation.fetch_add(2, Ordering::AcqRel);
            }
        }));
        assert_eq!(
            fixture
                .backend
                .timelapse
                .capture(&fixture.backend, &Policy::default()),
            Err("privacy_or_settings_changed")
        );
        assert_eq!(fs::read_dir(fixture.dir()).unwrap().count(), 2, "{phase}");
        assert_eq!(fs::read(fixture.dir().join("1-999.jpg")).unwrap(), JPEG);
    }
}

#[test]
fn invalidated_image_cleanup_failure_remains_visible_and_preserves_replacement() {
    let fixture = Fixture::new();
    let weak = Arc::downgrade(&fixture.backend);
    let directory = fixture.dir();
    *fixture.backend.timelapse.hook.lock().unwrap() = Some(Arc::new(move |phase| {
        if phase == "published" {
            let backend = weak.upgrade().unwrap();
            backend.ha_privacy_generation.fetch_add(1, Ordering::AcqRel);
            for entry in fs::read_dir(&directory).unwrap() {
                let path = entry.unwrap().path();
                if path.extension().is_some_and(|v| v == "jpg") {
                    fs::write(path, b"new user contents").unwrap();
                }
            }
        }
    }));
    assert_eq!(
        fixture
            .backend
            .timelapse
            .capture(&fixture.backend, &Policy::default()),
        Err("cleanup_failed")
    );
    let state = fixture.backend.timelapse.state.lock().unwrap();
    assert!(state.cleanup_blocked);
    assert_eq!(state.successes, 0);
    assert_eq!(state.last_error, Some("cleanup_failed"));
    assert!(
        fixture
            .backend
            .timelapse
            .quarantine
            .lock()
            .unwrap()
            .is_some()
    );
}

#[test]
fn presets_restore_and_concurrent_user_change_stops_remaining_restoration() {
    let fixture = Fixture::new();
    let mut lease = preset::Lease::read(&fixture.backend).unwrap();
    lease.apply(&fixture.backend, [true; 3]).unwrap();
    assert_eq!(*fixture.outputs.lock().unwrap(), [true; 3]);
    lease.restore(&fixture.backend, || {}).unwrap();
    assert_eq!(*fixture.outputs.lock().unwrap(), [false; 3]);
    let mut lease = preset::Lease::read(&fixture.backend).unwrap();
    lease.apply(&fixture.backend, [true; 3]).unwrap();
    let result = lease.restore(&fixture.backend, || {
        let _guard = fixture.backend.lock_mutation().unwrap();
        fixture
            .backend
            .timelapse_user_generation
            .fetch_add(1, Ordering::AcqRel);
        fixture
            .backend
            .set_ir_output("ircut", false, Instant::now() + Duration::from_secs(1))
            .unwrap();
    });
    assert!(result.is_err());
    assert_eq!(*fixture.outputs.lock().unwrap(), [false, true, false]);
}

#[test]
fn privacy_transition_prevents_preset_restoration_even_after_privacy_is_off() {
    let fixture = Fixture::new();
    let mut lease = preset::Lease::read(&fixture.backend).unwrap();
    lease.apply(&fixture.backend, [true; 3]).unwrap();
    fixture
        .backend
        .ha_privacy_generation
        .fetch_add(2, Ordering::AcqRel);
    assert!(lease.restore(&fixture.backend, || {}).is_err());
    assert_eq!(*fixture.outputs.lock().unwrap(), [true; 3]);
}

#[test]
fn delayed_http_and_staging_allow_preview_mutations_and_discard_invalidated_image() {
    for stage in [false, true] {
        let fixture = Fixture::new();
        let (entered, arrival) = mpsc::sync_channel(1);
        let (release, resume) = mpsc::sync_channel(1);
        let resume = Mutex::new(resume);
        let hook: Hook = Arc::new(move || {
            entered.send(()).unwrap();
            resume
                .lock()
                .unwrap()
                .recv_timeout(Duration::from_secs(2))
                .unwrap();
        });
        if stage {
            *fixture.backend.timelapse.hook.lock().unwrap() = Some(Arc::new(move |phase| {
                if phase == "staging_io" {
                    hook();
                }
            }));
        } else {
            *fixture.http_hook.lock().unwrap() = Some(hook);
        }
        thread::scope(|scope| {
            let capture = scope.spawn(|| {
                fixture
                    .backend
                    .timelapse
                    .capture(&fixture.backend, &Policy::default())
            });
            arrival.recv_timeout(Duration::from_secs(2)).unwrap();
            {
                let _guard = fixture.backend.lock_mutation().unwrap();
                fixture
                    .backend
                    .ha_privacy_generation
                    .fetch_add(2, Ordering::AcqRel);
            }
            release.send(()).unwrap();
            assert_eq!(capture.join().unwrap(), Err("privacy_or_settings_changed"));
        });
        if fixture.dir().exists() {
            assert_eq!(fs::read_dir(fixture.dir()).unwrap().count(), 0);
        }
    }
}

#[test]
fn storage_pause_waits_for_staging_and_blocks_new_capture_without_persisting_policy() {
    let fixture = Fixture::new();
    let (entered, arrival) = mpsc::sync_channel(1);
    let (release, resume) = mpsc::sync_channel(1);
    let resume = Mutex::new(resume);
    *fixture.backend.timelapse.hook.lock().unwrap() = Some(Arc::new(move |phase| {
        if phase == "staging_io" {
            entered.send(()).unwrap();
            resume
                .lock()
                .unwrap()
                .recv_timeout(Duration::from_secs(2))
                .unwrap();
        }
    }));
    let (paused_send, paused_recv) = mpsc::sync_channel(1);
    let (lease_release, lease_wait) = mpsc::sync_channel(1);
    thread::scope(|scope| {
        let capture = scope.spawn(|| {
            fixture
                .backend
                .timelapse
                .capture(&fixture.backend, &Policy::default())
        });
        arrival.recv_timeout(Duration::from_secs(2)).unwrap();
        let service = &fixture.backend.timelapse;
        let pause = scope.spawn(move || {
            let lease = service
                .pause_storage(Instant::now() + Duration::from_secs(1))
                .unwrap();
            paused_send.send(()).unwrap();
            lease_wait.recv_timeout(Duration::from_secs(1)).unwrap();
            drop(lease);
        });
        assert_eq!(
            paused_recv.recv_timeout(Duration::from_millis(30)),
            Err(mpsc::RecvTimeoutError::Timeout)
        );
        release.send(()).unwrap();
        assert_eq!(capture.join().unwrap(), Err("privacy_or_settings_changed"));
        paused_recv.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(
            fixture
                .backend
                .timelapse
                .capture(&fixture.backend, &Policy::default()),
            Err("privacy_or_settings_changed")
        );
        lease_release.send(()).unwrap();
        pause.join().unwrap();
    });
    assert!(
        !fixture
            .backend
            .timelapse
            .maintenance
            .load(Ordering::Acquire)
    );
    assert!(
        !fixture
            .backend
            .timelapse
            .storage_active
            .load(Ordering::Acquire)
    );
}

#[test]
fn storage_pause_timeout_reopens_the_gate_while_active_storage_refuses_quiescence() {
    let fixture = Fixture::new();
    fixture
        .backend
        .timelapse
        .storage_active
        .store(true, Ordering::Release);
    assert_eq!(
        fixture
            .backend
            .timelapse
            .pause_storage(Instant::now() + Duration::from_millis(20))
            .map(drop),
        Err(BackendError::Timeout)
    );
    assert!(
        !fixture
            .backend
            .timelapse
            .maintenance
            .load(Ordering::Acquire)
    );
    fixture
        .backend
        .timelapse
        .storage_active
        .store(false, Ordering::Release);
    let lease = fixture
        .backend
        .timelapse
        .pause_storage(Instant::now() + Duration::from_secs(1))
        .unwrap();
    drop(lease);
    assert!(
        !fixture
            .backend
            .timelapse
            .maintenance
            .load(Ordering::Acquire)
    );
}

#[test]
fn worker_saves_disabled_policy_with_checked_receipt_and_no_capture() {
    let fixture = Fixture::new();
    fixture.backend.start_timelapse().unwrap();
    let until = Instant::now() + Duration::from_secs(2);
    while !fixture.backend.timelapse.state.lock().unwrap().initialized {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
    let response = fixture
        .backend
        .timelapse
        .submit(br#"{"timelapse":{"enabled":false,"interval":9}}"#, until)
        .unwrap();
    assert_eq!(
        crate::json::parse(&response.body)
            .unwrap()
            .get_path("persistent")
            .and_then(Value::as_bool),
        Some(true)
    );
    let saved = storage::read_limited(&fixture.backend.timelapse.config, 8192).unwrap();
    assert_eq!(
        Policy::parse(&crate::json::parse(&saved).unwrap())
            .unwrap()
            .interval,
        9
    );
    assert!(!fixture.dir().exists());
    assert_eq!(fixture.backend.timelapse.enabled_observation(), Some(false));
    fs::remove_file(&fixture.backend.timelapse.config).unwrap();
    fs::create_dir(&fixture.backend.timelapse.config).unwrap();
    assert!(
        fixture
            .backend
            .timelapse
            .submit(br#"{"timelapse":{"enabled":false}}"#, until)
            .is_err()
    );
    assert_eq!(fixture.backend.timelapse.enabled_observation(), None);
    fs::remove_dir(&fixture.backend.timelapse.config).unwrap();
    fixture
        .backend
        .timelapse
        .submit(br#"{"timelapse":{"enabled":false}}"#, until)
        .unwrap();
    assert_eq!(fixture.backend.timelapse.enabled_observation(), Some(false));

    fixture
        .backend
        .timelapse
        .stop
        .store(true, Ordering::Release);
    while fixture.backend.timelapse.started.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn enabled_boot_capture_schedules_next_attempt_and_does_not_contact_recorders() {
    let fixture = Fixture::new();
    let main = UnixListener::bind(fixture.root.join("rmr0.sock")).unwrap();
    let sub = UnixListener::bind(fixture.root.join("rmr1.sock")).unwrap();
    main.set_nonblocking(true).unwrap();
    sub.set_nonblocking(true).unwrap();
    fixture
        .backend
        .timelapse
        .persist(&Policy {
            enabled: true,
            interval: 3,
            keep_days: 0,
            ..Policy::default()
        })
        .unwrap();
    fixture.backend.start_timelapse().unwrap();
    let until = Instant::now() + Duration::from_secs(2);
    loop {
        let state = fixture.backend.timelapse.state.lock().unwrap().clone();
        if state.successes == 1 {
            assert!(state.next_due.unwrap() >= unix_now() + 175);
            break;
        }
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
    fixture
        .backend
        .timelapse
        .stop
        .store(true, Ordering::Release);
    while fixture.backend.timelapse.started.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(main.accept().unwrap_err().kind(), io::ErrorKind::WouldBlock);
    assert_eq!(sub.accept().unwrap_err().kind(), io::ErrorKind::WouldBlock);
    assert_eq!(fixture.backend.timelapse.state.lock().unwrap().successes, 1);
}

#[test]
fn queued_save_timeout_cancels_capture_and_expired_update_is_not_applied() {
    let fixture = Fixture::new();
    fixture
        .backend
        .timelapse
        .persist(&Policy {
            enabled: true,
            keep_days: 0,
            ..Policy::default()
        })
        .unwrap();
    let (entered, arrival) = mpsc::sync_channel(1);
    let (release, resume) = mpsc::sync_channel(1);
    let resume = Mutex::new(resume);
    *fixture.http_hook.lock().unwrap() = Some(Arc::new(move || {
        entered.send(()).unwrap();
        resume
            .lock()
            .unwrap()
            .recv_timeout(Duration::from_secs(2))
            .unwrap();
    }));
    fixture.backend.start_timelapse().unwrap();
    arrival.recv_timeout(Duration::from_secs(2)).unwrap();
    let result = fixture.backend.timelapse.submit(
        br#"{"timelapse":{"interval":9}}"#,
        Instant::now() + Duration::from_millis(30),
    );
    assert!(matches!(result, Err(BackendError::PartialApply(_))));
    assert!(fixture.backend.lock_mutation().is_ok());
    release.send(()).unwrap();
    let until = Instant::now() + Duration::from_secs(2);
    while fixture.backend.timelapse.pending.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
    fixture
        .backend
        .timelapse
        .stop
        .store(true, Ordering::Release);
    while fixture.backend.timelapse.started.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
    let state = fixture.backend.timelapse.state.lock().unwrap();
    assert_eq!(state.policy.interval, 1);
    assert_eq!(state.saved.as_ref().unwrap().interval, 1);
    assert_eq!(state.successes, 0);
}

#[test]
fn privacy_on_or_unknown_blocks_capture_before_sd_work() {
    for value in [1, 2] {
        let fixture = Fixture::new();
        fixture.privacy.store(value, Ordering::Release);
        assert_eq!(
            fixture
                .backend
                .timelapse
                .capture(&fixture.backend, &Policy::default()),
            Err("privacy_or_settings_changed")
        );
        assert!(!fixture.dir().exists());
    }
}

#[test]
fn automatic_daynight_mode_rejects_presets_before_imaging_changes() {
    let root = task_temp("timelapse-auto");
    let ric = super::super::tests::serve_daemon(
        &root,
        "ric.sock",
        br#"{"cmd":"mode"}"#,
        br#"{"status":"ok","mode":"auto","state":"day"}"#,
    );
    let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
    assert!(preset::Lease::read(&backend).is_err());
    ric.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn heartbeat_observes_confirmed_timelapse_policy_without_io_or_waiting() {
    let root = task_temp("timelapse-heartbeat");
    let mut backend = backend(&root, "127.0.0.1:9".parse().unwrap());
    fs::write(root.join("uptime"), b"123.0 0.0\n").unwrap();
    backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
        uptime: root.join("uptime"),
        ..crate::camera::CameraPaths::default()
    });
    backend.timelapse = Arc::new(Service::new(
        root.join("policy.json"),
        storage::Store::default(),
    ));
    let service = &backend.timelapse;
    let observe = || {
        let response = backend.heartbeat(Instant::now()).unwrap();
        crate::json::parse(&response.body)
            .unwrap()
            .get_path("timelapse_enabled")
            .unwrap()
            .clone()
    };
    assert_eq!(observe(), Value::Null);
    service.started.store(true, Ordering::Release);
    assert_eq!(observe(), Value::Null); // worker has not loaded policy yet
    service.load(); // absent policy is the disabled default
    assert_eq!(observe(), Value::Bool(false));
    assert_eq!(
        service.value().get_path("data.available"),
        Some(&Value::Bool(true))
    );
    assert_eq!(
        service.value().get_path("data.timelapse.enabled"),
        Some(&Value::Bool(false))
    );

    let policy = Policy {
        enabled: true,
        ..Policy::default()
    };
    service.persist(&policy).unwrap();
    service.load();
    assert_eq!(observe(), Value::Bool(true));
    assert_eq!(
        service.value().get_path("data.timelapse.enabled"),
        Some(&Value::Bool(true))
    );
    // Changing the file alone cannot affect a memory observation.
    fs::write(&service.config, b"{}").unwrap();
    assert_eq!(observe(), Value::Bool(true));
    service.state.lock().unwrap().last_error = Some("capture_failed");
    assert_eq!(observe(), Value::Bool(true));
    {
        let _held = service.state.lock().unwrap();
        assert_eq!(observe(), Value::Null); // returns even while this thread owns the lock
    }
    service.load(); // malformed saved policy is not a known disabled default
    assert_eq!(observe(), Value::Null);
    fs::remove_file(&service.config).unwrap();
    fs::create_dir(&service.config).unwrap();
    service.load(); // unreadable policy
    assert_eq!(observe(), Value::Null);
    fs::remove_dir(&service.config).unwrap();
    service.persist(&policy).unwrap();
    service.load();
    assert_eq!(observe(), Value::Bool(true));
    service.stop.store(true, Ordering::Release);
    assert_eq!(observe(), Value::Null);
    service.stop.store(false, Ordering::Release);
    service.started.store(false, Ordering::Release);
    assert_eq!(observe(), Value::Null);
    fs::remove_dir_all(root).unwrap();
}
