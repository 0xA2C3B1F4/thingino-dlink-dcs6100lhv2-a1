use std::fs;
use std::io::{Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream};
use std::os::unix::fs::symlink;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Barrier, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use thingino_control::{
    Backend, BackendError, BackendResponse, BackendRoute, MAX_BACKEND_OPERATIONS, MAX_BODY_BYTES,
    MAX_HEADER_BYTES, PasswordHasher, PasswordVerifier, WebAuth, WebAuthPaths, WhipProxy, serve,
    serve_with_web_auth, serve_with_web_auth_and_whip,
};

const TOKEN: &str = "fixture-token-public-poc";
const HEALTH: &str = include_str!("../fixtures/health.json");
const MEDIA: &str = include_str!("../fixtures/runtime-media.json");
const MEDIA_METRICS: &str =
    "prudynt_rtsp_clients 1\nprudynt_rtsp_queue_bytes 0\nprudynt_mjpeg_rejections_total 0\n";
const HA_RUNTIME: &str = "{\"enabled\":true,\"state\":\"online\",\"connected\":true,\"last_connect_unix\":1787480000,\"last_disconnect_unix\":null,\"last_error\":null,\"reconnect_in_ms\":null,\"queue_depth\":0,\"queue_high_water_mark\":3,\"published_messages\":42,\"received_commands\":3,\"rejected_commands\":1,\"dropped_messages\":0}\n";

#[derive(Clone)]
enum Behavior {
    Success,
    Error(BackendError),
    Delay(Duration),
}

struct FakeBackend {
    behavior: Behavior,
    active: AtomicUsize,
    peak: AtomicUsize,
}

impl FakeBackend {
    fn new(behavior: Behavior) -> Self {
        Self {
            behavior,
            active: AtomicUsize::new(0),
            peak: AtomicUsize::new(0),
        }
    }

    fn record_peak(&self, active: usize) {
        let mut peak = self.peak.load(Ordering::Acquire);
        while active > peak {
            match self
                .peak
                .compare_exchange_weak(peak, active, Ordering::AcqRel, Ordering::Acquire)
            {
                Ok(_) => return,
                Err(current) => peak = current,
            }
        }
    }

    fn api_response(&self, response: BackendResponse) -> Result<BackendResponse, BackendError> {
        match &self.behavior {
            Behavior::Error(error) => Err(error.clone()),
            Behavior::Delay(duration) => {
                thread::sleep(*duration);
                Ok(response)
            }
            Behavior::Success => Ok(response),
        }
    }
}

impl Backend for FakeBackend {
    fn authorize_media(&self, target: &str) -> bool {
        matches!(
            target,
            "/api/v1/actions/snapshot?stream_id=0"
                | "/api/v1/actions/snapshot?stream_id=1"
                | "/onvif/image.cgi"
                | "/onvif/image1.cgi"
        )
    }

    fn request(
        &self,
        route: BackendRoute,
        _deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let active = self.active.fetch_add(1, Ordering::AcqRel) + 1;
        self.record_peak(active);
        let result = match &self.behavior {
            Behavior::Error(error) => Err(error.clone()),
            Behavior::Delay(duration) => {
                thread::sleep(*duration);
                Ok(response_for(route))
            }
            Behavior::Success => Ok(response_for(route)),
        };
        self.active.fetch_sub(1, Ordering::AcqRel);
        result
    }

    fn api_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
        _deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        if method == "GET" && target == "/api/v1/runtime/media/metrics" && body.is_empty() {
            return Some(self.api_response(BackendResponse::prometheus(
                MEDIA_METRICS.as_bytes().to_vec(),
            )));
        }
        if method == "GET" && target == "/api/v1/runtime/ha" && body.is_empty() {
            return Some(self.api_response(BackendResponse::json(HA_RUNTIME.as_bytes().to_vec())));
        }
        if method == "POST" && target == "/api/v1/actions/ha" {
            let action = match body {
                b"{\"action\":\"reconnect\"}" => "reconnect",
                b"{\"action\":\"republish_discovery\"}" => "republish_discovery",
                b"{\"action\":\"publish_state\"}" => "publish_state",
                _ => return Some(Err(BackendError::Protocol)),
            };
            return Some(self.api_response(BackendResponse::json(
                format!("{{\"status\":\"accepted\",\"action\":\"{action}\"}}\n").into_bytes(),
            )));
        }
        if method == "POST" && target == "/api/v1/config/access" {
            if body.is_empty()
                || !body
                    .windows(b"\"username\":\"viewer\"".len())
                    .any(|window| window == b"\"username\":\"viewer\"")
                || !body
                    .windows(b"\"password\":".len())
                    .any(|window| window == b"\"password\":")
            {
                return Some(Err(BackendError::Protocol));
            }
            return Some(
                self.api_response(BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec())),
            );
        }
        if target == "/api/v1/config/crontab" {
            return match (method, body) {
                ("GET", b"") => Some(self.api_response(BackendResponse::json(
                    b"{\"content\":\"# fixture schedule\\n\",\"max_bytes\":16384}\n".to_vec(),
                ))),
                ("POST", b"{\"content\":\"# fixture schedule\\n\"}") => Some(
                    self.api_response(BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec())),
                ),
                _ => Some(Err(BackendError::Protocol)),
            };
        }
        if target == "/api/v1/actions/time/sync" {
            return match (method, body.is_empty()) {
                ("POST", true) => Some(self.api_response(BackendResponse::json(
                    b"{\"status\":\"ok\",\"message\":\"Time synchronized\"}\n".to_vec(),
                ))),
                _ => Some(Err(BackendError::Protocol)),
            };
        }
        if target == "/api/v1/actions/reboot" {
            return match (method, body.is_empty()) {
                ("POST", true) => Some(self.api_response(BackendResponse::json(
                    b"{\"status\":\"accepted\",\"message\":\"Reboot scheduled\"}\n".to_vec(),
                ))),
                _ => Some(Err(BackendError::Protocol)),
            };
        }
        if matches!(target, "/api/v1/runtime/sensor" | "/api/v1/sensor/iq") {
            return match (method, body.is_empty()) {
                ("GET", true) => Some(self.api_response(BackendResponse::json(
                    b"{\"sensor_model\":\"os02g10\",\"soc_model\":\"t31n\",\"soc_family\":\"t\",\"file_path\":\"/usr/share/sensor/os02g10-t31n.bin\",\"md5\":\"not computed in request path\"}\n".to_vec(),
                ))),
                _ => Some(Err(BackendError::Protocol)),
            };
        }
        None
    }

    fn update_management_credential(
        &self,
        username: &str,
        password: &str,
        _deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        if username != "root" || !(10..=128).contains(&password.len()) {
            return Some(Err(BackendError::Protocol));
        }
        Some(self.api_response(BackendResponse::json(
            b"{\"status\":\"ok\",\"management_password_changed\":true}\n".to_vec(),
        )))
    }
}

