use std::net::Ipv6Addr;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};

use crate::camera::motion_events::{
    MotionEvent, MotionEventDisposition, MotionEventSink, MotionState,
};
use crate::json::Value;
use crate::{BackendError, BackendResponse};

use super::RaptorBackend;

const QUEUE_CAPACITY: usize = 2;
const SUBJECT: &str = "Motion";
const BODY: &str = "Motion detected.";

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) enum TlsMode {
    #[default]
    Starttls,
    Implicit,
}

impl TlsMode {
    fn from_value(value: Option<&Value>) -> Self {
        match value.and_then(Value::as_str) {
            Some("implicit") => Self::Implicit,
            _ => Self::Starttls,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Starttls => "starttls",
            Self::Implicit => "implicit",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct Config {
    pub enabled: bool,
    pub host: String,
    pub port: u16,
    pub tls_mode: TlsMode,
    pub username: String,
    pub password: String,
    pub from_address: String,
    pub to_address: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            enabled: false,
            host: String::new(),
            port: 587,
            tls_mode: TlsMode::Starttls,
            username: String::new(),
            password: String::new(),
            from_address: String::new(),
            to_address: String::new(),
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
            port: value.get_path("port").and_then(value_u16).unwrap_or(587),
            tls_mode: TlsMode::from_value(value.get_path("tls_mode")),
            username: text(value, "username"),
            password: text(value, "password"),
            from_address: text(value, "from_address"),
            to_address: text(value, "to_address"),
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
    fn send(&self, config: &Config) -> Result<u16, ()>;
}

struct QueuedEvent {
    generation: u32,
}

struct LiveConfig {
    value: Config,
    generation: u32,
}

struct CurlEmailTransport {
    curl: super::motion_webhook::CurlTransport,
}

impl CurlEmailTransport {
    fn load() -> Self {
        Self {
            curl: super::motion_webhook::CurlTransport::load(),
        }
    }
}

impl Transport for CurlEmailTransport {
    fn available(&self) -> bool {
        self.curl.supports_protocols(&["smtp", "smtps"])
            && super::motion_webhook::Transport::available(&self.curl)
    }

    fn send(&self, config: &Config) -> Result<u16, ()> {
        let url = smtp_url(config);
        let message = smtp_message(&config.from_address, &config.to_address);
        let from = format!("<{}>", config.from_address);
        let to = format!("<{}>", config.to_address);
        self.curl.send_smtp(&super::motion_webhook::SmtpRequest {
            url: &url,
            username: &config.username,
            password: &config.password,
            from: &from,
            to: &to,
            message: message.as_bytes(),
        })
    }
}

fn smtp_url(config: &Config) -> String {
    let scheme = match config.tls_mode {
        TlsMode::Starttls => "smtp",
        TlsMode::Implicit => "smtps",
    };
    let host = if config.host.parse::<Ipv6Addr>().is_ok() {
        format!("[{}]", config.host)
    } else {
        config.host.clone()
    };
    format!("{scheme}://{host}:{}", config.port)
}

fn smtp_message(from: &str, to: &str) -> String {
    format!("From: <{from}>\r\nTo: <{to}>\r\nSubject: {SUBJECT}\r\n\r\n{BODY}\r\n")
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ResultClass {
    Idle = 0,
    Success = 1,
    SmtpError = 2,
    TransportError = 3,
}

impl ResultClass {
    fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Success => "success",
            Self::SmtpError => "smtp_error",
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
    last_smtp_status: AtomicU32,
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
        Self::with_transport(Arc::new(CurlEmailTransport::load()))
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
        let runtime = self.runtime.clone();
        let transport = self.transport.clone();
        let shutdown = self.shutdown.clone();
        *worker = Some(
            thread::Builder::new()
                .name("control-motion-email".to_owned())
                .spawn(move || worker_loop(receiver, config, runtime, transport, shutdown))
                .map_err(|_| BackendError::Unavailable)?,
        );
        self.runtime.running.store(true, Ordering::Release);
        Ok(())
    }

    pub(super) fn apply(&self, config: Config) -> Result<(), BackendError> {
        if config.enabled
            && (!self.transport.available()
                || config.host.is_empty()
                || config.from_address.is_empty()
                || config.to_address.is_empty()
                || config.username.is_empty() != config.password.is_empty())
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
                "{{\"saved_enabled\":{},\"enabled\":{},\"host\":{},\"live_host\":{},\"port\":{},\"live_port\":{},\"tls_mode\":\"{}\",\"live_tls_mode\":\"{}\",\"username\":{},\"live_username\":{},\"password\":null,\"password_set\":{},\"live_password_set\":{},\"from_address\":{},\"live_from_address\":{},\"to_address\":{},\"live_to_address\":{},\"matches_saved\":{},\"transport_available\":{}}}\n",
                saved.enabled,
                live.enabled,
                Value::String(saved.host.clone()).to_json(),
                Value::String(live.host.clone()).to_json(),
                saved.port,
                live.port,
                saved.tls_mode.as_str(),
                live.tls_mode.as_str(),
                Value::String(saved.username.clone()).to_json(),
                Value::String(live.username.clone()).to_json(),
                !saved.password.is_empty(),
                !live.password.is_empty(),
                Value::String(saved.from_address.clone()).to_json(),
                Value::String(live.from_address.clone()).to_json(),
                Value::String(saved.to_address.clone()).to_json(),
                Value::String(live.to_address.clone()).to_json(),
                saved == &live,
                self.transport.available(),
            )
            .into_bytes(),
        ))
    }

