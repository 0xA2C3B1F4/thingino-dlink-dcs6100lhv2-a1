//! Own the worker handle and serialize enqueue, stop, join, and restart.
use std::io;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
#[cfg(test)]
use std::time::Duration;

use super::super::{
    config::HaConfig,
    motion::{MotionSlot, MotionUpdate},
};
use super::session::{session_control, worker_loop};
use super::{EVENT_QUEUE_CAPACITY, HaService, Request, RuntimeState, update_runtime};
#[cfg(test)]
use super::{HaTestHooks, TestBarrier};
use crate::BackendError;
use crate::camera::{PrudyntBackend, motion_events::MotionEventDisposition};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum WorkerState {
    Running,
    Stopping,
}

pub(super) struct WorkerControl {
    pub(super) launch_generation: usize,
    pub(super) state: WorkerState,
    pub(super) sender: Option<SyncSender<Request>>,
    pub(super) shutdown_requested: Arc<AtomicBool>,
    pub(super) handle: Option<JoinHandle<()>>,
}

impl HaService {
    pub(in crate::camera) fn new() -> Self {
        Self {
            backend: Mutex::new(Weak::new()),
            worker: Mutex::new(None),
            runtime: Arc::new(Mutex::new(RuntimeState::default())),
            queue_depth: Arc::new(AtomicUsize::new(0)),
            queue_high_water: Arc::new(AtomicUsize::new(0)),
            config_generation: Arc::new(AtomicUsize::new(1)),
            accept_motion: Arc::new(AtomicBool::new(false)),
            motion_slot: Arc::new(MotionSlot::default()),
            #[cfg(test)]
            test_hooks: HaTestHooks::default(),
        }
    }

    pub(in crate::camera) fn start(&self, backend: Arc<PrudyntBackend>) {
        *self
            .backend
            .lock()
            .expect("HA backend lock must be available") = Arc::downgrade(&backend);
        self.reconfigure();
    }

    pub(in crate::camera) fn reconfigure(&self) {
        let Some(backend) = self
            .backend
            .lock()
            .ok()
            .and_then(|backend| backend.upgrade())
        else {
            return;
        };
        let (enabled, motion_enabled, invalid) = match HaConfig::load(&backend.paths) {
            Ok(config) => (
                config.enabled,
                config.enabled && config.entities.motion,
                false,
            ),
            Err(_) => {
                update_runtime(&self.runtime, |state| {
                    state.enabled = false;
                    state.state = "error";
                    state.connected = false;
                    state.last_error = Some("invalid HA configuration");
                    state.reconnect_at = None;
                });
                (false, false, true)
            }
        };
        let Ok(mut worker) = self.worker.lock() else {
            return;
        };
        self.accept_motion.store(motion_enabled, Ordering::Release);
        self.motion_slot.reset();
        let generation = self.config_generation.fetch_add(1, Ordering::AcqRel) + 1;
        let finished = match worker.as_mut() {
            Some(active) if active.state == WorkerState::Running => return,
            Some(active) => active
                .handle
                .take()
                .map(|handle| (active.launch_generation, handle)),
            None => None,
        };
        if worker.is_some() && finished.is_none() {
            return;
        }
        drop(worker);

        if let Some((launch_generation, handle)) = finished {
            let joined = handle.join();
            let Ok(mut worker) = self.worker.lock() else {
                return;
            };
            if worker.as_ref().is_some_and(|active| {
                active.launch_generation == launch_generation
                    && active.state == WorkerState::Stopping
                    && active.handle.is_none()
            }) {
                *worker = None;
            }
            drop(worker);
            if joined.is_err() {
                update_runtime(&self.runtime, |state| {
                    state.state = "error";
                    state.connected = false;
                    state.last_error = Some("HA worker join failed");
                    state.reconnect_at = None;
                });
                return;
            }
        }

        if !enabled && !invalid {
            update_runtime(&self.runtime, |state| {
                state.enabled = false;
                state.state = "disabled";
                state.connected = false;
                state.last_error = None;
                state.reconnect_at = None;
            });
            return;
        }
        let Ok(mut worker) = self.worker.lock() else {
            return;
        };
        if worker.is_some() {
            return;
        }
        let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
        let shutdown_requested = Arc::new(AtomicBool::new(false));
        let runtime = Arc::clone(&self.runtime);
        let queue_depth = Arc::clone(&self.queue_depth);
        let config_generation = Arc::clone(&self.config_generation);
        let motion_slot = Arc::clone(&self.motion_slot);
        let worker_shutdown = Arc::clone(&shutdown_requested);
        let service = Arc::clone(&backend.ha);
        let handle = self.spawn_worker(move || {
            loop {
                #[cfg(test)]
                wait_test_barrier(&service.test_hooks.start_barrier, "start");
                let exit_generation = worker_loop(
                    Arc::clone(&backend),
                    &receiver,
                    Arc::clone(&runtime),
                    Arc::clone(&queue_depth),
                    Arc::clone(&config_generation),
                    Arc::clone(&motion_slot),
                    &worker_shutdown,
                );
                #[cfg(test)]
                wait_test_barrier(&service.test_hooks.pre_exit_barrier, "pre-exit");
                if service.worker_exited(generation, exit_generation, &receiver) {
                    return;
                }
            }
        });
        match handle {
            Ok(handle) => {
                *worker = Some(WorkerControl {
                    launch_generation: generation,
                    state: WorkerState::Running,
                    sender: Some(sender),
                    shutdown_requested,
                    handle: Some(handle),
                });
            }
            Err(_) => {
                self.accept_motion.store(false, Ordering::Release);
                self.motion_slot.reset();
                self.queue_depth.store(0, Ordering::Release);
                update_runtime(&self.runtime, |state| {
                    state.enabled = enabled;
                    state.state = "error";
                    state.connected = false;
                    state.last_error = Some("HA worker spawn failed");
                    state.reconnect_at = None;
                });
            }
        }
    }

