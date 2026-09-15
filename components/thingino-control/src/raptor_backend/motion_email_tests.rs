#[cfg(test)]
mod cases {
    use super::super::super::tests::{backend, task_temp};
    use super::super::*;
    use crate::camera::motion_events::MOTION_EVENT_VERSION;
    use std::fs;
    use std::io::{BufRead, BufReader};
    use std::process::{Command, Stdio};
    use std::sync::Condvar;
    use std::time::{Duration, Instant};

    fn config() -> Config {
        Config {
            enabled: true,
            host: "mail.example.test".to_owned(),
            port: 587,
            tls_mode: TlsMode::Starttls,
            username: "camera".to_owned(),
            password: "secret".to_owned(),
            from_address: "camera@example.test".to_owned(),
            to_address: "owner@example.test".to_owned(),
        }
    }

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

    #[test]
    fn tls_modes_build_only_smtp_urls_and_message_is_fixed_text() {
        assert_eq!(smtp_url(&config()), "smtp://mail.example.test:587");
        let mut implicit = config();
        implicit.tls_mode = TlsMode::Implicit;
        implicit.port = 465;
        assert_eq!(smtp_url(&implicit), "smtps://mail.example.test:465");
        implicit.host = "2001:db8::1".to_owned();
        assert_eq!(smtp_url(&implicit), "smtps://[2001:db8::1]:465");
        assert_eq!(
            smtp_message("camera@example.test", "owner@example.test"),
            "From: <camera@example.test>\r\nTo: <owner@example.test>\r\nSubject: Motion\r\n\r\nMotion detected.\r\n"
        );
    }

    #[test]
    fn config_readback_is_exact_and_never_discloses_passwords() {
        let root = task_temp("motion-email-config");
        let path = root.join("thingino.json");
        fs::write(
            &path,
            r#"{"motion_email":{"enabled":true,"host":"saved.example.test","port":587,"tls_mode":"starttls","username":"camera","password":"__SET_LOCALLY__","from_address":"camera@example.test","to_address":"owner@example.test"}}"#,
        )
        .unwrap();
        let mut fixture = backend(&root, "127.0.0.1:9".parse().unwrap());
        fixture.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: path,
            ..crate::camera::CameraPaths::default()
        });
        fixture.motion_email = Arc::new(Service::with_transport(Arc::new(StatusTransport)));
        let mut live = config();
        live.host = "live.example.test".to_owned();
        live.password = "__GENERATE_LOCALLY__".to_owned();
        fixture.motion_email.apply(live).unwrap();
        let response = fixture
            .motion_email_request("GET", "/api/v1/config/motion-email", b"")
            .unwrap()
            .unwrap();
        let body = String::from_utf8(response.body).unwrap();
        assert!(body.contains("\"host\":\"saved.example.test\""));
        assert!(body.contains("\"live_host\":\"live.example.test\""));
        assert!(body.contains("\"password_set\":true"));
        assert!(body.contains("\"live_password_set\":true"));
        assert!(body.contains("\"matches_saved\":false"));
        assert!(!body.contains("__SET_LOCALLY__"));
        assert!(!body.contains("__GENERATE_LOCALLY__"));
        drop(fixture);
        fs::remove_dir_all(root).unwrap();
    }

    const SMTP_TLS_FIXTURE: &str = r#"
import base64, socket, ssl, sys, time
mode, scenario, cert, key = sys.argv[1:]
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.settimeout(8)
listener.bind(('127.0.0.1', 0))
listener.listen(1)
print(listener.getsockname()[1], flush=True)
raw, _ = listener.accept()
raw.settimeout(8)
if scenario == 'timeout':
    time.sleep(6)
    raw.close()
    listener.close()
    sys.exit(0)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(cert, key)
conn = context.wrap_socket(raw, server_side=True) if mode == 'implicit' else raw
conn.sendall(b'220 localhost fixture\r\n')

def line():
    data = b''
    while not data.endswith(b'\n'):
        part = conn.recv(1)
        if not part:
            raise RuntimeError('unexpected EOF')
        data += part
    return data

if not line().startswith(b'EHLO '): raise RuntimeError('missing EHLO')
if mode == 'starttls':
    if scenario == 'missing-starttls':
        conn.sendall(b'250 localhost\r\n')
        conn.close()
        listener.close()
        sys.exit(0)
    conn.sendall(b'250-localhost\r\n250 STARTTLS\r\n')
    if line() != b'STARTTLS\r\n': raise RuntimeError('missing STARTTLS')
    if scenario == 'starttls-failed':
        conn.sendall(b'454 TLS unavailable\r\n')
        conn.close()
        listener.close()
        sys.exit(0)
    conn.sendall(b'220 begin TLS\r\n')
    conn = context.wrap_socket(conn, server_side=True)
    if not line().startswith(b'EHLO '): raise RuntimeError('missing TLS EHLO')
