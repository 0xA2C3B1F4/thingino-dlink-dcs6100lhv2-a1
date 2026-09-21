//! One bounded SD reader. Timeouts abandon replies, never spawn replacement workers.
use super::*;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32};
use std::sync::{Arc, Mutex, Weak, mpsc};
use std::thread;

pub(super) const WAIT: Duration = Duration::from_millis(150);
#[cfg(test)]
type Hook = Arc<dyn Fn() + Send + Sync>;
pub(in crate::raptor_backend) struct Reads {
    sender: Mutex<Option<mpsc::SyncSender<Job>>>,
    diagnostics: Arc<StorageDiagnostics>,
    format_sender: Mutex<Option<mpsc::SyncSender<FormatJob>>>,
    format_active: AtomicBool,
    format_state: Mutex<FormatState>,
    #[cfg(test)]
    pub(super) hook: Arc<Mutex<Option<Hook>>>,
    #[cfg(test)]
    admission_hook: Arc<Mutex<Option<Hook>>>,
    #[cfg(test)]
    pub(super) submitted: std::sync::atomic::AtomicUsize,
}
impl Default for Reads {
    fn default() -> Self {
        Self {
            sender: Mutex::new(None),
            diagnostics: Arc::new(StorageDiagnostics::default()),
            format_sender: Mutex::new(None),
            format_active: AtomicBool::new(false),
            format_state: Mutex::new(FormatState::default()),
            #[cfg(test)]
            hook: Arc::new(Mutex::new(None)),
            #[cfg(test)]
            admission_hook: Arc::new(Mutex::new(None)),
            #[cfg(test)]
            submitted: std::sync::atomic::AtomicUsize::new(0),
        }
    }
}
struct FormatJob {
    cid: String,
}
struct FormatState {
    phase: &'static str,
    last_output: String,
    sd_snapshot: Vec<u8>,
}
impl Default for FormatState {
    fn default() -> Self {
        Self {
            phase: "idle",
            last_output: String::new(),
            sd_snapshot: Vec::new(),
        }
    }
}
enum FormatRunError {
    Backend(BackendError),
    Uncertain,
}
impl From<BackendError> for FormatRunError {
    fn from(value: BackendError) -> Self {
        Self::Backend(value)
    }
}
enum Operation {
    List(String),
    Delete(String),
    Identity(String),
    Sd,
    Drain,
}
impl Operation {
    fn diagnostic_route(&self) -> &'static str {
        match self {
            Self::List(_) | Self::Delete(_) => "files",
            Self::Identity(_) => "media-identity",
            Self::Sd => "storage-sd",
            Self::Drain => "storage-drain",
        }
    }
}
enum Reply {
    Json(BackendResponse),
    Identity(Option<MediaFileIdentity>),
}
struct Job {
    operation: Operation,
    deadline: Instant,
    submitted: Instant,
    reply: mpsc::SyncSender<Result<Reply, BackendError>>,
}
#[derive(Default)]
struct StorageDiagnostics {
    submit_expired: AtomicU32,
    submit_full: AtomicU32,
    submit_unavailable: AtomicU32,
    reply_timeout: AtomicU32,
    reply_disconnect: AtomicU32,
    queued_expiry: AtomicU32,
    worker_start: AtomicU32,
    worker_end: AtomicU32,
    worker_error: AtomicU32,
    late_completion: AtomicU32,
}
impl StorageDiagnostics {
    fn record(
        &self,
        counter: &AtomicU32,
        route: &'static str,
        stage: &'static str,
        outcome: &'static str,
        elapsed: Duration,
    ) -> Option<String> {
        let previous = counter
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |count| {
                Some(count.saturating_add(1))
            })
            .unwrap();
        let count = previous.saturating_add(1);
        if previous == u32::MAX || (count > 8 && !count.is_power_of_two()) {
            return None;
        }
        Some(format!(
            "storage-diagnostic component=sd-worker route={route} stage={stage} outcome={outcome} elapsed_ms={} count={count}",
            elapsed.as_millis()
        ))
    }

    fn log(
        &self,
        counter: &AtomicU32,
        route: &'static str,
        stage: &'static str,
        outcome: &'static str,
        elapsed: Duration,
    ) {
        if let Some(record) = self.record(counter, route, stage, outcome, elapsed) {
            eprintln!("{record}");
        }
    }

    fn error(error: &BackendError) -> &'static str {
        match error {
            BackendError::Connection => "connection",
            BackendError::Timeout => "timeout",
            BackendError::Protocol => "protocol",
            BackendError::Unavailable => "unavailable",
            BackendError::Busy => "busy",
            BackendError::Unsupported(_) => "unsupported",
            BackendError::PartialApply(_) => "partial",
            BackendError::Upstream(_) => "upstream",
        }
    }
}
pub(super) struct Reader {
    pub(super) timelapse: Arc<timelapse::Service>,
    conflicts: [PathBuf; 2],
    pub(super) pages: VecDeque<(String, Page)>,
    pub(super) cursor_serial: u64,
    pub(super) cursor_nonce: String,
}
impl Reader {
    pub(super) fn ensure_exclusive_owner(&self) -> Result<(), BackendError> {
        for path in &self.conflicts {
            if !fs::symlink_metadata(path).is_err_and(|e| e.kind() == io::ErrorKind::NotFound) {
                return Err(BackendError::Unavailable);
            }
        }
        Ok(())
    }
    fn run(&mut self, operation: Operation, deadline: Instant) -> Result<Reply, BackendError> {
        match operation {
            Operation::List(target) => self
                .recording_files("GET", &target, b"", deadline)
                .map(Reply::Json),
            Operation::Delete(target) => self.delete_recording(&target).map(Reply::Json),
            Operation::Identity(target) => Ok(Reply::Identity(self.recording_identity(&target))),
            Operation::Sd => self.recording_sd().map(Reply::Json),
            Operation::Drain => {
                self.pages.clear();
                Ok(Reply::Json(BackendResponse::json(
                    br#"{"status":"drained"}"#.to_vec(),
                )))
            }
        }
    }
}
impl Reads {
    fn submit(
        &self,
        backend: &RaptorBackend,
        operation: Operation,
        deadline: Instant,
    ) -> Result<Reply, BackendError> {
        let started = Instant::now();
        let route = operation.diagnostic_route();
        let drain = matches!(&operation, Operation::Drain);
        let deadline = if drain {
            deadline
        } else {
            deadline.min(Instant::now() + WAIT)
        };
        if Instant::now() >= deadline {
            self.diagnostics.log(
                &self.diagnostics.submit_expired,
                route,
                "submit",
                "deadline-expired",
                started.elapsed(),
            );
            return Err(BackendError::Timeout);
        }
        #[cfg(test)]
        if !drain {
            let callback = self.admission_hook.lock().unwrap().clone();
            if let Some(callback) = callback {
                callback();
            }
        }
        let (reply, receive) = mpsc::sync_channel(1);
        let mut slot = match self.sender.lock() {
            Ok(slot) => slot,
            Err(_) => {
                self.diagnostics.log(
                    &self.diagnostics.submit_unavailable,
                    route,
                    "submit",
                    "unavailable",
                    started.elapsed(),
                );
                return Err(BackendError::Unavailable);
            }
        };
        if !drain && self.format_active.load(Ordering::Acquire) {
            return Err(BackendError::Busy);
        }
        if slot.is_none() {
            let (send, receive) = mpsc::sync_channel::<Job>(1);
            let mut reader = Reader {
                timelapse: Arc::clone(&backend.timelapse),
                conflicts: [
                    backend.conflicting_owner_socket.clone(),
                    backend.conflicting_owner_pid.clone(),
                ],
                pages: VecDeque::new(),
                cursor_serial: 0,
                cursor_nonce: String::new(),
            };
            #[cfg(test)]
            let hook = Arc::clone(&self.hook);
            let diagnostics = Arc::clone(&self.diagnostics);
            if thread::Builder::new()
                .name("raptor-sd-read".into())
                .spawn(move || {
                    loop {
                        reader
                            .pages
                            .retain(|(_, page)| page.expires > Instant::now());
                        let job = match receive.recv_timeout(Duration::from_secs(1)) {
                            Ok(job) => job,
                            Err(mpsc::RecvTimeoutError::Timeout) => continue,
                            Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        };
                        let route = job.operation.diagnostic_route();
                        // Expired queued work must not touch the disk after a stalled read returns.
                        if Instant::now() >= job.deadline {
                            diagnostics.log(
                                &diagnostics.queued_expiry,
                                route,
                                "queue",
                                "expired",
                                job.submitted.elapsed(),
                            );
                            continue;
                        }
                        let worker_started = Instant::now();
                        diagnostics.log(
                            &diagnostics.worker_start,
                            route,
                            "worker",
                            "start",
                            job.submitted.elapsed(),
                        );
                        #[cfg(test)]
                        {
                            let callback = hook.lock().unwrap().clone();
                            if let Some(callback) = callback {
                                callback();
                            }
                        }
                        let result = reader.run(job.operation, job.deadline);
                        let worker_elapsed = worker_started.elapsed();
                        let error = result.as_ref().err().map(StorageDiagnostics::error);
                        let before_deadline = Instant::now() < job.deadline;
                        let receiver_gone = before_deadline && job.reply.try_send(result).is_err();
                        if let Some(error) = error {
                            diagnostics.log(
                                &diagnostics.worker_error,
                                route,
                                "worker",
                                error,
                                worker_elapsed,
                            );
                        } else {
                            diagnostics.log(
                                &diagnostics.worker_end,
                                route,
                                "worker",
                                "success",
                                worker_elapsed,
                            );
                        }
                        if !before_deadline {
                            diagnostics.log(
                                &diagnostics.late_completion,
                                route,
                                "completion",
                                "deadline-expired",
                                job.submitted.elapsed(),
                            );
                        } else if receiver_gone {
                            diagnostics.log(
                                &diagnostics.late_completion,
                                route,
                                "completion",
                                "receiver-gone",
                                job.submitted.elapsed(),
                            );
                        }
                    }
                })
                .is_err()
            {
                drop(slot);
                self.diagnostics.log(
                    &self.diagnostics.submit_unavailable,
                    route,
                    "submit",
                    "unavailable",
                    started.elapsed(),
                );
                return Err(BackendError::Unavailable);
            }
            *slot = Some(send);
        }
        let submitted = Instant::now();
        match slot.as_ref().unwrap().try_send(Job {
            operation,
            deadline,
            submitted,
            reply,
        }) {
            Ok(()) => {}
            Err(mpsc::TrySendError::Full(_)) => {
                drop(slot);
                self.diagnostics.log(
                    &self.diagnostics.submit_full,
                    route,
                    "submit",
                    "full",
                    started.elapsed(),
                );
                return Err(BackendError::Busy);
            }
            Err(mpsc::TrySendError::Disconnected(_)) => {
                drop(slot);
                self.diagnostics.log(
                    &self.diagnostics.submit_unavailable,
                    route,
                    "submit",
                    "unavailable",
                    started.elapsed(),
                );
                return Err(BackendError::Unavailable);
            }
        }
        drop(slot);
        #[cfg(test)]
        self.submitted.fetch_add(1, Ordering::AcqRel);
        match receive.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.diagnostics.log(
                    &self.diagnostics.reply_timeout,
                    route,
                    "reply",
                    "timeout",
                    started.elapsed(),
                );
                Err(BackendError::Timeout)
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.diagnostics.log(
                    &self.diagnostics.reply_disconnect,
                    route,
                    "reply",
                    "disconnected",
                    started.elapsed(),
                );
                Err(BackendError::Timeout)
            }
        }
    }

    fn activate_format(&self, sd_snapshot: Vec<u8>) -> Result<(), BackendError> {
        // This is the admission boundary for every external reader job. Jobs
        // already accepted are ordered ahead of Drain; later jobs cannot enter.
        let _admission = self.sender.lock().map_err(|_| BackendError::Unavailable)?;
        let mut state = self
            .format_state
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        if self
            .format_active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(BackendError::Busy);
        }
        state.phase = "queued";
        state.last_output.clear();
        state.sd_snapshot = sd_snapshot;
        Ok(())
    }
}
impl RaptorBackend {
    pub(in crate::raptor_backend) fn recording_identity(
        &self,
        target: &str,
        deadline: Instant,
    ) -> Result<Option<MediaFileIdentity>, BackendError> {
        // Live preflight must never queue behind SD work.
        if !target.starts_with("/media/v1/file?") {
            return Ok(None);
        }
        if self.file_reads.format_active.load(Ordering::Acquire) {
            return Err(BackendError::Busy);
        }
        match self
            .file_reads
            .submit(self, Operation::Identity(target.into()), deadline)?
        {
            Reply::Identity(value) => Ok(value),
            _ => Err(BackendError::Protocol),
        }
    }
    pub(in crate::raptor_backend) fn recording_files(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        if self.file_reads.format_active.load(Ordering::Acquire) {
            return Err(BackendError::Busy);
        }
        if !body.is_empty() {
            return Err(BackendError::Protocol);
        }
        let operation = match method {
            "GET" => Operation::List(target.into()),
            "POST" if target.starts_with("/api/v1/files?rm=") => {
                // Serialize user mutations while the bounded worker performs the
                // final descriptor-anchored identity checks and unlink.
                let _guard = self.lock_mutation()?;
                return match self.file_reads.submit(
                    self,
                    Operation::Delete(target.into()),
                    deadline,
                )? {
                    Reply::Json(value) => Ok(value),
                    _ => Err(BackendError::Protocol),
                };
            }
            _ => return Err(BackendError::Protocol),
        };
        match self.file_reads.submit(self, operation, deadline)? {
            Reply::Json(value) => Ok(value),
            _ => Err(BackendError::Protocol),
        }
    }
    pub(in crate::raptor_backend) fn recording_sd(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        if self.file_reads.format_active.load(Ordering::Acquire) {
            let body = self
                .file_reads
                .format_state
                .lock()
                .map_err(|_| BackendError::Unavailable)?
                .sd_snapshot
                .clone();
            if body.is_empty() {
                return Err(BackendError::Busy);
            }
            return self
                .file_reads
                .decorate_sd(self, BackendResponse::json(body));
        }
        match self.file_reads.submit(self, Operation::Sd, deadline)? {
            Reply::Json(value) => self.file_reads.decorate_sd(self, value),
            _ => Err(BackendError::Protocol),
        }
    }

