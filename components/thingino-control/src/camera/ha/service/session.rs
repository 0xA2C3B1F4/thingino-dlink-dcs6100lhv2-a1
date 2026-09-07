//! Drive configuration generations, MQTT sessions, and reconnect backoff.
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use super::super::{commands, config::HaConfig, motion::MotionSlot, mqtt};
use super::publication::{
    publish, publish_discovery, publish_live_image, publish_motion_resync, publish_motion_updates,
    publish_states,
};
use super::{Request, RuntimeState, clear_command_error, unix_now, update_runtime};
use crate::camera::PrudyntBackend;

const KEEPALIVE_SECONDS: u16 = 30;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);

pub(super) fn worker_loop(
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

pub(super) enum SessionEnd {
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
pub(super) struct SessionRequests {
    pub(super) discovery: bool,
    pub(super) state: bool,
    pub(super) motion: bool,
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

pub(super) fn session_control(
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

pub(super) fn wait_backoff(
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

pub(super) fn reconnect_delay(device_id: &str, attempt: u32) -> Duration {
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
