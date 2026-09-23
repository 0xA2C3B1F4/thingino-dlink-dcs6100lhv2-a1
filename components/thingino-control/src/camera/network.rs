use super::*;
use crate::decode::{HexSsidDecode, decode_hex_ssid, decode_wpa_ssid};

pub(super) fn split_host_port(
    target: &str,
    default_port: u16,
) -> Result<(String, u16), BackendError> {
    if target.starts_with('[') {
        let (host, port) = target
            .strip_prefix('[')
            .and_then(|value| value.split_once("]:"))
            .ok_or(BackendError::Protocol)?;
        let port = port.parse::<u16>().map_err(|_| BackendError::Protocol)?;
        return Ok((host.to_owned(), port));
    }
    if target.matches(':').count() == 1 {
        let (host, port) = target.split_once(':').ok_or(BackendError::Protocol)?;
        let port = port.parse::<u16>().map_err(|_| BackendError::Protocol)?;
        if host.is_empty() || port == 0 {
            return Err(BackendError::Protocol);
        }
        return Ok((host.to_owned(), port));
    }
    Ok((target.to_owned(), default_port))
}

#[cfg(target_os = "linux")]
pub(super) static WPA_CLIENT_SEQUENCE: AtomicU32 = AtomicU32::new(1);

#[cfg(target_os = "linux")]
pub(super) struct WpaControl {
    fd: c_int,
}

#[cfg(target_os = "linux")]
impl Drop for WpaControl {
    fn drop(&mut self) {
        // SAFETY: fd is owned by this value and is closed exactly once.
        unsafe {
            libc_close(self.fd);
        }
    }
}

