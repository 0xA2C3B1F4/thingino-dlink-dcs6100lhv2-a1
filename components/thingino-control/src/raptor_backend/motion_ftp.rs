use std::io::Read;
use std::net::Ipv6Addr;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::camera::motion_events::{
    MotionEvent, MotionEventDisposition, MotionEventSink, MotionState,
};
use crate::json::Value;
use crate::{BackendError, BackendResponse};

use super::RaptorBackend;
use super::motion_webhook::{FtpRequest, FtpResult, TransferCancel};

const QUEUE_CAPACITY: usize = 2;
const MAX_JPEG_BYTES: usize = 256 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct Config {
    pub enabled: bool,
    pub host: String,
    pub port: u16,
    pub username: String,
    pub password: String,
    pub path: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            enabled: false,
            host: String::new(),
            port: 21,
            username: String::new(),
            password: String::new(),
            path: String::new(),
        }
    }
}

impl Config {
    fn from_value(value: &Value) -> Self {
        Self {
            enabled: value
                .get_path("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            host: text(value, "host"),
            port: value.get_path("port").and_then(value_u16).unwrap_or(21),
            username: text(value, "username"),
            password: text(value, "password"),
            path: text(value, "path"),
        }
    }
}

fn text(value: &Value, name: &str) -> String {
    value
        .get_path(name)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn value_u16(value: &Value) -> Option<u16> {
    match value {
        Value::Number(raw) => raw.parse().ok(),
        _ => None,
    }
}

pub(super) trait Transport: Send + Sync {
    fn available(&self) -> bool;
    fn upload(
        &self,
        config: &Config,
        filename: &str,
        jpeg: &[u8],
        cancel: TransferCancel<'_>,
    ) -> FtpResult;
}

struct CurlFtpTransport {
    curl: super::motion_webhook::CurlTransport,
}

impl CurlFtpTransport {
    fn load() -> Self {
        Self {
            curl: super::motion_webhook::CurlTransport::load(),
        }
    }
}

impl Transport for CurlFtpTransport {
    fn available(&self) -> bool {
        self.curl.supports_protocols(&["ftp"]) && self.curl.supports_verified_tls()
    }

    fn upload(
        &self,
        config: &Config,
        filename: &str,
        jpeg: &[u8],
        cancel: TransferCancel<'_>,
    ) -> FtpResult {
        let url = ftp_url(config, filename);
        self.curl.upload_explicit_ftps(&FtpRequest {
            url: &url,
            username: &config.username,
            password: &config.password,
            jpeg,
            cancel,
        })
    }
}

fn ftp_url(config: &Config, filename: &str) -> String {
    let host = if config.host.parse::<Ipv6Addr>().is_ok() {
        format!("[{}]", config.host)
    } else {
        config.host.clone()
    };
    let prefix = if config.path.is_empty() {
        String::new()
    } else {
        format!("{}/", config.path)
    };
    format!("ftp://{host}:{}/{prefix}{filename}", config.port)
}

fn filename(event: &MotionEvent, session: &str) -> String {
    format!(
        "motion-{session}-ch{}-seq{}.jpg",
        event.channel, event.sequence
    )
}

fn session_id() -> Option<String> {
    let mut bytes = [0_u8; 8];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(&mut bytes))
        .ok()?;
    Some(
        bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<Vec<_>>()
            .concat(),
    )
}

struct QueuedEvent {
    event: MotionEvent,
    generation: u32,
}

struct LiveConfig {
    value: Config,
    generation: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ResultClass {
    Idle = 0,
    Success = 1,
    CaptureError = 2,
    FtpError = 3,
    Cancelled = 4,
}

impl ResultClass {
    fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Success => "success",
            Self::CaptureError => "capture_error",
            Self::FtpError => "ftp_error",
            Self::Cancelled => "cancelled",
        }
    }
}

