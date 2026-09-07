//! Motion service state and camera-facing API.
//!
//! The service retains ownership of every lock and thread handle. Lifecycle,
//! event dispatch and persisted clips operate on that same state without
//! introducing another owner or changing lock acquisition order.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, RwLock};
use std::thread::JoinHandle;
use std::time::{SystemTime, UNIX_EPOCH};

use super::motion_events::{MotionEventSink, MotionState};
use super::motion_queue::MotionQueue;
use super::motion_state::{MotionStateMachine, motion_config_from_document};
use super::{BackendError, BackendResponse, CameraPaths, number, object, read_bounded};
use crate::json::{self, Value};

mod clips;
mod dispatch;
mod lifecycle;

const EVENT_QUEUE_CAPACITY: usize = 32;
const MAX_PENDING_CLIPS: usize = 4;
const FILE_LIMIT: u64 = 64 * 1024;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum MotionLifecycleState {
    Running,
    Stopping,
    #[default]
    Stopped,
}

#[derive(Debug, Default)]
struct MotionLifecycle {
    state: MotionLifecycleState,
    receiver: Option<JoinHandle<()>>,
    worker: Option<JoinHandle<()>>,
}

#[cfg(test)]
#[derive(Debug, Default)]
struct MotionTestHooks {
    fail_next_worker_spawn: AtomicBool,
    fail_next_receive: AtomicBool,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) struct MotionRuntimeSnapshot {
    pub ingress_ready: bool,
    pub ingress_fatal_exits: u64,
    pub monitoring: bool,
    pub active: bool,
    pub channel: u8,
    pub producer_pid: Option<u32>,
    pub producer_sequence: Option<u64>,
    pub last_observation_monotonic_ms: Option<u64>,
    pub last_transition_unix_ms: Option<u64>,
    pub received: u64,
    pub rejected: u64,
    pub accepted: u64,
    pub duplicate_or_replayed: u64,
    pub producer_restarts: u64,
    pub transitions: u64,
    pub sink_queued: u64,
    pub sink_coalesced: u64,
    pub sink_dropped: u64,
    pub sink_disabled: u64,
    pub speaker_queued: u64,
    pub speaker_dropped: u64,
    pub clips_requested: u64,
    pub clips_ready: u64,
    pub clips_dropped: u64,
    pub clips_recovered: u64,
    pub clip_manifest_errors: u64,
    pub unsupported_destination_events: u64,
}

#[derive(Debug)]
struct PendingClip {
    id: String,
    partial_path: PathBuf,
    final_path: PathBuf,
    channel: u8,
    created_unix_ms: u64,
    deadline_unix_ms: u64,
}

pub(super) struct MotionService {
    paths: CameraPaths,
    queue: Arc<MotionQueue>,
    machine: Mutex<MotionStateMachine>,
    runtime: Mutex<MotionRuntimeSnapshot>,
    pending_clips: Mutex<VecDeque<PendingClip>>,
    sink: RwLock<Arc<dyn MotionEventSink>>,
    next_event_sequence: Mutex<u64>,
    lifecycle: Mutex<MotionLifecycle>,
    shutdown_requested: AtomicBool,
    #[cfg(test)]
    test_hooks: MotionTestHooks,
}

impl std::fmt::Debug for MotionService {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let lifecycle = self.lock_lifecycle();
        formatter
            .debug_struct("MotionService")
            .field("socket", &self.paths.motion_event_socket)
            .field("lifecycle", &lifecycle.state)
            .finish_non_exhaustive()
    }
}

impl MotionService {
    pub(super) fn with_sink(paths: CameraPaths, sink: Arc<dyn MotionEventSink>) -> Arc<Self> {
        let recovered_active = paths.motion_alarm.is_file() || paths.motion_detected.is_file();
        let config = read_bounded(&paths.prudynt_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok())
            .map(|document| motion_config_from_document(&document))
            .unwrap_or_default();
        Arc::new(Self {
            paths,
            queue: Arc::new(MotionQueue::new(EVENT_QUEUE_CAPACITY)),
            machine: Mutex::new(MotionStateMachine::new(config, recovered_active)),
            runtime: Mutex::new(MotionRuntimeSnapshot {
                active: recovered_active,
                ..MotionRuntimeSnapshot::default()
            }),
            pending_clips: Mutex::new(VecDeque::new()),
            sink: RwLock::new(sink),
            next_event_sequence: Mutex::new(0),
            lifecycle: Mutex::new(MotionLifecycle::default()),
            shutdown_requested: AtomicBool::new(false),
            #[cfg(test)]
            test_hooks: MotionTestHooks::default(),
        })
    }

