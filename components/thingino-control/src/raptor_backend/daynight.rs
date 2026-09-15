use super::*;

fn object<const N: usize>(fields: [(&str, Value); N]) -> Value {
    Value::Object(
        fields
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
    )
}

fn mode_value(value: Option<&Value>) -> Result<&str, BackendError> {
    match value.and_then(Value::as_str) {
        Some(mode @ ("auto" | "day" | "night")) => Ok(mode),
        _ => Err(BackendError::Upstream(502)),
    }
}

fn service_enabled(reply: &RaptorReply) -> Result<bool, BackendError> {
    match reply.value.get_path("service_enabled") {
        None => Ok(true), // Earlier RIC exits when disabled and cannot return this reply.
        Some(Value::Bool(enabled)) => Ok(*enabled),
        _ => Err(BackendError::Upstream(502)),
    }
}

fn initial_mode_value(value: Option<&Value>) -> Result<&str, BackendError> {
    match value.and_then(Value::as_str) {
        Some(mode @ ("default" | "day" | "night")) => Ok(mode),
        _ => Err(BackendError::Upstream(502)),
    }
}

fn startup_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("startup") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 4
        || object.keys().any(|key| {
            !["supported", "available", "configured", "boot_active"].contains(&key.as_str())
        })
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
    if available && !supported {
        return Err(BackendError::Upstream(502));
    }
    initial_mode_value(value.get_path("configured"))?;
    initial_mode_value(value.get_path("boot_active"))?;
    Ok(Some(value.clone()))
}

fn saved_initial_mode(keys: &Value, require_explicit: bool) -> Result<&str, BackendError> {
    match keys.get_path("initial_mode") {
        None if !require_explicit => Ok("default"),
        value => initial_mode_value(value),
    }
}

fn restart_only_initial_mode(request: &Value, live_mode: Option<&str>) -> bool {
    request.get_path("initial_mode").is_some()
        && request.as_object().is_some_and(|object| {
            object
                .keys()
                .all(|key| key == "mode" || key == "initial_mode")
        })
        && request.get_path("mode").and_then(Value::as_str) == live_mode
}

fn ir850_at_night_observation(reply: &RaptorReply) -> Result<Option<bool>, BackendError> {
    match reply.value.get_path("ir850_at_night") {
        None => Ok(None),
        Some(value) => value.as_bool().map(Some).ok_or(BackendError::Upstream(502)),
    }
}

fn saved_ir850_at_night(keys: &Value, require_explicit: bool) -> Result<bool, BackendError> {
    match keys.get_path("ir850") {
        Some(value) if value.as_str() == Some("true") => Ok(true),
        Some(value) if value.as_str() == Some("false") => Ok(false),
        None if !require_explicit => Ok(true),
        _ => Err(BackendError::Upstream(502)),
    }
}

fn saved_bool(
    keys: &Value,
    key: &str,
    default: bool,
    require_explicit: bool,
) -> Result<bool, BackendError> {
    match keys.get_path(key) {
        Some(value) if value.as_str() == Some("true") => Ok(true),
        Some(value) if value.as_str() == Some("false") => Ok(false),
        None if !require_explicit => Ok(default),
        _ => Err(BackendError::Upstream(502)),
    }
}

fn saved_confirmation_count(
    keys: &Value,
    key: &str,
    require_explicit: bool,
) -> Result<u64, BackendError> {
    match keys.get_path(key) {
        Some(value) => value
            .as_str()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| (1..=255).contains(value))
            .ok_or(BackendError::Upstream(502)),
        None if !require_explicit => Ok(1),
        None => Err(BackendError::Upstream(502)),
    }
}

