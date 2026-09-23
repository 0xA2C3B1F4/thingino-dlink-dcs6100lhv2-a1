//! Publish retained discovery/state and non-retained camera images.
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use super::super::backend::HaBackend;
use super::super::{
    config::HaConfig,
    discovery,
    motion::{self, MotionSlot},
    mqtt, state,
};
use super::{RuntimeState, update_runtime};
use crate::BackendError;
use crate::camera::motion_events::MotionState;

pub(super) fn publish_live_image(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &HaBackend,
    stream: u8,
    current: &dyn Fn() -> bool,
    last_published: &mut BTreeMap<String, Vec<u8>>,
) -> Result<(), ()> {
    if backend.is_raptor() && client.pending_write() {
        return Ok(());
    }
    let result = backend.with_snapshot(stream, current, |snapshot| {
        if !current() {
            return Err(BackendError::Unavailable);
        }
        let topic = format!("{}/live_view/image", config.base_topic());
        if backend.is_raptor() {
            #[cfg(test)]
            if backend
                .service()
                .test_hooks
                .fail_next_image_write
                .swap(false, std::sync::atomic::Ordering::AcqRel)
            {
                client.fail_next_image_write = true;
            }
            client
                .publish_image(&topic, snapshot)
                .map_err(|_| BackendError::Connection)?;
            update_runtime(runtime, |state| {
                state.published_messages = state.published_messages.saturating_add(1)
            });
            publish(
                client,
                runtime,
                &format!("{}/live_view/availability", config.base_topic()),
                b"online",
                true,
            )
            .map_err(|_| BackendError::Connection)?;
        } else {
            publish(client, runtime, &topic, snapshot, false)
                .map_err(|_| BackendError::Connection)?;
        }
        Ok(())
    });
    if matches!(result, Err(BackendError::Connection)) {
        return Err(());
    }
    if !current() {
        return Ok(());
    }
    if backend.is_raptor() {
        if result.is_err() {
            publish(
                client,
                runtime,
                &format!("{}/live_view/availability", config.base_topic()),
                b"offline",
                true,
            )
            .map_err(|_| ())?;
        }
        last_published.insert(
            "live_view".to_owned(),
            if result.is_ok() {
                b"ready".to_vec()
            } else {
                Vec::new()
            },
        );
    }
    update_runtime(runtime, |state| {
        if result.is_err() {
            state.last_error = Some("camera preview unavailable");
        } else if state.last_error == Some("camera preview unavailable") {
            state.last_error = None;
        }
    });
    Ok(())
}
pub(super) fn publish_motion_resync(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &HaBackend,
    last_published: &mut BTreeMap<String, Vec<u8>>,
    current: &dyn Fn() -> bool,
) -> Result<(), &'static str> {
    if backend.is_raptor() {
        let active = backend.motion_active();
        if !current() {
            return Err("HA configuration changed");
        }
        return publish_raptor_motion(client, runtime, config, active, last_published, true);
    }
    publish_motion_state(
        client,
        runtime,
        config,
        MotionState::Resync,
        backend.motion_active().unwrap_or(false),
        last_published,
    )
}

