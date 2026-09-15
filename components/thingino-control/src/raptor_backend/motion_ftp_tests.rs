#[cfg(test)]
mod cases {
    use super::super::*;
    use std::fs;
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpListener;
    use std::process::{Command, Stdio};
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::{Arc, Condvar, Mutex};
    use std::time::{Duration, Instant};

    use crate::camera::motion_events::MOTION_EVENT_VERSION;
    use crate::raptor_backend::motion::tests::sequence;
    use crate::raptor_backend::motion_webhook::CurlTransport;
    use crate::raptor_backend::tests::{backend, task_temp};

    const PRIVACY_OFF: &[u8] = br#"{"status":"ok","supported":true,"video":[false,false],"jpeg_required":false,"audio_required":false}"#;

    fn active(sequence: u64) -> MotionEvent {
        MotionEvent {
            version: MOTION_EVENT_VERSION,
            sequence,
            state: MotionState::Active,
            channel: 1,
            monotonic_ms: 1,
            occurred_unix_ms: None,
            snapshot: None,
            clip: None,
        }
    }

    fn config() -> Config {
        Config {
            enabled: true,
            host: "ftp.example.test".to_owned(),
            port: 21,
            username: "camera".to_owned(),
            password: "secret".to_owned(),
            path: "motion/events".to_owned(),
        }
    }