    fn lock_lifecycle(&self) -> std::sync::MutexGuard<'_, MotionLifecycle> {
        self.lifecycle
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }

    #[allow(dead_code)]
    pub(super) fn set_sink(&self, sink: Arc<dyn MotionEventSink>) {
        *self.sink.write().unwrap_or_else(|error| error.into_inner()) = sink;
        self.publish_current_state(if self.snapshot().active {
            MotionState::Active
        } else {
            MotionState::Inactive
        });
        self.publish_current_state(MotionState::Resync);
    }

    pub(super) fn snapshot(&self) -> MotionRuntimeSnapshot {
        *self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }

    pub(super) fn runtime_response(&self) -> Result<BackendResponse, BackendError> {
        let runtime = self.snapshot();
        let (queue_depth, queue_high_water, queue_dropped, queue_coalesced) = self.queue.snapshot();
        let pending_clips = self
            .pending_clips
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .len();
        super::json_response(object([
            ("version", number(1)),
            ("ingress_ready", Value::Bool(runtime.ingress_ready)),
            ("ingress_fatal_exits", number(runtime.ingress_fatal_exits)),
            ("monitoring", Value::Bool(runtime.monitoring)),
            ("active", Value::Bool(runtime.active)),
            ("channel", number(u64::from(runtime.channel))),
            (
                "producer_pid",
                runtime
                    .producer_pid
                    .map_or(Value::Null, |value| number(u64::from(value))),
            ),
            (
                "producer_sequence",
                runtime.producer_sequence.map_or(Value::Null, number),
            ),
            (
                "last_observation_monotonic_ms",
                runtime
                    .last_observation_monotonic_ms
                    .map_or(Value::Null, number),
            ),
            (
                "last_transition_unix_ms",
                runtime.last_transition_unix_ms.map_or(Value::Null, number),
            ),
            (
                "queue",
                object([
                    ("capacity", number(EVENT_QUEUE_CAPACITY as u64)),
                    ("depth", number(queue_depth as u64)),
                    ("high_water", number(queue_high_water as u64)),
                    ("dropped", number(queue_dropped)),
                    ("coalesced", number(queue_coalesced)),
                ]),
            ),
            ("received", number(runtime.received)),
            ("rejected", number(runtime.rejected)),
            ("accepted", number(runtime.accepted)),
            (
                "duplicate_or_replayed",
                number(runtime.duplicate_or_replayed),
            ),
            ("producer_restarts", number(runtime.producer_restarts)),
            ("transitions", number(runtime.transitions)),
            (
                "sink",
                object([
                    ("queued", number(runtime.sink_queued)),
                    ("coalesced", number(runtime.sink_coalesced)),
                    ("dropped", number(runtime.sink_dropped)),
                    ("disabled", number(runtime.sink_disabled)),
                ]),
            ),
            (
                "speaker",
                object([
                    ("queued", number(runtime.speaker_queued)),
                    ("dropped", number(runtime.speaker_dropped)),
                ]),
            ),
            (
                "clips",
                object([
                    ("capacity", number(MAX_PENDING_CLIPS as u64)),
                    ("pending", number(pending_clips as u64)),
                    ("requested", number(runtime.clips_requested)),
                    ("ready", number(runtime.clips_ready)),
                    ("dropped", number(runtime.clips_dropped)),
                    ("recovered", number(runtime.clips_recovered)),
                    ("manifest_errors", number(runtime.clip_manifest_errors)),
                ]),
            ),
            (
                "unsupported_destination_events",
                number(runtime.unsupported_destination_events),
            ),
        ]))
    }
}

fn unix_milliseconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or(0)
}

#[cfg(test)]
#[path = "motion_tests.rs"]
mod tests;