    pub(in crate::camera) fn shutdown(&self) -> io::Result<()> {
        self.accept_motion.store(false, Ordering::Release);
        self.motion_slot.reset();
        let (launch_generation, handle) = {
            let mut worker = self
                .worker
                .lock()
                .map_err(|_| io::Error::other("HA worker lifecycle lock is poisoned"))?;
            let Some(active) = worker.as_mut() else {
                self.queue_depth.store(0, Ordering::Release);
                return Ok(());
            };
            active.state = WorkerState::Stopping;
            active.shutdown_requested.store(true, Ordering::Release);
            active.sender.take();
            let handle = active.handle.take().ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "HA worker join is already in progress",
                )
            })?;
            (active.launch_generation, handle)
        };

        let joined = handle.join();
        let mut worker = self
            .worker
            .lock()
            .map_err(|_| io::Error::other("HA worker lifecycle lock is poisoned"))?;
        if worker.as_ref().is_some_and(|active| {
            active.launch_generation == launch_generation
                && active.state == WorkerState::Stopping
                && active.handle.is_none()
        }) {
            self.queue_depth.store(0, Ordering::Release);
            *worker = None;
        }
        joined.map_err(|_| io::Error::other("HA worker panicked during join"))
    }

    fn spawn_worker<F>(&self, task: F) -> io::Result<JoinHandle<()>>
    where
        F: FnOnce() + Send + 'static,
    {
        #[cfg(test)]
        if self
            .test_hooks
            .fail_next_spawn
            .swap(false, Ordering::AcqRel)
        {
            return Err(io::Error::other("injected HA worker spawn failure"));
        }
        thread::Builder::new()
            .name("control-ha".to_owned())
            .spawn(task)
    }

    fn worker_exited(
        &self,
        launch_generation: usize,
        exit_generation: usize,
        receiver: &Receiver<Request>,
    ) -> bool {
        let Ok(mut worker) = self.worker.lock() else {
            self.accept_motion.store(false, Ordering::Release);
            self.motion_slot.reset();
            self.queue_depth.store(0, Ordering::Release);
            return true;
        };
        let Some(active) = worker
            .as_mut()
            .filter(|active| active.launch_generation == launch_generation)
        else {
            return true;
        };
        if active.state == WorkerState::Running
            && !active.shutdown_requested.load(Ordering::Acquire)
            && self.config_generation.load(Ordering::Acquire) != exit_generation
        {
            let _ = session_control(
                receiver,
                &self.queue_depth,
                &self.config_generation,
                exit_generation,
                &active.shutdown_requested,
                None,
            );
            return false;
        }
        #[cfg(test)]
        wait_test_barrier(&self.test_hooks.exit_barrier, "exit");
        active.state = WorkerState::Stopping;
        active.sender.take();
        self.accept_motion.store(false, Ordering::Release);
        self.motion_slot.reset();
        self.queue_depth.store(0, Ordering::Release);
        true
    }

    pub(super) fn enqueue(&self, request: Request) -> Result<(), BackendError> {
        let worker = self.worker.lock().map_err(|_| BackendError::Unavailable)?;
        let active = worker.as_ref().ok_or(BackendError::Unavailable)?;
        if active.state != WorkerState::Running {
            return Err(BackendError::Unavailable);
        }
        let sender = active.sender.as_ref().ok_or(BackendError::Unavailable)?;
        enqueue_to(sender, &self.queue_depth, &self.queue_high_water, request)
    }

    pub(in crate::camera::ha) fn accepts_motion(&self) -> MotionEventDisposition {
        let worker = match self.worker.try_lock() {
            Ok(worker) => worker,
            Err(_) => return MotionEventDisposition::Dropped,
        };
        if !self.accept_motion.load(Ordering::Acquire) {
            return MotionEventDisposition::Disabled;
        }
        let Some(active) = worker.as_ref() else {
            return MotionEventDisposition::Dropped;
        };
        if active.state != WorkerState::Running || active.sender.is_none() {
            return MotionEventDisposition::Dropped;
        }
        MotionEventDisposition::Queued
    }

    pub(in crate::camera::ha) fn enqueue_motion(
        &self,
        update: MotionUpdate,
    ) -> MotionEventDisposition {
        let worker = match self.worker.try_lock() {
            Ok(worker) => worker,
            Err(_) => return MotionEventDisposition::Dropped,
        };
        if !self.accept_motion.load(Ordering::Acquire) {
            return MotionEventDisposition::Disabled;
        }
        let Some(active) = worker.as_ref() else {
            return MotionEventDisposition::Dropped;
        };
        if active.state != WorkerState::Running {
            return MotionEventDisposition::Dropped;
        }
        let Some(sender) = active.sender.as_ref() else {
            return MotionEventDisposition::Dropped;
        };
        self.motion_slot.submit(update, || {
            enqueue_to(
                sender,
                &self.queue_depth,
                &self.queue_high_water,
                Request::Motion,
            )
            .is_ok()
        })
    }
}

pub(super) fn enqueue_to(
    sender: &SyncSender<Request>,
    queue_depth: &AtomicUsize,
    queue_high_water: &AtomicUsize,
    request: Request,
) -> Result<(), BackendError> {
    let depth = queue_depth.fetch_add(1, Ordering::AcqRel) + 1;
    match sender.try_send(request) {
        Ok(()) => {
            queue_high_water.fetch_max(depth, Ordering::AcqRel);
            Ok(())
        }
        Err(TrySendError::Full(_) | TrySendError::Disconnected(_)) => {
            queue_depth.fetch_sub(1, Ordering::AcqRel);
            Err(BackendError::Unavailable)
        }
    }
}

#[cfg(test)]
fn wait_test_barrier(barrier: &Mutex<Option<TestBarrier>>, phase: &str) {
    let barrier = barrier
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .take();
    if let Some(barrier) = barrier {
        barrier
            .reached
            .send(())
            .unwrap_or_else(|_| panic!("HA worker {phase} barrier observer disappeared"));
        barrier
            .release
            .recv_timeout(Duration::from_secs(3))
            .unwrap_or_else(|_| panic!("HA worker {phase} barrier timed out"));
    }
}
