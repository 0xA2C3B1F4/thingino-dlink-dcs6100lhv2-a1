use std::collections::BTreeMap;
use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::IpAddr;
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use crate::decode::decode_base64;
use crate::json::{self, Value};

const MAX_KEY_BYTES: u64 = 4096;
const MAX_CONFIG_BYTES: u64 = 128 * 1024;
const MAX_SHADOW_BYTES: u64 = 16 * 1024;
const COOKIE_NAME: &str = "thingino_session";
const SESSION_RECENT_SECONDS: u64 = 10 * 60;
const SESSION_IDLE_SECONDS: u64 = 12 * 60 * 60;
const SESSION_MAX_SECONDS: u64 = 24 * 60 * 60;
const LOGIN_WINDOW: Duration = Duration::from_secs(60);
const LOGIN_SOURCE_LIMIT: u16 = 5;
const LOGIN_GLOBAL_LIMIT: u16 = 32;
static SYSTEM_CRYPT_LOCK: Mutex<()> = Mutex::new(());

#[cfg(all(target_os = "linux", target_arch = "aarch64"))]
const O_NOFOLLOW: i32 = 0x8000;
#[cfg(all(target_os = "linux", not(target_arch = "aarch64")))]
const O_NOFOLLOW: i32 = 0x20000;
#[cfg(target_os = "macos")]
const O_NOFOLLOW: i32 = 0x100;
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
const O_NOFOLLOW: i32 = 0;

#[derive(Clone, Debug)]
pub struct WebAuthPaths {
    pub api_key: PathBuf,
    pub thingino_config: PathBuf,
    pub shadow: PathBuf,
}

fn random_hex(bytes: usize) -> io::Result<String> {
    let mut random = vec![0_u8; bytes];
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW)
        .open("/dev/urandom")?;
    file.read_exact(&mut random)?;
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes * 2);
    for byte in random {
        result.push(char::from(HEX[usize::from(byte >> 4)]));
        result.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    Ok(result)
}

fn shadow_hash(path: &Path, username: &str) -> io::Result<String> {
    let raw = read_regular(path, MAX_SHADOW_BYTES)?;
    let text = std::str::from_utf8(&raw)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "shadow is not UTF-8"))?;
    let prefix = format!("{username}:");
    let line = text
        .lines()
        .find(|line| line.starts_with(&prefix))
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "account is absent"))?;
    let stored = line
        .split(':')
        .nth(1)
        .filter(|value| value.starts_with("$6$") && value.len() <= 256)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "password is locked"))?;
    Ok(stored.to_owned())
}

#[cfg_attr(target_os = "linux", link(name = "dl"))]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
    fn fchmod(fd: c_int, mode: u32) -> c_int;
    fn fchown(fd: c_int, owner: u32, group: u32) -> c_int;
    fn getegid() -> u32;
    fn geteuid() -> u32;
}

fn with_crypt_lock<T>(lock: &Mutex<()>, operation: impl FnOnce() -> Option<T>) -> Option<T> {
    let _guard = lock.lock().ok()?;
    operation()
}

fn crypt_password(password: &[u8], salt: &str) -> Option<String> {
    let Ok(password) = CString::new(password) else {
        return None;
    };
    let Ok(salt_input) = CString::new(salt) else {
        return None;
    };
    with_crypt_lock(&SYSTEM_CRYPT_LOCK, || {
        const RTLD_NOW: c_int = 2;
        let mut libraries: Vec<Option<CString>> = vec![None];
        for name in ["libcrypt.so.2", "libcrypt.so.1", "libcrypt.so"] {
            if let Ok(name) = CString::new(name) {
                libraries.push(Some(name));
            }
        }
        for library in libraries {
            let filename = library.as_ref().map_or(ptr::null(), |value| value.as_ptr());
            // SAFETY: filename is null or a live NUL-terminated library name.
            let handle = unsafe { dlopen(filename, RTLD_NOW) };
            if handle.is_null() {
                continue;
            }
            // SAFETY: the symbol name is static and NUL-terminated.
            let symbol = unsafe { dlsym(handle, c"crypt".as_ptr()) };
            if symbol.is_null() {
                // SAFETY: handle came from dlopen above.
                unsafe { dlclose(handle) };
                continue;
            }
            // SAFETY: a symbol named crypt has the POSIX crypt function signature.
            let crypt: unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_char =
                unsafe { std::mem::transmute(symbol) };
            // SAFETY: both arguments are live NUL-terminated strings. The process-global lock
            // remains held until the returned process-global buffer has been copied below.
            let result = unsafe { crypt(password.as_ptr(), salt_input.as_ptr()) };
            let calculated = if result.is_null() {
                None
            } else {
                // SAFETY: crypt returns a NUL-terminated string until the next crypt call, which
                // cannot start through this process boundary while SYSTEM_CRYPT_LOCK is held.
                unsafe { CStr::from_ptr(result) }
                    .to_str()
                    .ok()
                    .map(str::to_owned)
            };
            // SAFETY: the result string was copied before closing the library.
            unsafe { dlclose(handle) };
            return calculated;
        }
        None
    })
}

