use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::fs::FileTypeExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::os::fd::FromRawFd;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::os::unix::ffi::OsStrExt;

use crate::BackendError;
use crate::json::{self, Value};

pub const RAPTOR_PROTOCOL_VERSION: u8 = 1;
pub const MAX_RAPTOR_REQUEST_BYTES: usize = 4 * 1024;
pub const MAX_RAPTOR_RESPONSE_BYTES: usize = 16 * 1024;

#[cfg(any(target_os = "linux", target_os = "macos"))]
const AF_UNIX: i32 = 1;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const SOCK_STREAM: i32 = 2;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const SOCK_STREAM: i32 = 1;
#[cfg(target_os = "linux")]
const SOCK_CLOEXEC: i32 = 0x80000;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const O_NONBLOCK: i32 = 0x80;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const O_NONBLOCK: i32 = 0x800;
#[cfg(any(target_os = "linux", target_os = "macos"))]
const F_GETFL: i32 = 3;
#[cfg(any(target_os = "linux", target_os = "macos"))]
const F_SETFL: i32 = 4;
#[cfg(any(target_os = "linux", target_os = "macos"))]
const POLLOUT: i16 = 0x0004;
#[cfg(target_os = "linux")]
const EAGAIN: i32 = 11;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const EALREADY: i32 = 149;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const EALREADY: i32 = 114;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const EINPROGRESS: i32 = 150;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const EINPROGRESS: i32 = 115;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const EISCONN: i32 = 133;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const EISCONN: i32 = 106;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const SOL_SOCKET: i32 = 0xffff;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const SOL_SOCKET: i32 = 1;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const SO_ERROR: i32 = 0x1007;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const SO_ERROR: i32 = 4;

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[repr(C)]
struct SockaddrUn {
    #[cfg(target_os = "macos")]
    length: u8,
    #[cfg(target_os = "macos")]
    family: u8,
    #[cfg(target_os = "linux")]
    family: u16,
    #[cfg(target_os = "linux")]
    path: [std::os::raw::c_char; 108],
    #[cfg(target_os = "macos")]
    path: [std::os::raw::c_char; 104],
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[repr(C)]
struct PollFd {
    fd: i32,
    events: i16,
    revents: i16,
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" {
    #[link_name = "socket"]
    fn libc_socket(domain: i32, kind: i32, protocol: i32) -> i32;
    #[link_name = "connect"]
    fn libc_connect(fd: i32, address: *const SockaddrUn, length: u32) -> i32;
    #[link_name = "poll"]
    fn libc_poll(fds: *mut PollFd, count: Nfds, timeout_ms: i32) -> i32;
    #[link_name = "fcntl"]
    fn libc_fcntl(fd: i32, operation: i32, ...) -> i32;
    #[link_name = "getsockopt"]
    fn libc_getsockopt(fd: i32, level: i32, option: i32, value: *mut i32, length: *mut u32) -> i32;
}

#[cfg(target_os = "linux")]
type Nfds = usize;
#[cfg(target_os = "macos")]
type Nfds = u32;
#[cfg(target_os = "macos")]
const SOCK_STREAM: i32 = 1;
#[cfg(target_os = "macos")]
const O_NONBLOCK: i32 = 4;
#[cfg(target_os = "macos")]
const EAGAIN: i32 = 35;
#[cfg(target_os = "macos")]
const EALREADY: i32 = 37;
#[cfg(target_os = "macos")]
const EINPROGRESS: i32 = 36;
#[cfg(target_os = "macos")]
const EISCONN: i32 = 56;
#[cfg(target_os = "macos")]
const SOL_SOCKET: i32 = 0xffff;
#[cfg(target_os = "macos")]
const SO_ERROR: i32 = 0x1007;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RaptorDaemon {
    Rvd,
    Rsd,
    Rhd,
    Rad,
    Ric,
    Rod,
    Rmr,
    Rmr0,
    Rmr1,
    Rwd,
}

impl RaptorDaemon {
    fn socket_name(self) -> &'static str {
        match self {
            Self::Rvd => "rvd.sock",
            Self::Rsd => "rsd.sock",
            Self::Rhd => "rhd.sock",
            Self::Rad => "rad.sock",
            Self::Ric => "ric.sock",
            Self::Rod => "rod.sock",
            Self::Rmr => "rmr.sock",
            Self::Rmr0 => "rmr0.sock",
            Self::Rmr1 => "rmr1.sock",
            Self::Rwd => "rwd.sock",
        }
    }
}

