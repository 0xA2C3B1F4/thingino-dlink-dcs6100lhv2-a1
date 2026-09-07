use crate::json::Value;

use super::motion_datagram::{MotionObservation, ObservationState, number_u64};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct MotionConfig {
    pub debounce_samples: u64,
    pub cooldown_ms: u64,
    pub init_ms: u64,
    pub min_active_ms: u64,
    pub post_ms: u64,
}

impl Default for MotionConfig {
    fn default() -> Self {
        Self {
            debounce_samples: 0,
            cooldown_ms: 5_000,
            init_ms: 5_000,
            min_active_ms: 1_000,
            post_ms: 0,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum MotionTransition {
    Active,
    Inactive,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) struct MotionMachineCounters {
    pub accepted: u64,
    pub duplicate_or_replayed: u64,
    pub producer_restarts: u64,
}

#[derive(Debug)]
pub(super) struct MotionStateMachine {
    config: MotionConfig,
    active: bool,
    monitoring: bool,
    session: Option<(u32, u64)>,
    last_sequence: u64,
    debounce_samples: u64,
    last_qualifying_motion_ms: Option<u64>,
    cooldown_until_ms: u64,
    motor_suppressed: bool,
    awaiting_new_session: bool,
    counters: MotionMachineCounters,
}

impl MotionStateMachine {
    pub(super) fn new(config: MotionConfig, recovered_active: bool) -> Self {
        Self {
            config,
            active: recovered_active,
            monitoring: false,
            session: None,
            last_sequence: 0,
            debounce_samples: 0,
            last_qualifying_motion_ms: None,
            cooldown_until_ms: 0,
            motor_suppressed: false,
            awaiting_new_session: false,
            counters: MotionMachineCounters::default(),
        }
    }

    pub(super) fn apply(&mut self, observation: MotionObservation) -> Option<MotionTransition> {
        let session = observation.session();
        if self.awaiting_new_session {
            if self.session == Some(session) {
                self.counters.duplicate_or_replayed =
                    self.counters.duplicate_or_replayed.saturating_add(1);
                return None;
            }
            self.awaiting_new_session = false;
        }
        if self.session != Some(session) {
            if self.session.is_some() {
                self.counters.producer_restarts = self.counters.producer_restarts.saturating_add(1);
            }
            self.session = Some(session);
            self.last_sequence = 0;
            self.debounce_samples = 0;
            self.monitoring = true;
        }
        if observation.sequence <= self.last_sequence {
            self.counters.duplicate_or_replayed =
                self.counters.duplicate_or_replayed.saturating_add(1);
            return None;
        }
        self.last_sequence = observation.sequence;
        self.counters.accepted = self.counters.accepted.saturating_add(1);

        if self.motor_suppressed
            && matches!(
                observation.state,
                ObservationState::Detected | ObservationState::Clear
            )
        {
            self.motor_suppressed = false;
            self.cooldown_until_ms = observation
                .monotonic_ms
                .saturating_add(self.config.cooldown_ms);
        }

        match observation.state {
            ObservationState::Monitoring => {
                self.monitoring = true;
                None
            }
            ObservationState::Stopped => self.apply_stopped(observation.monotonic_ms),
            ObservationState::Suppressed => {
                self.motor_suppressed = true;
                self.apply_clear(observation)
            }
            ObservationState::Detected => self.apply_detected(observation),
            ObservationState::Clear => self.apply_clear(observation),
        }
    }

    pub(super) fn retire_producer(&mut self, monotonic_ms: u64) -> Option<MotionTransition> {
        self.awaiting_new_session = self.session.is_some();
        self.apply_stopped(monotonic_ms)
    }

    fn apply_stopped(&mut self, monotonic_ms: u64) -> Option<MotionTransition> {
        self.monitoring = false;
        self.motor_suppressed = false;
        self.debounce_samples = 0;
        self.last_qualifying_motion_ms = None;
        if self.active {
            self.active = false;
            self.cooldown_until_ms = monotonic_ms.saturating_add(self.config.cooldown_ms);
            Some(MotionTransition::Inactive)
        } else {
            None
        }
    }

    fn apply_detected(&mut self, observation: MotionObservation) -> Option<MotionTransition> {
        self.monitoring = true;
        if observation.initial_grace
            && observation.monotonic_ms
                < observation
                    .monitoring_since_ms
                    .saturating_add(self.config.init_ms)
            || observation.monotonic_ms < self.cooldown_until_ms
        {
            return None;
        }
        let regions = u64::from(observation.roi_mask.count_ones()).max(1);
        self.debounce_samples = self.debounce_samples.saturating_add(regions);
        if self.debounce_samples < self.config.debounce_samples {
            return None;
        }
        self.last_qualifying_motion_ms = Some(observation.monotonic_ms);
        if self.active {
            None
        } else {
            self.active = true;
            Some(MotionTransition::Active)
        }
    }

    fn apply_clear(&mut self, observation: MotionObservation) -> Option<MotionTransition> {
        self.monitoring = true;
        self.debounce_samples = 0;
        if self.active && self.last_qualifying_motion_ms.is_none() {
            // Control may have restarted while Prudynt and an active event kept
            // running. Start a fresh bounded hold window from the first clear
            // observation rather than leaving recovered state active forever.
            self.last_qualifying_motion_ms = Some(observation.monotonic_ms);
            return None;
        }
        let hold_ms = self.config.min_active_ms.max(self.config.post_ms);
        let may_stop = self.active
            && self
                .last_qualifying_motion_ms
                .is_some_and(|last| observation.monotonic_ms.saturating_sub(last) >= hold_ms);
        if !may_stop {
            return None;
        }
        self.active = false;
        self.last_qualifying_motion_ms = None;
        self.cooldown_until_ms = observation
            .monotonic_ms
            .saturating_add(self.config.cooldown_ms);
        Some(MotionTransition::Inactive)
    }

    pub(super) fn active(&self) -> bool {
        self.active
    }

    pub(super) fn monitoring(&self) -> bool {
        self.monitoring
    }

    pub(super) fn counters(&self) -> MotionMachineCounters {
        self.counters
    }

    pub(super) fn set_config(&mut self, config: MotionConfig) {
        self.config = config;
    }
}

pub(super) fn motion_config_from_document(document: &Value) -> MotionConfig {
    let seconds = |path: &str, fallback: u64| {
        document
            .get_path(path)
            .and_then(number_u64)
            .unwrap_or(fallback)
            .saturating_mul(1_000)
    };
    MotionConfig {
        debounce_samples: document
            .get_path("motion.debounce_time")
            .and_then(number_u64)
            .unwrap_or(0),
        cooldown_ms: seconds("motion.cooldown_time", 5),
        init_ms: seconds("motion.init_time", 5),
        min_active_ms: seconds("motion.min_time", 1),
        post_ms: seconds("motion.post_time", 0),
    }
}
