use super::*;

const IMAGING_FIELDS: [(&str, &str, u64); 15] = [
    ("brightness", "brightness", 255),
    ("contrast", "contrast", 255),
    ("saturation", "saturation", 255),
    ("sharpness", "sharpness", 255),
    ("backlight", "backlight_comp", 10),
    ("wide_dynamic_range", "drc_strength", 255),
    ("tone", "highlight_depress", 255),
    ("defog", "defog_strength", 255),
    ("noise_reduction", "sinter", 255),
    ("hue", "hue", 255),
    ("dpc_strength", "dpc_strength", 255),
    ("exposure_compensation", "ae_comp", 255),
    ("hflip", "hflip", 1),
    ("vflip", "vflip", 1),
    ("anti_flicker", "antiflicker", 2),
];

const IMAGING_DISK_READ_BUDGET: Duration = Duration::from_millis(150);

fn imaging_field(name: &str) -> Option<(&'static str, u64)> {
    IMAGING_FIELDS
        .iter()
        .find(|(public, _, _)| *public == name)
        .map(|(_, config, maximum)| (*config, *maximum))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WhiteBalanceConfig {
    mode: u64,
    rgain: u64,
    bgain: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WhiteBalanceObservation {
    supported: bool,
    available: bool,
    live: Option<WhiteBalanceConfig>,
    configured: Option<WhiteBalanceConfig>,
}

fn bounded_u64(value: Option<&Value>, maximum: u64) -> Result<Option<u64>, BackendError> {
    match value {
        Some(Value::Null) => Ok(None),
        Some(value) => value_u64(value)
            .ok()
            .filter(|value| *value <= maximum)
            .map(Some)
            .ok_or(BackendError::Upstream(502)),
        None => Err(BackendError::Upstream(502)),
    }
}

fn native_white_balance(reply: &RaptorReply) -> Result<WhiteBalanceObservation, BackendError> {
    let Some(value) = reply.value.get_path("white_balance") else {
        return Ok(WhiteBalanceObservation {
            supported: false,
            available: false,
            live: None,
            configured: None,
        });
    };
    let fields = value.as_object().ok_or(BackendError::Upstream(502))?;
    let expected = [
        "supported",
        "available",
        "verification",
        "mode_min",
        "mode_max",
        "gain_min",
        "gain_max",
        "mode",
        "gains_effective",
        "rgain",
        "bgain",
        "configured_mode",
        "configured_rgain",
        "configured_bgain",
    ];
    if fields.len() != expected.len()
        || fields.keys().any(|name| !expected.contains(&name.as_str()))
        || value.get_path("mode_min").and_then(|v| value_u64(v).ok()) != Some(0)
        || value.get_path("mode_max").and_then(|v| value_u64(v).ok()) != Some(9)
        || value.get_path("gain_min").and_then(|v| value_u64(v).ok()) != Some(0)
        || value.get_path("gain_max").and_then(|v| value_u64(v).ok()) != Some(1024)
    {
        return Err(BackendError::Upstream(502));
    }
    let supported = value
        .get_path("supported")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let available = value
        .get_path("available")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let verification = value
        .get_path("verification")
        .and_then(Value::as_str)
        .ok_or(BackendError::Upstream(502))?;
    let gains_effective = value
        .get_path("gains_effective")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let mode = bounded_u64(value.get_path("mode"), 9)?;
    let rgain = bounded_u64(value.get_path("rgain"), 1024)?;
    let bgain = bounded_u64(value.get_path("bgain"), 1024)?;
    let configured_mode = bounded_u64(value.get_path("configured_mode"), 9)?;
    let configured_rgain = bounded_u64(value.get_path("configured_rgain"), 1024)?;
    let configured_bgain = bounded_u64(value.get_path("configured_bgain"), 1024)?;
    let configured = match (configured_mode, configured_rgain, configured_bgain) {
        (Some(mode), Some(rgain), Some(bgain)) => Some(WhiteBalanceConfig { mode, rgain, bgain }),
        (None, None, None) => None,
        _ => return Err(BackendError::Upstream(502)),
    };
    let live = if available {
        let mode = mode.ok_or(BackendError::Upstream(502))?;
        if gains_effective != (mode == 1)
            || (mode == 1 && (rgain.is_none() || bgain.is_none()))
            || (mode != 1 && (rgain.is_some() || bgain.is_some()))
        {
            return Err(BackendError::Upstream(502));
        }
        Some(WhiteBalanceConfig {
            mode,
            rgain: rgain.or(configured.map(|value| value.rgain)).unwrap_or(0),
            bgain: bgain.or(configured.map(|value| value.bgain)).unwrap_or(0),
        })
    } else {
        if mode.is_some() || rgain.is_some() || bgain.is_some() || gains_effective {
            return Err(BackendError::Upstream(502));
        }
        None
    };
    if (supported && verification != "sdk-readback")
        || (!supported && (verification != "unsupported" || available))
    {
        return Err(BackendError::Upstream(502));
    }
    Ok(WhiteBalanceObservation {
        supported,
        available,
        live,
        configured,
    })
}

fn requested_white_balance(value: &Value) -> Result<WhiteBalanceConfig, BackendError> {
    let fields = value
        .as_object()
        .filter(|fields| {
            fields.len() == 3
                && fields
                    .keys()
                    .all(|name| matches!(name.as_str(), "mode" | "rgain" | "bgain"))
        })
        .ok_or(BackendError::Protocol)?;
    let read = |name: &str, maximum: u64| {
        let value = fields
            .get(name)
            .ok_or(BackendError::Protocol)
            .and_then(value_u64)?;
        if value > maximum {
            return Err(BackendError::Protocol);
        }
        Ok(value)
    };
    Ok(WhiteBalanceConfig {
        mode: read("mode", 9)?,
        rgain: read("rgain", 1024)?,
        bgain: read("bgain", 1024)?,
    })
}

fn disk_white_balance(
    reply: &RaptorReply,
    require_present: bool,
) -> Result<WhiteBalanceConfig, BackendError> {
    require_ok(reply)?;
    reply.require_only_fields(&["status", "section", "keys"])?;
    if reply.value.get_path("section").and_then(Value::as_str) != Some("image")
        || reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .is_none()
    {
        return Err(BackendError::Upstream(502));
    }
    let read = |name: &str, fallback: u64, maximum: u64| {
        let Some(value) = reply.value.get_path(&format!("keys.{name}")) else {
            return if require_present {
                Err(BackendError::Upstream(502))
            } else {
                Ok(fallback)
            };
        };
        value
            .as_str()
            .filter(|value| !value.is_empty() && value.len() <= 4)
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value <= maximum)
            .ok_or(BackendError::Upstream(502))
    };
    Ok(WhiteBalanceConfig {
        mode: read("core_wb_mode", 0, 9)?,
        rgain: read("wb_rgain", 128, 1024)?,
        bgain: read("wb_bgain", 128, 1024)?,
    })
}

fn response_white_balance(value: &Value) -> Result<WhiteBalanceConfig, BackendError> {
    if value
        .get_path("message.white_balance.available")
        .and_then(Value::as_bool)
        != Some(true)
    {
        return Err(BackendError::Upstream(502));
    }
    let read = |name: &str, maximum: u64| {
        value
            .get_path(&format!("message.white_balance.{name}"))
            .ok_or(BackendError::Upstream(502))
            .and_then(value_u64)
            .map_err(|_| BackendError::Upstream(502))
            .and_then(|value| {
                if value <= maximum {
                    Ok(value)
                } else {
                    Err(BackendError::Upstream(502))
                }
            })
    };
    let result = WhiteBalanceConfig {
        mode: read("mode", 9)?,
        rgain: read("configured_rgain", 1024)?,
        bgain: read("configured_bgain", 1024)?,
    };
    if read("configured_mode", 9)? != result.mode
        || (result.mode == 1
            && (read("rgain", 1024)? != result.rgain || read("bgain", 1024)? != result.bgain))
    {
        return Err(BackendError::Upstream(502));
    }
    Ok(result)
}

fn white_balance_matches(observed: WhiteBalanceConfig, expected: WhiteBalanceConfig) -> bool {
    observed == expected
}

impl RaptorBackend {
    fn imaging_disk_antiflicker(&self, deadline: Instant) -> Result<u64, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"sensor"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str) != Some("sensor") {
            return Err(BackendError::Upstream(502));
        }
        reply
            .value
            .get_path("keys.antiflicker")
            .and_then(Value::as_str)
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value <= 2)
            .ok_or(BackendError::Upstream(502))
    }

    fn imaging_disk_white_balance(
        &self,
        deadline: Instant,
    ) -> Result<WhiteBalanceConfig, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"image"}"#,
            deadline,
        )?;
        disk_white_balance(&reply, false)
    }

    pub(super) fn color_state(&self, deadline: Instant) -> Result<bool, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"get-running-mode"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        match reply.value.get_path("mode").and_then(Value::as_str) {
            Some("day") => Ok(true),
            Some("night") => Ok(false),
            _ => Err(BackendError::Upstream(502)),
        }
    }

    pub(super) fn set_color(
        &self,
        enabled: bool,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let command = format!(
            "{{\"cmd\":\"set-running-mode\",\"value\":\"{}\"}}",
            if enabled { "day" } else { "night" }
        );
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        if self.color_state(deadline)? != enabled {
            return Err(BackendError::Upstream(502));
        }
        Ok(BackendResponse::json(b"{\"status\":\"accepted\"}".to_vec()))
    }

    pub(super) fn ir_output_state(
        &self,
        deadline: Instant,
    ) -> Result<[(bool, Option<bool>); 2], BackendError> {
        let reply = self.command(
            RaptorDaemon::Ric,
            br#"{"cmd":"get-output-state"}"#,
            deadline,
        )?;
        validate_ir_outputs(&reply)
    }

    // The live-control dispatcher holds the shared mutation lock.
    pub(super) fn set_ir_output(
        &self,
        output: &str,
        enabled: bool,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let index = match output {
            "ircut" => 0,
            "ir850" => 1,
            _ => return Err(BackendError::Protocol),
        };
        let before = self.ir_output_state(deadline)?;
        if !before[index].0 {
            return Err(BackendError::Unsupported("This IR output is not supported"));
        }
        if before[index].1.is_none() {
            return Err(BackendError::Unavailable);
        }
        let apply = || -> Result<(), BackendError> {
            let command =
                format!(r#"{{"cmd":"set-output","output":"{output}","enabled":{enabled}}}"#);
            let reply = self.command(RaptorDaemon::Ric, command.as_bytes(), deadline)?;
            let applied = validate_ir_outputs(&reply)?;
            let observed = self.ir_output_state(deadline)?;
            if applied[index] != (true, Some(enabled)) || observed[index] != (true, Some(enabled)) {
                return Err(BackendError::Upstream(502));
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply(
            "IR output transition or independent readback failed. Reload before explicitly retrying."))?;
        Ok(BackendResponse::json(br#"{"status":"accepted"}"#.to_vec()))
    }

    pub(super) fn imaging(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        self.imaging_state(deadline, true, None)
    }

    fn imaging_state(
        &self,
        deadline: Instant,
        include_saved: bool,
        saved_override: Option<WhiteBalanceConfig>,
    ) -> Result<BackendResponse, BackendError> {
        let reply = self.command(RaptorDaemon::Rvd, br#"{"cmd":"get-imaging"}"#, deadline)?;
        require_ok(&reply)?;
        let white_balance = native_white_balance(&reply)?;
        let fields = reply
            .value
            .get_path("fields")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        let anti_supported = fields
            .get("anti_flicker")
            .and_then(|field| field.get_path("supported"))
            .and_then(Value::as_bool)
            == Some(true);
        let saved_antiflicker = if include_saved && anti_supported {
            self.imaging_disk_antiflicker(deadline.min(Instant::now() + IMAGING_DISK_READ_BUDGET))
                .ok()
        } else {
            None
        };
        let mut observed = std::collections::BTreeMap::new();
        for (index, (name, _, maximum)) in IMAGING_FIELDS.into_iter().enumerate() {
            let Some(field) = fields.get(name) else {
                if index < 4 {
                    return Err(BackendError::Upstream(502));
                }
                let mut entry = std::collections::BTreeMap::new();
                entry.insert("supported".into(), Value::Bool(false));
                entry.insert("available".into(), Value::Bool(false));
                entry.insert("value".into(), Value::Null);
                entry.insert("min".into(), Value::Number("0".into()));
                entry.insert("max".into(), Value::Number(maximum.to_string()));
                entry.insert("verification".into(), Value::String("unsupported".into()));
                entry.insert("observed_value".into(), Value::Null);
                entry.insert("configured_value".into(), Value::Null);
                observed.insert(name.into(), Value::Object(entry));
                continue;
            };
            let field_object = field.as_object().ok_or(BackendError::Upstream(502))?;
            let has_verification = field_object.contains_key("verification");
            if (field_object.len() != 5 && field_object.len() != 8)
                || field_object.keys().any(|key| {
                    ![
                        "supported",
                        "available",
                        "value",
                        "min",
                        "max",
                        "verification",
                        "observed_value",
                        "configured_value",
                    ]
                    .contains(&key.as_str())
                })
                || has_verification != (field_object.len() == 8)
                || field
                    .get_path("min")
                    .and_then(|value| value_u64(value).ok())
                    != Some(0)
                || field
                    .get_path("max")
                    .and_then(|value| value_u64(value).ok())
                    != Some(maximum)
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
            let verification = if has_verification {
                field
                    .get_path("verification")
                    .and_then(Value::as_str)
                    .ok_or(BackendError::Upstream(502))?
            } else if supported {
                "sdk-readback"
            } else {
                "unsupported"
            };
            let nullable_value = |name: &str| -> Result<Option<u64>, BackendError> {
                match field.get_path(name) {
                    None if !has_verification => Ok(None),
                    Some(Value::Null) => Ok(None),
                    Some(value) => value_u64(value)
                        .ok()
                        .filter(|value| *value <= maximum)
                        .map(Some)
                        .ok_or(BackendError::Upstream(502)),
                    None => Err(BackendError::Upstream(502)),
                }
            };
            let observed_value = if has_verification {
                nullable_value("observed_value")?
            } else if available {
                nullable_value("value")?
            } else {
                None
            };
            let configured_value = nullable_value("configured_value")?;
            if available
                && (!supported
                    || field
                        .get_path("value")
                        .and_then(|value| value_u64(value).ok())
                        .is_none_or(|value| value > maximum))
            {
                return Err(BackendError::Upstream(502));
            }
            let numeric_value = if available {
                nullable_value("value")?
            } else {
                None
            };
            if !matches!(
                verification,
                "unsupported" | "sdk-readback" | "sdk-setter-config"
            ) || (has_verification
                && !available
                && field.get_path("value") != Some(&Value::Null))
                || (verification == "unsupported" && (supported || available))
                || (verification == "sdk-readback"
                    && (!supported || (available && observed_value != numeric_value)))
                || (verification == "sdk-setter-config"
                    && (!supported
                        || observed_value.is_some()
                        || (available && configured_value != numeric_value)))
            {
                return Err(BackendError::Upstream(502));
            }
            let value = if available {
                field.get_path("value").unwrap().clone()
            } else {
                Value::Null
            };
            let mut entry = std::collections::BTreeMap::new();
            entry.insert("supported".into(), Value::Bool(supported));
            entry.insert("available".into(), Value::Bool(available));
            entry.insert("value".into(), value);
            entry.insert("min".into(), Value::Number("0".into()));
            entry.insert("max".into(), Value::Number(maximum.to_string()));
            entry.insert("verification".into(), Value::String(verification.into()));
            entry.insert(
                "observed_value".into(),
                observed_value.map_or(Value::Null, |value| Value::Number(value.to_string())),
            );
            entry.insert(
                "configured_value".into(),
                configured_value.map_or(Value::Null, |value| Value::Number(value.to_string())),
            );
            if name == "anti_flicker" {
                entry.insert(
                    "saved_value".into(),
                    saved_antiflicker.map_or(Value::Null, |value| Value::Number(value.to_string())),
                );
                entry.insert(
                    "matches_saved".into(),
                    Value::Bool(
                        available
                            && configured_value == saved_antiflicker
                            && numeric_value == saved_antiflicker,
                    ),
                );
            }
            observed.insert(name.into(), Value::Object(entry));
        }
        let persistent =
            reply.value.get_path("persistence").and_then(Value::as_str) == Some("checked-config");
        if white_balance.supported && !persistent {
            return Err(BackendError::Upstream(502));
        }
        let saved = if include_saved && white_balance.supported {
            match saved_override {
                Some(saved) => Some(saved),
                None => self
                    .imaging_disk_white_balance(
                        deadline.min(Instant::now() + IMAGING_DISK_READ_BUDGET),
                    )
                    .ok(),
            }
        } else {
            None
        };
        let mut wb = std::collections::BTreeMap::new();
        wb.insert("supported".into(), Value::Bool(white_balance.supported));
        wb.insert("available".into(), Value::Bool(white_balance.available));
        wb.insert(
            "verification".into(),
            Value::String(if white_balance.supported {
                "sdk-readback".into()
            } else {
                "unsupported".into()
            }),
        );
        wb.insert("gain_min".into(), Value::Number("0".into()));
        wb.insert("gain_max".into(), Value::Number("1024".into()));
        wb.insert(
            "modes".into(),
            Value::Array(
                (0..=9)
                    .map(|value| Value::Number(value.to_string()))
                    .collect(),
            ),
        );
        let mode = white_balance.live.map(|value| value.mode);
        let effective = mode == Some(1);
        wb.insert(
            "mode".into(),
            mode.map_or(Value::Null, |value| Value::Number(value.to_string())),
        );
        wb.insert("gains_effective".into(), Value::Bool(effective));
        wb.insert(
            "rgain".into(),
            if effective {
                Value::Number(white_balance.live.unwrap().rgain.to_string())
            } else {
                Value::Null
            },
        );
        wb.insert(
            "bgain".into(),
            if effective {
                Value::Number(white_balance.live.unwrap().bgain.to_string())
            } else {
                Value::Null
            },
        );
        for (prefix, value) in [("configured", white_balance.configured), ("saved", saved)] {
            wb.insert(
                format!("{prefix}_mode"),
                value.map_or(Value::Null, |value| Value::Number(value.mode.to_string())),
            );
            wb.insert(
                format!("{prefix}_rgain"),
                value.map_or(Value::Null, |value| Value::Number(value.rgain.to_string())),
            );
            wb.insert(
                format!("{prefix}_bgain"),
                value.map_or(Value::Null, |value| Value::Number(value.bgain.to_string())),
            );
        }
        let matches_saved = white_balance.available
            && white_balance.configured == saved
            && white_balance.live.is_some_and(|live| {
                saved.is_some_and(|saved| {
                    live.mode == saved.mode
                        && (live.mode != 1
                            || (live.rgain == saved.rgain && live.bgain == saved.bgain))
                })
            });
        wb.insert("matches_saved".into(), Value::Bool(matches_saved));
        let mut message = std::collections::BTreeMap::new();
        message.insert("fields".into(), Value::Object(observed));
        message.insert("white_balance".into(), Value::Object(wb));
        Ok(BackendResponse::json(format!(
            "{{\"code\":200,\"result\":\"success\",\"source\":\"raptor\",\"persistent\":{persistent},\"message\":{}}}",
            Value::Object(message).to_json()
        ).into_bytes()))
    }

    pub(super) fn update_imaging(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let _mutation = self.lock_mutation()?;
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.is_empty() || fields.len() > IMAGING_FIELDS.len() + 1 {
            return Err(BackendError::Protocol);
        }
        let white_balance = fields
            .get("white_balance")
            .map(requested_white_balance)
            .transpose()?;
        for (name, value) in fields {
            if name == "white_balance" {
                continue;
            }
            let Some((_, maximum)) = imaging_field(name) else {
                return Err(BackendError::Protocol);
            };
            if value_u64(value)? > maximum {
                return Err(BackendError::Protocol);
            }
        }
        let before = self.imaging_state(deadline, false, None)?;
        let before = crate::json::parse(&before.body).map_err(|_| BackendError::Upstream(502))?;
        let persistent = before.get_path("persistent").and_then(Value::as_bool) == Some(true);
        for name in fields.keys() {
            if name == "white_balance" {
                continue;
            }
            if before
                .get_path(&format!("message.fields.{name}.available"))
                .and_then(Value::as_bool)
                != Some(true)
            {
                return Err(BackendError::Unsupported(
                    "An imaging control is unavailable; reload before saving",
                ));
            }
        }
        if let Some(white_balance) = white_balance
            && (!persistent
                || before
                    .get_path("message.white_balance.available")
                    .and_then(Value::as_bool)
                    != Some(true)
                || before
                    .get_path("message.white_balance.configured_rgain")
                    .and_then(|value| value_u64(value).ok())
                    .is_none_or(|value| white_balance.mode != 1 && value != white_balance.rgain)
                || before
                    .get_path("message.white_balance.configured_bgain")
                    .and_then(|value| value_u64(value).ok())
                    .is_none_or(|value| white_balance.mode != 1 && value != white_balance.bgain))
        {
            return Err(BackendError::Unsupported(
                "White balance is unavailable or automatic gains changed; reload before saving",
            ));
        }
        let apply = || -> Result<BackendResponse, BackendError> {
            if persistent {
                let command = format!(
                    "{{\"cmd\":\"set-imaging\",\"values\":{}}}",
                    request.to_json()
                );
                require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
            } else {
                for (name, value) in fields {
                    if name == "white_balance" {
                        return Err(BackendError::Unsupported(
                            "Grouped white balance requires checked persistence",
                        ));
                    }
                    let command =
                        format!("{{\"cmd\":\"set-{name}\",\"value\":{}}}", value.to_json());
                    require_ok(&self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?)?;
                }
            }
            let response = self.imaging_state(deadline, false, None)?;
            let readback =
                crate::json::parse(&response.body).map_err(|_| BackendError::Upstream(502))?;
            for (name, value) in fields {
                if name == "white_balance" {
                    continue;
                }
                if readback
                    .get_path(&format!("message.fields.{name}.available"))
                    .and_then(Value::as_bool)
                    != Some(true)
                    || readback.get_path(&format!("message.fields.{name}.value")) != Some(value)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(expected) = white_balance {
                let observed = response_white_balance(&readback)?;
                if !white_balance_matches(observed, expected) {
                    return Err(BackendError::Upstream(502));
                }
            }
            if !persistent {
                return Ok(response);
            }
            if readback.get_path("persistent").and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let needs_image_disk = white_balance.is_some()
                || fields
                    .keys()
                    .any(|name| name != "white_balance" && name != "anti_flicker");
            let disk = if needs_image_disk {
                let disk = self.command(
                    RaptorDaemon::Rvd,
                    br#"{"cmd":"config-read-section","section":"image"}"#,
                    deadline,
                )?;
                require_ok(&disk)?;
                disk.require_only_fields(&["status", "section", "keys"])?;
                if disk.value.get_path("section").and_then(Value::as_str) != Some("image") {
                    return Err(BackendError::Upstream(502));
                }
                Some(disk)
            } else {
                None
            };
            for (name, value) in fields {
                if name == "white_balance" {
                    continue;
                }
                if name == "anti_flicker" {
                    if self.imaging_disk_antiflicker(deadline)? != value_u64(value)? {
                        return Err(BackendError::Upstream(502));
                    }
                    continue;
                }
                let (config_name, _) = imaging_field(name).ok_or(BackendError::Protocol)?;
                if disk
                    .as_ref()
                    .ok_or(BackendError::Protocol)?
                    .value
                    .get_path(&format!("keys.{config_name}"))
                    .and_then(Value::as_str)
                    != Some(value.to_json().as_str())
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            let saved_white_balance = white_balance
                .map(|expected| {
                    let saved =
                        disk_white_balance(disk.as_ref().ok_or(BackendError::Protocol)?, true)?;
                    if saved != expected {
                        return Err(BackendError::Upstream(502));
                    }
                    Ok(saved)
                })
                .transpose()?;
            if let Some(expected) = white_balance {
                let final_response = self.imaging_state(deadline, true, saved_white_balance)?;
                let final_value = crate::json::parse(&final_response.body)
                    .map_err(|_| BackendError::Upstream(502))?;
                if !white_balance_matches(response_white_balance(&final_value)?, expected)
                    || final_value
                        .get_path("message.white_balance.matches_saved")
                        .and_then(Value::as_bool)
                        != Some(true)
                {
                    return Err(BackendError::Upstream(502));
                }
                Ok(final_response)
            } else {
                if !fields.contains_key("anti_flicker") {
                    return Ok(response);
                }
                let final_response = self.imaging_state(deadline, true, None)?;
                let final_value = crate::json::parse(&final_response.body)
                    .map_err(|_| BackendError::Upstream(502))?;
                if final_value
                    .get_path("message.fields.anti_flicker.matches_saved")
                    .and_then(Value::as_bool)
                    != Some(true)
                {
                    return Err(BackendError::Upstream(502));
                }
                Ok(final_response)
            }
        };
        apply().map_err(|_| BackendError::PartialApply("Image settings may have changed, but apply, save or readback was incomplete. Reload and explicitly retry saving."))
    }
}

fn validate_ir_outputs(reply: &RaptorReply) -> Result<[(bool, Option<bool>); 2], BackendError> {
    require_ok(reply)?;
    reply.require_only_fields(&["status", "ircut", "ir850"])?;
    let mut outputs = [(false, None); 2];
    for (index, name) in ["ircut", "ir850"].into_iter().enumerate() {
        let value = reply
            .value
            .get_path(name)
            .ok_or(BackendError::Upstream(502))?;
        let fields = value.as_object().ok_or(BackendError::Upstream(502))?;
        if fields.len() != 3
            || fields
                .keys()
                .any(|key| !["supported", "available", "enabled"].contains(&key.as_str()))
        {
            return Err(BackendError::Upstream(502));
        }
        let supported = value
            .get_path("supported")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let available = value
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let enabled = value
            .get_path("enabled")
            .ok_or(BackendError::Upstream(502))?;
        if (available && (!supported || enabled.as_bool().is_none()))
            || (!available && enabled != &Value::Null)
        {
            return Err(BackendError::Upstream(502));
        }
        outputs[index] = (supported, enabled.as_bool());
    }
    Ok(outputs)
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::io::Write;
    use std::os::unix::net::UnixListener;
    use std::{fs, thread};

    fn ir_observation(enabled: bool) -> Vec<u8> {
        format!(r#"{{"status":"ok","ircut":{{"supported":true,"available":true,"enabled":{enabled}}},"ir850":{{"supported":true,"available":true,"enabled":{enabled}}}}}"#).into_bytes()
    }

    #[test]
    fn ir_outputs_require_strict_owner_state() {
        let valid = String::from_utf8(ir_observation(false)).unwrap();
        for (body, accepted) in [
            (valid.clone(), true),
            (
                valid.replace(
                    "\"available\":true,\"enabled\":false",
                    "\"available\":false,\"enabled\":null",
                ),
                true,
            ),
            (
                valid.replace("\"supported\":true", "\"supported\":false"),
                false,
            ),
            (
                valid.replace("\"available\":true", "\"available\":false"),
                false,
            ),
            (
                valid.replace("\"enabled\":false", "\"enabled\":null"),
                false,
            ),
            (valid.replace("\"enabled\":false", "\"enabled\":0"), false),
            (
                valid.replace("\"enabled\":false", "\"enabled\":false,\"extra\":0"),
                false,
            ),
            (
                valid.replace("\"status\":\"ok\"", "\"status\":\"ok\",\"extra\":0"),
                false,
            ),
            ("{}".into(), false),
        ] {
            let root = task_temp("ir-shape");
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let daemon = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"get-output-state"}"#);
                socket.write_all(&framed(body.as_bytes())).unwrap();
            });
            assert_eq!(
                backend(&root, "127.0.0.1:9".parse().unwrap())
                    .ir_output_state(Instant::now() + Duration::from_secs(1))
                    .is_ok(),
                accepted
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn ir_live_outputs_verify_set_response_and_separate_readback() {
        for output in ["ircut", "ir850"] {
            for enabled in [false, true] {
                for fault in ["none", "setter", "readback"] {
                    let root = task_temp("ir-live");
                    let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
                    let command = format!(
                        r#"{{"cmd":"set-output","output":"{output}","enabled":{enabled}}}"#
                    );
                    let daemon = thread::spawn(move || {
                        let mut replies = vec![
                            (
                                br#"{"cmd":"get-output-state"}"#.to_vec(),
                                ir_observation(!enabled),
                            ),
                            (
                                command.into_bytes(),
                                if fault == "setter" {
                                    br#"{"status":"error"}"#.to_vec()
                                } else {
                                    ir_observation(enabled)
                                },
                            ),
                        ];
                        if fault != "setter" {
                            replies.push((
                                br#"{"cmd":"get-output-state"}"#.to_vec(),
                                ir_observation(if fault == "readback" {
                                    !enabled
                                } else {
                                    enabled
                                }),
                            ));
                        }
                        for (request, reply) in replies {
                            let (mut socket, _) = listener.accept().unwrap();
                            socket
                                .set_read_timeout(Some(Duration::from_secs(1)))
                                .unwrap();
                            assert_eq!(read_request(&mut socket), request);
                            socket.write_all(&framed(&reply)).unwrap();
                        }
                    });
                    let body = format!(r#"{{"cmd":"{output}","val":{}}}"#, u8::from(enabled));
                    let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                        .live_control(body.as_bytes(), Instant::now() + Duration::from_secs(1));
                    if fault == "none" {
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
    }

    fn observation_values(changes: &[(&str, u32)], available: bool) -> Vec<u8> {
        let fields = IMAGING_FIELDS
            .iter()
            .filter(|(name, _, _)| *name != "anti_flicker")
            .map(|(name, _, maximum)| {
                let default = if *name == "backlight" || *maximum == 1 { 0 } else { 128 };
                let value = changes
                    .iter()
                    .find(|(changed, _)| changed == name)
                    .map_or(default, |(_, value)| *value);
                let value = if available {
                    value.to_string()
                } else {
                    "null".into()
                };
                format!(r#""{name}":{{"supported":true,"available":{available},"value":{value},"min":0,"max":{maximum}}}"#)
            })
            .collect::<Vec<_>>()
            .join(",");
        format!(r#"{{"status":"ok","persistence":"checked-config","fields":{{{fields}}}}}"#)
            .into_bytes()
    }

    fn observation(value: u32, available: bool) -> Vec<u8> {
        observation_values(&[("brightness", value)], available)
    }

    fn antiflicker_observation_values(
        value: u32,
        configured: u32,
        changes: &[(&str, u32)],
    ) -> Vec<u8> {
        let mut observation = String::from_utf8(observation_values(changes, true)).unwrap();
        observation.truncate(observation.len() - 2);
        format!(
            r#"{observation},"anti_flicker":{{"supported":true,"available":true,"value":{value},"min":0,"max":2,"verification":"sdk-readback","observed_value":{value},"configured_value":{configured}}}}}}}"#
        )
        .into_bytes()
    }

    fn antiflicker_observation(value: u32, configured: u32) -> Vec<u8> {
        antiflicker_observation_values(value, configured, &[])
    }

    fn antiflicker_disk(value: u32) -> Vec<u8> {
        format!(r#"{{"status":"ok","section":"sensor","keys":{{"antiflicker":"{value}"}}}}"#)
            .into_bytes()
    }

    fn white_balance_observation(mode: u32, rgain: u32, bgain: u32) -> Vec<u8> {
        let mut observation = String::from_utf8(observation_values(&[], true)).unwrap();
        observation.pop();
        let (live_rgain, live_bgain, effective) = if mode == 1 {
            (rgain.to_string(), bgain.to_string(), true)
        } else {
            ("null".into(), "null".into(), false)
        };
        format!(
            r#"{observation},"white_balance":{{"supported":true,"available":true,"verification":"sdk-readback","mode_min":0,"mode_max":9,"gain_min":0,"gain_max":1024,"mode":{mode},"gains_effective":{effective},"rgain":{live_rgain},"bgain":{live_bgain},"configured_mode":{mode},"configured_rgain":{rgain},"configured_bgain":{bgain}}}}}"#
        )
        .into_bytes()
    }

    fn white_balance_disk(mode: u32, rgain: u32, bgain: u32) -> Vec<u8> {
        format!(
            r#"{{"status":"ok","section":"image","keys":{{"core_wb_mode":"{mode}","wb_rgain":"{rgain}","wb_bgain":"{bgain}"}}}}"#
        )
        .into_bytes()
    }

    #[test]
    fn antiflicker_get_reports_live_saved_and_match_state() {
        let root = task_temp("antiflicker-get");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (
                    br#"{"cmd":"get-imaging"}"#.as_slice(),
                    antiflicker_observation(1, 1),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"sensor"}"#.as_slice(),
                    antiflicker_disk(2),
                ),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .imaging(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("message.fields.anti_flicker.observed_value"),
            Some(&Value::Number("1".into()))
        );
        assert_eq!(
            value.get_path("message.fields.anti_flicker.saved_value"),
            Some(&Value::Number("2".into()))
        );
        assert_eq!(
            value.get_path("message.fields.anti_flicker.matches_saved"),
            Some(&Value::Bool(false))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn antiflicker_save_verifies_live_disk_and_final_live() {
        for requested in 0..=2 {
            let root = task_temp("antiflicker-save");
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let before = (requested + 1) % 3;
            let daemon = thread::spawn(move || {
                let pairs = vec![
                    (
                        br#"{"cmd":"get-imaging"}"#.to_vec(),
                        antiflicker_observation(before, before),
                    ),
                    (
                        format!(
                            r#"{{"cmd":"set-imaging","values":{{"anti_flicker":{requested}}}}}"#
                        )
                        .into_bytes(),
                        br#"{"status":"ok"}"#.to_vec(),
                    ),
                    (
                        br#"{"cmd":"get-imaging"}"#.to_vec(),
                        antiflicker_observation(requested, requested),
                    ),
                    (
                        br#"{"cmd":"config-save"}"#.to_vec(),
                        br#"{"status":"ok"}"#.to_vec(),
                    ),
                    (
                        br#"{"cmd":"config-read-section","section":"sensor"}"#.to_vec(),
                        antiflicker_disk(requested),
                    ),
                    (
                        br#"{"cmd":"get-imaging"}"#.to_vec(),
                        antiflicker_observation(requested, requested),
                    ),
                    (
                        br#"{"cmd":"config-read-section","section":"sensor"}"#.to_vec(),
                        antiflicker_disk(requested),
                    ),
                ];
                for (request, response) in pairs {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .update_imaging(
                    format!(r#"{{"anti_flicker":{requested}}}"#).as_bytes(),
                    Instant::now() + Duration::from_secs(2),
                )
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(
                value.get_path("message.fields.anti_flicker.value"),
                Some(&Value::Number(requested.to_string()))
            );
            assert_eq!(
                value.get_path("message.fields.anti_flicker.saved_value"),
                Some(&Value::Number(requested.to_string()))
            );
            assert_eq!(
                value.get_path("message.fields.anti_flicker.matches_saved"),
                Some(&Value::Bool(true))
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn antiflicker_mixed_save_keeps_image_and_sensor_disk_readback() {
        let root = task_temp("antiflicker-mixed-save");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let pairs = vec![
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    antiflicker_observation_values(2, 2, &[("brightness", 128)]),
                ),
                (
                    br#"{"cmd":"set-imaging","values":{"anti_flicker":1,"brightness":140}}"#
                        .to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    antiflicker_observation_values(1, 1, &[("brightness", 140)]),
                ),
                (
                    br#"{"cmd":"config-save"}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"image"}"#.to_vec(),
                    br#"{"status":"ok","section":"image","keys":{"brightness":"140"}}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"sensor"}"#.to_vec(),
                    antiflicker_disk(1),
                ),
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    antiflicker_observation_values(1, 1, &[("brightness", 140)]),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"sensor"}"#.to_vec(),
                    antiflicker_disk(1),
                ),
            ];
            for (request, response) in pairs {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .update_imaging(
                br#"{"anti_flicker":1,"brightness":140}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("message.fields.anti_flicker.matches_saved"),
            Some(&Value::Bool(true))
        );
        assert_eq!(
            value.get_path("message.fields.brightness.value"),
            Some(&Value::Number("140".into()))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn antiflicker_save_reports_partial_apply_on_disk_mismatch() {
        let root = task_temp("antiflicker-disk-mismatch");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let pairs = vec![
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    antiflicker_observation(2, 2),
                ),
                (
                    br#"{"cmd":"set-imaging","values":{"anti_flicker":1}}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    antiflicker_observation(1, 1),
                ),
                (
                    br#"{"cmd":"config-save"}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"sensor"}"#.to_vec(),
                    antiflicker_disk(2),
                ),
            ];
            for (request, response) in pairs {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_imaging(
            br#"{"anti_flicker":1}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(
            matches!(result, Err(BackendError::PartialApply(_))),
            "{result:?}"
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn white_balance_rejects_invalid_group_before_ipc() {
        let root = task_temp("white-balance-invalid");
        for body in [
            br#"{"white_balance":{"mode":10,"rgain":128,"bgain":128}}"#.as_slice(),
            br#"{"white_balance":{"mode":1,"rgain":1025,"bgain":128}}"#.as_slice(),
            br#"{"white_balance":{"mode":1.5,"rgain":128,"bgain":128}}"#.as_slice(),
            br#"{"white_balance":{"mode":1,"rgain":128}}"#.as_slice(),
            br#"{"white_balance":{"mode":true,"rgain":128,"bgain":128}}"#.as_slice(),
            br#"{"white_balance":{"mode":1,"rgain":128,"bgain":128,"extra":0}}"#.as_slice(),
        ] {
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .update_imaging(body, Instant::now() + Duration::from_secs(1));
            assert!(matches!(result, Err(BackendError::Protocol)), "{result:?}");
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn white_balance_get_keeps_automatic_gains_out_of_live_observation() {
        let root = task_temp("white-balance-get");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (
                    br#"{"cmd":"get-imaging"}"#.as_slice(),
                    white_balance_observation(2, 300, 400),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"image"}"#.as_slice(),
                    white_balance_disk(2, 300, 400),
                ),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .imaging(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("message.white_balance.mode"),
            Some(&Value::Number("2".into()))
        );
        assert_eq!(
            value.get_path("message.white_balance.rgain"),
            Some(&Value::Null)
        );
        assert_eq!(
            value.get_path("message.white_balance.configured_rgain"),
            Some(&Value::Number("300".into()))
        );
        assert_eq!(
            value.get_path("message.white_balance.matches_saved"),
            Some(&Value::Bool(true))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn white_balance_saved_or_configured_failure_keeps_scalar_imaging_available() {
        for missing_config in [false, true] {
            let root = task_temp("white-balance-independent-failure");
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let mut observation =
                String::from_utf8(white_balance_observation(2, 300, 400)).unwrap();
            if missing_config {
                for (name, value) in [
                    ("configured_mode", "2"),
                    ("configured_rgain", "300"),
                    ("configured_bgain", "400"),
                ] {
                    observation = observation.replace(
                        &format!(r#""{name}":{value}"#),
                        &format!(r#""{name}":null"#),
                    );
                }
            }
            let disk = if missing_config {
                white_balance_disk(2, 300, 400)
            } else {
                br#"{"status":"error"}"#.to_vec()
            };
            let daemon = thread::spawn(move || {
                for (request, response) in [
                    (
                        br#"{"cmd":"get-imaging"}"#.as_slice(),
                        observation.into_bytes(),
                    ),
                    (
                        br#"{"cmd":"config-read-section","section":"image"}"#.as_slice(),
                        disk,
                    ),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .imaging(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(
                value.get_path("message.fields.brightness.value"),
                Some(&Value::Number("128".into()))
            );
            assert_eq!(
                value.get_path("message.white_balance.available"),
                Some(&Value::Bool(true))
            );
            assert_eq!(
                value.get_path(if missing_config {
                    "message.white_balance.configured_mode"
                } else {
                    "message.white_balance.saved_mode"
                }),
                Some(&Value::Null)
            );
            assert_eq!(
                value.get_path("message.white_balance.matches_saved"),
                Some(&Value::Bool(false))
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn white_balance_disk_timeout_is_bounded_and_keeps_scalar_imaging_available() {
        let root = task_temp("white-balance-disk-timeout");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut socket), br#"{"cmd":"get-imaging"}"#);
            socket
                .write_all(&framed(&white_balance_observation(2, 300, 400)))
                .unwrap();
            drop(socket);

            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(
                read_request(&mut socket),
                br#"{"cmd":"config-read-section","section":"image"}"#
            );
            thread::sleep(Duration::from_millis(500));
        });
        let started = Instant::now();
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .imaging(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let elapsed = started.elapsed();
        let value = crate::json::parse(&response.body).unwrap();
        assert!(
            elapsed < Duration::from_millis(400),
            "optional disk read consumed {elapsed:?}"
        );
        assert_eq!(
            value.get_path("message.fields.brightness.value"),
            Some(&Value::Number("128".into()))
        );
        assert_eq!(
            value.get_path("message.white_balance.available"),
            Some(&Value::Bool(true))
        );
        assert_eq!(
            value.get_path("message.white_balance.saved_mode"),
            Some(&Value::Null)
        );
        assert_eq!(
            value.get_path("message.white_balance.matches_saved"),
            Some(&Value::Bool(false))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn white_balance_rejects_malformed_native_observations() {
        let valid_auto = String::from_utf8(white_balance_observation(2, 300, 400)).unwrap();
        let valid_manual = String::from_utf8(white_balance_observation(1, 300, 400)).unwrap();
        for malformed in [
            valid_auto.replacen("\"mode\":2", "\"mode\":10", 1),
            valid_auto.replacen("\"rgain\":null", "\"rgain\":300", 1),
            valid_manual.replacen("\"rgain\":300", "\"rgain\":1025", 1),
            valid_auto.replacen(
                "\"verification\":\"sdk-readback\"",
                "\"verification\":\"unsupported\"",
                1,
            ),
        ] {
            let root = task_temp("white-balance-malformed");
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let daemon = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"get-imaging"}"#);
                socket.write_all(&framed(malformed.as_bytes())).unwrap();
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .imaging(Instant::now() + Duration::from_secs(1));
            assert!(
                matches!(result, Err(BackendError::Upstream(502))),
                "{result:?}"
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn white_balance_manual_save_verifies_live_disk_and_final_live() {
        let root = task_temp("white-balance-save");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let command = br#"{"cmd":"set-imaging","values":{"white_balance":{"bgain":401,"mode":1,"rgain":301}}}"#;
        let daemon = thread::spawn(move || {
            let pairs = vec![
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    white_balance_observation(0, 128, 128),
                ),
                (command.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    white_balance_observation(1, 301, 401),
                ),
                (
                    br#"{"cmd":"config-save"}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"image"}"#.to_vec(),
                    white_balance_disk(1, 301, 401),
                ),
                (
                    br#"{"cmd":"get-imaging"}"#.to_vec(),
                    white_balance_observation(1, 301, 401),
                ),
            ];
            for (request, response) in pairs {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .update_imaging(
                br#"{"white_balance":{"mode":1,"rgain":301,"bgain":401}}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("message.white_balance.matches_saved"),
            Some(&Value::Bool(true))
        );
        assert_eq!(
            value.get_path("message.white_balance.rgain"),
            Some(&Value::Number("301".into()))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn white_balance_save_does_not_treat_missing_default_disk_keys_as_persisted() {
        let root = task_temp("white-balance-missing-disk");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let pairs = vec![
                (br#"{"cmd":"get-imaging"}"#.to_vec(), white_balance_observation(1, 128, 128)),
                (br#"{"cmd":"set-imaging","values":{"white_balance":{"bgain":128,"mode":0,"rgain":128}}}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-imaging"}"#.to_vec(), white_balance_observation(0, 128, 128)),
                (br#"{"cmd":"config-save"}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"image"}"#.to_vec(), br#"{"status":"ok","section":"image","keys":{}}"#.to_vec()),
            ];
            for (request, response) in pairs {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_imaging(
            br#"{"white_balance":{"mode":0,"rgain":128,"bgain":128}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(matches!(result, Err(BackendError::PartialApply(_))));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn white_balance_preset_rejects_changed_retained_gains_before_setter() {
        let root = task_temp("white-balance-preset-gain");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut socket), br#"{"cmd":"get-imaging"}"#);
            socket
                .write_all(&framed(&white_balance_observation(2, 300, 400)))
                .unwrap();
        });
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_imaging(
            br#"{"white_balance":{"mode":3,"rgain":301,"bgain":400}}"#,
            Instant::now() + Duration::from_secs(1),
        );
        assert!(matches!(result, Err(BackendError::Unsupported(_))));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    fn setter_config_observation(value: u32) -> Vec<u8> {
        let observation =
            String::from_utf8(observation_values(&[("noise_reduction", value)], true)).unwrap();
        observation
            .replace(
                &format!(r#""noise_reduction":{{"supported":true,"available":true,"value":{value},"min":0,"max":255}}"#),
                &format!(r#""noise_reduction":{{"supported":true,"available":true,"value":{value},"min":0,"max":255,"verification":"sdk-setter-config","observed_value":null,"configured_value":{value}}}"#),
            )
            .into_bytes()
    }

    #[test]
    fn imaging_rejects_invalid_extended_values_before_ipc() {
        let root = task_temp("imaging-invalid");
        for body in [
            br#"{"backlight":11}"#.as_slice(),
            br#"{"wide_dynamic_range":256}"#.as_slice(),
            br#"{"tone":1.5}"#.as_slice(),
            br#"{"defog":true}"#.as_slice(),
            br#"{"noise_reduction":-1}"#.as_slice(),
            br#"{"hue":256}"#.as_slice(),
            br#"{"dpc_strength":-1}"#.as_slice(),
            br#"{"exposure_compensation":256}"#.as_slice(),
            br#"{"hflip":2}"#.as_slice(),
            br#"{"vflip":true}"#.as_slice(),
            br#"{"anti_flicker":3}"#.as_slice(),
            br#"{"unknown":1}"#.as_slice(),
        ] {
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .update_imaging(body, Instant::now() + Duration::from_secs(1));
            assert!(matches!(result, Err(BackendError::Protocol)));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn legacy_four_field_observation_disables_extended_controls() {
        let root = task_temp("imaging-legacy-fields");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut socket), br#"{"cmd":"get-imaging"}"#);
            socket.write_all(&framed(br#"{"status":"ok","fields":{"brightness":{"supported":true,"available":true,"value":128,"min":0,"max":255},"contrast":{"supported":true,"available":true,"value":128,"min":0,"max":255},"saturation":{"supported":true,"available":true,"value":128,"min":0,"max":255},"sharpness":{"supported":true,"available":true,"value":128,"min":0,"max":255}}}"#)).unwrap();
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .imaging(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("persistent"), Some(&Value::Bool(false)));
        assert_eq!(
            value.get_path("message.fields.backlight.supported"),
            Some(&Value::Bool(false))
        );
        assert_eq!(
            value.get_path("message.fields.noise_reduction.value"),
            Some(&Value::Null)
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn setter_config_field_never_claims_independent_observation() {
        let root = task_temp("imaging-setter-config");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        let daemon = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut socket), br#"{"cmd":"get-imaging"}"#);
            socket
                .write_all(&framed(&setter_config_observation(120)))
                .unwrap();
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .imaging(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("message.fields.noise_reduction.verification"),
            Some(&Value::String("sdk-setter-config".into()))
        );
        assert_eq!(
            value.get_path("message.fields.noise_reduction.observed_value"),
            Some(&Value::Null)
        );
        assert_eq!(
            value.get_path("message.fields.noise_reduction.configured_value"),
            Some(&Value::Number("120".into()))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn imaging_save_requires_apply_live_and_independent_disk_readback() {
        for fault in [
            "none",
            "unavailable",
            "setter",
            "live",
            "save",
            "disk",
            "section",
            "missing",
        ] {
            let root = task_temp("imaging-save");
            let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
            listener.set_nonblocking(true).unwrap();
            let mut pairs = vec![(
                br#"{"cmd":"get-imaging"}"#.to_vec(),
                observation(128, fault != "unavailable"),
            )];
            if fault != "unavailable" {
                pairs.push((
                    br#"{"cmd":"set-imaging","values":{"brightness":140}}"#.to_vec(),
                    if fault == "setter" {
                        br#"{"status":"error"}"#.to_vec()
                    } else {
                        br#"{"status":"ok"}"#.to_vec()
                    },
                ));
                if fault != "setter" {
                    pairs.push((
                        br#"{"cmd":"get-imaging"}"#.to_vec(),
                        observation(if fault == "live" { 139 } else { 140 }, true),
                    ));
                    if fault != "live" {
                        pairs.push((
                            br#"{"cmd":"config-save"}"#.to_vec(),
                            if fault == "save" {
                                br#"{"status":"error"}"#.to_vec()
                            } else {
                                br#"{"status":"ok"}"#.to_vec()
                            },
                        ));
                        if fault != "save" {
                            pairs.push((
                                br#"{"cmd":"config-read-section","section":"image"}"#.to_vec(),
                                format!(
                                    r#"{{"status":"ok","section":"{}","keys":{{{}}}}}"#,
                                    if fault == "section" { "audio" } else { "image" },
                                    if fault == "missing" {
                                        String::new()
                                    } else {
                                        format!(
                                            r#""brightness":"{}""#,
                                            if fault == "disk" { 139 } else { 140 }
                                        )
                                    }
                                )
                                .into_bytes(),
                            ));
                        }
                    }
                }
            }
            let daemon = thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(3);
                for (request, response) in pairs {
                    let mut socket = loop {
                        match listener.accept() {
                            Ok((socket, _)) => break socket,
                            Err(error)
                                if error.kind() == std::io::ErrorKind::WouldBlock
                                    && Instant::now() < deadline =>
                            {
                                thread::sleep(Duration::from_millis(2))
                            }
                            other => panic!("missing imaging request: {other:?}"),
                        }
                    };
                    socket
                        .set_read_timeout(Some(Duration::from_secs(1)))
                        .unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_imaging(
                br#"{"brightness":140}"#,
                Instant::now() + Duration::from_secs(2),
            );
            if fault == "none" {
                let value = crate::json::parse(&result.unwrap().body).unwrap();
                assert_eq!(value.get_path("persistent"), Some(&Value::Bool(true)));
            } else if fault == "unavailable" {
                assert!(matches!(result, Err(BackendError::Unsupported(_))));
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
    fn imaging_save_maps_extended_fields_to_exact_disk_keys() {
        let root = task_temp("imaging-extended-save");
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        let changes = [
            ("backlight", 4),
            ("wide_dynamic_range", 150),
            ("tone", 20),
            ("defog", 130),
            ("noise_reduction", 110),
            ("hue", 135),
            ("dpc_strength", 90),
            ("exposure_compensation", 145),
            ("hflip", 1),
            ("vflip", 1),
        ];
        let body = br#"{"backlight":4,"defog":130,"dpc_strength":90,"exposure_compensation":145,"hflip":1,"hue":135,"noise_reduction":110,"tone":20,"vflip":1,"wide_dynamic_range":150}"#;
        let command = br#"{"cmd":"set-imaging","values":{"backlight":4,"defog":130,"dpc_strength":90,"exposure_compensation":145,"hflip":1,"hue":135,"noise_reduction":110,"tone":20,"vflip":1,"wide_dynamic_range":150}}"#;
        let pairs = vec![
            (br#"{"cmd":"get-imaging"}"#.to_vec(), observation_values(&[], true)),
            (command.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (
                br#"{"cmd":"get-imaging"}"#.to_vec(),
                observation_values(&changes, true),
            ),
            (br#"{"cmd":"config-save"}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (
                br#"{"cmd":"config-read-section","section":"image"}"#.to_vec(),
                br#"{"status":"ok","section":"image","keys":{"backlight_comp":"4","defog_strength":"130","drc_strength":"150","highlight_depress":"20","sinter":"110","hue":"135","dpc_strength":"90","ae_comp":"145","hflip":"1","vflip":"1"}}"#.to_vec(),
            ),
        ];
        let daemon = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for (request, response) in pairs {
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == std::io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing extended imaging request: {other:?}"),
                    }
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .update_imaging(body, Instant::now() + Duration::from_secs(2))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("message.fields.backlight.max"),
            Some(&Value::Number("10".into()))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
