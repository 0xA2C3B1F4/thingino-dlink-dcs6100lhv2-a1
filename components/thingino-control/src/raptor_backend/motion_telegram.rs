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
const API_ROOT: &str = "https://api.telegram.org";
const MESSAGE: &str = "Motion detected.";

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(super) struct Config {
    pub enabled: bool,
    pub bot_token: String,
    pub chat_id: String,
}

impl Config {
    fn from_value(value: &Value) -> Self {
        Self {
            enabled: value
                .get_path("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            bot_token: value
                .get_path("bot_token")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            chat_id: value
                .get_path("chat_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        }
    }
}

pub(super) trait Transport: Send + Sync {
    fn available(&self) -> bool;
    fn post(&self, bot_token: &str, chat_id: &str) -> Result<u16, ()>;
}

struct QueuedEvent {
    generation: u32,
}

struct LiveConfig {
    value: Config,
    generation: u32,
}

struct CurlTelegramTransport {
    curl: super::motion_webhook::CurlTransport,
}

impl CurlTelegramTransport {
    fn load() -> Self {
        Self {
            curl: super::motion_webhook::CurlTransport::load(),
        }
    }
}

impl Transport for CurlTelegramTransport {
    fn available(&self) -> bool {
        super::motion_webhook::Transport::available(&self.curl)
    }

    fn post(&self, bot_token: &str, chat_id: &str) -> Result<u16, ()> {
        let endpoint = telegram_endpoint(bot_token);
        let body = telegram_body(chat_id);
        let (status, response) = self.curl.post_with_headers_and_response(
            &endpoint,
            body.as_bytes(),
            &["Content-Type: application/json"],
        )?;
        if (200..300).contains(&status) && !telegram_acknowledged(&response) {
            return Err(());
        }
        Ok(status)
    }
}

fn telegram_endpoint(bot_token: &str) -> String {
    format!("{API_ROOT}/bot{bot_token}/sendMessage")
}

fn telegram_body(chat_id: &str) -> String {
    format!(
        "{{\"chat_id\":{},\"text\":\"{MESSAGE}\"}}",
        Value::String(chat_id.to_owned()).to_json()
    )
}

fn telegram_acknowledged(body: &[u8]) -> bool {
    let Ok(value) = crate::json::parse(body) else {
        return false;
    };
    value.get_path("ok").and_then(Value::as_bool) == Some(true)
        && value
            .get_path("result.message_id")
            .and_then(|id| super::value_u64(id).ok())
            .is_some_and(|id| id > 0)
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
        Self::with_transport(Arc::new(CurlTelegramTransport::load()))
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
                .name("control-motion-telegram".to_owned())
                .spawn(move || worker_loop(receiver, config, runtime, transport, shutdown))
                .map_err(|_| BackendError::Unavailable)?,
        );
        self.runtime.running.store(true, Ordering::Release);
        Ok(())
    }

    pub(super) fn apply(&self, config: Config) -> Result<(), BackendError> {
        if config.enabled
            && (!self.transport.available()
                || config.bot_token.is_empty()
                || config.chat_id.is_empty())
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
                "{{\"saved_enabled\":{},\"enabled\":{},\"bot_token\":null,\"bot_token_set\":{},\"live_bot_token_set\":{},\"chat_id\":null,\"chat_id_set\":{},\"live_chat_id_set\":{},\"matches_saved\":{},\"transport_available\":{}}}\n",
                saved.enabled,
                live.enabled,
                !saved.bot_token.is_empty(),
                !live.bot_token.is_empty(),
                !saved.chat_id.is_empty(),
                !live.chat_id.is_empty(),
                saved == &live,
                self.transport.available(),
            )
            .into_bytes(),
        ))
    }

    fn runtime(&self) -> BackendResponse {
        let class = match self.runtime.last_result.load(Ordering::Relaxed) {
            1 => ResultClass::Success,
            2 => ResultClass::HttpError,
            3 => ResultClass::TransportError,
            _ => ResultClass::Idle,
        };
        let status = self.runtime.last_http_status.load(Ordering::Relaxed);
        BackendResponse::json(
            format!(
                "{{\"running\":{},\"enabled\":{},\"transport_available\":{},\"queue_capacity\":{QUEUE_CAPACITY},\"queue_depth\":{},\"queue_dropped\":{},\"requests\":{},\"successes\":{},\"failures\":{},\"last_result\":\"{}\",\"last_http_status\":{}}}\n",
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
        let event = QueuedEvent {
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
        match transport.post(&config.bot_token, &config.chat_id) {
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

impl RaptorBackend {
    pub(super) fn motion_telegram_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Option<Result<BackendResponse, BackendError>> {
        if !matches!(
            target,
            "/api/v1/config/motion-telegram" | "/api/v1/runtime/motion-telegram"
        ) {
            return None;
        }
        Some((|| match target {
            "/api/v1/config/motion-telegram" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let saved = Config::from_value(&self.host.motion_telegram_config()?);
                self.motion_telegram.public_config(&saved)
            }
            "/api/v1/config/motion-telegram" if method == "POST" && !body.is_empty() => {
                self.ensure_exclusive_owner()?;
                let _mutation = self.lock_mutation()?;
                self.host.motion_telegram_config_request("POST", body)?;
                let config = Config::from_value(&self.host.motion_telegram_config()?);
                let enabled = config.enabled;
                self.motion_telegram.apply(config).map_err(|_| BackendError::PartialApply(
                    "telegram settings were saved but could not be applied. Reload the saved and live status before retrying.",
                ))?;
                if enabled && !self.motion_telegram.runtime.running.load(Ordering::Acquire) {
                    return Err(BackendError::PartialApply(
                        "telegram settings were saved but its delivery worker is not running. Reload the saved and live status before retrying.",
                    ));
                }
                Ok(BackendResponse::json(
                    b"{\"status\":\"applied\",\"persistent\":true}\n".to_vec(),
                ))
            }
            "/api/v1/runtime/motion-telegram" if method == "GET" && body.is_empty() => {
                self.ensure_exclusive_owner()?;
                Ok(self.motion_telegram.runtime())
            }
            _ => Err(BackendError::Protocol),
        })())
    }

    pub(super) fn start_motion_telegram(&self) {
        let config = self
            .host
            .motion_telegram_config()
            .map(|value| Config::from_value(&value))
            .unwrap_or_default();
        if self.motion_telegram.apply(config).is_err() {
            let _ = self.motion_telegram.apply(Config::default());
        }
        let _ = self.motion_telegram.start();
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, task_temp};
    use super::*;
    use crate::camera::motion_events::MOTION_EVENT_VERSION;
    use std::fs;
    use std::sync::Condvar;
    use std::time::{Duration, Instant};

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

    struct AvailableTransport;

    impl Transport for AvailableTransport {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _bot_token: &str, _chat_id: &str) -> Result<u16, ()> {
            Ok(204)
        }
    }

    fn config_body(
        saved_token: &str,
        live_token: &str,
        saved_chat: &str,
        live_chat: &str,
    ) -> String {
        let root = task_temp("motion-telegram-config");
        let config = root.join("thingino.json");
        fs::write(
            &config,
            format!(
                "{{\"motion_telegram\":{{\"enabled\":true,\"bot_token\":\"{saved_token}\",\"chat_id\":\"{saved_chat}\"}}}}"
            ),
        )
        .unwrap();
        let mut fixture = backend(&root, "127.0.0.1:9".parse().unwrap());
        fixture.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: config,
            ..crate::camera::CameraPaths::default()
        });
        fixture.motion_telegram = Arc::new(Service::with_transport(Arc::new(AvailableTransport)));
        fixture
            .motion_telegram
            .apply(Config {
                enabled: true,
                bot_token: live_token.to_owned(),
                chat_id: live_chat.to_owned(),
            })
            .unwrap();
        let response = fixture
            .motion_telegram_request("GET", "/api/v1/config/motion-telegram", b"")
            .unwrap()
            .unwrap();
        let body = String::from_utf8(response.body).unwrap();
        drop(fixture);
        fs::remove_dir_all(root).unwrap();
        body
    }

    #[test]
    fn saved_live_comparison_uses_token_and_chat_without_disclosing_them() {
        let different = config_body("saved-secret", "live-secret", "saved-chat", "live-chat");
        assert!(different.contains("\"matches_saved\":false"));
        for secret in ["saved-secret", "live-secret", "saved-chat", "live-chat"] {
            assert!(!different.contains(secret));
        }
        let matching = config_body("same-secret", "same-secret", "same-chat", "same-chat");
        assert!(matching.contains("\"matches_saved\":true"));
        assert!(!matching.contains("same-secret"));
        assert!(!matching.contains("same-chat"));
    }

    #[test]
    fn protocol_is_fixed_to_official_send_message_with_json_text() {
        assert_eq!(
            telegram_endpoint("123:ABC"),
            "https://api.telegram.org/bot123:ABC/sendMessage"
        );
        assert_eq!(
            telegram_body("@camera_alerts"),
            "{\"chat_id\":\"@camera_alerts\",\"text\":\"Motion detected.\"}"
        );
        assert_eq!(
            telegram_body("-100123"),
            "{\"chat_id\":\"-100123\",\"text\":\"Motion detected.\"}"
        );
        assert!(telegram_acknowledged(
            br#"{"ok":true,"result":{"message_id":9}}"#
        ));
        assert!(!telegram_acknowledged(br#"{"ok":true,"result":{}}"#));
        assert!(!telegram_acknowledged(br#"{"ok":false}"#));
    }

    struct HeldTransport {
        state: Arc<(Mutex<bool>, Condvar)>,
        calls: AtomicU32,
    }

    impl Transport for HeldTransport {
        fn available(&self) -> bool {
            true
        }

        fn post(&self, _bot_token: &str, _chat_id: &str) -> Result<u16, ()> {
            self.calls.fetch_add(1, Ordering::Relaxed);
            let (lock, wake) = &*self.state;
            let held = lock.lock().unwrap();
            let _ = wake
                .wait_timeout_while(held, Duration::from_secs(2), |released| !*released)
                .unwrap();
            Ok(204)
        }
    }

    #[test]
    fn disabling_cancels_queued_events_before_another_request() {
        let state = Arc::new((Mutex::new(false), Condvar::new()));
        let transport = Arc::new(HeldTransport {
            state: state.clone(),
            calls: AtomicU32::new(0),
        });
        let service = Service::with_transport(transport.clone());
        service
            .apply(Config {
                enabled: true,
                bot_token: "123:ABC".to_owned(),
                chat_id: "-100123".to_owned(),
            })
            .unwrap();
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
        service.apply(Config::default()).unwrap();
        let (lock, wake) = &*state;
        *lock.lock().unwrap() = true;
        wake.notify_all();
        thread::sleep(Duration::from_millis(150));
        assert_eq!(transport.calls.load(Ordering::Acquire), 1);
        assert_eq!(service.runtime.requests.load(Ordering::Acquire), 1);
        assert_eq!(service.runtime.queued.load(Ordering::Acquire), 0);
    }

    #[test]
    fn endpoint_change_discards_an_event_queued_for_the_old_generation() {
        let state = Arc::new((Mutex::new(false), Condvar::new()));
        let transport = Arc::new(HeldTransport {
            state: state.clone(),
            calls: AtomicU32::new(0),
        });
        let service = Service::with_transport(transport.clone());
        service
            .apply(Config {
                enabled: true,
                bot_token: "123:OLD".to_owned(),
                chat_id: "-100123".to_owned(),
            })
            .unwrap();
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
        service
            .apply(Config {
                enabled: true,
                bot_token: "123:NEW".to_owned(),
                chat_id: "-100456".to_owned(),
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
        assert_eq!(service.runtime.queued.load(Ordering::Acquire), 0);
        assert_eq!(service.runtime.queue_dropped.load(Ordering::Acquire), 1);
    }
}