fn response_for(route: BackendRoute) -> BackendResponse {
    match route {
        BackendRoute::Health => BackendResponse::json(HEALTH.as_bytes().to_vec()),
        BackendRoute::RuntimeMedia => BackendResponse::json(MEDIA.as_bytes().to_vec()),
        BackendRoute::Config => {
            BackendResponse::json(b"{\"backend\":{\"name\":\"prudynt\"}}\n".to_vec())
        }
        BackendRoute::Snapshot(stream_id) => {
            BackendResponse::jpeg(vec![0xff, 0xd8, stream_id, 0xff, 0xd9])
        }
        BackendRoute::DayNight(mode) => BackendResponse::json(
            format!(
                "{{\"status\":\"accepted\",\"mode\":\"{}\"}}\n",
                mode.as_str()
            )
            .into_bytes(),
        ),
    }
}

struct RunningServer {
    address: SocketAddr,
    shutdown: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}

impl RunningServer {
    fn start(backend: Arc<dyn Backend>) -> Self {
        Self::start_inner(backend, None)
    }

    fn start_with_web_auth(backend: Arc<dyn Backend>, web_auth: WebAuth) -> Self {
        Self::start_inner(backend, Some(web_auth))
    }

    fn start_with_whip(backend: Arc<dyn Backend>, whip_address: SocketAddr) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let shutdown = Arc::new(AtomicBool::new(false));
        let thread_shutdown = Arc::clone(&shutdown);
        let server_thread = thread::spawn(move || {
            serve_with_web_auth_and_whip(
                listener,
                backend,
                TOKEN.as_bytes().to_vec(),
                None,
                Some(WhipProxy::new(whip_address)),
                thread_shutdown,
            )
            .unwrap();
        });
        Self {
            address,
            shutdown,
            thread: Some(server_thread),
        }
    }

    fn start_inner(backend: Arc<dyn Backend>, web_auth: Option<WebAuth>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let shutdown = Arc::new(AtomicBool::new(false));
        let thread_shutdown = Arc::clone(&shutdown);
        let server_thread = thread::spawn(move || {
            if let Some(web_auth) = web_auth {
                serve_with_web_auth(
                    listener,
                    backend,
                    TOKEN.as_bytes().to_vec(),
                    Some(web_auth),
                    thread_shutdown,
                )
                .unwrap();
            } else {
                serve(
                    listener,
                    backend,
                    TOKEN.as_bytes().to_vec(),
                    thread_shutdown,
                )
                .unwrap();
            }
        });
        Self {
            address,
            shutdown,
            thread: Some(server_thread),
        }
    }
}

struct AuthFixture {
    root: PathBuf,
}

struct FixturePasswordVerifier;
struct FixturePasswordHasher;

impl PasswordVerifier for FixturePasswordVerifier {
    fn verify(&self, password: &[u8], stored: &str) -> bool {
        password == b"__SET_LOCALLY__" && stored.starts_with("$6$thingino$")
    }
}

impl PasswordHasher for FixturePasswordHasher {
    fn hash(&self, _password: &[u8], _salt: &str) -> Option<String> {
        Some("$6$fixture$updated-management-password-hash".to_owned())
    }
}

