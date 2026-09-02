use std::collections::BTreeMap;
use std::io;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TryRecvError, TrySendError};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use super::super::motion_events::MotionEventDisposition;
use super::super::*;
use super::commands;
use super::config::HaConfig;
use super::discovery;
use super::motion::{self, MotionSlot, MotionUpdate};
use super::mqtt;
use super::state;

pub(super) const EVENT_QUEUE_CAPACITY: usize = 64;
const KEEPALIVE_SECONDS: u16 = 30;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);

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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum WorkerState {
    Running,
    Stopping,
}

struct WorkerControl {
    launch_generation: usize,
    state: WorkerState,
    sender: Option<SyncSender<Request>>,
    shutdown_requested: Arc<AtomicBool>,
    handle: Option<JoinHandle<()>>,
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

    fn enqueue(&self, request: Request) -> Result<(), BackendError> {
        let worker = self.worker.lock().map_err(|_| BackendError::Unavailable)?;
        let active = worker.as_ref().ok_or(BackendError::Unavailable)?;
        if active.state != WorkerState::Running {
            return Err(BackendError::Unavailable);
        }
        let sender = active.sender.as_ref().ok_or(BackendError::Unavailable)?;
        enqueue_to(sender, &self.queue_depth, &self.queue_high_water, request)
    }