fn verify_shadow_password(password: &[u8], stored: &str) -> bool {
    crypt_password(password, stored)
        .is_some_and(|calculated| constant_time_equal(calculated.as_bytes(), stored.as_bytes()))
}

impl Default for WebAuthPaths {
    fn default() -> Self {
        Self {
            api_key: "/etc/thingino-api.key".into(),
            thingino_config: "/etc/thingino.json".into(),
            shadow: "/etc/shadow".into(),
        }
    }
}

pub trait PasswordVerifier: Send + Sync {
    fn verify(&self, password: &[u8], stored: &str) -> bool;
}

pub trait PasswordHasher: Send + Sync {
    fn hash(&self, password: &[u8], salt: &str) -> Option<String>;
}

struct SystemPasswordVerifier;
struct SystemPasswordHasher;

impl PasswordVerifier for SystemPasswordVerifier {
    fn verify(&self, password: &[u8], stored: &str) -> bool {
        verify_shadow_password(password, stored)
    }
}

impl PasswordHasher for SystemPasswordHasher {
    fn hash(&self, password: &[u8], salt: &str) -> Option<String> {
        crypt_password(password, salt)
    }
}

#[derive(Clone)]
pub struct WebAuth {
    paths: WebAuthPaths,
    sessions: Arc<Mutex<BTreeMap<String, Session>>>,
    login_limiter: Arc<Mutex<LoginRateLimiter>>,
    credential_updates: Arc<Mutex<()>>,
    password_verifier: Arc<dyn PasswordVerifier>,
    password_hasher: Arc<dyn PasswordHasher>,
    // Sessions are process-local. NTP/manual wall-clock changes must not alter
    // their age, idle timeout or recent-authentication window.
    session_clock: Arc<dyn Fn() -> Instant + Send + Sync>,
}

#[derive(Debug, Default)]
struct LoginRateLimiter {
    window_started: Option<Instant>,
    global_attempts: u16,
    source_attempts: BTreeMap<Option<IpAddr>, u16>,
}

impl LoginRateLimiter {
    fn admit(&mut self, source: Option<IpAddr>, now: Instant) -> bool {
        if self
            .window_started
            .is_none_or(|started| now.saturating_duration_since(started) >= LOGIN_WINDOW)
        {
            self.window_started = Some(now);
            self.global_attempts = 0;
            self.source_attempts.clear();
        }
        let source_attempts = self.source_attempts.entry(source).or_default();
        if self.global_attempts >= LOGIN_GLOBAL_LIMIT || *source_attempts >= LOGIN_SOURCE_LIMIT {
            return false;
        }
        self.global_attempts += 1;
        *source_attempts += 1;
        true
    }
}

