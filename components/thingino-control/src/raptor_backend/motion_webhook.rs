use std::ffi::{CString, c_char, c_int, c_long, c_void};
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::{self, JoinHandle};

use crate::camera::motion_events::{
    MotionEvent, MotionEventDisposition, MotionEventSink, MotionState,
};
use crate::json::Value;
use crate::{BackendError, BackendResponse};

use super::RaptorBackend;

const QUEUE_CAPACITY: usize = 2;
const MAX_BODY_BYTES: usize = 512;
const MAX_RESPONSE_BYTES: usize = 4_096;
const CA_BUNDLE: &str = "/etc/ssl/certs/ca-certificates.crt";

const RTLD_NOW: c_int = 2;
const CURLE_OK: c_int = 0;
const CURL_GLOBAL_DEFAULT: c_long = 3;
const CURLOPT_WRITEDATA: c_int = 10_001;
const CURLOPT_URL: c_int = 10_002;
const CURLOPT_PROXY: c_int = 10_004;
const CURLOPT_READDATA: c_int = 10_009;
const CURLOPT_WRITEFUNCTION: c_int = 20_011;
const CURLOPT_READFUNCTION: c_int = 20_012;
const CURLOPT_POSTFIELDS: c_int = 10_015;
const CURLOPT_HTTPHEADER: c_int = 10_023;
const CURLOPT_POST: c_int = 47;
const CURLOPT_NOPROGRESS: c_int = 43;
const CURLOPT_UPLOAD: c_int = 46;
const CURLOPT_FOLLOWLOCATION: c_int = 52;
const CURLOPT_POSTFIELDSIZE: c_int = 60;
const CURLOPT_SSL_VERIFYPEER: c_int = 64;
const CURLOPT_CAINFO: c_int = 10_065;
const CURLOPT_SSL_VERIFYHOST: c_int = 81;
const CURLOPT_NOSIGNAL: c_int = 99;
const CURLOPT_TIMEOUT_MS: c_int = 155;
const CURLOPT_CONNECTTIMEOUT_MS: c_int = 156;
const CURLOPT_PROTOCOLS: c_int = 181;
const CURLOPT_REDIR_PROTOCOLS: c_int = 182;
const CURLOPT_USE_SSL: c_int = 119;
const CURLOPT_FTPSSLAUTH: c_int = 129;
const CURLOPT_FTP_SKIP_PASV_IP: c_int = 137;
const CURLOPT_INFILESIZE_LARGE: c_int = 30_115;
const CURLOPT_USERNAME: c_int = 10_173;
const CURLOPT_PASSWORD: c_int = 10_174;
const CURLOPT_MAIL_FROM: c_int = 10_186;
const CURLOPT_MAIL_RCPT: c_int = 10_187;
const CURLOPT_XFERINFODATA: c_int = 10_057;
const CURLOPT_XFERINFOFUNCTION: c_int = 20_219;
const CURLPROTO_HTTP_HTTPS: c_long = 3;
const CURLPROTO_FTP: c_long = 1 << 2;
const CURLPROTO_SMTP_SMTPS: c_long = (1 << 16) | (1 << 17);
const CURLUSESSL_ALL: c_long = 3;
const CURLFTPAUTH_TLS: c_long = 2;
const CURLE_ABORTED_BY_CALLBACK: c_int = 42;
const CURLINFO_RESPONSE_CODE: c_int = 0x20_0002;
static CURL_API: OnceLock<Result<Arc<CurlApi>, ()>> = OnceLock::new();

#[cfg(target_os = "linux")]
#[link(name = "dl")]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
}

type Curl = c_void;
type CurlSlist = c_void;
type WriteCallback = unsafe extern "C" fn(*mut c_char, usize, usize, *mut c_void) -> usize;
type ReadCallback = unsafe extern "C" fn(*mut c_char, usize, usize, *mut c_void) -> usize;
type ProgressCallback = unsafe extern "C" fn(*mut c_void, i64, i64, i64, i64) -> c_int;
type EasySetopt = unsafe extern "C" fn(*mut Curl, c_int, ...) -> c_int;

