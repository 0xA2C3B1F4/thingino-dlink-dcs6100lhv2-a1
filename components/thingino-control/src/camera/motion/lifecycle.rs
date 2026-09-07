//! Own startup, receive ingress and shutdown, including rollback and joins.

use std::fs;
use std::io;
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::UnixDatagram;
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use super::super::motion_datagram::{MAX_INGRESS_BYTES, parse_observation};
use super::super::motion_events::MotionState;
use super::{MotionLifecycleState, MotionService};

const RECEIVE_SHUTDOWN_POLL: Duration = Duration::from_millis(50);

impl MotionService {
    pub(in crate::camera) fn start(self: &Arc<Self>) -> io::Result<()> {
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
    pub(in crate::camera) fn shutdown(&self) -> io::Result<()> {
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
