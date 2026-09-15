use super::*;
use std::collections::BTreeMap;

const LEVELS: [(&str, &str, &str, i32, i32); 4] = [
    ("mic_vol", "set-volume", "volume", -30, 120),
    ("mic_gain", "set-gain", "gain", 0, 31),
    ("spk_vol", "ao-set-volume", "ao_volume", -30, 120),
    ("spk_gain", "ao-set-gain", "ao_gain", 0, 31),
];

impl RaptorBackend {
    fn audio_observation(&self, deadline: Instant) -> Result<Value, BackendError> {
        self.audio_levels(deadline, false)
    }

    fn audio_levels(&self, deadline: Instant, extended: bool) -> Result<Value, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rad,
            if extended {
                br#"{"cmd":"get-audio-observation-levels"}"#
            } else {
                br#"{"cmd":"get-audio-levels"}"#
            },
            deadline,
        )?;
        require_ok(&reply)?;
        reply
            .require_only_fields(&["status", "fields", "ai_enabled", "ao_enabled"])
            .map_err(|_| BackendError::Upstream(502))?;
        let fields = reply
            .value
            .get_path("fields")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        if fields.len() != LEVELS.len() {
            return Err(BackendError::Upstream(502));
        }
        for (name, _, _, min, max) in LEVELS {
            let field = fields.get(name).ok_or(BackendError::Upstream(502))?;
            let object = field.as_object().ok_or(BackendError::Upstream(502))?;
            if object.len() != 5
                || object.keys().any(|key| {
                    !["supported", "available", "value", "min", "max"].contains(&key.as_str())
                })
            {
                return Err(BackendError::Upstream(502));
            }
            let supported = field
                .get_path("supported")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?;
            let available = field
                .get_path("available")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?;
            if audio_integer(field.get_path("min")) != Some(min)
                || audio_integer(field.get_path("max")) != Some(max)
            {
                return Err(BackendError::Upstream(502));
            }
            if available {
                if !supported
                    || audio_integer(field.get_path("value"))
                        .is_none_or(|value| !(min..=max).contains(&value))
                {
                    return Err(BackendError::Upstream(502));
                }
            } else if field.get_path("value") != Some(&Value::Null) {
                return Err(BackendError::Upstream(502));
            }
        }
        let mut result = BTreeMap::new();
        result.insert("source".into(), Value::String("raptor".into()));
        result.insert("levels".into(), Value::Object(fields.clone()));
        for (name, _, _, _, _) in LEVELS {
            result.insert(name.into(), fields[name].get_path("value").unwrap().clone());
        }
        for (public, daemon) in [("mic_enabled", "ai_enabled"), ("spk_enabled", "ao_enabled")] {
            let enabled = reply
                .value
                .get_path(daemon)
                .ok_or(BackendError::Upstream(502))?;
            if enabled.as_bool().is_none() && !(extended && enabled == &Value::Null) {
                return Err(BackendError::Upstream(502));
            }
            result.insert(public.into(), enabled.clone());
        }
        Ok(Value::Object(result))
    }

    pub(super) fn live_audio_owner_states(&self, deadline: Instant) -> Result<Value, BackendError> {
        self.audio_levels(deadline, true)
    }

    pub(super) fn audio_config(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let observed = self.audio_settings_observation(deadline)?;
        Ok(BackendResponse::json(observed.to_json().into_bytes()))
    }

    pub(super) fn update_audio(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let root = request
            .as_object()
            .filter(|root| root.len() == 1)
            .ok_or(BackendError::Protocol)?;
        if !root.contains_key("audio") {
            return Err(BackendError::Unsupported(
                "This configuration domain is not yet mapped to Raptor",
            ));
        }
        if root
            .get("audio")
            .and_then(Value::as_object)
            .is_some_and(|fields| {
                fields
                    .keys()
                    .any(|name| !LEVELS.iter().any(|level| level.0 == name))
            })
        {
            return self.update_audio_settings(root.get("audio").unwrap(), deadline);
        }
        let fields = root
            .get("audio")
            .and_then(Value::as_object)
            .filter(|fields| !fields.is_empty() && fields.len() <= 4)
            .ok_or(BackendError::Protocol)?;
        let mut changes = Vec::new();
        for (name, value) in fields {
            let (_, command, key, min, max) =
                LEVELS
                    .iter()
                    .find(|level| level.0 == name)
                    .ok_or(BackendError::Unsupported(
                        "This audio setting is not yet mapped to RAD",
                    ))?;
            let number = audio_integer(Some(value))
                .filter(|number| (min..=max).contains(&number))
                .ok_or(BackendError::Protocol)?;
            changes.push((name.as_str(), *command, *key, number));
        }
        let _mutation = self.lock_mutation()?;
        let observed = self.audio_observation(deadline)?;
        for (name, _, _, _) in &changes {
            if observed
                .get_path(&format!("levels.{name}.available"))
                .and_then(Value::as_bool)
                != Some(true)
            {
                return Err(BackendError::Unavailable);
            }
        }
        // Input level setters must not interfere with active microphone
        // privacy/mute. RAD owns speaker levels independently.
        if changes
            .iter()
            .any(|(name, _, _, _)| name.starts_with("mic_"))
        {
            let status = self.command(RaptorDaemon::Rad, br#"{"cmd":"status"}"#, deadline)?;
            require_ok(&status)?;
            if status.value.get_path("muted").and_then(Value::as_bool) != Some(false) {
                return Err(BackendError::Unsupported(
                    "Audio input is muted or its mute state is unknown; explicitly clear privacy or mute before changing microphone levels",
                ));
            }
        }
        let apply = || -> Result<(), BackendError> {
            for (_, command, _, number) in &changes {
                let body = format!("{{\"cmd\":\"{command}\",\"value\":{number}}}");
                require_ok(&self.command(RaptorDaemon::Rad, body.as_bytes(), deadline)?)?;
            }
            let live = self.audio_observation(deadline)?;
            for (name, _, _, number) in &changes {
                if audio_integer(live.get_path(name)) != Some(*number) {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rad, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let disk = self.command(
                RaptorDaemon::Rad,
                br#"{"cmd":"config-read-section","section":"audio"}"#,
                deadline,
            )?;
            require_ok(&disk)?;
            disk.require_only_fields(&["status", "section", "keys"])?;
            if disk.value.get_path("section").and_then(Value::as_str) != Some("audio") {
                return Err(BackendError::Upstream(502));
            }
            for (_, _, key, number) in &changes {
                if disk
                    .value
                    .get_path(&format!("keys.{key}"))
                    .and_then(Value::as_str)
                    != Some(number.to_string().as_str())
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Audio levels may have changed, but apply, save or readback was incomplete. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

fn audio_integer(value: Option<&Value>) -> Option<i32> {
    match value {
        Some(Value::Number(number)) => number.parse().ok(),
        _ => None,
    }
}

const INPUT_NUMBERS: [(&str, &str, i32, i32); 4] = [
    ("mic_alc_gain", "alc_gain", 0, 7),
    ("mic_noise_suppression", "ns_level", 0, 4),
    ("mic_agc_target_level_dbfs", "agc_target_dbfs", 0, 31),
    ("mic_agc_compression_gain_db", "agc_compression_db", 0, 90),
];
const CODECS: [(&str, &str); 5] = [
    ("PCM", "l16"),
    ("G711U", "pcmu"),
    ("G711A", "pcma"),
    ("AAC", "aac"),
    ("OPUS", "opus"),
];

impl RaptorBackend {
    fn input_observation(&self, deadline: Instant) -> Result<RaptorReply, BackendError> {
        let input = self.command(RaptorDaemon::Rad, br#"{"cmd":"get-input-state"}"#, deadline)?;
        require_ok(&input)?;
        if input.value.get_path("readback").and_then(Value::as_str) != Some("owner") {
            return Err(BackendError::Upstream(502));
        }
        let available = input
            .value
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let enabled = input
            .value
            .get_path("enabled")
            .ok_or(BackendError::Upstream(502))?;
        let muted = input
            .value
            .get_path("muted")
            .ok_or(BackendError::Upstream(502))?;
        if (available && (enabled.as_bool().is_none() || muted.as_bool().is_none()))
            || (!available && (enabled != &Value::Null || muted != &Value::Null))
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(input)
    }

    pub(super) fn live_audio_observation(&self, deadline: Instant) -> Result<Value, BackendError> {
        let mut result = self.audio_levels(deadline, true)?;
        let input = self.input_observation(deadline)?;
        for (public, native) in [("mic_enabled", "enabled"), ("mic_muted", "muted")] {
            result
                .set_path(public, input.value.get_path(native).unwrap().clone())
                .map_err(|_| BackendError::Upstream(502))?;
        }
        Ok(result)
    }

    // The live-control dispatcher already holds the shared mutation lock.
    pub(super) fn set_live_audio(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let fields = request
            .as_object()
            .filter(|fields| fields.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let (name, value) = fields.iter().next().unwrap();
        let prefix = match name.as_str() {
            "mic_enabled" => "ai",
            "spk_enabled" => "ao",
            _ => return Err(BackendError::Protocol),
        };
        let enabled = value.as_bool().ok_or(BackendError::Protocol)?;
        let before = self.live_audio_observation(deadline)?;
        if before.get_path(name).and_then(Value::as_bool).is_none() {
            return Err(BackendError::Unavailable);
        }
        let apply = || -> Result<(), BackendError> {
            let command = format!(
                r#"{{"cmd":"{prefix}-{}"}}"#,
                if enabled { "enable" } else { "disable" }
            );
            require_ok(&self.command(RaptorDaemon::Rad, command.as_bytes(), deadline)?)?;
            let after = self.live_audio_observation(deadline)?;
            if after.get_path(name).and_then(Value::as_bool) != Some(enabled)
                || after.get_path("mic_muted") != before.get_path("mic_muted")
            {
                return Err(BackendError::Upstream(502));
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "Audio transition or independent readback failed. Reload before explicitly retrying."))?;
        Ok(BackendResponse::json(br#"{"status":"accepted"}"#.to_vec()))
    }

    pub(super) fn audio_settings_observation(
        &self,
        deadline: Instant,
    ) -> Result<Value, BackendError> {
        let mut result = self.audio_levels(deadline, true)?;
        let input = self.input_observation(deadline)?;
        let available = input
            .value
            .get_path("available")
            .and_then(Value::as_bool)
            .unwrap();
        let enabled = input.value.get_path("enabled").unwrap();
        let muted = input.value.get_path("muted").unwrap();
        result
            .set_path("mic_enabled", enabled.clone())
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("mic_muted", muted.clone())
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("input_readback", Value::String("owner".into()))
            .map_err(|_| BackendError::Upstream(502))?;
        let active = available && enabled.as_bool() == Some(true);
        for (name, owner_active) in [
            ("mic_vol", active),
            ("mic_gain", active),
            (
                "spk_vol",
                result.get_path("spk_enabled").and_then(Value::as_bool) == Some(true),
            ),
            (
                "spk_gain",
                result.get_path("spk_enabled").and_then(Value::as_bool) == Some(true),
            ),
        ] {
            if !owner_active
                && result
                    .get_path(&format!("levels.{name}.available"))
                    .and_then(Value::as_bool)
                    == Some(true)
            {
                return Err(BackendError::Upstream(502));
            }
        }

        let built = input
            .value
            .get_path("codecs_built")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        if built.len() != CODECS.len() {
            return Err(BackendError::Upstream(502));
        }
        let mut codecs = BTreeMap::new();
        for (public, daemon) in CODECS {
            let supported = built
                .get(daemon)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?;
            codecs.insert(public.into(), Value::Bool(supported));
        }
        let codec = if active {
            let daemon = input
                .value
                .get_path("codec")
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?;
            let public = CODECS
                .iter()
                .find(|(_, name)| *name == daemon)
                .ok_or(BackendError::Upstream(502))?
                .0;
            if codecs[public].as_bool() != Some(true) {
                return Err(BackendError::Upstream(502));
            }
            Value::String(public.into())
        } else {
            Value::Null
        };
        let rate = if active {
            let rate = audio_integer(input.value.get_path("sample_rate"))
                .filter(|rate| [8000, 16000, 24000, 32000, 44100, 48000, 96000].contains(rate))
                .ok_or(BackendError::Upstream(502))?;
            if [Some("G711A"), Some("G711U")].contains(&codec.as_str()) && rate != 8000 {
                return Err(BackendError::Upstream(502));
            }
            Value::Number(rate.to_string())
        } else {
            Value::Null
        };
        result
            .set_path("mic_sample_rate", rate)
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("mic_format", codec)
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("codecs_built", Value::Object(codecs))
            .map_err(|_| BackendError::Upstream(502))?;
        let alc = self.command(RaptorDaemon::Rad, br#"{"cmd":"get-alc-gain"}"#, deadline)?;
        require_ok(&alc)?;
        let field = alc
            .value
            .get_path("field")
            .ok_or(BackendError::Upstream(502))?;
        validate_audio_field(field, 0, 7)?;
        if field.get_path("available").and_then(Value::as_bool) == Some(true) && !active {
            return Err(BackendError::Upstream(502));
        }
        result
            .set_path("levels.mic_alc_gain", field.clone())
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("mic_alc_gain", field.get_path("value").unwrap().clone())
            .map_err(|_| BackendError::Upstream(502))?;
        let effects = input
            .value
            .get_path("effects_built")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let processing = input
            .value
            .get_path("processing")
            .ok_or(BackendError::Upstream(502))?;
        let processing_available = processing
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if processing.get_path("readback").and_then(Value::as_str) != Some("owner")
            || (processing_available && (!effects || !active))
        {
            return Err(BackendError::Upstream(502));
        }
        result
            .set_path("effects_built", Value::Bool(effects))
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("processing_available", Value::Bool(processing_available))
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("processing_readback", Value::String("owner".into()))
            .map_err(|_| BackendError::Upstream(502))?;
        for (public, daemon) in [("mic_high_pass_filter", "hpf"), ("mic_agc_enabled", "agc")] {
            let value = if processing_available {
                Value::Bool(
                    processing
                        .get_path(daemon)
                        .and_then(Value::as_bool)
                        .ok_or(BackendError::Upstream(502))?,
                )
            } else {
                Value::Null
            };
            result
                .set_path(public, value)
                .map_err(|_| BackendError::Upstream(502))?;
        }
        for (public, daemon, max) in [
            ("mic_noise_suppression", "level", 3),
            ("mic_agc_target_level_dbfs", "target", 31),
            ("mic_agc_compression_gain_db", "compression", 90),
        ] {
            let value = if processing_available {
                let number = audio_integer(processing.get_path(daemon))
                    .filter(|n| (0..=max).contains(n))
                    .ok_or(BackendError::Upstream(502))?;
                let number = if daemon == "level" {
                    if processing
                        .get_path("ns")
                        .and_then(Value::as_bool)
                        .ok_or(BackendError::Upstream(502))?
                    {
                        number + 1
                    } else {
                        0
                    }
                } else {
                    number
                };
                Value::Number(number.to_string())
            } else {
                Value::Null
            };
            result
                .set_path(public, value)
                .map_err(|_| BackendError::Upstream(502))?;
        }
        // The DCS-6100LHV2 A1 profile fixes RAD to the analog microphone and
        // RAD publishes one-channel audio. Prudynt queue/tap controls have no
        // equivalent RAD owner, so expose their status without inventing a
        // writable mapping.
        for (name, value) in [
            ("mic_is_digital", Value::Bool(false)),
            ("force_stereo", Value::Bool(false)),
            ("buffer_warn_frames", Value::Null),
            ("buffer_cap_frames", Value::Null),
            ("tap_enabled", Value::Null),
            ("tap_path", Value::Null),
        ] {
            result
                .set_path(name, value)
                .map_err(|_| BackendError::Upstream(502))?;
        }
        result
            .set_path(
                "mic_input_basis",
                Value::String("dlink-a1-profile-amic".into()),
            )
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("channel_basis", Value::String("rad-fixed-mono".into()))
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path(
                "buffer_control",
                Value::String("unsupported-prudynt-queue-policy".into()),
            )
            .map_err(|_| BackendError::Upstream(502))?;
        result
            .set_path("tap_control", Value::String("unsupported".into()))
            .map_err(|_| BackendError::Upstream(502))?;
        Ok(result)
    }

    fn update_audio_settings(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let fields = request
            .as_object()
            .filter(|fields| !fields.is_empty())
            .ok_or(BackendError::Protocol)?;
        // Validate all values before requesting or changing daemon state.
        for (name, value) in fields {
            if let Some((_, _, _, min, max)) = LEVELS.iter().find(|row| row.0 == name) {
                audio_integer(Some(value))
                    .filter(|n| (min..=max).contains(&n))
                    .ok_or(BackendError::Protocol)?;
            } else if let Some((_, _, min, max)) = INPUT_NUMBERS.iter().find(|row| row.0 == name) {
                audio_integer(Some(value))
                    .filter(|n| (min..=max).contains(&n))
                    .ok_or(BackendError::Protocol)?;
            } else if [
                "mic_enabled",
                "spk_enabled",
                "mic_agc_enabled",
                "mic_high_pass_filter",
            ]
            .contains(&name.as_str())
            {
                value.as_bool().ok_or(BackendError::Protocol)?;
            } else if name == "mic_format" {
                CODECS
                    .iter()
                    .find(|row| Some(row.0) == value.as_str())
                    .ok_or(BackendError::Protocol)?;
            } else {
                return Err(BackendError::Unsupported(
                    "This audio setting is not mapped to RAD",
                ));
            }
        }
        let _mutation = self.lock_mutation()?;
        let observed = self.audio_settings_observation(deadline)?;
        let disabling = fields.get("mic_enabled").and_then(Value::as_bool) == Some(false);
        let disabling_output = fields.get("spk_enabled").and_then(Value::as_bool) == Some(false);
        let mut commands = Vec::<String>::new();
        let mut disk_values = BTreeMap::<String, String>::new();
        let mut expected_live = fields.clone();
        if let Some(muted) = observed
            .get_path("mic_muted")
            .filter(|v| v.as_bool().is_some())
        {
            expected_live.insert("mic_muted".into(), muted.clone());
        }
        let mut processing = false;
        for (name, value) in fields {
            if disabling && name.starts_with("mic_") && name != "mic_enabled" {
                return Err(BackendError::Protocol);
            }
            if disabling_output && name.starts_with("spk_") && name != "spk_enabled" {
                return Err(BackendError::Protocol);
            }
            if name == "mic_enabled" {
                let enabled = value.as_bool().unwrap();
                commands.push(format!(
                    r#"{{"cmd":"ai-{}"}}"#,
                    if enabled { "enable" } else { "disable" }
                ));
                disk_values.insert("ai_enabled".into(), enabled.to_string());
            } else if name == "spk_enabled" {
                let enabled = value.as_bool().unwrap();
                commands.push(format!(
                    r#"{{"cmd":"ao-{}"}}"#,
                    if enabled { "enable" } else { "disable" }
                ));
                disk_values.insert("ao_enabled".into(), enabled.to_string());
            } else if name == "mic_format" {
                if observed.get_path("mic_enabled").and_then(Value::as_bool) != Some(true) {
                    return Err(BackendError::Unavailable);
                }
                let public = value.as_str().unwrap();
                if observed
                    .get_path(&format!("codecs_built.{public}"))
                    .and_then(Value::as_bool)
                    != Some(true)
                {
                    return Err(BackendError::Unsupported("This codec was not built"));
                }
                let codec = CODECS.iter().find(|row| row.0 == public).unwrap().1;
                commands.push(format!(r#"{{"cmd":"set-codec","value":"{codec}"}}"#));
                disk_values.insert("codec".into(), codec.into());
                let rate = if ["pcma", "pcmu"].contains(&codec) {
                    8000
                } else {
                    audio_integer(observed.get_path("mic_sample_rate"))
                        .ok_or(BackendError::Upstream(502))?
                };
                disk_values.insert("sample_rate".into(), rate.to_string());
            } else if !LEVELS.iter().any(|row| row.0 == name) && name != "mic_alc_gain" {
                if observed.get_path("effects_built").and_then(Value::as_bool) != Some(true) {
                    return Err(BackendError::Unsupported("Audio effects were not built"));
                }
                if observed
                    .get_path("processing_available")
                    .and_then(Value::as_bool)
                    != Some(true)
                {
                    return Err(BackendError::Unavailable);
                }
                processing = true;
            }
        }
        if processing {
            let get = |name: &str| {
                fields
                    .get(name)
                    .or_else(|| observed.get_path(name))
                    .unwrap()
            };
            let ns = audio_integer(Some(get("mic_noise_suppression"))).unwrap();
            let hpf = get("mic_high_pass_filter").as_bool().unwrap();
            let agc = get("mic_agc_enabled").as_bool().unwrap();
            let target = audio_integer(Some(get("mic_agc_target_level_dbfs"))).unwrap();
            let compression = audio_integer(Some(get("mic_agc_compression_gain_db"))).unwrap();
            let level = (ns - 1).max(0);
            for (name, value) in [
                ("mic_noise_suppression", Value::Number(ns.to_string())),
                ("mic_high_pass_filter", Value::Bool(hpf)),
                ("mic_agc_enabled", Value::Bool(agc)),
                (
                    "mic_agc_target_level_dbfs",
                    Value::Number(target.to_string()),
                ),
                (
                    "mic_agc_compression_gain_db",
                    Value::Number(compression.to_string()),
                ),
            ] {
                expected_live.insert(name.into(), value);
            }
            commands.push(format!(r#"{{"cmd":"set-input-processing","ns":{},"level":{level},"hpf":{},"agc":{},"target":{target},"compression":{compression}}}"#, i32::from(ns > 0), i32::from(hpf), i32::from(agc)));
            for (key, value) in [
                ("ns_enabled", (ns > 0).to_string()),
                ("ns_level", level.to_string()),
                ("hpf_enabled", hpf.to_string()),
                ("agc_enabled", agc.to_string()),
                ("agc_target_dbfs", target.to_string()),
                ("agc_compression_db", compression.to_string()),
            ] {
                disk_values.insert(key.into(), value);
            }
        }
        for (name, value) in fields {
            let mapping = LEVELS
                .iter()
                .find(|row| row.0 == name)
                .map(|row| (row.1, row.2))
                .or_else(|| (name == "mic_alc_gain").then_some(("set-alc-gain", "alc_gain")));
            if let Some((command, key)) = mapping {
                if observed
                    .get_path(&format!("levels.{name}.available"))
                    .and_then(Value::as_bool)
                    != Some(true)
                {
                    return Err(BackendError::Unavailable);
                }
                if name.starts_with("mic_")
                    && observed.get_path("mic_muted").and_then(Value::as_bool) != Some(false)
                {
                    return Err(BackendError::Unsupported(
                        "Explicitly clear audio mute before changing microphone levels",
                    ));
                }
                let number = audio_integer(Some(value)).unwrap();
                commands.push(format!(r#"{{"cmd":"{command}","value":{number}}}"#));
                disk_values.insert(key.into(), number.to_string());
            }
        }
        let apply = || -> Result<(), BackendError> {
            for command in &commands {
                require_ok(&self.command(RaptorDaemon::Rad, command.as_bytes(), deadline)?)?;
            }
            let live = self.audio_settings_observation(deadline)?;
            for (name, value) in &expected_live {
                if live.get_path(name) != Some(value) {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(rate) = disk_values.get("sample_rate")
                && audio_integer(live.get_path("mic_sample_rate"))
                    .map(|n| n.to_string())
                    .as_ref()
                    != Some(rate)
            {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rad, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let disk = self.command(
                RaptorDaemon::Rad,
                br#"{"cmd":"config-read-section","section":"audio"}"#,
                deadline,
            )?;
            require_ok(&disk)?;
            disk.require_only_fields(&["status", "section", "keys"])?;
            if disk.value.get_path("section").and_then(Value::as_str) != Some("audio") {
                return Err(BackendError::Upstream(502));
            }
            for (key, value) in &disk_values {
                if disk
                    .value
                    .get_path(&format!("keys.{key}"))
                    .and_then(Value::as_str)
                    != Some(value)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Audio may have changed, but apply, save or readback was incomplete. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

fn validate_audio_field(field: &Value, min: i32, max: i32) -> Result<(), BackendError> {
    let object = field.as_object().ok_or(BackendError::Upstream(502))?;
    let supported = field
        .get_path("supported")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let available = field
        .get_path("available")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    if object.len() != 5
        || audio_integer(field.get_path("min")) != Some(min)
        || audio_integer(field.get_path("max")) != Some(max)
        || (available
            && (!supported
                || audio_integer(field.get_path("value"))
                    .is_none_or(|n| !(min..=max).contains(&n))))
        || (!available && field.get_path("value") != Some(&Value::Null))
    {
        return Err(BackendError::Upstream(502));
    }
    Ok(())
}

#[cfg(test)]
pub(super) mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::thread;

    pub(crate) fn levels(value: i32) -> Vec<u8> {
        let fields = LEVELS.iter().map(|(name, _, _, min, max)| {
            let value = if *name == "mic_vol" { value } else { 20 };
            format!(r#""{name}":{{"supported":true,"available":true,"value":{value},"min":{min},"max":{max}}}"#)
        }).collect::<Vec<_>>().join(",");
        format!(r#"{{"status":"ok","ai_enabled":true,"ao_enabled":true,"fields":{{{fields}}}}}"#)
            .into_bytes()
    }

    pub(crate) fn input_state(enabled: bool, codec: &str, effects: bool) -> Vec<u8> {
        format!(r#"{{"status":"ok","readback":"owner","available":true,"enabled":{enabled},"muted":false,"codec":{},"sample_rate":{},"configured_codec":"{codec}","codecs_built":{{"pcmu":true,"pcma":true,"l16":true,"aac":false,"opus":false}},"effects_built":{effects},"processing":{{"readback":"owner","available":{},"ns":true,"level":1,"hpf":false,"agc":false,"target":10,"compression":0}}}}"#,
            if enabled { format!("\"{codec}\"") } else { "null".into() },
            if enabled { if codec == "l16" { "16000" } else { "8000" } } else { "null" }, enabled && effects).into_bytes()
    }

    fn live_levels(mic: bool, speaker: bool) -> Vec<u8> {
        let mut value = crate::json::parse(&levels(50)).unwrap();
        for (name, enabled) in [("ai_enabled", mic), ("ao_enabled", speaker)] {
            value.set_path(name, Value::Bool(enabled)).unwrap();
        }
        for (name, enabled) in [
            ("mic_vol", mic),
            ("mic_gain", mic),
            ("spk_vol", speaker),
            ("spk_gain", speaker),
        ] {
            if !enabled {
                value
                    .set_path(&format!("fields.{name}.available"), Value::Bool(false))
                    .unwrap();
                value
                    .set_path(&format!("fields.{name}.value"), Value::Null)
                    .unwrap();
            }
        }
        value.to_json().into_bytes()
    }

    #[test]
    fn live_audio_heartbeat_preserves_off_on_and_missing_state() {
        for state in [None, Some(false), Some(true)] {
            let root = task_temp("audio-heartbeat");
            let daemon = state.map(|enabled| {
                sequence(
                    &root,
                    vec![(
                        br#"{"cmd":"get-audio-observation-levels"}"#,
                        live_levels(enabled, enabled),
                    )],
                )
            });
            fs::write(root.join("uptime"), b"123.5 12.0\n").unwrap();
            let mut adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
            adapter.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
                uptime: root.join("uptime"),
                ..crate::camera::CameraPaths::default()
            });
            let reply = adapter
                .heartbeat(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let observed = crate::json::parse(&reply.body).unwrap();
            for name in ["mic_enabled", "spk_enabled"] {
                assert_eq!(
                    observed.get_path(name),
                    Some(&state.map_or(Value::Null, Value::Bool))
                );
            }
            if let Some(daemon) = daemon {
                daemon.join().unwrap();
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn live_audio_toggles_require_independent_readback_without_saving() {
        for (name, enable, command) in [
            ("mic_enabled", true, br#"{"cmd":"ai-enable"}"#.as_slice()),
            ("mic_enabled", false, br#"{"cmd":"ai-disable"}"#.as_slice()),
            ("spk_enabled", true, br#"{"cmd":"ao-enable"}"#.as_slice()),
            ("spk_enabled", false, br#"{"cmd":"ao-disable"}"#.as_slice()),
        ] {
            for agrees in [true, false] {
                let root = task_temp("live-audio-toggle");
                let after = if agrees { enable } else { !enable };
                let mic = name == "mic_enabled";
                let daemon = sequence(
                    &root,
                    vec![
                        (
                            br#"{"cmd":"get-audio-observation-levels"}"#,
                            live_levels(!enable, !enable),
                        ),
                        (
                            br#"{"cmd":"get-input-state"}"#,
                            input_state(!enable, "l16", false),
                        ),
                        (command, br#"{"status":"ok"}"#.to_vec()),
                        (
                            br#"{"cmd":"get-audio-observation-levels"}"#,
                            live_levels(
                                if mic { after } else { !enable },
                                if mic { !enable } else { after },
                            ),
                        ),
                        (
                            br#"{"cmd":"get-input-state"}"#,
                            input_state(if mic { after } else { !enable }, "l16", false),
                        ),
                    ],
                );
                let request = format!(r#"{{"audio":{{"{name}":{enable}}}}}"#);
                let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                    .live_control(request.as_bytes(), Instant::now() + Duration::from_secs(1));
                if agrees {
                    assert!(result.is_ok());
                } else {
                    assert!(
                        matches!(result, Err(BackendError::PartialApply(_))),
                        "{result:?}"
                    );
                }
                daemon.join().unwrap();
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn live_audio_rejects_unknown_owner_and_invalid_requests_before_mutation() {
        let root = task_temp("live-audio-unavailable");
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"audio":{}}"#.as_slice(),
            br#"{"audio":{"mic_enabled":1}}"#,
            br#"{"audio":{"mic_enabled":true,"spk_enabled":true}}"#,
            br#"{"audio":{"mic_enabled":true},"motion":{"enabled":true}}"#,
        ] {
            assert!(matches!(
                adapter.live_control(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        assert!(matches!(
            adapter.live_control(
                br#"{"audio":{"mic_enabled":true}}"#,
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::Unavailable)
        ));
        let unknown = String::from_utf8(input_state(false, "l16", false))
            .unwrap()
            .replace("\"available\":true", "\"available\":false")
            .replace("\"enabled\":false", "\"enabled\":null")
            .replace("\"muted\":false", "\"muted\":null");
        let daemon = sequence(
            &root,
            vec![
                (
                    br#"{"cmd":"get-audio-observation-levels"}"#,
                    live_levels(false, false),
                ),
                (br#"{"cmd":"get-input-state"}"#, unknown.into_bytes()),
            ],
        );
        assert!(matches!(
            adapter.live_control(
                br#"{"audio":{"mic_enabled":true}}"#,
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::Unavailable)
        ));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    pub(crate) fn alc(value: i32) -> Vec<u8> {
        format!(r#"{{"status":"ok","field":{{"supported":true,"available":true,"value":{value},"min":0,"max":7}}}}"#).into_bytes()
    }

    fn sequence(
        root: &std::path::Path,
        pairs: Vec<(&'static [u8], Vec<u8>)>,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join("rad.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for (expected, reply) in pairs {
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing expected RAD request: {other:?}"),
                    }
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                assert_eq!(read_request(&mut socket), expected);
                socket.write_all(&framed(&reply)).unwrap();
            }
        })
    }

    #[test]
    fn audio_requires_live_apply_save_and_complete_disk_readback() {
        for fault in [
            "none", "setter", "live", "save", "empty", "wrong", "missing",
        ] {
            let root = task_temp("audio-save");
            let ok = br#"{"status":"ok"}"#.to_vec();
            let error = br#"{"status":"error"}"#.to_vec();
            let mut pairs: Vec<(&'static [u8], Vec<u8>)> = vec![
                (br#"{"cmd":"get-audio-levels"}"#, levels(20)),
                (
                    br#"{"cmd":"status"}"#,
                    br#"{"status":"ok","muted":false}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"set-volume","value":30}"#,
                    if fault == "setter" {
                        error.clone()
                    } else {
                        ok.clone()
                    },
                ),
            ];
            if fault != "setter" {
                pairs.push((
                    br#"{"cmd":"get-audio-levels"}"#,
                    levels(if fault == "live" { 20 } else { 30 }),
                ));
                if fault != "live" {
                    pairs.push((
                        br#"{"cmd":"config-save"}"#,
                        if fault == "save" {
                            error.clone()
                        } else {
                            ok.clone()
                        },
                    ));
                    if fault != "save" {
                        let disk = match fault {
                            "empty" => br#"{"status":"ok","section":"audio","keys":{}}"#.to_vec(),
                            "wrong" => {
                                br#"{"status":"ok","section":"audio","keys":{"volume":"20"}}"#
                                    .to_vec()
                            }
                            "missing" => br#"{"status":"ok","section":"audio"}"#.to_vec(),
                            _ => br#"{"status":"ok","section":"audio","keys":{"volume":"30"}}"#
                                .to_vec(),
                        };
                        pairs.push((br#"{"cmd":"config-read-section","section":"audio"}"#, disk));
                    }
                }
            }
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_audio(
                br#"{"audio":{"mic_vol":30}}"#,
                Instant::now() + Duration::from_secs(2),
            );
            if fault == "none" {
                assert_eq!(
                    crate::json::parse(&result.unwrap().body)
                        .unwrap()
                        .get_path("persistent"),
                    Some(&Value::Bool(true))
                );
            } else {
                assert!(
                    matches!(result, Err(BackendError::PartialApply(_))),
                    "{fault}"
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn speaker_enable_requires_live_save_and_disk_readback() {
        for fault in ["none", "apply", "live", "save", "disk"] {
            let root = task_temp("speaker-enable-save");
            let ok = br#"{"status":"ok"}"#.to_vec();
            let error = br#"{"status":"error"}"#.to_vec();
            let mut pairs = vec![
                (
                    br#"{"cmd":"get-audio-observation-levels"}"#.as_slice(),
                    live_levels(true, false),
                ),
                (
                    br#"{"cmd":"get-input-state"}"#.as_slice(),
                    input_state(true, "l16", false),
                ),
                (br#"{"cmd":"get-alc-gain"}"#.as_slice(), alc(2)),
                (
                    br#"{"cmd":"ao-enable"}"#.as_slice(),
                    if fault == "apply" {
                        error.clone()
                    } else {
                        ok.clone()
                    },
                ),
            ];
            if fault != "apply" {
                pairs.extend([
                    (
                        br#"{"cmd":"get-audio-observation-levels"}"#.as_slice(),
                        live_levels(true, fault != "live"),
                    ),
                    (
                        br#"{"cmd":"get-input-state"}"#.as_slice(),
                        input_state(true, "l16", false),
                    ),
                    (br#"{"cmd":"get-alc-gain"}"#.as_slice(), alc(2)),
                ]);
                if fault != "live" {
                    pairs.push((
                        br#"{"cmd":"config-save"}"#.as_slice(),
                        if fault == "save" { error } else { ok },
                    ));
                    if fault != "save" {
                        pairs.push((br#"{"cmd":"config-read-section","section":"audio"}"#.as_slice(),
                            format!(r#"{{"status":"ok","section":"audio","keys":{{"ao_enabled":"{}"}}}}"#, fault != "disk").into_bytes()));
                    }
                }
            }
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_audio(
                br#"{"audio":{"spk_enabled":true}}"#,
                Instant::now() + Duration::from_secs(2),
            );
            if fault == "none" {
                assert!(result.is_ok(), "{result:?}");
            } else {
                assert!(
                    matches!(result, Err(BackendError::PartialApply(_))),
                    "{fault}: {result:?}"
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn speaker_levels_do_not_depend_on_microphone_mute() {
        let root = task_temp("speaker-level-muted-input");
        let observation = |volume: i32| {
            let mut levels = crate::json::parse(&live_levels(true, true)).unwrap();
            levels
                .set_path("fields.spk_vol.value", Value::Number(volume.to_string()))
                .unwrap();
            let mut input = crate::json::parse(&input_state(true, "l16", false)).unwrap();
            input.set_path("muted", Value::Bool(true)).unwrap();
            vec![
                (
                    br#"{"cmd":"get-audio-observation-levels"}"#.as_slice(),
                    levels.to_json().into_bytes(),
                ),
                (
                    br#"{"cmd":"get-input-state"}"#.as_slice(),
                    input.to_json().into_bytes(),
                ),
                (br#"{"cmd":"get-alc-gain"}"#.as_slice(), alc(2)),
            ]
        };
        let mut pairs = observation(20);
        pairs.push((br#"{"cmd":"ao-enable"}"#, br#"{"status":"ok"}"#.to_vec()));
        pairs.push((
            br#"{"cmd":"ao-set-volume","value":30}"#,
            br#"{"status":"ok"}"#.to_vec(),
        ));
        pairs.extend(observation(30));
        pairs.push((br#"{"cmd":"config-save"}"#, br#"{"status":"ok"}"#.to_vec()));
        pairs.push((
            br#"{"cmd":"config-read-section","section":"audio"}"#,
            br#"{"status":"ok","section":"audio","keys":{"ao_enabled":"true","ao_volume":"30"}}"#
                .to_vec(),
        ));
        let daemon = sequence(&root, pairs);
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_audio(
            br#"{"audio":{"spk_enabled":true,"spk_vol":30}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn audio_and_privacy_mutations_fail_promptly_when_another_apply_is_active() {
        let root = task_temp("audio-busy");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        let _guard = backend.mutation_lock.lock().unwrap();
        let deadline = Instant::now() + Duration::from_secs(1);
        assert!(matches!(
            backend.update_audio(br#"{"audio":{"mic_vol":30}}"#, deadline),
            Err(BackendError::Busy)
        ));
        assert!(matches!(
            backend.live_control(br#"{"privacy":{"enabled":true}}"#, deadline),
            Err(BackendError::Busy)
        ));
        fs::remove_dir_all(root).unwrap();
    }

    fn settings_pairs(value: i32, codec: &str, changed: bool) -> Vec<(&'static [u8], Vec<u8>)> {
        let mut input = crate::json::parse(&input_state(true, codec, true)).unwrap();
        if changed {
            for (key, value) in [
                ("level", Value::Number("3".into())),
                ("hpf", Value::Bool(true)),
                ("agc", Value::Bool(true)),
                ("target", Value::Number("12".into())),
                ("compression", Value::Number("20".into())),
            ] {
                input.set_path(&format!("processing.{key}"), value).unwrap();
            }
        }
        vec![
            (br#"{"cmd":"get-audio-observation-levels"}"#, levels(20)),
            (
                br#"{"cmd":"get-input-state"}"#,
                input.to_json().into_bytes(),
            ),
            (br#"{"cmd":"get-alc-gain"}"#, alc(value)),
        ]
    }

    #[test]
    fn input_codec_processing_and_alc_require_live_save_and_disk_agreement() {
        for fault in ["none", "apply", "live", "save", "disk"] {
            let root = task_temp("audio-settings");
            let ok = br#"{"status":"ok"}"#.to_vec();
            let error = br#"{"status":"error"}"#.to_vec();
            let mut pairs = settings_pairs(2, "l16", false);
            pairs.push((br#"{"cmd":"ai-enable"}"#, ok.clone()));
            pairs.push((br#"{"cmd":"set-codec","value":"pcma"}"#, ok.clone()));
            pairs.push((br#"{"cmd":"set-input-processing","ns":1,"level":3,"hpf":1,"agc":1,"target":12,"compression":20}"#, if fault == "apply" { error.clone() } else { ok.clone() }));
            if fault != "apply" {
                pairs.push((br#"{"cmd":"set-alc-gain","value":5}"#, ok.clone()));
                pairs.extend(settings_pairs(
                    if fault == "live" { 2 } else { 5 },
                    "pcma",
                    true,
                ));
                if fault != "live" {
                    pairs.push((
                        br#"{"cmd":"config-save"}"#,
                        if fault == "save" {
                            error.clone()
                        } else {
                            ok.clone()
                        },
                    ));
                    if fault != "save" {
                        let disk = format!(
                            r#"{{"status":"ok","section":"audio","keys":{{"alc_gain":"{}","ai_enabled":"true","codec":"pcma","sample_rate":"8000","ns_enabled":"true","ns_level":"3","hpf_enabled":"true","agc_enabled":"true","agc_target_dbfs":"12","agc_compression_db":"20"}}}}"#,
                            if fault == "disk" { 2 } else { 5 }
                        );
                        pairs.push((
                            br#"{"cmd":"config-read-section","section":"audio"}"#,
                            disk.into_bytes(),
                        ));
                    }
                }
            }
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_audio(
                br#"{"audio":{"mic_enabled":true,"mic_format":"G711A","mic_alc_gain":5,"mic_noise_suppression":4,"mic_high_pass_filter":true,"mic_agc_enabled":true,"mic_agc_target_level_dbfs":12,"mic_agc_compression_gain_db":20}}"#,
                Instant::now() + Duration::from_secs(2));
            if fault == "none" {
                assert!(result.is_ok(), "{result:?}");
            } else {
                assert!(
                    matches!(result, Err(BackendError::PartialApply(_))),
                    "{fault}: {result:?}"
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn processing_partial_update_checks_unchanged_parameters_and_mute() {
        for fault in ["target", "mute"] {
            let root = task_temp("processing-preserve");
            let mut pairs = settings_pairs(2, "l16", false);
            pairs.push((br#"{"cmd":"set-input-processing","ns":1,"level":1,"hpf":0,"agc":1,"target":10,"compression":0}"#, br#"{"status":"ok"}"#.to_vec()));
            let mut live = settings_pairs(2, "l16", false);
            let mut input = crate::json::parse(&live[1].1).unwrap();
            input.set_path("processing.agc", Value::Bool(true)).unwrap();
            if fault == "target" {
                input
                    .set_path("processing.target", Value::Number("9".into()))
                    .unwrap();
            } else {
                input.set_path("muted", Value::Bool(true)).unwrap();
            }
            live[1].1 = input.to_json().into_bytes();
            pairs.extend(live);
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_audio(
                br#"{"audio":{"mic_agc_enabled":true}}"#,
                Instant::now() + Duration::from_secs(2),
            );
            assert!(
                matches!(result, Err(BackendError::PartialApply(_))),
                "{result:?}"
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn settings_preflight_rejects_unbuilt_muted_and_conflicting_changes() {
        for (case, body) in [
            ("codec", br#"{"audio":{"mic_format":"AAC"}}"#.as_slice()),
            ("effects", br#"{"audio":{"mic_agc_enabled":true}}"#),
            ("mute", br#"{"audio":{"mic_alc_gain":5}}"#),
            (
                "disable",
                br#"{"audio":{"mic_enabled":false,"mic_alc_gain":5}}"#,
            ),
        ] {
            let root = task_temp("audio-preflight");
            let mut pairs = settings_pairs(2, "l16", false);
            let mut input = crate::json::parse(&pairs[1].1).unwrap();
            if case == "effects" {
                input.set_path("effects_built", Value::Bool(false)).unwrap();
                input
                    .set_path("processing.available", Value::Bool(false))
                    .unwrap();
            }
            if case == "mute" {
                input.set_path("muted", Value::Bool(true)).unwrap();
            }
            pairs[1].1 = input.to_json().into_bytes();
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .update_audio(body, Instant::now() + Duration::from_secs(2));
            if case == "disable" {
                assert!(matches!(result, Err(BackendError::Protocol)));
            } else {
                assert!(
                    matches!(result, Err(BackendError::Unsupported(_))),
                    "{case}: {result:?}"
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn audio_get_returns_plain_domain_and_rejects_incomplete_status() {
        for missing in [false, true] {
            let root = task_temp("audio-get");
            let reply = if missing {
                br#"{"status":"ok","ai_enabled":true,"ao_enabled":true,"fields":{}}"#.to_vec()
            } else {
                levels(20)
            };
            let mut pairs: Vec<(&'static [u8], Vec<u8>)> =
                vec![(br#"{"cmd":"get-audio-observation-levels"}"#, reply)];
            if !missing {
                pairs.push((
                    br#"{"cmd":"get-input-state"}"#,
                    input_state(true, "l16", false),
                ));
                pairs.push((br#"{"cmd":"get-alc-gain"}"#, alc(2)));
            }
            let daemon = sequence(&root, pairs);
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .audio_config(Instant::now() + Duration::from_secs(2));
            if missing {
                assert!(matches!(result, Err(BackendError::Upstream(502))));
            } else {
                let value = crate::json::parse(&result.unwrap().body).unwrap();
                assert_eq!(
                    value.get_path("source").and_then(Value::as_str),
                    Some("raptor")
                );
                assert_eq!(audio_integer(value.get_path("mic_vol")), Some(20));
                assert_eq!(value.get_path("mic_is_digital"), Some(&Value::Bool(false)));
                assert_eq!(value.get_path("force_stereo"), Some(&Value::Bool(false)));
                for name in [
                    "buffer_warn_frames",
                    "buffer_cap_frames",
                    "tap_enabled",
                    "tap_path",
                ] {
                    assert_eq!(value.get_path(name), Some(&Value::Null), "{name}");
                }
                assert_eq!(
                    value.get_path("mic_input_basis").and_then(Value::as_str),
                    Some("dlink-a1-profile-amic")
                );
                assert_eq!(
                    value.get_path("channel_basis").and_then(Value::as_str),
                    Some("rad-fixed-mono")
                );
                assert!(value.get_path("message").is_none());
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn audio_validates_entire_request_before_applying_and_preserves_mute() {
        let root = task_temp("audio-validation");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"audio":{"mic_vol":121}}"#.as_slice(),
            br#"{"audio":{"mic_gain":-1}}"#,
            br#"{"audio":{"mic_vol":30,"spk_gain":"bad"}}"#,
            br#"{"audio":{}}"#,
        ] {
            assert!(matches!(
                backend.update_audio(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        let daemon = sequence(
            &root,
            vec![
                (br#"{"cmd":"get-audio-levels"}"#, levels(20)),
                (
                    br#"{"cmd":"status"}"#,
                    br#"{"status":"ok","muted":true}"#.to_vec(),
                ),
            ],
        );
        assert!(matches!(
            backend.update_audio(
                br#"{"audio":{"mic_vol":30}}"#,
                Instant::now() + Duration::from_secs(2)
            ),
            Err(BackendError::Unsupported(_))
        ));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