    pub fn start_storage_format(self: &Arc<Self>) -> Result<(), BackendError> {
        let mut sender = self
            .file_reads
            .format_sender
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        if sender.is_some() {
            return Ok(());
        }
        let (send, receive) = mpsc::sync_channel::<FormatJob>(1);
        let backend = Arc::downgrade(self);
        thread::Builder::new()
            .name("raptor-sd-format".into())
            .stack_size(256 * 1024)
            .spawn(move || format_loop(backend, receive))
            .map_err(|_| BackendError::Unavailable)?;
        *sender = Some(send);
        Ok(())
    }

    pub(in crate::raptor_backend) fn queue_storage_format(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.len() != 3
            || fields.get("action").and_then(Value::as_str) != Some("format")
            || fields.get("filesystem").and_then(Value::as_str) != Some("fat32")
            || fields.get("confirm").and_then(Value::as_str) != Some("erase")
        {
            return Err(BackendError::Protocol);
        }
        let sd_snapshot = match self.file_reads.submit(self, Operation::Sd, deadline)? {
            Reply::Json(value) => value.body,
            _ => return Err(BackendError::Protocol),
        };
        let cid = self.host.raptor_storage_format_target()?;
        self.file_reads.activate_format(sd_snapshot)?;
        let queued = self
            .file_reads
            .format_sender
            .lock()
            .ok()
            .and_then(|sender| sender.clone())
            .is_some_and(|sender| sender.try_send(FormatJob { cid }).is_ok());
        if !queued {
            self.file_reads
                .format_active
                .store(false, Ordering::Release);
            if let Ok(mut state) = self.file_reads.format_state.lock() {
                state.phase = "idle";
            }
            return Err(BackendError::Unavailable);
        }
        Ok(BackendResponse::json(
            br#"{"status":"queued","filesystem":"fat32"}"#.to_vec(),
        ))
    }