#[derive(Clone, Debug)]
struct Session {
    username: String,
    is_default_password: bool,
    created: Instant,
    last_access: Instant,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LoginResult {
    pub session_id: String,
    pub is_default_password: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionStatus {
    pub authenticated: bool,
    pub username: Option<String>,
    pub is_default_password: bool,
    pub client_ip: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AuthError {
    InvalidRequest,
    InvalidCredentials,
    Unavailable,
}

impl WebAuth {
    pub fn new(paths: WebAuthPaths) -> Self {
        Self::with_password_crypto(
            paths,
            Arc::new(SystemPasswordVerifier),
            Arc::new(SystemPasswordHasher),
        )
    }

    pub fn with_password_verifier(
        paths: WebAuthPaths,
        password_verifier: Arc<dyn PasswordVerifier>,
    ) -> Self {
        Self::with_password_crypto(paths, password_verifier, Arc::new(SystemPasswordHasher))
    }

    pub fn with_password_crypto(
        paths: WebAuthPaths,
        password_verifier: Arc<dyn PasswordVerifier>,
        password_hasher: Arc<dyn PasswordHasher>,
    ) -> Self {
        Self {
            paths,
            sessions: Arc::new(Mutex::new(BTreeMap::new())),
            login_limiter: Arc::new(Mutex::new(LoginRateLimiter::default())),
            credential_updates: Arc::new(Mutex::new(())),
            password_verifier,
            password_hasher,
            session_clock: Arc::new(Instant::now),
        }
    }

    pub(crate) fn admit_login(&self, remote_address: Option<&[u8]>) -> bool {
        let source = remote_address
            .and_then(|value| std::str::from_utf8(value).ok())
            .and_then(normalize_address);
        self.login_limiter
            .lock()
            .is_ok_and(|mut limiter| limiter.admit(source, Instant::now()))
    }

    pub(crate) fn lock_credential_update(&self) -> Result<MutexGuard<'_, ()>, AuthError> {
        self.credential_updates
            .lock()
            .map_err(|_| AuthError::Unavailable)
    }

    pub(crate) fn authorize(
        &self,
        cookie: Option<&[u8]>,
        api_key: Option<&[u8]>,
        remote_address: Option<&[u8]>,
        allow_trusted_read: bool,
    ) -> bool {
        let remote = remote_address
            .and_then(|value| std::str::from_utf8(value).ok())
            .and_then(normalize_address);
        if allow_trusted_read && remote.is_some_and(|address| self.is_trusted(address)) {
            return true;
        }
        if self.authorize_session(cookie) {
            return true;
        }
        api_key.is_some_and(|provided| self.valid_api_key(provided))
    }

    pub(crate) fn authorize_session(&self, cookie: Option<&[u8]>) -> bool {
        cookie
            .and_then(session_from_cookie)
            .is_some_and(|session| self.valid_session(session))
    }

    pub(crate) fn authorize_recent_session(&self, cookie: Option<&[u8]>) -> bool {
        cookie
            .and_then(session_from_cookie)
            .is_some_and(|session| self.valid_session_with_max_age(session, SESSION_RECENT_SECONDS))
    }

    fn valid_api_key(&self, provided: &[u8]) -> bool {
        if provided.is_empty() || provided.len() > MAX_KEY_BYTES as usize {
            return false;
        }
        let Ok(mut stored) = read_regular(&self.paths.api_key, MAX_KEY_BYTES) else {
            return false;
        };
        stored.retain(|byte| !matches!(byte, b' ' | b'\r' | b'\n'));
        !stored.is_empty() && constant_time_equal(provided, &stored)
    }

    fn valid_session(&self, session: &str) -> bool {
        self.valid_session_with_max_age(session, SESSION_MAX_SECONDS)
    }

    fn valid_session_with_max_age(&self, session: &str, maximum_age: u64) -> bool {
        if session.len() != 32 || !session.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return false;
        }
        let Ok(mut sessions) = self.sessions.lock() else {
            return false;
        };
        let Some(current) = sessions.get_mut(session) else {
            return false;
        };
        let now = (self.session_clock)();
        if now.saturating_duration_since(current.last_access)
            > Duration::from_secs(SESSION_IDLE_SECONDS)
            || now.saturating_duration_since(current.created)
                > Duration::from_secs(SESSION_MAX_SECONDS)
        {
            sessions.remove(session);
            return false;
        }
        let accepted =
            now.saturating_duration_since(current.created) <= Duration::from_secs(maximum_age);
        current.last_access = now;
        accepted
    }

    pub fn login(&self, body: &[u8]) -> Result<LoginResult, AuthError> {
        let document = json::parse(body).map_err(|_| AuthError::InvalidRequest)?;
        let username = document
            .get_path("username")
            .and_then(Value::as_str)
            .filter(|value| *value == "root")
            .ok_or(AuthError::InvalidCredentials)?;
        let encoded = document
            .get_path("password")
            .and_then(Value::as_str)
            .ok_or(AuthError::InvalidRequest)?;
        let password = match document.get_path("encoding").and_then(Value::as_str) {
            Some("base64") => decode_base64(encoded).ok_or(AuthError::InvalidRequest)?,
            None | Some("") => encoded.as_bytes().to_vec(),
            _ => return Err(AuthError::InvalidRequest),
        };
        if password.is_empty() || password.len() > 256 || password.contains(&0) {
            return Err(AuthError::InvalidRequest);
        }
        let stored =
            shadow_hash(&self.paths.shadow, username).map_err(|_| AuthError::Unavailable)?;
        if !self.password_verifier.verify(&password, &stored) {
            return Err(AuthError::InvalidCredentials);
        }
        let session_id = random_hex(16).map_err(|_| AuthError::Unavailable)?;
        let now = (self.session_clock)();
        let session = Session {
            username: username.to_owned(),
            is_default_password: password == b"root",
            created: now,
            last_access: now,
        };
        let mut sessions = self.sessions.lock().map_err(|_| AuthError::Unavailable)?;
        if sessions.len() >= 16
            && let Some(oldest) = sessions
                .iter()
                .min_by_key(|(_, value)| (value.last_access, value.created))
                .map(|(key, _)| key.clone())
        {
            sessions.remove(&oldest);
        }
        sessions.insert(session_id.clone(), session);
        Ok(LoginResult {
            session_id,
            is_default_password: password == b"root",
        })
    }

    pub fn logout(&self, cookie: Option<&[u8]>) {
        let Some(session) = cookie.and_then(session_from_cookie) else {
            return;
        };
        if let Ok(mut sessions) = self.sessions.lock() {
            sessions.remove(session);
        }
    }

    pub fn session_status(
        &self,
        cookie: Option<&[u8]>,
        remote_address: Option<&[u8]>,
    ) -> SessionStatus {
        let remote = remote_address
            .and_then(|value| std::str::from_utf8(value).ok())
            .and_then(normalize_address);
        let client_ip = remote.map(|value| value.to_string()).unwrap_or_default();
        let session_id = cookie.and_then(session_from_cookie);
        let session = self.sessions.lock().ok().and_then(|mut sessions| {
            let session_id = session_id?;
            let value = sessions.get_mut(session_id)?;
            let now = (self.session_clock)();
            if now.saturating_duration_since(value.last_access)
                > Duration::from_secs(SESSION_IDLE_SECONDS)
                || now.saturating_duration_since(value.created)
                    > Duration::from_secs(SESSION_MAX_SECONDS)
            {
                sessions.remove(session_id);
                return None;
            }
            value.last_access = now;
            Some(value.clone())
        });
        SessionStatus {
            authenticated: session.is_some(),
            username: session.as_ref().map(|value| value.username.clone()),
            is_default_password: session
                .as_ref()
                .is_some_and(|value| value.is_default_password),
            client_ip,
        }
    }

    pub fn api_key_exists(&self) -> Result<bool, AuthError> {
        if !self.paths.api_key.exists() {
            return Ok(false);
        }
        let mut raw =
            read_regular(&self.paths.api_key, MAX_KEY_BYTES).map_err(|_| AuthError::Unavailable)?;
        raw.retain(|byte| !matches!(byte, b' ' | b'\r' | b'\n'));
        if raw.is_empty() {
            return Err(AuthError::Unavailable);
        }
        Ok(true)
    }

    pub fn generate_api_key(&self) -> Result<String, AuthError> {
        let key = random_hex(32).map_err(|_| AuthError::Unavailable)?;
        let content = format!("{key}\n");
        write_private_atomic(&self.paths.api_key, content.as_bytes(), 0o600, true)
            .map_err(|_| AuthError::Unavailable)?;
        Ok(key)
    }

    pub fn delete_api_key(&self) -> Result<(), AuthError> {
        match fs::remove_file(&self.paths.api_key) {
            Ok(()) => sync_parent(&self.paths.api_key).map_err(|_| AuthError::Unavailable),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(AuthError::Unavailable),
        }
    }

    pub fn change_password(&self, body: &[u8]) -> Result<(String, Vec<u8>), AuthError> {
        let document = json::parse(body).map_err(|_| AuthError::InvalidRequest)?;
        let password = document
            .get_path("password")
            .and_then(Value::as_str)
            .filter(|value| (10..=128).contains(&value.len()) && !value.contains('\0'))
            .ok_or(AuthError::InvalidRequest)?;
        let salt = format!("$6${}$", random_hex(8).map_err(|_| AuthError::Unavailable)?);
        let hash = self
            .password_hasher
            .hash(password.as_bytes(), &salt)
            .filter(|value| value.starts_with("$6$") && value.len() <= 256)
            .ok_or(AuthError::Unavailable)?;
        let original = read_regular(&self.paths.shadow, MAX_SHADOW_BYTES)
            .map_err(|_| AuthError::Unavailable)?;
        let text = std::str::from_utf8(&original).map_err(|_| AuthError::Unavailable)?;
        let mut found = false;
        let mut updated = String::with_capacity(text.len() + hash.len());
        for line in text.lines() {
            if line.starts_with("root:") {
                let mut fields = line.splitn(3, ':');
                let _ = fields.next();
                let _ = fields.next();
                let tail = fields.next().ok_or(AuthError::Unavailable)?;
                updated.push_str("root:");
                updated.push_str(&hash);
                updated.push(':');
                updated.push_str(tail);
                found = true;
            } else {
                updated.push_str(line);
            }
            updated.push('\n');
        }
        if !found || updated.len() as u64 > MAX_SHADOW_BYTES {
            return Err(AuthError::Unavailable);
        }
        write_existing(&self.paths.shadow, updated.as_bytes())
            .map_err(|_| AuthError::Unavailable)?;
        Ok((password.to_owned(), original))
    }

    pub fn restore_password(&self, original: &[u8]) -> Result<(), AuthError> {
        if original.is_empty() || original.len() as u64 > MAX_SHADOW_BYTES {
            return Err(AuthError::Unavailable);
        }
        write_existing(&self.paths.shadow, original).map_err(|_| AuthError::Unavailable)?;
        if read_regular(&self.paths.shadow, MAX_SHADOW_BYTES).map_err(|_| AuthError::Unavailable)?
            != original
        {
            return Err(AuthError::Unavailable);
        }
        Ok(())
    }

    pub fn invalidate_sessions(&self) {
        if let Ok(mut sessions) = self.sessions.lock() {
            sessions.clear();
        }
    }

    fn is_trusted(&self, remote: IpAddr) -> bool {
        let Ok(raw) = read_regular(&self.paths.thingino_config, MAX_CONFIG_BYTES) else {
            return false;
        };
        let Ok(document) = json::parse(&raw) else {
            return false;
        };
        if document.get_path("webui.paranoid").and_then(Value::as_bool) == Some(true) {
            return false;
        }
        let Some(value) = document.get_path("webui.auth_bypass_ips") else {
            return false;
        };
        let entries: Vec<&str> = match value {
            Value::String(value) => value
                .split(|character: char| character == ',' || character.is_ascii_whitespace())
                .filter(|entry| !entry.is_empty())
                .collect(),
            Value::Array(_) => value
                .as_array()
                .unwrap_or_default()
                .iter()
                .filter_map(Value::as_str)
                .collect(),
            _ => return false,
        };
        entries
            .into_iter()
            .any(|entry| address_matches(remote, entry))
    }
}

fn open_regular(path: &Path, limit: u64) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW)
        .open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "authentication input is not a bounded regular file",
        ));
    }
    Ok(file)
}

