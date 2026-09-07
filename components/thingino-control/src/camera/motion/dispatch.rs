//! Apply motion transitions, publish bounded events and dispatch configured actions.

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use super::super::motion_datagram::{MotionObservation, number_u64};
use super::super::motion_events::{
    MOTION_EVENT_VERSION, MediaArtifact, MotionEvent, MotionEventDisposition, MotionState,
};
use super::super::motion_state::{MotionTransition, motion_config_from_document};
use super::super::{read_bounded, write_fifo};
use super::{FILE_LIMIT, MotionService, unix_milliseconds};
use crate::json::{self, Value};

impl MotionService {
    pub(super) fn process(&self, observation: MotionObservation) {
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

    pub(super) fn publish_current_state(&self, requested: MotionState) {
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

    pub(super) fn publish_with_artifacts(
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

    pub(super) fn apply_state_files(&self, active: bool) {
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

    pub(super) fn dispatch_speaker(&self) {
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

    pub(in crate::camera) fn refresh_config(&self) {
        if let Ok(bytes) = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)
            && let Ok(document) = json::parse(&bytes)
        {
            self.machine
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .set_config(motion_config_from_document(&document));
        }
    }

    pub(in crate::camera) fn reconcile_prudynt(&self, running: bool) {
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