#[derive(Clone, Debug)]
pub struct RaptorClient {
    run_dir: PathBuf,
}

impl RaptorClient {
    pub fn new(run_dir: PathBuf, protocol_version: u8) -> Result<Self, BackendError> {
        if protocol_version != RAPTOR_PROTOCOL_VERSION {
            return Err(BackendError::Protocol);
        }
        Ok(Self { run_dir })
    }

    pub fn command(
        &self,
        daemon: RaptorDaemon,
        request: &[u8],
        deadline: Instant,
    ) -> Result<RaptorReply, BackendError> {
        validate_request(request)?;
        let socket = self.run_dir.join(daemon.socket_name());
        validate_socket(&socket)?;
        let mut stream = connect_deadline(&socket, deadline)?;
        write_frame(&mut stream, request, deadline)?;
        let _ = stream.shutdown(std::net::Shutdown::Write);
        let body = read_frame(&mut stream, deadline)?;
        require_connection_end(&mut stream, deadline)?;
        let value = json::parse(&body).map_err(|_| BackendError::Protocol)?;
        if value.as_object().is_none() {
            return Err(BackendError::Protocol);
        }
        Ok(RaptorReply {
            #[cfg(test)]
            body,
            value,
        })
    }

    pub fn daemon_socket_absent(&self, daemon: RaptorDaemon) -> Result<bool, BackendError> {
        match fs::symlink_metadata(self.run_dir.join(daemon.socket_name())) {
            Ok(metadata) if metadata.file_type().is_socket() => Ok(false),
            Ok(_) => Err(BackendError::Unavailable),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(true),
            Err(_) => Err(BackendError::Unavailable),
        }
    }
}

#[derive(Clone, Debug)]
pub struct RaptorReply {
    #[cfg(test)]
    body: Vec<u8>,
    pub(crate) value: Value,
}

impl RaptorReply {
    #[cfg(test)]
    pub fn body(&self) -> &[u8] {
        &self.body
    }

    pub fn require_only_fields(&self, allowed: &[&str]) -> Result<(), BackendError> {
        let fields = self.value.as_object().ok_or(BackendError::Protocol)?;
        if fields
            .keys()
            .any(|name| !allowed.iter().any(|allowed| name == allowed))
        {
            return Err(BackendError::Protocol);
        }
        Ok(())
    }
}

