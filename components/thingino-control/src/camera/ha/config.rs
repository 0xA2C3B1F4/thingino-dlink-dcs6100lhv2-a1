use super::super::*;

pub(in crate::camera) const MIN_STATE_INTERVAL: u64 = 5;
pub(in crate::camera) const MIN_DISCOVERY_INTERVAL: u64 = 60;
pub(in crate::camera) const MIN_CAMERA_INTERVAL: u64 = 5;

pub(super) struct HaConfig {
    pub(super) enabled: bool,
    pub(super) host: String,
    pub(super) port: u16,
    pub(super) username: String,
    pub(super) password: String,
    pub(super) client_id: String,
    pub(super) use_tls: bool,
    pub(super) tls_skip_verify: bool,
    pub(super) device_id: String,
    pub(super) device_name: String,
    pub(super) device_model: String,
    pub(super) discovery_prefix: String,
    pub(super) state_interval: Duration,
    pub(super) discovery_interval: Duration,
    pub(super) camera_interval: Duration,
    pub(super) entities: EntityFlags,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) struct EntityFlags {
    pub(super) motion: bool,
    pub(super) motion_guard: bool,
    pub(super) ircut: bool,
    pub(super) daynight: bool,
    pub(super) privacy: bool,
    pub(super) color: bool,
    pub(super) ir850: bool,
    pub(super) gain: bool,
    pub(super) rssi: bool,
    pub(super) snapshot: bool,
    pub(super) live_view: bool,
    pub(super) firmware_version: bool,
    pub(super) firmware_timestamp: bool,
    pub(super) reboot: bool,
}

impl HaConfig {
    pub(super) fn load(paths: &CameraPaths) -> Result<Self, BackendError> {
        let raw = read_bounded(&paths.thingino_config, FILE_LIMIT)?;
        let document = json::parse(&raw).map_err(|_| BackendError::Protocol)?;
        let ha = document
            .get_path("ha")
            .and_then(Value::as_object)
            .ok_or(BackendError::Protocol)?;
        let mqtt = ha
            .get("mqtt")
            .and_then(Value::as_object)
            .ok_or(BackendError::Protocol)?;
        let device_id = device_id(paths);
        let prefix = text(mqtt, "client_id_prefix", "thingino-ha");
        let client_id = bounded_client_id(&prefix, &device_id);
        Ok(Self {
            enabled: boolean(ha, "enabled", false),
            host: text(mqtt, "host", ""),
            port: integer(mqtt, "port", 1883).clamp(1, 65_535) as u16,
            username: text(mqtt, "username", ""),
            password: text(mqtt, "password", ""),
            client_id,
            use_tls: boolean(mqtt, "use_ssl", false),
            tls_skip_verify: boolean(mqtt, "tls_skip_verify", false),
            device_id: device_id.clone(),
            device_name: text(ha, "device_name", &device_id),
            device_model: text(ha, "device_model", "D-Link DCS-6100LHV2 A1"),
            discovery_prefix: text(ha, "discovery_prefix", "homeassistant"),
            state_interval: Duration::from_secs(
                integer(ha, "state_interval", 15).clamp(MIN_STATE_INTERVAL, 604_800),
            ),
            discovery_interval: Duration::from_secs(
                integer(ha, "discovery_interval", 3_600).clamp(MIN_DISCOVERY_INTERVAL, 604_800),
            ),
            camera_interval: Duration::from_secs(
                integer(ha, "camera_interval", MIN_CAMERA_INTERVAL)
                    .clamp(MIN_CAMERA_INTERVAL, 604_800),
            ),
            entities: EntityFlags {
                motion: boolean(ha, "enable_motion", true),
                motion_guard: boolean(ha, "enable_motion_guard", true),
                ircut: boolean(ha, "enable_ircut", true),
                daynight: boolean(ha, "enable_daynight", true),
                privacy: boolean(ha, "enable_privacy", true),
                color: boolean(ha, "enable_color", true),
                ir850: boolean(ha, "enable_ir850", true),
                gain: boolean(ha, "enable_gain", true),
                rssi: boolean(ha, "enable_rssi", true),
                snapshot: boolean(ha, "enable_snapshot", true),
                live_view: boolean(ha, "enable_live_view", true),
                firmware_version: boolean(ha, "enable_firmware_version", true),
                firmware_timestamp: boolean(ha, "enable_firmware_timestamp", true),
                reboot: boolean(ha, "enable_reboot", false),
            },
        })
    }

    pub(super) fn availability_topic(&self) -> String {
        format!("cameras/{}/status", self.device_id)
    }

    pub(super) fn base_topic(&self) -> String {
        format!("cameras/{}", self.device_id)
    }
}

fn text(fields: &BTreeMap<String, Value>, name: &str, fallback: &str) -> String {
    fields
        .get(name)
        .and_then(Value::as_str)
        .unwrap_or(fallback)
        .to_owned()
}

fn boolean(fields: &BTreeMap<String, Value>, name: &str, fallback: bool) -> bool {
    fields
        .get(name)
        .and_then(Value::as_bool)
        .unwrap_or(fallback)
}

fn integer(fields: &BTreeMap<String, Value>, name: &str, fallback: u64) -> u64 {
    fields.get(name).and_then(value_u64).unwrap_or(fallback)
}

fn device_id(paths: &CameraPaths) -> String {
    let raw = read_text_value(&paths.hostname, 128).unwrap_or_else(|| "thingino".to_owned());
    let mut result = String::with_capacity(raw.len().min(64));
    for character in raw.chars() {
        if result.len() >= 64 {
            break;
        }
        if character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.') {
            result.push(character.to_ascii_lowercase());
        } else if !result.ends_with('-') {
            result.push('-');
        }
    }
    let result = result.trim_matches(['-', '.']).to_owned();
    if result.is_empty() {
        "thingino".to_owned()
    } else {
        result
    }
}

fn bounded_client_id(prefix: &str, device_id: &str) -> String {
    let mut id = format!("{prefix}-{device_id}");
    id.retain(|character| {
        character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')
    });
    id.truncate(128);
    if id.trim_matches(['-', '_', '.']).is_empty() {
        "thingino-ha".to_owned()
    } else {
        id
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn client_identity_is_stable_and_bounded() {
        assert_eq!(
            bounded_client_id("thingino-ha", "front-camera"),
            "thingino-ha-front-camera"
        );
        assert_eq!(bounded_client_id("!", "?"), "thingino-ha");
        assert_eq!(bounded_client_id(&"p".repeat(200), "camera").len(), 128);
    }
}