fn write_existing(path: &Path, content: &[u8]) -> io::Result<()> {
    write_private_atomic(path, content, 0, false)
}

fn write_private_atomic(
    path: &Path,
    content: &[u8],
    create_mode: u32,
    allow_missing: bool,
) -> io::Result<()> {
    let identity = match path.symlink_metadata() {
        Ok(metadata) if metadata.is_file() && metadata.nlink() == 1 => {
            (metadata.mode() & 0o777, metadata.uid(), metadata.gid())
        }
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "target is not a private regular file",
            ));
        }
        Err(error) if allow_missing && error.kind() == io::ErrorKind::NotFound => {
            (create_mode, unsafe { geteuid() }, unsafe { getegid() })
        }
        Err(error) => return Err(error),
    };
    let (mode, owner, group) = identity;
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "target has no parent"))?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "target name is invalid"))?;
    let temporary = parent.join(format!(
        ".{name}.thingino-auth-{}-{}.tmp",
        std::process::id(),
        random_hex(8)?
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(mode)
            .custom_flags(O_NOFOLLOW)
            .open(&temporary)?;
        // SAFETY: the descriptor belongs to the live temporary file. Preserve the exact
        // ownership and mode of an existing credential file; for a new API key, explicitly
        // enforce the requested private mode independent of the process umask.
        if unsafe { fchown(file.as_raw_fd(), owner, group) } != 0
            || unsafe { fchmod(file.as_raw_fd(), mode) } != 0
        {
            return Err(io::Error::last_os_error());
        }
        file.write_all(content)?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        if File::open(parent)
            .and_then(|directory| directory.sync_all())
            .is_err()
        {
            eprintln!("thingino-controld: credential committed but directory sync failed");
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn sync_parent(path: &Path) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "target has no parent"))?;
    File::open(parent)?.sync_all()
}

