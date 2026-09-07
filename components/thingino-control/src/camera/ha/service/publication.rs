//! Publish retained discovery/state and non-retained camera images.
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use super::super::{
    config::HaConfig,
    discovery,
    motion::{self, MotionSlot},
    mqtt, snapshot, state,
};
use super::{RuntimeState, update_runtime};
use crate::BackendError;
use crate::camera::{PrudyntBackend, motion_events::MotionState};

pub(super) fn publish_live_image(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &PrudyntBackend,
) -> Result<(), ()> {
    let snapshot = match snapshot::capture(backend, 1) {
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
pub(super) fn publish_motion_resync(
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
        MotionState::Resync,
        backend.motion.snapshot().active,
        last_published,
    )
}

pub(super) fn publish_motion_updates(
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
    state: MotionState,
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

pub(super) fn publish_discovery(
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

pub(super) fn publish_states(
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

pub(super) fn publish(
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