#[repr(C)]
struct CurlVersionInfo {
    age: c_int,
    version: *const c_char,
    version_num: u32,
    host: *const c_char,
    features: c_int,
    ssl_version: *const c_char,
    ssl_version_num: c_long,
    libz_version: *const c_char,
    protocols: *const *const c_char,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(super) struct Config {
    pub(super) enabled: bool,
    pub(super) url: String,
}

impl Config {
    fn from_value(value: &Value) -> Result<Self, BackendError> {
        Ok(Self {
            enabled: value
                .get_path("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            url: value
                .get_path("url")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ResultClass {
    Idle = 0,
    Success = 1,
    HttpError = 2,
    TransportError = 3,
}

impl ResultClass {
    fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Success => "success",
            Self::HttpError => "http_error",
            Self::TransportError => "transport_error",
        }
    }
}

#[derive(Default)]
struct Runtime {
    running: AtomicBool,
    enabled: AtomicBool,
    queued: AtomicU32,
    queue_dropped: AtomicU32,
    requests: AtomicU32,
    successes: AtomicU32,
    failures: AtomicU32,
    last_result: AtomicU32,
    last_http_status: AtomicU32,
    last_sequence: AtomicU32,
}

pub(super) trait Transport: Send + Sync {
    fn available(&self) -> bool;
    fn post(&self, url: &str, body: &[u8]) -> Result<u16, ()>;
}

struct QueuedEvent {
    event: MotionEvent,
    generation: u32,
}

struct LiveConfig {
    value: Config,
    generation: u32,
}

pub(super) struct Service {
    sender: SyncSender<QueuedEvent>,
    receiver: Mutex<Option<Receiver<QueuedEvent>>>,
    worker: Mutex<Option<JoinHandle<()>>>,
    config: Arc<Mutex<LiveConfig>>,
    generation: Arc<AtomicU32>,
    runtime: Arc<Runtime>,
    transport: Arc<dyn Transport>,
    shutdown: Arc<AtomicBool>,
}

impl Service {
    pub(super) fn new() -> Self {
        Self::with_transport(Arc::new(CurlTransport::load()))
    }

    pub(super) fn with_transport(transport: Arc<dyn Transport>) -> Self {
        let (sender, receiver) = sync_channel(QUEUE_CAPACITY);
        Self {
            sender,
            receiver: Mutex::new(Some(receiver)),
            worker: Mutex::new(None),
            config: Arc::new(Mutex::new(LiveConfig {
                value: Config::default(),
                generation: 0,
            })),
            generation: Arc::new(AtomicU32::new(0)),
            runtime: Arc::new(Runtime::default()),
            transport,
            shutdown: Arc::new(AtomicBool::new(false)),
        }
    }

    pub(super) fn start(&self) -> Result<(), BackendError> {
        let mut worker = self.worker.lock().map_err(|_| BackendError::Unavailable)?;
        if worker.is_some() {
            return Ok(());
        }
        let receiver = self
            .receiver
            .lock()
            .map_err(|_| BackendError::Unavailable)?
            .take()
            .ok_or(BackendError::Unavailable)?;
        let config = self.config.clone();
        let worker_runtime = self.runtime.clone();
        let transport = self.transport.clone();
        let shutdown = self.shutdown.clone();
        let handle = thread::Builder::new()
            .name("control-motion-webhook".to_owned())
            .spawn(move || worker_loop(receiver, config, worker_runtime, transport, shutdown))
            .map_err(|_| BackendError::Unavailable)?;
        self.runtime.running.store(true, Ordering::Release);
        *worker = Some(handle);
        Ok(())
    }

    pub(super) fn apply(&self, config: Config) -> Result<(), BackendError> {
        if config.enabled && (!self.transport.available() || config.url.is_empty()) {
            return Err(BackendError::Unavailable);
        }
        self.runtime.enabled.store(false, Ordering::Release);
        let enabled = config.enabled;
        let mut live = self.config.lock().map_err(|_| BackendError::Unavailable)?;
        if live.value != config {
            let generation = self
                .generation
                .fetch_add(1, Ordering::AcqRel)
                .wrapping_add(1);
            live.value = config;
            live.generation = generation;
        }
        drop(live);
        self.runtime.enabled.store(enabled, Ordering::Release);
        Ok(())
    }

    fn public_config(&self, saved: &Config) -> Result<BackendResponse, BackendError> {
        let saved_enabled = saved.enabled;
        let saved_url_set = !saved.url.is_empty();
        let live = self
            .config
            .lock()
            .map_err(|_| BackendError::Unavailable)?
            .value
            .clone();
        let body = format!(
            "{{\"saved_enabled\":{saved_enabled},\"enabled\":{},\"url\":null,\"url_set\":{saved_url_set},\"live_url_set\":{},\"matches_saved\":{},\"transport_available\":{}}}\n",
            live.enabled,
            !live.url.is_empty(),
            saved == &live,
            self.transport.available()
        );
        Ok(BackendResponse::json(body.into_bytes()))
    }

    fn runtime(&self) -> BackendResponse {
        let class = match self.runtime.last_result.load(Ordering::Relaxed) {
            1 => ResultClass::Success,
            2 => ResultClass::HttpError,
            3 => ResultClass::TransportError,
            _ => ResultClass::Idle,
        };
        let status = self.runtime.last_http_status.load(Ordering::Relaxed);
        let sequence = self.runtime.last_sequence.load(Ordering::Relaxed);
        BackendResponse::json(format!(
            "{{\"running\":{},\"enabled\":{},\"transport_available\":{},\"queue_depth\":{},\"queue_dropped\":{},\"requests\":{},\"successes\":{},\"failures\":{},\"last_result\":\"{}\",\"last_http_status\":{},\"last_event_sequence\":{}}}\n",
            self.runtime.running.load(Ordering::Acquire),
            self.runtime.enabled.load(Ordering::Acquire),
            self.transport.available(),
            self.runtime.queued.load(Ordering::Relaxed),
            self.runtime.queue_dropped.load(Ordering::Relaxed),
            self.runtime.requests.load(Ordering::Relaxed),
            self.runtime.successes.load(Ordering::Relaxed),
            self.runtime.failures.load(Ordering::Relaxed),
            class.as_str(),
            if status == 0 { "null".to_owned() } else { status.to_string() },
            if sequence == 0 { "null".to_owned() } else { sequence.to_string() },
        ).into_bytes())
    }
}

impl MotionEventSink for Service {
    fn try_send_motion(&self, event: MotionEvent) -> MotionEventDisposition {
        if event.state != MotionState::Active || !self.runtime.enabled.load(Ordering::Acquire) {
            return MotionEventDisposition::Disabled;
        }
        if !event.has_bounded_metadata() {
            self.runtime.queue_dropped.fetch_add(1, Ordering::Relaxed);
            return MotionEventDisposition::Dropped;
        }
        self.runtime.queued.fetch_add(1, Ordering::Relaxed);
        let event = QueuedEvent {
            event,
            generation: self.generation.load(Ordering::Acquire),
        };
        match self.sender.try_send(event) {
            Ok(()) => MotionEventDisposition::Queued,
            Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {
                self.runtime.queued.fetch_sub(1, Ordering::Relaxed);
                self.runtime.queue_dropped.fetch_add(1, Ordering::Relaxed);
                MotionEventDisposition::Dropped
            }
        }
    }
}

impl Drop for Service {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::Release);
        if let Ok(worker) = self.worker.get_mut()
            && let Some(worker) = worker.take()
        {
            let _ = worker.join();
        }
    }
}

fn worker_loop(
    receiver: Receiver<QueuedEvent>,
    config: Arc<Mutex<LiveConfig>>,
    runtime: Arc<Runtime>,
    transport: Arc<dyn Transport>,
    shutdown: Arc<AtomicBool>,
) {
    runtime.running.store(true, Ordering::Release);
    while !shutdown.load(Ordering::Acquire) {
        let Ok(event) = receiver.recv_timeout(std::time::Duration::from_millis(100)) else {
            continue;
        };
        if shutdown.load(Ordering::Acquire) {
            break;
        }
        runtime.queued.fetch_sub(1, Ordering::Relaxed);
        let config = match config.lock() {
            Ok(config) if config.generation == event.generation => config.value.clone(),
            Ok(_) => {
                runtime.queue_dropped.fetch_add(1, Ordering::Relaxed);
                continue;
            }
            Err(_) => break,
        };
        if !config.enabled {
            continue;
        }
        let body = event_body(&event.event);
        runtime.requests.fetch_add(1, Ordering::Relaxed);
        runtime
            .last_sequence
            .store(event.event.sequence as u32, Ordering::Relaxed);
        match transport.post(&config.url, body.as_bytes()) {
            Ok(status) if (200..300).contains(&status) => {
                runtime.successes.fetch_add(1, Ordering::Relaxed);
                runtime
                    .last_result
                    .store(ResultClass::Success as u32, Ordering::Relaxed);
                runtime
                    .last_http_status
                    .store(u32::from(status), Ordering::Relaxed);
            }
            Ok(status) => {
                runtime.failures.fetch_add(1, Ordering::Relaxed);
                runtime
                    .last_result
                    .store(ResultClass::HttpError as u32, Ordering::Relaxed);
                runtime
                    .last_http_status
                    .store(u32::from(status), Ordering::Relaxed);
            }
            Err(()) => {
                runtime.failures.fetch_add(1, Ordering::Relaxed);
                runtime
                    .last_result
                    .store(ResultClass::TransportError as u32, Ordering::Relaxed);
                runtime.last_http_status.store(0, Ordering::Relaxed);
            }
        }
    }
    runtime.running.store(false, Ordering::Release);
}

fn event_body(event: &MotionEvent) -> String {
    let occurred = event
        .occurred_unix_ms
        .map(|value| value.to_string())
        .unwrap_or_else(|| "null".to_owned());
    let body = format!(
        "{{\"schema\":\"thingino.motion.webhook.v1\",\"event\":\"motion\",\"state\":\"active\",\"sequence\":{},\"channel\":{},\"monotonic_ms\":{},\"occurred_unix_ms\":{occurred}}}",
        event.sequence, event.channel, event.monotonic_ms
    );
    debug_assert!(body.len() <= MAX_BODY_BYTES);
    body
}

struct CurlApi {
    easy_init: unsafe extern "C" fn() -> *mut Curl,
    easy_cleanup: unsafe extern "C" fn(*mut Curl),
    easy_setopt: EasySetopt,
    easy_perform: unsafe extern "C" fn(*mut Curl) -> c_int,
    easy_getinfo: unsafe extern "C" fn(*mut Curl, c_int, ...) -> c_int,
    slist_append: unsafe extern "C" fn(*mut CurlSlist, *const c_char) -> *mut CurlSlist,
    slist_free_all: unsafe extern "C" fn(*mut CurlSlist),
    version_info: unsafe extern "C" fn(c_int) -> *mut CurlVersionInfo,
}

unsafe impl Send for CurlApi {}
unsafe impl Sync for CurlApi {}

pub(super) struct CurlTransport {
    api: Option<Arc<CurlApi>>,
}

impl CurlTransport {
    pub(super) fn load() -> Self {
        Self {
            api: CURL_API
                .get_or_init(|| load_curl().map(Arc::new))
                .clone()
                .ok(),
        }
    }

