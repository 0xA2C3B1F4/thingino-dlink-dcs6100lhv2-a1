use super::super::*;

fn valid_timelapse_path(value: &str) -> bool {
    safe_path_fragment(value) && !value.bytes().any(|byte| byte.is_ascii_whitespace())
}

impl PrudyntBackend {
    pub(in crate::camera) fn recorder(&self) -> Result<BackendResponse, BackendError> {
        let prudynt = read_json_or_empty(&self.paths.prudynt_config)?;
        let timelapse = read_json_or_empty(&self.paths.timelapse_config)?;
        let hostname = read_text_value(&self.paths.hostname, 255)
            .filter(|value| safe_hostname(value))
            .unwrap_or_else(|| "thingino-camera".to_owned());
        let video = object([
            (
                "autostart",
                value_or(&prudynt, "recorder.autostart", Value::Bool(false)),
            ),
            ("channel", value_or(&prudynt, "recorder.channel", number(0))),
            (
                "check_interval",
                value_or(&prudynt, "recorder.check_interval", number(60)),
            ),
            (
                "cleanup_enabled",
                value_or(&prudynt, "recorder.cleanup_enabled", Value::Bool(false)),
            ),
            (
                "device_path",
                value_or(
                    &prudynt,
                    "recorder.device_path",
                    Value::String(format!("{hostname}/records")),
                ),
            ),
            (
                "duration",
                value_or(&prudynt, "recorder.duration", number(60)),
            ),
            (
                "filename",
                value_or(
                    &prudynt,
                    "recorder.filename",
                    Value::String("%Y%m%d/%H/%Y%m%dT%H%M%S".to_owned()),
                ),
            ),
            ("limit", value_or(&prudynt, "recorder.limit", number(15))),
            (
                "min_free_mb",
                value_or(&prudynt, "recorder.min_free_mb", number(500)),
            ),
            (
                "mount",
                value_or(&prudynt, "recorder.mount", Value::String(String::new())),
            ),
        ]);
        let timelapse_value = object([
            (
                "enabled",
                value_or(&timelapse, "timelapse.enabled", Value::Bool(false)),
            ),
            (
                "mount",
                value_or(&timelapse, "timelapse.mount", Value::String(String::new())),
            ),
            (
                "filepath",
                value_or(
                    &timelapse,
                    "timelapse.filepath",
                    Value::String(format!("{hostname}/timelapses")),
                ),
            ),
            (
                "filename",
                value_or(
                    &timelapse,
                    "timelapse.filename",
                    Value::String("%Y%m%d/%Y%m%dT%H%M%S.jpg".to_owned()),
                ),
            ),
            (
                "interval",
                value_or(&timelapse, "timelapse.interval", number(1)),
            ),
            (
                "keep_days",
                value_or(&timelapse, "timelapse.keep_days", number(7)),
            ),
            (
                "preset_enabled",
                value_or(&timelapse, "timelapse.preset_enabled", Value::Bool(false)),
            ),
            (
                "presets",
                object([
                    (
                        "ircut",
                        value_or(&timelapse, "timelapse.ircut", Value::Bool(false)),
                    ),
                    (
                        "ir850",
                        value_or(&timelapse, "timelapse.ir850", Value::Bool(false)),
                    ),
                    (
                        "ir940",
                        value_or(&timelapse, "timelapse.ir940", Value::Bool(false)),
                    ),
                    (
                        "white",
                        value_or(&timelapse, "timelapse.white", Value::Bool(false)),
                    ),
                    (
                        "color",
                        value_or(&timelapse, "timelapse.color", Value::Bool(false)),
                    ),
                ]),
            ),
        ]);
        let data = object([
            ("video", video),
            ("timelapse", timelapse_value),
            (
                "mounts",
                Value::Array(storage_mounts(&self.paths.proc_mounts)),
            ),
            (
                "messages",
                object([(
                    "strftime_hint",
                    Value::String(
                        "Supports strftime-style placeholders such as %Y or %H.".to_owned(),
                    ),
                )]),
            ),
            (
                "debug",
                object([
                    ("video", Value::String(String::new())),
                    ("timelapse", Value::String(String::new())),
                    ("crontab", Value::String(String::new())),
                ]),
            ),
        ]);
        json_response(object([("ok", Value::Bool(true)), ("data", data)]))
    }
    pub(in crate::camera) fn update_recorder(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        if self.storage_format_busy() {
            return Err(BackendError::Unavailable);
        }
        if body
            .iter()
            .copied()
            .find(|byte| !byte.is_ascii_whitespace())
            == Some(b'{')
        {
            return self.update_recorder_json(body);
        }
        let form = parse_form(body)?;
        match form.get("form").map(String::as_str) {
            Some("video") => {
                let mut update = BTreeMap::new();
                update.insert(
                    "autostart".to_owned(),
                    Value::Bool(form_bool(&form, "vr_autostart")),
                );
                update.insert(
                    "cleanup_enabled".to_owned(),
                    Value::Bool(form_bool(&form, "vr_cleanup_enabled")),
                );
                for (form_key, key, minimum) in [
                    ("vr_channel", "channel", 0_u64),
                    ("vr_duration", "duration", 1),
                    ("vr_limit", "limit", 1),
                    ("vr_min_free_mb", "min_free_mb", 1),
                    ("vr_check_interval", "check_interval", 1),
                ] {
                    let value = form_u64(&form, form_key, minimum)?;
                    if key == "channel" && value > 1 {
                        return Err(BackendError::Protocol);
                    }
                    update.insert(key.to_owned(), number(value));
                }
                for (form_key, key, required) in [
                    ("vr_mount", "mount", true),
                    ("vr_device_path", "device_path", false),
                    ("vr_filename", "filename", true),
                ] {
                    let mut value = form.get(form_key).cloned().unwrap_or_default();
                    if key == "filename" {
                        value = value.trim_start_matches('/').to_owned();
                    }
                    if required && value.is_empty()
                        || !safe_path_fragment(&value)
                        || value.bytes().any(|byte| byte.is_ascii_whitespace())
                    {
                        return Err(BackendError::Protocol);
                    }
                    update.insert(key.to_owned(), Value::String(value));
                }
                self.merge_config_domain(
                    &self.paths.prudynt_config,
                    "recorder",
                    Value::Object(update).to_json().as_bytes(),
                )?;
            }
            Some("timelapse") => {
                let enabled = form_bool(&form, "tl_enabled");
                let mount = form.get("tl_mount").cloned().unwrap_or_default();
                let filepath = form.get("tl_filepath").cloned().unwrap_or_default();
                let mut filename = form.get("tl_filename").cloned().unwrap_or_default();
                filename = filename.trim_start_matches('/').to_owned();
                if enabled && (mount.is_empty() || filename.is_empty())
                    || !valid_timelapse_path(&mount)
                    || !valid_timelapse_path(&filepath)
                    || !valid_timelapse_path(&filename)
                {
                    return Err(BackendError::Protocol);
                }
                let interval = form_u64(&form, "tl_interval", 1)?;
                if interval > 1440 {
                    return Err(BackendError::Protocol);
                }
                let keep_days = form_u64(&form, "tl_keep_days", 0)?;
                if keep_days > 365 {
                    return Err(BackendError::Protocol);
                }
                let update = object([
                    ("enabled", Value::Bool(enabled)),
                    ("mount", Value::String(mount)),
                    ("filepath", Value::String(filepath)),
                    ("filename", Value::String(filename)),
                    ("interval", number(interval)),
                    ("keep_days", number(keep_days)),
                    (
                        "preset_enabled",
                        Value::Bool(form_bool(&form, "tl_preset_enabled")),
                    ),
                    ("ircut", Value::Bool(form_bool(&form, "tl_ircut"))),
                    ("ir850", Value::Bool(form_bool(&form, "tl_ir850"))),
                    ("ir940", Value::Bool(form_bool(&form, "tl_ir940"))),
                    ("white", Value::Bool(form_bool(&form, "tl_white"))),
                    ("color", Value::Bool(form_bool(&form, "tl_color"))),
                ]);
                if !self.paths.timelapse_config.exists() {
                    write_config_file(&self.paths.timelapse_config, b"{}\n", 0o600)?;
                }
                self.merge_config_domain(
                    &self.paths.timelapse_config,
                    "timelapse",
                    update.to_json().as_bytes(),
                )?;
            }
            _ => return Err(BackendError::Protocol),
        }
        self.recorder()
    }
    pub(in crate::camera) fn update_recorder_json(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        if self.storage_format_busy() {
            return Err(BackendError::Unavailable);
        }
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let request = request.as_object().ok_or(BackendError::Protocol)?;
        if request.len() != 1 {
            return Err(BackendError::Protocol);
        }
        if let Some(video) = request.get("video") {
            let source = video.as_object().ok_or(BackendError::Protocol)?;
            if source.is_empty()
                || source.keys().any(|key| {
                    !matches!(
                        key.as_str(),
                        "autostart"
                            | "channel"
                            | "check_interval"
                            | "cleanup_enabled"
                            | "device_path"
                            | "duration"
                            | "filename"
                            | "limit"
                            | "min_free_mb"
                            | "mount"
                    )
                })
            {
                return Err(BackendError::Protocol);
            }
            let mut update = BTreeMap::new();
            for key in ["autostart", "cleanup_enabled"] {
                if let Some(value) = source.get(key) {
                    update.insert(
                        key.to_owned(),
                        Value::Bool(value.as_bool().ok_or(BackendError::Protocol)?),
                    );
                }
            }
            for (key, minimum, maximum) in [
                ("channel", 0_u64, 1_u64),
                ("check_interval", 1, 86_400),
                ("duration", 1, 86_400),
                ("limit", 1, 100_000),
                ("min_free_mb", 1, 1_000_000),
            ] {
                if let Some(value) = source.get(key) {
                    let value = value_u64(value)
                        .filter(|value| *value >= minimum && *value <= maximum)
                        .ok_or(BackendError::Protocol)?;
                    update.insert(key.to_owned(), number(value));
                }
            }
            for key in ["device_path", "filename", "mount"] {
                if let Some(value) = source.get(key) {
                    let value = value.as_str().ok_or(BackendError::Protocol)?;
                    if !safe_path_fragment(value)
                        || value.bytes().any(|byte| byte.is_ascii_whitespace())
                    {
                        return Err(BackendError::Protocol);
                    }
                    update.insert(key.to_owned(), Value::String(value.to_owned()));
                }
            }
            self.merge_config_domain(
                &self.paths.prudynt_config,
                "recorder",
                Value::Object(update).to_json().as_bytes(),
            )?;
        } else if let Some(timelapse) = request.get("timelapse") {
            let source = timelapse.as_object().ok_or(BackendError::Protocol)?;
            if source.is_empty()
                || source.keys().any(|key| {
                    !matches!(
                        key.as_str(),
                        "enabled"
                            | "mount"
                            | "filepath"
                            | "filename"
                            | "interval"
                            | "keep_days"
                            | "preset_enabled"
                            | "presets"
                    )
                })
            {
                return Err(BackendError::Protocol);
            }
            let mut update = BTreeMap::new();
            for key in ["enabled", "preset_enabled"] {
                if let Some(value) = source.get(key) {
                    update.insert(
                        key.to_owned(),
                        Value::Bool(value.as_bool().ok_or(BackendError::Protocol)?),
                    );
                }
            }
            for (key, maximum) in [("interval", 1440_u64), ("keep_days", 365)] {
                if let Some(value) = source.get(key) {
                    let value = value_u64(value)
                        .filter(|value| *value <= maximum && (key == "keep_days" || *value >= 1))
                        .ok_or(BackendError::Protocol)?;
                    update.insert(key.to_owned(), number(value));
                }
            }
            for key in ["mount", "filepath", "filename"] {
                if let Some(value) = source.get(key) {
                    let value = value.as_str().ok_or(BackendError::Protocol)?;
                    if !valid_timelapse_path(value) {
                        return Err(BackendError::Protocol);
                    }
                    update.insert(key.to_owned(), Value::String(value.to_owned()));
                }
            }
            if let Some(presets) = source.get("presets") {
                let presets = presets.as_object().ok_or(BackendError::Protocol)?;
                if presets.is_empty()
                    || presets.keys().any(|key| {
                        !matches!(
                            key.as_str(),
                            "ircut" | "ir850" | "ir940" | "white" | "color"
                        )
                    })
                {
                    return Err(BackendError::Protocol);
                }
                for (key, value) in presets {
                    update.insert(
                        key.clone(),
                        Value::Bool(value.as_bool().ok_or(BackendError::Protocol)?),
                    );
                }
            }
            if !self.paths.timelapse_config.exists() {
                write_config_file(&self.paths.timelapse_config, b"{}\n", 0o600)?;
            }
            self.merge_config_domain(
                &self.paths.timelapse_config,
                "timelapse",
                Value::Object(update).to_json().as_bytes(),
            )?;
        } else {
            return Err(BackendError::Protocol);
        }
        self.recorder()
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        fs::create_dir_all(&root).unwrap();
        let path = PathBuf::from(root).join(format!(
            "thingino-control-recorder-{}-{}-{name}",
            std::process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    fn test_backend(root: &Path) -> PrudyntBackend {
        PrudyntBackend::new(CameraPaths {
            prudynt_config: root.join("prudynt.json"),
            timelapse_config: root.join("timelapse.json"),
            hostname: root.join("hostname"),
            proc_mounts: root.join("mounts"),
            ..CameraPaths::default()
        })
    }

    fn form_encode(value: &str) -> String {
        const HEX: &[u8; 16] = b"0123456789ABCDEF";
        let mut encoded = String::with_capacity(value.len());
        for byte in value.bytes() {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~' | b'/') {
                encoded.push(char::from(byte));
            } else {
                encoded.push('%');
                encoded.push(char::from(HEX[usize::from(byte >> 4)]));
                encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
            }
        }
        encoded
    }

    fn timelapse_form(filepath: &str) -> Vec<u8> {
        format!(
            "form=timelapse&tl_enabled=1&tl_mount=/mnt/card&tl_filepath={}&tl_filename=%25Y%25m%25d.jpg&tl_interval=1&tl_keep_days=7",
            form_encode(filepath)
        )
        .into_bytes()
    }

    fn timelapse_json(filepath: &str) -> Vec<u8> {
        object([(
            "timelapse",
            object([("filepath", Value::String(filepath.to_owned()))]),
        )])
        .to_json()
        .into_bytes()
    }

    fn assert_success(response: &BackendResponse) {
        assert_eq!(
            json::parse(&response.body)
                .unwrap()
                .get_path("ok")
                .and_then(Value::as_bool),
            Some(true)
        );
    }

    #[test]
    fn timelapse_form_and_json_filepath_validation_match() {
        let cases = [
            ("camera/timelapses".to_owned(), true),
            ("nested/2026".to_owned(), true),
            ("../escape".to_owned(), false),
            ("with space".to_owned(), false),
            ("line\nbreak".to_owned(), false),
            ("a".repeat(513), false),
        ];

        for (index, (filepath, accepted)) in cases.iter().enumerate() {
            let form_root = task_temp(&format!("matrix-form-{index}"));
            let form_backend = test_backend(&form_root);
            let form_result = form_backend.update_recorder(&timelapse_form(filepath));
            assert_eq!(
                form_result.is_ok(),
                *accepted,
                "form filepath: {filepath:?}"
            );
            if let Ok(response) = form_result {
                assert_success(&response);
            } else {
                assert!(!form_root.join("timelapse.json").exists());
            }

            let json_root = task_temp(&format!("matrix-json-{index}"));
            let json_backend = test_backend(&json_root);
            let json_result = json_backend.update_recorder_json(&timelapse_json(filepath));
            assert_eq!(
                json_result.is_ok(),
                *accepted,
                "JSON filepath: {filepath:?}"
            );
            if let Ok(response) = json_result {
                assert_success(&response);
            } else {
                assert!(!json_root.join("timelapse.json").exists());
            }

            fs::remove_dir_all(form_root).unwrap();
            fs::remove_dir_all(json_root).unwrap();
        }
    }

    #[test]
    fn invalid_timelapse_updates_leave_existing_config_unchanged() {
        let root = task_temp("unchanged");
        let config = root.join("timelapse.json");
        let original = b"{\"timelapse\":{\"enabled\":true,\"filepath\":\"camera/timelapses\",\"filename\":\"%Y.jpg\",\"interval\":1,\"keep_days\":7,\"mount\":\"/mnt/card\"}}\n";
        fs::write(&config, original).unwrap();
        let backend = test_backend(&root);

        assert_eq!(
            backend.update_recorder(&timelapse_form("../escape")),
            Err(BackendError::Protocol)
        );
        assert_eq!(fs::read(&config).unwrap(), original);
        assert_eq!(
            backend.update_recorder_json(&timelapse_json("with space")),
            Err(BackendError::Protocol)
        );
        assert_eq!(fs::read(&config).unwrap(), original);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn timelapse_capture_and_cleanup_reject_unsafe_stored_filepath() {
        let root = task_temp("runtime-fail-closed");
        let paths = CameraPaths {
            prudynt_config: root.join("prudynt.json"),
            timelapse_config: root.join("timelapse.json"),
            proc_mounts: root.join("mounts"),
            ..CameraPaths::default()
        };
        fs::write(&paths.prudynt_config, b"{}\n").unwrap();
        fs::write(
            &paths.proc_mounts,
            b"/dev/mmcblk0p1 /mnt/card vfat rw,sync 0 0\n",
        )
        .unwrap();
        let config = object([(
            "timelapse",
            object([
                ("enabled", Value::Bool(true)),
                ("mount", Value::String("/mnt/card".to_owned())),
                ("filepath", Value::String("../escape".to_owned())),
                ("filename", Value::String("%Y.jpg".to_owned())),
                ("keep_days", number(7)),
            ]),
        )]);

        assert_eq!(
            capture_timelapse(&paths, &config, 0),
            Err(BackendError::Protocol)
        );
        fs::write(&paths.timelapse_config, format!("{}\n", config.to_json())).unwrap();
        assert_eq!(
            cleanup_recording_storage(&paths, 0),
            Err(BackendError::Protocol)
        );

        fs::remove_dir_all(root).unwrap();
    }
}