impl AuthFixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "thingino-control-web-auth-{}-{}",
            std::process::id(),
            AUTH_FIXTURE_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir_all(&root).unwrap();
        fs::write(
            root.join("shadow"),
            b"root:$6$thingino$qQeUfTABzMaN6mFplFe53kdxi/HttHogoy7giJKZ/DR5eIq6vohDDSJERqk2sz4sXu92qRu4jwWShGdIRKyT./:0:0:99999:7:::\n",
        )
        .unwrap();
        fs::write(root.join("api.key"), b"fixture-api-key\n").unwrap();
        fs::write(
            root.join("thingino.json"),
            b"{\"webui\":{\"auth_bypass_ips\":[\"192.0.2.0/24\",\"2001:db8::/32\"],\"paranoid\":false}}\n",
        )
        .unwrap();
        Self { root }
    }

    fn web_auth(&self) -> WebAuth {
        WebAuth::with_password_crypto(
            WebAuthPaths {
                api_key: self.root.join("api.key"),
                thingino_config: self.root.join("thingino.json"),
                shadow: self.root.join("shadow"),
            },
            Arc::new(FixturePasswordVerifier),
            Arc::new(FixturePasswordHasher),
        )
    }

    fn login(&self, server: &RunningServer) -> String {
        let body = br#"{"username":"root","password":"__SET_LOCALLY__"}"#;
        let response = raw_exchange(
            server,
            format!(
                "POST /api/v1/auth/login HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                body.len(),
                std::str::from_utf8(body).unwrap()
            )
            .as_bytes(),
        );
        assert!(response.starts_with(b"HTTP/1.1 200 "), "{response:?}");
        let headers = std::str::from_utf8(
            &response[..response
                .windows(4)
                .position(|value| value == b"\r\n\r\n")
                .unwrap()],
        )
        .unwrap();
        let cookie = headers
            .lines()
            .find_map(|line| line.strip_prefix("Set-Cookie: thingino_session="))
            .unwrap();
        assert!(
            cookie.ends_with("; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Strict"),
            "session cookie is missing its security attributes: {cookie}"
        );
        cookie.split(';').next().unwrap().to_owned()
    }
}

impl Drop for AuthFixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

static AUTH_FIXTURE_COUNTER: AtomicUsize = AtomicUsize::new(0);

impl Drop for RunningServer {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::Release);
        let _ = TcpStream::connect(self.address);
        if let Some(thread) = self.thread.take() {
            thread.join().unwrap();
        }
    }
}

fn request(
    server: &RunningServer,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: &[u8],
) -> (u16, Vec<u8>) {
    let mut stream = TcpStream::connect(server.address).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let authorization = token
        .map(|value| format!("Authorization: Bearer {value}\r\n"))
        .unwrap_or_default();
    let content_type = if body.is_empty() {
        ""
    } else {
        "Content-Type: application/json\r\n"
    };
    let csrf_guard = matches!(method, "POST" | "PUT" | "DELETE")
        .then_some("X-Requested-With: Thingino-WebUI\r\n")
        .unwrap_or("");
    let header = format!(
        "{method} {path} HTTP/1.1\r\nHost: fixture\r\n{authorization}{content_type}{csrf_guard}Content-Length: {}\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).unwrap();
    stream.write_all(body).unwrap();
    read_response(stream)
}

fn raw_request(server: &RunningServer, data: &[u8]) -> (u16, Vec<u8>) {
    read_response_bytes(raw_exchange(server, data))
}

fn raw_exchange(server: &RunningServer, data: &[u8]) -> Vec<u8> {
    let mut stream = TcpStream::connect(server.address).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream.write_all(data).unwrap();
    let mut response = Vec::new();
    let mut chunk = [0_u8; 1024];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(count) => response.extend_from_slice(&chunk[..count]),
            Err(error) if error.kind() == std::io::ErrorKind::ConnectionReset => break,
            Err(error) => panic!("response read failed: {error}"),
        }
    }
    response
}

fn read_response(stream: TcpStream) -> (u16, Vec<u8>) {
    let mut stream = stream;
    let mut response = Vec::new();
    let mut chunk = [0_u8; 1024];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(count) => response.extend_from_slice(&chunk[..count]),
            Err(error) if error.kind() == std::io::ErrorKind::ConnectionReset => break,
            Err(error) => panic!("response read failed: {error}"),
        }
    }
    read_response_bytes(response)
}

fn read_response_bytes(response: Vec<u8>) -> (u16, Vec<u8>) {
    let body_start = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .unwrap()
        + 4;
    let status = std::str::from_utf8(&response[..body_start - 4])
        .unwrap()
        .split_ascii_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    (status, response[body_start..].to_vec())
}

fn read_http_request(mut stream: &TcpStream) -> Vec<u8> {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut request = Vec::new();
    let mut chunk = [0_u8; 1024];
    loop {
        let count = stream.read(&mut chunk).unwrap();
        request.extend_from_slice(&chunk[..count]);
        let Some(header_end) = request.windows(4).position(|value| value == b"\r\n\r\n") else {
            continue;
        };
        let headers = std::str::from_utf8(&request[..header_end]).unwrap();
        let length = headers
            .lines()
            .find_map(|line| line.strip_prefix("Content-Length: "))
            .unwrap()
            .parse::<usize>()
            .unwrap();
        if request.len() == header_end + 4 + length {
            return request;
        }
    }
}