    fn runtime(&self) -> BackendResponse {
        let class = match self.runtime.last_result.load(Ordering::Relaxed) {
            1 => ResultClass::Success,
            2 => ResultClass::SmtpError,
            3 => ResultClass::TransportError,
            _ => ResultClass::Idle,
        };
        let status = self.runtime.last_smtp_status.load(Ordering::Relaxed);
        BackendResponse::json(
            format!(
                "{{\"running\":{},\"enabled\":{},\"transport_available\":{},\"queue_capacity\":{QUEUE_CAPACITY},\"queue_depth\":{},\"queue_dropped\":{},\"requests\":{},\"successes\":{},\"failures\":{},\"last_result\":\"{}\",\"last_smtp_status\":{}}}\n",
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
            )
            .into_bytes(),
        )
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
        let queued = QueuedEvent {
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
        runtime.queued.fetch_sub(1, Ordering::Relaxed);
        if shutdown.load(Ordering::Acquire) {
            break;
        }
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
        runtime.requests.fetch_add(1, Ordering::Relaxed);
        match transport.send(&config) {
            Ok(status) if (200..300).contains(&status) => {
                runtime.successes.fetch_add(1, Ordering::Relaxed);
                runtime
                    .last_result
                    .store(ResultClass::Success as u32, Ordering::Relaxed);
                runtime
                    .last_smtp_status
                    .store(u32::from(status), Ordering::Relaxed);
            }
            Ok(status) => {
                runtime.failures.fetch_add(1, Ordering::Relaxed);
                runtime
                    .last_result
                    .store(ResultClass::SmtpError as u32, Ordering::Relaxed);
                runtime
                    .last_smtp_status
                    .store(u32::from(status), Ordering::Relaxed);
            }
            Err(()) => {
                runtime.failures.fetch_add(1, Ordering::Relaxed);
                runtime
                    .last_result
                    .store(ResultClass::TransportError as u32, Ordering::Relaxed);
                runtime.last_smtp_status.store(0, Ordering::Relaxed);
            }
        }
    }
    runtime.running.store(false, Ordering::Release);
}

impl RaptorBackend {
    pub(super) fn motion_email_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Option<Result<BackendResponse, BackendError>> {
        if !matches!(
            target,
            "/api/v1/config/motion-email" | "/api/v1/runtime/motion-email"
        ) {
            return None;
        }
        Some((|| match target {
            "/api/v1/config/motion-email" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let saved = Config::from_value(&self.host.motion_email_config()?);
                self.motion_email.public_config(&saved)
            }
            "/api/v1/config/motion-email" if method == "POST" && !body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let _mutation = self.lock_mutation()?;
                self.host.motion_email_config_request("POST", body)?;
                let config = Config::from_value(&self.host.motion_email_config()?);
                let enabled = config.enabled;
                self.motion_email.apply(config).map_err(|_| BackendError::PartialApply(
                    "Email settings were saved but could not be applied. Reload the saved and live status before retrying.",
                ))?;
                if enabled && !self.motion_email.runtime.running.load(Ordering::Acquire) {
                    return Err(BackendError::PartialApply(
                        "Email settings were saved but its delivery worker is not running. Reload the saved and live status before retrying.",
                    ));
                }
                Ok(BackendResponse::json(
                    b"{\"status\":\"applied\",\"persistent\":true}\n".to_vec(),
                ))
            }
            "/api/v1/runtime/motion-email" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                Ok(self.motion_email.runtime())
            }
            _ => Err(BackendError::Protocol),
        })())
    }

    pub(super) fn start_motion_email(&self) {
        let config = self
            .host
            .motion_email_config()
            .map(|value| Config::from_value(&value))
            .unwrap_or_default();
        if self.motion_email.apply(config).is_err() {
            let _ = self.motion_email.apply(Config::default());
        }
        let _ = self.motion_email.start();
    }
}

#[cfg(test)]
#[path = "motion_email_tests.rs"]
mod tests;