if scenario in ('auth-success', 'auth-535'):
    conn.sendall(b'250-localhost\r\n250 AUTH PLAIN LOGIN\r\n')
    auth = line().rstrip(b'\r\n')
    if auth.startswith(b'AUTH PLAIN '):
        credentials = base64.b64decode(auth.split(b' ', 2)[2])
    elif auth == b'AUTH PLAIN':
        conn.sendall(b'334 \r\n')
        credentials = base64.b64decode(line().strip())
    elif auth == b'AUTH LOGIN':
        conn.sendall(b'334 VXNlcm5hbWU6\r\n')
        username = base64.b64decode(line().strip())
        conn.sendall(b'334 UGFzc3dvcmQ6\r\n')
        password = base64.b64decode(line().strip())
        credentials = b'\0' + username + b'\0' + password
    else:
        raise RuntimeError('missing supported AUTH')
    if credentials != b'\0fixture-user\0fixture-password':
        raise RuntimeError('bad credentials')
    if scenario == 'auth-535':
        conn.sendall(b'535 authentication rejected\r\n')
        conn.close()
        listener.close()
        sys.exit(0)
    conn.sendall(b'235 authenticated\r\n')
else:
    conn.sendall(b'250 localhost\r\n')
if not line().startswith(b'MAIL FROM:<camera@example.test>'): raise RuntimeError('bad sender')
conn.sendall(b'250 sender ok\r\n')
if line() != b'RCPT TO:<owner@example.test>\r\n': raise RuntimeError('bad recipient')
conn.sendall(b'250 recipient ok\r\n')
if line() != b'DATA\r\n': raise RuntimeError('missing DATA')
conn.sendall(b'354 send message\r\n')
message = b''
while True:
    item = line()
    if item == b'.\r\n': break
    message += item