#[test]
fn contract_routes_return_fixture_shapes() {
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Success)));

    let (status, body) = request(&server, "GET", "/api/v1/health", Some(TOKEN), b"");
    assert_eq!(status, 200);
    assert_eq!(body, HEALTH.as_bytes());

    let (status, body) = request(&server, "GET", "/api/v1/runtime/media", Some(TOKEN), b"");
    assert_eq!(status, 200);
    assert_eq!(body, MEDIA.as_bytes());

    let (status, body) = request(
        &server,
        "GET",
        "/api/v1/runtime/media/metrics",
        Some(TOKEN),
        b"",
    );
    assert_eq!(status, 200);
    assert_eq!(body, MEDIA_METRICS.as_bytes());

    let (status, body) = request(&server, "GET", "/api/v1/runtime/ha", Some(TOKEN), b"");
    assert_eq!(status, 200);
    assert_eq!(body, HA_RUNTIME.as_bytes());

    for action in ["reconnect", "republish_discovery", "publish_state"] {
        let request_body = format!("{{\"action\":\"{action}\"}}");
        let (status, body) = request(
            &server,
            "POST",
            "/api/v1/actions/ha",
            Some(TOKEN),
            request_body.as_bytes(),
        );
        assert_eq!(status, 200);
        assert_eq!(
            body,
            format!("{{\"status\":\"accepted\",\"action\":\"{action}\"}}\n").as_bytes()
        );
    }

    let (status, body) = request(&server, "GET", "/api/v1/config", Some(TOKEN), b"");
    assert_eq!(status, 200);
    assert_eq!(body, b"{\"backend\":{\"name\":\"prudynt\"}}\n");

    for stream_id in [0, 1] {
        let path = format!("/api/v1/actions/snapshot?stream_id={stream_id}");
        let (status, body) = request(&server, "POST", &path, Some(TOKEN), b"");
        assert_eq!(status, 200);
        assert_eq!(body, [0xff, 0xd8, stream_id, 0xff, 0xd9]);

        let raw = raw_exchange(
            &server,
            format!(
                "POST {path} HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: 0\r\n\r\n"
            )
            .as_bytes(),
        );
        let headers = raw
            .split(|byte| *byte == b'\n')
            .take_while(|line| *line != b"\r")
            .collect::<Vec<_>>();
        assert!(headers.iter().any(|line| {
            *line
                == format!(
                    "Content-Disposition: attachment; filename=\"snapshot-ch{stream_id}.jpg\"\r"
                )
                .as_bytes()
        }));
    }

    for mode in ["auto", "day", "night"] {
        let body = format!("{{\"mode\":\"{mode}\"}}");
        let (status, response) = request(
            &server,
            "POST",
            "/api/v1/actions/daynight",
            Some(TOKEN),
            body.as_bytes(),
        );
        assert_eq!(status, 200);
        assert_eq!(
            response,
            format!("{{\"status\":\"accepted\",\"mode\":\"{mode}\"}}\n").as_bytes()
        );
    }
}

#[test]
fn authentication_fails_closed() {
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Success)));
    assert_eq!(request(&server, "GET", "/api/v1/health", None, b"").0, 401);
    assert_eq!(
        request(
            &server,
            "GET",
            "/api/v1/health",
            Some("wrong-fixture-token"),
            b""
        )
        .0,
        401
    );
}

#[test]
fn authenticated_stream1_whip_proxy_rewrites_location_and_deletes_session() {
    let upstream = TcpListener::bind("127.0.0.1:0").unwrap();
    let upstream_address = upstream.local_addr().unwrap();
    let requests = Arc::new(Mutex::new(Vec::<Vec<u8>>::new()));
    let thread_requests = Arc::clone(&requests);
    let upstream_thread = thread::spawn(move || {
        let responses: [&[u8]; 2] = [
            b"HTTP/1.1 201 Created\r\nContent-Type: application/sdp\r\nContent-Length: 5\r\nLocation: /whip/0123456789abcdef0123456789abcdef\r\n\r\nv=0\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK",
        ];
        for response in responses {
            let (mut stream, _) = upstream.accept().unwrap();
            thread_requests
                .lock()
                .unwrap()
                .push(read_http_request(&stream));
            stream.write_all(response).unwrap();
        }
    });
    let server = RunningServer::start_with_whip(
        Arc::new(FakeBackend::new(Behavior::Success)),
        upstream_address,
    );
    let offer = b"v=0\r\n";
    let create = raw_exchange(
        &server,
        format!(
            "POST /api/v1/media/webrtc/whip?stream=1 HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: application/sdp\r\nContent-Length: {}\r\n\r\n{}",
            offer.len(),
            std::str::from_utf8(offer).unwrap(),
        )
        .as_bytes(),
    );
    assert!(
        create.starts_with(b"HTTP/1.1 201 Created\r\n"),
        "{create:?}"
    );
    assert!(
        create
            .windows(
                b"Location: /api/v1/media/webrtc/whip/0123456789abcdef0123456789abcdef\r\n".len()
            )
            .any(|window| window
                == b"Location: /api/v1/media/webrtc/whip/0123456789abcdef0123456789abcdef\r\n")
    );
    assert!(create.ends_with(offer));

    let delete = raw_exchange(
        &server,
        format!(
            "DELETE /api/v1/media/webrtc/whip/0123456789abcdef0123456789abcdef HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: 0\r\n\r\n"
        )
        .as_bytes(),
    );
    assert!(
        delete.starts_with(b"HTTP/1.1 204 No Content\r\n"),
        "{delete:?}"
    );
    upstream_thread.join().unwrap();
    let requests = requests.lock().unwrap();
    assert!(requests[0].starts_with(b"POST /whip?stream=1 HTTP/1.1\r\n"));
    assert!(requests[1].starts_with(b"DELETE /whip/0123456789abcdef0123456789abcdef HTTP/1.1\r\n"));
    assert!(requests.iter().all(|request| {
        !request
            .windows(b"Authorization".len())
            .any(|window| window == b"Authorization")
    }));
    assert!(requests.iter().all(|request| {
        !request
            .windows(b"Cookie".len())
            .any(|window| window == b"Cookie")
    }));
}

