use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::camera::motion_datagram::{MotionObservation, ObservationState, PROTOCOL_VERSION};
use crate::camera::motion_events::{
    DisabledMotionEventSink, MOTION_EVENT_VERSION, MotionEvent, MotionEventSink, MotionState,
};
use crate::camera::motion_state::{MotionConfig, MotionStateMachine, MotionTransition};
use crate::{BackendError, RaptorBackend};

const POLL_INTERVAL: Duration = Duration::from_millis(500);
const READ_DEADLINE: Duration = Duration::from_millis(150);

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) struct Snapshot {
    pub running: bool,
    pub supported: bool,
    pub available: bool,
    pub monitoring: bool,
    pub receiving: bool,
    pub active: bool,
    pub sample_active: bool,
    pub polls: u64,
    pub failures: u64,
    pub transitions: u64,
    pub last_poll_monotonic_ms: Option<u64>,
}

pub(super) struct Service {
    machine: Mutex<MotionStateMachine>,
    runtime: Mutex<Snapshot>,
    sink: RwLock<Arc<dyn MotionEventSink>>,
    next_event_sequence: Mutex<u64>,
    shutdown: AtomicBool,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl Service {
    pub(super) fn new() -> Self {
        Self {
            machine: Mutex::new(MotionStateMachine::new(MotionConfig::default(), false)),
            runtime: Mutex::new(Snapshot::default()),
            sink: RwLock::new(Arc::new(DisabledMotionEventSink)),
            next_event_sequence: Mutex::new(0),
            shutdown: AtomicBool::new(false),
            worker: Mutex::new(None),
        }
    }

    pub(super) fn start(
        self: &Arc<Self>,
        backend: Weak<RaptorBackend>,
    ) -> Result<(), BackendError> {
        let mut worker = self.worker.lock().map_err(|_| BackendError::Unavailable)?;
        if worker.is_some() {
            return Ok(());
        }
        self.shutdown.store(false, Ordering::Release);
        let service = Arc::clone(self);
        let handle = thread::Builder::new()
            .name("control-motion-raptor".to_owned())
            .spawn(move || service.worker_loop(backend))
            .map_err(|_| BackendError::Unavailable)?;
        *worker = Some(handle);
        Ok(())
    }

    pub(super) fn set_config(&self, config: MotionConfig) {
        self.machine
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .set_config(config);
    }

    fn reset_for_producer(&self, config: MotionConfig) {
        *self
            .machine
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = MotionStateMachine::new(config, false);
    }

    pub(super) fn set_sink(&self, sink: Arc<dyn MotionEventSink>) {
        *self.sink.write().unwrap_or_else(|error| error.into_inner()) = sink;
    }

    pub(super) fn snapshot(&self) -> Snapshot {
        *self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }

    #[allow(dead_code)]
    pub(super) fn shutdown(&self) -> std::io::Result<()> {
        self.shutdown.store(true, Ordering::Release);
        let handle = self
            .worker
            .lock()
            .map_err(|_| std::io::Error::other("Raptor motion lifecycle lock is poisoned"))?
            .take();
        if let Some(handle) = handle {
            handle
                .join()
                .map_err(|_| std::io::Error::other("Raptor motion lifecycle worker panicked"))?;
        }
        Ok(())
    }

    fn worker_loop(self: &Arc<Self>, backend: Weak<RaptorBackend>) {
        let origin = Instant::now();
        let producer_pid = std::process::id();
        let mut sequence = 0_u64;
        let mut monitoring_since_ms = 0_u64;
        let mut previously_monitoring = false;
        let mut configured = false;
        let mut producer_reset_pending = false;
        let mut awaiting_producer_sample = true;
        let mut last_result = None;
        self.runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .running = true;
        while !self.shutdown.load(Ordering::Acquire) {
            let Some(backend) = backend.upgrade() else {
                break;
            };
            let now_ms = u64::try_from(origin.elapsed().as_millis()).unwrap_or(u64::MAX);
            if !configured {
                let config = backend
                    .motion_config_observation(Instant::now() + READ_DEADLINE)
                    .and_then(|reply| RaptorBackend::motion_lifecycle_config(&reply));
                match config {
                    Ok(config) => {
                        if producer_reset_pending {
                            self.reset_for_producer(config);
                            producer_reset_pending = false;
                            previously_monitoring = false;
                            monitoring_since_ms = now_ms;
                        } else {
                            self.set_config(config);
                        }
                        configured = true;
                    }
                    Err(_) => {
                        let mut runtime = self
                            .runtime
                            .lock()
                            .unwrap_or_else(|error| error.into_inner());
                        runtime.available = false;
                        runtime.failures = runtime.failures.saturating_add(1);
                        drop(runtime);
                        drop(backend);
                        self.wait_for_next_poll();
                        continue;
                    }
                }
            }
            sequence = sequence.saturating_add(1);
            let status = backend
                .motion_status(Instant::now() + READ_DEADLINE)
                .ok()
                .and_then(|reply| {
                    let flag = |name| {
                        reply
                            .value
                            .get_path(name)
                            .and_then(crate::json::Value::as_bool)
                    };
                    let supported = flag("supported")?;
                    let monitoring = flag("active")?;
                    let receiving = flag("receiving")?;
                    let result = match reply.value.get_path("move_result") {
                        Some(crate::json::Value::Null) | None => None,
                        Some(_) => {
                            let epoch = reply
                                .value
                                .get_path("move_result.epoch")
                                .and_then(|value| crate::raptor_backend::value_u64(value).ok())?;
                            let result_sequence = reply
                                .value
                                .get_path("move_result.sequence")
                                .and_then(|value| crate::raptor_backend::value_u64(value).ok())?;
                            let raw_motion = flag("move_result.raw_motion")?;
                            let timestamp = reply
                                .value
                                .get_path("move_result.timestamp")
                                .and_then(|value| crate::raptor_backend::value_u64(value).ok())?;
                            Some((epoch, result_sequence, raw_motion, timestamp))
                        }
                    };
                    Some((supported, monitoring, receiving, result))
                });
            let Some((supported, monitoring, receiving, result)) = status else {
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.available = false;
                runtime.polls = runtime.polls.saturating_add(1);
                runtime.failures = runtime.failures.saturating_add(1);
                runtime.last_poll_monotonic_ms = Some(now_ms);
                drop(runtime);
                drop(backend);
                self.wait_for_next_poll();
                continue;
            };
            let available = supported && monitoring == receiving;
            if !available {
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.supported = supported;
                runtime.available = false;
                runtime.receiving = receiving;
                runtime.polls = runtime.polls.saturating_add(1);
                runtime.failures = runtime.failures.saturating_add(1);
                runtime.last_poll_monotonic_ms = Some(now_ms);
                drop(runtime);
                drop(backend);
                self.wait_for_next_poll();
                continue;
            }
            let producer_changed = matches!(
                (last_result, result),
                (Some((previous_epoch, _)), Some((epoch, _, _, _)))
                    if previous_epoch != epoch
            );
            if producer_changed {
                let Some((epoch, result_sequence, _, _)) = result else {
                    unreachable!("producer change requires a result")
                };
                /* An RVD restart restores its saved configuration and starts a
                 * new result identity. Reload that configuration before any
                 * sample from the new producer can enter the state machine.
                 * The first coherent result is a baseline, never an event. */
                last_result = Some((epoch, result_sequence));
                awaiting_producer_sample = true;
                let config = backend
                    .motion_config_observation(Instant::now() + READ_DEADLINE)
                    .and_then(|reply| RaptorBackend::motion_lifecycle_config(&reply));
                let Ok(config) = config else {
                    configured = false;
                    producer_reset_pending = true;
                    let mut runtime = self
                        .runtime
                        .lock()
                        .unwrap_or_else(|error| error.into_inner());
                    runtime.available = false;
                    runtime.polls = runtime.polls.saturating_add(1);
                    runtime.failures = runtime.failures.saturating_add(1);
                    runtime.last_poll_monotonic_ms = Some(now_ms);
                    drop(runtime);
                    drop(backend);
                    self.wait_for_next_poll();
                    continue;
                };
                self.reset_for_producer(config);
                configured = true;
                monitoring_since_ms = now_ms;
                previously_monitoring = monitoring;
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.supported = supported;
                runtime.available = false;
                runtime.monitoring = monitoring;
                runtime.receiving = receiving;
                runtime.active = false;
                runtime.sample_active = false;
                runtime.polls = runtime.polls.saturating_add(1);
                runtime.last_poll_monotonic_ms = Some(now_ms);
                drop(runtime);
                drop(backend);
                self.wait_for_next_poll();
                continue;
            }
            let new_sample = if monitoring {
                result.and_then(|(epoch, result_sequence, raw_motion, _timestamp)| {
                    let identity = (epoch, result_sequence);
                    match last_result {
                        None => {
                            last_result = Some(identity);
                            None
                        }
                        Some((previous_epoch, previous_sequence))
                            if previous_epoch == epoch && result_sequence > previous_sequence =>
                        {
                            last_result = Some(identity);
                            Some(raw_motion)
                        }
                        Some((previous_epoch, previous_sequence))
                            if previous_epoch == epoch && result_sequence < previous_sequence =>
                        {
                            None
                        }
                        Some((previous_epoch, _)) if previous_epoch != epoch => {
                            last_result = Some(identity);
                            None
                        }
                        Some(_) => None,
                    }
                })
            } else {
                last_result = result.map(|(epoch, result_sequence, _, _)| (epoch, result_sequence));
                Some(false)
            };
            if monitoring && new_sample.is_none() {
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.supported = supported;
                runtime.available = !awaiting_producer_sample && result.is_some();
                runtime.monitoring = monitoring;
                runtime.receiving = receiving;
                runtime.polls = runtime.polls.saturating_add(1);
                runtime.last_poll_monotonic_ms = Some(now_ms);
                drop(runtime);
                drop(backend);
                self.wait_for_next_poll();
                continue;
            }
            awaiting_producer_sample = false;
            let sample_active = new_sample.unwrap_or(false);
            if monitoring && !previously_monitoring {
                monitoring_since_ms = now_ms;
            }
            previously_monitoring = monitoring;
            let observation = MotionObservation {
                version: PROTOCOL_VERSION,
                state: if !available || !monitoring {
                    ObservationState::Stopped
                } else if sample_active {
                    ObservationState::Detected
                } else {
                    ObservationState::Clear
                },
                channel: 1,
                monotonic_ms: now_ms,
                monitoring_since_ms,
                sequence,
                producer_pid,
                roi_mask: u64::from(sample_active),
                initial_grace: true,
            };
            let (active, transition) = {
                let mut machine = self
                    .machine
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                let transition = machine.apply(observation);
                (machine.active(), transition)
            };
            {
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.supported = supported;
                runtime.available = available;
                runtime.monitoring = monitoring;
                runtime.receiving = receiving;
                runtime.active = active;
                runtime.sample_active = sample_active;
                runtime.polls = runtime.polls.saturating_add(1);
                runtime.last_poll_monotonic_ms = Some(now_ms);
                if transition.is_some() {
                    runtime.transitions = runtime.transitions.saturating_add(1);
                }
            }
            if let Some(transition) = transition {
                self.publish(transition, now_ms);
            }
            drop(backend);
            self.wait_for_next_poll();
        }
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        runtime.running = false;
        runtime.supported = false;
        runtime.available = false;
        runtime.monitoring = false;
        runtime.receiving = false;
        runtime.active = false;
        runtime.sample_active = false;
    }

    fn wait_for_next_poll(&self) {
        let until = Instant::now() + POLL_INTERVAL;
        while Instant::now() < until && !self.shutdown.load(Ordering::Acquire) {
            thread::sleep(Duration::from_millis(25));
        }
    }

    fn publish(&self, transition: MotionTransition, monotonic_ms: u64) {
        let mut sequence = self
            .next_event_sequence
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        *sequence = sequence.saturating_add(1);
        let event = MotionEvent {
            version: MOTION_EVENT_VERSION,
            sequence: *sequence,
            state: if transition == MotionTransition::Active {
                MotionState::Active
            } else {
                MotionState::Inactive
            },
            channel: 1,
            monotonic_ms,
            occurred_unix_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .ok()
                .and_then(|value| u64::try_from(value.as_millis()).ok()),
            snapshot: None,
            clip: None,
        };
        let _ = self
            .sink
            .read()
            .unwrap_or_else(|error| error.into_inner())
            .try_send_motion(event);
    }
}

#[cfg(test)]
mod tests {
    use super::super::motion::tests::sequence;
    use super::super::tests::{backend, task_temp};
    use super::*;

