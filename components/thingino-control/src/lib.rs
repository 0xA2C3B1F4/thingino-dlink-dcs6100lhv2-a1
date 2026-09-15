//! Bounded Thingino management daemon.
//!
//! The crate deliberately uses only Rust's standard library. The public HTTP
//! backend trait keeps camera-specific media integration out of the HTTP
//! server and makes the contract testable on a host.

mod camera;
mod decode;
mod json;
mod protocol;
#[cfg(feature = "raptor-backend")]
mod raptor;
#[cfg(feature = "raptor-backend")]
mod raptor_backend;
mod request;
mod request_parse;
mod response;
mod router;
mod server;
mod web_auth;
mod whip;

use request::*;
use response::*;
use router::handle_client;
use server::SharedState;
pub use server::{serve, serve_with_web_auth, serve_with_web_auth_and_whip};

pub use camera::CameraPaths;
pub use protocol::{
    Backend, BackendError, BackendResponse, BackendRoute, DayNightMode, MediaFileIdentity,
};
#[cfg(feature = "raptor-backend")]
#[doc(hidden)]
pub use raptor::connect_deadline as connect_raptor_unix_deadline;
#[cfg(feature = "raptor-backend")]
pub use raptor_backend::RaptorBackend;
pub use web_auth::{
    AuthError, LoginResult, PasswordHasher, PasswordVerifier, SessionStatus, WebAuth, WebAuthPaths,
};
pub use whip::WhipProxy;

pub fn control_token_from_bytes(input: &[u8]) -> Option<Vec<u8>> {
    let trimmed = input.strip_suffix(b"\n").unwrap_or(input);
    let trimmed = trimmed.strip_suffix(b"\r").unwrap_or(trimmed);
    let token = if trimmed.first() == Some(&b'{') {
        let document = json::parse(trimmed).ok()?;
        match document.get_path("control.token")? {
            json::Value::String(value) => value.as_bytes().to_vec(),
            _ => return None,
        }
    } else {
        trimmed.to_vec()
    };
    (token.len() == 64 && token.iter().all(|byte| byte.is_ascii_hexdigit())).then_some(token)
}

use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub use request_parse::{MAX_BODY_BYTES, MAX_HEADER_BYTES};
pub const MAX_BACKEND_RESPONSE_BYTES: usize = 64 * 1024;
pub const MAX_SNAPSHOT_BYTES: usize = 2 * 1024 * 1024;
pub const CONNECTION_TIMEOUT: Duration = Duration::from_secs(1);
pub const TOTAL_TIMEOUT: Duration = Duration::from_secs(3);
pub const MAX_BACKEND_OPERATIONS: usize = 2;

// Keep two workers outside the bounded Prudynt operation set. One absorbs a
// slow or incomplete request while the other keeps auth and session routes
// responsive. Backend-gate waiters have a shorter budget so they cannot consume
// that reserve for the whole request deadline.
const WORKER_COUNT: usize = 4;
const CONNECTION_QUEUE_CAPACITY: usize = 16;
const RESPONSE_RESERVE: Duration = Duration::from_millis(50);
const BACKEND_QUEUE_TIMEOUT: Duration = Duration::from_millis(500);

#[derive(Clone, Debug)]
pub struct HttpBackend {
    address: SocketAddr,
}

impl HttpBackend {
    pub fn new(address: SocketAddr) -> Self {
        Self { address }
    }
}

