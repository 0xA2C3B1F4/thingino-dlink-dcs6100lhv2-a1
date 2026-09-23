use std::sync::{Mutex, TryLockError};

use super::super::motion_events::{
    MotionEvent, MotionEventDisposition, MotionEventSink, MotionState,
};
use super::service::HaService;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct MotionUpdate {
    pub(super) state: MotionState,
}

impl MotionUpdate {
    fn from_event(event: MotionEvent) -> Option<Self> {
        if !event.has_bounded_metadata() {
            return None;
        }
        Some(Self { state: event.state })
    }
}

#[derive(Debug, Default)]
struct SlotState {
    pending: Option<MotionUpdate>,
    wakeup_queued: bool,
}

#[derive(Debug, Default)]
pub(super) struct MotionSlot {
    state: Mutex<SlotState>,
}

impl MotionSlot {
    pub(super) fn submit(
        &self,
        update: MotionUpdate,
        queue_wakeup: impl FnOnce() -> bool,
    ) -> MotionEventDisposition {
        let mut state = match self.state.try_lock() {
            Ok(state) => state,
            Err(TryLockError::WouldBlock | TryLockError::Poisoned(_)) => {
                return MotionEventDisposition::Dropped;
            }
        };
        if state.wakeup_queued {
            state.pending = Some(update);
            return MotionEventDisposition::Coalesced;
        }
        state.pending = Some(update);
        state.wakeup_queued = true;
        if queue_wakeup() {
            MotionEventDisposition::Queued
        } else {
            state.pending = None;
            state.wakeup_queued = false;
            MotionEventDisposition::Dropped
        }
    }

    pub(super) fn take_next(&self) -> Option<MotionUpdate> {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(update) = state.pending.take() {
            return Some(update);
        }
        state.wakeup_queued = false;
        None
    }

    pub(super) fn reset(&self) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        *state = SlotState::default();
    }
}

pub(super) fn retained_payload(state: MotionState, current_active: bool) -> &'static [u8] {
    match state {
        MotionState::Active => b"ON",
        MotionState::Inactive => b"OFF",
        MotionState::Resync if current_active => b"ON",
        MotionState::Resync => b"OFF",
    }
}

impl MotionEventSink for HaService {
    fn try_send_motion(&self, event: MotionEvent) -> MotionEventDisposition {
        let acceptance = self.accepts_motion();
        if acceptance != MotionEventDisposition::Queued {
            return acceptance;
        }
        let Some(update) = MotionUpdate::from_event(event) else {
            return MotionEventDisposition::Dropped;
        };
        self.enqueue_motion(update)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn update(state: MotionState) -> MotionUpdate {
        MotionUpdate { state }
    }

    #[test]
    fn latest_motion_level_coalesces_behind_one_wakeup() {
        let slot = MotionSlot::default();
        assert_eq!(
            slot.submit(update(MotionState::Active), || true),
            MotionEventDisposition::Queued
        );
        assert_eq!(
            slot.submit(update(MotionState::Inactive), || panic!("second wakeup")),
            MotionEventDisposition::Coalesced
        );
        assert_eq!(slot.take_next(), Some(update(MotionState::Inactive)));
        assert_eq!(slot.take_next(), None);
    }

    #[test]
    fn failed_wakeup_drops_and_releases_the_slot() {
        let slot = MotionSlot::default();
        assert_eq!(
            slot.submit(update(MotionState::Active), || false),
            MotionEventDisposition::Dropped
        );
        assert_eq!(slot.take_next(), None);
        assert_eq!(
            slot.submit(update(MotionState::Inactive), || true),
            MotionEventDisposition::Queued
        );
    }

    #[test]
    fn reconnect_resync_uses_the_current_motion_level() {
        assert_eq!(retained_payload(MotionState::Resync, true), b"ON");
        assert_eq!(retained_payload(MotionState::Resync, false), b"OFF");
    }
}