fn read_regular(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let mut file = open_regular(path, limit)?;
    read_open_regular(&mut file, limit)
}

fn read_open_regular(file: &mut File, limit: u64) -> io::Result<Vec<u8>> {
    let before = file.metadata()?;
    let mut raw = Vec::with_capacity(before.len() as usize);
    Read::by_ref(file).take(limit + 1).read_to_end(&mut raw)?;
    let after = file.metadata()?;
    if raw.len() as u64 != before.len()
        || (before.dev(), before.ino(), before.len()) != (after.dev(), after.ino(), after.len())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "authentication input changed while being read",
        ));
    }
    Ok(raw)
}

fn session_from_cookie(cookie: &[u8]) -> Option<&str> {
    let cookie = std::str::from_utf8(cookie).ok()?;
    cookie.split(';').find_map(|part| {
        let value = part.trim().strip_prefix(COOKIE_NAME)?.strip_prefix('=')?;
        Some(value.split_ascii_whitespace().next().unwrap_or(""))
    })
}

fn normalize_address(value: &str) -> Option<IpAddr> {
    let value = value.trim().trim_start_matches('[').trim_end_matches(']');
    let address = value.parse::<IpAddr>().ok()?;
    match address {
        IpAddr::V6(address) => address
            .to_ipv4_mapped()
            .map(IpAddr::V4)
            .or(Some(IpAddr::V6(address))),
        address => Some(address),
    }
}

