use std::collections::VecDeque;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{FileTypeExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::net::UnixDatagram;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::json::{self, Value};

use super::motion_datagram::{MAX_INGRESS_BYTES, MotionObservation, number_u64, parse_observation};
#[cfg(test)]
use super::motion_datagram::{ObservationState, PROTOCOL_VERSION};
use super::motion_events::{
    MOTION_EVENT_VERSION, MediaArtifact, MediaArtifactKind, MotionEvent, MotionEventDisposition,
    MotionEventSink, MotionState,
};
use super::motion_queue::MotionQueue;
#[cfg(test)]
use super::motion_queue::QueuePushResult;
#[cfg(test)]
use super::motion_state::MotionConfig;
use super::motion_state::{MotionStateMachine, MotionTransition, motion_config_from_document};
use super::storage::{mounted_writable, safe_mount_path, safe_storage_component};
use super::{BackendError, BackendResponse, CameraPaths, number, object, read_bounded, write_fifo};

const EVENT_QUEUE_CAPACITY: usize = 32;
const MAX_PENDING_CLIPS: usize = 4;
const FILE_LIMIT: u64 = 64 * 1024;
const CLIP_MANIFEST_LIMIT: u64 = 16 * 1024;
const RECEIVE_SHUTDOWN_POLL: Duration = Duration::from_millis(50);

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

    pub(super) fn start(self: &Arc<Self>) -> io::Result<()> {
        let mut lifecycle = self.lock_lifecycle();
        if lifecycle.state == MotionLifecycleState::Running {
            return Ok(());
        }
        if lifecycle.state == MotionLifecycleState::Stopping {
            if lifecycle.receiver.is_none() && lifecycle.worker.is_none() {
                return Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "motion lifecycle transition is already in progress",
                ));
            }
            let receiver = lifecycle.receiver.take();
            let worker = lifecycle.worker.take();
            drop(lifecycle);
            let join_result = join_motion_threads(receiver, worker);
            lifecycle = self.lock_lifecycle();
            lifecycle.state = MotionLifecycleState::Stopped;
            join_result?;
        }

        self.shutdown_requested.store(false, Ordering::Release);
        self.queue.reset_for_start();
        self.runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .ingress_ready = false;

        let socket = match bind_ingress_socket(&self.paths.motion_event_socket).and_then(|socket| {
            socket.set_read_timeout(Some(RECEIVE_SHUTDOWN_POLL))?;
            Ok(socket)
        }) {
            Ok(socket) => socket,
            Err(error) => {
                lifecycle.state = MotionLifecycleState::Stopped;
                self.signal_shutdown();
                return Err(error);
            }
        };
        self.recover_pending_clips();
        self.poll_media_lifecycle();

        let receiver = Arc::clone(self);
        let receiver_handle = match thread::Builder::new()
            .name("control-motion-rx".to_owned())
            .spawn(move || {
                if receiver.receive_loop(socket).is_err() {
                    receiver.receive_failed();
                }
            }) {
            Ok(handle) => handle,
            Err(error) => {
                lifecycle.state = MotionLifecycleState::Stopped;
                self.signal_shutdown();
                return Err(error);
            }
        };
        lifecycle.receiver = Some(receiver_handle);

        let worker = Arc::clone(self);
        let worker_handle = match self.spawn_worker(move || {
            worker.worker_loop();
        }) {
            Ok(handle) => handle,
            Err(spawn_error) => {
                lifecycle.state = MotionLifecycleState::Stopping;
                self.signal_shutdown();
                let receiver_handle = lifecycle.receiver.take();
                drop(lifecycle);
                let rollback_result = join_motion_threads(receiver_handle, None);
                let mut lifecycle = self.lock_lifecycle();
                lifecycle.state = MotionLifecycleState::Stopped;
                return match rollback_result {
                    Ok(()) => Err(spawn_error),
                    Err(join_error) => Err(io::Error::other(format!(
                        "motion worker spawn failed: {spawn_error}; receiver rollback join failed: {join_error}"
                    ))),
                };
            }
        };
        lifecycle.worker = Some(worker_handle);
        self.apply_state_files(self.snapshot().active);
        self.publish_current_state(if self.snapshot().active {
            MotionState::Active
        } else {
            MotionState::Inactive
        });
        self.runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .ingress_ready = true;
        lifecycle.state = MotionLifecycleState::Running;
        Ok(())
    }

    #[allow(dead_code)]
    pub(super) fn shutdown(&self) -> io::Result<()> {
        let mut lifecycle = self.lock_lifecycle();
        if lifecycle.state == MotionLifecycleState::Stopped {
            return Ok(());
        }
        if lifecycle.receiver.is_none() && lifecycle.worker.is_none() {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "motion lifecycle transition is already in progress",
            ));
        }

        lifecycle.state = MotionLifecycleState::Stopping;
        self.signal_shutdown();
        let receiver = lifecycle.receiver.take();
        let worker = lifecycle.worker.take();
        drop(lifecycle);

        let join_result = join_motion_threads(receiver, worker);
        let mut lifecycle = self.lock_lifecycle();
        lifecycle.state = MotionLifecycleState::Stopped;
        join_result
    }

    fn signal_shutdown(&self) {
        self.shutdown_requested.store(true, Ordering::Release);
        self.queue.shutdown();
        self.runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .ingress_ready = false;
    }

    fn spawn_worker<F>(&self, task: F) -> io::Result<JoinHandle<()>>
    where
        F: FnOnce() + Send + 'static,
    {
        #[cfg(test)]
        if self
            .test_hooks
            .fail_next_worker_spawn
            .swap(false, Ordering::AcqRel)
        {
            return Err(io::Error::other("injected motion worker spawn failure"));
        }
        thread::Builder::new()
            .name("control-motion".to_owned())
            .spawn(task)
    }

    fn receive_failed(&self) {
        let mut lifecycle = self.lock_lifecycle();
        if lifecycle.state == MotionLifecycleState::Running {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.ingress_fatal_exits = runtime.ingress_fatal_exits.saturating_add(1);
            drop(runtime);
            lifecycle.state = MotionLifecycleState::Stopping;
            self.signal_shutdown();
        }
    }

    fn receive_loop(&self, socket: UnixDatagram) -> io::Result<()> {
        let mut buffer = [0_u8; MAX_INGRESS_BYTES + 1];
        loop {
            if self.shutdown_requested.load(Ordering::Acquire) {
                return Ok(());
            }
            #[cfg(test)]
            if self
                .test_hooks
                .fail_next_receive
                .swap(false, Ordering::AcqRel)
            {
                return Err(io::Error::new(
                    io::ErrorKind::ConnectionAborted,
                    "injected motion receive failure",
                ));
            }
            match socket.recv(&mut buffer) {
                Ok(length) => {
                    let parsed = parse_observation(&buffer[..length]);
                    let mut runtime = self
                        .runtime
                        .lock()
                        .unwrap_or_else(|error| error.into_inner());
                    runtime.received = runtime.received.saturating_add(1);
                    drop(runtime);
                    match parsed {
                        Ok(observation) => {
                            let _ = self.queue.push(observation);
                        }
                        Err(()) => {
                            let mut runtime = self
                                .runtime
                                .lock()
                                .unwrap_or_else(|error| error.into_inner());
                            runtime.rejected = runtime.rejected.saturating_add(1);
                        }
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                    ) => {}
                Err(error) => return Err(error),
            }
        }
    }

    fn worker_loop(&self) {
        while let Some(observation) = self.queue.pop() {
            self.process(observation);
        }
    }

    fn process(&self, observation: MotionObservation) {
        let transition = {
            let mut machine = self
                .machine
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            let transition = machine.apply(observation);
            let counters = machine.counters();
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.monitoring = machine.monitoring();
            runtime.active = machine.active();
            runtime.channel = observation.channel;
            runtime.producer_pid = Some(observation.producer_pid);
            runtime.producer_sequence = Some(observation.sequence);
            runtime.last_observation_monotonic_ms = Some(observation.monotonic_ms);
            runtime.accepted = counters.accepted;
            runtime.duplicate_or_replayed = counters.duplicate_or_replayed;
            runtime.producer_restarts = counters.producer_restarts;
            transition
        };
        let Some(transition) = transition else {
            return;
        };
        self.finish_transition(transition, observation.channel, observation.monotonic_ms);
    }

    fn finish_transition(&self, transition: MotionTransition, channel: u8, monotonic_ms: u64) {
        let active = transition == MotionTransition::Active;
        self.apply_state_files(active);
        let unix_ms = unix_milliseconds();
        {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.last_transition_unix_ms = Some(unix_ms);
            runtime.transitions = runtime.transitions.saturating_add(1);
        }
        self.publish(
            if active {
                MotionState::Active
            } else {
                MotionState::Inactive
            },
            channel,
            monotonic_ms,
            Some(unix_ms),
        );
        if active {
            self.dispatch_active_actions(channel);
        }
    }

    fn publish_current_state(&self, requested: MotionState) {
        let runtime = self.snapshot();
        let state = match requested {
            MotionState::Resync => MotionState::Resync,
            _ if runtime.active => MotionState::Active,
            _ => MotionState::Inactive,
        };
        self.publish(
            state,
            runtime.channel,
            runtime.last_observation_monotonic_ms.unwrap_or(0),
            Some(unix_milliseconds()),
        );
    }

    fn publish(
        &self,
        state: MotionState,
        channel: u8,
        monotonic_ms: u64,
        occurred_unix_ms: Option<u64>,
    ) {
        self.publish_with_artifacts(state, channel, monotonic_ms, occurred_unix_ms, None, None);
    }

    fn publish_with_artifacts(
        &self,
        state: MotionState,
        channel: u8,
        monotonic_ms: u64,
        occurred_unix_ms: Option<u64>,
        snapshot: Option<MediaArtifact>,
        clip: Option<MediaArtifact>,
    ) {
        let sequence = {
            let mut sequence = self
                .next_event_sequence
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            *sequence = sequence.saturating_add(1);
            *sequence
        };
        let event = MotionEvent {
            version: MOTION_EVENT_VERSION,
            sequence,
            state,
            channel,
            monotonic_ms,
            occurred_unix_ms,
            snapshot,
            clip,
        };
        if !event.has_bounded_metadata() {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.sink_dropped = runtime.sink_dropped.saturating_add(1);
            return;
        }
        let disposition = self
            .sink
            .read()
            .unwrap_or_else(|error| error.into_inner())
            .try_send_motion(event);
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        match disposition {
            MotionEventDisposition::Queued => {
                runtime.sink_queued = runtime.sink_queued.saturating_add(1)
            }
            MotionEventDisposition::Coalesced => {
                runtime.sink_coalesced = runtime.sink_coalesced.saturating_add(1)
            }
            MotionEventDisposition::Dropped => {
                runtime.sink_dropped = runtime.sink_dropped.saturating_add(1)
            }
            MotionEventDisposition::Disabled => {
                runtime.sink_disabled = runtime.sink_disabled.saturating_add(1)
            }
        }
    }

    fn apply_state_files(&self, active: bool) {
        for path in [&self.paths.motion_alarm, &self.paths.motion_detected] {
            if active {
                let _ = create_state_file(path);
            } else if let Err(error) = fs::remove_file(path)
                && error.kind() != io::ErrorKind::NotFound
            {
                // Runtime counters intentionally remain bounded and do not retain
                // error strings or path data.
            }
        }
    }

    fn dispatch_active_actions(&self, channel: u8) {
        let prudynt = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok());
        let Some(prudynt) = prudynt else {
            return;
        };
        if prudynt
            .get_path("motion.playonspeaker")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            self.dispatch_speaker();
        }
        if prudynt
            .get_path("motion.send2storage")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            self.dispatch_storage_clip(&prudynt, channel);
        }
        let unsupported = [
            "send2email",
            "send2ftp",
            "send2gotify",
            "send2ntfy",
            "send2telegram",
            "send2webhook",
        ]
        .iter()
        .filter(|flag| {
            prudynt
                .get_path(&format!("motion.{flag}"))
                .and_then(Value::as_bool)
                .unwrap_or(false)
        })
        .count() as u64;
        if unsupported > 0 {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.unsupported_destination_events = runtime
                .unsupported_destination_events
                .saturating_add(unsupported);
        }
    }

    fn dispatch_storage_clip(&self, prudynt: &Value, channel: u8) {
        let send = read_bounded(&self.paths.send2_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok());
        let Some(send) = send else {
            self.record_clip(false, false);
            return;
        };
        if send
            .get_path("storage.send_photo")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.unsupported_destination_events =
                runtime.unsupported_destination_events.saturating_add(1);
        }
        if !send
            .get_path("storage.send_video")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            return;
        }
        let mount = send
            .get_path("storage.mount")
            .and_then(Value::as_str)
            .unwrap_or("");
        let directory = send
            .get_path("storage.device_path")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !safe_mount_path(mount)
            || (!directory.is_empty() && !safe_storage_component(directory))
            || !mounted_writable(&self.paths.proc_mounts, Path::new(mount))
        {
            self.record_clip(false, false);
            return;
        }
        let destination = match secure_storage_directory(Path::new(mount), directory) {
            Ok(path) => path,
            Err(_) => {
                self.record_clip(false, false);
                return;
            }
        };
        let created_unix_ms = unix_milliseconds();
        let event_sequence = self
            .next_event_sequence
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .saturating_add(1);
        let id = format!("motion-{created_unix_ms}-{event_sequence}");
        let final_path = destination.join(format!("{id}.mp4"));
        let partial_path = destination.join(format!(".{id}.partial"));
        if final_path.exists() || partial_path.exists() {
            self.record_clip(false, false);
            return;
        }
        let duration = prudynt
            .get_path("motion.video_length")
            .and_then(number_u64)
            .unwrap_or(10)
            .clamp(1, 86_400);
        let mut pending = self
            .pending_clips
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if pending.len() >= MAX_PENDING_CLIPS {
            drop(pending);
            self.record_clip(false, false);
            return;
        }
        let command = format!(
            "START path={} dur={duration} ch={channel} loop=0\n",
            partial_path.to_string_lossy()
        );
        if command.len() >= 512 || write_fifo(&self.paths.recorder_ctl, command.as_bytes()).is_err()
        {
            drop(pending);
            self.record_clip(false, false);
            return;
        }
        pending.push_back(PendingClip {
            id,
            partial_path,
            final_path,
            channel,
            created_unix_ms,
            deadline_unix_ms: created_unix_ms
                .saturating_add(duration.saturating_add(30).saturating_mul(1_000)),
        });
        if self.persist_pending_clips(&pending).is_err() {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.clip_manifest_errors = runtime.clip_manifest_errors.saturating_add(1);
        }
        drop(pending);
        self.record_clip(true, false);
    }

    fn record_clip(&self, requested: bool, ready: bool) {
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if requested {
            runtime.clips_requested = runtime.clips_requested.saturating_add(1);
        } else if ready {
            runtime.clips_ready = runtime.clips_ready.saturating_add(1);
        } else {
            runtime.clips_dropped = runtime.clips_dropped.saturating_add(1);
        }
    }

    pub(super) fn poll_media_lifecycle(&self) {
        let now = unix_milliseconds();
        let mut ready = Vec::new();
        let mut dropped = 0_u64;
        {
            let mut pending = self
                .pending_clips
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            let mut remaining = VecDeque::with_capacity(pending.len());
            while let Some(clip) = pending.pop_front() {
                let active_path = if clip.channel == 0 {
                    &self.paths.recorder_ch0_active
                } else {
                    &self.paths.recorder_ch1_active
                };
                if active_path.exists() && now < clip.deadline_unix_ms {
                    remaining.push_back(clip);
                    continue;
                }
                let completed = clip
                    .partial_path
                    .symlink_metadata()
                    .ok()
                    .filter(|metadata| {
                        metadata.is_file()
                            && !metadata.file_type().is_symlink()
                            && metadata.len() > 0
                    });
                if let Some(metadata) = completed
                    && !clip.final_path.exists()
                    && fs::rename(&clip.partial_path, &clip.final_path).is_ok()
                {
                    ready.push((clip, metadata.len()));
                    continue;
                }
                if now < clip.deadline_unix_ms {
                    remaining.push_back(clip);
                } else {
                    let _ = fs::remove_file(&clip.partial_path);
                    dropped = dropped.saturating_add(1);
                }
            }
            *pending = remaining;
            if self.persist_pending_clips(&pending).is_err() {
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.clip_manifest_errors = runtime.clip_manifest_errors.saturating_add(1);
            }
        }
        if dropped > 0 {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.clips_dropped = runtime.clips_dropped.saturating_add(dropped);
        }
        for (clip, size_bytes) in ready {
            self.record_clip(false, true);
            self.publish_with_artifacts(
                MotionState::Resync,
                clip.channel,
                self.snapshot().last_observation_monotonic_ms.unwrap_or(0),
                Some(unix_milliseconds()),
                None,
                Some(MediaArtifact {
                    kind: MediaArtifactKind::Clip,
                    id: clip.id,
                    local_path: Some(clip.final_path),
                    content_type: "video/mp4".to_owned(),
                    size_bytes,
                    created_unix_ms: clip.created_unix_ms,
                }),
            );
        }
    }

    fn recover_pending_clips(&self) {
        let Some(destination) = self.storage_destination() else {
            if self.paths.motion_clip_manifest.exists() {
                self.record_manifest_error();
            }
            return;
        };
        self.recover_pending_clips_from(&destination);
    }

    fn recover_pending_clips_from(&self, destination: &Path) {
        let Ok(bytes) = read_bounded(&self.paths.motion_clip_manifest, CLIP_MANIFEST_LIMIT) else {
            return;
        };
        let Ok(document) = json::parse(&bytes) else {
            self.record_manifest_error();
            return;
        };
        if document.get_path("version").and_then(number_u64) != Some(1) {
            self.record_manifest_error();
            return;
        }
        let Some(clips) = document.get_path("clips").and_then(Value::as_array) else {
            self.record_manifest_error();
            return;
        };
        if clips.len() > MAX_PENDING_CLIPS {
            self.record_manifest_error();
            return;
        }
        let now = unix_milliseconds();
        let mut recovered = VecDeque::with_capacity(clips.len());
        for value in clips {
            let Some(clip) = parse_pending_clip(value, destination, now, &self.paths) else {
                self.record_manifest_error();
                return;
            };
            recovered.push_back(clip);
        }
        let recovered_count = recovered.len() as u64;
        *self
            .pending_clips
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = recovered;
        if recovered_count > 0 {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.clips_recovered = runtime.clips_recovered.saturating_add(recovered_count);
        }
    }

    fn persist_pending_clips(&self, clips: &VecDeque<PendingClip>) -> io::Result<()> {
        if clips.is_empty() {
            return match fs::remove_file(&self.paths.motion_clip_manifest) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
                Err(error) => Err(error),
            };
        }
        let manifest = object([
            ("version", number(1)),
            (
                "clips",
                Value::Array(
                    clips
                        .iter()
                        .map(|clip| {
                            object([
                                ("id", Value::String(clip.id.clone())),
                                (
                                    "partial_path",
                                    Value::String(clip.partial_path.to_string_lossy().into_owned()),
                                ),
                                (
                                    "final_path",
                                    Value::String(clip.final_path.to_string_lossy().into_owned()),
                                ),
                                ("channel", number(u64::from(clip.channel))),
                                ("created_unix_ms", number(clip.created_unix_ms)),
                                ("deadline_unix_ms", number(clip.deadline_unix_ms)),
                            ])
                        })
                        .collect(),
                ),
            ),
        ]);
        let mut bytes = manifest.to_json().into_bytes();
        bytes.push(b'\n');
        if bytes.len() as u64 > CLIP_MANIFEST_LIMIT {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "motion clip manifest exceeds limit",
            ));
        }
        atomic_write_private(&self.paths.motion_clip_manifest, &bytes)
    }

    fn storage_destination(&self) -> Option<PathBuf> {
        let send = read_bounded(&self.paths.send2_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok())?;
        let mount = send.get_path("storage.mount").and_then(Value::as_str)?;
        let directory = send
            .get_path("storage.device_path")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !safe_mount_path(mount)
            || (!directory.is_empty() && !safe_storage_component(directory))
            || !mounted_writable(&self.paths.proc_mounts, Path::new(mount))
        {
            return None;
        }
        secure_storage_directory(Path::new(mount), directory).ok()
    }

    fn record_manifest_error(&self) {
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        runtime.clip_manifest_errors = runtime.clip_manifest_errors.saturating_add(1);
    }

    fn dispatch_speaker(&self) {
        let config = read_bounded(&self.paths.send2_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok());
        let Some(config) = config else {
            self.record_speaker(false);
            return;
        };
        let Some(path) = config
            .get_path("speaker.file")
            .and_then(Value::as_str)
            .filter(|path| safe_audio_path(path))
        else {
            self.record_speaker(false);
            return;
        };
        let volume = config
            .get_path("speaker.volume")
            .and_then(number_u64)
            .unwrap_or(80)
            .clamp(0, 100);
        let gain = config
            .get_path("speaker.gain")
            .and_then(number_u64)
            .unwrap_or(20)
            .clamp(0, 31);
        let loops = config
            .get_path("speaker.loop")
            .and_then(number_u64)
            .unwrap_or(1)
            .clamp(1, 32);
        let command = format!("PLAY url={path} vol={volume} gain={gain} loop={loops}\n");
        self.record_speaker(write_fifo(&self.paths.audio_output, command.as_bytes()).is_ok());
    }

    fn record_speaker(&self, queued: bool) {
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if queued {
            runtime.speaker_queued = runtime.speaker_queued.saturating_add(1);
        } else {
            runtime.speaker_dropped = runtime.speaker_dropped.saturating_add(1);
        }
    }

    pub(super) fn refresh_config(&self) {
        if let Ok(bytes) = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)
            && let Ok(document) = json::parse(&bytes)
        {
            self.machine
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .set_config(motion_config_from_document(&document));
        }
    }

    pub(super) fn reconcile_prudynt(&self, running: bool) {
        if running || (!self.snapshot().monitoring && !self.snapshot().active) {
            return;
        }
        let runtime = self.snapshot();
        let transition = {
            let mut machine = self
                .machine
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            let transition =
                machine.retire_producer(runtime.last_observation_monotonic_ms.unwrap_or(0));
            let counters = machine.counters();
            let mut current = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            current.monitoring = machine.monitoring();
            current.active = machine.active();
            current.accepted = counters.accepted;
            current.duplicate_or_replayed = counters.duplicate_or_replayed;
            current.producer_restarts = counters.producer_restarts;
            transition
        };
        if let Some(transition) = transition {
            self.finish_transition(
                transition,
                runtime.channel,
                runtime.last_observation_monotonic_ms.unwrap_or(0),
            );
        }
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

fn join_motion_threads(
    receiver: Option<JoinHandle<()>>,
    worker: Option<JoinHandle<()>>,
) -> io::Result<()> {
    let mut failed = None;
    for (role, handle) in [("receiver", receiver), ("worker", worker)] {
        if handle.is_some_and(|handle| handle.join().is_err()) && failed.is_none() {
            failed = Some(role);
        }
    }
    failed.map_or(Ok(()), |role| {
        Err(io::Error::other(format!(
            "motion {role} thread panicked during join"
        )))
    })
}

fn bind_ingress_socket(path: &Path) -> io::Result<UnixDatagram> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "motion socket has no parent")
        })?;
    fs::create_dir_all(parent)?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o755))?;
    match path.symlink_metadata() {
        Ok(metadata) if metadata.file_type().is_socket() => fs::remove_file(path)?,
        Ok(_) => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "motion socket path is not a socket",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let socket = UnixDatagram::bind(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(socket)
}

fn create_state_file(path: &Path) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "state path has no parent"))?;
    fs::create_dir_all(parent)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(b"1\n")
}