#[cfg(target_os = "linux")]
impl WpaControl {
    fn connect(path: &Path) -> Result<Self, BackendError> {
        let remote = path.as_os_str().as_encoded_bytes();
        if !path.is_absolute() || remote.len() + 1 >= 108 {
            return Err(BackendError::Protocol);
        }
        // SAFETY: socket has no pointer arguments and returns a new descriptor.
        let fd = unsafe { libc_socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0) };
        if fd < 0 {
            return Err(wpa_control_error("socket"));
        }
        let control = Self { fd };
        let sequence = WPA_CLIENT_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        // SAFETY: getpid has no preconditions.
        let local_name = format!("thingino-control-{}-{sequence}", unsafe { getpid() });
        let (local, local_length) = unix_address(local_name.as_bytes(), true)?;
        let (remote, remote_length) = unix_address(remote, false)?;
        // SAFETY: both sockaddr values are initialized for the supplied lengths.
        if unsafe { libc_bind(fd, &raw const local, local_length) } != 0 {
            return Err(wpa_control_error("bind"));
        }
        if unsafe { libc_connect(fd, &raw const remote, remote_length) } != 0 {
            return Err(wpa_control_error("connect"));
        }
        Ok(control)
    }

    fn receive(&self, deadline: Instant) -> Result<Vec<u8>, BackendError> {
        let mut response = vec![0_u8; 64 * 1024];
        loop {
            if Instant::now() >= deadline {
                return Err(BackendError::Timeout);
            }
            // SAFETY: response owns a writable buffer of response.len() bytes.
            let count = unsafe {
                libc_recv(
                    self.fd,
                    response.as_mut_ptr().cast(),
                    response.len(),
                    MSG_DONTWAIT,
                )
            };
            if count >= 0 {
                response.truncate(count as usize);
                return Ok(response);
            }
            let error = io::Error::last_os_error();
            let retryable_errno = matches!(error.raw_os_error(), Some(EAGAIN | EINTR));
            let retryable_kind = matches!(
                error.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
            );
            if !retryable_errno && !retryable_kind {
                eprintln!(
                    "thingino-controld: wpa control recv failed: errno={}",
                    error.raw_os_error().unwrap_or(-1)
                );
                return Err(BackendError::Unavailable);
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn request(&self, command: &[u8], deadline: Instant) -> Result<Vec<u8>, BackendError> {
        if command.is_empty() || command.len() > 64 {
            return Err(BackendError::Protocol);
        }
        // SAFETY: command points to command.len() readable bytes.
        if unsafe { libc_send(self.fd, command.as_ptr().cast(), command.len(), 0) }
            != command.len() as isize
        {
            return Err(wpa_control_error("send"));
        }
        loop {
            let response = self.receive(deadline)?;
            // An attached control socket can receive asynchronous events while
            // it is waiting for a command reply. They are not command results.
            if !response.starts_with(b"<") {
                return Ok(response);
            }
        }
    }
}

#[cfg(target_os = "linux")]
pub(super) fn wpa_signal_rssi(path: &Path, deadline: Instant) -> Result<i32, BackendError> {
    let response = WpaControl::connect(path)?.request(b"SIGNAL_POLL", deadline)?;
    parse_wpa_signal_poll(&response).ok_or(BackendError::Protocol)
}

#[cfg(not(target_os = "linux"))]
pub(super) fn wpa_signal_rssi(_path: &Path, _deadline: Instant) -> Result<i32, BackendError> {
    Err(BackendError::Unavailable)
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(super) fn parse_wpa_signal_poll(response: &[u8]) -> Option<i32> {
    let text = std::str::from_utf8(response).ok()?;
    text.lines().take(32).find_map(|line| {
        let value = line.strip_prefix("RSSI=")?.parse::<i32>().ok()?;
        (-127..=-1).contains(&value).then_some(value)
    })
}

#[cfg(target_os = "linux")]
pub(super) fn wpa_control_error(stage: &str) -> BackendError {
    eprintln!(
        "thingino-controld: wpa control {stage} failed: errno={}",
        io::Error::last_os_error().raw_os_error().unwrap_or(-1)
    );
    BackendError::Unavailable
}

#[cfg(target_os = "linux")]
pub(super) fn unix_address(
    value: &[u8],
    abstract_name: bool,
) -> Result<(SockaddrUn, u32), BackendError> {
    let offset = std::mem::offset_of!(SockaddrUn, path);
    let required = value.len() + usize::from(abstract_name);
    if value.is_empty() || required >= 108 {
        return Err(BackendError::Protocol);
    }
    let mut address = SockaddrUn {
        family: AF_UNIX as u16,
        path: [0; 108],
    };
    let start = usize::from(abstract_name);
    for (index, byte) in value.iter().enumerate() {
        address.path[start + index] = *byte as c_char;
    }
    let terminator = usize::from(!abstract_name);
    Ok((address, (offset + required + terminator) as u32))
}

#[cfg(target_os = "linux")]
pub(super) fn wpa_scan_results(path: &Path, deadline: Instant) -> Result<Vec<Value>, BackendError> {
    let control = WpaControl::connect(path)?;
    if control.request(b"ATTACH", deadline)? != b"OK\n" {
        eprintln!("thingino-controld: wpa control rejected ATTACH");
        return Err(BackendError::Unavailable);
    }
    let scan = control.request(b"SCAN", deadline)?;
    if scan != b"OK\n" && scan != b"FAIL-BUSY\n" {
        eprintln!("thingino-controld: wpa control rejected SCAN");
        return Err(BackendError::Unavailable);
    }
    loop {
        let event = control.receive(deadline)?;
        if event
            .windows(b"CTRL-EVENT-SCAN-RESULTS".len())
            .any(|value| value == b"CTRL-EVENT-SCAN-RESULTS")
        {
            break;
        }
        if event
            .windows(b"CTRL-EVENT-SCAN-FAILED".len())
            .any(|value| value == b"CTRL-EVENT-SCAN-FAILED")
        {
            return Err(BackendError::Unavailable);
        }
    }
    let response = control.request(b"SCAN_RESULTS", deadline)?;
    parse_wpa_scan_results(&response)
}

#[cfg(not(target_os = "linux"))]
pub(super) fn wpa_scan_results(
    _path: &Path,
    _deadline: Instant,
) -> Result<Vec<Value>, BackendError> {
    Err(BackendError::Unavailable)
}

#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub(super) fn parse_wpa_scan_results(response: &[u8]) -> Result<Vec<Value>, BackendError> {
    let text = std::str::from_utf8(response).map_err(|_| BackendError::Protocol)?;
    let mut lines = text.lines();
    if lines.next() != Some("bssid / frequency / signal level / flags / ssid") {
        return Err(BackendError::Protocol);
    }
    let mut networks = Vec::new();
    for line in lines.take(128) {
        let mut fields = line.splitn(5, '\t');
        let bssid = fields.next().ok_or(BackendError::Protocol)?;
        let frequency = fields.next().ok_or(BackendError::Protocol)?;
        let signal = fields.next().ok_or(BackendError::Protocol)?;
        let flags = fields.next().ok_or(BackendError::Protocol)?;
        let ssid = fields.next().ok_or(BackendError::Protocol)?;
        if !valid_mac(bssid)
            || frequency.parse::<u32>().is_err()
            || signal.parse::<i32>().is_err()
            || flags.len() > 256
        {
            continue;
        }
        let Some(ssid) = decode_wpa_ssid(ssid) else {
            continue;
        };
        let security = if flags.contains("SAE") {
            "WPA3"
        } else if flags.contains("WPA2") || flags.contains("RSN") {
            "WPA2"
        } else if flags.contains("WPA") {
            "WPA"
        } else if flags.contains("WEP") {
            "WEP"
        } else {
            "Open"
        };
        networks.push(object([
            ("ssid", Value::String(ssid)),
            ("bssid", Value::String(bssid.to_owned())),
            ("frequency", Value::Number(frequency.to_owned())),
            ("signal", Value::Number(signal.to_owned())),
            ("security", Value::String(security.to_owned())),
        ]));
    }
    Ok(networks)
}

pub(super) fn config_assignment(config: &str, name: &str) -> Option<String> {
    config.lines().find_map(|line| {
        let (key, value) = line.trim().split_once('=')?;
        if key != name {
            return None;
        }
        let value = value.trim();
        Some(
            value
                .strip_prefix('"')
                .and_then(|value| value.strip_suffix('"'))
                .unwrap_or(value)
                .replace("\\\"", "\"")
                .replace("\\\\", "\\"),
        )
    })
}

pub(super) fn config_ssid_assignment(config: &str) -> Option<String> {
    let raw = config.lines().find_map(|line| {
        let (key, value) = line.trim().split_once('=')?;
        (key == "ssid").then(|| value.trim())
    })?;
    if raw.starts_with('"') {
        return config_assignment(config, "ssid");
    }
    match decode_hex_ssid(raw) {
        HexSsidDecode::Decoded(value) => return Some(value),
        HexSsidDecode::Invalid => return None,
        HexSsidDecode::Plain => {}
    }
    config_assignment(config, "ssid")
}

pub(super) fn interface_value(config: &str, name: &str) -> String {
    config
        .lines()
        .find_map(|line| {
            let mut fields = line.split_ascii_whitespace();
            (fields.next()? == name).then(|| fields.next().unwrap_or_default().to_owned())
        })
        .unwrap_or_default()
}

pub(super) fn valid_mac(value: &str) -> bool {
    let fields = value.split(':').collect::<Vec<_>>();
    fields.len() == 6
        && fields
            .iter()
            .all(|field| field.len() == 2 && field.bytes().all(|byte| byte.is_ascii_hexdigit()))
}

pub(super) fn valid_wifi_password(value: &str) -> bool {
    !value.chars().any(char::is_control)
        && ((8..=63).contains(&value.len())
            || value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
}

pub(super) fn wpa_quoted(value: &str) -> Option<String> {
    if value.chars().any(char::is_control) {
        return None;
    }
    let mut quoted = String::with_capacity(value.len() + 2);
    quoted.push('"');
    for character in value.chars() {
        if matches!(character, '"' | '\\') {
            quoted.push('\\');
        }
        quoted.push(character);
    }
    quoted.push('"');
    Some(quoted)
}

pub(super) fn safe_server_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 253
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_' | b':'))
}

pub(super) fn local_ipv4(path: &Path) -> Option<String> {
    let text = String::from_utf8(read_bounded(path, 256 * 1024).ok()?).ok()?;
    let mut candidate = None;
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(value) = trimmed.strip_prefix("|-- ") {
            candidate = Some(value.to_owned());
        } else if trimmed == "/32 host LOCAL"
            && let Some(value) = candidate.take()
            && value.parse::<std::net::Ipv4Addr>().ok().is_some()
            && !value.starts_with("127.")
        {
            return Some(value);
        }
    }
    None
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct InterfaceRoute {
    destination: u32,
    gateway: u32,
    mask: u32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct RuntimeIpv4Values {
    pub address: String,
    pub netmask: String,
    pub gateway: String,
    pub broadcast: String,
}

pub(super) fn parse_interface_routes(text: &str, name: &str) -> Vec<InterfaceRoute> {
    text.lines()
        .skip(1)
        .filter_map(|line| {
            let fields = line.split_ascii_whitespace().collect::<Vec<_>>();
            if fields.len() < 8 || fields[0] != name {
                return None;
            }
            let flags = u16::from_str_radix(fields[3], 16).ok()?;
            if flags & 1 == 0 {
                return None;
            }
            Some(InterfaceRoute {
                destination: u32::from_str_radix(fields[1], 16).ok()?,
                gateway: u32::from_str_radix(fields[2], 16).ok()?,
                mask: u32::from_str_radix(fields[7], 16).ok()?,
            })
        })
        .collect()
}

fn route_ipv4(value: u32) -> std::net::Ipv4Addr {
    std::net::Ipv4Addr::from(value.to_le_bytes())
}

pub(super) fn runtime_ipv4_values(
    local_ip: &str,
    routes: &[InterfaceRoute],
) -> Option<RuntimeIpv4Values> {
    let address = local_ip.parse::<std::net::Ipv4Addr>().ok()?;
    let address_bits = u32::from_le_bytes(address.octets());
    let connected = routes
        .iter()
        .filter(|route| route.mask != 0 && address_bits & route.mask == route.destination)
        .max_by_key(|route| route.mask.count_ones())?;
    let gateway = routes
        .iter()
        .find(|route| route.destination == 0 && route.mask == 0 && route.gateway != 0)
        .map(|route| route_ipv4(route.gateway).to_string())
        .unwrap_or_default();
    Some(RuntimeIpv4Values {
        address: address.to_string(),
        netmask: route_ipv4(connected.mask).to_string(),
        gateway,
        broadcast: route_ipv4(address_bits | !connected.mask).to_string(),
    })
}

pub(super) fn safe_hostname(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 63
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

pub(super) fn safe_access_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_graphic() && !matches!(byte, b':' | b'/' | b'\\'))
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use std::os::unix::net::UnixDatagram;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = PathBuf::from(root).join(format!(
            "wpa-control-{}-{}-{name}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn signal_poll_uses_datagram_control_socket_and_returns_dbm() {
        let root = task_temp("signal-poll");
        let socket_path = root.join("wlan0");
        let server = UnixDatagram::bind(&socket_path).unwrap();
        let worker = thread::spawn(move || {
            let mut request = [0_u8; 64];
            let (length, peer) = server.recv_from(&mut request).unwrap();
            assert_eq!(&request[..length], b"SIGNAL_POLL");
            server
                .send_to_addr(b"RSSI=-44\nLINKSPEED=72\n", &peer)
                .unwrap();
        });

        assert_eq!(
            wpa_signal_rssi(&socket_path, Instant::now() + Duration::from_secs(1)),
            Ok(-44)
        );
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