#[derive(Default)]
struct Runtime {
    running: AtomicBool,
    enabled: AtomicBool,
    queued: AtomicU32,
    queue_dropped: AtomicU32,
    captures: AtomicU32,
    requests: AtomicU32,
    successes: AtomicU32,
    failures: AtomicU32,
    cancellations: AtomicU32,
    last_result: AtomicU32,
    last_ftp_status: AtomicU32,
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
    session: Option<String>,
}

impl Service {
    pub(super) fn new() -> Self {
        Self::build(Arc::new(CurlFtpTransport::load()))
    }

    #[cfg(test)]
    pub(super) fn with_transport(transport: Arc<dyn Transport>) -> Self {
        Self::build(transport)
    }

    fn build(transport: Arc<dyn Transport>) -> Self {
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
            session: session_id(),
        }
    }

    pub(super) fn start(&self, backend: Weak<RaptorBackend>) -> Result<(), BackendError> {
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
        let context = WorkerContext {
            backend,
            config: self.config.clone(),
            generation: self.generation.clone(),
            runtime: self.runtime.clone(),
            transport: self.transport.clone(),
            shutdown: self.shutdown.clone(),
            session: self.session.clone().ok_or(BackendError::Unavailable)?,
        };
        *worker = Some(
            thread::Builder::new()
                .name("control-motion-ftp".to_owned())
                .spawn(move || worker_loop(receiver, context))
                .map_err(|_| BackendError::Unavailable)?,
        );
        self.runtime.running.store(true, Ordering::Release);
        Ok(())
    }

    pub(super) fn apply(&self, config: Config) -> Result<(), BackendError> {
        if config.enabled
            && (self.session.is_none()
                || !self.transport.available()
                || config.host.is_empty()
                || config.username.is_empty()
                || config.password.is_empty())
        {
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
        let live = self
            .config
            .lock()
            .map_err(|_| BackendError::Unavailable)?
            .value
            .clone();
        Ok(BackendResponse::json(
            format!(
                "{{\"saved_enabled\":{},\"enabled\":{},\"host\":{},\"live_host\":{},\"port\":{},\"live_port\":{},\"tls_mode\":\"explicit\",\"live_tls_mode\":\"explicit\",\"username\":{},\"live_username\":{},\"password\":null,\"password_set\":{},\"live_password_set\":{},\"path\":{},\"live_path\":{},\"matches_saved\":{},\"transport_available\":{}}}\n",
                saved.enabled,
                live.enabled,
                Value::String(saved.host.clone()).to_json(),
                Value::String(live.host.clone()).to_json(),
                saved.port,
                live.port,
                Value::String(saved.username.clone()).to_json(),
                Value::String(live.username.clone()).to_json(),
                !saved.password.is_empty(),
                !live.password.is_empty(),
                Value::String(saved.path.clone()).to_json(),
                Value::String(live.path.clone()).to_json(),
                saved == &live,
                self.transport.available(),
            )
            .into_bytes(),
        ))
    }

    fn runtime(&self) -> BackendResponse {
        let class = match self.runtime.last_result.load(Ordering::Relaxed) {
            1 => ResultClass::Success,
            2 => ResultClass::CaptureError,
            3 => ResultClass::FtpError,
            4 => ResultClass::Cancelled,
            _ => ResultClass::Idle,
        };
        let status = self.runtime.last_ftp_status.load(Ordering::Relaxed);
        BackendResponse::json(
            format!(
                "{{\"running\":{},\"enabled\":{},\"transport_available\":{},\"queue_capacity\":{QUEUE_CAPACITY},\"queue_depth\":{},\"queue_dropped\":{},\"captures\":{},\"requests\":{},\"successes\":{},\"failures\":{},\"cancellations\":{},\"last_result\":\"{}\",\"last_ftp_status\":{}}}\n",
                self.runtime.running.load(Ordering::Acquire),
                self.runtime.enabled.load(Ordering::Acquire),
                self.transport.available(),
                self.runtime.queued.load(Ordering::Relaxed),
                self.runtime.queue_dropped.load(Ordering::Relaxed),
                self.runtime.captures.load(Ordering::Relaxed),
                self.runtime.requests.load(Ordering::Relaxed),
                self.runtime.successes.load(Ordering::Relaxed),
                self.runtime.failures.load(Ordering::Relaxed),
                self.runtime.cancellations.load(Ordering::Relaxed),
                class.as_str(),
                if status == 0 { "null".to_owned() } else { status.to_string() },
            )
            .into_bytes(),
        )
    }
}

