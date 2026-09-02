use super::*;

mod access;
mod crontab;
mod daynight;
#[allow(dead_code)]
mod domains;
mod gpio;
mod imaging;
mod network;
mod recorder;
mod schema;
mod send;
mod time;

pub(super) use schema::{
    validate_prudynt_domain, validate_prudynt_effective_streams, validate_prudynt_update,
};

impl PrudyntBackend {
    pub(super) fn config(&self) -> Result<BackendResponse, BackendError> {
        let prudynt_bytes = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)?;
        let prudynt = json::parse(&prudynt_bytes).map_err(|_| BackendError::Protocol)?;
        let thingino_bytes = read_bounded(&self.paths.thingino_config, FILE_LIMIT)?;
        let thingino = json::parse(&thingino_bytes).map_err(|_| BackendError::Protocol)?;
        let image = [
            ("brightness", "image.brightness", "null"),
            ("contrast", "image.contrast", "null"),
            ("saturation", "image.saturation", "null"),
            ("sharpness", "image.sharpness", "null"),
            ("anti_flicker", "image.anti_flicker", "null"),
            ("hflip", "image.hflip", "false"),
            ("vflip", "image.vflip", "false"),
        ]
        .into_iter()
        .map(|(name, path, fallback)| format!("\"{name}\":{}", raw_or(&prudynt, path, fallback)))
        .collect::<Vec<_>>()
        .join(",");
        let storage = [
            ("autostart", "recorder.autostart", "false", false),
            ("channel", "recorder.channel", "null", false),
            ("device_path", "recorder.device_path", "null", true),
            ("duration", "recorder.duration", "null", false),
            ("filename", "recorder.filename", "null", true),
            ("mount", "recorder.mount", "null", true),
        ]
        .into_iter()
        .map(|(name, path, fallback, text)| {
            let value = if text {
                string_or_null(&prudynt, path)
            } else {
                raw_or(&prudynt, path, fallback)
            };
            format!("\"{name}\":{value}")
        })
        .collect::<Vec<_>>()
        .join(",");
        let body = format!(
            "{{\"image\":{{{image}}},\"motion\":{{\"enabled\":{}}},\"daynight\":{{\"enabled\":{},\"force_mode\":{}}},\"privacy\":{{\"enabled\":{},\"channel\":\"all\"}},\"storage\":{{{storage}}},\"streams\":[{},{}],\"backend\":{{\"name\":\"prudynt\"}}}}\n",
            raw_or(&prudynt, "motion.enabled", "false"),
            raw_or(&thingino, "daynight.enabled", "false"),
            string_or_null(&thingino, "daynight.force_mode"),
            bool_json(self.paths.privacy_active.is_file()),
            stream_config(&prudynt, 0),
            stream_config(&prudynt, 1),
        );
        Ok(BackendResponse::json(body.into_bytes()))
    }
}