pub(super) fn publish_motion_updates(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    backend: &HaBackend,
    motion_slot: &MotionSlot,
    last_published: &mut BTreeMap<String, Vec<u8>>,
    current: &dyn Fn() -> bool,
) -> Result<(), &'static str> {
    if backend.is_raptor() {
        motion_slot.reset();
        let active = backend.motion_active();
        if !current() {
            return Err("HA configuration changed");
        }
        return publish_raptor_motion(client, runtime, config, active, last_published, false);
    }
    while let Some(update) = motion_slot.take_next() {
        publish_motion_state(
            client,
            runtime,
            config,
            update.state,
            backend.motion_active().unwrap_or(false),
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
    backend: &HaBackend,
    current: &dyn Fn() -> bool,
) -> Result<(), &'static str> {
    let release = state::os_release(backend.paths());
    let publications = if backend.is_raptor() {
        discovery::raptor_publications(config, &release)
    } else {
        discovery::discovery_publications(config, &release)
    };
    for publication in publications {
        if !current() {
            return Err("HA configuration changed");
        }
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
    backend: &HaBackend,
    last_published: &mut BTreeMap<String, Vec<u8>>,
    force: bool,
    current: &dyn Fn() -> bool,
) -> Result<(), BackendError> {
    let observation = backend.collect(config);
    if !current() {
        return Err(BackendError::Unavailable);
    }
    let mut states = match observation {
        Ok(states) => states,
        Err(error) if backend.is_raptor() => {
            for entity in last_published.keys() {
                publish(
                    client,
                    runtime,
                    &format!("{}/{entity}/availability", config.base_topic()),
                    b"offline",
                    true,
                )
                .map_err(|_| BackendError::Connection)?;
                publish(
                    client,
                    runtime,
                    &format!("{}/{entity}/state", config.base_topic()),
                    b"",
                    true,
                )
                .map_err(|_| BackendError::Connection)?;
            }
            last_published.clear();
            publish(
                client,
                runtime,
                &config.availability_topic(),
                b"offline",
                true,
            )
            .map_err(|_| BackendError::Connection)?;
            return Err(error);
        }
        Err(error) => return Err(error),
    };
    // A reconnect cannot reuse availability from an earlier image capture.
    if backend.is_raptor() && force && config.entities.live_view {
        states.insert("live_view".to_owned(), Vec::new());
    }
    for (entity, payload) in states {
        if backend.is_raptor() {
            publish_raptor_entity(
                client,
                runtime,
                config,
                &entity,
                &payload,
                last_published,
                force,
            )
            .map_err(|_| BackendError::Connection)?;
        } else if force || last_published.get(&entity) != Some(&payload) {
            let topic = format!("{}/{entity}/state", config.base_topic());
            publish(client, runtime, &topic, &payload, true)
                .map_err(|_| BackendError::Connection)?;
            last_published.insert(entity, payload);
        }
    }
    if backend.is_raptor() {
        publish(
            client,
            runtime,
            &config.availability_topic(),
            b"online",
            true,
        )
        .map_err(|_| BackendError::Connection)?;
    }
    Ok(())
}

fn publish_raptor_motion(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    active: Option<bool>,
    last: &mut BTreeMap<String, Vec<u8>>,
    force: bool,
) -> Result<(), &'static str> {
    let payload: &[u8] = match active {
        Some(true) => b"ON",
        Some(false) => b"OFF",
        None => b"",
    };
    publish_raptor_entity(client, runtime, config, "motion", payload, last, force)
}

fn publish_raptor_entity(
    client: &mut mqtt::Client,
    runtime: &Arc<Mutex<RuntimeState>>,
    config: &HaConfig,
    entity: &str,
    payload: &[u8],
    last: &mut BTreeMap<String, Vec<u8>>,
    force: bool,
) -> Result<(), &'static str> {
    if !force && last.get(entity).is_some_and(|old| old == payload) {
        return Ok(());
    }
    publish_entity_payload(payload, |suffix, data| {
        publish(
            client,
            runtime,
            &format!("{}/{entity}/{suffix}", config.base_topic()),
            data,
            true,
        )
    })?;
    last.insert(entity.to_owned(), payload.to_vec());
    Ok(())
}

// Availability precedes clearing a missing state, and follows a known state.
fn publish_entity_payload(
    payload: &[u8],
    mut send: impl FnMut(&str, &[u8]) -> Result<(), &'static str>,
) -> Result<(), &'static str> {
    if payload.is_empty() {
        send("availability", b"offline")?;
    }
    send("state", payload)?;
    if !payload.is_empty() {
        send("availability", b"online")?;
    }
    Ok(())
}

#[cfg(test)]
#[test]
fn unknown_entity_goes_offline_before_retained_state_is_cleared() {
    for payload in [b"ON".as_slice(), b"OFF", b""] {
        let mut sent = Vec::new();
        publish_entity_payload(payload, |suffix, data| {
            sent.push((suffix.to_owned(), data.to_vec()));
            Ok(())
        })
        .unwrap();
        let expected = if payload.is_empty() {
            vec![
                ("availability".to_owned(), b"offline".to_vec()),
                ("state".to_owned(), Vec::new()),
            ]
        } else {
            vec![
                ("state".to_owned(), payload.to_vec()),
                ("availability".to_owned(), b"online".to_vec()),
            ]
        };
        assert_eq!(sent, expected);
    }
    let mut calls = 0;
    assert!(
        publish_entity_payload(b"", |_, _| {
            calls += 1;
            Err("unavailable")
        })
        .is_err()
    );
    assert_eq!(calls, 1);
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