#[test]
fn whip_proxy_rejects_unauthorized_and_malformed_requests_before_upstream() {
    let unused = TcpListener::bind("127.0.0.1:0").unwrap();
    let server = RunningServer::start_with_whip(
        Arc::new(FakeBackend::new(Behavior::Success)),
        unused.local_addr().unwrap(),
    );
    assert_eq!(
        raw_request(
            &server,
            b"POST /api/v1/media/webrtc/whip?stream=0 HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/sdp\r\nContent-Length: 5\r\n\r\nv=0\r\n",
        )
        .0,
        401,
    );
    assert_eq!(
        raw_request(
            &server,
            format!("POST /api/v1/media/webrtc/whip?stream=0 HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nv=0\r\n").as_bytes(),
        )
        .0,
        415,
    );
    assert_eq!(
        raw_request(
            &server,
            format!("POST /api/v1/media/webrtc/whip?stream=2 HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: application/sdp\r\nContent-Length: 5\r\n\r\nv=0\r\n").as_bytes(),
        )
        .0,
        404,
    );
    assert_eq!(
        request(
            &server,
            "DELETE",
            "/api/v1/media/webrtc/whip/not-a-session",
            Some(TOKEN),
            b"",
        )
        .0,
        400,
    );
    unused.set_nonblocking(true).unwrap();
    assert_eq!(
        unused.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}

#[test]
fn whip_proxy_timeout_is_bounded() {
    let upstream = TcpListener::bind("127.0.0.1:0").unwrap();
    let upstream_address = upstream.local_addr().unwrap();
    let upstream_thread = thread::spawn(move || {
        let (stream, _) = upstream.accept().unwrap();
        let _ = read_http_request(&stream);
        thread::sleep(Duration::from_secs(4));
    });
    let server = RunningServer::start_with_whip(
        Arc::new(FakeBackend::new(Behavior::Success)),
        upstream_address,
    );
    let started = Instant::now();
    let response = raw_request(
        &server,
        format!("POST /api/v1/media/webrtc/whip?stream=0 HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: application/sdp\r\nContent-Length: 5\r\n\r\nv=0\r\n").as_bytes(),
    );
    assert_eq!(response.0, 504);
    assert!(started.elapsed() < Duration::from_millis(3500));
    upstream_thread.join().unwrap();
}

#[test]
fn onvif_snapshots_use_the_normal_authenticated_media_path() {
    let fixture = AuthFixture::new();
    let server = RunningServer::start_with_web_auth(
        Arc::new(FakeBackend::new(Behavior::Success)),
        fixture.web_auth(),
    );

    for target in ["%2Fonvif%2Fimage.cgi", "%2Fonvif%2Fimage1.cgi"] {
        let unauthorized = raw_request(
            &server,
            format!(
                "GET /api/v1/internal/media-authorize?target={target} HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nContent-Length: 0\r\n\r\n"
            )
            .as_bytes(),
        );
        assert_eq!(unauthorized.0, 401);

        let authorized = raw_request(
            &server,
            format!(
                "GET /api/v1/internal/media-authorize?target={target} HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-API-Key: fixture-api-key\r\nContent-Length: 0\r\n\r\n"
            )
            .as_bytes(),
        );
        assert_eq!(authorized, (204, Vec::new()));
    }

    for request in [
        b"GET /api/v1/internal/media-authorize?target=%2Fonvif%2Fimage.cgi HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 2\r\nContent-Length: 0\r\n\r\n".as_slice(),
        b"GET /api/v1/internal/onvif-snapshot?stream=0 HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 2\r\nContent-Length: 0\r\n\r\n".as_slice(),
        b"GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 2\r\nContent-Length: 0\r\n\r\n".as_slice(),
    ] {
        assert_eq!(raw_request(&server, request).0, 401);
    }
}

#[test]
fn proxy_web_auth_matches_session_api_key_and_trusted_ip_contract() {
    let fixture = AuthFixture::new();
    let server = RunningServer::start_with_web_auth(
        Arc::new(FakeBackend::new(Behavior::Success)),
        fixture.web_auth(),
    );
    let session_id = fixture.login(&server);

    let request_with = |headers: &str| {
        raw_request(
            &server,
            format!(
                "GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\n{headers}Content-Length: 0\r\n\r\n"
            )
            .as_bytes(),
        )
        .0
    };

    assert_eq!(
        request_with(&format!("Cookie: thingino_session={}\r\n", session_id)),
        200
    );
    assert_eq!(request_with("X-API-Key: fixture-api-key\r\n"), 200);
    assert_eq!(request_with("X-Thingino-Remote-Addr: 192.0.2.55\r\n"), 200);
    assert_eq!(
        request_with("X-Thingino-Remote-Addr: 2001:db8::1234\r\n"),
        200
    );
    assert_eq!(
        request_with("X-Thingino-Remote-Addr: ::ffff:192.0.2.55\r\n"),
        200
    );
    assert_eq!(request_with("X-Thingino-Remote-Addr: 10.42.7.8\r\n"), 401);
    assert_eq!(request_with("X-Thingino-Remote-Addr: 172.16.2.44\r\n"), 401);
    assert_eq!(request_with("X-API-Key: wrong\r\n"), 401);
    assert_eq!(
        raw_request(
            &server,
            b"GET /api/v1/health?token=fixture-api-key HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nContent-Length: 0\r\n\r\n"
        )
        .0,
        401
    );
    assert_eq!(
        request_with("X-Thingino-Remote-Addr: 192.168.31.1\r\n"),
        401
    );
    assert_eq!(request_with("X-Thingino-Remote-Addr: 172.16.20.1\r\n"), 401);

    assert_eq!(
        raw_request(
            &server,
            b"POST /api/v1/actions/reboot HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-Thingino-Remote-Addr: 192.0.2.55\r\nContent-Length: 0\r\n\r\n",
        )
        .0,
        401
    );
    assert_eq!(
        raw_request(
            &server,
            b"POST /api/v1/actions/reboot HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-Thingino-Remote-Addr: 192.0.2.55\r\nX-Requested-With: Thingino-WebUI\r\nContent-Length: 0\r\n\r\n",
        )
        .0,
        401
    );

    assert_eq!(
        raw_request(
            &server,
            b"POST /api/v1/webui/api-key HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-API-Key: fixture-api-key\r\nContent-Length: 0\r\n\r\n",
        )
        .0,
        401
    );
    assert_eq!(
        raw_request(
            &server,
            b"GET /api/v1/config/access HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-API-Key: fixture-api-key\r\nContent-Length: 0\r\n\r\n",
        )
        .0,
        401
    );

    let key_status = raw_request(
        &server,
        format!(
            "GET /api/v1/webui/api-key HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nCookie: thingino_session={session_id}\r\nContent-Length: 0\r\n\r\n"
        )
        .as_bytes(),
    );
    assert_eq!(key_status.0, 200);
    assert_eq!(key_status.1, b"{\"exists\":true}\n");
    assert!(
        !key_status
            .1
            .windows(b"fixture-api-key".len())
            .any(|value| value == b"fixture-api-key")
    );

    fs::write(
        fixture.root.join("thingino.json"),
        b"{\"webui\":{\"auth_bypass_ips\":[\"192.0.2.0/24\"],\"paranoid\":true}}\n",
    )
    .unwrap();
    assert_eq!(request_with("X-Thingino-Remote-Addr: 192.0.2.55\r\n"), 401);

    let no_marker = format!(
        "GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nCookie: thingino_session={}\r\nContent-Length: 0\r\n\r\n",
        session_id
    );
    assert_eq!(raw_request(&server, no_marker.as_bytes()).0, 401);

    assert_eq!(
        raw_request(
            &server,
            b"GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nAccept: text/html,application/xhtml+xml\r\nContent-Length: 0\r\n\r\n"
        )
        .0,
        302
    );

    assert_eq!(
        request(&server, "GET", "/api/v1/health", Some(TOKEN), b"").0,
        200
    );
}

#[test]
fn login_rate_limit_rejects_before_unbounded_password_checks() {
    const SOURCE_LIMIT: usize = 5;
    let fixture = AuthFixture::new();
    let server = RunningServer::start_with_web_auth(
        Arc::new(FakeBackend::new(Behavior::Success)),
        fixture.web_auth(),
    );
    let body = br#"{"username":"root","password":"__GENERATE_LOCALLY__"}"#;
    for attempt in 0..=SOURCE_LIMIT {
        let response = raw_request(
            &server,
            format!(
                "POST /api/v1/auth/login HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-Thingino-Remote-Addr: 192.0.2.10\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                body.len(),
                std::str::from_utf8(body).unwrap()
            )
            .as_bytes(),
        );
        assert_eq!(response.0, if attempt < SOURCE_LIMIT { 401 } else { 429 });
    }
}

#[test]
fn proxy_web_auth_rejects_unknown_session_symlinked_inputs_and_duplicate_context() {
    let fixture = AuthFixture::new();
    let server = RunningServer::start_with_web_auth(
        Arc::new(FakeBackend::new(Behavior::Success)),
        fixture.web_auth(),
    );
    let unknown_id = "fedcba9876543210fedcba9876543210";
    let linked = format!(
        "GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nCookie: thingino_session={unknown_id}\r\nContent-Length: 0\r\n\r\n"
    );
    let response = raw_request(&server, linked.as_bytes());
    assert_eq!(response.0, 401);
    assert!(
        response
            .1
            .windows(b"\"code\":\"unauthorized\"".len())
            .any(|window| window == b"\"code\":\"unauthorized\"")
    );

    fs::rename(
        fixture.root.join("api.key"),
        fixture.root.join("real-api.key"),
    )
    .unwrap();
    symlink(
        fixture.root.join("real-api.key"),
        fixture.root.join("api.key"),
    )
    .unwrap();
    let linked_key = b"GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-API-Key: fixture-api-key\r\nContent-Length: 0\r\n\r\n";
    assert_eq!(raw_request(&server, linked_key).0, 401);

    fs::rename(
        fixture.root.join("thingino.json"),
        fixture.root.join("real-thingino.json"),
    )
    .unwrap();
    symlink(
        fixture.root.join("real-thingino.json"),
        fixture.root.join("thingino.json"),
    )
    .unwrap();
    let linked_config = b"GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-Thingino-Remote-Addr: 192.0.2.55\r\nContent-Length: 0\r\n\r\n";
    assert_eq!(raw_request(&server, linked_config).0, 401);

    assert_eq!(
        raw_request(
            &server,
            b"GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nX-Thingino-Proxy: 1\r\nContent-Length: 0\r\n\r\n"
        )
        .0,
        400
    );
}

#[test]
fn management_password_updates_access_credentials_and_rolls_back_on_failure() {
    let fixture = AuthFixture::new();
    let original = fs::read(fixture.root.join("shadow")).unwrap();
    let server = RunningServer::start_with_web_auth(
        Arc::new(FakeBackend::new(Behavior::Success)),
        fixture.web_auth(),
    );
    let session_id = fixture.login(&server);
    let body = br#"{"password":"__SET_LOCALLY__"}"#;
    let response = raw_exchange(
        &server,
        format!(
            "POST /api/v1/auth/password HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nCookie: thingino_session={session_id}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            std::str::from_utf8(body).unwrap()
        )
        .as_bytes(),
    );
    assert!(response.starts_with(b"HTTP/1.1 200 "), "{response:?}");
    assert_ne!(fs::read(fixture.root.join("shadow")).unwrap(), original);
    assert_eq!(
        raw_request(
            &server,
            format!(
                "GET /api/v1/health HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nCookie: thingino_session={session_id}\r\nContent-Length: 0\r\n\r\n"
            )
            .as_bytes(),
        )
        .0,
        401,
        "a management-password change must invalidate existing sessions"
    );

    let rejected = AuthFixture::new();
    let rejected_original = fs::read(rejected.root.join("shadow")).unwrap();
    let server = RunningServer::start_with_web_auth(
        Arc::new(FakeBackend::new(Behavior::Error(BackendError::Unavailable))),
        rejected.web_auth(),
    );
    let session_id = rejected.login(&server);
    let response = raw_exchange(
        &server,
        format!(
            "POST /api/v1/auth/password HTTP/1.1\r\nHost: fixture\r\nX-Thingino-Proxy: 1\r\nCookie: thingino_session={session_id}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            std::str::from_utf8(body).unwrap()
        )
        .as_bytes(),
    );
    assert!(response.starts_with(b"HTTP/1.1 503 "), "{response:?}");
    assert_eq!(
        fs::read(rejected.root.join("shadow")).unwrap(),
        rejected_original
    );
}

#[test]
fn malformed_unknown_and_oversized_requests_are_bounded() {
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Success)));
    assert_eq!(
        request(
            &server,
            "POST",
            "/api/v1/actions/daynight",
            Some(TOKEN),
            b"not-json"
        )
        .0,
        400
    );
    assert_eq!(
        request(
            &server,
            "POST",
            "/api/v1/actions/daynight",
            Some(TOKEN),
            br#"{"mode":"}"#,
        )
        .0,
        400
    );
    assert_eq!(
        request(
            &server,
            "POST",
            "/api/v1/actions/daynight",
            Some(TOKEN),
            br#"{"mode":"dusk"}"#,
        )
        .0,
        400
    );
    assert_eq!(
        request(&server, "GET", "/api/v1/unknown", Some(TOKEN), b"").0,
        404
    );
    let wrong_content_type = raw_request(
        &server,
        format!(
            "POST /api/v1/actions/daynight HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: text/plain\r\nContent-Length: 14\r\n\r\n{{\"mode\":\"day\"}}"
        )
        .as_bytes(),
    );
    assert_eq!(wrong_content_type.0, 415);
    assert!(
        wrong_content_type
            .1
            .windows(b"\"code\":\"unsupported_media_type\"".len())
            .any(|window| window == b"\"code\":\"unsupported_media_type\"")
    );
    for request in [
        format!(
            "POST /api/v1/files/text?file=notes.txt HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nX-Requested-With: Thingino-WebUI\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: 4\r\n\r\nnote"
        ),
        format!(
            "POST /api/v1/network/probe HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nX-Requested-With: Thingino-WebUI\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 25\r\n\r\naction=dns&target=example"
        ),
    ] {
        assert_ne!(raw_request(&server, request.as_bytes()).0, 415);
    }
    assert_eq!(
        raw_request(
            &server,
            format!(
                "POST /api/v1/files/text?file=notes.txt HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nX-Requested-With: Thingino-WebUI\r\nContent-Type: application/json\r\nContent-Length: 4\r\n\r\nnote"
            )
            .as_bytes(),
        )
        .0,
        415
    );

    let unguarded_reboot = raw_request(
        &server,
        format!(
            "POST /api/v1/actions/reboot HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: 0\r\n\r\n"
        )
        .as_bytes(),
    );
    assert_eq!(
        unguarded_reboot.0, 200,
        "bearer authorization is a non-simple guard"
    );

    let oversized_header = format!(
        "GET /api/v1/health HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\nX-Pad: {}\r\n\r\n",
        "x".repeat(MAX_HEADER_BYTES)
    );
    assert_eq!(raw_request(&server, oversized_header.as_bytes()).0, 400);

    let oversized_body = format!(
        "POST /api/v1/actions/daynight HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: {}\r\n\r\n",
        MAX_BODY_BYTES + 1
    );
    assert_eq!(raw_request(&server, oversized_body.as_bytes()).0, 413);
}