fn automation_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("automation") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 3
        || object
            .keys()
            .any(|key| !["supported", "available", "paused"].contains(&key.as_str()))
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
    value
        .get_path("paused")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    if available && !supported {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn automatic_outputs_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("automatic_outputs") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 8
        || object.keys().any(|key| {
            ![
                "supported",
                "available",
                "color",
                "ircut",
                "color_owner",
                "ircut_owner",
                "color_state",
                "ircut_commanded_state",
            ]
            .contains(&key.as_str())
        })
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
    for key in ["color", "ircut"] {
        value
            .get_path(key)
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
    }
    for key in ["color_owner", "ircut_owner"] {
        let owner = value.get_path(key).and_then(Value::as_str);
        let valid = if service_enabled(reply)? {
            matches!(owner, Some("automatic" | "manual"))
        } else {
            owner == Some("disabled") && !available
        };
        if !valid {
            return Err(BackendError::Upstream(502));
        }
    }
    for key in ["color_state", "ircut_commanded_state"] {
        let valid = match value.get_path(key) {
            Some(Value::Null) => true,
            Some(Value::String(mode)) => mode == "day" || mode == "night",
            _ => false,
        };
        if !valid || (!service_enabled(reply)? && value.get_path(key) != Some(&Value::Null)) {
            return Err(BackendError::Upstream(502));
        }
    }
    if available && !supported {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn confirmation_values(value: &Value) -> Result<Value, BackendError> {
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 2 {
        return Err(BackendError::Upstream(502));
    }
    let day = value_u64(object.get("day").ok_or(BackendError::Upstream(502))?)?;
    let night = value_u64(object.get("night").ok_or(BackendError::Upstream(502))?)?;
    if !(1..=255).contains(&day) || !(1..=255).contains(&night) {
        return Err(BackendError::Upstream(502));
    }
    Ok(value.clone())
}

fn confirmation_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("confirmation") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 3 {
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
    if available {
        confirmation_values(
            value
                .get_path("values")
                .ok_or(BackendError::Upstream(502))?,
        )?;
    } else if value.get_path("values") != Some(&Value::Null) {
        return Err(BackendError::Upstream(502));
    }
    if available && !supported {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn valid_loglevel(value: &str) -> bool {
    matches!(
        value,
        "fatal" | "error" | "warn" | "info" | "debug" | "trace"
    )
}

fn loglevel_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("loglevel") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 3 {
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
    let current = value
        .get_path("value")
        .and_then(Value::as_str)
        .ok_or(BackendError::Upstream(502))?;
    if !valid_loglevel(current) || available && !supported {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn threshold_fields(trigger: &str) -> Option<&'static [(&'static str, u64, u64, u64)]> {
    match trigger {
        "luma" => Some(&[
            ("night_luma", 0, 255, 20),
            ("night_gain", 0, 2_147_483_647, 80_000),
            ("day_gain_pct", 1, 100, 25),
        ]),
        "gain" => Some(&[
            ("day_threshold", 0, 2_147_483_647, 25_000),
            ("night_threshold", 0, 2_147_483_647, 40_000),
        ]),
        "adc" => Some(&[
            ("adc_night", 0, 2_147_483_647, 200),
            ("adc_day", 0, 2_147_483_647, 600),
        ]),
        "photo" => Some(&[
            ("photo_ev_day", 0, 2_147_483_647, 5_000),
            ("photo_ev_night", 0, 2_147_483_647, 50_000),
            ("photo_ev_deep", 0, 2_147_483_647, 150_000),
        ]),
        _ => None,
    }
}

fn threshold_values(
    trigger: &str,
    values: &Value,
    disk: bool,
    defaults: bool,
) -> Result<Value, BackendError> {
    let fields = threshold_fields(trigger).ok_or(BackendError::Upstream(502))?;
    let object = values.as_object().ok_or(BackendError::Upstream(502))?;
    if !disk && object.len() != fields.len() {
        return Err(BackendError::Upstream(502));
    }
    let mut result = std::collections::BTreeMap::new();
    let mut previous = None;
    for (key, min, max, default) in fields {
        let value = match object.get(*key) {
            Some(value) if disk => value
                .as_str()
                .and_then(|v| v.parse::<u64>().ok())
                .ok_or(BackendError::Upstream(502))?,
            Some(value) => value_u64(value)?,
            None if defaults => *default,
            None => return Err(BackendError::Upstream(502)),
        };
        if value < *min
            || value > *max
            || (trigger != "luma" && previous.is_some_and(|before| before >= value))
        {
            return Err(BackendError::Upstream(502));
        }
        previous = Some(value);
        result.insert((*key).into(), Value::Number(value.to_string()));
    }
    Ok(Value::Object(result))
}

fn threshold_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("thresholds") else {
        return Ok(None);
    };
    let trigger = value
        .get_path("trigger")
        .and_then(Value::as_str)
        .ok_or(BackendError::Upstream(502))?;
    let supported = value
        .get_path("supported")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let available = value
        .get_path("available")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    if (supported && threshold_fields(trigger).is_none()) || (available && !supported) {
        return Err(BackendError::Upstream(502));
    }
    if available {
        threshold_values(
            trigger,
            value
                .get_path("values")
                .ok_or(BackendError::Upstream(502))?,
            false,
            false,
        )?;
    } else if value.get_path("values") != Some(&Value::Null) {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn timing_values(
    value: &Value,
    disk: bool,
    transition_supported: bool,
) -> Result<Value, BackendError> {
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if (!disk
        && object
            .keys()
            .any(|key| key != "sample_interval_ms" && key != "transition_delay_s"))
        || (!disk
            && ((transition_supported && object.len() != 2)
                || (!transition_supported && !(1..=2).contains(&object.len()))))
    {
        return Err(BackendError::Upstream(502));
    }
    let parse = |key: &str, disk_key: &str, default: u64| -> Result<u64, BackendError> {
        match object.get(if disk { disk_key } else { key }) {
            Some(value) if disk => value
                .as_str()
                .and_then(|value| value.parse::<u64>().ok())
                .ok_or(BackendError::Upstream(502)),
            Some(value) => value_u64(value),
            None if disk => Ok(default),
            None => Err(BackendError::Upstream(502)),
        }
    };
    let sample = parse(
        "sample_interval_ms",
        "poll_interval_ms",
        if transition_supported { 1_000 } else { 100 },
    )?;
    if !(50..=10_000).contains(&sample) {
        return Err(BackendError::Upstream(502));
    }
    let mut result = std::collections::BTreeMap::new();
    result.insert(
        "sample_interval_ms".into(),
        Value::Number(sample.to_string()),
    );
    if transition_supported {
        let delay = parse("transition_delay_s", "hysteresis_sec", 5)?;
        if !(1..=300).contains(&delay) {
            return Err(BackendError::Upstream(502));
        }
        result.insert(
            "transition_delay_s".into(),
            Value::Number(delay.to_string()),
        );
    }
    Ok(Value::Object(result))
}

fn timing_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("timing") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 4
        || object.keys().any(|key| {
            ![
                "supported",
                "available",
                "transition_delay_supported",
                "values",
            ]
            .contains(&key.as_str())
        })
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
    let transition_supported = value
        .get_path("transition_delay_supported")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    if (transition_supported || available) && !supported {
        return Err(BackendError::Upstream(502));
    }
    if available {
        let values = value
            .get_path("values")
            .ok_or(BackendError::Upstream(502))?;
        timing_values(values, false, transition_supported)?;
        if !transition_supported && values.get_path("transition_delay_s") != Some(&Value::Null) {
            return Err(BackendError::Upstream(502));
        }
    } else if value.get_path("values") != Some(&Value::Null) {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn schedule_values(value: &Value, disk: bool) -> Result<Value, BackendError> {
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    let (enabled, start, stop) = if disk {
        let enabled = match object.get("schedule_enabled").and_then(Value::as_str) {
            Some("true") => true,
            Some("false") => false,
            _ => return Err(BackendError::Upstream(502)),
        };
        (
            enabled,
            object
                .get("schedule_start")
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?,
            object
                .get("schedule_stop")
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?,
        )
    } else {
        if object.len() != 3 {
            return Err(BackendError::Upstream(502));
        }
        (
            object
                .get("enabled")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?,
            object
                .get("start_at")
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?,
            object
                .get("stop_at")
                .and_then(Value::as_str)
                .ok_or(BackendError::Upstream(502))?,
        )
    };
    let valid_time = |time: &str| {
        let bytes = time.as_bytes();
        bytes.len() == 5
            && bytes[2] == b':'
            && [bytes[0], bytes[1], bytes[3], bytes[4]]
                .iter()
                .all(u8::is_ascii_digit)
            && (bytes[0] - b'0') * 10 + bytes[1] - b'0' < 24
            && (bytes[3] - b'0') * 10 + bytes[4] - b'0' < 60
    };
    if !valid_time(start) || !valid_time(stop) {
        return Err(BackendError::Upstream(502));
    }
    Ok(crate::json::parse(
        format!(r#"{{"enabled":{enabled},"start_at":"{start}","stop_at":"{stop}"}}"#).as_bytes(),
    )
    .unwrap())
}

fn schedule_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("schedule") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 6 {
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
    let active = value
        .get_path("active")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let application_ok = value
        .get_path("application_ok")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    if available && !supported || active && !available {
        return Err(BackendError::Upstream(502));
    }
    schedule_values(
        value
            .get_path("values")
            .ok_or(BackendError::Upstream(502))?,
        false,
    )?;
    match value.get_path("target") {
        Some(Value::Null) if !active => {}
        Some(Value::Null) if active && !application_ok => {}
        Some(target) if active && matches!(target.as_str(), Some("day" | "night")) => {}
        _ => return Err(BackendError::Upstream(502)),
    }
    if active && application_ok && value.get_path("target") != reply.value.get_path("state") {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn number_f64(value: &Value) -> Result<f64, BackendError> {
    let Value::Number(raw) = value else {
        return Err(BackendError::Upstream(502));
    };
    raw.parse::<f64>()
        .ok()
        .filter(|value| value.is_finite())
        .ok_or(BackendError::Upstream(502))
}

fn sun_values(value: &Value, disk: bool) -> Result<Value, BackendError> {
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    let enabled = if disk {
        match object.get("sun_enabled").and_then(Value::as_str) {
            Some("true") => true,
            Some("false") => false,
            _ => return Err(BackendError::Upstream(502)),
        }
    } else {
        object
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?
    };
    if !disk && object.len() != 5 {
        return Err(BackendError::Upstream(502));
    }
    let coordinate = |live_key: &str, disk_key: &str, min: f64, max: f64| {
        let value = object
            .get(if disk { disk_key } else { live_key })
            .ok_or(BackendError::Upstream(502))?;
        let parsed = if disk {
            value
                .as_str()
                .and_then(|value| value.parse::<f64>().ok())
                .filter(|value| value.is_finite())
                .ok_or(BackendError::Upstream(502))?
        } else {
            number_f64(value)?
        };
        if parsed < min || parsed > max {
            return Err(BackendError::Upstream(502));
        }
        Ok(parsed)
    };
    let offset = |key: &str| {
        let value = object.get(key).ok_or(BackendError::Upstream(502))?;
        let parsed = if disk {
            value
                .as_str()
                .and_then(|value| value.parse::<i64>().ok())
                .ok_or(BackendError::Upstream(502))?
        } else {
            let Value::Number(raw) = value else {
                return Err(BackendError::Upstream(502));
            };
            raw.parse::<i64>()
                .map_err(|_| BackendError::Upstream(502))?
        };
        if !(-1_440..=1_440).contains(&parsed) {
            return Err(BackendError::Upstream(502));
        }
        Ok(parsed)
    };
    let latitude = coordinate("latitude", "sun_latitude", -90.0, 90.0)?;
    let longitude = coordinate("longitude", "sun_longitude", -180.0, 180.0)?;
    let sunrise_offset = offset("sunrise_offset")?;
    let sunset_offset = offset("sunset_offset")?;
    crate::json::parse(
        format!(
            r#"{{"enabled":{enabled},"latitude":{latitude},"longitude":{longitude},"sunrise_offset":{sunrise_offset},"sunset_offset":{sunset_offset}}}"#
        )
        .as_bytes(),
    )
    .map_err(|_| BackendError::Upstream(502))
}

fn sun_observation(reply: &RaptorReply) -> Result<Option<Value>, BackendError> {
    let Some(value) = reply.value.get_path("sun") else {
        return Ok(None);
    };
    let object = value.as_object().ok_or(BackendError::Upstream(502))?;
    if object.len() != 7 {
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
    let active = value
        .get_path("active")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let application_ok = value
        .get_path("application_ok")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    if available && !supported || active && !available {
        return Err(BackendError::Upstream(502));
    }
    sun_values(
        value
            .get_path("values")
            .ok_or(BackendError::Upstream(502))?,
        false,
    )?;
    match (value.get_path("target"), value.get_path("condition")) {
        (Some(Value::Null), Some(Value::Null)) if !active || !application_ok => {}
        (Some(target), Some(Value::String(condition)))
            if active
                && matches!(target.as_str(), Some("day" | "night"))
                && matches!(condition.as_str(), "normal" | "polar_day" | "polar_night") => {}
        _ => return Err(BackendError::Upstream(502)),
    }
    if active && application_ok && value.get_path("target") != reply.value.get_path("state") {
        return Err(BackendError::Upstream(502));
    }
    Ok(Some(value.clone()))
}

fn requested_timing(value: &Value) -> Result<Value, BackendError> {
    let object = value.as_object().ok_or(BackendError::Protocol)?;
    if object.is_empty()
        || object.len() > 2
        || object
            .keys()
            .any(|key| key != "sample_interval_ms" && key != "transition_delay_s")
    {
        return Err(BackendError::Protocol);
    }
    let mut result = std::collections::BTreeMap::new();
    for (key, min, max) in [
        ("sample_interval_ms", 50, 10_000),
        ("transition_delay_s", 1, 300),
    ] {
        if let Some(value) = object.get(key) {
            let value = value_u64(value).map_err(|_| BackendError::Protocol)?;
            if value < min || value > max {
                return Err(BackendError::Protocol);
            }
            result.insert(key.into(), Value::Number(value.to_string()));
        }
    }
    Ok(Value::Object(result))
}

fn requested_timing_matches(requested: &Value, observed: &Value) -> bool {
    requested.as_object().is_some_and(|values| {
        values
            .iter()
            .all(|(key, value)| observed.get_path(key) == Some(value))
    })
}

fn saved_timing_for_request(requested: &Value, disk: &Value) -> Result<Value, BackendError> {
    for (request_key, disk_key) in [
        ("sample_interval_ms", "poll_interval_ms"),
        ("transition_delay_s", "hysteresis_sec"),
    ] {
        if requested.get_path(request_key).is_some() && disk.get_path(disk_key).is_none() {
            return Err(BackendError::Upstream(502));
        }
    }
    timing_values(
        disk,
        true,
        requested.get_path("transition_delay_s").is_some(),
    )
}

impl RaptorBackend {
    pub(super) fn retain_daynight_sample(&self, body: &[u8]) {
        // Leave room for array punctuation below the shared 64 KiB JSON limit.
        const MAX_BYTES: usize = 63 * 1024;
        if body.len() > MAX_BYTES {
            return;
        }
        if let Ok(mut history) = self.daynight_history.lock() {
            let mut bytes = history.iter().map(Vec::len).sum::<usize>() + body.len();
            while history.len() >= 300 || bytes > MAX_BYTES {
                let Some(oldest) = history.pop_front() else {
                    break;
                };
                bytes -= oldest.len();
            }
            // Preserve the exact observations without retaining hundreds of
            // nested JSON objects and duplicated map-key allocations.
            history.push_back(body.to_vec());
        }
    }

    pub(super) fn daynight_exposure(&self, deadline: Instant) -> Result<Value, BackendError> {
        let reply = self.command(RaptorDaemon::Rvd, br#"{"cmd":"get-exposure"}"#, deadline)?;
        reply.require_only_fields(&[
            "status",
            "total_gain",
            "exposure_us",
            "ae_luma",
            "ev",
            "wb_rgain",
            "wb_bgain",
        ])?;
        require_ok(&reply)?;
        let mut current = std::collections::BTreeMap::new();
        for name in [
            "total_gain",
            "exposure_us",
            "ae_luma",
            "ev",
            "wb_rgain",
            "wb_bgain",
        ] {
            let value = reply
                .value
                .get_path(name)
                .ok_or(BackendError::Upstream(502))?;
            let Value::Number(raw) = value else {
                return Err(BackendError::Upstream(502));
            };
            let parsed = raw
                .parse::<f64>()
                .map_err(|_| BackendError::Upstream(502))?;
            if !parsed.is_finite() || parsed < 0.0 {
                return Err(BackendError::Upstream(502));
            }
            current.insert(name.to_owned(), value.clone());
        }
        let ae_luma = match current.get("ae_luma").unwrap() {
            Value::Number(raw) => raw.parse::<f64>().unwrap(),
            _ => unreachable!(),
        };
        if ae_luma > 255.0 {
            return Err(BackendError::Upstream(502));
        }
        current.insert(
            "brightness_pct".to_owned(),
            Value::Number(format!("{:.2}", ae_luma * 100.0 / 255.0)),
        );
        Ok(Value::Object(current))
    }

    pub(super) fn daynight_history(&self) -> Result<BackendResponse, BackendError> {
        let history = self
            .daynight_history
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        let mut body =
            Vec::with_capacity(history.iter().map(Vec::len).sum::<usize>() + history.len() + 2);
        body.push(b'[');
        for (index, sample) in history.iter().enumerate() {
            if index != 0 {
                body.push(b',');
            }
            body.extend_from_slice(sample);
        }
        body.push(b']');
        Ok(BackendResponse::json(body))
    }

    pub(super) fn daynight_sensors(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let thresholds = self
            .daynight_observation(deadline.min(Instant::now() + Duration::from_millis(150)))
            .and_then(|settings| threshold_observation(&settings))
            .ok()
            .flatten()
            .unwrap_or(Value::Null);
        let current = self
            .daynight_exposure(deadline.min(Instant::now() + Duration::from_millis(150)))
            .unwrap_or(Value::Null);
        let mut fields = std::collections::BTreeMap::new();
        fields.insert("source".to_owned(), Value::String("raptor".to_owned()));
        // RIC's luma, gain and photo thresholds are not the legacy Prudynt
        // percentage pair. Keep those two legacy fields explicitly absent.
        fields.insert("night_threshold_pct".to_owned(), Value::Null);
        fields.insert("day_threshold_pct".to_owned(), Value::Null);
        fields.insert("thresholds".to_owned(), thresholds);
        fields.insert("current".to_owned(), current);
        Ok(BackendResponse::json(
            Value::Object(fields).to_json().into_bytes(),
        ))
    }

    fn daynight_observation(&self, deadline: Instant) -> Result<RaptorReply, BackendError> {
        let reply = self.command(
            RaptorDaemon::Ric,
            br#"{"cmd":"get-daynight-settings"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-io") {
            return Err(BackendError::Unavailable);
        }
        let mode = mode_value(reply.value.get_path("mode"))?;
        let supported = reply
            .value
            .get_path("supported")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let available = reply
            .value
            .get_path("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if !service_enabled(&reply)? {
            if available {
                return Err(BackendError::Upstream(502));
            }
            for field in [
                "thresholds",
                "timing",
                "schedule",
                "sun",
                "startup",
                "automation",
                "automatic_outputs",
                "confirmation",
                "loglevel",
            ] {
                if reply.value.get_path(&format!("{field}.available")) == Some(&Value::Bool(true)) {
                    return Err(BackendError::Upstream(502));
                }
            }
        }
        let state = reply.value.get_path("state");
        if available {
            if !supported
                || !matches!(state.and_then(Value::as_str), Some("day" | "night"))
                || (mode != "auto" && state.and_then(Value::as_str) != Some(mode))
            {
                return Err(BackendError::Upstream(502));
            }
        } else if state != Some(&Value::Null) {
            return Err(BackendError::Upstream(502));
        }
        threshold_observation(&reply)?;
        timing_observation(&reply)?;
        schedule_observation(&reply)?;
        sun_observation(&reply)?;
        startup_observation(&reply)?;
        ir850_at_night_observation(&reply)?;
        automation_observation(&reply)?;
        automatic_outputs_observation(&reply)?;
        confirmation_observation(&reply)?;
        loglevel_observation(&reply)?;
        Ok(reply)
    }

    fn daynight_disk_section(&self, deadline: Instant) -> Result<RaptorReply, BackendError> {
        let disk = self.command(
            RaptorDaemon::Ric,
            br#"{"cmd":"config-read-section","section":"ircut"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        disk.require_only_fields(&["status", "section", "keys"])?;
        if disk.value.get_path("section").and_then(Value::as_str) != Some("ircut")
            || disk
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        Ok(disk)
    }

    pub(super) fn daynight_config(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let state = self.daynight_observation(deadline)?;
        let disk = self.daynight_disk_section(deadline).ok();
        let saved = disk
            .as_ref()
            .and_then(|disk| match disk.value.get_path("keys.mode") {
                None => Some("auto".to_owned()),
                value => mode_value(value).ok().map(str::to_owned),
            });
        let mode = mode_value(state.value.get_path("mode"))?;
        let available = state.value.get_path("available").and_then(Value::as_bool) == Some(true);
        let mut response = std::collections::BTreeMap::new();
        if let Some(mut automation) = automation_observation(&state)? {
            let live = automation
                .get_path("paused")
                .and_then(Value::as_bool)
                .unwrap();
            let saved = disk.as_ref().and_then(|disk| {
                saved_bool(
                    disk.value.get_path("keys").unwrap(),
                    "automation_paused",
                    false,
                    false,
                )
                .ok()
            });
            let Value::Object(ref mut fields) = automation else {
                unreachable!()
            };
            fields.insert(
                "saved_paused".into(),
                saved.map_or(Value::Null, Value::Bool),
            );
            fields.insert("matches_saved".into(), Value::Bool(saved == Some(live)));
            response.insert("automation".into(), automation);
        }
        if let Some(mut outputs) = automatic_outputs_observation(&state)? {
            let saved = disk.as_ref().and_then(|disk| {
                let keys = disk.value.get_path("keys").unwrap();
                Some(object([
                    (
                        "color",
                        Value::Bool(saved_bool(keys, "auto_color", true, false).ok()?),
                    ),
                    (
                        "ircut",
                        Value::Bool(saved_bool(keys, "auto_ircut", true, false).ok()?),
                    ),
                ]))
            });
            let matches = saved.as_ref().is_some_and(|saved| {
                saved.get_path("color") == outputs.get_path("color")
                    && saved.get_path("ircut") == outputs.get_path("ircut")
            });
            let Value::Object(ref mut fields) = outputs else {
                unreachable!()
            };
            fields.insert("saved_values".into(), saved.unwrap_or(Value::Null));
            fields.insert("matches_saved".into(), Value::Bool(matches));
            response.insert("automatic_outputs".into(), outputs);
        }
        if let Some(mut confirmation) = confirmation_observation(&state)? {
            let supported =
                confirmation.get_path("supported").and_then(Value::as_bool) == Some(true);
            let saved = if supported {
                disk.as_ref().and_then(|disk| {
                    let keys = disk.value.get_path("keys").unwrap();
                    let day = saved_confirmation_count(keys, "day_confirm_count", false).ok()?;
                    let night =
                        saved_confirmation_count(keys, "night_confirm_count", false).ok()?;
                    confirmation_values(&object([
                        ("day", Value::Number(day.to_string())),
                        ("night", Value::Number(night.to_string())),
                    ]))
                    .ok()
                })
            } else {
                None
            };
            let matches = saved.as_ref() == confirmation.get_path("values");
            let Value::Object(ref mut fields) = confirmation else {
                unreachable!()
            };
            fields.insert("saved_values".into(), saved.unwrap_or(Value::Null));
            fields.insert("matches_saved".into(), Value::Bool(matches));
            response.insert("confirmation".into(), confirmation);
        }
        if let Some(mut loglevel) = loglevel_observation(&state)? {
            let live = loglevel
                .get_path("value")
                .and_then(Value::as_str)
                .unwrap()
                .to_owned();
            let saved = disk.as_ref().and_then(|disk| {
                disk.value
                    .get_path("keys.loglevel")
                    .map_or(Some("info"), Value::as_str)
                    .filter(|value| valid_loglevel(value))
                    .map(str::to_owned)
            });
            let Value::Object(ref mut fields) = loglevel else {
                unreachable!()
            };
            fields.insert(
                "saved_value".into(),
                saved.clone().map_or(Value::Null, Value::String),
            );
            fields.insert(
                "matches_saved".into(),
                Value::Bool(saved.as_deref() == Some(live.as_str())),
            );
            response.insert("loglevel".into(), loglevel);
        }
        if let Some(live) = ir850_at_night_observation(&state)? {
            let saved = disk.as_ref().and_then(|disk| {
                saved_ir850_at_night(disk.value.get_path("keys").unwrap(), false).ok()
            });
            response.insert("ir850_at_night".into(), Value::Bool(live));
            response.insert(
                "saved_ir850_at_night".into(),
                saved.map_or(Value::Null, Value::Bool),
            );
            response.insert(
                "ir850_at_night_matches_saved".into(),
                Value::Bool(saved == Some(live)),
            );
        }
        if let Some(mut thresholds) = threshold_observation(&state)? {
            let trigger = thresholds
                .get_path("trigger")
                .and_then(Value::as_str)
                .unwrap();
            let saved_values = disk.as_ref().and_then(|disk| {
                if disk
                    .value
                    .get_path("keys.trigger")
                    .map_or(Some("luma"), Value::as_str)
                    != Some(trigger)
                {
                    return None;
                }
                threshold_values(trigger, disk.value.get_path("keys").unwrap(), true, true).ok()
            });
            let matches = thresholds.get_path("available").and_then(Value::as_bool) == Some(true)
                && saved_values.as_ref() == thresholds.get_path("values");
            let Value::Object(ref mut fields) = thresholds else {
                return Err(BackendError::Upstream(502));
            };
            fields.insert("saved_values".into(), saved_values.unwrap_or(Value::Null));
            fields.insert("matches_saved".into(), Value::Bool(matches));
            response.insert("thresholds".into(), thresholds);
        }
        if let Some(mut timing) = timing_observation(&state)? {
            let transition_supported = timing
                .get_path("transition_delay_supported")
                .and_then(Value::as_bool)
                .unwrap();
            let saved_values = disk.as_ref().and_then(|disk| {
                timing_values(
                    disk.value.get_path("keys").unwrap(),
                    true,
                    transition_supported,
                )
                .ok()
            });
            let matches = timing.get_path("available").and_then(Value::as_bool) == Some(true)
                && saved_values.as_ref().is_some_and(|saved| {
                    timing
                        .get_path("values")
                        .is_some_and(|live| requested_timing_matches(saved, live))
                });
            let Value::Object(ref mut fields) = timing else {
                return Err(BackendError::Upstream(502));
            };
            fields.insert("saved_values".into(), saved_values.unwrap_or(Value::Null));
            fields.insert("matches_saved".into(), Value::Bool(matches));
            response.insert("timing".into(), timing);
        }
        if let Some(mut schedule) = schedule_observation(&state)? {
            let saved_values = disk
                .as_ref()
                .and_then(|disk| schedule_values(disk.value.get_path("keys").unwrap(), true).ok());
            let matches = saved_values.as_ref() == schedule.get_path("values");
            let Value::Object(ref mut fields) = schedule else {
                return Err(BackendError::Upstream(502));
            };
            fields.insert("saved_values".into(), saved_values.unwrap_or(Value::Null));
            fields.insert("matches_saved".into(), Value::Bool(matches));
            response.insert("schedule".into(), schedule);
        }
        if let Some(mut sun) = sun_observation(&state)? {
            let saved_values = disk
                .as_ref()
                .and_then(|disk| sun_values(disk.value.get_path("keys").unwrap(), true).ok());
            let matches = saved_values.as_ref() == sun.get_path("values");
            let Value::Object(ref mut fields) = sun else {
                return Err(BackendError::Upstream(502));
            };
            fields.insert("saved_values".into(), saved_values.unwrap_or(Value::Null));
            fields.insert("matches_saved".into(), Value::Bool(matches));
            response.insert("sun".into(), sun);
        }
        if let Some(mut startup) = startup_observation(&state)? {
            let saved = disk.as_ref().and_then(|disk| {
                saved_initial_mode(disk.value.get_path("keys").unwrap(), false)
                    .ok()
                    .map(str::to_owned)
            });
            let configured = initial_mode_value(startup.get_path("configured"))?.to_owned();
            let boot_active = initial_mode_value(startup.get_path("boot_active"))?.to_owned();
            let Value::Object(ref mut fields) = startup else {
                return Err(BackendError::Upstream(502));
            };
            fields.insert(
                "saved".into(),
                saved.clone().map_or(Value::Null, Value::String),
            );
            fields.insert(
                "matches_saved".into(),
                Value::Bool(saved.as_deref() == Some(configured.as_str())),
            );
            fields.insert(
                "restart_required".into(),
                saved.as_deref().map_or(Value::Null, |saved| {
                    Value::Bool(saved != boot_active.as_str())
                }),
            );
            response.insert("startup".into(), startup);
        }
        response.insert("source".into(), Value::String("raptor".into()));
        response.insert("persistent".into(), Value::Bool(true));
        response.insert(
            "service_enabled".into(),
            Value::Bool(service_enabled(&state)?),
        );
        for key in ["supported", "available", "mode", "state"] {
            response.insert(key.into(), state.value.get_path(key).unwrap().clone());
        }
        response.insert(
            "matches_saved".into(),
            Value::Bool(available && saved.as_deref() == Some(mode)),
        );
        response.insert(
            "saved_mode".into(),
            saved.map_or(Value::Null, Value::String),
        );
        Ok(BackendResponse::json(
            Value::Object(response).to_json().into_bytes(),
        ))
    }

    pub(super) fn update_daynight_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        if request.as_object().is_none_or(|object| {
            object.is_empty()
                || object.len() > 11
                || object.keys().any(|key| {
                    key != "mode"
                        && key != "thresholds"
                        && key != "timing"
                        && key != "schedule"
                        && key != "sun"
                        && key != "ir850_at_night"
                        && key != "initial_mode"
                        && key != "automation"
                        && key != "automatic_outputs"
                        && key != "confirmation"
                        && key != "loglevel"
                })
        }) {
            return Err(BackendError::Protocol);
        }
        let mode = mode_value(request.get_path("mode")).map_err(|_| BackendError::Protocol)?;
        let initial_mode = request
            .get_path("initial_mode")
            .map(|value| {
                initial_mode_value(Some(value))
                    .map(str::to_owned)
                    .map_err(|_| BackendError::Protocol)
            })
            .transpose()?;
        let thresholds = request
            .get_path("thresholds")
            .map(|value| {
                if value.as_object().is_none_or(|object| object.len() != 2) {
                    return Err(BackendError::Protocol);
                }
                let trigger = value
                    .get_path("trigger")
                    .and_then(Value::as_str)
                    .ok_or(BackendError::Protocol)?;
                let values = threshold_values(
                    trigger,
                    value.get_path("values").ok_or(BackendError::Protocol)?,
                    false,
                    false,
                )
                .map_err(|_| BackendError::Protocol)?;
                Ok((trigger, values))
            })
            .transpose()?;
        let timing = request
            .get_path("timing")
            .map(requested_timing)
            .transpose()?;
        let schedule = request
            .get_path("schedule")
            .map(|value| schedule_values(value, false).map_err(|_| BackendError::Protocol))
            .transpose()?;
        let sun = request
            .get_path("sun")
            .map(|value| sun_values(value, false).map_err(|_| BackendError::Protocol))
            .transpose()?;
        let ir850_at_night = request
            .get_path("ir850_at_night")
            .map(|value| value.as_bool().ok_or(BackendError::Protocol))
            .transpose()?;
        let automation = request
            .get_path("automation")
            .map(|value| {
                let object = value.as_object().ok_or(BackendError::Protocol)?;
                if object.len() != 1 {
                    return Err(BackendError::Protocol);
                }
                object
                    .get("paused")
                    .and_then(Value::as_bool)
                    .ok_or(BackendError::Protocol)
            })
            .transpose()?;
        let automatic_outputs = request
            .get_path("automatic_outputs")
            .map(|value| {
                let fields = value.as_object().ok_or(BackendError::Protocol)?;
                if fields.len() != 2 {
                    return Err(BackendError::Protocol);
                }
                let color = fields
                    .get("color")
                    .and_then(Value::as_bool)
                    .ok_or(BackendError::Protocol)?;
                let ircut = fields
                    .get("ircut")
                    .and_then(Value::as_bool)
                    .ok_or(BackendError::Protocol)?;
                Ok(object([
                    ("color", Value::Bool(color)),
                    ("ircut", Value::Bool(ircut)),
                ]))
            })
            .transpose()?;
        let confirmation = request
            .get_path("confirmation")
            .map(|value| confirmation_values(value).map_err(|_| BackendError::Protocol))
            .transpose()?;
        let loglevel = request
            .get_path("loglevel")
            .map(|value| {
                let value = value.as_str().ok_or(BackendError::Protocol)?;
                if !valid_loglevel(value) {
                    return Err(BackendError::Protocol);
                }
                Ok(value.to_owned())
            })
            .transpose()?;
        let _mutation = self.lock_mutation()?;
        let before = self.daynight_observation(deadline)?;
        if before.value.get_path("supported").and_then(Value::as_bool) != Some(true)
            || !service_enabled(&before)?
        {
            return Err(BackendError::Unavailable);
        }
        if let Some((trigger, _)) = &thresholds {
            let observed = threshold_observation(&before)?.ok_or(BackendError::Unavailable)?;
            if observed.get_path("available").and_then(Value::as_bool) != Some(true)
                || observed.get_path("trigger").and_then(Value::as_str) != Some(*trigger)
            {
                return Err(BackendError::Unavailable);
            }
        }
        if let Some(requested) = &timing {
            let observed = timing_observation(&before)?.ok_or(BackendError::Unavailable)?;
            if observed.get_path("supported").and_then(Value::as_bool) != Some(true)
                || observed.get_path("available").and_then(Value::as_bool) != Some(true)
                || (requested.get_path("transition_delay_s").is_some()
                    && observed
                        .get_path("transition_delay_supported")
                        .and_then(Value::as_bool)
                        != Some(true))
            {
                return Err(BackendError::Unavailable);
            }
        }
        if schedule.is_some() {
            let observed = schedule_observation(&before)?.ok_or(BackendError::Unavailable)?;
            if observed.get_path("available").and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Unavailable);
            }
        }
        if sun.is_some() {
            let observed = sun_observation(&before)?.ok_or(BackendError::Unavailable)?;
            if observed.get_path("available").and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Unavailable);
            }
        }
        if ir850_at_night.is_some() && ir850_at_night_observation(&before)?.is_none() {
            return Err(BackendError::Unavailable);
        }
        if initial_mode.is_some() {
            let startup = startup_observation(&before)?.ok_or(BackendError::Unavailable)?;
            if startup.get_path("available").and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Unavailable);
            }
        }
        if automation.is_some()
            && automation_observation(&before)?
                .and_then(|value| value.get_path("available").and_then(Value::as_bool))
                != Some(true)
            || automatic_outputs.is_some()
                && automatic_outputs_observation(&before)?
                    .and_then(|value| value.get_path("available").and_then(Value::as_bool))
                    != Some(true)
            || confirmation.is_some()
                && confirmation_observation(&before)?
                    .and_then(|value| value.get_path("available").and_then(Value::as_bool))
                    != Some(true)
            || loglevel.is_some()
                && loglevel_observation(&before)?
                    .and_then(|value| value.get_path("available").and_then(Value::as_bool))
                    != Some(true)
        {
            return Err(BackendError::Unavailable);
        }
        let only_initial_mode = restart_only_initial_mode(
            &request,
            before.value.get_path("mode").and_then(Value::as_str),
        );
        if !only_initial_mode {
            self.timelapse_user_generation
                .fetch_add(1, Ordering::AcqRel);
        }
        let apply = || -> Result<(), BackendError> {
            if let Some((trigger, values)) = &thresholds {
                let command = format!(
                    r#"{{"cmd":"set-daynight-thresholds","trigger":"{trigger}","values":{}}}"#,
                    values.to_json()
                );
                require_ok(&self.command(RaptorDaemon::Ric, command.as_bytes(), deadline)?)?;
            }
            if !only_initial_mode {
                let mut fields = std::collections::BTreeMap::new();
                fields.insert("cmd".into(), Value::String("set-daynight-settings".into()));
                fields.insert("mode".into(), Value::String(mode.into()));
                if let Some(value) = &timing {
                    fields.insert("timing".into(), value.clone());
                }
                if let Some(value) = &schedule {
                    fields.insert("schedule".into(), value.clone());
                }
                if let Some(value) = &sun {
                    fields.insert("sun".into(), value.clone());
                }
                if let Some(value) = ir850_at_night {
                    fields.insert("ir850_at_night".into(), Value::Bool(value));
                }
                if let Some(value) = automation {
                    fields.insert(
                        "automation".into(),
                        object([("paused", Value::Bool(value))]),
                    );
                }
                if let Some(value) = &automatic_outputs {
                    fields.insert("automatic_outputs".into(), value.clone());
                }
                if let Some(value) = &confirmation {
                    fields.insert("confirmation".into(), value.clone());
                }
                if let Some(value) = &loglevel {
                    fields.insert("loglevel".into(), Value::String(value.clone()));
                }
                let command = Value::Object(fields).to_json();
                require_ok(&self.command(RaptorDaemon::Ric, command.as_bytes(), deadline)?)?;
            }
            if let Some(value) = &initial_mode {
                let command = format!(r#"{{"cmd":"set-daynight-initial-mode","value":"{value}"}}"#);
                require_ok(&self.command(RaptorDaemon::Ric, command.as_bytes(), deadline)?)?;
            }
            let state = self.daynight_observation(deadline)?;
            if state.value.get_path("available").and_then(Value::as_bool) != Some(true)
                || state.value.get_path("mode").and_then(Value::as_str) != Some(mode)
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some((trigger, values)) = &thresholds {
                let observed = threshold_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("available").and_then(Value::as_bool) != Some(true)
                    || observed.get_path("trigger").and_then(Value::as_str) != Some(*trigger)
                    || observed.get_path("values") != Some(values)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &timing {
                let observed = timing_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("available").and_then(Value::as_bool) != Some(true)
                    || !requested_timing_matches(
                        requested,
                        observed
                            .get_path("values")
                            .ok_or(BackendError::Upstream(502))?,
                    )
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &schedule {
                let observed = schedule_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("values") != Some(requested)
                    || observed.get_path("application_ok") != Some(&Value::Bool(true))
                    || observed.get_path("active")
                        != Some(&Value::Bool(
                            mode == "auto"
                                && requested.get_path("enabled") == Some(&Value::Bool(true)),
                        ))
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &sun {
                let observed = sun_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("values") != Some(requested)
                    || observed.get_path("application_ok") != Some(&Value::Bool(true))
                    || observed.get_path("active")
                        != Some(&Value::Bool(
                            mode == "auto"
                                && requested.get_path("enabled") == Some(&Value::Bool(true)),
                        ))
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if ir850_at_night.is_some() && ir850_at_night_observation(&state)? != ir850_at_night {
                return Err(BackendError::Upstream(502));
            }
            if let Some(requested) = automation {
                let observed = automation_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("paused").and_then(Value::as_bool) != Some(requested) {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &automatic_outputs {
                let observed =
                    automatic_outputs_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("color") != requested.get_path("color")
                    || observed.get_path("ircut") != requested.get_path("ircut")
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &confirmation {
                let observed =
                    confirmation_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("values") != Some(requested) {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &loglevel {
                let observed = loglevel_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("available") != Some(&Value::Bool(true))
                    || observed.get_path("value").and_then(Value::as_str)
                        != Some(requested.as_str())
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &initial_mode {
                let observed = startup_observation(&state)?.ok_or(BackendError::Unavailable)?;
                if observed.get_path("configured").and_then(Value::as_str)
                    != Some(requested.as_str())
                    || observed.get_path("boot_active")
                        != before.value.get_path("startup.boot_active")
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if !only_initial_mode {
                let paused = automation_observation(&state)?
                    .and_then(|value| value.get_path("paused").and_then(Value::as_bool))
                    .unwrap_or(false);
                let color_participates = automatic_outputs_observation(&state)?
                    .and_then(|value| value.get_path("color").and_then(Value::as_bool))
                    .unwrap_or(true);
                if mode != "auto" || !paused && color_participates {
                    let isp = self.command(
                        RaptorDaemon::Rvd,
                        br#"{"cmd":"get-running-mode"}"#,
                        deadline,
                    )?;
                    require_ok(&isp)?;
                    if isp.value.get_path("mode") != state.value.get_path("state") {
                        return Err(BackendError::Upstream(502));
                    }
                }
            }
            require_ok(&self.command(RaptorDaemon::Ric, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let disk = self.daynight_disk_section(deadline)?;
            if mode_value(disk.value.get_path("keys.mode"))? != mode {
                return Err(BackendError::Upstream(502));
            }
            if let Some((trigger, values)) = &thresholds
                && (disk
                    .value
                    .get_path("keys.trigger")
                    .map_or(Some("luma"), Value::as_str)
                    != Some(*trigger)
                    || threshold_values(
                        trigger,
                        disk.value.get_path("keys").unwrap(),
                        true,
                        false,
                    )? != *values)
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(requested) = &timing {
                let saved =
                    saved_timing_for_request(requested, disk.value.get_path("keys").unwrap())?;
                if !requested_timing_matches(requested, &saved) {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &schedule {
                let saved = schedule_values(disk.value.get_path("keys").unwrap(), true)?;
                if saved != *requested {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &sun {
                let saved = sun_values(disk.value.get_path("keys").unwrap(), true)?;
                if saved != *requested {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = ir850_at_night
                && saved_ir850_at_night(disk.value.get_path("keys").unwrap(), true)? != requested
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(requested) = automation
                && saved_bool(
                    disk.value.get_path("keys").unwrap(),
                    "automation_paused",
                    false,
                    true,
                )? != requested
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(requested) = &automatic_outputs {
                let keys = disk.value.get_path("keys").unwrap();
                if saved_bool(keys, "auto_color", true, true)?
                    != requested
                        .get_path("color")
                        .and_then(Value::as_bool)
                        .unwrap()
                    || saved_bool(keys, "auto_ircut", true, true)?
                        != requested
                            .get_path("ircut")
                            .and_then(Value::as_bool)
                            .unwrap()
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if let Some(requested) = &confirmation {
                let keys = disk.value.get_path("keys").unwrap();
                for (name, disk_key) in [
                    ("day", "day_confirm_count"),
                    ("night", "night_confirm_count"),
                ] {
                    let saved = saved_confirmation_count(keys, disk_key, true)?;
                    if Some(saved)
                        != requested
                            .get_path(name)
                            .and_then(|value| value_u64(value).ok())
                    {
                        return Err(BackendError::Upstream(502));
                    }
                }
            }
            if let Some(requested) = &loglevel
                && disk.value.get_path("keys.loglevel").and_then(Value::as_str)
                    != Some(requested.as_str())
            {
                return Err(BackendError::Upstream(502));
            }
            if let Some(requested) = &initial_mode
                && saved_initial_mode(disk.value.get_path("keys").unwrap(), true)? != requested
            {
                return Err(BackendError::Upstream(502));
            }
            if sun.is_some()
                || ir850_at_night.is_some()
                || initial_mode.is_some()
                || automation.is_some()
                || automatic_outputs.is_some()
                || confirmation.is_some()
                || loglevel.is_some()
            {
                let final_state = self.daynight_observation(deadline)?;
                if final_state.value.get_path("mode").and_then(Value::as_str) != Some(mode) {
                    return Err(BackendError::Upstream(502));
                }
                if let Some(requested) = &sun {
                    let observed =
                        sun_observation(&final_state)?.ok_or(BackendError::Unavailable)?;
                    if observed.get_path("values") != Some(requested)
                        || observed.get_path("application_ok") != Some(&Value::Bool(true))
                        || observed.get_path("active")
                            != Some(&Value::Bool(
                                mode == "auto"
                                    && requested.get_path("enabled") == Some(&Value::Bool(true)),
                            ))
                    {
                        return Err(BackendError::Upstream(502));
                    }
                }
                if ir850_at_night.is_some()
                    && ir850_at_night_observation(&final_state)? != ir850_at_night
                {
                    return Err(BackendError::Upstream(502));
                }
                if let Some(requested) = automation {
                    let observed =
                        automation_observation(&final_state)?.ok_or(BackendError::Unavailable)?;
                    if observed.get_path("paused").and_then(Value::as_bool) != Some(requested) {
                        return Err(BackendError::Upstream(502));
                    }
                }
                if let Some(requested) = &automatic_outputs {
                    let observed = automatic_outputs_observation(&final_state)?
                        .ok_or(BackendError::Unavailable)?;
                    if observed.get_path("color") != requested.get_path("color")
                        || observed.get_path("ircut") != requested.get_path("ircut")
                    {
                        return Err(BackendError::Upstream(502));
                    }
                }
                if let Some(requested) = &confirmation {
                    let observed =
                        confirmation_observation(&final_state)?.ok_or(BackendError::Unavailable)?;
                    if observed.get_path("values") != Some(requested) {
                        return Err(BackendError::Upstream(502));
                    }
                }
                if let Some(requested) = &loglevel {
                    let observed =
                        loglevel_observation(&final_state)?.ok_or(BackendError::Unavailable)?;
                    if observed.get_path("value").and_then(Value::as_str)
                        != Some(requested.as_str())
                    {
                        return Err(BackendError::Upstream(502));
                    }
                }
                if let Some(requested) = &initial_mode {
                    let observed =
                        startup_observation(&final_state)?.ok_or(BackendError::Unavailable)?;
                    if observed.get_path("configured").and_then(Value::as_str)
                        != Some(requested.as_str())
                        || observed.get_path("boot_active")
                            != before.value.get_path("startup.boot_active")
                    {
                        return Err(BackendError::Upstream(502));
                    }
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Day/night may have changed, but GPIO, ISP, save or readback was incomplete. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, serve_daemon, task_temp};
    use super::*;
    use std::{fs, io::Write, os::unix::net::UnixListener, thread};

    fn disabled_service_state() -> Vec<u8> {
        br#"{"status":"ok","persistence":"checked-io","service_enabled":false,"supported":true,"available":false,"mode":"auto","state":null,"automatic_outputs":{"supported":true,"available":false,"color":true,"ircut":true,"color_owner":"disabled","ircut_owner":"disabled","color_state":null,"ircut_commanded_state":null}}"#.to_vec()
    }

    #[test]
    fn disabled_service_preserves_support_and_saved_mode_without_live_state() {
        let root = task_temp("daynight-service-disabled-get");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), disabled_service_state()),
                (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(),
                 br#"{"status":"ok","section":"ircut","keys":{"enabled":"false","mode":"auto"}}"#.to_vec()),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_config(Instant::now() + Duration::from_secs(2))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("supported"), Some(&Value::Bool(true)));
        assert_eq!(value.get_path("service_enabled"), Some(&Value::Bool(false)));
        assert_eq!(value.get_path("available"), Some(&Value::Bool(false)));
        assert_eq!(value.get_path("state"), Some(&Value::Null));
        assert_eq!(
            value.get_path("saved_mode").and_then(Value::as_str),
            Some("auto")
        );
        assert_eq!(value.get_path("matches_saved"), Some(&Value::Bool(false)));
        assert_eq!(
            value
                .get_path("automatic_outputs.color_owner")
                .and_then(Value::as_str),
            Some("disabled")
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn disabled_service_rejects_update_before_any_mutation_command() {
        let root = task_temp("daynight-service-disabled-post");
        let daemon = serve_daemon(
            &root,
            "ric.sock",
            br#"{"cmd":"get-daynight-settings"}"#,
            &disabled_service_state(),
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
            br#"{"mode":"night"}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(matches!(result, Err(BackendError::Unavailable)));
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn service_observation_rejects_malformed_and_contradictory_state() {
        let valid = String::from_utf8(disabled_service_state()).unwrap();
        for raw in [
            valid.replace("\"service_enabled\":false", "\"service_enabled\":null"),
            valid.replace("\"service_enabled\":false", "\"service_enabled\":\"false\""),
            valid.replacen("\"available\":false", "\"available\":true", 1),
            valid.replace("\"color_owner\":\"disabled\"", "\"color_owner\":\"manual\""),
            valid.replace("\"color_state\":null", "\"color_state\":\"day\""),
            valid.replace("\"service_enabled\":false", "\"service_enabled\":true"),
            valid.replace(
                "\"automatic_outputs\":{\"supported\":true,\"available\":false",
                "\"automatic_outputs\":{\"supported\":true,\"available\":true",
            ),
        ] {
            let root = task_temp("daynight-service-invalid");
            let daemon = serve_daemon(
                &root,
                "ric.sock",
                br#"{"cmd":"get-daynight-settings"}"#,
                raw.as_bytes(),
            );
            assert!(
                backend(&root, "127.0.0.1:9".parse().unwrap())
                    .daynight_observation(Instant::now() + Duration::from_secs(2))
                    .is_err()
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    fn remaining_policy_state(
        paused: bool,
        color: bool,
        ircut: bool,
        day_count: u64,
        night_count: u64,
        loglevel: &str,
    ) -> Vec<u8> {
        format!(
            r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"day","automation":{{"supported":true,"available":true,"paused":{paused}}},"automatic_outputs":{{"supported":true,"available":true,"color":{color},"ircut":{ircut},"color_owner":"{}","ircut_owner":"{}","color_state":"day","ircut_commanded_state":"day"}},"confirmation":{{"supported":true,"available":true,"values":{{"day":{day_count},"night":{night_count}}}}},"loglevel":{{"supported":true,"available":true,"value":"{loglevel}"}}}}"#,
            if color { "automatic" } else { "manual" },
            if ircut { "automatic" } else { "manual" },
        )
        .into_bytes()
    }

    #[test]
    fn remaining_policies_require_live_disk_and_final_readback() {
        for fault in ["none", "live", "disk"] {
            let root = task_temp("daynight-remaining-policies");
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let before = remaining_policy_state(false, true, true, 1, 1, "info");
            let desired = remaining_policy_state(true, false, false, 3, 4, "debug");
            let live = if fault == "live" {
                remaining_policy_state(true, false, false, 3, 4, "info")
            } else {
                desired.clone()
            };
            let mut pairs = vec![
                (br#"{"cmd":"get-daynight-settings"}"#.to_vec(), before),
                (
                    br#"{"automatic_outputs":{"color":false,"ircut":false},"automation":{"paused":true},"cmd":"set-daynight-settings","confirmation":{"day":3,"night":4},"loglevel":"debug","mode":"auto"}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (br#"{"cmd":"get-daynight-settings"}"#.to_vec(), live),
            ];
            if fault != "live" {
                pairs.push((
                    br#"{"cmd":"config-save"}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ));
                pairs.push((
                    br#"{"cmd":"config-read-section","section":"ircut"}"#.to_vec(),
                    format!(
                        r#"{{"status":"ok","section":"ircut","keys":{{"mode":"auto","automation_paused":"true","auto_color":"{}","auto_ircut":"false","day_confirm_count":"3","night_confirm_count":"4","loglevel":"debug"}}}}"#,
                        if fault == "disk" { "true" } else { "false" },
                    )
                    .into_bytes(),
                ));
            }
            if fault == "none" {
                pairs.push((br#"{"cmd":"get-daynight-settings"}"#.to_vec(), desired));
            }
            let daemon = thread::spawn(move || {
                for (request, response) in pairs {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .update_daynight_config(
                    br#"{"mode":"auto","automation":{"paused":true},"automatic_outputs":{"color":false,"ircut":false},"confirmation":{"day":3,"night":4},"loglevel":"debug"}"#,
                    Instant::now() + Duration::from_secs(2),
                );
            assert_eq!(result.is_ok(), fault == "none", "{fault}: {result:?}");
            if fault != "none" {
                assert!(matches!(result, Err(BackendError::PartialApply(_))));
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn remaining_policy_disk_defaults_require_absent_keys() {
        for (keys, valid_defaults) in [
            (r#"{"mode":"auto"}"#, true),
            (
                r#"{"mode":"auto","automation_paused":17,"auto_color":17,"auto_ircut":null,"day_confirm_count":17,"night_confirm_count":null,"loglevel":17}"#,
                false,
            ),
        ] {
            let root = task_temp("daynight-remaining-policy-disk");
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let state = remaining_policy_state(false, true, true, 1, 1, "info");
            let disk = format!(r#"{{"status":"ok","section":"ircut","keys":{keys}}}"#).into_bytes();
            let daemon = thread::spawn(move || {
                for (request, response) in [
                    (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), state),
                    (
                        br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(),
                        disk,
                    ),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .daynight_config(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(
                value.get_path("automation.saved_paused").cloned(),
                Some(if valid_defaults {
                    Value::Bool(false)
                } else {
                    Value::Null
                })
            );
            assert_eq!(
                value
                    .get_path("automatic_outputs.saved_values.color")
                    .cloned(),
                if valid_defaults {
                    Some(Value::Bool(true))
                } else {
                    None
                }
            );
            assert_eq!(
                value.get_path("confirmation.saved_values.day").cloned(),
                if valid_defaults {
                    Some(Value::Number("1".into()))
                } else {
                    None
                }
            );
            assert_eq!(
                value.get_path("loglevel.saved_value").cloned(),
                Some(if valid_defaults {
                    Value::String("info".into())
                } else {
                    Value::Null
                })
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    fn startup_state(configured: &str, boot_active: &str) -> Vec<u8> {
        format!(
            r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"day","startup":{{"supported":true,"available":true,"configured":"{configured}","boot_active":"{boot_active}"}}}}"#
        )
        .into_bytes()
    }

    #[test]
    fn initial_mode_save_is_restart_only_and_never_contacts_rvd() {
        let root = task_temp("daynight-initial-mode-save");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (
                    br#"{"cmd":"get-daynight-settings"}"#.as_slice(),
                    startup_state("default", "default"),
                ),
                (
                    br#"{"cmd":"set-daynight-initial-mode","value":"night"}"#.as_slice(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"get-daynight-settings"}"#.as_slice(),
                    startup_state("night", "default"),
                ),
                (
                    br#"{"cmd":"config-save"}"#.as_slice(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(),
                    br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","initial_mode":"night"}}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"get-daynight-settings"}"#.as_slice(),
                    startup_state("night", "default"),
                ),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let camera = backend(&root, "127.0.0.1:9".parse().unwrap());
        let generation = camera.timelapse_user_generation.load(Ordering::Acquire);
        let response = camera
            .update_daynight_config(
                br#"{"mode":"auto","initial_mode":"night"}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        assert_eq!(
            camera.timelapse_user_generation.load(Ordering::Acquire),
            generation
        );
        assert_eq!(
            crate::json::parse(&response.body)
                .unwrap()
                .get_path("persistent"),
            Some(&Value::Bool(true))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn initial_mode_combined_with_live_policy_is_not_restart_only() {
        let initial_only =
            crate::json::parse(br#"{"mode":"auto","initial_mode":"night"}"#).unwrap();
        let combined = crate::json::parse(
            br#"{"mode":"auto","initial_mode":"night","schedule":{"enabled":true,"start_at":"20:00","stop_at":"06:00"}}"#,
        )
        .unwrap();
        assert!(restart_only_initial_mode(&initial_only, Some("auto")));
        assert!(!restart_only_initial_mode(&initial_only, Some("day")));
        assert!(!restart_only_initial_mode(&combined, Some("auto")));
    }

    #[test]
    fn initial_mode_get_separates_configured_saved_and_boot_active() {
        let root = task_temp("daynight-initial-mode-get");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (
                    br#"{"cmd":"get-daynight-settings"}"#.as_slice(),
                    startup_state("night", "default"),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(),
                    br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","initial_mode":"night"}}"#.to_vec(),
                ),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("startup.configured"),
            Some(&Value::String("night".into()))
        );
        assert_eq!(
            value.get_path("startup.saved"),
            Some(&Value::String("night".into()))
        );
        assert_eq!(
            value.get_path("startup.boot_active"),
            Some(&Value::String("default".into()))
        );
        assert_eq!(
            value.get_path("startup.matches_saved"),
            Some(&Value::Bool(true))
        );
        assert_eq!(
            value.get_path("startup.restart_required"),
            Some(&Value::Bool(true))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn initial_mode_get_keeps_restart_requirement_unknown_without_disk_readback() {
        let root = task_temp("daynight-initial-mode-get-no-disk");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (
                    br#"{"cmd":"get-daynight-settings"}"#.as_slice(),
                    startup_state("night", "default"),
                ),
                (
                    br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(),
                    br#"{"status":"error"}"#.to_vec(),
                ),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("startup.saved"), Some(&Value::Null));
        assert_eq!(
            value.get_path("startup.matches_saved"),
            Some(&Value::Bool(false))
        );
        assert_eq!(
            value.get_path("startup.restart_required"),
            Some(&Value::Null)
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn adc_threshold_shape_is_ordered_and_bounded() {
        let values = crate::json::parse(br#"{"adc_night":200,"adc_day":600}"#).unwrap();
        assert_eq!(
            threshold_values("adc", &values, false, false).unwrap(),
            values
        );
        for invalid in [
            br#"{"adc_night":600,"adc_day":200}"#.as_slice(),
            br#"{"adc_night":200.5,"adc_day":600}"#.as_slice(),
            br#"{"adc_night":200,"adc_day":600,"adc_channel":0}"#.as_slice(),
        ] {
            assert!(
                threshold_values("adc", &crate::json::parse(invalid).unwrap(), false, false)
                    .is_err()
            );
        }
    }

    #[test]
    fn schedule_save_requires_runtime_application_and_disk_values() {
        let root = task_temp("daynight-schedule");
        let observation = |enabled: bool| {
            format!(r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"night","schedule":{{"supported":true,"available":true,"values":{{"enabled":{enabled},"start_at":"20:00","stop_at":"06:00"}},"active":{enabled},"target":{},"application_ok":true}}}}"#, if enabled { "\"night\"" } else { "null" }).into_bytes()
        };
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), observation(false)),
                (br#"{"cmd":"set-daynight-settings","mode":"auto","schedule":{"enabled":true,"start_at":"20:00","stop_at":"06:00"}}"#.as_slice(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), observation(true)),
                (br#"{"cmd":"config-save"}"#.as_slice(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(), br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","schedule_enabled":"true","schedule_start":"20:00","schedule_stop":"06:00"}}"#.to_vec()),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let isp = serve_daemon(
            &root,
            "rvd.sock",
            br#"{"cmd":"get-running-mode"}"#,
            br#"{"status":"ok","mode":"night"}"#,
        );
        let camera = backend(&root, "127.0.0.1:9".parse().unwrap());
        let generation = camera.timelapse_user_generation.load(Ordering::Acquire);
        let result = camera.update_daynight_config(
            br#"{"mode":"auto","schedule":{"enabled":true,"start_at":"20:00","stop_at":"06:00"}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        assert_eq!(
            camera.timelapse_user_generation.load(Ordering::Acquire),
            generation.wrapping_add(1)
        );
        daemon.join().unwrap();
        isp.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ir850_night_policy_requires_live_disk_and_final_live_values() {
        for fault in ["none", "live", "save", "disk", "final"] {
            let root = task_temp("daynight-ir850-policy");
            let observation = |enabled: bool| {
                format!(r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"night","state":"night","ir850_at_night":{enabled}}}"#).into_bytes()
            };
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let mut pairs = vec![
                (
                    br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                    observation(true),
                ),
                (
                    br#"{"cmd":"set-daynight-settings","ir850_at_night":false,"mode":"night"}"#
                        .to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                    observation(fault == "live"),
                ),
            ];
            if fault != "live" {
                pairs.push((
                    br#"{"cmd":"config-save"}"#.to_vec(),
                    if fault == "save" {
                        br#"{"status":"error"}"#.to_vec()
                    } else {
                        br#"{"status":"ok"}"#.to_vec()
                    },
                ));
            }
            if fault != "live" && fault != "save" {
                pairs.push((br#"{"cmd":"config-read-section","section":"ircut"}"#.to_vec(),
                    format!(r#"{{"status":"ok","section":"ircut","keys":{{"mode":"night","ir850":"{}"}}}}"#,
                        if fault == "disk" { "true" } else { "false" }).into_bytes()));
            }
            if fault == "none" || fault == "final" {
                pairs.push((
                    br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                    observation(fault == "final"),
                ));
            }
            let daemon = thread::spawn(move || {
                for (request, response) in pairs {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let isp = if fault == "live" {
                None
            } else {
                Some(serve_daemon(
                    &root,
                    "rvd.sock",
                    br#"{"cmd":"get-running-mode"}"#,
                    br#"{"status":"ok","mode":"night"}"#,
                ))
            };
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
                br#"{"mode":"night","ir850_at_night":false}"#,
                Instant::now() + Duration::from_secs(2),
            );
            assert_eq!(result.is_ok(), fault == "none", "{fault}: {result:?}");
            if fault != "none" {
                assert!(matches!(result, Err(BackendError::PartialApply(_))));
            }
            daemon.join().unwrap();
            if let Some(isp) = isp {
                isp.join().unwrap();
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn ir850_night_policy_disk_default_is_only_for_observation() {
        let empty = crate::json::parse(br#"{}"#).unwrap();
        assert!(saved_ir850_at_night(&empty, false).unwrap());
        assert!(saved_ir850_at_night(&empty, true).is_err());
        for invalid in [
            br#"{"ir850":true}"#.as_slice(),
            br#"{"ir850":"yes"}"#.as_slice(),
        ] {
            assert!(saved_ir850_at_night(&crate::json::parse(invalid).unwrap(), false).is_err());
        }
    }

    #[test]
    fn ir850_night_policy_get_separates_live_and_saved_values() {
        for (disk_key, saved, matches) in [
            ("", Some(true), true),
            (r#", "ir850":"false""#, Some(false), false),
            (r#", "ir850":"invalid""#, None, false),
        ] {
            let root = task_temp("daynight-ir850-get");
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let disk = format!(
                r#"{{"status":"ok","section":"ircut","keys":{{"mode":"night"{disk_key}}}}}"#
            )
            .into_bytes();
            let daemon = thread::spawn(move || {
                for (request, response) in [
                    (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), br#"{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"night","state":"night","ir850_at_night":true}"#.to_vec()),
                    (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(), disk),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .daynight_config(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(value.get_path("ir850_at_night"), Some(&Value::Bool(true)));
            let expected_saved = saved.map_or(Value::Null, Value::Bool);
            assert_eq!(
                value.get_path("saved_ir850_at_night"),
                Some(&expected_saved)
            );
            assert_eq!(
                value.get_path("ir850_at_night_matches_saved"),
                Some(&Value::Bool(matches))
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn public_contract_names_startup_and_ir850_policy_semantics() {
        let contract = crate::json::parse(include_bytes!(
            "../../../../contracts/thingino-control-api-v1.json"
        ))
        .unwrap();
        let endpoint = contract
            .get_path("routes")
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .find(|entry| entry.get_path("id").and_then(Value::as_str) == Some("config.domain"))
            .unwrap();
        assert_eq!(
            endpoint
                .get_path("request_schema.semantics")
                .and_then(Value::as_object)
                .and_then(|semantics| semantics.get("raptor.daynight_ir850_at_night"))
                .and_then(|policy| policy.get_path("type"))
                .and_then(Value::as_str),
            Some("boolean")
        );
        let initial_modes = endpoint
            .get_path("request_schema.semantics")
            .and_then(Value::as_object)
            .and_then(|semantics| semantics.get("raptor.daynight_initial_mode"))
            .and_then(|policy| policy.get_path("enum"))
            .and_then(Value::as_array)
            .unwrap();
        assert_eq!(
            initial_modes,
            &[
                Value::String("default".into()),
                Value::String("day".into()),
                Value::String("night".into()),
            ]
        );
        assert!(
            endpoint
                .get_path("response_schema.oneOf")
                .and_then(Value::as_array)
                .unwrap()
                .iter()
                .filter_map(|shape| shape.get_path("semantics").and_then(Value::as_str))
                .any(|semantics| semantics.contains("restart_required is null"))
        );
        let fields = endpoint
            .get_path("response_schema.oneOf")
            .and_then(Value::as_array)
            .unwrap()
            .iter()
            .find_map(|shape| shape.get_path("ir850_policy.fields"))
            .unwrap();
        for key in [
            "ir850_at_night",
            "saved_ir850_at_night",
            "ir850_at_night_matches_saved",
        ] {
            assert!(fields.get_path(key).and_then(Value::as_str).is_some());
        }
    }

    #[test]
    fn solar_high_precision_save_requires_live_disk_and_final_live_values() {
        for fault in ["none", "save", "disk", "final"] {
            let root = task_temp("daynight-sun");
            let observation = |enabled: bool, latitude: &str| {
                format!(r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"day","schedule":{{"supported":true,"available":true,"values":{{"enabled":false,"start_at":"07:00","stop_at":"19:00"}},"active":false,"target":null,"application_ok":true}},"sun":{{"supported":true,"available":true,"values":{{"enabled":{enabled},"latitude":{latitude},"longitude":24.9384567,"sunrise_offset":15,"sunset_offset":-20}},"active":{enabled},"target":{},"condition":{},"application_ok":true}}}}"#,
                    if enabled { "\"day\"" } else { "null" },
                    if enabled { "\"normal\"" } else { "null" }).into_bytes()
            };
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            let final_latitude = if fault == "final" {
                "61.0"
            } else {
                "60.1699123"
            };
            let mut pairs = vec![
                (br#"{"cmd":"get-daynight-settings"}"#.to_vec(), observation(false, "60.1699123")),
                (br#"{"cmd":"set-daynight-settings","mode":"auto","sun":{"enabled":true,"latitude":60.1699123,"longitude":24.9384567,"sunrise_offset":15,"sunset_offset":-20}}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-daynight-settings"}"#.to_vec(), observation(true, "60.1699123")),
                (br#"{"cmd":"config-save"}"#.to_vec(), if fault == "save" { br#"{"status":"error"}"#.to_vec() } else { br#"{"status":"ok"}"#.to_vec() }),
            ];
            if fault != "save" {
                pairs.push((br#"{"cmd":"config-read-section","section":"ircut"}"#.to_vec(),
                    format!(r#"{{"status":"ok","section":"ircut","keys":{{"mode":"auto","sun_enabled":"true","sun_latitude":"{}","sun_longitude":"24.9384567","sunrise_offset":"15","sunset_offset":"-20"}}}}"#,
                        if fault == "disk" { "61" } else { "60.1699123" }).into_bytes()));
            }
            if fault != "save" && fault != "disk" {
                pairs.push((
                    br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                    observation(true, final_latitude),
                ));
            }
            let started = Instant::now();
            let daemon = thread::spawn(move || {
                for (index, (request, response)) in pairs.into_iter().enumerate() {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket
                        .write_all(&framed(&response))
                        .unwrap_or_else(|error| {
                            panic!(
                                "solar fixture fault={fault} reply={index} elapsed={:?}: {error}",
                                started.elapsed()
                            );
                        });
                }
            });
            let isp = serve_daemon(
                &root,
                "rvd.sock",
                br#"{"cmd":"get-running-mode"}"#,
                br#"{"status":"ok","mode":"day"}"#,
            );
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
                br#"{"mode":"auto","sun":{"enabled":true,"latitude":60.1699123,"longitude":24.9384567,"sunrise_offset":15,"sunset_offset":-20}}"#,
                Instant::now() + Duration::from_secs(2));
            assert_eq!(result.is_ok(), fault == "none", "{fault}: {result:?}");
            if fault != "none" {
                assert!(matches!(result, Err(BackendError::PartialApply(_))));
            }
            daemon.join().unwrap();
            isp.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn solar_unknown_target_requires_an_explicit_null_condition() {
        for (condition, valid) in [("null", true), ("42", false), ("false", false)] {
            let body = format!(r#"{{"sun":{{"supported":true,"available":true,"active":false,"application_ok":true,"values":{{"enabled":false,"latitude":0,"longitude":0,"sunrise_offset":0,"sunset_offset":0}},"target":null,"condition":{condition}}}}}"#).into_bytes();
            let root = task_temp("daynight-sun-condition");
            let daemon = serve_daemon(
                &root,
                "ric.sock",
                br#"{"cmd":"get-daynight-settings"}"#,
                &body,
            );
            let reply = backend(&root, "127.0.0.1:9".parse().unwrap())
                .command(
                    RaptorDaemon::Ric,
                    br#"{"cmd":"get-daynight-settings"}"#,
                    Instant::now() + Duration::from_secs(2),
                )
                .unwrap();
            assert_eq!(
                sun_observation(&reply).is_ok(),
                valid,
                "condition={condition}"
            );
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn solar_values_reject_nonfinite_ranges_and_fractional_offsets() {
        for body in [
            br#"{"enabled":true,"latitude":91,"longitude":0,"sunrise_offset":0,"sunset_offset":0}"#.as_slice(),
            br#"{"enabled":true,"latitude":0,"longitude":181,"sunrise_offset":0,"sunset_offset":0}"#.as_slice(),
            br#"{"enabled":true,"latitude":0,"longitude":0,"sunrise_offset":1441,"sunset_offset":0}"#.as_slice(),
            br#"{"enabled":true,"latitude":0,"longitude":0,"sunrise_offset":0.5,"sunset_offset":0}"#.as_slice(),
        ] {
            assert!(sun_values(&crate::json::parse(body).unwrap(), false).is_err());
        }
    }

    #[test]
    fn solar_restart_observation_matches_independently_saved_config() {
        let root = task_temp("daynight-sun-restart");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let observation = br#"{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"night","schedule":{"supported":true,"available":true,"values":{"enabled":false,"start_at":"07:00","stop_at":"19:00"},"active":false,"target":null,"application_ok":true},"sun":{"supported":true,"available":true,"values":{"enabled":true,"latitude":69.6492,"longitude":18.9553,"sunrise_offset":15,"sunset_offset":-20},"active":true,"target":"night","condition":"polar_night","application_ok":true}}"#.to_vec();
        let disk = br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","sun_enabled":"true","sun_latitude":"69.649200","sun_longitude":"18.955300","sunrise_offset":"15","sunset_offset":"-20"}}"#.to_vec();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (
                    br#"{"cmd":"get-daynight-settings"}"#.as_slice(),
                    observation,
                ),
                (
                    br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(),
                    disk,
                ),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let body = crate::json::parse(&response.body).unwrap();
        assert_eq!(body.get_path("sun.matches_saved"), Some(&Value::Bool(true)));
        assert_eq!(
            body.get_path("sun.target").and_then(Value::as_str),
            Some("night")
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn active_schedule_keeps_clock_failure_visible_but_cannot_complete_save() {
        let failed = br#"{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"night","schedule":{"supported":true,"available":true,"values":{"enabled":true,"start_at":"20:00","stop_at":"06:00"},"active":true,"target":null,"application_ok":false}}"#.to_vec();

        let root = task_temp("daynight-schedule-clock-get");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let get_daemon = thread::spawn({
            let failed = failed.clone();
            move || {
                for (request, response) in [
                    (br#"{"cmd":"mode","value":"auto"}"#.as_slice(), br#"{"status":"ok","mode":"auto","state":"night"}"#.to_vec()),
                    (br#"{"cmd":"mode"}"#.as_slice(), br#"{"status":"ok","mode":"auto","state":"night"}"#.to_vec()),
                    (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), failed),
                    (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(), br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","schedule_enabled":"true","schedule_start":"20:00","schedule_stop":"06:00"}}"#.to_vec()),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            }
        });
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        adapter
            .set_daynight_mode(DayNightMode::Auto, Instant::now() + Duration::from_secs(1))
            .unwrap();
        let response = adapter
            .daynight_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("schedule.active"), Some(&Value::Bool(true)));
        assert_eq!(value.get_path("schedule.target"), Some(&Value::Null));
        assert_eq!(
            value.get_path("schedule.application_ok"),
            Some(&Value::Bool(false))
        );
        get_daemon.join().unwrap();
        fs::remove_dir_all(&root).unwrap();

        let root = task_temp("daynight-schedule-clock-save");
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let save_daemon = thread::spawn(move || {
            let before = br#"{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"night","schedule":{"supported":true,"available":true,"values":{"enabled":false,"start_at":"20:00","stop_at":"06:00"},"active":false,"target":null,"application_ok":true}}"#.to_vec();
            for (request, response) in [
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), before),
                (br#"{"cmd":"set-daynight-settings","mode":"auto","schedule":{"enabled":true,"start_at":"20:00","stop_at":"06:00"}}"#.as_slice(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), failed),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
            br#"{"mode":"auto","schedule":{"enabled":true,"start_at":"20:00","stop_at":"06:00"}}"#,
            Instant::now() + Duration::from_secs(1),
        );
        assert!(
            matches!(result, Err(BackendError::PartialApply(_))),
            "{result:?}"
        );
        save_daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    fn observation(mode: &str, available: bool) -> Vec<u8> {
        format!(r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":{available},"mode":"{mode}","state":{}}}"#,
            if available { format!("\"{}\"", if mode == "night" { "night" } else { "day" }) } else { "null".into() }).into_bytes()
    }

    #[test]
    fn sensor_exposure_remains_visible_when_ric_is_unavailable() {
        let root = task_temp("sensor-without-ric");
        let daemon = serve_daemon(
            &root,
            "rvd.sock",
            br#"{"cmd":"get-exposure"}"#,
            br#"{"status":"ok","total_gain":17.5,"exposure_us":1000,"ae_luma":127.5,"ev":22,"wb_rgain":256,"wb_bgain":257}"#,
        );
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_sensors(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("thresholds"), Some(&Value::Null));
        assert_eq!(value.get_path("night_threshold_pct"), Some(&Value::Null));
        assert_eq!(
            value.get_path("current.brightness_pct"),
            Some(&Value::Number("50.00".into()))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn exposure_is_checked_and_converted_without_inventing_missing_values() {
        for (reply, accepted) in [
            (
                br#"{"status":"ok","total_gain":17.5,"exposure_us":1000,"ae_luma":127.5,"ev":22,"wb_rgain":256,"wb_bgain":257}"#.as_slice(),
                true,
            ),
            (
                br#"{"status":"error","total_gain":0,"exposure_us":0,"ae_luma":0,"ev":0,"wb_rgain":0,"wb_bgain":0}"#.as_slice(),
                false,
            ),
            (
                br#"{"status":"ok","total_gain":17.5,"exposure_us":1000,"ae_luma":256,"ev":22,"wb_rgain":256,"wb_bgain":257}"#.as_slice(),
                false,
            ),
        ] {
            let root = task_temp("daynight-exposure");
            let daemon = serve_daemon(
                &root,
                "rvd.sock",
                br#"{"cmd":"get-exposure"}"#,
                reply,
            );
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .daynight_exposure(Instant::now() + Duration::from_secs(1));
            assert_eq!(result.is_ok(), accepted, "{result:?}");
            if let Ok(value) = result {
                assert_eq!(
                    value.get_path("brightness_pct"),
                    Some(&Value::Number("50.00".to_owned()))
                );
                assert_eq!(
                    value.get_path("total_gain"),
                    Some(&Value::Number("17.5".to_owned()))
                );
            }
            daemon.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn history_limits_encoded_bytes_and_samples_without_changing_observations() {
        let root = task_temp("daynight-history-bounds");
        let camera = backend(&root, "127.0.0.1:9".parse().unwrap());
        for sample in 0..305 {
            camera.retain_daynight_sample(format!(r#"{{"time_now":{sample}}}"#).as_bytes());
        }
        let response = camera.daynight_history().unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        let samples = value.as_array().unwrap();
        assert_eq!(samples.len(), 300);
        assert_eq!(
            samples[0].get_path("time_now"),
            Some(&Value::Number("5".into()))
        );
        let large = format!(r#"{{"payload":"{}"}}"#, "x".repeat(4096));
        for _ in 0..60 {
            camera.retain_daynight_sample(large.as_bytes());
        }
        let response = camera.daynight_history().unwrap();
        assert!(response.body.len() <= crate::MAX_BACKEND_RESPONSE_BYTES);
        assert!(
            crate::json::parse(&response.body)
                .unwrap()
                .as_array()
                .unwrap()
                .len()
                < 300
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn empty_raptor_history_is_a_supported_bounded_result() {
        let root = task_temp("daynight-history");
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_history()
            .unwrap();
        assert_eq!(response.body, b"[]");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn daynight_persistence_requires_gpio_isp_and_disk_confirmation() {
        for mode in ["auto", "day", "night"] {
            for fault in ["none", "setter", "state", "isp", "save", "disk", "missing"] {
                let root = task_temp("daynight-persist");
                let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
                listener.set_nonblocking(true).unwrap();
                let mut pairs = vec![
                    (
                        br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                        observation("auto", false),
                    ),
                    (
                        format!(r#"{{"cmd":"set-daynight-settings","mode":"{mode}"}}"#)
                            .into_bytes(),
                        if fault == "setter" {
                            br#"{"status":"error"}"#.to_vec()
                        } else {
                            br#"{"status":"ok"}"#.to_vec()
                        },
                    ),
                ];
                if fault != "setter" {
                    pairs.push((
                        br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                        observation(mode, fault != "state"),
                    ));
                    if fault != "state" && fault != "isp" {
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
                                br#"{"cmd":"config-read-section","section":"ircut"}"#.to_vec(),
                                format!(
                                    r#"{{"status":"ok","section":"ircut","keys":{{{}}}}}"#,
                                    if fault == "missing" {
                                        "".into()
                                    } else {
                                        format!(
                                            r#""mode":"{}""#,
                                            if fault == "disk" { "invalid" } else { mode }
                                        )
                                    }
                                )
                                .into_bytes(),
                            ));
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
                                    if error.kind() == io::ErrorKind::WouldBlock
                                        && Instant::now() < deadline =>
                                {
                                    thread::sleep(Duration::from_millis(2))
                                }
                                other => panic!("missing RIC request: {other:?}"),
                            }
                        };
                        socket
                            .set_read_timeout(Some(Duration::from_secs(1)))
                            .unwrap();
                        assert_eq!(read_request(&mut socket), request);
                        socket.write_all(&framed(&response)).unwrap();
                    }
                });
                let isp = if fault != "setter" && fault != "state" {
                    Some(serve_daemon(
                        &root,
                        "rvd.sock",
                        br#"{"cmd":"get-running-mode"}"#,
                        format!(
                            r#"{{"status":"ok","mode":"{}"}}"#,
                            if (mode == "night") != (fault == "isp") {
                                "night"
                            } else {
                                "day"
                            }
                        )
                        .as_bytes(),
                    ))
                } else {
                    None
                };
                let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
                    format!(r#"{{"mode":"{mode}"}}"#).as_bytes(),
                    Instant::now() + Duration::from_secs(2),
                );
                if fault == "none" {
                    assert!(result.is_ok(), "{result:?}");
                } else {
                    assert!(
                        matches!(result, Err(BackendError::PartialApply(_))),
                        "{mode}/{fault}: {result:?}"
                    );
                }
                daemon.join().unwrap();
                if let Some(isp) = isp {
                    isp.join().unwrap();
                }
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn timing_requires_runtime_disk_and_elapsed_semantics_contract() {
        for body in [
            br#"{"mode":"auto","timing":{}}"#.as_slice(),
            br#"{"mode":"auto","timing":{"sample_interval_ms":49}}"#.as_slice(),
            br#"{"mode":"auto","timing":{"sample_interval_ms":500.5}}"#.as_slice(),
            br#"{"mode":"auto","timing":{"transition_delay_s":301}}"#.as_slice(),
            br#"{"mode":"auto","timing":{"sample_interval_ms":500,"other":1}}"#.as_slice(),
        ] {
            let root = task_temp("daynight-invalid-timing");
            assert!(matches!(
                backend(&root, "127.0.0.1:9".parse().unwrap())
                    .update_daynight_config(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
            fs::remove_dir_all(root).unwrap();
        }

        let requested =
            crate::json::parse(br#"{"sample_interval_ms":1000,"transition_delay_s":5}"#).unwrap();
        for disk in [
            br#"{"hysteresis_sec":"5"}"#.as_slice(),
            br#"{"poll_interval_ms":"1000"}"#.as_slice(),
        ] {
            assert!(
                saved_timing_for_request(&requested, &crate::json::parse(disk).unwrap()).is_err()
            );
        }

        let root = task_temp("daynight-timing");
        let observed = |sample: u32, delay: u32| {
            format!(r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"day","timing":{{"supported":true,"available":true,"transition_delay_supported":true,"values":{{"sample_interval_ms":{sample},"transition_delay_s":{delay}}}}}}}"#).into_bytes()
        };
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        let pairs = vec![
            (br#"{"cmd":"get-daynight-settings"}"#.to_vec(), observed(1_000, 5)),
            (br#"{"cmd":"set-daynight-settings","mode":"auto","timing":{"sample_interval_ms":500,"transition_delay_s":7}}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (br#"{"cmd":"get-daynight-settings"}"#.to_vec(), observed(500, 7)),
            (br#"{"cmd":"config-save"}"#.to_vec(), br#"{"status":"ok"}"#.to_vec()),
            (br#"{"cmd":"config-read-section","section":"ircut"}"#.to_vec(), br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","poll_interval_ms":"500","hysteresis_sec":"7"}}"#.to_vec()),
        ];
        let daemon = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for (request, response) in pairs {
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing RIC timing request: {other:?}"),
                    }
                };
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let isp = serve_daemon(
            &root,
            "rvd.sock",
            br#"{"cmd":"get-running-mode"}"#,
            br#"{"status":"ok","mode":"day"}"#,
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
            br#"{"mode":"auto","timing":{"sample_interval_ms":500,"transition_delay_s":7}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        daemon.join().unwrap();
        isp.join().unwrap();

        fs::remove_file(root.join("ric.sock")).unwrap();
        let ric = UnixListener::bind(root.join("ric.sock")).unwrap();
        let disk = thread::spawn(move || {
            for (request, response) in [
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), observed(500, 7)),
                (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(), br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","poll_interval_ms":"500","hysteresis_sec":"7"}}"#.to_vec()),
            ] {
                let (mut socket, _) = ric.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("timing.values.transition_delay_s"),
            Some(&Value::Number("7".into()))
        );
        assert_eq!(
            value.get_path("timing.saved_values.sample_interval_ms"),
            Some(&Value::Number("500".into()))
        );
        assert_eq!(
            value.get_path("timing.matches_saved"),
            Some(&Value::Bool(true))
        );
        disk.join().unwrap();
        fs::remove_dir_all(root).unwrap();

        let root = task_temp("daynight-photo-timing");
        let photo = br#"{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"auto","state":"day","timing":{"supported":true,"available":true,"transition_delay_supported":false,"values":{"sample_interval_ms":100,"transition_delay_s":null}}}"#.to_vec();
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let before = photo.clone();
        let after = photo.clone();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), before),
                (br#"{"cmd":"set-daynight-settings","mode":"auto","timing":{"sample_interval_ms":100}}"#.as_slice(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), after),
                (br#"{"cmd":"config-save"}"#.as_slice(), br#"{"status":"ok"}"#.to_vec()),
                (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(), br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","trigger":"photo","poll_interval_ms":"100"}}"#.to_vec()),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let isp = serve_daemon(
            &root,
            "rvd.sock",
            br#"{"cmd":"get-running-mode"}"#,
            br#"{"status":"ok","mode":"day"}"#,
        );
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
            br#"{"mode":"auto","timing":{"sample_interval_ms":100}}"#,
            Instant::now() + Duration::from_secs(2),
        );
        assert!(result.is_ok(), "{result:?}");
        daemon.join().unwrap();
        isp.join().unwrap();

        fs::remove_file(root.join("ric.sock")).unwrap();
        let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
        let daemon = thread::spawn(move || {
            for (request, response) in [
                (br#"{"cmd":"get-daynight-settings"}"#.as_slice(), photo),
                (br#"{"cmd":"config-read-section","section":"ircut"}"#.as_slice(), br#"{"status":"ok","section":"ircut","keys":{"mode":"auto","trigger":"photo","poll_interval_ms":"100"}}"#.to_vec()),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .daynight_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("timing.saved_values"),
            Some(&crate::json::parse(br#"{"sample_interval_ms":100}"#).unwrap())
        );
        assert_eq!(
            value.get_path("timing.matches_saved"),
            Some(&Value::Bool(true))
        );
        daemon.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn thresholds_require_current_values_and_independent_saved_values() {
        for fault in [
            "none",
            "setter",
            "readback",
            "trigger",
            "disk",
            "disk-trigger",
            "missing",
        ] {
            let root = task_temp("daynight-thresholds");
            let values =
                crate::json::parse(br#"{"night_luma":23,"night_gain":80000,"day_gain_pct":25}"#)
                    .unwrap();
            let observed = |changed: bool| {
                format!(r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"{}","state":"day","thresholds":{{"trigger":"{}","supported":true,"available":true,"values":{}}}}}"#,
                    if changed { "day" } else { "auto" },
                    if changed && fault == "trigger" { "gain" } else { "luma" },
                    if changed && fault == "trigger" { r#"{"day_threshold":1,"night_threshold":2}"#.into() }
                    else if changed && fault != "readback" { values.to_json() }
                    else { r#"{"night_luma":20,"night_gain":80000,"day_gain_pct":25}"#.into() }).into_bytes()
            };
            let mut pairs = vec![
                (
                    br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                    observed(false),
                ),
                (
                    format!(
                        r#"{{"cmd":"set-daynight-thresholds","trigger":"luma","values":{}}}"#,
                        values.to_json()
                    )
                    .into_bytes(),
                    if fault == "setter" {
                        br#"{"status":"error"}"#.to_vec()
                    } else {
                        br#"{"status":"ok"}"#.to_vec()
                    },
                ),
            ];
            if fault != "setter" {
                pairs.push((
                    br#"{"cmd":"set-daynight-settings","mode":"day"}"#.to_vec(),
                    br#"{"status":"ok"}"#.to_vec(),
                ));
                pairs.push((
                    br#"{"cmd":"get-daynight-settings"}"#.to_vec(),
                    observed(true),
                ));
                if fault != "readback" && fault != "trigger" {
                    pairs.push((
                        br#"{"cmd":"config-save"}"#.to_vec(),
                        br#"{"status":"ok"}"#.to_vec(),
                    ));
                    pairs.push((br#"{"cmd":"config-read-section","section":"ircut"}"#.to_vec(),
                        format!(r#"{{"status":"ok","section":"ircut","keys":{{"mode":"day","trigger":"{}","night_gain":"80000","day_gain_pct":"25"{}}}}}"#,
                            if fault == "disk-trigger" { "gain" } else { "luma" },
                            if fault == "missing" { String::new() } else { format!(r#", "night_luma":"{}""#, if fault == "disk" { 20 } else { 23 }) }).into_bytes()));
                }
            }
            let listener = UnixListener::bind(root.join("ric.sock")).unwrap();
            listener.set_nonblocking(true).unwrap();
            let daemon = thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(3);
                for (request, response) in pairs {
                    let mut socket = loop {
                        match listener.accept() {
                            Ok((socket, _)) => break socket,
                            Err(error)
                                if error.kind() == io::ErrorKind::WouldBlock
                                    && Instant::now() < deadline =>
                            {
                                thread::sleep(Duration::from_millis(2))
                            }
                            other => panic!("missing RIC request: {other:?}"),
                        }
                    };
                    socket
                        .set_read_timeout(Some(Duration::from_secs(1)))
                        .unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let isp = if matches!(fault, "none" | "disk" | "disk-trigger" | "missing") {
                Some(serve_daemon(
                    &root,
                    "rvd.sock",
                    br#"{"cmd":"get-running-mode"}"#,
                    br#"{"status":"ok","mode":"day"}"#,
                ))
            } else {
                None
            };
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_daynight_config(
                format!(
                    r#"{{"mode":"day","thresholds":{{"trigger":"luma","values":{}}}}}"#,
                    values.to_json()
                )
                .as_bytes(),
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
            if let Some(isp) = isp {
                isp.join().unwrap();
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn thresholds_validate_each_trigger_and_reject_unknown_or_unordered_values() {
        for (trigger, json) in [
            (
                "luma",
                r#"{"night_luma":0,"night_gain":2147483647,"day_gain_pct":100}"#,
            ),
            ("gain", r#"{"day_threshold":0,"night_threshold":1}"#),
            ("adc", r#"{"adc_night":0,"adc_day":1}"#),
            (
                "photo",
                r#"{"photo_ev_day":0,"photo_ev_night":1,"photo_ev_deep":2}"#,
            ),
        ] {
            assert!(
                threshold_values(
                    trigger,
                    &crate::json::parse(json.as_bytes()).unwrap(),
                    false,
                    false
                )
                .is_ok()
            );
        }
        for (trigger, json) in [
            (
                "luma",
                r#"{"night_luma":0.5,"night_gain":1,"day_gain_pct":20}"#,
            ),
            ("gain", r#"{"day_threshold":2,"night_threshold":1}"#),
            (
                "photo",
                r#"{"photo_ev_day":1,"photo_ev_night":1,"photo_ev_deep":2}"#,
            ),
        ] {
            assert!(
                threshold_values(
                    trigger,
                    &crate::json::parse(json.as_bytes()).unwrap(),
                    false,
                    false
                )
                .is_err()
            );
        }
    }
}
