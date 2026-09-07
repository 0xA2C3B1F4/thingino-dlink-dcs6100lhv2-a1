use std::collections::VecDeque;
use std::sync::{Condvar, Mutex};

use super::motion_datagram::{MotionObservation, ObservationState};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) enum QueuePushResult {
    Queued,
    Coalesced,
    #[default]
    Dropped,
}

#[derive(Debug, Default)]
struct QueueState {
    items: VecDeque<MotionObservation>,
    high_water: usize,
    dropped: u64,
    coalesced: u64,
    shutdown: bool,
}

#[derive(Debug)]
pub(super) struct MotionQueue {
    capacity: usize,
    state: Mutex<QueueState>,
    available: Condvar,
    #[cfg(test)]
    wait_observer: Mutex<Option<std::sync::mpsc::Sender<()>>>,
}

impl MotionQueue {
    pub(super) fn new(capacity: usize) -> Self {
        Self {
            capacity,
            state: Mutex::new(QueueState::default()),
            available: Condvar::new(),
            #[cfg(test)]
            wait_observer: Mutex::new(None),
        }
    }

    pub(super) fn reset_for_start(&self) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        state.items.clear();
        state.shutdown = false;
    }

    pub(super) fn shutdown(&self) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        state.items.clear();
        state.shutdown = true;
        self.available.notify_all();
    }

    pub(super) fn push(&self, observation: MotionObservation) -> QueuePushResult {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        if state.shutdown {
            state.dropped = state.dropped.saturating_add(1);
            return QueuePushResult::Dropped;
        }
        let replaceable = matches!(
            observation.state,
            ObservationState::Monitoring | ObservationState::Clear
        );
        // Only collapse an adjacent level observation. Replacing an older
        // Clear/Monitoring item across a later Detected item would reorder the
        // producer sequence and could make the worker discard that Detected as
        // a replay.
        if replaceable
            && let Some(pending) = state.items.back_mut()
            && pending.session() == observation.session()
            && pending.channel == observation.channel
            && pending.state == observation.state
        {
            *pending = observation;
            state.coalesced = state.coalesced.saturating_add(1);
            return QueuePushResult::Coalesced;
        }
        if state.items.len() >= self.capacity {
            state.dropped = state.dropped.saturating_add(1);
            return QueuePushResult::Dropped;
        }
        state.items.push_back(observation);
        state.high_water = state.high_water.max(state.items.len());
        self.available.notify_one();
        QueuePushResult::Queued
    }

    pub(super) fn pop(&self) -> Option<MotionObservation> {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        loop {
            if state.shutdown {
                return None;
            }
            if let Some(observation) = state.items.pop_front() {
                return Some(observation);
            }
            #[cfg(test)]
            if let Some(observer) = self
                .wait_observer
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .take()
            {
                let _ = observer.send(());
            }
            state = self
                .available
                .wait(state)
                .unwrap_or_else(|error| error.into_inner());
        }
    }

    pub(super) fn snapshot(&self) -> (usize, usize, u64, u64) {
        let state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        (
            state.items.len(),
            state.high_water,
            state.dropped,
            state.coalesced,
        )
    }

    #[cfg(test)]
    pub(super) fn observe_next_wait(&self, observer: std::sync::mpsc::Sender<()>) {
        *self
            .wait_observer
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = Some(observer);
    }
}
