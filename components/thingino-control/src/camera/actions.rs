use super::*;

pub(super) const IRCUT_PULSE_DURATION: Duration = Duration::from_millis(100);

impl PrudyntBackend {
    pub(super) fn daynight(
        &self,
        mode: DayNightMode,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        // Automatic sensing needs the daemon to consume future sensor samples.
        // Forced modes are owned completely by Control, so a stopped or stale
        // daynightd must not make the direct Prudynt/GPIO transition unavailable.
        let daynight_pid = match self.validated_daynight_pid() {
            Ok(pid) => Some(pid),
            Err(error) if mode == DayNightMode::Auto => return Err(error),
            Err(_) => None,
        };
        let original = read_bounded(&self.paths.thingino_config, FILE_LIMIT)?;
        let updated = daynight_config(&original, mode)?;
        write_in_place(&self.paths.thingino_config, &updated)?;
        if mode != DayNightMode::Auto
            && let Err(error) = self.apply_daynight_mode(mode, deadline)
        {
            let _ = write_in_place(&self.paths.thingino_config, &original);
            return Err(error);
        }
        if let Some(pid) = daynight_pid {
            let still_valid = self.validated_daynight_pid() == Ok(pid);
            // SAFETY: kill is called with the same path-validated live daynightd
            // PID and SIGHUP only. A forced transition is already complete if
            // the daemon exits during it; a later replacement reads the new
            // disabled configuration at startup.
            if (!still_valid || unsafe { kill(pid, SIGHUP) } != 0) && mode == DayNightMode::Auto {
                let _ = write_in_place(&self.paths.thingino_config, &original);
                return Err(BackendError::Unavailable);
            }
        }
        Ok(BackendResponse::json(
            format!(
                "{{\"status\":\"accepted\",\"mode\":\"{}\"}}\n",
                mode.as_str()
            )
            .into_bytes(),
        ))
    }

    pub(super) fn control(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.len() == 1 {
            if let Some(domain) = fields.get("motion").or_else(|| fields.get("privacy")) {
                let domain = domain.as_object().ok_or(BackendError::Protocol)?;
                if domain.len() != 1 || domain.get("enabled").and_then(Value::as_bool).is_none() {
                    return Err(BackendError::Protocol);
                }
                self.prudynt_json(body, deadline)?;
                return Ok(action_ok());
            }
            if let Some(audio) = fields.get("audio") {
                let audio = audio.as_object().ok_or(BackendError::Protocol)?;
                if audio.is_empty()
                    || audio
                        .keys()
                        .any(|key| !matches!(key.as_str(), "mic_enabled" | "spk_enabled"))
                    || audio.values().any(|value| value.as_bool().is_none())
                {
                    return Err(BackendError::Protocol);
                }
                self.prudynt_json(body, deadline)?;
                let mut state = self
                    .audio_state
                    .lock()
                    .map_err(|_| BackendError::Unavailable)?;
                if let Some(enabled) = audio.get("mic_enabled").and_then(Value::as_bool) {
                    state.microphone = enabled;
                }
                if let Some(enabled) = audio.get("spk_enabled").and_then(Value::as_bool) {
                    state.speaker = enabled;
                }
                return Ok(action_ok());
            }
            if let Some(mp4) = fields.get("mp4") {
                let mp4 = mp4.as_object().ok_or(BackendError::Protocol)?;
                if mp4.len() != 1 {
                    return Err(BackendError::Protocol);
                }
                let start = mp4.contains_key("start");
                let operation = if start {
                    mp4.get("start")
                } else {
                    mp4.get("stop")
                };
                let channel = operation.and_then(Value::as_object).and_then(|value| {
                    (value.len() == 1)
                        .then(|| value.get("channel"))
                        .flatten()
                        .and_then(value_u64)
                });
                if !matches!(channel, Some(0) | Some(1)) {
                    return Err(BackendError::Protocol);
                }
                if start && self.storage_format_busy() {
                    return Err(BackendError::Unavailable);
                }
                control_recorder(&self.paths, channel.unwrap_or_default(), start, deadline)?;
                return Ok(action_ok());
            }
        }
        let command = request
            .get_path("cmd")
            .and_then(Value::as_str)
            .ok_or(BackendError::Protocol)?;
        let raw = request.get_path("val").ok_or(BackendError::Protocol)?;
        if raw
            .as_str()
            .is_some_and(|value| matches!(value, "~" | "toggle"))
        {
            match command {
                "ir850" => self.toggle_light("ir850")?,
                _ => return Err(BackendError::Protocol),
            }
            return Ok(action_ok());
        }
        let enabled = match raw {
            Value::Bool(value) => *value,
            Value::Number(value) => value == "1",
            Value::String(value) => matches!(value.as_str(), "1" | "true" | "on" | "day"),
            _ => return Err(BackendError::Protocol),
        };
        match command {
            "color" => {
                let payload = object([(
                    "image",
                    object([("running_mode", number(color_running_mode(enabled)))]),
                )]);
                self.prudynt_json(payload.to_json().as_bytes(), deadline)?;
            }
            "ir850" => self.set_light("ir850", enabled)?,
            "ircut" => self.set_ircut(enabled)?,
            "auto" => {
                let mode = if enabled {
                    DayNightMode::Auto
                } else {
                    DayNightMode::Day
                };
                return self.daynight(mode, deadline);
            }
            "daynight" => {
                let mode = if enabled {
                    DayNightMode::Day
                } else {
                    DayNightMode::Night
                };
                return self.daynight(mode, deadline);
            }
            // These controls are deliberately absent in the D-Link A1 profile.
            "ir940" | "white" => return Err(BackendError::Protocol),
            _ => return Err(BackendError::Protocol),
        }
        self.disable_automatic_daynight()?;
        Ok(action_ok())
    }