    pub(super) fn supports_protocols(&self, required: &[&str]) -> bool {
        let Some(api) = self.api.as_ref() else {
            return false;
        };
        // CURLVERSION_FIRST requests the stable prefix containing protocols.
        let info = unsafe { (api.version_info)(0) };
        if info.is_null() {
            return false;
        }
        let mut protocols = unsafe { (*info).protocols };
        if protocols.is_null() {
            return false;
        }
        let mut found = vec![false; required.len()];
        loop {
            let protocol = unsafe { *protocols };
            if protocol.is_null() {
                break;
            }
            if let Ok(protocol) = unsafe { std::ffi::CStr::from_ptr(protocol) }.to_str() {
                for (index, required) in required.iter().enumerate() {
                    found[index] |= protocol.eq_ignore_ascii_case(required);
                }
            }
            protocols = unsafe { protocols.add(1) };
        }
        found.into_iter().all(|value| value)
    }

    pub(super) fn supports_verified_tls(&self) -> bool {
        let Some(api) = self.api.as_ref() else {
            return false;
        };
        let info = unsafe { (api.version_info)(0) };
        if info.is_null() || unsafe { (*info).ssl_version }.is_null() {
            return false;
        }
        let has_tls = !unsafe { std::ffi::CStr::from_ptr((*info).ssl_version) }
            .to_bytes()
            .is_empty();
        has_tls && (!cfg!(target_os = "linux") || std::path::Path::new(CA_BUNDLE).is_file())
    }

    pub(super) fn upload_explicit_ftps(&self, request: &FtpRequest<'_>) -> FtpResult {
        self.upload_explicit_ftps_inner(request, None)
    }

    #[cfg(test)]
    pub(super) fn upload_explicit_ftps_with_ca(
        &self,
        request: &FtpRequest<'_>,
        ca_override: Option<&std::path::Path>,
    ) -> FtpResult {
        self.upload_explicit_ftps_inner(request, ca_override)
    }