    fn backend_with_saved_config(
        root: &std::path::Path,
        address: std::net::SocketAddr,
        saved: &Config,
    ) -> RaptorBackend {
        let path = root.join("thingino.json");
        fs::write(
            &path,
            format!(
                "{{\"motion_ftp\":{{\"enabled\":{},\"host\":{},\"port\":{},\"tls_mode\":\"explicit\",\"username\":{},\"password\":{},\"path\":{}}}}}",
                saved.enabled,
                Value::String(saved.host.clone()).to_json(),
                saved.port,
                Value::String(saved.username.clone()).to_json(),
                Value::String(saved.password.clone()).to_json(),
                Value::String(saved.path.clone()).to_json(),
            ),
        )
        .unwrap();
        let mut fixture = backend(root, address);
        fixture.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: path,
            ..crate::camera::CameraPaths::default()
        });
        fixture
    }

    #[test]
    fn filename_and_url_are_fixed_and_bounded() {
        let mut config = config();
        assert_eq!(
            ftp_url(&config, "motion-session-ch1-seq7.jpg"),
            "ftp://ftp.example.test:21/motion/events/motion-session-ch1-seq7.jpg"
        );
        config.host = "2001:db8::1".to_owned();
        assert_eq!(
            ftp_url(&config, "motion-session-ch1-seq7.jpg"),
            "ftp://[2001:db8::1]:21/motion/events/motion-session-ch1-seq7.jpg"
        );
    }

    #[test]
    fn config_readback_is_exact_and_never_discloses_passwords() {
        let root = task_temp("motion-ftp-config");
        let path = root.join("thingino.json");
        fs::write(
            &path,
            r#"{"motion_ftp":{"enabled":true,"host":"saved.ftp.example.test","port":21,"tls_mode":"explicit","username":"saved-user","password":"__SET_LOCALLY__","path":"saved/events"}}"#,
        )
        .unwrap();
        let mut fixture = backend(&root, "127.0.0.1:9".parse().unwrap());
        fixture.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: path,
            ..crate::camera::CameraPaths::default()
        });
        fixture.motion_ftp = Arc::new(Service::with_transport(Arc::new(
            RecordingTransport::default(),
        )));
        fixture
            .motion_ftp
            .apply(Config {
                enabled: false,
                host: "live.ftp.example.test".to_owned(),
                port: 2121,
                username: "live-user".to_owned(),
                password: "__GENERATE_LOCALLY__".to_owned(),
                path: "live/events".to_owned(),
            })
            .unwrap();
        let response = fixture
            .motion_ftp_request("GET", "/api/v1/config/motion-ftp", b"")
            .unwrap()
            .unwrap();
        let body = String::from_utf8(response.body).unwrap();
        for expected in [
            r#""host":"saved.ftp.example.test""#,
            r#""live_host":"live.ftp.example.test""#,
            r#""port":21"#,
            r#""live_port":2121"#,
            r#""path":"saved/events""#,
            r#""live_path":"live/events""#,
            r#""password_set":true"#,
            r#""live_password_set":true"#,
            r#""matches_saved":false"#,
        ] {
            assert!(body.contains(expected), "missing {expected}: {body}");
        }
        assert!(!body.contains("__SET_LOCALLY__"));
        assert!(!body.contains("__GENERATE_LOCALLY__"));
        drop(fixture);
        fs::remove_dir_all(root).unwrap();
    }

    #[derive(Default)]
    struct RecordingTransport {
        uploads: Mutex<Vec<(String, Vec<u8>)>>,
    }

    impl Transport for RecordingTransport {
        fn available(&self) -> bool {
            true
        }

        fn upload(
            &self,
            _config: &Config,
            filename: &str,
            jpeg: &[u8],
            _cancel: TransferCancel<'_>,
        ) -> FtpResult {
            self.uploads
                .lock()
                .unwrap()
                .push((filename.to_owned(), jpeg.to_vec()));
            FtpResult::Stored(226)
        }
    }

    fn snapshot_fixture() -> (std::net::SocketAddr, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let worker = std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error)
                        if error.kind() == std::io::ErrorKind::WouldBlock
                            && Instant::now() < deadline =>
                    {
                        std::thread::sleep(Duration::from_millis(2));
                    }
                    other => panic!("missing snapshot request: {other:?}"),
                }
            };
            stream
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let mut request = [0_u8; 512];
            let count = stream.read(&mut request).unwrap();
            assert!(
                String::from_utf8_lossy(&request[..count]).starts_with("GET /snap.jpg?stream=1 ")
            );
            stream
            .write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\nConnection: close\r\n\r\n\xff\xd8jpeg\xff\xd9",
            )
            .unwrap();
        });
        (address, worker)
    }

    fn privacy_sequence(root: &std::path::Path, replies: usize) -> std::thread::JoinHandle<()> {
        sequence(
            root,
            (0..replies)
                .map(|_| {
                    (
                        br#"{"cmd":"privacy-status"}"#.to_vec(),
                        PRIVACY_OFF.to_vec(),
                    )
                })
                .collect(),
        )
    }

    #[test]
    fn worker_captures_one_stream_one_jpeg_and_uploads_once() {
        let root = task_temp("motion-ftp-worker");
        let (address, snapshot) = snapshot_fixture();
        let privacy = privacy_sequence(&root, 3);
        let mut backend = backend_with_saved_config(&root, address, &config());
        let transport = Arc::new(RecordingTransport::default());
        let mut service = Service::with_transport(transport.clone());
        service.session = Some("0123456789abcdef".to_owned());
        service.apply(config()).unwrap();
        backend.motion_ftp = Arc::new(service);
        let backend = Arc::new(backend);
        backend.motion_ftp.start(Arc::downgrade(&backend)).unwrap();
        assert_eq!(
            backend.motion_ftp.try_send_motion(active(7)),
            MotionEventDisposition::Queued
        );
        let deadline = Instant::now() + Duration::from_secs(2);
        while backend.motion_ftp.runtime.successes.load(Ordering::Acquire) == 0
            && Instant::now() < deadline
        {
            std::thread::sleep(Duration::from_millis(5));
        }
        let uploads = transport.uploads.lock().unwrap();
        assert_eq!(uploads.len(), 1);
        assert_eq!(uploads[0].0, "motion-0123456789abcdef-ch1-seq7.jpg");
        assert_eq!(uploads[0].1, b"\xff\xd8jpeg\xff\xd9");
        drop(uploads);
        drop(backend);
        privacy.join().unwrap();
        snapshot.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn saved_live_mismatch_cancels_before_upload() {
        let root = task_temp("motion-ftp-saved-mismatch");
        let (address, snapshot) = snapshot_fixture();
        let privacy = privacy_sequence(&root, 3);
        let mut saved = config();
        saved.host = "new-saved.ftp.example.test".to_owned();
        let mut backend = backend_with_saved_config(&root, address, &saved);
        let transport = Arc::new(RecordingTransport::default());
        let mut service = Service::with_transport(transport.clone());
        service.session = Some("0123456789abcdef".to_owned());
        service.apply(config()).unwrap();
        backend.motion_ftp = Arc::new(service);
        let backend = Arc::new(backend);
        backend.motion_ftp.start(Arc::downgrade(&backend)).unwrap();
        assert_eq!(
            backend.motion_ftp.try_send_motion(active(10)),
            MotionEventDisposition::Queued
        );
        let deadline = Instant::now() + Duration::from_secs(2);
        while backend
            .motion_ftp
            .runtime
            .cancellations
            .load(Ordering::Acquire)
            == 0
            && Instant::now() < deadline
        {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            backend
                .motion_ftp
                .runtime
                .cancellations
                .load(Ordering::Acquire),
            1
        );
        assert_eq!(
            backend.motion_ftp.runtime.captures.load(Ordering::Acquire),
            1
        );
        assert_eq!(
            backend.motion_ftp.runtime.requests.load(Ordering::Acquire),
            0
        );
        assert!(transport.uploads.lock().unwrap().is_empty());
        drop(backend);
        privacy.join().unwrap();
        snapshot.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    struct CancellingTransport {
        entered: Arc<(Mutex<bool>, Condvar)>,
    }

    impl Transport for CancellingTransport {
        fn available(&self) -> bool {
            true
        }

        fn upload(
            &self,
            _config: &Config,
            _filename: &str,
            _jpeg: &[u8],
            cancel: TransferCancel<'_>,
        ) -> FtpResult {
            let (entered, wake) = &*self.entered;
            *entered.lock().unwrap() = true;
            wake.notify_all();
            let deadline = Instant::now() + Duration::from_secs(2);
            while !cancel.cancelled() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(2));
            }
            if cancel.cancelled() {
                FtpResult::Cancelled
            } else {
                FtpResult::Failed
            }
        }
    }

    #[test]
    fn privacy_generation_cancels_upload_without_holding_mutation_lock() {
        let root = task_temp("motion-ftp-cancel");
        let (address, snapshot) = snapshot_fixture();
        let privacy = privacy_sequence(&root, 3);
        let mut backend = backend_with_saved_config(&root, address, &config());
        let entered = Arc::new((Mutex::new(false), Condvar::new()));
        let mut service = Service::with_transport(Arc::new(CancellingTransport {
            entered: entered.clone(),
        }));
        service.session = Some("0123456789abcdef".to_owned());
        service.apply(config()).unwrap();
        backend.motion_ftp = Arc::new(service);
        let backend = Arc::new(backend);
        backend.motion_ftp.start(Arc::downgrade(&backend)).unwrap();
        assert_eq!(
            backend.motion_ftp.try_send_motion(active(8)),
            MotionEventDisposition::Queued
        );
        let (lock, wake) = &*entered;
        let state = lock.lock().unwrap();
        let (state, wait) = wake
            .wait_timeout_while(state, Duration::from_secs(2), |entered| !*entered)
            .unwrap();
        assert!(*state, "upload did not begin: {wait:?}");
        drop(state);
        let mutation = backend
            .mutation_lock
            .try_lock()
            .expect("upload must not hold the mutation lock");
        drop(mutation);
        backend.ha_privacy_generation.fetch_add(1, Ordering::AcqRel);
        let deadline = Instant::now() + Duration::from_secs(2);
        while backend
            .motion_ftp
            .runtime
            .cancellations
            .load(Ordering::Acquire)
            == 0
            && Instant::now() < deadline
        {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            backend
                .motion_ftp
                .runtime
                .cancellations
                .load(Ordering::Acquire),
            1
        );
        drop(backend);
        privacy.join().unwrap();
        snapshot.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[derive(Default)]
    struct InflightState {
        entered: bool,
        release: bool,
        transport_dropped: bool,
    }

    struct InflightTransport {
        state: Arc<(Mutex<InflightState>, Condvar)>,
    }

    impl Transport for InflightTransport {
        fn available(&self) -> bool {
            true
        }

        fn upload(
            &self,
            _config: &Config,
            _filename: &str,
            _jpeg: &[u8],
            _cancel: TransferCancel<'_>,
        ) -> FtpResult {
            let (state, wake) = &*self.state;
            let mut state = state.lock().unwrap();
            state.entered = true;
            wake.notify_all();
            let (state, _) = wake
                .wait_timeout_while(state, Duration::from_secs(2), |state| !state.release)
                .unwrap();
            if state.release {
                FtpResult::Stored(226)
            } else {
                FtpResult::Failed
            }
        }
    }

    impl Drop for InflightTransport {
        fn drop(&mut self) {
            let (state, wake) = &*self.state;
            state.lock().unwrap().transport_dropped = true;
            wake.notify_all();
        }
    }

    #[test]
    fn dropping_last_backend_during_upload_does_not_join_worker_from_itself() {
        let root = task_temp("motion-ftp-drop-inflight");
        let (address, snapshot) = snapshot_fixture();
        let privacy = privacy_sequence(&root, 3);
        let mut backend = backend_with_saved_config(&root, address, &config());
        let state = Arc::new((Mutex::new(InflightState::default()), Condvar::new()));
        let mut service = Service::with_transport(Arc::new(InflightTransport {
            state: state.clone(),
        }));
        service.session = Some("0123456789abcdef".to_owned());
        service.apply(config()).unwrap();
        backend.motion_ftp = Arc::new(service);
        let backend = Arc::new(backend);
        backend.motion_ftp.start(Arc::downgrade(&backend)).unwrap();
        assert_eq!(
            backend.motion_ftp.try_send_motion(active(9)),
            MotionEventDisposition::Queued
        );
        let (lock, wake) = &*state;
        let state_guard = lock.lock().unwrap();
        let (mut state_guard, wait) = wake
            .wait_timeout_while(state_guard, Duration::from_secs(2), |state| !state.entered)
            .unwrap();
        assert!(state_guard.entered, "upload did not begin: {wait:?}");
        drop(backend);
        state_guard.release = true;
        wake.notify_all();
        let (state_guard, wait) = wake
            .wait_timeout_while(state_guard, Duration::from_secs(2), |state| {
                !state.transport_dropped
            })
            .unwrap();
        assert!(
            state_guard.transport_dropped,
            "worker did not shut down after in-flight upload: {wait:?}"
        );
        drop(state_guard);
        privacy.join().unwrap();
        snapshot.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    const FTPS_FIXTURE: &str = r#"
import socket, ssl, sys, time
scenario, cert, key = sys.argv[1:]
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.settimeout(8)
listener.bind(('127.0.0.1', 0))
listener.listen(1)
print(listener.getsockname()[1], flush=True)
control, _ = listener.accept()
control.settimeout(8)
control.sendall(b'220 fixture\r\n')
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(cert, key)
protected = False
passive = None

def line():
    data = b''
    while not data.endswith(b'\n'):
        part = control.recv(1)
        if not part: return b''
        data += part
    return data.rstrip(b'\r\n')

while True:
    command = line()
    if not command: break
    verb, _, argument = command.partition(b' ')
    if verb == b'AUTH':
        if argument != b'TLS': raise RuntimeError('AUTH TLS required')
        if scenario == 'no-auth-tls':
            control.sendall(b'500 TLS required\r\n')
            break
        control.sendall(b'234 begin TLS\r\n')
        control = context.wrap_socket(control, server_side=True)
        control.settimeout(8)
    elif verb == b'USER':
        if argument != b'fixture-user': raise RuntimeError('bad username')
        control.sendall(b'331 password\r\n')
    elif verb == b'PASS':
        if argument != b'fixture-password': raise RuntimeError('bad password')
        control.sendall(b'230 logged in\r\n')
    elif verb == b'PBSZ':
        control.sendall(b'200 PBSZ 0\r\n')
    elif verb == b'PROT':
        if argument != b'P': raise RuntimeError('PROT P required')
        protected = True
        control.sendall(b'200 protected\r\n')
    elif verb == b'PWD':
        control.sendall(b'257 "/"\r\n')
    elif verb == b'CWD':
        control.sendall(b'250 directory ok\r\n')
    elif verb == b'TYPE':
        control.sendall(b'200 binary\r\n')
    elif verb == b'EPSV':
        control.sendall(b'500 use PASV\r\n')
    elif verb == b'PASV':
        passive = socket.socket()
        passive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        passive.settimeout(8)
        passive.bind(('127.0.0.1', 0))
        passive.listen(1)
        port = passive.getsockname()[1]
        control.sendall(f'227 (192,0,2,1,{port // 256},{port % 256})\r\n'.encode())
    elif verb == b'STOR':
        if not protected: raise RuntimeError('unprotected data channel')
        if scenario == 'reject-stor':
            control.sendall(b'550 rejected\r\n')
            continue
        if passive is None: raise RuntimeError('missing passive listener')
        control.sendall(b'150 opening protected data\r\n')
        data, _ = passive.accept()
        data.settimeout(8)
        data = context.wrap_socket(data, server_side=True)
        if scenario == 'slow-data':
            print('data', flush=True)
            time.sleep(1)
        payload = b''
        while True:
            part = data.recv(8192)
            if not part: break
            payload += part
        data.close()
        passive.close()
        if payload != b'\xff\xd8fixture\xff\xd9': raise RuntimeError('unexpected JPEG')
        control.sendall(b'226 stored\r\n')
    elif verb == b'QUIT':
        control.sendall(b'221 bye\r\n')
        break
    else:
        control.sendall(b'200 ok\r\n')
control.close()
listener.close()
"#;

    #[test]
    fn curl_transport_requires_verified_explicit_ftps_protected_data_and_stor_ack() {
        let root = task_temp("motion-ftps");
        let cert = root.join("localhost.crt");
        let key = root.join("localhost.key");
        let untrusted_cert = root.join("untrusted.crt");
        let untrusted_key = root.join("untrusted.key");
        generate_certificate(&cert, &key, "localhost");
        generate_certificate(&untrusted_cert, &untrusted_key, "localhost");
        for (scenario, server_cert, server_key, ca, host, expected, server_ok) in [
            (
                "success",
                &cert,
                &key,
                &cert,
                "localhost",
                FtpResult::Stored(226),
                true,
            ),
            (
                "no-auth-tls",
                &cert,
                &key,
                &cert,
                "localhost",
                FtpResult::Failed,
                true,
            ),
            (
                "reject-stor",
                &cert,
                &key,
                &cert,
                "localhost",
                FtpResult::Failed,
                true,
            ),
            (
                "success",
                &untrusted_cert,
                &untrusted_key,
                &cert,
                "localhost",
                FtpResult::Failed,
                false,
            ),
            (
                "success",
                &cert,
                &key,
                &cert,
                "127.0.0.1",
                FtpResult::Failed,
                false,
            ),
        ] {
            let (result, output) =
                run_ftps_fixture(scenario, server_cert, server_key, ca, host, false);
            assert_eq!(result, expected, "{scenario}");
            if server_ok {
                assert!(output.status.success(), "{scenario}");
            }
            assert!(!String::from_utf8_lossy(&output.stderr).contains("fixture-password"));
        }
        let (result, _) = run_ftps_fixture("success", &cert, &key, &cert, "localhost", true);
        assert_eq!(result, FtpResult::Cancelled);
        assert_eq!(
            run_inflight_cancel_fixture(&cert, &key),
            FtpResult::Cancelled
        );
        fs::remove_dir_all(root).unwrap();
    }

    fn generate_certificate(cert: &std::path::Path, key: &std::path::Path, common_name: &str) {
        let status = Command::new("/usr/bin/openssl")
            .args([
                "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            ])
            .arg("-keyout")
            .arg(key)
            .arg("-out")
            .arg(cert)
            .arg("-subj")
            .arg(format!("/CN={common_name}"))
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap();
        assert!(status.success());
    }

    fn run_ftps_fixture(
        scenario: &str,
        cert: &std::path::Path,
        key: &std::path::Path,
        ca: &std::path::Path,
        host: &str,
        cancel: bool,
    ) -> (FtpResult, std::process::Output) {
        let mut child = Command::new("/usr/bin/python3")
            .args(["-c", FTPS_FIXTURE, scenario])
            .arg(cert)
            .arg(key)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut port = String::new();
        stdout.read_line(&mut port).unwrap();
        let config_generation = AtomicU32::new(u32::from(cancel));
        let privacy_generation = AtomicU32::new(0);
        let url = format!("ftp://{host}:{}/motion.jpg", port.trim());
        let request = FtpRequest {
            url: &url,
            username: "fixture-user",
            password: "fixture-password",
            jpeg: b"\xff\xd8fixture\xff\xd9",
            cancel: TransferCancel {
                config_generation: &config_generation,
                expected_config_generation: 0,
                privacy_generation: &privacy_generation,
                expected_privacy_generation: 0,
            },
        };
        let result = CurlTransport::load().upload_explicit_ftps_with_ca(&request, Some(ca));
        drop(stdout);
        if cancel {
            child.kill().unwrap();
            return (result, child.wait_with_output().unwrap());
        }
        let deadline = Instant::now() + Duration::from_secs(9);
        while child.try_wait().unwrap().is_none() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        if child.try_wait().unwrap().is_none() {
            child.kill().unwrap();
        }
        (result, child.wait_with_output().unwrap())
    }

    fn run_inflight_cancel_fixture(cert: &std::path::Path, key: &std::path::Path) -> FtpResult {
        let mut child = Command::new("/usr/bin/python3")
            .args(["-c", FTPS_FIXTURE, "slow-data"])
            .arg(cert)
            .arg(key)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut port = String::new();
        stdout.read_line(&mut port).unwrap();
        let config_generation = Arc::new(AtomicU32::new(0));
        let privacy_generation = Arc::new(AtomicU32::new(0));
        let worker_config = config_generation.clone();
        let worker_privacy = privacy_generation.clone();
        let url = format!("ftp://localhost:{}/motion.jpg", port.trim());
        let trusted_ca = cert.to_path_buf();
        let upload = std::thread::spawn(move || {
            let request = FtpRequest {
                url: &url,
                username: "fixture-user",
                password: "fixture-password",
                jpeg: b"\xff\xd8fixture\xff\xd9",
                cancel: TransferCancel {
                    config_generation: &worker_config,
                    expected_config_generation: 0,
                    privacy_generation: &worker_privacy,
                    expected_privacy_generation: 0,
                },
            };
            CurlTransport::load().upload_explicit_ftps_with_ca(&request, Some(&trusted_ca))
        });
        let mut marker = String::new();
        stdout.read_line(&mut marker).unwrap();
        assert_eq!(marker.trim(), "data");
        config_generation.fetch_add(1, Ordering::AcqRel);
        let result = upload.join().unwrap();
        drop(stdout);
        if child.try_wait().unwrap().is_none() {
            child.kill().unwrap();
        }
        let output = child.wait_with_output().unwrap();
        assert!(!String::from_utf8_lossy(&output.stderr).contains("fixture-password"));
        result
    }
}
