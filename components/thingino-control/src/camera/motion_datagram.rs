use crate::json::{self, Value};

pub(super) const PROTOCOL_VERSION: u8 = 1;
pub(super) const MAX_INGRESS_BYTES: usize = 512;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum ObservationState {
    Monitoring,
    Detected,
    Clear,
    Suppressed,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct MotionObservation {
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

pub(super) fn parse_observation(input: &[u8]) -> Result<MotionObservation, ()> {
    if input.is_empty() || input.len() > MAX_INGRESS_BYTES {
        return Err(());
    }
    let value = json::parse(input)?;
    let fields = value.as_object().ok_or(())?;
    let allowed = [
        "version",
        "event",
        "state",
        "channel",
        "monotonic_ms",
        "monitoring_since_ms",
        "sequence",
        "producer_pid",
        "roi_mask",
        "initial_grace",
    ];
    if fields.len() != allowed.len() || fields.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err(());
    }
    let version = number_u64(fields.get("version").ok_or(())?).ok_or(())?;
    if version != u64::from(PROTOCOL_VERSION)
        || fields.get("event").and_then(Value::as_str) != Some("motion")
    {
        return Err(());
    }
    let state = match fields.get("state").and_then(Value::as_str) {
        Some("monitoring") => ObservationState::Monitoring,
        Some("detected") => ObservationState::Detected,
        Some("clear") => ObservationState::Clear,
        Some("suppressed") => ObservationState::Suppressed,
        Some("stopped") => ObservationState::Stopped,
        _ => return Err(()),
    };
    let channel = number_u64(fields.get("channel").ok_or(())?).ok_or(())?;
    let producer_pid = number_u64(fields.get("producer_pid").ok_or(())?).ok_or(())?;
    let roi_mask = number_u64(fields.get("roi_mask").ok_or(())?).ok_or(())?;
    if matches!(state, ObservationState::Detected) != (roi_mask != 0) {
        return Err(());
    }
    Ok(MotionObservation {
        version: PROTOCOL_VERSION,
        state,
        channel: u8::try_from(channel)
            .ok()
            .filter(|value| *value <= 1)
            .ok_or(())?,
        monotonic_ms: number_u64(fields.get("monotonic_ms").ok_or(())?).ok_or(())?,
        monitoring_since_ms: number_u64(fields.get("monitoring_since_ms").ok_or(())?).ok_or(())?,
        sequence: number_u64(fields.get("sequence").ok_or(())?)
            .filter(|value| *value > 0)
            .ok_or(())?,
        producer_pid: u32::try_from(producer_pid)
            .ok()
            .filter(|value| *value > 0)
            .ok_or(())?,
        roi_mask,
        initial_grace: fields
            .get("initial_grace")
            .and_then(Value::as_bool)
            .ok_or(())?,
    })
}

pub(super) fn number_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(number) => number.parse().ok(),
        _ => None,
    }
}
