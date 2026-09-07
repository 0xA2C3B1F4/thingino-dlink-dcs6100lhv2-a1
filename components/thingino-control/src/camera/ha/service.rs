//! HA request/status facade and shared state. Worker ownership, MQTT sessions,
//! and publication are implemented in separate private modules.
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
#[cfg(test)]
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use super::super::{PrudyntBackend, json_response, number, object};
use super::motion::MotionSlot;
use crate::json::{self, Value};
use crate::{BackendError, BackendResponse};

mod lifecycle;
mod publication;
mod session;

use lifecycle::WorkerControl;

pub(super) const EVENT_QUEUE_CAPACITY: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Request {
    Reconnect,
    RepublishDiscovery,
    PublishState,
    Motion,
}

#[derive(Clone, Debug)]
struct RuntimeState {
    enabled: bool,
    state: &'static str,
    connected: bool,
    last_connect_unix: Option<u64>,
    last_disconnect_unix: Option<u64>,
    last_error: Option<&'static str>,
    reconnect_at: Option<Instant>,
    published_messages: u64,
    received_commands: u64,
    rejected_commands: u64,
    dropped_messages: u64,
}

impl Default for RuntimeState {
    fn default() -> Self {
        Self {
            enabled: false,
            state: "disabled",
            connected: false,
            last_connect_unix: None,
            last_disconnect_unix: None,
            last_error: None,
            reconnect_at: None,
            published_messages: 0,
            received_commands: 0,
            rejected_commands: 0,
            dropped_messages: 0,
        }
    }
}

pub(in crate::camera) struct HaService {
    backend: Mutex<Weak<PrudyntBackend>>,
    worker: Mutex<Option<WorkerControl>>,
    runtime: Arc<Mutex<RuntimeState>>,
    queue_depth: Arc<AtomicUsize>,
    queue_high_water: Arc<AtomicUsize>,
    config_generation: Arc<AtomicUsize>,
    accept_motion: Arc<AtomicBool>,
    motion_slot: Arc<MotionSlot>,
    #[cfg(test)]
    test_hooks: HaTestHooks,
}

#[cfg(test)]
struct TestBarrier {
    reached: mpsc::Sender<()>,
    release: Receiver<()>,
}

#[cfg(test)]
#[derive(Default)]
struct HaTestHooks {
    fail_next_spawn: AtomicBool,
    start_barrier: Mutex<Option<TestBarrier>>,
    pre_exit_barrier: Mutex<Option<TestBarrier>>,
    exit_barrier: Mutex<Option<TestBarrier>>,
}

impl HaService {
    pub(in crate::camera) fn action(&self, body: &[u8]) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.len() != 1 {
            return Err(BackendError::Protocol);
        }
        let action = fields
            .get("action")
            .and_then(Value::as_str)
            .ok_or(BackendError::Protocol)?;
        let request = match action {
            "reconnect" => Request::Reconnect,
            "republish_discovery" => Request::RepublishDiscovery,
            "publish_state" => Request::PublishState,
            _ => return Err(BackendError::Protocol),
        };
        self.enqueue(request)?;
        json_response(object([
            ("status", Value::String("accepted".to_owned())),
            ("action", Value::String(action.to_owned())),
        ]))
    }

    pub(in crate::camera) fn runtime(&self) -> Result<BackendResponse, BackendError> {
        let runtime = self
            .runtime
            .lock()
            .map_err(|_| BackendError::Unavailable)?
            .clone();
        let reconnect_in_ms = runtime.reconnect_at.map(|deadline| {
            u64::try_from(
                deadline
                    .saturating_duration_since(Instant::now())
                    .as_millis(),
            )
            .unwrap_or(u64::MAX)
        });
        json_response(object([
            ("enabled", Value::Bool(runtime.enabled)),
            ("state", Value::String(runtime.state.to_owned())),
            ("connected", Value::Bool(runtime.connected)),
            (
                "last_connect_unix",
                optional_number(runtime.last_connect_unix),
            ),
            (
                "last_disconnect_unix",
                optional_number(runtime.last_disconnect_unix),
            ),
            (
                "last_error",
                runtime
                    .last_error
                    .map(|value| Value::String(value.to_owned()))
                    .unwrap_or(Value::Null),
            ),
            ("reconnect_in_ms", optional_number(reconnect_in_ms)),
            (
                "queue_depth",
                number(self.queue_depth.load(Ordering::Acquire) as u64),
            ),
            (
                "queue_high_water_mark",
                number(self.queue_high_water.load(Ordering::Acquire) as u64),
            ),
            ("published_messages", number(runtime.published_messages)),
            ("received_commands", number(runtime.received_commands)),
            ("rejected_commands", number(runtime.rejected_commands)),
            ("dropped_messages", number(runtime.dropped_messages)),
        ]))
    }
}

fn optional_number(value: Option<u64>) -> Value {
    value.map(number).unwrap_or(Value::Null)
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0)
}

fn update_runtime(runtime: &Arc<Mutex<RuntimeState>>, update: impl FnOnce(&mut RuntimeState)) {
    if let Ok(mut state) = runtime.lock() {
        update(&mut state);
    }
}

fn clear_command_error(state: &mut RuntimeState) {
    if matches!(
        state.last_error,
        Some("camera command failed" | "camera command state readback failed")
    ) {
        state.last_error = None;
    }
}

#[cfg(test)]
#[path = "service_tests.rs"]
mod tests;