    fn upload_explicit_ftps_inner(
        &self,
        request: &FtpRequest<'_>,
        ca_override: Option<&std::path::Path>,
    ) -> FtpResult {
        let Some(api) = self.api.as_ref() else {
            return FtpResult::Failed;
        };
        if !self.supports_protocols(&["ftp"])
            || (!self.supports_verified_tls() && ca_override.is_none())
        {
            return FtpResult::Failed;
        }
        let Ok(url) = CString::new(request.url) else {
            return FtpResult::Failed;
        };
        let Ok(username) = CString::new(request.username) else {
            return FtpResult::Failed;
        };
        let Ok(password) = CString::new(request.password) else {
            return FtpResult::Failed;
        };
        let proxy = CString::new("").unwrap();
        let ca = match ca_override
            .map(|path| CString::new(path.as_os_str().as_encoded_bytes()))
            .transpose()
        {
            Ok(Some(ca)) => ca,
            Ok(None) => CString::new(CA_BUNDLE).unwrap(),
            Err(_) => return FtpResult::Failed,
        };
        let mut upload = UploadBuffer {
            bytes: request.jpeg,
            offset: 0,
        };
        let mut progress = request.cancel;
        unsafe {
            let easy = (api.easy_init)();
            if easy.is_null() {
                return FtpResult::Failed;
            }
            macro_rules! option {
                ($name:expr, $value:expr) => {
                    if (api.easy_setopt)(easy, $name, $value) != CURLE_OK {
                        (api.easy_cleanup)(easy);
                        return FtpResult::Failed;
                    }
                };
            }
            option!(CURLOPT_URL, url.as_ptr());
            option!(CURLOPT_PROXY, proxy.as_ptr());
            option!(CURLOPT_PROTOCOLS, CURLPROTO_FTP);
            option!(CURLOPT_REDIR_PROTOCOLS, 0 as c_long);
            option!(CURLOPT_FOLLOWLOCATION, 0 as c_long);
            option!(CURLOPT_USE_SSL, CURLUSESSL_ALL);
            option!(CURLOPT_FTPSSLAUTH, CURLFTPAUTH_TLS);
            option!(CURLOPT_FTP_SKIP_PASV_IP, 1 as c_long);
            option!(CURLOPT_USERNAME, username.as_ptr());
            option!(CURLOPT_PASSWORD, password.as_ptr());
            option!(CURLOPT_UPLOAD, 1 as c_long);
            option!(CURLOPT_INFILESIZE_LARGE, request.jpeg.len() as i64);
            option!(CURLOPT_READFUNCTION, read_upload as ReadCallback);
            option!(
                CURLOPT_READDATA,
                (&mut upload as *mut UploadBuffer<'_>).cast::<c_void>()
            );
            option!(CURLOPT_NOPROGRESS, 0 as c_long);
            option!(
                CURLOPT_XFERINFOFUNCTION,
                cancel_transfer as ProgressCallback
            );
            option!(
                CURLOPT_XFERINFODATA,
                (&mut progress as *mut TransferCancel<'_>).cast::<c_void>()
            );
            option!(CURLOPT_CONNECTTIMEOUT_MS, 500 as c_long);
            option!(CURLOPT_TIMEOUT_MS, 5_000 as c_long);
            option!(CURLOPT_NOSIGNAL, 1 as c_long);
            option!(CURLOPT_SSL_VERIFYPEER, 1 as c_long);
            option!(CURLOPT_SSL_VERIFYHOST, 2 as c_long);
            if ca_override.is_some() || cfg!(target_os = "linux") {
                option!(CURLOPT_CAINFO, ca.as_ptr());
            }
            let result = (api.easy_perform)(easy);
            let mut status: c_long = 0;
            let info = (api.easy_getinfo)(easy, CURLINFO_RESPONSE_CODE, &mut status);
            (api.easy_cleanup)(easy);
            if result == CURLE_ABORTED_BY_CALLBACK {
                return FtpResult::Cancelled;
            }
            if result != CURLE_OK || info != CURLE_OK {
                return FtpResult::Failed;
            }
            match u16::try_from(status) {
                Ok(status @ 200..=299) => FtpResult::Stored(status),
                _ => FtpResult::Failed,
            }
        }
    }

    pub(super) fn send_smtp(&self, request: &SmtpRequest<'_>) -> Result<u16, ()> {
        self.send_smtp_inner(request, None)
    }

    #[cfg(test)]
    pub(super) fn send_smtp_with_ca(
        &self,
        request: &SmtpRequest<'_>,
        ca_override: Option<&std::path::Path>,
    ) -> Result<u16, ()> {
        self.send_smtp_inner(request, ca_override)
    }

    fn send_smtp_inner(
        &self,
        request: &SmtpRequest<'_>,
        ca_override: Option<&std::path::Path>,
    ) -> Result<u16, ()> {
        let api = self.api.as_ref().ok_or(())?;
        if !self.supports_protocols(&["smtp", "smtps"])
            || (ca_override.is_none()
                && !std::path::Path::new(CA_BUNDLE).is_file()
                && cfg!(target_os = "linux"))
        {
            return Err(());
        }
        let url = CString::new(request.url).map_err(|_| ())?;
        let proxy = CString::new("").unwrap();
        let ca = ca_override
            .map(|path| CString::new(path.as_os_str().as_encoded_bytes()).map_err(|_| ()))
            .transpose()?
            .unwrap_or_else(|| CString::new(CA_BUNDLE).unwrap());
        let username = CString::new(request.username).map_err(|_| ())?;
        let password = CString::new(request.password).map_err(|_| ())?;
        let from = CString::new(request.from).map_err(|_| ())?;
        let to = CString::new(request.to).map_err(|_| ())?;
        let mut upload = UploadBuffer {
            bytes: request.message,
            offset: 0,
        };
        // SAFETY: all functions and option types follow libcurl's stable easy ABI.
        unsafe {
            let easy = (api.easy_init)();
            if easy.is_null() {
                return Err(());
            }
            let recipients = (api.slist_append)(ptr::null_mut(), to.as_ptr());
            if recipients.is_null() {
                (api.easy_cleanup)(easy);
                return Err(());
            }
            macro_rules! option {
                ($name:expr, $value:expr) => {
                    if (api.easy_setopt)(easy, $name, $value) != CURLE_OK {
                        (api.slist_free_all)(recipients);
                        (api.easy_cleanup)(easy);
                        return Err(());
                    }
                };
            }
            option!(CURLOPT_URL, url.as_ptr());
            option!(CURLOPT_PROXY, proxy.as_ptr());
            option!(CURLOPT_PROTOCOLS, CURLPROTO_SMTP_SMTPS);
            option!(CURLOPT_REDIR_PROTOCOLS, 0 as c_long);
            option!(CURLOPT_FOLLOWLOCATION, 0 as c_long);
            option!(CURLOPT_USE_SSL, CURLUSESSL_ALL);
            option!(CURLOPT_UPLOAD, 1 as c_long);
            option!(CURLOPT_MAIL_FROM, from.as_ptr());
            option!(CURLOPT_MAIL_RCPT, recipients);
            if !username.is_empty() {
                option!(CURLOPT_USERNAME, username.as_ptr());
                option!(CURLOPT_PASSWORD, password.as_ptr());
            }
            option!(CURLOPT_INFILESIZE_LARGE, request.message.len() as i64);
            option!(CURLOPT_READFUNCTION, read_upload as ReadCallback);
            option!(
                CURLOPT_READDATA,
                (&mut upload as *mut UploadBuffer<'_>).cast::<c_void>()
            );
            option!(CURLOPT_CONNECTTIMEOUT_MS, 500 as c_long);
            option!(CURLOPT_TIMEOUT_MS, 5_000 as c_long);
            option!(CURLOPT_NOSIGNAL, 1 as c_long);
            option!(CURLOPT_SSL_VERIFYPEER, 1 as c_long);
            option!(CURLOPT_SSL_VERIFYHOST, 2 as c_long);
            if ca_override.is_some() || cfg!(target_os = "linux") {
                option!(CURLOPT_CAINFO, ca.as_ptr());
            }
            let result = (api.easy_perform)(easy);
            let mut status: c_long = 0;
            let info = (api.easy_getinfo)(easy, CURLINFO_RESPONSE_CODE, &mut status);
            (api.slist_free_all)(recipients);
            (api.easy_cleanup)(easy);
            if info != CURLE_OK {
                return Err(());
            }
            let status = u16::try_from(status).map_err(|_| ())?;
            if result != CURLE_OK && !(400..=599).contains(&status) {
                return Err(());
            }
            Ok(status)
        }
    }

    pub(super) fn post_with_headers(
        &self,
        url: &str,
        body: &[u8],
        headers: &[&str],
    ) -> Result<u16, ()> {
        self.post_with_headers_and_response(url, body, headers)
            .map(|(status, _)| status)
    }

    pub(super) fn post_with_headers_and_response(
        &self,
        url: &str,
        body: &[u8],
        headers: &[&str],
    ) -> Result<(u16, Vec<u8>), ()> {
        let api = self.api.as_ref().ok_or(())?;
        let https = url.starts_with("https://");
        if https && !std::path::Path::new(CA_BUNDLE).is_file() && cfg!(target_os = "linux") {
            return Err(());
        }
        let url = CString::new(url).map_err(|_| ())?;
        let proxy = CString::new("").unwrap();
        let ca = CString::new(CA_BUNDLE).unwrap();
        let headers = headers
            .iter()
            .map(|header| CString::new(*header).map_err(|_| ()))
            .collect::<Result<Vec<_>, _>>()?;
        // SAFETY: all functions and option types follow libcurl's stable easy ABI.
        unsafe {
            let easy = (api.easy_init)();
            if easy.is_null() {
                return Err(());
            }
            let mut header_list = ptr::null_mut();
            for header in &headers {
                let next = (api.slist_append)(header_list, header.as_ptr());
                if next.is_null() {
                    (api.slist_free_all)(header_list);
                    (api.easy_cleanup)(easy);
                    return Err(());
                }
                header_list = next;
            }
            let mut response = ResponseBuffer { bytes: Vec::new() };
            macro_rules! option {
                ($name:expr, $value:expr) => {
                    if (api.easy_setopt)(easy, $name, $value) != CURLE_OK {
                        (api.slist_free_all)(header_list);
                        (api.easy_cleanup)(easy);
                        return Err(());
                    }
                };
            }
            option!(CURLOPT_URL, url.as_ptr());
            option!(CURLOPT_PROXY, proxy.as_ptr());
            option!(CURLOPT_PROTOCOLS, CURLPROTO_HTTP_HTTPS);
            option!(CURLOPT_REDIR_PROTOCOLS, 0 as c_long);
            option!(CURLOPT_FOLLOWLOCATION, 0 as c_long);
            option!(CURLOPT_POST, 1 as c_long);
            option!(CURLOPT_POSTFIELDS, body.as_ptr());
            option!(CURLOPT_POSTFIELDSIZE, body.len() as c_long);
            option!(CURLOPT_HTTPHEADER, header_list);
            option!(CURLOPT_CONNECTTIMEOUT_MS, 500 as c_long);
            option!(CURLOPT_TIMEOUT_MS, 2_000 as c_long);
            option!(CURLOPT_NOSIGNAL, 1 as c_long);
            option!(CURLOPT_SSL_VERIFYPEER, 1 as c_long);
            option!(CURLOPT_SSL_VERIFYHOST, 2 as c_long);
            if https && cfg!(target_os = "linux") {
                option!(CURLOPT_CAINFO, ca.as_ptr());
            }
            option!(CURLOPT_WRITEFUNCTION, count_response as WriteCallback);
            option!(
                CURLOPT_WRITEDATA,
                (&mut response as *mut ResponseBuffer).cast::<c_void>()
            );
            let result = (api.easy_perform)(easy);
            let mut status: c_long = 0;
            let info = (api.easy_getinfo)(easy, CURLINFO_RESPONSE_CODE, &mut status);
            (api.slist_free_all)(header_list);
            (api.easy_cleanup)(easy);
            if result != CURLE_OK || info != CURLE_OK {
                return Err(());
            }
            Ok((u16::try_from(status).map_err(|_| ())?, response.bytes))
        }
    }
}

pub(super) struct SmtpRequest<'a> {
    pub url: &'a str,
    pub username: &'a str,
    pub password: &'a str,
    pub from: &'a str,
    pub to: &'a str,
    pub message: &'a [u8],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum FtpResult {
    Stored(u16),
    Cancelled,
    Failed,
}

#[derive(Clone, Copy)]
pub(super) struct TransferCancel<'a> {
    pub config_generation: &'a AtomicU32,
    pub expected_config_generation: u32,
    pub privacy_generation: &'a AtomicU32,
    pub expected_privacy_generation: u32,
}

impl TransferCancel<'_> {
    pub fn cancelled(self) -> bool {
        self.config_generation.load(Ordering::Acquire) != self.expected_config_generation
            || self.privacy_generation.load(Ordering::Acquire) != self.expected_privacy_generation
    }
}

pub(super) struct FtpRequest<'a> {
    pub url: &'a str,
    pub username: &'a str,
    pub password: &'a str,
    pub jpeg: &'a [u8],
    pub cancel: TransferCancel<'a>,
}

struct UploadBuffer<'a> {
    bytes: &'a [u8],
    offset: usize,
}

