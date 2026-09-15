//! One Raptor-only background worker. Never holds the Preview lock during HTTP or disk I/O.
use super::*;
use std::os::unix::fs::MetadataExt;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, Weak, mpsc};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

mod policy;
mod preset;
pub(super) mod storage;
#[cfg(test)]
mod tests;
use policy::Policy;

fn object<const N: usize>(fields: [(&str, Value); N]) -> Value {
    Value::Object(
        fields
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
    )
}
fn string(text: &str) -> Value {
    Value::String(text.to_owned())
}
fn number(value: u64) -> Value {
    Value::Number(value.to_string())
}
fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |v| v.as_secs())
}

#[derive(Clone)]
struct State {
    policy: Policy,
    saved: Option<Policy>,
    initialized: bool,
    policy_known: bool,
    phase: &'static str,
    last_error: Option<&'static str>,
    restored: &'static str,
    successes: u64,
    last_success: Option<u64>,
    next_due: Option<u64>,
    cleanup_blocked: bool,
}
impl Default for State {
    fn default() -> Self {
        Self {
            policy: Policy::default(),
            saved: None,
            initialized: false,
            policy_known: false,
            phase: "starting",
            last_error: None,
            restored: "not_used",
            successes: 0,
            last_success: None,
            next_due: None,
            cleanup_blocked: false,
        }
    }
}
struct Update {
    policy: Policy,
    deadline: Instant,
    reply: mpsc::SyncSender<Result<(), BackendError>>,
}

#[cfg(test)]
type PhaseHook = Arc<dyn Fn(&str) + Send + Sync>;

pub(super) struct Service {
    state: Mutex<State>,
    sender: Mutex<Option<mpsc::SyncSender<Update>>>,
    started: AtomicBool,
    stop: AtomicBool,
    pending: AtomicBool,
    maintenance: AtomicBool,
    storage_active: AtomicBool,
    generation: AtomicU32,
    serial: AtomicU32,
    quarantine: Mutex<Option<storage::Image>>,
    publishing_name: Mutex<Option<String>>,
    config: PathBuf,
    store: storage::Store,
    #[cfg(test)]
    hook: Mutex<Option<PhaseHook>>,
}
struct Publication<'a>(&'a Service);
impl Drop for Publication<'_> {
    fn drop(&mut self) {
        if !self.0.state.lock().unwrap().cleanup_blocked {
            *self.0.publishing_name.lock().unwrap() = None;
        }
    }
}
impl Default for Service {
    fn default() -> Self {
        Self::new(
            "/etc/raptor-timelapse.json".into(),
            storage::Store::default(),
        )
    }
}
impl Service {
    pub(super) fn store(&self) -> &storage::Store {
        &self.store
    }
    pub(super) fn image_visible(&self, name: &str) -> bool {
        self.publishing_name
            .lock()
            .is_ok_and(|pending| pending.as_deref() != Some(name))
    }
    #[cfg(test)]
    pub(in crate::raptor_backend) fn storage_paused(&self) -> bool {
        self.maintenance.load(Ordering::Acquire)
    }