    pub(super) fn accepts_motion(&self) -> MotionEventDisposition {
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

    pub(super) fn enqueue_motion(&self, update: MotionUpdate) -> MotionEventDisposition {
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

fn enqueue_to(
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

fn optional_number(value: Option<u64>) -> Value {
    value.map(number).unwrap_or(Value::Null)
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

fn worker_loop(
    backend: Arc<PrudyntBackend>,
    receiver: &Receiver<Request>,
    runtime: Arc<Mutex<RuntimeState>>,
    queue_depth: Arc<AtomicUsize>,
    generation: Arc<AtomicUsize>,
    motion_slot: Arc<MotionSlot>,
    shutdown_requested: &AtomicBool,
) -> usize {
    let mut observed_generation = 0;
    let mut backoff_attempt = 0_u32;
    loop {
        if shutdown_requested.load(Ordering::Acquire) {
            return observed_generation;
        }
        let current_generation = generation.load(Ordering::Acquire);
        if current_generation != observed_generation {
            observed_generation = current_generation;
            backoff_attempt = 0;
        }
        let config = match HaConfig::load(&backend.paths) {
            Ok(config) => config,
            Err(_) => {
                motion_slot.reset();
                update_runtime(&runtime, |state| {
                    state.enabled = false;
                    state.state = "error";
                    state.connected = false;
                    state.last_error = Some("invalid HA configuration");
                    state.reconnect_at = None;
                });
                if matches!(
                    wait_backoff(
                        Instant::now() + Duration::from_secs(1),
                        receiver,
                        &queue_depth,
                        &generation,
                        observed_generation,
                        shutdown_requested,
                    ),
                    Some(SessionEnd::Shutdown)
                ) {
                    return observed_generation;
                }
                continue;
            }
        };
        if !config.enabled {
            motion_slot.reset();
            update_runtime(&runtime, |state| {
                state.enabled = false;
                state.state = "disabled";
                state.connected = false;
                state.last_error = None;
                state.reconnect_at = None;
            });
            return observed_generation;
        }
        if config.host.is_empty() {
            update_runtime(&runtime, |state| {
                state.enabled = true;
                state.state = "error";
                state.connected = false;
                state.last_error = Some("MQTT host is not configured");
                state.reconnect_at = None;
            });
            return observed_generation;
        }

        update_runtime(&runtime, |state| {
            state.enabled = true;
            state.state = "connecting";
            state.connected = false;
            state.last_error = None;
            state.reconnect_at = None;
        });
        let result = connected_session(
            &backend,
            &config,
            receiver,
            observed_generation,
            SessionContext {
                runtime: &runtime,
                queue_depth: &queue_depth,
                generation: &generation,
                motion_slot: &motion_slot,
                shutdown_requested,
            },
        );
        update_runtime(&runtime, |state| {
            if state.connected {
                state.last_disconnect_unix = Some(unix_now());
            }
            state.connected = false;
        });
        match result {
            SessionEnd::Reconfigure(next) => {
                observed_generation = next;
                backoff_attempt = 0;
            }
            SessionEnd::Reconnect => backoff_attempt = 0,
            SessionEnd::Shutdown => return observed_generation,
            SessionEnd::Failure(error) => {
                let delay = reconnect_delay(&config.device_id, backoff_attempt);
                backoff_attempt = backoff_attempt.saturating_add(1);
                let deadline = Instant::now() + delay;
                update_runtime(&runtime, |state| {
                    state.state = "backoff";
                    state.last_error = Some(error);
                    state.reconnect_at = Some(deadline);
                });
                if let Some(end) = wait_backoff(
                    deadline,
                    receiver,
                    &queue_depth,
                    &generation,
                    observed_generation,
                    shutdown_requested,
                ) {
                    match end {
                        SessionEnd::Reconfigure(next) => {
                            observed_generation = next;
                            backoff_attempt = 0;
                        }
                        SessionEnd::Reconnect => backoff_attempt = 0,
                        SessionEnd::Shutdown => return observed_generation,
                        SessionEnd::Failure(_) => {}
                    }
                }
            }
        }
    }
}

enum SessionEnd {
    Reconfigure(usize),
    Reconnect,
    Shutdown,
    Failure(&'static str),
}

struct SessionContext<'a> {
    runtime: &'a Arc<Mutex<RuntimeState>>,
    queue_depth: &'a AtomicUsize,
    generation: &'a AtomicUsize,
    motion_slot: &'a MotionSlot,
    shutdown_requested: &'a AtomicBool,
}

#[derive(Default)]
struct SessionRequests {
    discovery: bool,
    state: bool,
    motion: bool,
}

fn connected_session(
    backend: &Arc<PrudyntBackend>,
    config: &HaConfig,
    receiver: &Receiver<Request>,
    observed_generation: usize,
    context: SessionContext<'_>,
) -> SessionEnd {
    let SessionContext {
        runtime,
        queue_depth,
        generation,
        motion_slot,
        shutdown_requested,
    } = context;
    let api = match mqtt::Api::load() {
        Ok(api) => api,
        Err(error) => return SessionEnd::Failure(error),
    };
    let mut client = match mqtt::Client::new(api, &config.client_id) {
        Ok(client) => client,
        Err(error) => return SessionEnd::Failure(error),
    };
    if let Err(error) = configure_client(&mut client, config) {
        return SessionEnd::Failure(error);
    }
    if let Err(error) = client.connect(&config.host, config.port, KEEPALIVE_SECONDS) {
        return SessionEnd::Failure(error);
    }
    let connect_deadline = Instant::now() + CONNECT_TIMEOUT;
    loop {
        if let Some(end) = session_control(
            receiver,
            queue_depth,
            generation,
            observed_generation,
            shutdown_requested,
            None,
        ) {
            return end;
        }
        if Instant::now() >= connect_deadline {
            return SessionEnd::Failure("MQTT connection timed out");
        }
        if client.loop_once(100).is_err() {
            return SessionEnd::Failure("MQTT connection failed");
        }
        if let Some(result) = client.take_connect_result() {
            if result != 0 {
                return SessionEnd::Failure("MQTT broker rejected connection");
            }
            break;
        }
    }

    for topic in commands::command_topics(config) {
        if client.subscribe(&topic).is_err() {
            return SessionEnd::Failure("MQTT subscribe failed");
        }
    }
    if publish(
        &mut client,
        runtime,
        &config.availability_topic(),
        b"online",
        true,
    )
    .is_err()
        || publish_discovery(&mut client, runtime, config, backend).is_err()
    {
        return SessionEnd::Failure("MQTT initial publish failed");
    }
    let mut last_published = BTreeMap::new();
    if publish_states(
        &mut client,
        runtime,
        config,
        backend,
        &mut last_published,
        true,
    )
    .is_err()
    {
        return SessionEnd::Failure("camera state unavailable");
    }
    if config.entities.motion {
        if publish_motion_resync(&mut client, runtime, config, backend, &mut last_published)
            .is_err()
            || publish_motion_updates(
                &mut client,
                runtime,
                config,
                backend,
                motion_slot,
                &mut last_published,
            )
            .is_err()
        {
            return SessionEnd::Failure("MQTT motion state publish failed");
        }
    } else {
        while motion_slot.take_next().is_some() {}
    }
    let now = Instant::now();
    let mut next_state = now + config.state_interval;
    let mut next_discovery = now + config.discovery_interval;
    update_runtime(runtime, |state| {
        state.state = "online";
        state.connected = true;
        state.last_connect_unix = Some(unix_now());
        state.last_error = None;
        state.reconnect_at = None;
    });
    if config.entities.live_view
        && publish_live_image(&mut client, runtime, config, backend).is_err()
    {
        return SessionEnd::Failure("MQTT camera image publish failed");
    }
    let mut next_camera = Instant::now() + config.camera_interval;

    loop {
        let mut requested = SessionRequests::default();
        if let Some(end) = session_control(
            receiver,
            queue_depth,
            generation,
            observed_generation,
            shutdown_requested,
            Some(&mut requested),
        ) {
            let _ = publish(
                &mut client,
                runtime,
                &config.availability_topic(),
                b"offline",
                true,
            );
            client.disconnect();
            return end;
        }
        if client.loop_once(100).is_err() || client.take_disconnect_result().is_some() {
            return SessionEnd::Failure("MQTT connection lost");
        }
        while let Some(message) = client.take_message() {
            let Some(command) = commands::parse(config, &message.topic, &message.payload) else {
                update_runtime(runtime, |state| {
                    state.rejected_commands = state.rejected_commands.saturating_add(1);
                });
                continue;
            };
            update_runtime(runtime, |state| {
                state.received_commands = state.received_commands.saturating_add(1);
            });
            match backend.ha_execute_command(command) {
                Ok(Some(snapshot)) => {
                    if publish(
                        &mut client,
                        runtime,
                        &format!("{}/live_view/image", config.base_topic()),
                        &snapshot,
                        false,
                    )
                    .is_err()
                    {
                        return SessionEnd::Failure("MQTT snapshot publish failed");
                    }
                    update_runtime(runtime, clear_command_error);
                }
                Ok(None) => {
                    match publish_states(
                        &mut client,
                        runtime,
                        config,
                        backend,
                        &mut last_published,
                        false,
                    ) {
                        Ok(()) => update_runtime(runtime, clear_command_error),
                        Err(_) => update_runtime(runtime, |state| {
                            state.last_error = Some("camera command state readback failed")
                        }),
                    }
                }
                Err(_) => update_runtime(runtime, |state| {
                    state.last_error = Some("camera command failed")
                }),
            }
        }
        update_runtime(runtime, |state| {
            state.dropped_messages = client.dropped_messages()
        });
        if requested.motion
            && config.entities.motion
            && publish_motion_updates(
                &mut client,
                runtime,
                config,
                backend,
                motion_slot,
                &mut last_published,
            )
            .is_err()
        {
            return SessionEnd::Failure("MQTT motion state publish failed");
        }
        let now = Instant::now();
        if requested.discovery || now >= next_discovery {
            if publish_discovery(&mut client, runtime, config, backend).is_err() {
                return SessionEnd::Failure("MQTT discovery publish failed");
            }
            next_discovery = now + config.discovery_interval;
        }
        if requested.state || now >= next_state {
            if publish_states(
                &mut client,
                runtime,
                config,
                backend,
                &mut last_published,
                requested.state,
            )
            .is_err()
            {
                update_runtime(runtime, |state| {
                    state.last_error = Some("camera state unavailable")
                });
            }
            next_state = now + config.state_interval;
        }
        if config.entities.live_view && now >= next_camera {
            if publish_live_image(&mut client, runtime, config, backend).is_err() {
                return SessionEnd::Failure("MQTT live-view publish failed");
            }
            next_camera = now + config.camera_interval;
        }
    }
}

fn publish_live_image(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &PrudyntBackend,
) -> Result<(), ()> {
    let snapshot = match super::snapshot::capture(backend, 1) {
        Ok(snapshot) => snapshot,
        Err(_) => {
            update_runtime(runtime, |state| {
                state.last_error = Some("camera preview unavailable")
            });
            return Ok(());
        }
    };
    publish(
        client,
        runtime,
        &format!("{}/live_view/image", config.base_topic()),
        &snapshot,
        false,
    )
    .map_err(|_| ())?;
    update_runtime(runtime, |state| {
        if state.last_error == Some("camera preview unavailable") {
            state.last_error = None;
        }
    });
    Ok(())
}

fn configure_client(client: &mut mqtt::Client, config: &HaConfig) -> Result<(), &'static str> {
    client.set_credentials(
        &config.username,
        (!config.password.is_empty()).then_some(config.password.as_str()),
    )?;
    if config.use_tls {
        client.set_tls(None, Some("/etc/ssl/certs"), config.tls_skip_verify)?;
    }
    client.set_will(&config.availability_topic(), b"offline")
}

fn session_control(
    receiver: &Receiver<Request>,
    queue_depth: &AtomicUsize,
    generation: &AtomicUsize,
    observed_generation: usize,
    shutdown_requested: &AtomicBool,
    mut requests: Option<&mut SessionRequests>,
) -> Option<SessionEnd> {
    if shutdown_requested.load(Ordering::Acquire) {
        return Some(SessionEnd::Shutdown);
    }
    let current = generation.load(Ordering::Acquire);
    let reconfigure = current != observed_generation;
    loop {
        match receiver.try_recv() {
            Ok(request) => {
                queue_depth.fetch_sub(1, Ordering::AcqRel);
                if reconfigure {
                    continue;
                }
                match request {
                    Request::Reconnect => return Some(SessionEnd::Reconnect),
                    Request::RepublishDiscovery => {
                        if let Some(requests) = requests.as_mut() {
                            requests.discovery = true;
                        }
                    }
                    Request::PublishState => {
                        if let Some(requests) = requests.as_mut() {
                            requests.state = true;
                        }
                    }
                    Request::Motion => {
                        if let Some(requests) = requests.as_mut() {
                            requests.motion = true;
                        }
                    }
                }
            }
            Err(TryRecvError::Empty) => {
                return reconfigure.then_some(SessionEnd::Reconfigure(current));
            }
            Err(TryRecvError::Disconnected) => {
                return Some(SessionEnd::Shutdown);
            }
        }
    }
}

fn publish_motion_resync(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &PrudyntBackend,
    last_published: &mut BTreeMap<String, Vec<u8>>,
) -> Result<(), &'static str> {
    publish_motion_state(
        client,
        runtime,
        config,
        super::super::motion_events::MotionState::Resync,
        backend.motion.snapshot().active,
        last_published,
    )
}

fn publish_motion_updates(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &PrudyntBackend,
    motion_slot: &MotionSlot,
    last_published: &mut BTreeMap<String, Vec<u8>>,
) -> Result<(), &'static str> {
    while let Some(update) = motion_slot.take_next() {
        publish_motion_state(
            client,
            runtime,
            config,
            update.state,
            backend.motion.snapshot().active,
            last_published,
        )?;
    }
    Ok(())
}

fn publish_motion_state(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    state: super::super::motion_events::MotionState,
    current_active: bool,
    last_published: &mut BTreeMap<String, Vec<u8>>,
) -> Result<(), &'static str> {
    let payload = motion::retained_payload(state, current_active);
    publish(
        client,
        runtime,
        &format!("{}/motion/state", config.base_topic()),
        payload,
        true,
    )?;
    last_published.insert("motion".to_owned(), payload.to_vec());
    Ok(())
}

fn publish_discovery(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &PrudyntBackend,
) -> Result<(), &'static str> {
    let release = state::os_release(&backend.paths);
    for publication in discovery::discovery_publications(config, &release) {
        publish(
            client,
            runtime,
            &publication.topic,
            &publication.payload,
            publication.retained,
        )?;
    }
    Ok(())
}