fn validate_request(request: &[u8]) -> Result<(), BackendError> {
    if request.is_empty() || request.len() > MAX_RAPTOR_REQUEST_BYTES {
        return Err(BackendError::Protocol);
    }
    let value = json::parse(request).map_err(|_| BackendError::Protocol)?;
    if value.as_object().is_none() {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn validate_socket(path: &Path) -> Result<(), BackendError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| BackendError::Unavailable)?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_socket() {
        return Err(BackendError::Unavailable);
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
/// Internal bounded Unix connection used by the feature-gated lifecycle probe.
#[doc(hidden)]
pub fn connect_deadline(path: &Path, deadline: Instant) -> Result<UnixStream, BackendError> {
    let bytes = path.as_os_str().as_bytes();
    if bytes.is_empty() || bytes.len() >= 104 || bytes.contains(&0) {
        return Err(BackendError::Protocol);
    }
    let mut address = SockaddrUn {
        #[cfg(target_os = "macos")]
        length: (2 + bytes.len() + 1) as u8,
        family: AF_UNIX as _,
        #[cfg(target_os = "linux")]
        path: [0; 108],
        #[cfg(target_os = "macos")]
        path: [0; 104],
    };
    for (destination, source) in address.path.iter_mut().zip(bytes) {
        *destination = *source as std::os::raw::c_char;
    }
    let address_length = u32::try_from(2 + bytes.len() + 1).map_err(|_| BackendError::Protocol)?;
    remaining(deadline)?;
    // SAFETY: all arguments are fixed constants and a zeroed protocol value.
    #[cfg(target_os = "linux")]
    let fd = unsafe { libc_socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | O_NONBLOCK, 0) };
    // Darwin does not accept creation-time CLOEXEC/NONBLOCK flags.
    #[cfg(target_os = "macos")]
    let fd = unsafe { libc_socket(AF_UNIX, SOCK_STREAM, 0) };
    if fd < 0 {
        return Err(BackendError::Connection);
    }
    // SAFETY: fd was returned uniquely by socket and ownership moves to stream.
    let stream = unsafe { UnixStream::from_raw_fd(fd) };
    #[cfg(target_os = "macos")]
    {
        // SAFETY: fcntl updates flags on our live, uniquely owned descriptor.
        if unsafe { libc_fcntl(fd, 2, 1) } != 0 {
            return Err(BackendError::Connection);
        }
        stream
            .set_nonblocking(true)
            .map_err(|_| BackendError::Connection)?;
    }
    // SAFETY: address is initialized for the Linux sockaddr_un ABI and the
    // length covers family, path bytes, and the trailing NUL only.
    loop {
        // SAFETY: address remains initialized and live for every bounded retry.
        if unsafe { libc_connect(fd, &raw const address, address_length) } == 0 {
            break;
        }
        let error = io::Error::last_os_error();
        match error.raw_os_error() {
            Some(EISCONN) => break,
            Some(EAGAIN) => {
                let budget = remaining(deadline)?;
                std::thread::sleep(budget.min(Duration::from_millis(10)));
            }
            Some(EINPROGRESS | EALREADY) => {
                wait_for_connect(fd, deadline)?;
                break;
            }
            _ if error.kind() == io::ErrorKind::Interrupted => continue,
            _ => return Err(classify_connect_error(error)),
        }
    }
    // SAFETY: fcntl only reads and updates the status flags of our live fd.
    let flags = unsafe { libc_fcntl(fd, F_GETFL, 0) };
    if flags < 0 || unsafe { libc_fcntl(fd, F_SETFL, flags | O_NONBLOCK) } != 0 {
        return Err(BackendError::Connection);
    }
    Ok(stream)
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
#[doc(hidden)]
pub fn connect_deadline(path: &Path, deadline: Instant) -> Result<UnixStream, BackendError> {
    remaining(deadline)?;
    let _ = path;
    Err(BackendError::Unavailable)
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn wait_for_connect(fd: i32, deadline: Instant) -> Result<(), BackendError> {
    loop {
        let timeout_ms = poll_timeout_ms(remaining(deadline)?);
        let mut descriptor = PollFd {
            fd,
            events: POLLOUT,
            revents: 0,
        };
        // SAFETY: descriptor points to one initialized pollfd for the duration
        // of the call, and timeout_ms is finite and nonnegative.
        match unsafe { libc_poll(&raw mut descriptor, 1, timeout_ms) } {
            0 => return Err(BackendError::Timeout),
            result if result > 0 => match socket_error(fd)? {
                0 => return Ok(()),
                EAGAIN | EALREADY | EINPROGRESS => continue,
                error => {
                    return Err(classify_connect_error(io::Error::from_raw_os_error(error)));
                }
            },
            _ if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted => continue,
            _ => return Err(BackendError::Connection),
        }
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn socket_error(fd: i32) -> Result<i32, BackendError> {
    let mut error = 0_i32;
    let mut length =
        u32::try_from(std::mem::size_of_val(&error)).map_err(|_| BackendError::Protocol)?;
    // SAFETY: error and length point to initialized writable values of the
    // exact Linux getsockopt SO_ERROR ABI sizes for this call.
    if unsafe { libc_getsockopt(fd, SOL_SOCKET, SO_ERROR, &raw mut error, &raw mut length) } != 0
        || length as usize != std::mem::size_of_val(&error)
    {
        return Err(BackendError::Connection);
    }
    Ok(error)
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn poll_timeout_ms(duration: Duration) -> i32 {
    let mut milliseconds = duration.as_millis();
    if !duration.subsec_nanos().is_multiple_of(1_000_000) {
        milliseconds = milliseconds.saturating_add(1);
    }
    i32::try_from(milliseconds.max(1)).unwrap_or(i32::MAX)
}

fn write_frame(
    stream: &mut UnixStream,
    body: &[u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    remaining(deadline)?;
    let length = u16::try_from(body.len()).map_err(|_| BackendError::Protocol)?;
    write_all_deadline(stream, &length.to_be_bytes(), deadline)?;
    write_all_deadline(stream, body, deadline)
}

fn read_frame(stream: &mut UnixStream, deadline: Instant) -> Result<Vec<u8>, BackendError> {
    remaining(deadline)?;
    let mut header = [0_u8; 2];
    read_exact_deadline(stream, &mut header, deadline)?;
    let length = usize::from(u16::from_be_bytes(header));
    if length == 0 || length > MAX_RAPTOR_RESPONSE_BYTES {
        return Err(BackendError::Protocol);
    }
    let mut body = vec![0_u8; length];
    read_exact_deadline(stream, &mut body, deadline)?;
    Ok(body)
}

fn require_connection_end(stream: &mut UnixStream, deadline: Instant) -> Result<(), BackendError> {
    loop {
        remaining(deadline)?;
        let mut extra = [0_u8; 1];
        match stream.read(&mut extra) {
            Ok(0) => return Ok(()),
            Ok(_) => return Err(BackendError::Protocol),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                wait_ready(stream, 1, deadline)?
            }
            Err(error) => return Err(classify_io_error(&error)),
        }
    }
}

fn write_all_deadline(
    stream: &mut UnixStream,
    mut bytes: &[u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !bytes.is_empty() {
        remaining(deadline)?;
        match stream.write(bytes) {
            Ok(0) => return Err(BackendError::Connection),
            Ok(count) => bytes = &bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                wait_ready(stream, POLLOUT, deadline)?
            }
            Err(error) => return Err(classify_io_error(&error)),
        }
    }
    Ok(())
}

fn read_exact_deadline(
    stream: &mut UnixStream,
    mut bytes: &mut [u8],
    deadline: Instant,
) -> Result<(), BackendError> {
    while !bytes.is_empty() {
        remaining(deadline)?;
        match stream.read(bytes) {
            Ok(0) => return Err(BackendError::Protocol),
            Ok(count) => bytes = &mut bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                wait_ready(stream, 1, deadline)?
            }
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
                ) =>
            {
                return Err(BackendError::Timeout);
            }
            Err(_) => return Err(BackendError::Protocol),
        }
    }
    Ok(())
}

fn wait_ready(stream: &UnixStream, events: i16, deadline: Instant) -> Result<(), BackendError> {
    use std::os::fd::AsRawFd;
    loop {
        let mut descriptor = PollFd {
            fd: stream.as_raw_fd(),
            events,
            revents: 0,
        };
        let timeout = poll_timeout_ms(remaining(deadline)?);
        // SAFETY: descriptor is initialized and live; one fd and finite timeout.
        match unsafe { libc_poll(&raw mut descriptor, 1, timeout) } {
            0 => return Err(BackendError::Timeout),
            result if result > 0 => return Ok(()),
            _ if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted => continue,
            _ => return Err(BackendError::Connection),
        }
    }
}

fn remaining(deadline: Instant) -> Result<Duration, BackendError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or(BackendError::Timeout)
}

fn classify_connect_error(error: io::Error) -> BackendError {
    match error.kind() {
        io::ErrorKind::NotFound | io::ErrorKind::ConnectionRefused => BackendError::Unavailable,
        _ => classify_io_error(&error),
    }
}

fn classify_io_error(error: &io::Error) -> BackendError {
    match error.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => BackendError::Timeout,
        _ => BackendError::Connection,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use std::os::fd::AsRawFd;
    use std::os::unix::net::UnixListener;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(_name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = fs::canonicalize(root).unwrap().join(format!(
            "rc-{}-{}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    fn client(root: &Path) -> RaptorClient {
        RaptorClient::new(root.to_path_buf(), RAPTOR_PROTOCOL_VERSION).unwrap()
    }

    fn read_request(stream: &mut UnixStream) -> Vec<u8> {
        let mut header = [0_u8; 2];
        stream.read_exact(&mut header).unwrap();
        let mut body = vec![0_u8; usize::from(u16::from_be_bytes(header))];
        for byte in &mut body {
            stream.read_exact(std::slice::from_mut(byte)).unwrap();
        }
        let mut end = [0_u8; 1];
        assert_eq!(stream.read(&mut end).unwrap(), 0);
        body
    }

    fn framed(body: &[u8]) -> Vec<u8> {
        let mut frame = Vec::with_capacity(body.len() + 2);
        frame.extend_from_slice(&(body.len() as u16).to_be_bytes());
        frame.extend_from_slice(body);
        frame
    }

    fn serve_once<F>(root: &Path, handler: F) -> thread::JoinHandle<()>
    where
        F: FnOnce(&mut UnixStream) + Send + 'static,
    {
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            handler(&mut stream);
            let _ = stream.shutdown(std::net::Shutdown::Write);
        })
    }

    #[test]
    fn partial_reads_and_writes_preserve_one_framed_json_exchange() {
        let root = task_temp("partial");
        let server = serve_once(&root, |stream| {
            assert_eq!(read_request(stream), br#"{"cmd":"status"}"#);
            for byte in framed(br#"{"status":"ok"}"#) {
                stream.write_all(&[byte]).unwrap();
            }
        });
        let reply = client(&root)
            .command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status"}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();
        assert_eq!(reply.body(), br#"{"status":"ok"}"#);
        let value = json::parse(reply.body()).unwrap();
        assert_eq!(
            value
                .get_path("status")
                .and_then(crate::json::Value::as_str),
            Some("ok")
        );
        reply.require_only_fields(&["status"]).unwrap();
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_duplicate_and_non_object_json_fail_closed() {
        for (name, response) in [
            ("malformed", br#"{"status":}"#.as_slice()),
            (
                "duplicate",
                br#"{"status":"ok","status":"error"}"#.as_slice(),
            ),
            ("non-object", br#"["ok"]"#.as_slice()),
        ] {
            let root = task_temp(name);
            let response = response.to_vec();
            let server = serve_once(&root, move |stream| {
                let _ = read_request(stream);
                stream.write_all(&framed(&response)).unwrap();
            });
            let result = client(&root).command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status"}"#,
                Instant::now() + Duration::from_secs(1),
            );
            assert!(
                matches!(result, Err(BackendError::Protocol)),
                "{name}: {result:?}"
            );
            server.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn zero_oversized_truncated_and_multiple_responses_fail_closed() {
        let oversized = (MAX_RAPTOR_RESPONSE_BYTES + 1) as u16;
        let cases = [
            ("zero", vec![0, 0]),
            ("oversized", oversized.to_be_bytes().to_vec()),
            ("truncated", vec![0, 4, b'{', b'}']),
            (
                "multiple",
                [framed(br#"{"status":"ok"}"#), framed(br#"{"extra":1}"#)].concat(),
            ),
        ];
        for (name, response) in cases {
            let root = task_temp(name);
            let server = serve_once(&root, move |stream| {
                let _ = read_request(stream);
                stream.write_all(&response).unwrap();
            });
            let result = client(&root).command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status"}"#,
                Instant::now() + Duration::from_secs(1),
            );
            assert!(
                matches!(result, Err(BackendError::Protocol)),
                "{name}: {result:?}"
            );
            server.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn timeout_disconnect_and_restart_are_bounded_and_recoverable() {
        let root = task_temp("timeout");
        let server = serve_once(&root, |stream| {
            let _ = read_request(stream);
            thread::sleep(Duration::from_millis(100));
        });
        let result = client(&root).command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"status"}"#,
            Instant::now() + Duration::from_millis(30),
        );
        assert!(matches!(result, Err(BackendError::Timeout)), "{result:?}");
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();

        let root = task_temp("disconnect");
        let server = serve_once(&root, |stream| {
            let _ = read_request(stream);
            stream.write_all(&[0, 4, b'{', b'}']).unwrap();
        });
        let result = client(&root).command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"status"}"#,
            Instant::now() + Duration::from_secs(1),
        );
        assert!(
            matches!(result, Err(BackendError::Protocol)),
            "disconnect: {result:?}"
        );
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();

        let root = task_temp("restart");
        let client = client(&root);
        assert!(matches!(
            client.command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status"}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Unavailable)
        ));
        let server = serve_once(&root, |stream| {
            let _ = read_request(stream);
            stream.write_all(&framed(br#"{"status":"ok"}"#)).unwrap();
        });
        assert!(
            client
                .command(
                    RaptorDaemon::Rvd,
                    br#"{"cmd":"status"}"#,
                    Instant::now() + Duration::from_secs(1),
                )
                .is_ok()
        );
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn saturated_accept_queue_does_not_escape_the_connect_deadline() {
        unsafe extern "C" {
            fn listen(fd: i32, backlog: i32) -> i32;
        }

        let root = task_temp("connect-backlog");
        let path = root.join("rvd.sock");
        let listener = UnixListener::bind(&path).unwrap();
        // SAFETY: listener owns a valid listening AF_UNIX socket for this test.
        assert_eq!(unsafe { listen(listener.as_raw_fd(), 1) }, 0);
        let mut queued = Vec::new();
        let started = Instant::now();
        let mut bounded_timeout = false;
        for _ in 0..8 {
            match connect_deadline(&path, Instant::now() + Duration::from_millis(30)) {
                Ok(stream) => queued.push(stream),
                Err(BackendError::Timeout) => {
                    bounded_timeout = true;
                    break;
                }
                // Darwin refuses a full Unix listen queue immediately.
                #[cfg(target_os = "macos")]
                Err(BackendError::Unavailable) => {
                    bounded_timeout = true;
                    break;
                }
                Err(error) => panic!("unexpected connect result: {error:?}"),
            }
        }
        assert!(bounded_timeout, "test did not saturate the accept queue");
        assert!(started.elapsed() < Duration::from_millis(250));
        drop(queued);
        drop(listener);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn request_and_protocol_versions_are_strictly_bounded() {
        assert!(matches!(
            RaptorClient::new(PathBuf::from("/run/rss"), RAPTOR_PROTOCOL_VERSION + 1),
            Err(BackendError::Protocol)
        ));
        let root = task_temp("request-bounds");
        let client = client(&root);
        assert!(matches!(
            client.command(
                RaptorDaemon::Rvd,
                &[b'x'; MAX_RAPTOR_REQUEST_BYTES + 1],
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Protocol)
        ));
        assert!(matches!(
            client.command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status","cmd":"config-show"}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Protocol)
        ));
        assert!(matches!(
            client.command(
                RaptorDaemon::Rvd,
                br#"["status"]"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Protocol)
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn typed_consumers_reject_unknown_response_fields() {
        let root = task_temp("unknown-field");
        let server = serve_once(&root, |stream| {
            let _ = read_request(stream);
            stream
                .write_all(&framed(br#"{"status":"ok","unexpected":true}"#))
                .unwrap();
        });
        let reply = client(&root)
            .command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status"}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();
        assert!(matches!(
            reply.require_only_fields(&["status"]),
            Err(BackendError::Protocol)
        ));
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn slow_fragmented_reply_obeys_one_absolute_deadline() {
        let root = task_temp("slow");
        let server = serve_once(&root, |stream| {
            let _ = read_request(stream);
            for byte in framed(br#"{"status":"ok"}"#) {
                thread::sleep(Duration::from_millis(20));
                if stream.write_all(&[byte]).is_err() {
                    break;
                }
            }
        });
        let started = Instant::now();
        assert!(matches!(
            client(&root).command(
                RaptorDaemon::Rvd,
                br#"{"cmd":"status"}"#,
                started + Duration::from_millis(85)
            ),
            Err(BackendError::Timeout)
        ));
        assert!(started.elapsed() < Duration::from_millis(150));
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