unsafe extern "C" fn read_upload(
    target: *mut c_char,
    size: usize,
    count: usize,
    user: *mut c_void,
) -> usize {
    let Some(capacity) = size.checked_mul(count) else {
        return 0;
    };
    if capacity == 0 || user.is_null() || target.is_null() {
        return 0;
    }
    let state = unsafe { &mut *(user.cast::<UploadBuffer<'_>>()) };
    let remaining = &state.bytes[state.offset..];
    let copied = remaining.len().min(capacity);
    unsafe { ptr::copy_nonoverlapping(remaining.as_ptr(), target.cast::<u8>(), copied) };
    state.offset += copied;
    copied
}

unsafe extern "C" fn cancel_transfer(
    user: *mut c_void,
    _download_total: i64,
    _download_now: i64,
    _upload_total: i64,
    _upload_now: i64,
) -> c_int {
    if user.is_null() {
        return 1;
    }
    let cancel = unsafe { *user.cast::<TransferCancel<'_>>() };
    c_int::from(cancel.cancelled())
}

#[cfg(test)]
mod upload_callback_tests {
    use super::*;

    #[test]
    fn upload_callback_rejects_invalid_pointers_sizes_and_overflow() {
        let source = b"motion";
        let mut state = UploadBuffer {
            bytes: source,
            offset: 0,
        };
        let mut target = [0_u8; 8];
        let state_ptr = (&mut state as *mut UploadBuffer<'_>).cast::<c_void>();
        assert_eq!(unsafe { read_upload(ptr::null_mut(), 0, 1, state_ptr) }, 0);
        assert_eq!(
            unsafe { read_upload(target.as_mut_ptr().cast(), 1, 1, ptr::null_mut()) },
            0
        );
        assert_eq!(unsafe { read_upload(ptr::null_mut(), 1, 1, state_ptr) }, 0);
        assert_eq!(
            unsafe { read_upload(target.as_mut_ptr().cast(), usize::MAX, 2, state_ptr) },
            0
        );
        assert_eq!(state.offset, 0);
    }

    #[test]
    fn upload_callback_copies_only_available_bounded_bytes() {
        let mut state = UploadBuffer {
            bytes: b"motion",
            offset: 0,
        };
        let mut target = [0_u8; 8];
        let state_ptr = (&mut state as *mut UploadBuffer<'_>).cast::<c_void>();
        assert_eq!(
            unsafe { read_upload(target.as_mut_ptr().cast(), 1, 3, state_ptr) },
            3
        );
        assert_eq!(&target[..3], b"mot");
        assert_eq!(
            unsafe { read_upload(target.as_mut_ptr().cast(), 2, 4, state_ptr) },
            3
        );
        assert_eq!(&target[..3], b"ion");
        assert_eq!(
            unsafe { read_upload(target.as_mut_ptr().cast(), 1, 8, state_ptr) },
            0
        );
    }
}

struct ResponseBuffer {
    bytes: Vec<u8>,
}

unsafe extern "C" fn count_response(
    data: *mut c_char,
    size: usize,
    count: usize,
    user: *mut c_void,
) -> usize {
    let Some(bytes) = size.checked_mul(count) else {
        return 0;
    };
    if bytes == 0 || data.is_null() || user.is_null() {
        return 0;
    }
    // SAFETY: the pointer is installed for this synchronous transfer only.
    let state = unsafe { &mut *(user.cast::<ResponseBuffer>()) };
    let Some(total) = state.bytes.len().checked_add(bytes) else {
        return 0;
    };
    if total > MAX_RESPONSE_BYTES {
        return 0;
    }
    // SAFETY: libcurl provides `bytes` readable bytes for this callback.
    let data = unsafe { std::slice::from_raw_parts(data.cast::<u8>(), bytes) };
    state.bytes.extend_from_slice(data);
    bytes
}

impl Transport for CurlTransport {
    fn available(&self) -> bool {
        self.api.is_some()
            && (!cfg!(target_os = "linux") || std::path::Path::new(CA_BUNDLE).is_file())
    }

    fn post(&self, url: &str, body: &[u8]) -> Result<u16, ()> {
        self.post_with_headers(url, body, &["Content-Type: application/json"])
    }
}

fn load_curl() -> Result<CurlApi, ()> {
    let mut handle = ptr::null_mut();
    for candidate in library_candidates() {
        let candidate = CString::new(*candidate).map_err(|_| ())?;
        // SAFETY: candidate is NUL-terminated and RTLD_NOW is a valid flag.
        handle = unsafe { dlopen(candidate.as_ptr(), RTLD_NOW) };
        if !handle.is_null() {
            break;
        }
    }
    if handle.is_null() {
        return Err(());
    }
    macro_rules! symbol {
        ($name:literal, $kind:ty) => {{
            let name = CString::new($name).unwrap();
            // SAFETY: the symbol is checked before conversion to its documented ABI.
            let raw = unsafe { dlsym(handle, name.as_ptr()) };
            if raw.is_null() {
                return Err(());
            }
            // SAFETY: libcurl's public ABI fixes this symbol signature.
            unsafe { std::mem::transmute::<*mut c_void, $kind>(raw) }
        }};
    }
    let global_init = symbol!("curl_global_init", unsafe extern "C" fn(c_long) -> c_int);
    // SAFETY: initialization is idempotent for the process and no cleanup races are introduced.
    if unsafe { global_init(CURL_GLOBAL_DEFAULT) } != CURLE_OK {
        return Err(());
    }
    Ok(CurlApi {
        easy_init: symbol!("curl_easy_init", unsafe extern "C" fn() -> *mut Curl),
        easy_cleanup: symbol!("curl_easy_cleanup", unsafe extern "C" fn(*mut Curl)),
        easy_setopt: symbol!("curl_easy_setopt", EasySetopt),
        easy_perform: symbol!(
            "curl_easy_perform",
            unsafe extern "C" fn(*mut Curl) -> c_int
        ),
        easy_getinfo: symbol!(
            "curl_easy_getinfo",
            unsafe extern "C" fn(*mut Curl, c_int, ...) -> c_int
        ),
        slist_append: symbol!(
            "curl_slist_append",
            unsafe extern "C" fn(*mut CurlSlist, *const c_char) -> *mut CurlSlist
        ),
        slist_free_all: symbol!("curl_slist_free_all", unsafe extern "C" fn(*mut CurlSlist)),
        version_info: symbol!(
            "curl_version_info",
            unsafe extern "C" fn(c_int) -> *mut CurlVersionInfo
        ),
    })
}

#[cfg(target_os = "linux")]
fn library_candidates() -> &'static [&'static str] {
    &["libcurl.so.4"]
}

#[cfg(target_os = "macos")]
fn library_candidates() -> &'static [&'static str] {
    &["libcurl.dylib", "/usr/lib/libcurl.dylib"]
}

impl RaptorBackend {
    pub(super) fn motion_webhook_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Option<Result<BackendResponse, BackendError>> {
        if !matches!(
            target,
            "/api/v1/config/motion-webhook" | "/api/v1/runtime/motion-webhook"
        ) {
            return None;
        }
        Some((|| match target {
            "/api/v1/config/motion-webhook" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let saved = Config::from_value(&self.host.motion_webhook_config()?)?;
                self.motion_webhook.public_config(&saved)
            }
            "/api/v1/config/motion-webhook" if method == "POST" && !body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let _mutation = self.lock_mutation()?;
                self.host.motion_webhook_config_request("POST", body)?;
                let config = Config::from_value(&self.host.motion_webhook_config()?)?;
                let enabled = config.enabled;
                self.motion_webhook.apply(config).map_err(|_| BackendError::PartialApply(
                    "Webhook settings were saved but could not be applied. Reload the saved and live status before retrying.",
                ))?;
                if enabled && !self.motion_webhook.runtime.running.load(Ordering::Acquire) {
                    return Err(BackendError::PartialApply(
                        "Webhook settings were saved but its delivery worker is not running. Reload the saved and live status before retrying.",
                    ));
                }
                Ok(BackendResponse::json(
                    b"{\"status\":\"applied\",\"persistent\":true}\n".to_vec(),
                ))
            }
            "/api/v1/runtime/motion-webhook" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                Ok(self.motion_webhook.runtime())
            }
            _ => Err(BackendError::Protocol),
        })())
    }

    pub(super) fn start_motion_webhook(&self) {
        let config = self
            .host
            .motion_webhook_config()
            .and_then(|value| Config::from_value(&value))
            .unwrap_or_default();
        if self.motion_webhook.apply(config).is_err() {
            let _ = self.motion_webhook.apply(Config::default());
        }
        let _ = self.motion_webhook.start();
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, task_temp};
    use super::*;
    use std::fs;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Condvar;
    use std::time::{Duration, Instant};

    #[test]
    fn response_buffer_bounds_chunks_and_handles_empty_callbacks() {
        let mut response = ResponseBuffer { bytes: Vec::new() };
        let user = (&mut response as *mut ResponseBuffer).cast::<c_void>();
        let mut chunk = [b'x'; MAX_RESPONSE_BYTES];
        let data = chunk.as_mut_ptr().cast::<c_char>();
        // SAFETY: nonempty calls use the live buffer and response above;
        // null pointers occur only in explicitly rejected callback inputs.
        unsafe {
            assert_eq!(count_response(ptr::null_mut(), 0, 1, user), 0);
            assert_eq!(count_response(data, usize::MAX, 2, user), 0);
            assert_eq!(count_response(data, 1, 1, ptr::null_mut()), 0);
            assert_eq!(count_response(data, 1, chunk.len(), user), chunk.len());
            assert_eq!(count_response(data, 1, 1, user), 0);
        }
        assert_eq!(response.bytes, chunk);
    }

    fn event(sequence: u64) -> MotionEvent {
        MotionEvent {
            version: 1,
            sequence,
            state: MotionState::Active,
            channel: 1,
            monotonic_ms: 1234,
            occurred_unix_ms: Some(1_789_000_000_000),
            snapshot: None,
            clip: None,
        }
    }

    struct AvailableTransport;

    impl Transport for AvailableTransport {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _url: &str, _body: &[u8]) -> Result<u16, ()> {
            Ok(204)
        }
    }

    fn public_config(saved_url: &str, live_url: &str, name: &str) -> String {
        let root = task_temp(name);
        let config = root.join("thingino.json");
        fs::write(
            &config,
            format!("{{\"motion_webhook\":{{\"enabled\":true,\"url\":\"{saved_url}\"}}}}"),
        )
        .unwrap();
        let mut backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: config,
            ..crate::camera::CameraPaths::default()
        });
        backend.motion_webhook = Arc::new(Service::with_transport(Arc::new(AvailableTransport)));
        backend
            .motion_webhook
            .apply(Config {
                enabled: true,
                url: live_url.to_owned(),
            })
            .unwrap();

        let response = backend
            .motion_webhook_request("GET", "/api/v1/config/motion-webhook", b"")
            .unwrap()
            .unwrap();
        let body = String::from_utf8(response.body).unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
        body
    }

    #[test]
    fn public_config_detects_different_nonempty_saved_and_live_urls() {
        let body = public_config(
            "https://saved.example.test/private?token=saved",
            "https://live.example.test/private?token=live",
            "motion-webhook-config-different",
        );

        assert!(body.contains("\"url\":null"));
        assert!(body.contains("\"url_set\":true"));
        assert!(body.contains("\"live_url_set\":true"));
        assert!(body.contains("\"matches_saved\":false"));
        assert!(!body.contains("live.example.test"));
        assert!(!body.contains("saved.example.test"));
        assert!(!body.contains("token="));
    }

    #[test]
    fn public_config_matches_identical_nonempty_saved_and_live_urls() {
        let body = public_config(
            "https://same.example.test/private?token=secret",
            "https://same.example.test/private?token=secret",
            "motion-webhook-config-matching",
        );

        assert!(body.contains("\"matches_saved\":true"));
        assert!(body.contains("\"url\":null"));
        assert!(!body.contains("same.example.test"));
        assert!(!body.contains("secret"));
    }

    #[test]
    fn malformed_optional_config_fails_closed_without_blocking_startup() {
        let root = task_temp("motion-webhook-malformed");
        let config = root.join("thingino.json");
        fs::write(
            &config,
            br#"{"motion_webhook":{"enabled":true,"url":"file:///etc/passwd"}}"#,
        )
        .unwrap();
        let mut backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: config,
            ..crate::camera::CameraPaths::default()
        });
        backend.start_motion_webhook();
        assert!(
            !backend
                .motion_webhook
                .runtime
                .enabled
                .load(Ordering::Acquire)
        );
        assert!(
            backend
                .motion_webhook
                .runtime
                .running
                .load(Ordering::Acquire)
        );
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn curl_posts_bounded_json_to_loopback() {
        let transport = CurlTransport::load();
        assert!(transport.available());
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let mut request = vec![0_u8; 2048];
            let count = stream.read(&mut request).unwrap();
            stream
                .write_all(
                    b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                )
                .unwrap();
            String::from_utf8(request[..count].to_vec()).unwrap()
        });
        let body = event_body(&event(7));
        assert_eq!(
            transport.post(&format!("http://{address}/motion"), body.as_bytes()),
            Ok(204)
        );
        let request = server.join().unwrap();
        assert!(request.starts_with("POST /motion HTTP/1.1\r\n"));
        assert!(request.contains("Content-Type: application/json\r\n"));
        assert!(request.contains("\"schema\":\"thingino.motion.webhook.v1\""));
        assert!(!request.contains("snapshot"));
    }

    struct BlockingTransport {
        state: Arc<(Mutex<bool>, Condvar)>,
    }

    struct HeldGenerationTransport {
        state: Arc<(Mutex<bool>, Condvar)>,
        calls: AtomicU32,
    }

    impl Transport for HeldGenerationTransport {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _url: &str, _body: &[u8]) -> Result<u16, ()> {
            self.calls.fetch_add(1, Ordering::Relaxed);
            let (lock, wake) = &*self.state;
            let held = lock.lock().unwrap();
            let _ = wake
                .wait_timeout_while(held, Duration::from_secs(2), |released| !*released)
                .unwrap();
            Ok(204)
        }
    }

    impl Transport for BlockingTransport {
        fn available(&self) -> bool {
            true
        }
        fn post(&self, _url: &str, _body: &[u8]) -> Result<u16, ()> {
            let (lock, wake) = &*self.state;
            let started = lock.lock().unwrap();
            let _ = wake
                .wait_timeout_while(started, Duration::from_millis(250), |done| !*done)
                .unwrap();
            Err(())
        }
    }

    #[test]
    fn stalled_transport_does_not_block_event_producer_and_queue_is_bounded() {
        let state = Arc::new((Mutex::new(false), Condvar::new()));
        let service = Service::with_transport(Arc::new(BlockingTransport {
            state: state.clone(),
        }));
        service
            .apply(Config {
                enabled: true,
                url: "http://127.0.0.1/".to_owned(),
            })
            .unwrap();
        service.start().unwrap();
        let started = Instant::now();
        for sequence in 1..=16 {
            let _ = service.try_send_motion(event(sequence));
        }
        assert!(started.elapsed() < Duration::from_millis(50));
        assert!(service.runtime.queue_dropped.load(Ordering::Relaxed) >= 13);
        let (lock, wake) = &*state;
        *lock.lock().unwrap() = true;
        wake.notify_all();
    }

    #[test]
    fn endpoint_change_discards_an_event_queued_for_the_old_generation() {
        let state = Arc::new((Mutex::new(false), Condvar::new()));
        let transport = Arc::new(HeldGenerationTransport {
            state: state.clone(),
            calls: AtomicU32::new(0),
        });
        let service = Service::with_transport(transport.clone());
        service
            .apply(Config {
                enabled: true,
                url: "http://127.0.0.1/old-hook".to_owned(),
            })
            .unwrap();
        service.start().unwrap();
        assert_eq!(
            service.try_send_motion(event(1)),
            MotionEventDisposition::Queued
        );
        assert_eq!(
            service.try_send_motion(event(2)),
            MotionEventDisposition::Queued
        );
        let deadline = Instant::now() + Duration::from_secs(1);
        while transport.calls.load(Ordering::Acquire) == 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        service
            .apply(Config {
                enabled: true,
                url: "http://127.0.0.1/new-hook".to_owned(),
            })
            .unwrap();
        let (lock, wake) = &*state;
        *lock.lock().unwrap() = true;
        wake.notify_all();
        let deadline = Instant::now() + Duration::from_secs(1);
        while service.runtime.queued.load(Ordering::Acquire) != 0 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(transport.calls.load(Ordering::Acquire), 1);
        assert_eq!(service.runtime.requests.load(Ordering::Acquire), 1);
        assert_eq!(service.runtime.last_sequence.load(Ordering::Acquire), 1);
        assert_eq!(service.runtime.queued.load(Ordering::Acquire), 0);
        assert_eq!(service.runtime.queue_dropped.load(Ordering::Acquire), 1);
    }
}
