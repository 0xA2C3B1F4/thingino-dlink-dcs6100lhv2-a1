use super::*;

impl HostBackend {
    pub(super) fn reset_actions(&self) -> Result<BackendResponse, BackendError> {
        json_response(object([(
            "actions",
            Value::Array(vec![
                object([
                    ("id", Value::String("reboot".to_owned())),
                    ("title", Value::String("Reboot camera".to_owned())),
                    (
                        "description_html",
                        Value::String("Reboot the camera to apply new settings.".to_owned()),
                    ),
                    (
                        "cta",
                        object([
                            ("type", Value::String("form".to_owned())),
                            ("method", Value::String("POST".to_owned())),
                            ("action", Value::String("/api/v1/actions/reboot".to_owned())),
                            ("button", Value::String("Reboot camera".to_owned())),
                            ("variant", Value::String("danger".to_owned())),
                            (
                                "fields",
                                Value::Array(vec![object([
                                    ("name", Value::String("action".to_owned())),
                                    ("value", Value::String("reboot".to_owned())),
                                ])]),
                            ),
                        ]),
                    ),
                ]),
                reset_link(
                    "wipeoverlay",
                    "Wipe overlay",
                    "Remove files stored in the overlay partition.",
                ),
                reset_link(
                    "fullreset",
                    "Reset firmware",
                    "Restore the writable overlay to defaults.",
                ),
            ]),
        )]))
    }

    pub(super) fn factory_reset(&self, body: &[u8]) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let action = request
            .get_path("action")
            .and_then(Value::as_str)
            .filter(|value| matches!(*value, "wipeoverlay" | "fullreset"))
            .ok_or(BackendError::Protocol)?;
        request_overlay_reset(&self.paths.overlay).map_err(|_| BackendError::Unavailable)?;
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(750));
            reboot_now();
        });
        json_response(object([
            ("status", Value::String("accepted".to_owned())),
            ("action", Value::String(action.to_owned())),
            ("reboot", Value::Bool(true)),
        ]))
    }

    pub(super) fn reboot(&self) -> Result<BackendResponse, BackendError> {
        thread::spawn(|| {
            thread::sleep(Duration::from_millis(750));
            reboot_now();
        });
        json_response(object([
            ("status", Value::String("accepted".to_owned())),
            ("message", Value::String("Reboot scheduled".to_owned())),
        ]))
    }
}
