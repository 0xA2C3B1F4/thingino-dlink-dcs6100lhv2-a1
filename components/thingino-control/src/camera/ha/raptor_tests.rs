//! Real disposable localhost broker with framed Raptor owner fixtures.
use super::*;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener};
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

static BROKER_TEST_LOCK: Mutex<()> = Mutex::new(());

struct Fixture {
    root: PathBuf,
    backend: Arc<crate::RaptorBackend>,
    running: Arc<AtomicBool>,
    motion: Arc<AtomicBool>,
    privacy: Arc<AtomicBool>,
    capture_started: Arc<AtomicBool>,
    release_capture: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<String>>>,
    threads: Vec<thread::JoinHandle<()>>,
}

impl Fixture {
    fn new(port: u16) -> Self {
        let root = PathBuf::from(std::env::var_os("TMPDIR").unwrap())
            .join(format!("raptor-ha-broker-{}-{port}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("hostname"), "raptor-ha-fixture\n").unwrap();
        fs::write(root.join("uptime"), "42.0 0.0\n").unwrap();
        fs::write(root.join("os-release"), "IMAGE_ID=a1\nCOMMIT_ID=fixture\n").unwrap();
        fs::write(root.join("thingino.json"), format!(r#"{{"ha":{{"enabled":true,"state_interval":1,"camera_interval":5,"mqtt":{{"host":"127.0.0.1","port":{port}}}}}}}"#)).unwrap();
        let running = Arc::new(AtomicBool::new(true));
        let motion = Arc::new(AtomicBool::new(true));
        let privacy = Arc::new(AtomicBool::new(false));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let capture_started = Arc::new(AtomicBool::new(false));
        let release_capture = Arc::new(AtomicBool::new(true));
        let mut threads = Vec::new();
        for name in ["rvd", "rhd"] {
            let listener = UnixListener::bind(root.join(format!("{name}.sock"))).unwrap();
            listener.set_nonblocking(true).unwrap();
            let running = Arc::clone(&running);
            let motion = Arc::clone(&motion);
            let privacy = Arc::clone(&privacy);
            let requests = Arc::clone(&requests);
            threads.push(thread::spawn(move || {
                while running.load(Ordering::Acquire) {
                    let (mut socket, _) = match listener.accept() {
                        Ok(value) => value,
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                        Err(e) => panic!("fixture accept: {e}"),
                    };
                    socket.set_nonblocking(false).unwrap();
                    socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                    let mut header = [0; 2];
                    if socket.read_exact(&mut header).is_err() {
                        continue;
                    }
                    let mut body = vec![0; u16::from_be_bytes(header) as usize];
                    socket.read_exact(&mut body).unwrap();
                    let mut eof = [0];
                    assert_eq!(socket.read(&mut eof).unwrap(), 0);
                    let request = json::parse(&body).unwrap();
                    let cmd = request.get_path("cmd").and_then(Value::as_str).unwrap();
                    requests.lock().unwrap().push(format!("{name}:{cmd}"));
                    let active = motion.load(Ordering::Acquire);
                    let masked = privacy.load(Ordering::Acquire);
                    let reply = match (name, cmd) {
                        ("rvd", "ivs-status") => format!(r#"{{"status":"ok","supported":true,"active":{active},"receiving":{active},"motion":{active}}}"#),
                        ("rvd", "ivs-enable") => {
                            let value = request.get_path("value").and_then(Value::as_bool).unwrap();
                            motion.store(value, Ordering::Release);
                            format!(r#"{{"status":"ok","active":{value}}}"#)
                        }
                        ("rvd", "privacy-status") => format!(r#"{{"status":"ok","supported":true,"video":[{masked},{masked}],"jpeg_required":false,"audio_required":false}}"#),
                        ("rvd", "privacy") => {
                            privacy.store(request.get_path("value").and_then(Value::as_str) == Some("on"), Ordering::Release);
                            r#"{"status":"ok","complete":true}"#.into()
                        }
                        ("rvd", "status") => r#"{"status":"ok","configured":[true,false],"streams":[{"chn":0,"stream_id":0,"jpeg":false,"available":true,"w":1920,"h":1080,"codec":0,"bitrate":750000,"avg_bitrate":0,"gop":25,"fps":15},{"chn":2,"stream_id":0,"jpeg":true,"available":true,"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":0,"gop":0,"fps":1}]}"#.into(),
                        ("rhd", "status") => format!(r#"{{"status":"ok","clients":0,"mjpeg":0,"audio":0,"port":8080,"jpeg_rings":2,"jpeg_available":[true,true],"exif_timestamp":false,"sign_snapshots":false,"privacy":{masked},"tls":false}}"#),
                        _ => r#"{"status":"error"}"#.into(),
                    };
                    let _ = socket.write_all(&(reply.len() as u16).to_be_bytes());
                    let _ = socket.write_all(reply.as_bytes());
                }
            }));
        }
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address: SocketAddr = listener.local_addr().unwrap();
        let backend = Arc::new(crate::RaptorBackend::ha_fixture(&root, address));
        let http_running = Arc::clone(&running);
        let started = Arc::clone(&capture_started);
        let release = Arc::clone(&release_capture);
        threads.push(thread::spawn(move || {
            while http_running.load(Ordering::Acquire) {
                let (mut socket, _) = match listener.accept() {
                    Ok(value) => value,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(e) => panic!("HTTP fixture: {e}"),
                };
                socket.set_nonblocking(false).unwrap();
                socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                let mut request = [0; 1024];
                let count = socket.read(&mut request).unwrap();
                assert!(request[..count].starts_with(b"GET /snap.jpg?stream=0 "));
                started.store(true, Ordering::Release);
                let until = Instant::now()+Duration::from_secs(3);
                while !release.load(Ordering::Acquire)
                    && http_running.load(Ordering::Acquire)
                    && Instant::now() < until
                {
                    thread::sleep(Duration::from_millis(2));
                }
                let _ = socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n\xff\xd8\xff\xd9");
            }
        }));
        Self {
            root,
            backend,
            running,
            motion,
            privacy,
            capture_started,
            release_capture,
            requests,
            threads,
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.release_capture.store(true, Ordering::Release);
        self.backend.shutdown_ha().unwrap();
        self.running.store(false, Ordering::Release);
        for thread in self.threads.drain(..) {
            thread.join().unwrap();
        }
        fs::remove_dir_all(&self.root).unwrap();
    }
}

fn connect(port: u16, id: &str) -> mqtt::Client {
    let mut client = mqtt::Client::new(mqtt::Api::load().unwrap(), id).unwrap();
    client.set_test_inbound_payload_limit(mqtt::MAX_PAYLOAD_BYTES);
    client.connect("127.0.0.1", port, 5).unwrap();
    let until = Instant::now() + Duration::from_secs(3);
    while client.take_connect_result() != Some(0) {
        assert!(Instant::now() < until);
        client.loop_once(20).unwrap();
    }
    client
}

fn wait_message(client: &mut mqtt::Client, suffix: &str, payload: &[u8]) -> mqtt::Message {
    let until = Instant::now() + Duration::from_secs(7);
    while Instant::now() < until {
        client.loop_once(20).unwrap();
        while let Some(message) = client.take_message() {
            if message.topic.ends_with(suffix) && message.payload == payload {
                return message;
            }
        }
    }
    panic!("missing MQTT {suffix}: {payload:?}");
}

#[test]
fn raptor_session_discovery_commands_owner_recovery_and_reconnect() {
    let _broker_guard = BROKER_TEST_LOCK.lock().unwrap();
    let Some(port) = std::env::var("THINGINO_HA_TEST_BROKER_PORT")
        .ok()
        .and_then(|v| v.parse::<u16>().ok())
    else {
        return;
    };
    let fixture = Fixture::new(port);
    let mut observer = connect(port, "raptor-ha-observer");
    for suffix in [
        "status",
        "motion/state",
        "motion/availability",
        "motion_guard/state",
        "live_view/image",
        "live_view/availability",
    ] {
        observer
            .subscribe(&format!("cameras/raptor-ha-fixture/{suffix}"))
            .unwrap();
    }
    observer.loop_once(20).unwrap();
    fixture.backend.start_ha();
    wait_message(&mut observer, "/motion/availability", b"online");
    wait_message(&mut observer, "/live_view/image", b"\xff\xd8\xff\xd9");
    // A new subscription proves discovery/state/availability are retained.
    let mut retained = connect(port, "raptor-ha-retained");
    retained
        .subscribe("homeassistant/binary_sensor/thingino_raptor-ha-fixture/motion/config")
        .unwrap();
    let until = Instant::now() + Duration::from_secs(3);
    let discovery = loop {
        assert!(Instant::now() < until);
        retained.loop_once(20).unwrap();
        if let Some(message) = retained.take_message() {
            break message;
        }
    };
    assert!(discovery.retained);
    let document = json::parse(&discovery.payload).unwrap();
    assert_eq!(
        document
            .get_path("availability_mode")
            .and_then(Value::as_str),
        Some("all")
    );
    assert_eq!(
        document
            .get_path("availability")
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        2
    );
    retained
        .subscribe("cameras/raptor-ha-fixture/motion/availability")
        .unwrap();
    assert!(wait_message(&mut retained, "/motion/availability", b"online").retained);
    observer
        .publish("cameras/raptor-ha-fixture/motion_guard/set", b"OFF", false)
        .unwrap();
    wait_message(&mut observer, "/motion_guard/state", b"OFF");
    assert!(!fixture.motion.load(Ordering::Acquire));
    let requests = fixture.requests.lock().unwrap().clone();
    let command = requests.iter().position(|r| r == "rvd:ivs-enable").unwrap();
    assert!(
        requests[command + 1..]
            .iter()
            .any(|r| r == "rvd:ivs-status")
    );
    retained
        .subscribe("cameras/raptor-ha-fixture/motion_guard/state")
        .unwrap();
    assert!(wait_message(&mut retained, "/motion_guard/state", b"OFF").retained);
    fs::rename(
        fixture.root.join("rvd.sock"),
        fixture.root.join("rvd.offline"),
    )
    .unwrap();
    wait_message(&mut observer, "/motion/availability", b"offline");
    wait_message(&mut observer, "/motion/state", b"");
    fs::rename(
        fixture.root.join("rvd.offline"),
        fixture.root.join("rvd.sock"),
    )
    .unwrap();
    fixture.motion.store(true, Ordering::Release);
    wait_message(&mut observer, "/motion/state", b"ON");
    wait_message(&mut observer, "/motion/availability", b"online");
    fixture
        .backend
        .ha_service()
        .action(br#"{"action":"reconnect"}"#)
        .unwrap();
    wait_message(&mut observer, "/status", b"offline");
    wait_message(&mut observer, "/status", b"online");
    wait_message(&mut observer, "/live_view/image", b"\xff\xd8\xff\xd9");
}

#[test]
fn raptor_saved_state_interval_preserves_prompt_motion_and_explicit_refresh() {
    let _broker_guard = BROKER_TEST_LOCK.lock().unwrap();
    let Some(port) = std::env::var("THINGINO_HA_TEST_BROKER_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
    else {
        return;
    };
    let fixture = Fixture::new(port);
    // A distinct device ID excludes retained publications from other broker tests.
    fs::write(fixture.root.join("hostname"), "raptor-ha-interval\n").unwrap();
    fs::write(
        fixture.root.join("thingino.json"),
        format!(r#"{{"ha":{{"enabled":true,"state_interval":8,"enable_live_view":false,"mqtt":{{"host":"127.0.0.1","port":{port}}}}}}}"#),
    ).unwrap();
    let mut observer = connect(port, "raptor-ha-interval-observer");
    observer
        .subscribe("cameras/raptor-ha-interval/privacy/state")
        .unwrap();
    observer
        .subscribe("cameras/raptor-ha-interval/motion/state")
        .unwrap();
    observer
        .subscribe("cameras/raptor-ha-interval/motion_guard/state")
        .unwrap();
    observer.loop_once(20).unwrap();
    fixture.backend.start_ha();
    wait_message(&mut observer, "/privacy/state", b"OFF");
    let initial_state = Instant::now();
    fixture.privacy.store(true, Ordering::Release);
    fixture.motion.store(false, Ordering::Release);
    wait_message(&mut observer, "/motion/state", b"OFF");
    assert!(
        initial_state.elapsed() < Duration::from_secs(3),
        "motion waited for the full state interval"
    );

    // Old Raptor code forced every saved interval down to five seconds. The
    // changed Privacy observation must not be published at that old deadline.
    while initial_state.elapsed() < Duration::from_secs(6) {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            assert!(
                !(message.topic.ends_with("/privacy/state") && message.payload == b"ON"),
                "full state refreshed before the saved eight-second interval"
            );
        }
    }
    wait_message(&mut observer, "/privacy/state", b"ON");
    assert!(initial_state.elapsed() < Duration::from_secs(12));

    fixture.privacy.store(false, Ordering::Release);
    let refresh = Instant::now();
    fixture
        .backend
        .ha_service()
        .action(br#"{"action":"publish_state"}"#)
        .unwrap();
    wait_message(&mut observer, "/privacy/state", b"OFF");
    assert!(
        refresh.elapsed() < Duration::from_secs(3),
        "explicit refresh waited for the periodic timer"
    );

    let command = Instant::now();
    observer
        .publish("cameras/raptor-ha-interval/motion_guard/set", b"ON", false)
        .unwrap();
    wait_message(&mut observer, "/motion_guard/state", b"ON");
    assert!(
        command.elapsed() < Duration::from_secs(3),
        "MQTT command readback waited for the periodic timer"
    );
    assert!(fixture.motion.load(Ordering::Acquire));
    assert!(
        fixture
            .requests
            .lock()
            .unwrap()
            .iter()
            .any(|request| request == "rvd:ivs-enable")
    );
}

#[test]
fn delayed_jpeg_preview_contention_measurement() {
    let fixture = Fixture::new(1);
    fixture.release_capture.store(false, Ordering::Release);
    let backend = Arc::clone(&fixture.backend);
    let capture = thread::spawn(move || backend.ha_publish_snapshot(|_| Ok(())));
    let until = Instant::now() + Duration::from_secs(1);
    while !fixture.capture_started.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(2));
    }
    let mut maximum = Duration::ZERO;
    for n in 0..10 {
        let start = Instant::now();
        fixture
            .backend
            .ha_execute_command(Command::MotionGuard(n % 2 == 0))
            .unwrap();
        maximum = maximum.max(start.elapsed());
        assert!(start.elapsed() < Duration::from_millis(300));
        thread::sleep(Duration::from_millis(100));
    }
    eprintln!(
        "delayed JPEG: 10 Preview commands succeeded while capture blocked for >1 s; maximum latency={maximum:?}"
    );
    fixture.release_capture.store(true, Ordering::Release);
    capture.join().unwrap().unwrap();
}

#[test]
fn captured_snapshot_is_discarded_when_generation_changes_before_publication() {
    let fixture = Fixture::new(3);
    fixture.release_capture.store(false, Ordering::Release);
    let current = Arc::new(AtomicBool::new(true));
    let capture_current = Arc::clone(&current);
    let backend = super::backend::HaBackend::Raptor(Arc::clone(&fixture.backend));
    let capture = thread::spawn(move || {
        backend.with_snapshot(0, &|| capture_current.load(Ordering::Acquire), |_| {
            panic!("stale generation published")
        })
    });
    let until = Instant::now() + Duration::from_secs(1);
    while !fixture.capture_started.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(2));
    }
    current.store(false, Ordering::Release);
    fixture.release_capture.store(true, Ordering::Release);
    assert!(matches!(
        capture.join().unwrap(),
        Err(BackendError::Unavailable)
    ));
}

#[test]
fn privacy_on_then_off_during_capture_discards_the_old_image() {
    let fixture = Fixture::new(2);
    fixture.release_capture.store(false, Ordering::Release);
    let backend = Arc::clone(&fixture.backend);
    let capture = thread::spawn(move || {
        backend.ha_publish_snapshot(|_| panic!("image survived privacy transition"))
    });
    let until = Instant::now() + Duration::from_secs(1);
    while !fixture.capture_started.load(Ordering::Acquire) {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(2));
    }
    fixture
        .backend
        .ha_execute_command(Command::Privacy(true))
        .unwrap();
    assert!(fixture.privacy.load(Ordering::Acquire));
    fixture
        .backend
        .ha_execute_command(Command::Privacy(false))
        .unwrap();
    assert!(!fixture.privacy.load(Ordering::Acquire));
    fixture.release_capture.store(true, Ordering::Release);
    assert!(matches!(
        capture.join().unwrap(),
        Err(BackendError::Unavailable)
    ));
}

#[test]
fn raptor_image_write_failure_drops_session_and_reconnects_without_replay() {
    let _broker_guard = BROKER_TEST_LOCK.lock().unwrap();
    let Some(port) = std::env::var("THINGINO_HA_TEST_BROKER_PORT")
        .ok()
        .and_then(|v| v.parse::<u16>().ok())
    else {
        return;
    };
    let fixture = Fixture::new(port);
    let mut observer = connect(port, "raptor-ha-image-failure-observer");
    for suffix in ["status", "live_view/image", "live_view/availability"] {
        observer
            .subscribe(&format!("cameras/raptor-ha-fixture/{suffix}"))
            .unwrap();
    }
    observer.loop_once(20).unwrap();
    fixture.backend.start_ha();
    wait_message(&mut observer, "/live_view/image", b"\xff\xd8\xff\xd9");
    fixture.backend.ha_service().fail_next_image_write();
    observer
        .publish("cameras/raptor-ha-fixture/snapshot/set", b"1", false)
        .unwrap();
    // The write fault is injected at the MQTT image boundary. Session teardown,
    // retained LWT delivery and subsequent reconnect use the real local broker.
    wait_message(&mut observer, "/status", b"offline");
    // No capture may be republished while the new session checks unknown Privacy.
    fixture.privacy.store(true, Ordering::Release);
    wait_message(&mut observer, "/live_view/availability", b"offline");
    wait_message(&mut observer, "/status", b"online");
    let until = Instant::now() + Duration::from_millis(300);
    while Instant::now() < until {
        observer.loop_once(20).unwrap();
        while let Some(message) = observer.take_message() {
            assert!(
                !message.topic.ends_with("/image"),
                "failed JPEG was replayed"
            );
        }
    }
    fixture.privacy.store(false, Ordering::Release);
    observer
        .publish("cameras/raptor-ha-fixture/snapshot/set", b"1", false)
        .unwrap();
    wait_message(&mut observer, "/live_view/image", b"\xff\xd8\xff\xd9");
}
