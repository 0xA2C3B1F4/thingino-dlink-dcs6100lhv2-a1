pub(crate) const PROTOCOL_VERSION: u8 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ObservationState {
    Monitoring,
    Detected,
    Clear,
    Suppressed,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct MotionObservation {
    pub version: u8,
    pub state: ObservationState,
    pub channel: u8,
    pub monotonic_ms: u64,
    pub monitoring_since_ms: u64,
    pub sequence: u64,
    pub producer_pid: u32,
    pub roi_mask: u64,
    pub initial_grace: bool,
}

impl MotionObservation {
    pub(super) fn session(self) -> (u32, u64) {
        (self.producer_pid, self.monitoring_since_ms)
    }
}