fn publish_states(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &PrudyntBackend,
    last_published: &mut BTreeMap<String, Vec<u8>>,
    force: bool,
) -> Result<(), BackendError> {
    for (entity, payload) in state::collect(backend, config)? {
        if force || last_published.get(&entity) != Some(&payload) {
            let topic = format!("{}/{entity}/state", config.base_topic());
            publish(client, runtime, &topic, &payload, true)
                .map_err(|_| BackendError::Connection)?;
            last_published.insert(entity, payload);
        }
    }
    Ok(())
}

fn publish(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    topic: &str,
    payload: &[u8],
    retained: bool,
) -> Result<(), &'static str> {
    client.publish(topic, payload, retained)?;
    update_runtime(runtime, |state| {
        state.published_messages = state.published_messages.saturating_add(1);
    });
    Ok(())
}

fn wait_backoff(
    deadline: Instant,
    receiver: &Receiver<Request>,
    queue_depth: &AtomicUsize,
    generation: &AtomicUsize,
    observed_generation: usize,
    shutdown_requested: &AtomicBool,
) -> Option<SessionEnd> {
    loop {
        if shutdown_requested.load(Ordering::Acquire) {
            return Some(SessionEnd::Shutdown);
        }
        let current = generation.load(Ordering::Acquire);
        if current != observed_generation {
            return Some(SessionEnd::Reconfigure(current));
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return None;
        }
        match receiver.recv_timeout(remaining.min(Duration::from_secs(1))) {
            Ok(Request::Motion) => {
                queue_depth.fetch_sub(1, Ordering::AcqRel);
            }
            Ok(Request::Reconnect | Request::RepublishDiscovery | Request::PublishState) => {
                queue_depth.fetch_sub(1, Ordering::AcqRel);
                // A fresh session always republishes discovery and state, so
                // either publish action can safely wake backoff early.
                return Some(SessionEnd::Reconnect);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Some(SessionEnd::Shutdown);
            }
        }
    }
}

fn reconnect_delay(device_id: &str, attempt: u32) -> Duration {
    let exponent = attempt.min(6);
    let base_ms = 1_000_u64.saturating_mul(1_u64 << exponent).min(60_000);
    let jitter = device_id
        .bytes()
        .fold(0_u64, |value, byte| {
            value.wrapping_mul(33).wrapping_add(u64::from(byte))
        })
        .wrapping_add(u64::from(attempt) * 97)
        % 251;
    Duration::from_millis(base_ms.saturating_add(jitter).min(60_000))
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
