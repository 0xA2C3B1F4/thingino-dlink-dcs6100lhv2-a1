use super::*;

pub(super) const MOUNT: &str = "/mnt/mmcblk0p1";
pub(super) const DIRECTORY: &str = "raptor/timelapse";
pub(super) const FILENAME: &str = "unix-seconds-sequence.jpg";

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct Policy {
    pub enabled: bool,
    pub interval: u64,
    pub keep_days: u64,
    pub preset_enabled: bool,
    pub presets: [bool; 3],
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            enabled: false,
            interval: 1,
            keep_days: 7,
            preset_enabled: false,
            presets: [false; 3],
        }
    }
}

impl Policy {
    pub fn value(&self) -> Value {
        object([
            ("enabled", Value::Bool(self.enabled)),
            ("mount", string(MOUNT)),
            ("filepath", string(DIRECTORY)),
            ("filename", string(FILENAME)),
            ("interval", number(self.interval)),
            ("keep_days", number(self.keep_days)),
            ("preset_enabled", Value::Bool(self.preset_enabled)),
            (
                "presets",
                object([
                    ("ircut", Value::Bool(self.presets[0])),
                    ("ir850", Value::Bool(self.presets[1])),
                    ("color", Value::Bool(self.presets[2])),
                ]),
            ),
        ])
    }

    pub fn parse(value: &Value) -> Result<Self, BackendError> {
        let fields = value
            .as_object()
            .filter(|fields| fields.len() == 8)
            .ok_or(BackendError::Protocol)?;
        for (key, fixed) in [
            ("mount", MOUNT),
            ("filepath", DIRECTORY),
            ("filename", FILENAME),
        ] {
            if fields.get(key).and_then(Value::as_str) != Some(fixed) {
                return Err(BackendError::Protocol);
            }
        }
        let flag = |key| {
            fields
                .get(key)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Protocol)
        };
        let integer = |key| {
            fields
                .get(key)
                .ok_or(BackendError::Protocol)
                .and_then(value_u64)
        };
        let interval = integer("interval")?;
        let keep_days = integer("keep_days")?;
        if !(1..=1440).contains(&interval) || keep_days > 365 {
            return Err(BackendError::Protocol);
        }
        let presets = fields
            .get("presets")
            .and_then(Value::as_object)
            .filter(|v| v.len() == 3)
            .ok_or(BackendError::Protocol)?;
        let mut preset = [false; 3];
        for (i, key) in ["ircut", "ir850", "color"].into_iter().enumerate() {
            preset[i] = presets
                .get(key)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Protocol)?;
        }
        Ok(Self {
            enabled: flag("enabled")?,
            interval,
            keep_days,
            preset_enabled: flag("preset_enabled")?,
            presets: preset,
        })
    }

    pub fn update(&self, body: &[u8]) -> Result<Self, BackendError> {
        let value = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = value
            .as_object()
            .filter(|fields| fields.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let patch = fields
            .get("timelapse")
            .and_then(Value::as_object)
            .filter(|v| !v.is_empty())
            .ok_or(BackendError::Protocol)?;
        let mut next = self.value();
        next.merge(&Value::Object(patch.clone()))
            .map_err(|_| BackendError::Protocol)?;
        Self::parse(&next)
    }
}