    fn run_storage_format(
        &self,
        cid: &str,
        deadline: Instant,
        restore_reserve: Duration,
    ) -> Result<String, FormatRunError> {
        let _mutation = self.lock_mutation()?;
        let pause_deadline = deadline.min(Instant::now() + Duration::from_secs(3));
        let _timelapse_pause = self.timelapse.pause_storage(pause_deadline)?;
        match self.file_reads.submit(
            self,
            Operation::Drain,
            deadline.min(Instant::now() + Duration::from_secs(2)),
        )? {
            Reply::Json(_) => {}
            _ => return Err(FormatRunError::Backend(BackendError::Protocol)),
        }

        let rmr1_absent = self.client.daemon_socket_absent(RaptorDaemon::Rmr1)?;
        let selected = [true, !rmr1_absent];
        let mut observations = [Some(self.recording_observation(0, deadline)?), None];
        if rmr1_absent {
            let stream1 = self.stream_enable_observation(1, deadline)?;
            if stream1.active {
                return Err(BackendError::Unavailable.into());
            }
        } else {
            observations[1] = Some(self.recording_observation(1, deadline)?);
        }

        let mut prior = [false; 2];
        for channel in 0..2_usize {
            if !selected[channel] {
                continue;
            }
            let observation = observations[channel]
                .take()
                .ok_or(BackendError::Unavailable)?;
            prior[channel] = observation
                .value
                .get_path("recording")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Unavailable)?;
        }