impl MotionEventSink for Service {
    fn try_send_motion(&self, event: MotionEvent) -> MotionEventDisposition {
        if event.state != MotionState::Active
            || event.channel != 1
            || !self.runtime.enabled.load(Ordering::Acquire)
        {
            return MotionEventDisposition::Disabled;
        }
        if !event.has_bounded_metadata() {
            self.runtime.queue_dropped.fetch_add(1, Ordering::Relaxed);
            return MotionEventDisposition::Dropped;
        }
        self.runtime.queued.fetch_add(1, Ordering::Relaxed);
        let queued = QueuedEvent {
            event,
            generation: self.generation.load(Ordering::Acquire),
        };
        match self.sender.try_send(queued) {
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
            && worker.thread().id() != thread::current().id()
        {
            let _ = worker.join();
        }
    }
}

struct WorkerContext {
    backend: Weak<RaptorBackend>,
    config: Arc<Mutex<LiveConfig>>,
    generation: Arc<AtomicU32>,
    runtime: Arc<Runtime>,
    transport: Arc<dyn Transport>,
    shutdown: Arc<AtomicBool>,
    session: String,
}

fn worker_loop(receiver: Receiver<QueuedEvent>, context: WorkerContext) {
    context.runtime.running.store(true, Ordering::Release);
    while !context.shutdown.load(Ordering::Acquire) {
        let Ok(event) = receiver.recv_timeout(Duration::from_millis(100)) else {
            continue;
        };
        context.runtime.queued.fetch_sub(1, Ordering::Relaxed);
        let Some(backend) = context.backend.upgrade() else {
            break;
        };
        process_event(&event, &backend, &context);
    }
    context.runtime.running.store(false, Ordering::Release);
}

fn process_event(event: &QueuedEvent, backend: &RaptorBackend, context: &WorkerContext) {
    if context.shutdown.load(Ordering::Acquire)
        || context.generation.load(Ordering::Acquire) != event.generation
    {
        return;
    }
    let config = match context.config.lock() {
        Ok(config) if config.generation == event.generation && config.value.enabled => {
            config.value.clone()
        }
        _ => return,
    };
    let privacy_generation = backend.ha_privacy_generation.load(Ordering::Acquire);
    let deadline = Instant::now() + Duration::from_millis(900);
    if backend.privacy_state(deadline) != Ok(false) {
        record_failure(&context.runtime, ResultClass::CaptureError);
        return;
    }
    if context.shutdown.load(Ordering::Acquire)
        || context.generation.load(Ordering::Acquire) != event.generation
    {
        record_failure(&context.runtime, ResultClass::Cancelled);
        return;
    }
    let jpeg = match backend.http_snapshot_bounded(1, deadline, MAX_JPEG_BYTES) {
        Ok(response) => response.body,
        Err(_) => {
            record_failure(&context.runtime, ResultClass::CaptureError);
            return;
        }
    };
    context.runtime.captures.fetch_add(1, Ordering::Relaxed);
    if backend.privacy_state(Instant::now() + Duration::from_millis(200)) != Ok(false) {
        record_failure(&context.runtime, ResultClass::CaptureError);
        return;
    }
    let guard = match backend.lock_mutation() {
        Ok(guard) => guard,
        Err(_) => {
            record_failure(&context.runtime, ResultClass::Cancelled);
            return;
        }
    };
    let config_current = context
        .config
        .lock()
        .map(|live| live.generation == event.generation && live.value.enabled)
        .unwrap_or(false);
    let saved_current = backend
        .host
        .motion_ftp_config()
        .map(|saved| Config::from_value(&saved) == config)
        .unwrap_or(false);
    let privacy_current =
        backend.ha_privacy_generation.load(Ordering::Acquire) == privacy_generation;
    let privacy_off =
        backend.privacy_state(Instant::now() + Duration::from_millis(150)) == Ok(false);
    drop(guard);
    if !config_current || !saved_current || !privacy_current || !privacy_off {
        record_failure(&context.runtime, ResultClass::Cancelled);
        return;
    }
    let cancel = TransferCancel {
        config_generation: &context.generation,
        expected_config_generation: event.generation,
        privacy_generation: &backend.ha_privacy_generation,
        expected_privacy_generation: privacy_generation,
    };
    context.runtime.requests.fetch_add(1, Ordering::Relaxed);
    match context.transport.upload(
        &config,
        &filename(&event.event, &context.session),
        &jpeg,
        cancel,
    ) {
        FtpResult::Stored(status) => {
            context.runtime.successes.fetch_add(1, Ordering::Relaxed);
            context
                .runtime
                .last_result
                .store(ResultClass::Success as u32, Ordering::Relaxed);
            context
                .runtime
                .last_ftp_status
                .store(u32::from(status), Ordering::Relaxed);
        }
        FtpResult::Cancelled => record_failure(&context.runtime, ResultClass::Cancelled),
        FtpResult::Failed => record_failure(&context.runtime, ResultClass::FtpError),
    }
}

fn record_failure(runtime: &Runtime, class: ResultClass) {
    runtime.failures.fetch_add(1, Ordering::Relaxed);
    if class == ResultClass::Cancelled {
        runtime.cancellations.fetch_add(1, Ordering::Relaxed);
    }
    runtime.last_result.store(class as u32, Ordering::Relaxed);
    runtime.last_ftp_status.store(0, Ordering::Relaxed);
}

impl RaptorBackend {
    pub(super) fn motion_ftp_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Option<Result<BackendResponse, BackendError>> {
        if !matches!(
            target,
            "/api/v1/config/motion-ftp" | "/api/v1/runtime/motion-ftp"
        ) {
            return None;
        }
        Some((|| match target {
            "/api/v1/config/motion-ftp" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let saved = Config::from_value(&self.host.motion_ftp_config()?);
                self.motion_ftp.public_config(&saved)
            }
            "/api/v1/config/motion-ftp" if method == "POST" && !body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let _mutation = self.lock_mutation()?;
                self.host.motion_ftp_config_request("POST", body)?;
                let config = Config::from_value(&self.host.motion_ftp_config()?);
                let enabled = config.enabled;
                self.motion_ftp.apply(config).map_err(|_| BackendError::PartialApply(
                    "FTP settings were saved but could not be applied. Reload saved and live status before retrying.",
                ))?;
                if enabled && !self.motion_ftp.runtime.running.load(Ordering::Acquire) {
                    return Err(BackendError::PartialApply(
                        "FTP settings were saved but its delivery worker is not running. Reload saved and live status before retrying.",
                    ));
                }
                Ok(BackendResponse::json(
                    b"{\"status\":\"applied\",\"persistent\":true}\n".to_vec(),
                ))
            }
            "/api/v1/runtime/motion-ftp" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                Ok(self.motion_ftp.runtime())
            }
            _ => Err(BackendError::Protocol),
        })())
    }

    pub(super) fn start_motion_ftp(self: &Arc<Self>) {
        let config = self
            .host
            .motion_ftp_config()
            .map(|value| Config::from_value(&value))
            .unwrap_or_default();
        if self.motion_ftp.apply(config).is_err() {
            let _ = self.motion_ftp.apply(Config::default());
        }
        let _ = self.motion_ftp.start(Arc::downgrade(self));
    }
}

#[cfg(test)]
#[path = "motion_ftp_tests.rs"]
mod tests;