fn address_matches(address: IpAddr, entry: &str) -> bool {
    if let Some((base, prefix)) = entry.split_once('/') {
        let Ok(base) = base.parse::<IpAddr>() else {
            return false;
        };
        let Ok(prefix) = prefix.parse::<u8>() else {
            return false;
        };
        return prefix > 0 && cidr_matches(address, base, prefix);
    }
    entry.parse::<IpAddr>().is_ok_and(|exact| address == exact)
}

fn cidr_matches(address: IpAddr, base: IpAddr, prefix: u8) -> bool {
    match (address, base) {
        (IpAddr::V4(address), IpAddr::V4(base)) if prefix <= 32 => {
            let mask = if prefix == 0 {
                0
            } else {
                u32::MAX << (32 - prefix)
            };
            u32::from(address) & mask == u32::from(base) & mask
        }
        (IpAddr::V6(address), IpAddr::V6(base)) if prefix <= 128 => {
            let mask = if prefix == 0 {
                0
            } else {
                u128::MAX << (128 - prefix)
            };
            u128::from(address) & mask == u128::from(base) & mask
        }
        _ => false,
    }
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    for index in 0..left.len().max(right.len()) {
        difference |= usize::from(
            left.get(index).copied().unwrap_or(0) ^ right.get(index).copied().unwrap_or(0),
        );
    }
    difference == 0
}

#[cfg(test)]
#[path = "web_auth_tests.rs"]
mod tests;