        let mut stop_attempted = [false; 2];
        let mut maintenance_attempted = [false; 2];
        let operation = (|| -> Result<String, FormatRunError> {
            for channel in 0..2_u64 {
                if !selected[channel as usize] {
                    continue;
                }
                if prior[channel as usize] {
                    stop_attempted[channel as usize] = true;
                    self.set_recording(
                        &obj([("stop", obj([("channel", number(channel))]))]),
                        deadline,
                    )?;
                }
            }
            // A separate observation after both transitions is the format barrier.
            // Null or transitional writer state is not quiescence.
            for channel in 0..2_u64 {
                if !selected[channel as usize] {
                    continue;
                }
                let observation = self.recording_observation(channel, deadline)?;
                if observation
                    .value
                    .get_path("recording")
                    .and_then(Value::as_bool)
                    != Some(false)
                    || observation
                        .value
                        .get_path("file_closed")
                        .and_then(Value::as_bool)
                        != Some(true)
                {
                    return Err(BackendError::Unavailable.into());
                }
            }
            for channel in 0..2_u64 {
                if !selected[channel as usize] {
                    continue;
                }
                maintenance_attempted[channel as usize] = true;
                self.set_storage_maintenance(channel, true, deadline)?;
            }
            let worker_deadline = deadline
                .checked_sub(restore_reserve)
                .ok_or(BackendError::Timeout)?;
            if Instant::now() >= worker_deadline {
                return Err(BackendError::Timeout.into());
            }
            match self.host.format_raptor_storage(cid, worker_deadline) {
                Ok(output) => Ok(output),
                Err(
                    crate::camera::RaptorStorageWorkerError::BeforeDispatch
                    | crate::camera::RaptorStorageWorkerError::Rejected,
                ) => Err(BackendError::Unavailable.into()),
                Err(crate::camera::RaptorStorageWorkerError::Uncertain) => {
                    Err(FormatRunError::Uncertain)
                }
            }
        })();

        if matches!(&operation, Err(FormatRunError::Uncertain)) {
            // No terminal worker response means mkfs/remount state is unknown.
            // Keep all admission and writer barriers latched until process reboot.
            std::mem::forget(_timelapse_pause);
            std::mem::forget(_mutation);
            return operation;
        }

        let mut maintenance_restored = true;
        for channel in 0..2_u64 {
            if maintenance_attempted[channel as usize]
                && self
                    .set_storage_maintenance(channel, false, deadline)
                    .is_err()
            {
                maintenance_restored = false;
            }
        }
        if !maintenance_restored {
            return Err(BackendError::PartialApply(
                "SD maintenance reached a terminal result, but recorder storage maintenance did not resume. Keep recording stopped and perform a normal reboot before retrying.",
            )
            .into());
        }

        let mut restored = true;
        for channel in 0..2_u64 {
            if stop_attempted[channel as usize]
                && self
                    .restore_recording_after_maintenance(channel, deadline)
                    .is_err()
            {
                restored = false;
            }
        }
        if !restored {
            return Err(BackendError::PartialApply(
                "SD maintenance reached a terminal result, but prior recorder intent was not restored. Reload both recorder states before an explicit retry.",
            )
            .into());
        }
        operation
    }

    fn restore_recording_after_maintenance(
        &self,
        channel: u64,
        deadline: Instant,
    ) -> Result<(), BackendError> {
        let until = deadline.min(Instant::now() + Duration::from_secs(5));
        loop {
            let observation = self.recording_observation(channel, until)?;
            if observation
                .value
                .get_path("available")
                .and_then(Value::as_bool)
                == Some(true)
            {
                return self
                    .set_recording(
                        &obj([("start", obj([("channel", number(channel))]))]),
                        until,
                    )
                    .map(|_| ());
            }
            if Instant::now() + Duration::from_millis(40) >= until {
                return Err(BackendError::Timeout);
            }
            thread::sleep(Duration::from_millis(40));
        }
    }
}

fn format_loop(backend: Weak<RaptorBackend>, receive: mpsc::Receiver<FormatJob>) {
    while let Ok(job) = receive.recv() {
        let Some(backend) = backend.upgrade() else {
            break;
        };
        if let Ok(mut state) = backend.file_reads.format_state.lock() {
            state.phase = "running";
        }
        let deadline = Instant::now() + Duration::from_secs(110);
        let result = backend.run_storage_format(&job.cid, deadline, Duration::from_secs(10));
        let uncertain = matches!(&result, Err(FormatRunError::Uncertain));
        if let Ok(mut state) = backend.file_reads.format_state.lock() {
            match result {
                Ok(output) => {
                    state.phase = "succeeded";
                    state.last_output = output;
                }
                Err(FormatRunError::Backend(BackendError::PartialApply(message))) => {
                    state.phase = "failed";
                    state.last_output = message.to_owned();
                }
                Err(FormatRunError::Backend(_)) => {
                    state.phase = "failed";
                    state.last_output =
                        "Formatting did not complete; reload card and recorder state before retrying."
                            .to_owned();
                }
                Err(FormatRunError::Uncertain) => {
                    state.phase = "failed";
                    state.last_output = "Storage worker completion is unknown. Recording and SD access remain paused; perform a normal reboot before recovery or retry."
                        .to_owned();
                }
            }
        }
        if !uncertain {
            backend
                .file_reads
                .format_active
                .store(false, Ordering::Release);
        }
    }
}