expected = b'From: <camera@example.test>\r\nTo: <owner@example.test>\r\nSubject: Motion\r\n\r\nMotion detected.\r\n'
if message != expected: raise RuntimeError('unexpected message')
status = b'450 temporarily rejected\r\n' if scenario == 'smtp-450' else b'550 rejected\r\n' if scenario == 'smtp-550' else b'250 queued\r\n'
conn.sendall(status)
conn.close()
listener.close()
"#;

    #[test]
    fn curl_transport_enforces_tls_protocol_and_smtp_acknowledgement() {
        let root = task_temp("motion-email-tls");
        let cert = root.join("localhost.crt");
        let key = root.join("localhost.key");
        let wrong_cert = root.join("wrong.crt");
        let wrong_key = root.join("wrong.key");
        let untrusted_cert = root.join("untrusted.crt");
        let untrusted_key = root.join("untrusted.key");
        generate_certificate(&cert, &key, "localhost");
        generate_certificate(&wrong_cert, &wrong_key, "wrong.example.test");
        generate_certificate(&untrusted_cert, &untrusted_key, "localhost");
        let curl = super::super::super::motion_webhook::CurlTransport::load();
        assert!(curl.supports_protocols(&["smtp", "smtps"]));
        for (mode, scenario, server_cert, server_key, trusted_ca, host, expected) in [
            (
                "starttls",
                "success",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(250),
            ),
            (
                "implicit",
                "success",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(250),
            ),
            (
                "starttls",
                "auth-success",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(250),
            ),
            (
                "implicit",
                "auth-535",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(535),
            ),
            (
                "starttls",
                "unknown-ca",
                &untrusted_cert,
                &untrusted_key,
                &cert,
                "localhost",
                Err(()),
            ),
            (
                "starttls",
                "wrong-host",
                &wrong_cert,
                &wrong_key,
                &wrong_cert,
                "localhost",
                Err(()),
            ),
            (
                "starttls",
                "missing-starttls",
                &cert,
                &key,
                &cert,
                "localhost",
                Err(()),
            ),
            (
                "starttls",
                "starttls-failed",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(454),
            ),
            (
                "starttls",
                "smtp-450",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(450),
            ),
            (
                "implicit",
                "smtp-550",
                &cert,
                &key,
                &cert,
                "localhost",
                Ok(550),
            ),
            (
                "starttls",
                "timeout",
                &cert,
                &key,
                &cert,
                "localhost",
                Err(()),
            ),
        ] {
            eprintln!("SMTP fixture {mode}/{scenario}");
            let started = Instant::now();
            let (result, output) = run_smtp_fixture(
                &curl,
                mode,
                scenario,
                server_cert,
                server_key,
                trusted_ca,
                host,
            );
            assert_eq!(result, expected, "{mode}/{scenario} result");
            let accepted = matches!(result, Ok(200..=299));
            assert_eq!(
                accepted,
                matches!(scenario, "success" | "auth-success"),
                "{mode}/{scenario} acceptance"
            );
            if matches!(
                scenario,
                "success"
                    | "auth-success"
                    | "auth-535"
                    | "missing-starttls"
                    | "starttls-failed"
                    | "smtp-450"
                    | "smtp-550"
                    | "timeout"
            ) {
                assert!(
                    output.status.success(),
                    "{mode}/{scenario} fixture failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                );
            }
            if scenario == "timeout" {
                assert!(started.elapsed() < Duration::from_secs(7));
            }
            let public_error = format!("{result:?}");
            assert!(!public_error.contains("fixture-password"));
            assert!(!public_error.contains("fixture-user"));
        }
        fs::remove_dir_all(root).unwrap();
    }

    fn generate_certificate(cert: &std::path::Path, key: &std::path::Path, common_name: &str) {
        let generated = Command::new("/usr/bin/openssl")
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
            .expect("host OpenSSL is required for the local TLS fixture");
        assert!(generated.success());
    }

    fn run_smtp_fixture(
        curl: &super::super::super::motion_webhook::CurlTransport,
        mode: &str,
        scenario: &str,
        server_cert: &std::path::Path,
        server_key: &std::path::Path,
        trusted_ca: &std::path::Path,
        host: &str,
    ) -> (Result<u16, ()>, std::process::Output) {
        let mut child = Command::new("/usr/bin/python3")
            .args(["-c", SMTP_TLS_FIXTURE, mode, scenario])
            .arg(server_cert)
            .arg(server_key)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("host Python is required for the local TLS fixture");
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut port = String::new();
        stdout.read_line(&mut port).unwrap();
        if port.trim().is_empty() {
            let output = child.wait_with_output().unwrap();
            panic!(
                "SMTP fixture failed to start: {}",
                String::from_utf8_lossy(&output.stderr)
            );
        }
        let port: u16 = port.trim().parse().unwrap();
        let message = smtp_message("camera@example.test", "owner@example.test");
        let scheme = if mode == "starttls" { "smtp" } else { "smtps" };
        let uses_auth = matches!(scenario, "auth-success" | "auth-535" | "wrong-host");
        let url = format!("{scheme}://{host}:{port}");
        let request = super::super::super::motion_webhook::SmtpRequest {
            url: &url,
            username: if uses_auth { "fixture-user" } else { "" },
            password: if uses_auth { "fixture-password" } else { "" },
            from: "<camera@example.test>",
            to: "<owner@example.test>",
            message: message.as_bytes(),
        };
        let result = curl.send_smtp_with_ca(&request, Some(trusted_ca));
        drop(stdout);
        let deadline = Instant::now() + Duration::from_secs(9);
        while child.try_wait().unwrap().is_none() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        if child.try_wait().unwrap().is_none() {
            child.kill().unwrap();
        }
        (result, child.wait_with_output().unwrap())
    }

    struct StatusTransport;

    impl Transport for StatusTransport {
        fn available(&self) -> bool {
            true
        }

        fn send(&self, _config: &Config) -> Result<u16, ()> {
            Ok(250)
        }
    }

    struct HeldTransport {
        state: Arc<(Mutex<bool>, Condvar)>,
        calls: AtomicU32,
    }

    impl Transport for HeldTransport {
        fn available(&self) -> bool {
            true
        }

        fn send(&self, _config: &Config) -> Result<u16, ()> {
            self.calls.fetch_add(1, Ordering::Relaxed);
            let (lock, wake) = &*self.state;
            let held = lock.lock().unwrap();
            let _ = wake
                .wait_timeout_while(held, Duration::from_secs(2), |released| !*released)
                .unwrap();
            Ok(250)
        }
    }

    #[test]
    fn config_generation_discards_queued_email() {
        let state = Arc::new((Mutex::new(false), Condvar::new()));
        let transport = Arc::new(HeldTransport {
            state: state.clone(),
            calls: AtomicU32::new(0),
        });
        let service = Service::with_transport(transport.clone());
        service.apply(config()).unwrap();
        service.start().unwrap();
        assert_eq!(
            service.try_send_motion(active(1)),
            MotionEventDisposition::Queued
        );
        assert_eq!(
            service.try_send_motion(active(2)),
            MotionEventDisposition::Queued
        );
        let deadline = Instant::now() + Duration::from_secs(1);
        while transport.calls.load(Ordering::Acquire) == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        let mut changed = config();
        changed.to_address = "second@example.test".to_owned();
        service.apply(changed).unwrap();
        let (lock, wake) = &*state;
        *lock.lock().unwrap() = true;
        wake.notify_all();
        let deadline = Instant::now() + Duration::from_secs(1);
        while service.runtime.queued.load(Ordering::Acquire) != 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(transport.calls.load(Ordering::Acquire), 1);
        assert_eq!(service.runtime.queue_dropped.load(Ordering::Acquire), 1);
    }
}