    pub(super) fn restart_prudynt(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        self.prudynt_json(br#"{"action":{"restart_thread":7}}"#, deadline)?;
        // Prudynt uses execve for the D-Link media restart, so the producer PID
        // may not change and the old run marker can outlive the Motion thread.
        // Retire the current producer session only after Prudynt accepted the
        // command. The next structured observation starts a fresh session.
        self.motion.reconcile_prudynt(false);
        let mut state = self
            .audio_state
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        *state = configured_audio_state(&self.paths);
        *self
            .daynight_reapply
            .lock()
            .map_err(|_| BackendError::Unavailable)? =
            Some(Instant::now() + Duration::from_secs(2));
        Ok(action_ok())
    }

    pub(super) fn reapply_prudynt_daynight(
        &self,
        mode: DayNightMode,
        deadline: Instant,
    ) -> Result<(), BackendError> {
        let day = match mode {
            DayNightMode::Day => true,
            DayNightMode::Night => false,
            DayNightMode::Auto => return Ok(()),
        };
        let payload = object([(
            "image",
            object([("running_mode", number(color_running_mode(day)))]),
        )]);
        self.prudynt_json(payload.to_json().as_bytes(), deadline)?;
        Ok(())
    }

    pub(super) fn disable_automatic_daynight(&self) -> Result<(), BackendError> {
        let mut document = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        document
            .set_path("daynight.enabled", Value::Bool(false))
            .map_err(|_| BackendError::Protocol)?;
        let mut serialized = document.to_json().into_bytes();
        serialized.push(b'\n');
        write_in_place(&self.paths.thingino_config, &serialized)?;
        if let Some(pid) = read_pid(&self.paths.daynight_pid) {
            // SAFETY: PID comes from the validated service pid file; SIGHUP only reloads config.
            let _ = unsafe { kill(pid, SIGHUP) };
        }
        Ok(())
    }

    pub(super) fn set_light(&self, name: &str, enabled: bool) -> Result<(), BackendError> {
        let config = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let pin = json_u64(&config, &format!("gpio.{name}"))?;
        let active_low = config
            .get_path(&format!("gpio.{name}.active_low"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let level = enabled ^ active_low;
        write_gpio(&self.paths.sys_class_gpio, pin, level)
    }

    pub(super) fn light_state(&self, config: &Value, name: &str) -> Value {
        let Ok(pin) = json_u64(config, &format!("gpio.{name}")) else {
            return Value::Null;
        };
        let Some(raw) = read_attribute_text(
            &self.paths.sys_class_gpio.join(format!("gpio{pin}/value")),
            8,
        ) else {
            return Value::Null;
        };
        let raw = match raw.as_str() {
            "0" => false,
            "1" => true,
            _ => return Value::Null,
        };
        let active_low = config
            .get_path(&format!("gpio.{name}.active_low"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        number(u64::from(raw ^ active_low))
    }

    pub(super) fn toggle_light(&self, name: &str) -> Result<(), BackendError> {
        let config = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let pin = json_u64(&config, &format!("gpio.{name}"))?;
        let path = self.paths.sys_class_gpio.join(format!("gpio{pin}/value"));
        let current = read_text_value(&path, 8).ok_or(BackendError::Unavailable)?;
        let enabled = match current.as_str() {
            "0" => true,
            "1" => false,
            _ => return Err(BackendError::Protocol),
        };
        write_gpio(&self.paths.sys_class_gpio, pin, enabled)
    }

    pub(super) fn set_ircut(&self, day: bool) -> Result<(), BackendError> {
        self.pulse_ircut(day)?;
        write_config_file(
            &self.paths.ircut_state,
            if day { b"1\n" } else { b"0\n" },
            0o600,
        )
    }

    fn pulse_ircut(&self, day: bool) -> Result<(), BackendError> {
        let config = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let pins = config
            .get_path("gpio.ircut")
            .and_then(Value::as_str)
            .ok_or(BackendError::Protocol)?
            .split_ascii_whitespace()
            .map(|value| value.parse::<u64>().map_err(|_| BackendError::Protocol))
            .collect::<Result<Vec<_>, _>>()?;
        if pins.len() != 2 {
            return Err(BackendError::Protocol);
        }
        let (asserted, idle) = if day { (0, 1) } else { (1, 0) };
        write_gpio(&self.paths.sys_class_gpio, pins[idle], false)?;
        write_gpio(&self.paths.sys_class_gpio, pins[asserted], true)?;
        // The D-Link A1 latching coil did not move reliably with the generic
        // 10 ms Thingino pulse. A physical A/B on Camera 1 showed that both
        // directions switch deterministically at 100 ms.
        thread::sleep(IRCUT_PULSE_DURATION);
        write_gpio(&self.paths.sys_class_gpio, pins[asserted], false)
    }

    pub(super) fn apply_daynight_mode(
        &self,
        mode: DayNightMode,
        deadline: Instant,
    ) -> Result<(), BackendError> {
        let day = match mode {
            DayNightMode::Day => true,
            DayNightMode::Night => false,
            DayNightMode::Auto => return Ok(()),
        };
        let transition_already_applied = read_text_value(&self.paths.ircut_state, 8)
            .is_some_and(|value| value == if day { "1" } else { "0" });
        let config = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let controlled = |name: &str| {
            config
                .get_path(&format!("daynight.controls.{name}"))
                .and_then(Value::as_bool)
                .unwrap_or(matches!(name, "color" | "ircut" | "ir850" | "ir940"))
        };
        let set_light_if_configured = |name: &str, enabled: bool| {
            if !controlled(name) {
                return Ok(());
            }
            let Ok(pin) = json_u64(&config, &format!("gpio.{name}")) else {
                return Ok(());
            };
            let active_low = config
                .get_path(&format!("gpio.{name}.active_low"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            write_gpio(&self.paths.sys_class_gpio, pin, enabled ^ active_low)
        };

        if day {
            // Remove infrared illumination and engage the physical filter first.
            // A slow ISP transition must not leave a daylight image exposed to IR.
            set_light_if_configured("ir850", false)?;
            set_light_if_configured("ir940", false)?;
            set_light_if_configured("white", false)?;
            if controlled("ircut") {
                self.pulse_ircut(true)?;
            }
        }
        let requested_running_mode = color_running_mode(day);
        let running_mode_current = read_json_or_empty(&self.paths.prudynt_config)?
            .get_path("image.running_mode")
            .and_then(value_u64)
            == Some(requested_running_mode);
        // The legacy day/night path always sent Prudynt running_mode. The
        // separate color control flag governed its extra ISP helper only.
        if !transition_already_applied || !running_mode_current {
            self.reapply_prudynt_daynight(mode, deadline)?;
        }
        if !day {
            if controlled("ircut") {
                self.pulse_ircut(false)?;
            }
            set_light_if_configured("ir850", true)?;
            set_light_if_configured("ir940", true)?;
            set_light_if_configured("white", true)?;
        }
        if controlled("ircut") {
            write_config_file(
                &self.paths.ircut_state,
                if day { b"1\n" } else { b"0\n" },
                0o600,
            )?;
        }
        write_config_file(
            &self.paths.daynight_mode,
            if day { b"day\n" } else { b"night\n" },
            0o600,
        )
    }

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

pub(super) fn color_running_mode(color_enabled: bool) -> u64 {
    // The Ingenic ISP enum is 0 for day/color and 1 for night/monochrome.
    u64::from(!color_enabled)
}