impl Reads {
    fn decorate_sd(
        &self,
        backend: &RaptorBackend,
        response: BackendResponse,
    ) -> Result<BackendResponse, BackendError> {
        let mut value = crate::json::parse(&response.body).map_err(|_| BackendError::Protocol)?;
        let target_ready = backend.host.raptor_storage_format_target().is_ok();
        let state = self
            .format_state
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        let uncertain_latch = self.format_active.load(Ordering::Acquire) && state.phase == "failed";
        let supported = target_ready && !uncertain_latch;
        value
            .set_path("data.format.supported", Value::Bool(supported))
            .and_then(|()| {
                value.set_path(
                    "data.format.options",
                    Value::Array(if supported {
                        vec![obj([
                            ("id", Value::String("fat32".to_owned())),
                            ("label", Value::String("FAT32".to_owned())),
                            (
                                "description",
                                Value::String(
                                    "Best compatibility for camera recordings.".to_owned(),
                                ),
                            ),
                        ])]
                    } else {
                        vec![]
                    }),
                )
            })
            .and_then(|()| {
                value.set_path("data.format.status", Value::String(state.phase.to_owned()))
            })
            .and_then(|()| {
                value.set_path(
                    "data.format.last_output_b64",
                    Value::String(crate::camera::base64_encode(state.last_output.as_bytes())),
                )
            })
            .map_err(|_| BackendError::Protocol)?;
        Ok(BackendResponse::json(value.to_json().into_bytes()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::camera::{CameraPaths, HostBackend};
    use crate::raptor_backend::tests::{backend, framed, read_request, serve_daemon, task_temp};
    use std::os::unix::net::UnixListener;
    use std::path::Path;

    #[test]
    fn storage_diagnostics_are_bounded_redacted_and_category_independent() {
        let diagnostics = StorageDiagnostics::default();
        let emitted = (1..=32)
            .filter(|_| {
                diagnostics
                    .record(
                        &diagnostics.reply_timeout,
                        "files",
                        "reply",
                        "timeout",
                        Duration::from_millis(150),
                    )
                    .is_some()
            })
            .collect::<Vec<_>>();
        assert_eq!(emitted, vec![1, 2, 3, 4, 5, 6, 7, 8, 16, 32]);
        for _ in 0..32 {
            diagnostics.record(
                &diagnostics.worker_end,
                "storage-sd",
                "worker",
                "success",
                Duration::from_millis(2),
            );
        }
        let private = BackendError::PartialApply("DO_NOT_LOG_PRIVATE_DETAIL");
        let error = diagnostics
            .record(
                &diagnostics.worker_error,
                "storage-sd",
                "worker",
                StorageDiagnostics::error(&private),
                Duration::from_millis(3),
            )
            .unwrap();
        assert!(error.contains("component=sd-worker route=storage-sd"));
        assert!(error.contains("stage=worker outcome=partial elapsed_ms=3 count=1"));
        assert!(!error.contains("DO_NOT_LOG") && !error.contains("component=router"));
        diagnostics.reply_timeout.store(u32::MAX, Ordering::Relaxed);
        assert!(
            diagnostics
                .record(
                    &diagnostics.reply_timeout,
                    "files",
                    "reply",
                    "timeout",
                    Duration::ZERO,
                )
                .is_none()
        );
        assert_eq!(diagnostics.reply_timeout.load(Ordering::Relaxed), u32::MAX);
    }

    #[test]
    fn caller_deadline_below_wait_stays_bounded_and_queued_job_expires() {
        let root = task_temp("short-storage-deadline");
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let calls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let observed = Arc::clone(&calls);
        let (started_send, started_receive) = mpsc::sync_channel(1);
        let (release_send, release_receive) = mpsc::sync_channel(1);
        let release_receive = Arc::new(Mutex::new(release_receive));
        let release = Arc::clone(&release_receive);
        *backend.file_reads.hook.lock().unwrap() = Some(Arc::new(move || {
            if observed.fetch_add(1, Ordering::AcqRel) == 0 {
                started_send.send(()).unwrap();
                release
                    .lock()
                    .unwrap()
                    .recv_timeout(Duration::from_secs(1))
                    .unwrap();
            }
        }));
        let request = |backend: Arc<RaptorBackend>| {
            thread::spawn(move || {
                let started = Instant::now();
                let result = backend.recording_sd(Instant::now() + Duration::from_millis(40));
                (result, started.elapsed())
            })
        };
        let first = request(Arc::clone(&backend));
        started_receive
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        let second = request(Arc::clone(&backend));
        let until = Instant::now() + Duration::from_secs(1);
        while backend.file_reads.submitted.load(Ordering::Acquire) < 2 {
            assert!(Instant::now() < until);
            thread::yield_now();
        }
        assert_eq!(
            backend.recording_sd(Instant::now() + Duration::from_millis(40)),
            Err(BackendError::Busy)
        );
        assert_eq!(
            backend
                .file_reads
                .diagnostics
                .submit_full
                .load(Ordering::Acquire),
            1
        );
        for (result, elapsed) in [first.join().unwrap(), second.join().unwrap()] {
            assert_eq!(result, Err(BackendError::Timeout));
            assert!(elapsed < WAIT, "elapsed={elapsed:?}");
        }
        assert_eq!(
            backend
                .file_reads
                .diagnostics
                .reply_timeout
                .load(Ordering::Acquire),
            2
        );
        assert_eq!(
            backend
                .file_reads
                .diagnostics
                .worker_start
                .load(Ordering::Acquire),
            1
        );
        release_send.send(()).unwrap();
        let until = Instant::now() + Duration::from_secs(1);
        while backend
            .file_reads
            .diagnostics
            .queued_expiry
            .load(Ordering::Acquire)
            == 0
        {
            assert!(Instant::now() < until);
            thread::yield_now();
        }
        assert_eq!(
            backend
                .file_reads
                .diagnostics
                .late_completion
                .load(Ordering::Acquire),
            1
        );
        let _ = backend.recording_sd(Instant::now() + Duration::from_millis(100));
        assert_eq!(
            calls.load(Ordering::Acquire),
            2,
            "expired queued job must not reach the worker hook"
        );
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    fn recorder(
        root: &Path,
        channel: u64,
        state: Arc<AtomicBool>,
        maintenance: Arc<AtomicBool>,
        stop: Arc<AtomicBool>,
    ) -> thread::JoinHandle<()> {
        recorder_with_pause_failure(root, channel, state, maintenance, stop, false)
    }

    fn recorder_with_pause_failure(
        root: &Path,
        channel: u64,
        state: Arc<AtomicBool>,
        maintenance: Arc<AtomicBool>,
        stop: Arc<AtomicBool>,
        reject_pause: bool,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join(format!("rmr{channel}.sock"))).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            while !stop.load(Ordering::Acquire) {
                let (mut socket, _) = match listener.accept() {
                    Ok(value) => value,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(error) => panic!("recorder listener failed: {error}"),
                };
                let request = read_request(&mut socket);
                let request = crate::json::parse(&request).unwrap();
                let response = match request.get_path("cmd").and_then(Value::as_str) {
                    Some("get-recording-state") => {
                        let recording = state.load(Ordering::Acquire);
                        format!(
                            r#"{{"status":"ok","stream_id":{channel},"available":true,"recording":{recording},"file_closed":{},"reason":null}}"#,
                            !recording
                        )
                        .into_bytes()
                    }
                    Some("set-recording") => {
                        state.store(
                            request
                                .get_path("enabled")
                                .and_then(Value::as_bool)
                                .unwrap(),
                            Ordering::Release,
                        );
                        br#"{"status":"ok"}"#.to_vec()
                    }
                    Some("get-storage-maintenance") => {
                        let paused = maintenance.load(Ordering::Acquire);
                        format!(
                            r#"{{"status":"ok","stream_id":{channel},"paused":{paused},"quiescent":{paused}}}"#
                        )
                        .into_bytes()
                    }
                    Some("set-storage-maintenance") => {
                        let paused = request.get_path("paused").and_then(Value::as_bool).unwrap();
                        if paused && reject_pause {
                            socket.write_all(&framed(br#"{"status":"error"}"#)).unwrap();
                            continue;
                        }
                        maintenance.store(paused, Ordering::Release);
                        format!(
                            r#"{{"status":"ok","stream_id":{channel},"paused":{paused},"quiescent":{paused}}}"#
                        )
                        .into_bytes()
                    }
                    other => panic!("unexpected recorder command: {other:?}"),
                };
                socket.write_all(&framed(&response)).unwrap();
            }
        })
    }

    fn storage_backend(root: &Path) -> (Arc<RaptorBackend>, UnixListener) {
        let mount = root.join("card");
        fs::create_dir_all(mount.join("raptor/timelapse")).unwrap();
        let block = root.join("block");
        fs::create_dir_all(block.join("mmcblk0/device")).unwrap();
        fs::write(block.join("mmcblk0/device/cid"), b"0123456789abcdef\n").unwrap();
        let mounts = root.join("mounts");
        fs::write(
            &mounts,
            format!("/dev/mmcblk0p1 {} vfat rw 0 0\n", mount.display()),
        )
        .unwrap();
        let worker = root.join("storage.sock");
        let listener = UnixListener::bind(&worker).unwrap();
        let mut value = backend(root, "127.0.0.1:9".parse().unwrap());
        let paths = CameraPaths {
            proc_mounts: mounts,
            sys_class_block: block,
            storage_worker_socket: worker,
            storage_mountpoint: mount.clone(),
            media_roots: vec![root.to_path_buf()],
            ..CameraPaths::default()
        };
        value.host = HostBackend::new(paths);
        value.timelapse = Arc::new(timelapse::Service::new(
            root.join("policy.json"),
            timelapse::storage::Store {
                mount,
                fixture: true,
                ..timelapse::storage::Store::default()
            },
        ));
        (Arc::new(value), listener)
    }

    #[test]
    fn expired_cursor_and_expired_request_are_rejected_without_continuation() {
        let root = task_temp("read-expiry");
        fs::create_dir_all(root.join("sd/raptor/timelapse")).unwrap();
        let mut reader = Reader {
            timelapse: Arc::new(timelapse::Service::new(
                root.join("policy.json"),
                timelapse::storage::Store {
                    mount: root.join("sd"),
                    fixture: true,
                    ..timelapse::storage::Store::default()
                },
            )),
            conflicts: [root.join("absent.sock"), root.join("absent.pid")],
            pages: VecDeque::new(),
            cursor_serial: 0,
            cursor_nonce: "abc".into(),
        };
        let target = format!("/api/v1/files?cd={ROOT}/timelapse");
        // A short scan budget produces a continuation without reading an entry.
        let first = reader
            .recording_files(
                "GET",
                &target,
                b"",
                Instant::now() + Duration::from_millis(20),
            )
            .unwrap();
        let first = crate::json::parse(&first.body).unwrap();
        let cursor = first.get_path("next_cursor").unwrap().as_str().unwrap();
        reader.pages[0].1.expires = Instant::now() - Duration::from_millis(1);
        assert_eq!(
            reader.recording_files(
                "GET",
                &format!("{target}&cursor={cursor}"),
                b"",
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::Protocol)
        );
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert_eq!(
            backend.recording_sd(Instant::now()),
            Err(BackendError::Timeout)
        );
        assert!(backend.file_reads.sender.lock().unwrap().is_none());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn format_activation_closes_the_reader_admission_race_before_drain() {
        let root = task_temp("format-reader-admission");
        let backend = Arc::new(backend(&root, "127.0.0.1:9".parse().unwrap()));
        let (entered_send, entered_receive) = mpsc::sync_channel(1);
        let (release_send, release_receive) = mpsc::sync_channel(1);
        let release_receive = Mutex::new(release_receive);
        *backend.file_reads.admission_hook.lock().unwrap() = Some(Arc::new(move || {
            entered_send.send(()).unwrap();
            release_receive
                .lock()
                .unwrap()
                .recv_timeout(Duration::from_secs(2))
                .unwrap();
        }));
        let request_backend = Arc::clone(&backend);
        let request = thread::spawn(move || {
            request_backend.recording_files(
                "GET",
                "/api/v1/files?cd=/mnt/mmcblk0p1/raptor/stream0",
                b"",
                Instant::now() + Duration::from_secs(2),
            )
        });
        entered_receive
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        backend
            .file_reads
            .activate_format(
                br#"{"ok":true,"data":{"format":{"supported":false,"options":[],"status":"idle","last_output_b64":""}}}"#
                    .to_vec(),
            )
            .unwrap();
        release_send.send(()).unwrap();
        assert_eq!(request.join().unwrap(), Err(BackendError::Busy));
        assert_eq!(backend.file_reads.submitted.load(Ordering::Acquire), 0);
        backend
            .file_reads
            .format_active
            .store(false, Ordering::Release);
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn format_quiesces_both_writers_bars_reads_and_restores_prior_intent() {
        let root = task_temp("format-orchestration");
        let (backend, worker) = storage_backend(&root);
        let rmr0 = Arc::new(AtomicBool::new(true));
        let rmr1 = Arc::new(AtomicBool::new(false));
        let maintenance0 = Arc::new(AtomicBool::new(false));
        let maintenance1 = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let channel0 = recorder(
            &root,
            0,
            Arc::clone(&rmr0),
            Arc::clone(&maintenance0),
            Arc::clone(&stop),
        );
        let channel1 = recorder(
            &root,
            1,
            Arc::clone(&rmr1),
            Arc::clone(&maintenance1),
            Arc::clone(&stop),
        );
        let (accepted_send, accepted_receive) = mpsc::sync_channel(1);
        let (release_send, release_receive) = mpsc::sync_channel(1);
        let worker_maintenance0 = Arc::clone(&maintenance0);
        let worker_maintenance1 = Arc::clone(&maintenance1);
        let worker_thread = thread::spawn(move || {
            let (mut socket, _) = worker.accept().unwrap();
            let mut header = [0_u8; 4];
            socket.read_exact(&mut header).unwrap();
            let mut request = vec![0_u8; u32::from_be_bytes(header) as usize];
            socket.read_exact(&mut request).unwrap();
            assert_eq!(&request[..3], &[1, 1, 16]);
            assert_eq!(&request[3..], b"0123456789abcdef");
            assert!(worker_maintenance0.load(Ordering::Acquire));
            assert!(worker_maintenance1.load(Ordering::Acquire));
            accepted_send.send(()).unwrap();
            release_receive
                .recv_timeout(Duration::from_secs(2))
                .unwrap();
            let response = b"\x01\x01formatted-fat32";
            socket
                .write_all(&(response.len() as u32).to_be_bytes())
                .and_then(|()| socket.write_all(response))
                .unwrap();
        });

        backend.start_storage_format().unwrap();
        backend
            .queue_storage_format(
                br#"{"action":"format","filesystem":"fat32","confirm":"erase"}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        accepted_receive
            .recv_timeout(Duration::from_secs(2))
            .unwrap();
        assert!(!rmr0.load(Ordering::Acquire));
        assert!(!rmr1.load(Ordering::Acquire));
        let submitted = backend.file_reads.submitted.load(Ordering::Acquire);
        let sd = backend
            .recording_sd(Instant::now() + Duration::from_secs(1))
            .unwrap();
        assert_eq!(
            backend.file_reads.submitted.load(Ordering::Acquire),
            submitted
        );
        let sd = crate::json::parse(&sd.body).unwrap();
        assert_eq!(
            sd.get_path("data.format.status").and_then(Value::as_str),
            Some("running")
        );
        let options = sd
            .get_path("data.format.options")
            .and_then(Value::as_array)
            .unwrap();
        assert_eq!(
            options[0].get_path("id").and_then(Value::as_str),
            Some("fat32")
        );
        release_send.send(()).unwrap();
        let until = Instant::now() + Duration::from_secs(2);
        loop {
            if backend.file_reads.format_state.lock().unwrap().phase == "succeeded" {
                break;
            }
            assert!(Instant::now() < until, "format worker did not finish");
            thread::sleep(Duration::from_millis(5));
        }
        assert!(rmr0.load(Ordering::Acquire));
        assert!(!rmr1.load(Ordering::Acquire));
        assert!(!maintenance0.load(Ordering::Acquire));
        assert!(!maintenance1.load(Ordering::Acquire));
        assert!(!backend.file_reads.format_active.load(Ordering::Acquire));

        worker_thread.join().unwrap();
        stop.store(true, Ordering::Release);
        channel0.join().unwrap();
        channel1.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn failed_second_recorder_pause_never_dispatches_format_and_restores_first() {
        let root = task_temp("format-pause-rejected");
        let (backend, worker) = storage_backend(&root);
        worker.set_nonblocking(true).unwrap();
        let recording0 = Arc::new(AtomicBool::new(true));
        let recording1 = Arc::new(AtomicBool::new(false));
        let maintenance0 = Arc::new(AtomicBool::new(false));
        let maintenance1 = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let channel0 = recorder(
            &root,
            0,
            Arc::clone(&recording0),
            Arc::clone(&maintenance0),
            Arc::clone(&stop),
        );
        let channel1 = recorder_with_pause_failure(
            &root,
            1,
            Arc::clone(&recording1),
            Arc::clone(&maintenance1),
            Arc::clone(&stop),
            true,
        );

        let result = backend.run_storage_format(
            "0123456789abcdef",
            Instant::now() + Duration::from_secs(3),
            Duration::from_secs(1),
        );
        assert!(matches!(result, Err(FormatRunError::Backend(_))));
        assert_eq!(
            worker.accept().unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert!(recording0.load(Ordering::Acquire));
        assert!(!recording1.load(Ordering::Acquire));
        assert!(!maintenance0.load(Ordering::Acquire));
        assert!(!maintenance1.load(Ordering::Acquire));
        assert!(!backend.timelapse.storage_paused());
        assert!(backend.lock_mutation().is_ok());

        stop.store(true, Ordering::Release);
        channel0.join().unwrap();
        channel1.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn terminal_worker_rejection_restores_prior_recording_and_timelapse_state() {
        for prior in [[false, false], [true, false], [false, true], [true, true]] {
            let root = task_temp("format-rejected-worker");
            let (backend, worker) = storage_backend(&root);
            let recording = prior.map(|enabled| Arc::new(AtomicBool::new(enabled)));
            let maintenance = [
                Arc::new(AtomicBool::new(false)),
                Arc::new(AtomicBool::new(false)),
            ];
            let stop = Arc::new(AtomicBool::new(false));
            let recorders: Vec<_> = (0..2)
                .map(|channel| {
                    recorder(
                        &root,
                        channel as u64,
                        Arc::clone(&recording[channel]),
                        Arc::clone(&maintenance[channel]),
                        Arc::clone(&stop),
                    )
                })
                .collect();
            let enabled =
                prior[1].then(|| super::super::recorder::tests::enabled_sequence(&root, 1, true));
            let paused = maintenance.clone();
            let worker_thread = thread::spawn(move || {
                let (mut socket, _) = worker.accept().unwrap();
                let mut header = [0_u8; 4];
                socket.read_exact(&mut header).unwrap();
                let mut request = vec![0_u8; u32::from_be_bytes(header) as usize];
                socket.read_exact(&mut request).unwrap();
                assert_eq!(&request[..3], &[1, 1, 16]);
                assert_eq!(&request[3..], b"0123456789abcdef");
                assert!(paused.iter().all(|state| state.load(Ordering::Acquire)));
                // A terminal refusal is different from a missing worker reply.
                // No real device or filesystem helper is used by this fixture.
                let response = b"\x01\x00card-not-mounted";
                socket
                    .write_all(&(response.len() as u32).to_be_bytes())
                    .and_then(|()| socket.write_all(response))
                    .unwrap();
            });

            let result = backend.run_storage_format(
                "0123456789abcdef",
                Instant::now() + Duration::from_secs(3),
                Duration::from_secs(1),
            );
            assert!(matches!(
                result,
                Err(FormatRunError::Backend(BackendError::Unavailable))
            ));
            for channel in 0..2 {
                assert_eq!(recording[channel].load(Ordering::Acquire), prior[channel]);
                assert!(!maintenance[channel].load(Ordering::Acquire));
            }
            assert!(!backend.timelapse.storage_paused());
            assert!(backend.lock_mutation().is_ok());

            worker_thread.join().unwrap();
            if let Some(enabled) = enabled {
                enabled.join().unwrap();
            }
            stop.store(true, Ordering::Release);
            for recorder in recorders {
                recorder.join().unwrap();
            }
            drop(backend);
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn disabled_substream_format_preserves_main_writer_barriers_without_rmr1() {
        let root = task_temp("format-disabled-substream");
        let (backend, worker) = storage_backend(&root);
        let rvd = serve_daemon(
            &root,
            "rvd.sock",
            br#"{"cmd":"get-stream-enabled","stream_id":1}"#,
            br#"{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":1,"supported":true,"editable":true,"required":false,"active_enabled":false,"configured_enabled":false,"pending_restart":false,"motion_blocks_disable":false,"recorder_blocks_disable":false}"#,
        );
        let recording = Arc::new(AtomicBool::new(false));
        let maintenance = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let recorder0 = recorder(
            &root,
            0,
            Arc::clone(&recording),
            Arc::clone(&maintenance),
            Arc::clone(&stop),
        );
        let paused = Arc::clone(&maintenance);
        let worker_thread = thread::spawn(move || {
            let (mut socket, _) = worker.accept().unwrap();
            let mut header = [0_u8; 4];
            socket.read_exact(&mut header).unwrap();
            let mut request = vec![0_u8; u32::from_be_bytes(header) as usize];
            socket.read_exact(&mut request).unwrap();
            assert!(paused.load(Ordering::Acquire));
            let response = b"\x01\x00card-not-mounted";
            socket
                .write_all(&(response.len() as u32).to_be_bytes())
                .and_then(|()| socket.write_all(response))
                .unwrap();
        });

        let result = backend.run_storage_format(
            "0123456789abcdef",
            Instant::now() + Duration::from_secs(3),
            Duration::from_secs(1),
        );
        assert!(matches!(
            result,
            Err(FormatRunError::Backend(BackendError::Unavailable))
        ));
        assert!(!recording.load(Ordering::Acquire));
        assert!(!maintenance.load(Ordering::Acquire));
        assert!(!backend.timelapse.storage_paused());

        rvd.join().unwrap();
        worker_thread.join().unwrap();
        stop.store(true, Ordering::Release);
        recorder0.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_terminal_worker_reply_latches_maintenance_and_does_not_resume() {
        let root = task_temp("format-uncertain-worker");
        let (backend, worker) = storage_backend(&root);
        let rmr0 = Arc::new(AtomicBool::new(true));
        let rmr1 = Arc::new(AtomicBool::new(false));
        let maintenance0 = Arc::new(AtomicBool::new(false));
        let maintenance1 = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let channel0 = recorder(
            &root,
            0,
            Arc::clone(&rmr0),
            Arc::clone(&maintenance0),
            Arc::clone(&stop),
        );
        let channel1 = recorder(
            &root,
            1,
            Arc::clone(&rmr1),
            Arc::clone(&maintenance1),
            Arc::clone(&stop),
        );
        let worker_thread = thread::spawn(move || {
            let (mut socket, _) = worker.accept().unwrap();
            let mut header = [0_u8; 4];
            socket.read_exact(&mut header).unwrap();
            let mut request = vec![0_u8; u32::from_be_bytes(header) as usize];
            socket.read_exact(&mut request).unwrap();
            thread::sleep(Duration::from_millis(500));
            let response = b"\x01\x01formatted-fat32";
            let _ = socket
                .write_all(&(response.len() as u32).to_be_bytes())
                .and_then(|()| socket.write_all(response));
        });

        backend
            .file_reads
            .activate_format(
                br#"{"ok":true,"data":{"format":{"supported":false,"options":[],"status":"idle","last_output_b64":""}}}"#
                    .to_vec(),
            )
            .unwrap();
        let result = backend.run_storage_format(
            "0123456789abcdef",
            Instant::now() + Duration::from_millis(500),
            Duration::from_millis(250),
        );
        assert!(matches!(result, Err(FormatRunError::Uncertain)));
        assert!(!rmr0.load(Ordering::Acquire));
        assert!(!rmr1.load(Ordering::Acquire));
        assert!(maintenance0.load(Ordering::Acquire));
        assert!(maintenance1.load(Ordering::Acquire));
        assert!(backend.timelapse.storage_paused());
        assert!(backend.file_reads.format_active.load(Ordering::Acquire));
        assert!(matches!(backend.lock_mutation(), Err(BackendError::Busy)));
        backend.file_reads.format_state.lock().unwrap().phase = "failed";
        let sd = backend
            .recording_sd(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let sd = crate::json::parse(&sd.body).unwrap();
        assert_eq!(
            sd.get_path("data.format.supported")
                .and_then(Value::as_bool),
            Some(false)
        );
        assert!(
            sd.get_path("data.format.options")
                .and_then(Value::as_array)
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            backend.recording_files(
                "GET",
                "/api/v1/files?cd=/mnt/mmcblk0p1/raptor/stream0",
                b"",
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Busy)
        );

        worker_thread.join().unwrap();
        stop.store(true, Ordering::Release);
        channel0.join().unwrap();
        channel1.join().unwrap();
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn format_requires_explicit_confirmation_and_a_started_worker() {
        let root = task_temp("format-confirmation");
        let (backend, _worker) = storage_backend(&root);
        for body in [
            br#"{"action":"format","filesystem":"fat32"}"#.as_slice(),
            br#"{"action":"format","filesystem":"fat32","confirm":"yes"}"#,
            br#"{"action":"format","filesystem":"ext4","confirm":"erase"}"#,
        ] {
            assert_eq!(
                backend.queue_storage_format(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            );
        }
        assert_eq!(
            backend.queue_storage_format(
                br#"{"action":"format","filesystem":"fat32","confirm":"erase"}"#,
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::Unavailable)
        );
        drop(backend);
        fs::remove_dir_all(root).unwrap();
    }
}