    pub(in crate::raptor_backend) fn new(config: PathBuf, store: storage::Store) -> Self {
        Self {
            state: Mutex::new(State::default()),
            sender: Mutex::new(None),
            started: AtomicBool::new(false),
            stop: AtomicBool::new(false),
            pending: AtomicBool::new(false),
            maintenance: AtomicBool::new(false),
            storage_active: AtomicBool::new(false),
            generation: AtomicU32::new(0),
            serial: AtomicU32::new(0),
            quarantine: Mutex::new(None),
            publishing_name: Mutex::new(None),
            config,
            store,
            #[cfg(test)]
            hook: Mutex::new(None),
        }
    }
    fn event(&self, phase: &'static str) {
        self.state.lock().unwrap().phase = phase;
        #[cfg(test)]
        {
            let hook = self.hook.lock().unwrap().clone();
            if let Some(hook) = hook {
                hook(phase);
            }
        }
    }
    fn begin_storage(&self) -> Result<StorageActivity<'_>, &'static str> {
        if self.maintenance.load(Ordering::Acquire) {
            return Err("storage_maintenance");
        }
        if self
            .storage_active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err("storage_busy");
        }
        if self.maintenance.load(Ordering::Acquire) {
            self.storage_active.store(false, Ordering::Release);
            return Err("storage_maintenance");
        }
        Ok(StorageActivity(self))
    }

    /// Prevent new timelapse SD work and wait for an already-started operation
    /// to finish. Policy and scheduling state are not changed or persisted.
    pub(super) fn pause_storage(
        &self,
        deadline: Instant,
    ) -> Result<StoragePause<'_>, BackendError> {
        if self
            .maintenance
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(BackendError::Busy);
        }
        self.generation.fetch_add(1, Ordering::AcqRel);
        loop {
            let inactive = !self.storage_active.load(Ordering::Acquire)
                && self.publishing_name.lock().is_ok_and(|name| name.is_none())
                && self.quarantine.lock().is_ok_and(|image| image.is_none());
            if inactive {
                return Ok(StoragePause(self));
            }
            if Instant::now() + Duration::from_millis(5) >= deadline {
                self.maintenance.store(false, Ordering::Release);
                return Err(BackendError::Timeout);
            }
            thread::sleep(Duration::from_millis(5));
        }
    }
    fn start(self: &Arc<Self>, backend: Weak<RaptorBackend>) -> Result<(), BackendError> {
        if self.started.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        let (sender, receiver) = mpsc::sync_channel(1);
        *self.sender.lock().unwrap() = Some(sender);
        let service = Arc::clone(self);
        if thread::Builder::new()
            .name("raptor-timelapse".into())
            .stack_size(256 * 1024)
            .spawn(move || service.worker(backend, receiver))
            .is_err()
        {
            self.started.store(false, Ordering::Release);
            *self.sender.lock().unwrap() = None;
            return Err(BackendError::Unavailable);
        }
        Ok(())
    }
    fn load(&self) {
        let loaded = storage::read_limited(&self.config, 8192);
        let mut state = self.state.lock().unwrap();
        state.policy_known = false;
        match loaded {
            Ok(bytes) => match crate::json::parse(&bytes)
                .ok()
                .and_then(|v| Policy::parse(&v).ok())
            {
                Some(policy) => {
                    state.policy_known = true;
                    state.policy = policy.clone();
                    state.saved = Some(policy);
                }
                None => {
                    state.last_error = Some("invalid_saved_config");
                }
            },
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                state.policy_known = true;
            }
            Err(_) => state.last_error = Some("config_unavailable"),
        }
        state.initialized = true;
        state.phase = if state.policy.enabled {
            "idle"
        } else {
            "disabled"
        };
    }
    fn persist(&self, policy: &Policy) -> Result<(), BackendError> {
        use std::os::unix::fs::OpenOptionsExt;
        let parent = self.config.parent().ok_or(BackendError::Unavailable)?;
        let metadata = parent
            .symlink_metadata()
            .map_err(|_| BackendError::Unavailable)?;
        if !metadata.is_dir() {
            return Err(BackendError::Unavailable);
        }
        let temporary = parent.join(format!(
            ".raptor-timelapse-{}-{}.pending",
            std::process::id(),
            self.serial.fetch_add(1, Ordering::AcqRel)
        ));
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(|_| BackendError::Unavailable)?;
        let result = (|| {
            file.write_all(policy.value().to_json().as_bytes())
                .and_then(|_| file.sync_all())
                .map_err(|_| BackendError::Unavailable)?;
            if let Ok(meta) = self.config.symlink_metadata()
                && (!meta.is_file() || meta.file_type().is_symlink())
            {
                return Err(BackendError::Unavailable);
            }
            fs::rename(&temporary, &self.config).map_err(|_| BackendError::Unavailable)?;
            std::fs::File::open(parent)
                .and_then(|directory| directory.sync_all())
                .map_err(|_| BackendError::Unavailable)?;
            let bytes =
                storage::read_limited(&self.config, 8192).map_err(|_| BackendError::Unavailable)?;
            if Policy::parse(&crate::json::parse(&bytes).map_err(|_| BackendError::Protocol)?)?
                != *policy
            {
                return Err(BackendError::Unavailable);
            }
            Ok(())
        })();
        if let (Ok(current), Ok(owned)) = (temporary.symlink_metadata(), file.metadata())
            && current.dev() == owned.dev()
            && current.ino() == owned.ino()
        {
            let _ = fs::remove_file(temporary);
        }
        result
    }
    fn submit(&self, body: &[u8], deadline: Instant) -> Result<BackendResponse, BackendError> {
        if !self.started.load(Ordering::Acquire) {
            return Err(BackendError::Unavailable);
        }
        if self
            .pending
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(BackendError::Busy);
        }
        let next = self
            .state
            .lock()
            .map_err(|_| BackendError::Unavailable)
            .and_then(|state| {
                if !state.initialized {
                    return Err(BackendError::Unavailable);
                }
                state.policy.update(body)
            });
        let next = match next {
            Ok(next) => next,
            Err(error) => {
                self.pending.store(false, Ordering::Release);
                return Err(error);
            }
        };
        let (reply, received) = mpsc::sync_channel(1);
        self.generation.fetch_add(1, Ordering::AcqRel);
        let sent = self
            .sender
            .lock()
            .ok()
            .and_then(|sender| sender.clone())
            .is_some_and(|sender| {
                sender
                    .try_send(Update {
                        policy: next,
                        deadline,
                        reply,
                    })
                    .is_ok()
            });
        if !sent {
            self.pending.store(false, Ordering::Release);
            return Err(BackendError::Unavailable);
        }
        received
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .map_err(|_| {
                BackendError::PartialApply(
                    "Timelapse save/readback is unconfirmed. Reload before retrying.",
                )
            })??;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
    /// Observe memory only; heartbeat must never wait for policy or capture work.
    pub(super) fn enabled_observation(&self) -> Option<bool> {
        if !self.started.load(Ordering::Acquire) || self.stop.load(Ordering::Acquire) {
            return None;
        }
        let state = self.state.try_lock().ok()?;
        (state.initialized && state.policy_known).then_some(state.policy.enabled)
    }

    pub(super) fn policy_value(&self) -> Value {
        self.state.lock().unwrap().policy.value()
    }
    fn value(&self) -> Value {
        let state = self.state.lock().unwrap().clone();
        object([
            ("ok", Value::Bool(true)),
            (
                "data",
                object([
                    ("source", string("raptor")),
                    ("domain", string("timelapse")),
                    ("persistent", Value::Bool(true)),
                    (
                        "available",
                        Value::Bool(state.initialized && self.started.load(Ordering::Acquire)),
                    ),
                    ("timelapse", state.policy.value()),
                    (
                        "saved_timelapse",
                        state.saved.as_ref().map_or(Value::Null, Policy::value),
                    ),
                    (
                        "matches_saved",
                        Value::Bool(state.saved.as_ref() == Some(&state.policy)),
                    ),
                    ("mounts", Value::Array(vec![string(policy::MOUNT)])),
                    (
                        "runtime",
                        object([
                            ("phase", string(state.phase)),
                            ("last_error", state.last_error.map_or(Value::Null, string)),
                            ("preset_restore", string(state.restored)),
                            ("successes", number(state.successes)),
                            (
                                "last_success",
                                state.last_success.map_or(Value::Null, number),
                            ),
                            ("next_due", state.next_due.map_or(Value::Null, number)),
                            ("cleanup_blocked", Value::Bool(state.cleanup_blocked)),
                        ]),
                    ),
                ]),
            ),
        ])
    }
    fn worker(&self, backend: Weak<RaptorBackend>, receiver: mpsc::Receiver<Update>) {
        self.load();
        let mut next_capture = Instant::now();
        let mut next_cleanup = Instant::now();
        while !self.stop.load(Ordering::Acquire) {
            match receiver.recv_timeout(Duration::from_millis(100)) {
                Ok(update) => {
                    let result = if Instant::now() >= update.deadline {
                        Err(BackendError::Upstream(504))
                    } else {
                        self.persist(&update.policy).map(|()| {
                            let mut state = self.state.lock().unwrap();
                            state.policy_known = true;
                            state.policy = update.policy.clone();
                            state.saved = Some(update.policy);
                            state.last_error = if state.cleanup_blocked { Some("cleanup_failed") } else { None };
                            state.phase = if state.cleanup_blocked { "error" } else if state.policy.enabled { "idle" } else { "disabled" };
                            state.restored = "not_used";
                            state.next_due = None;
                        }).map_err(|_| {
                            let mut state = self.state.lock().unwrap();
                            state.policy_known = false;
                            state.saved = None;
                            state.last_error = Some("config_unavailable");
                            BackendError::PartialApply("Timelapse configuration save/readback failed. Reload before retrying.")
                        })
                    };
                    self.pending.store(false, Ordering::Release);
                    if result.is_ok() {
                        next_capture = Instant::now();
                    }
                    let _ = update.reply.try_send(result);
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            let Some(backend) = backend.upgrade() else {
                break;
            };
            let state = self.state.lock().unwrap().clone();
            if state.policy.enabled
                && !self.maintenance.load(Ordering::Acquire)
                && !state.cleanup_blocked
                && state.restored != "conflict"
                && Instant::now() >= next_capture
            {
                let result = self.capture(&backend, &state.policy);
                let mut current = self.state.lock().unwrap();
                match result {
                    Ok(()) => {
                        current.last_success = Some(unix_now());
                        current.successes = current.successes.saturating_add(1);
                        current.last_error = None;
                        current.phase = "idle";
                    }
                    Err(error) => {
                        current.last_error = Some(error);
                        current.phase = "error";
                        if error == "cleanup_failed" {
                            current.cleanup_blocked = true;
                        }
                    }
                }
                next_capture = Instant::now() + Duration::from_secs(state.policy.interval * 60);
                current.next_due = if current.restored == "conflict" || current.cleanup_blocked {
                    None
                } else {
                    Some(unix_now().saturating_add(state.policy.interval * 60))
                };
            }
            let state = self.state.lock().unwrap().clone();
            if Instant::now() >= next_cleanup
                && !self.maintenance.load(Ordering::Acquire)
                && state.cleanup_blocked
            {
                let Ok(_activity) = self.begin_storage() else {
                    continue;
                };
                let image = self.quarantine.lock().unwrap().take();
                if let Some(image) = image
                    && self.discard(image).is_ok()
                {
                    let mut current = self.state.lock().unwrap();
                    current.cleanup_blocked = false;
                    *self.publishing_name.lock().unwrap() = None;
                    current.last_error = Some("invalidated_image_removed");
                }
                next_cleanup = Instant::now() + Duration::from_secs(60);
            }
            if Instant::now() >= next_cleanup
                && !self.maintenance.load(Ordering::Acquire)
                && state.policy.enabled
                && state.policy.keep_days > 0
                && !state.cleanup_blocked
            {
                let epoch = self.generation.load(Ordering::Acquire);
                let Ok(_activity) = self.begin_storage() else {
                    continue;
                };
                let result = self.store.directory().and_then(|dir| {
                    storage::cleanup(&dir, state.policy.keep_days, unix_now(), || {
                        self.stop.load(Ordering::Acquire)
                            || self.generation.load(Ordering::Acquire) != epoch
                    })
                });
                if result.is_err() {
                    self.state.lock().unwrap().last_error = Some("retention_unavailable");
                }
                next_cleanup = Instant::now()
                    + if matches!(result, Ok(32)) {
                        Duration::from_millis(100)
                    } else {
                        Duration::from_secs(60)
                    };
            }
        }
        self.started.store(false, Ordering::Release);
    }
    fn discard(&self, mut image: storage::Image) -> Result<(), &'static str> {
        if image.discard().is_err() {
            *self.quarantine.lock().unwrap() = Some(image);
            let mut state = self.state.lock().unwrap();
            state.cleanup_blocked = true;
            state.last_error = Some("cleanup_failed");
            return Err("cleanup_failed");
        }
        Ok(())
    }
    fn fence(&self, backend: &RaptorBackend, epoch: (u32, u32, u32)) -> Result<(), &'static str> {
        let unchanged = || {
            !self.stop.load(Ordering::Acquire)
                && !self.maintenance.load(Ordering::Acquire)
                && self.generation.load(Ordering::Acquire) == epoch.0
                && backend.ha_privacy_generation.load(Ordering::Acquire) == epoch.1
                && backend.timelapse_user_generation.load(Ordering::Acquire) == epoch.2
        };
        if !unchanged()
            || backend
                .privacy_state(Instant::now() + Duration::from_millis(150))
                .ok()
                != Some(false)
            || !unchanged()
        {
            return Err("privacy_or_settings_changed");
        }
        Ok(())
    }
    fn capture(&self, backend: &RaptorBackend, policy: &Policy) -> Result<(), &'static str> {
        if self.state.lock().unwrap().cleanup_blocked {
            return Err("cleanup_failed");
        }
        let epoch = (
            self.generation.load(Ordering::Acquire),
            backend.ha_privacy_generation.load(Ordering::Acquire),
            backend.timelapse_user_generation.load(Ordering::Acquire),
        );
        self.fence(backend, epoch)?;
        let mut preset = if policy.preset_enabled {
            Some(preset::Lease::read(backend).map_err(|_| "preset_unavailable")?)
        } else {
            None
        };
        self.fence(backend, epoch)?;
        if let Some(lease) = preset.as_mut()
            && lease.apply(backend, policy.presets).is_err()
        {
            let restored = lease.restore(backend, || self.event("restoring"));
            self.state.lock().unwrap().restored = if restored.is_ok() {
                "restored"
            } else {
                "conflict"
            };
            return Err(if restored.is_ok() {
                "preset_apply_failed"
            } else {
                "preset_restore_failed"
            });
        }
        self.event("capturing");
        let jpeg = (|| {
            self.fence(backend, epoch)?;
            let deadline = Instant::now() + Duration::from_secs(2);
            if backend.ha_jpeg_ready(deadline).ok() != Some(true) {
                return Err("jpeg_unavailable");
            }
            backend
                .http_snapshot_bounded(0, deadline, 256 * 1024)
                .map(|v| v.body)
                .map_err(|_| "capture_failed")
        })();
        if let Some(lease) = preset.as_mut() {
            let restored = lease.restore(backend, || self.event("restoring"));
            self.state.lock().unwrap().restored = if restored.is_ok() {
                "restored"
            } else {
                "conflict"
            };
            if restored.is_err() {
                return Err("preset_restore_failed");
            }
        } else {
            self.state.lock().unwrap().restored = "not_used";
        }
        let jpeg = jpeg?;
        self.fence(backend, epoch)?;
        let _activity = self.begin_storage()?;
        let dir = self.store.directory().map_err(|_| "storage_unavailable")?;
        let serial = self.serial.fetch_add(1, Ordering::AcqRel);
        let captured = unix_now();
        *self.publishing_name.lock().unwrap() = Some(format!("{captured}-{serial}.jpg"));
        let _publication = Publication(self);
        self.event("staging");
        let mut image = match storage::Image::stage(dir, captured, serial, &jpeg, || {
            self.event("staging_io")
        }) {
            Ok(image) => image,
            Err((_, Some(image))) => {
                self.discard(*image)?;
                return Err("staging_failed");
            }
            Err((_, None)) => return Err("staging_failed"),
        };
        let result = (|| {
            self.fence(backend, epoch)?;
            self.event("before_publish");
            self.fence(backend, epoch)?;
            image
                .verify_mount(&self.store.directory().map_err(|_| "storage_unavailable")?)
                .map_err(|_| "storage_changed")?;
            self.fence(backend, epoch)?;
            image.publish(serial).map_err(|_| "publish_failed")?;
            self.event("published");
            self.fence(backend, epoch)?;
            image
                .verify_mount(&self.store.directory().map_err(|_| "storage_unavailable")?)
                .map_err(|_| "storage_changed")?;
            self.fence(backend, epoch)
        })();
        // The final post-publication fence is the capture acceptance point.
        // Removing the hidden staging file makes an already accepted image listable.
        // A later Privacy transition does not invalidate a completed capture.
        let result = result.and_then(|()| image.complete().map_err(|_| "publish_failed"));
        if result.is_err() {
            self.discard(image)?;
        }
        result
    }
}

struct StorageActivity<'a>(&'a Service);
impl Drop for StorageActivity<'_> {
    fn drop(&mut self) {
        self.0.storage_active.store(false, Ordering::Release);
    }
}

pub(super) struct StoragePause<'a>(&'a Service);
impl Drop for StoragePause<'_> {
    fn drop(&mut self) {
        self.0.maintenance.store(false, Ordering::Release);
    }
}

impl RaptorBackend {
    pub fn start_timelapse(self: &Arc<Self>) -> Result<(), BackendError> {
        self.timelapse.start(Arc::downgrade(self))
    }
    pub(super) fn timelapse_request(
        &self,
        method: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        match method {
            "GET" if body.is_empty() => Ok(BackendResponse::json(
                self.timelapse.value().to_json().into_bytes(),
            )),
            "POST" if !body.is_empty() => self.timelapse.submit(body, deadline),
            _ => Err(BackendError::Protocol),
        }
    }
}