    #[derive(Default)]
    struct Sink(Mutex<Vec<MotionEvent>>);

    impl MotionEventSink for Sink {
        fn try_send_motion(
            &self,
            event: MotionEvent,
        ) -> crate::camera::motion_events::MotionEventDisposition {
            self.0.lock().unwrap().push(event);
            crate::camera::motion_events::MotionEventDisposition::Queued
        }
    }

    fn status(raw_motion: bool, epoch: u64, sequence: u64) -> Vec<u8> {
        format!(
            r#"{{"status":"ok","supported":true,"active":true,"receiving":true,"motion":{raw_motion},"timestamp":{sequence},"move_result":{{"epoch":{epoch},"sequence":{sequence},"raw_motion":{raw_motion},"timestamp":{sequence}}}}}"#
        )
        .into_bytes()
    }

    fn idle_status() -> Vec<u8> {
        br#"{"status":"ok","supported":true,"active":true,"receiving":true,"motion":false,"timestamp":0,"move_result":null}"#.to_vec()
    }

    fn config(debounce: u64) -> Vec<u8> {
        format!(r#"{{"status":"ok","persistence":"checked-config","supported":true,"available":true,"active":true,"receiving":true,"worker":true,"lifecycle":{{"debounce_time":{debounce},"cooldown_time":0,"init_time":0,"min_time":0,"post_time":0}}}}"#).into_bytes()
    }

    #[test]
    fn one_poller_applies_debounce_and_publishes_lifecycle_transitions() {
        let root = task_temp("raptor-motion-lifecycle");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 10)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 11)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 11)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 12)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 13)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        let erased: Arc<dyn MotionEventSink> = sink.clone();
        backend.motion_lifecycle.set_sink(erased);
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 5 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        backend.motion_lifecycle.shutdown().unwrap();
        let events = sink.0.lock().unwrap();
        assert_eq!(
            events.iter().map(|event| event.state).collect::<Vec<_>>(),
            vec![MotionState::Active, MotionState::Inactive]
        );
        assert!(events[0].monotonic_ms >= 1_300);
        drop(events);
        let state = backend.motion_lifecycle.snapshot();
        assert_eq!(state.polls, 5);
        assert_eq!(state.transitions, 2);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unknown_poll_does_not_publish_a_false_inactive_transition() {
        let root = task_temp("raptor-motion-unknown");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), b"{}".to_vec()),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 2)),
                (
                    br#"{"cmd":"ivs-status"}"#.to_vec(),
                    br#"{"status":"ok","supported":true,"active":false,"receiving":false,"motion":false,"timestamp":2,"move_result":{"epoch":1,"sequence":2,"raw_motion":true,"timestamp":2}}"#.to_vec(),
                ),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        let erased: Arc<dyn MotionEventSink> = sink.clone();
        backend.motion_lifecycle.set_sink(erased);
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 5 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        backend.motion_lifecycle.shutdown().unwrap();
        assert_eq!(
            sink.0
                .lock()
                .unwrap()
                .iter()
                .map(|event| event.state)
                .collect::<Vec<_>>(),
            vec![MotionState::Active, MotionState::Inactive]
        );
        assert_eq!(backend.motion_lifecycle.snapshot().failures, 1);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn producer_restart_rebaselines_reused_sequences_without_replaying_a_sample() {
        let root = task_temp("raptor-motion-producer-restart");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 10, 7)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 10, 7)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 7)),
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 7)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 8)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 20, 9)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        let erased: Arc<dyn MotionEventSink> = sink.clone();
        backend.motion_lifecycle.set_sink(erased);
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 6 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        backend.motion_lifecycle.shutdown().unwrap();
        assert_eq!(
            sink.0
                .lock()
                .unwrap()
                .iter()
                .map(|event| event.state)
                .collect::<Vec<_>>(),
            vec![MotionState::Active, MotionState::Inactive]
        );
        assert_eq!(backend.motion_lifecycle.snapshot().polls, 6);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn producer_restart_reloads_config_and_discards_old_debounce_progress() {
        let root = task_temp("raptor-motion-producer-config-reload");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 10, 1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 10, 2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 1)),
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), b"{}".to_vec()),
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(3)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 3)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 20, 4)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        let erased: Arc<dyn MotionEventSink> = sink.clone();
        backend.motion_lifecycle.set_sink(erased);
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 6 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        backend.motion_lifecycle.shutdown().unwrap();
        assert!(sink.0.lock().unwrap().is_empty());
        assert_eq!(backend.motion_lifecycle.snapshot().polls, 6);
        assert_eq!(backend.motion_lifecycle.snapshot().failures, 1);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn producer_restart_stays_unavailable_until_a_distinct_new_result() {
        let root = task_temp("raptor-motion-producer-await-result");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 10, 1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 10, 2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 1)),
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 20, 1)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        let erased: Arc<dyn MotionEventSink> = sink.clone();
        backend.motion_lifecycle.set_sink(erased);
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 4 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let state = backend.motion_lifecycle.snapshot();
        assert!(!state.available);
        assert!(!state.active);
        assert_eq!(state.transitions, 1);
        assert_eq!(
            sink.0
                .lock()
                .unwrap()
                .iter()
                .map(|event| event.state)
                .collect::<Vec<_>>(),
            vec![MotionState::Active]
        );
        backend.motion_lifecycle.shutdown().unwrap();
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_daemon_does_not_block_start_and_later_becomes_available() {
        let root = task_temp("raptor-motion-late-daemon");
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let started = Instant::now();
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        assert!(started.elapsed() < Duration::from_millis(100));
        let running_deadline = Instant::now() + Duration::from_millis(100);
        while !backend.motion_lifecycle.snapshot().running && Instant::now() < running_deadline {
            thread::sleep(Duration::from_millis(2));
        }
        assert!(backend.motion_lifecycle.snapshot().running);
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), idle_status()),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 2)),
            ],
        );
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while !backend.motion_lifecycle.snapshot().available && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(backend.motion_lifecycle.snapshot().available);
        backend.motion_lifecycle.shutdown().unwrap();
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cleared_sample_after_worker_restart_is_unknown_not_inactive() {
        let root = task_temp("raptor-motion-worker-reset");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(true, 1, 2)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), idle_status()),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        backend.motion_lifecycle.set_sink(sink.clone());
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 3 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let state = backend.motion_lifecycle.snapshot();
        assert_eq!(state.polls, 3);
        assert!(!state.available);
        assert!(state.active); // Last processed state is retained, but not advertised as current.
        assert_eq!(
            sink.0
                .lock()
                .unwrap()
                .iter()
                .map(|event| event.state)
                .collect::<Vec<_>>(),
            vec![MotionState::Active]
        );
        backend.motion_lifecycle.shutdown().unwrap();
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn initial_null_baseline_and_duplicate_stay_unavailable() {
        let root = task_temp("raptor-motion-initial-await-result");
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-motion-config"}"#.to_vec(), config(1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), idle_status()),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 1)),
                (br#"{"cmd":"ivs-status"}"#.to_vec(), status(false, 1, 1)),
            ],
        );
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let sink = Arc::new(Sink::default());
        let erased: Arc<dyn MotionEventSink> = sink.clone();
        backend.motion_lifecycle.set_sink(erased);
        backend
            .motion_lifecycle
            .start(Arc::downgrade(&backend))
            .unwrap();
        daemon.join().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        while backend.motion_lifecycle.snapshot().polls < 3 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let state = backend.motion_lifecycle.snapshot();
        assert!(!state.available);
        assert!(!state.active);
        assert_eq!(state.transitions, 0);
        assert!(sink.0.lock().unwrap().is_empty());
        backend.motion_lifecycle.shutdown().unwrap();
        std::fs::remove_dir_all(root).unwrap();
    }
}