#[test]
fn canonical_camera_route_policy_maps_known_mistakes_to_400() {
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Success)));

    assert_eq!(
        request(&server, "POST", "/api/v1/actions/reboot", Some(TOKEN), b"").0,
        200
    );
    assert_eq!(
        request(&server, "GET", "/api/v1/actions/reboot", Some(TOKEN), b"").0,
        400
    );
    assert_eq!(
        request(
            &server,
            "POST",
            "/api/v1/actions/reboot",
            Some(TOKEN),
            b"{}",
        )
        .0,
        400
    );

    assert_eq!(
        request(
            &server,
            "POST",
            "/api/v1/actions/time/sync",
            Some(TOKEN),
            b"",
        )
        .0,
        200
    );
    assert_eq!(
        request(
            &server,
            "GET",
            "/api/v1/actions/time/sync",
            Some(TOKEN),
            b"",
        )
        .0,
        400
    );
    assert_eq!(
        request(
            &server,
            "GET",
            "/api/v1/actions/time/sync?legacy=1",
            Some(TOKEN),
            b"",
        )
        .0,
        404
    );

    assert_eq!(
        request(&server, "GET", "/api/v1/config/crontab", Some(TOKEN), b"",).0,
        200
    );
    assert_eq!(
        request(
            &server,
            "POST",
            "/api/v1/config/crontab",
            Some(TOKEN),
            b"{\"content\":\"# fixture schedule\\n\"}",
        )
        .0,
        200
    );
    for (method, body) in [("PUT", b"{}".as_slice()), ("GET", b"{}"), ("POST", b"")] {
        assert_eq!(
            request(&server, method, "/api/v1/config/crontab", Some(TOKEN), body,).0,
            400,
            "{method} /api/v1/config/crontab",
        );
    }

    assert_eq!(
        request(&server, "GET", "/api/v1/sensor/iq", Some(TOKEN), b"").0,
        200
    );
    assert_eq!(
        request(&server, "POST", "/api/v1/sensor/iq", Some(TOKEN), b"{}",).0,
        400
    );
    assert_eq!(
        request(&server, "GET", "/api/v1/sensor/iq", Some(TOKEN), b"{}").0,
        400
    );
    assert_eq!(
        request(&server, "GET", "/api/v1/unknown", Some(TOKEN), b"").0,
        404
    );
}