impl Backend for HttpBackend {
    fn request(
        &self,
        route: BackendRoute,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let connect_budget = remaining(deadline)
            .ok_or(BackendError::Timeout)?
            .min(CONNECTION_TIMEOUT);
        let mut stream = TcpStream::connect_timeout(&self.address, connect_budget)
            .map_err(classify_connect_error)?;
        set_deadline_timeouts(&stream, deadline).map_err(|_| BackendError::Connection)?;

        let body = route.body();
        let request = format!(
            "{} {} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\nAccept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
            route.method(),
            route.path(),
            self.address,
            body.len()
        );
        write_all_deadline(&mut stream, request.as_bytes(), deadline)?;
        write_all_deadline(&mut stream, &body, deadline)?;

        let mut response = Vec::with_capacity(4096);
        let mut chunk = [0_u8; 4096];
        loop {
            if let Some(total) = backend_response_total(&response)? {
                if response.len() == total {
                    return parse_backend_response(&response);
                }
                if response.len() > total {
                    return Err(BackendError::Protocol);
                }
            }
            set_deadline_timeouts(&stream, deadline).map_err(|_| BackendError::Connection)?;
            match stream.read(&mut chunk) {
                Ok(0) => return parse_backend_response(&response),
                Ok(count) => {
                    if response.len() + count > MAX_BACKEND_RESPONSE_BYTES + MAX_HEADER_BYTES {
                        return Err(BackendError::Protocol);
                    }
                    response.extend_from_slice(&chunk[..count]);
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) if is_timeout(&error) => return Err(BackendError::Timeout),
                Err(_) => return Err(BackendError::Connection),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn daynight_parser_is_strict() {
        assert_eq!(
            parse_daynight_mode(br#"{"mode":"auto"}"#),
            Some(DayNightMode::Auto)
        );
        assert_eq!(
            parse_daynight_mode(br#" { "mode" : "day" } "#),
            Some(DayNightMode::Day)
        );
        assert_eq!(
            parse_daynight_mode(br#"{"mode":"night"}"#),
            Some(DayNightMode::Night)
        );
        assert_eq!(parse_daynight_mode(br#"{"mode":"invalid"}"#), None);
        assert_eq!(parse_daynight_mode(br#"{"mode":"}"#), None);
        assert_eq!(parse_daynight_mode(b"\x0b{\"mode\":\"day\"}"), None);
        assert_eq!(parse_daynight_mode(br#"{"mode":"day","extra":1}"#), None);
        assert_eq!(parse_daynight_mode(b"not-json"), None);
    }

    #[test]
    fn token_comparison_checks_length_and_content() {
        assert!(constant_time_equal(b"fixture", b"fixture"));
        assert!(!constant_time_equal(b"fixture", b"Fixture"));
        assert!(!constant_time_equal(b"fixture", b"fixture-long"));
    }

    #[test]
    fn control_token_accepts_raw_or_thingino_config_only() {
        let token = b"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        assert_eq!(control_token_from_bytes(token), Some(token.to_vec()));
        assert_eq!(
            control_token_from_bytes(
                br#"{"control":{"to\u006ben":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}}"#
            ),
            Some(token.to_vec())
        );
        assert_eq!(control_token_from_bytes(b"short"), None);
        assert_eq!(
            control_token_from_bytes(
                br#"{"control":{"to\u006ben":"zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"}}"#
            ),
            None
        );
        assert_eq!(
            control_token_from_bytes(br#"{"control":{"to\u006ben":null}}"#),
            None
        );
    }

    #[test]
    fn backend_response_framing_is_exact() {
        assert_eq!(
            parse_backend_response(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
            ),
            Ok(BackendResponse::json(b"{}".to_vec()))
        );
        assert_eq!(
            parse_backend_response(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{}"
            ),
            Err(BackendError::Protocol)
        );
        assert_eq!(
            parse_backend_response(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
            ),
            Err(BackendError::Protocol)
        );
    }

    #[test]
    fn http_backend_does_not_wait_for_keep_alive_eof() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let backend_thread = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request).unwrap();
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{}\n",
                )
                .unwrap();
            thread::sleep(Duration::from_millis(500));
        });
        let started = Instant::now();
        let response = HttpBackend::new(address)
            .request(
                BackendRoute::Health,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();
        assert_eq!(response, BackendResponse::json(b"{}\n".to_vec()));
        assert!(started.elapsed() < Duration::from_millis(250));
        backend_thread.join().unwrap();
    }

    #[test]
    fn http_backend_maps_stall_and_refusal() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let backend_thread = thread::spawn(move || {
            let (_stream, _) = listener.accept().unwrap();
            thread::sleep(Duration::from_millis(300));
        });
        assert_eq!(
            HttpBackend::new(address)
                .request(
                    BackendRoute::Health,
                    Instant::now() + Duration::from_millis(100)
                )
                .unwrap_err(),
            BackendError::Timeout
        );
        backend_thread.join().unwrap();

        let refused = TcpListener::bind("127.0.0.1:0").unwrap();
        let refused_address = refused.local_addr().unwrap();
        drop(refused);
        assert_eq!(
            HttpBackend::new(refused_address)
                .request(
                    BackendRoute::Health,
                    Instant::now() + Duration::from_secs(1)
                )
                .unwrap_err(),
            BackendError::Connection
        );
    }
}