fn safe_audio_path(value: &str) -> bool {
    let path = PathBuf::from(value);
    value.len() <= 255
        && path.is_absolute()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_graphic() && !matches!(byte, b'=' | b'\\'))
        && path
            .symlink_metadata()
            .is_ok_and(|metadata| metadata.is_file() && !metadata.file_type().is_symlink())
}

fn secure_storage_directory(mount: &Path, relative: &str) -> io::Result<PathBuf> {
    let metadata = mount.symlink_metadata()?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "storage mount is not a directory",
        ));
    }
    let mut current = mount.to_path_buf();
    for component in relative
        .split('/')
        .filter(|component| !component.is_empty())
    {
        if matches!(component, "." | "..")
            || !component
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "unsafe storage component",
            ));
        }
        current.push(component);
        match current.symlink_metadata() {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "storage component is not a directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                fs::create_dir(&current)?;
                fs::set_permissions(&current, fs::Permissions::from_mode(0o750))?;
            }
            Err(error) => return Err(error),
        }
    }
    Ok(current)
}

fn parse_pending_clip(
    value: &Value,
    destination: &Path,
    now: u64,
    paths: &CameraPaths,
) -> Option<PendingClip> {
    let fields = value.as_object()?;
    let allowed = [
        "id",
        "partial_path",
        "final_path",
        "channel",
        "created_unix_ms",
        "deadline_unix_ms",
    ];
    if fields.len() != allowed.len() || fields.keys().any(|key| !allowed.contains(&key.as_str())) {
        return None;
    }
    let id = fields.get("id")?.as_str()?;
    if id.len() > 128
        || !id.starts_with("motion-")
        || !id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return None;
    }
    let partial_path = PathBuf::from(fields.get("partial_path")?.as_str()?);
    let final_path = PathBuf::from(fields.get("final_path")?.as_str()?);
    if partial_path.parent() != Some(destination)
        || final_path.parent() != Some(destination)
        || partial_path.file_name()?.to_str()? != format!(".{id}.partial")
        || final_path.file_name()?.to_str()? != format!("{id}.mp4")
        || final_path.exists()
    {
        return None;
    }
    let channel = u8::try_from(number_u64(fields.get("channel")?)?).ok()?;
    if channel > 1 {
        return None;
    }
    let active_path = if channel == 0 {
        &paths.recorder_ch0_active
    } else {
        &paths.recorder_ch1_active
    };
    let active_duration = recorder_active_duration(active_path, &partial_path);
    match partial_path.symlink_metadata() {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound && active_duration.is_some() => {}
        _ => return None,
    }
    let persisted_deadline = number_u64(fields.get("deadline_unix_ms")?)?;
    let deadline_unix_ms = active_duration.map_or(persisted_deadline, |duration| {
        persisted_deadline
            .max(now.saturating_add(duration.saturating_add(30).saturating_mul(1_000)))
    });
    Some(PendingClip {
        id: id.to_owned(),
        partial_path,
        final_path,
        channel,
        created_unix_ms: number_u64(fields.get("created_unix_ms")?)?,
        deadline_unix_ms,
    })
}

fn recorder_active_duration(active_path: &Path, expected_path: &Path) -> Option<u64> {
    let bytes = read_bounded(active_path, 1_024).ok()?;
    let text = std::str::from_utf8(&bytes).ok()?;
    let mut path = None;
    let mut duration = None;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("path=") {
            if path.replace(value).is_some() {
                return None;
            }
        } else if let Some(value) = line.strip_prefix("duration=") {
            if duration
                .replace(value.parse::<u64>().ok()?.clamp(1, 86_400))
                .is_some()
            {
                return None;
            }
        } else if !line.is_empty() {
            return None;
        }
    }
    (path? == expected_path.to_string_lossy()).then_some(duration.unwrap_or(10))
}

fn atomic_write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "manifest has no parent"))?;
    fs::create_dir_all(parent)?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o755))?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid manifest name"))?,
        std::process::id()
    ));
    if let Err(error) = fs::remove_file(&temporary)
        && error.kind() != io::ErrorKind::NotFound
    {
        return Err(error);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)?;
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    drop(file);
    if let Err(error) = fs::rename(&temporary, path) {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    Ok(())
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