#[test]
fn backend_failures_map_to_gateway_statuses() {
    for error in [BackendError::Connection, BackendError::Upstream(500)] {
        let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Error(error))));
        assert_eq!(
            request(&server, "GET", "/api/v1/health", Some(TOKEN), b"").0,
            502
        );
    }
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Error(
        BackendError::Protocol,
    ))));
    let response = request(&server, "GET", "/api/v1/health", Some(TOKEN), b"");
    assert_eq!(response.0, 400);
    assert!(
        response
            .1
            .windows(b"\"code\":\"invalid_request\"".len())
            .any(|window| window == b"\"code\":\"invalid_request\"")
    );
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Error(
        BackendError::Timeout,
    ))));
    assert_eq!(
        request(&server, "GET", "/api/v1/health", Some(TOKEN), b"").0,
        504
    );
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Error(
        BackendError::Unavailable,
    ))));
    assert_eq!(
        request(&server, "GET", "/api/v1/health", Some(TOKEN), b"").0,
        503
    );
}

#[test]
fn disconnect_does_not_break_the_next_request() {
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Success)));
    let mut stream = TcpStream::connect(server.address).unwrap();
    stream
        .write_all(b"GET /api/v1/health HTTP/1.1\r\n")
        .unwrap();
    let _ = stream.shutdown(Shutdown::Both);
    drop(stream);
    assert_eq!(
        request(&server, "GET", "/api/v1/health", Some(TOKEN), b"").0,
        200
    );
}

#[test]
fn concurrent_requests_never_exceed_two_backend_operations() {
    let backend = Arc::new(FakeBackend::new(Behavior::Delay(Duration::from_millis(80))));
    let server = Arc::new(RunningServer::start(backend.clone()));
    let barrier = Arc::new(Barrier::new(9));
    let mut clients = Vec::new();
    for _ in 0..8 {
        let client_server = Arc::clone(&server);
        let client_barrier = Arc::clone(&barrier);
        clients.push(thread::spawn(move || {
            client_barrier.wait();
            request(
                &client_server,
                "GET",
                "/api/v1/runtime/media",
                Some(TOKEN),
                b"",
            )
            .0
        }));
    }
    barrier.wait();
    for client in clients {
        assert_eq!(client.join().unwrap(), 200);
    }
    assert!(backend.peak.load(Ordering::Acquire) <= MAX_BACKEND_OPERATIONS);
    assert_eq!(backend.active.load(Ordering::Acquire), 0);
}

#[test]
fn login_keeps_a_worker_during_slow_backend_saturation() {
    let fixture = AuthFixture::new();
    let backend = Arc::new(FakeBackend::new(Behavior::Delay(Duration::from_millis(
        1200,
    ))));
    let server = Arc::new(RunningServer::start_with_web_auth(
        backend.clone(),
        fixture.web_auth(),
    ));

    let mut active_clients = Vec::new();
    for _ in 0..2 {
        let client_server = Arc::clone(&server);
        active_clients.push(thread::spawn(move || {
            request(
                &client_server,
                "GET",
                "/api/v1/runtime/media",
                Some(TOKEN),
                b"",
            )
            .0
        }));
    }
    let active_deadline = Instant::now() + Duration::from_secs(1);
    while backend.active.load(Ordering::Acquire) < MAX_BACKEND_OPERATIONS {
        assert!(Instant::now() < active_deadline, "backend did not saturate");
        thread::sleep(Duration::from_millis(5));
    }

    let mut gate_waiters = Vec::new();
    for _ in 0..2 {
        let client_server = Arc::clone(&server);
        gate_waiters.push(thread::spawn(move || {
            request(
                &client_server,
                "GET",
                "/api/v1/runtime/media",
                Some(TOKEN),
                b"",
            )
            .0
        }));
    }
    thread::sleep(Duration::from_millis(50));

    let started = Instant::now();
    fixture.login(&server);
    assert!(
        started.elapsed() < Duration::from_millis(900),
        "login waited behind saturated backend workers"
    );

    for client in active_clients {
        assert_eq!(client.join().unwrap(), 200);
    }
    for waiter in gate_waiters {
        assert_eq!(waiter.join().unwrap(), 504);
    }
    assert!(backend.peak.load(Ordering::Acquire) <= MAX_BACKEND_OPERATIONS);
}

#[test]
fn one_thousand_sequential_media_requests_succeed() {
    let server = RunningServer::start(Arc::new(FakeBackend::new(Behavior::Success)));
    for _ in 0..1000 {
        assert_eq!(
            request(&server, "GET", "/api/v1/runtime/media", Some(TOKEN), b"").0,
            200
        );
    }
}
