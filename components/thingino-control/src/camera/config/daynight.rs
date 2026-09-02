use super::super::*;

const MAX_DAYNIGHT_PATH_BYTES: usize = 128;
const MAX_DAYNIGHT_COUNT: i64 = 255;
const MAX_TRANSITION_DELAY_SECONDS: i64 = 300;
const MAX_RAW_THRESHOLD: i64 = 2_147_483_647;

impl PrudyntBackend {
    pub(in crate::camera) fn daynight_config(&self) -> Result<BackendResponse, BackendError> {
        let original = read_bounded(&self.paths.thingino_config, FILE_LIMIT)?;
        let document = json::parse(&original).map_err(|_| BackendError::Protocol)?;
        let current = document
            .get_path("daynight")
            .cloned()
            .unwrap_or_else(|| Value::Object(BTreeMap::new()));
        json_response(canonical_daynight_view(&current)?)
    }

    pub(in crate::camera) fn update_daynight_config(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        self.update_daynight_config_with(
            body,
            |backend| backend.validated_daynight_pid(),
            |pid| {
                // SAFETY: the resolver checked the pid file and /proc/<pid>/exe
                // immediately before this signal; no other signal is allowed.
                unsafe { kill(pid, SIGHUP) }
            },
        )
    }

    fn update_daynight_config_with<P, H>(
        &self,
        body: &[u8],
        resolve_pid: P,
        hup: H,
    ) -> Result<BackendResponse, BackendError>
    where
        P: Fn(&Self) -> Result<i32, BackendError>,
        H: FnOnce(i32) -> i32,
    {
        let update = json::parse(body).map_err(|_| BackendError::Protocol)?;
        // D-Link A1 exposes neither IR940 nor white-light hardware. The
        // canonical GET omits both controls, and public updates may only send
        // `false` to retire a legacy stored value; they can never activate one.
        validate_daynight_object(&update, true, false)?;

        let original = read_bounded(&self.paths.thingino_config, FILE_LIMIT)?;
        let mut document = json::parse(&original).map_err(|_| BackendError::Protocol)?;
        let mut current = document
            .get_path("daynight")
            .cloned()
            .unwrap_or_else(|| Value::Object(BTreeMap::new()));
        // Existing images can carry fields which are not part of the public
        // update schema. Validate known persisted values, but preserve those
        // fields while merging a partial request.
        validate_daynight_object(&current, false, true)?;
        if introduces_unsupported_activation(&update, &current) {
            return Err(BackendError::Protocol);
        }
        current.merge(&update).map_err(|_| BackendError::Protocol)?;
        validate_daynight_object(&current, false, true)?;
        document
            .set_path("daynight", current)
            .map_err(|_| BackendError::Protocol)?;

        let mut serialized = document.to_json().into_bytes();
        serialized.push(b'\n');

        // A successful response means that SIGHUP was delivered to a live,
        // path-validated daynightd; it does not prove that reload completed.
        // The preflight avoids changing a stopped service;
        // the second validation in persist_daynight_and_hup closes the race
        // between writing the file and delivering the signal.
        resolve_pid(self)?;
        self.persist_daynight_and_hup(&original, &serialized, resolve_pid, hup)
    }

    pub(in crate::camera) fn validated_daynight_pid(&self) -> Result<i32, BackendError> {
        let pid = read_pid(&self.paths.daynight_pid).ok_or(BackendError::Unavailable)?;
        if !process_matches(&self.paths.daynight_pid, &self.paths.daynight_executable) {
            return Err(BackendError::Unavailable);
        }
        Ok(pid)
    }

    fn persist_daynight_and_hup<P, H>(
        &self,
        original: &[u8],
        serialized: &[u8],
        resolve_pid: P,
        hup: H,
    ) -> Result<BackendResponse, BackendError>
    where
        P: Fn(&Self) -> Result<i32, BackendError>,
        H: FnOnce(i32) -> i32,
    {
        write_in_place(&self.paths.thingino_config, serialized)?;
        let pid = match resolve_pid(self) {
            Ok(pid) => pid,
            Err(error) => {
                let _ = write_in_place(&self.paths.thingino_config, original);
                return Err(error);
            }
        };
        if hup(pid) != 0 {
            let _ = write_in_place(&self.paths.thingino_config, original);
            return Err(BackendError::Unavailable);
        }
        Ok(action_ok())
    }
}

type DayNightFields = BTreeMap<String, Value>;

fn canonical_daynight_view(value: &Value) -> Result<Value, BackendError> {
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    // Validate the fields which are retained in the public view. Unknown and
    // legacy fields remain in the persisted document but never cross GET.
    let enabled = field_or_default(fields, "enabled", Value::Bool(true), |value| {
        value.as_bool().is_some()
    })?;
    let initial_mode = field_or_default(
        fields,
        "initial_mode",
        Value::String(String::new()),
        valid_mode,
    )?;
    let force_mode = field_or_default(
        fields,
        "force_mode",
        Value::String(String::new()),
        valid_mode,
    )?;
    let night_threshold = field_or_default(fields, "night_threshold", number(25), |value| {
        bounded_integer(value, 0, 100)
    })?;
    let day_threshold = field_or_default(fields, "day_threshold", number(50), |value| {
        bounded_integer(value, 0, 100)
    })?;
    let night_count_threshold =
        field_or_default(fields, "night_count_threshold", number(6), |value| {
            bounded_integer(value, 1, MAX_DAYNIGHT_COUNT)
        })?;
    let day_count_threshold =
        field_or_default(fields, "day_count_threshold", number(4), |value| {
            bounded_integer(value, 1, MAX_DAYNIGHT_COUNT)
        })?;
    let sample_interval_ms =
        field_or_default(fields, "sample_interval_ms", number(1_000), |value| {
            bounded_integer(value, 100, 60_000)
        })?;
    let transition_delay_s = field_or_default(fields, "transition_delay_s", number(5), |value| {
        bounded_integer(value, 0, MAX_TRANSITION_DELAY_SECONDS)
    })?;
    let loglevel = field_or_default(
        fields,
        "loglevel",
        Value::String("INFO".to_owned()),
        valid_loglevel,
    )?;

    let controls = canonical_controls(fields.get("controls"))?;
    let schedule = canonical_schedule(fields.get("schedule"))?;
    let sun = canonical_sun(fields.get("sun"))?;

    let mut public = Value::Object(
        [
            ("controls", controls),
            ("day_count_threshold", day_count_threshold),
            ("day_threshold", day_threshold),
            ("enabled", enabled),
            ("force_mode", force_mode),
            ("initial_mode", initial_mode),
            ("loglevel", loglevel),
            ("night_count_threshold", night_count_threshold),
            ("night_threshold", night_threshold),
            ("sample_interval_ms", sample_interval_ms),
            ("schedule", schedule),
            ("sun", sun),
            ("transition_delay_s", transition_delay_s),
        ]
        .into_iter()
        .map(|(name, value)| (name.to_owned(), value))
        .collect::<DayNightFields>(),
    );
    // Keep the redaction invariant if a future public allowlist gains a
    // secret-named field; the current allowlist deliberately omits all such
    // legacy fields (including script_path).
    mask_secret_fields(&mut public);
    Ok(public)
}

fn field_or_default<F>(
    fields: &DayNightFields,
    name: &str,
    default: Value,
    valid: F,
) -> Result<Value, BackendError>
where
    F: Fn(&Value) -> bool,
{
    match fields.get(name) {
        Some(value) if valid(value) => Ok(value.clone()),
        Some(_) => Err(BackendError::Protocol),
        None => Ok(default),
    }
}

fn nested_field_or_default<F>(
    value: Option<&Value>,
    name: &str,
    default: Value,
    valid: F,
) -> Result<Value, BackendError>
where
    F: Fn(&Value) -> bool,
{
    let Some(value) = value else {
        return Ok(default);
    };
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    field_or_default(fields, name, default, valid)
}

fn canonical_controls(value: Option<&Value>) -> Result<Value, BackendError> {
    Ok(object([
        (
            "color",
            nested_field_or_default(value, "color", Value::Bool(false), |value| {
                value.as_bool().is_some()
            })?,
        ),
        (
            "ircut",
            nested_field_or_default(value, "ircut", Value::Bool(true), |value| {
                value.as_bool().is_some()
            })?,
        ),
        (
            "ir850",
            nested_field_or_default(value, "ir850", Value::Bool(true), |value| {
                value.as_bool().is_some()
            })?,
        ),
    ]))
}

fn canonical_schedule(value: Option<&Value>) -> Result<Value, BackendError> {
    Ok(object([
        (
            "enabled",
            nested_field_or_default(value, "enabled", Value::Bool(false), |value| {
                value.as_bool().is_some()
            })?,
        ),
        (
            "start_at",
            nested_field_or_default(
                value,
                "start_at",
                Value::String("06:00".to_owned()),
                |value| value.as_str().is_some_and(valid_hhmm),
            )?,
        ),
        (
            "stop_at",
            nested_field_or_default(
                value,
                "stop_at",
                Value::String("22:00".to_owned()),
                |value| value.as_str().is_some_and(valid_hhmm),
            )?,
        ),
    ]))
}

fn canonical_sun(value: Option<&Value>) -> Result<Value, BackendError> {
    Ok(object([
        (
            "enabled",
            nested_field_or_default(value, "enabled", Value::Bool(false), |value| {
                value.as_bool().is_some()
            })?,
        ),
        (
            "latitude",
            nested_field_or_default(value, "latitude", number(0), |value| {
                bounded_number(value, -90.0, 90.0).is_some()
            })?,
        ),
        (
            "longitude",
            nested_field_or_default(value, "longitude", number(0), |value| {
                bounded_number(value, -180.0, 180.0).is_some()
            })?,
        ),
        (
            "sunrise_offset",
            nested_field_or_default(value, "sunrise_offset", number(0), |value| {
                bounded_integer(value, -1_440, 1_440)
            })?,
        ),
        (
            "sunset_offset",
            nested_field_or_default(value, "sunset_offset", number(0), |value| {
                bounded_integer(value, -1_440, 1_440)
            })?,
        ),
    ]))
}

fn validate_daynight_object(
    value: &Value,
    reject_unknown: bool,
    allow_unsupported_active: bool,
) -> Result<(), BackendError> {
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    if reject_unknown && fields.is_empty() {
        return Err(BackendError::Protocol);
    }
    for (name, value) in fields {
        let recognized = matches!(
            name.as_str(),
            "enabled"
                | "initial_mode"
                | "force_mode"
                | "night_threshold"
                | "day_threshold"
                | "night_threshold_pct"
                | "day_threshold_pct"
                | "night_count_threshold"
                | "day_count_threshold"
                | "sample_interval_ms"
                | "transition_delay_s"
                | "ev_night_threshold"
                | "ev_day_threshold"
                | "hysteresis_factor"
                | "script_path"
                | "loglevel"
                | "controls"
                | "schedule"
                | "sun"
        );
        let known = match name.as_str() {
            "enabled" => value.as_bool().is_some(),
            "initial_mode" | "force_mode" => valid_mode(value),
            "night_threshold" | "day_threshold" | "night_threshold_pct" | "day_threshold_pct" => {
                bounded_integer(value, 0, 100)
            }
            "night_count_threshold" | "day_count_threshold" => {
                bounded_integer(value, 1, MAX_DAYNIGHT_COUNT)
            }
            "sample_interval_ms" => bounded_integer(value, 100, 60_000),
            "transition_delay_s" => bounded_integer(value, 0, MAX_TRANSITION_DELAY_SECONDS),
            "ev_night_threshold" | "ev_day_threshold" => {
                bounded_integer(value, 0, MAX_RAW_THRESHOLD)
            }
            "hysteresis_factor" => bounded_number(value, 0.0, 0.5).is_some(),
            // `script_path` is a legacy persisted field only.  Keeping it out
            // of strict request validation prevents the public control plane
            // from selecting an arbitrary executable, while the permissive
            // current-document pass still preserves existing configurations.
            "script_path" => !reject_unknown && valid_script_path(value),
            "loglevel" => valid_loglevel(value),
            "controls" => {
                validate_controls(value, reject_unknown, allow_unsupported_active).is_ok()
            }
            "schedule" => validate_schedule(value, reject_unknown).is_ok(),
            "sun" => validate_sun(value, reject_unknown).is_ok(),
            _ => false,
        };
        if !known && (recognized || reject_unknown) {
            return Err(BackendError::Protocol);
        }
        if !known && !reject_unknown {
            // Preserve unknown fields already present in a legacy config. A
            // request cannot introduce one because the strict update pass
            // above rejects it.
            continue;
        }
        match name.as_str() {
            "controls" => validate_controls(value, reject_unknown, allow_unsupported_active)?,
            "schedule" => validate_schedule(value, reject_unknown)?,
            "sun" => validate_sun(value, reject_unknown)?,
            _ => {}
        }
    }
    Ok(())
}

fn introduces_unsupported_activation(update: &Value, current: &Value) -> bool {
    let Some(controls) = update.get_path("controls").and_then(Value::as_object) else {
        return false;
    };
    ["ir940", "white"].into_iter().any(|name| {
        controls.get(name).and_then(Value::as_bool) == Some(true)
            && current
                .get_path(&format!("controls.{name}"))
                .and_then(Value::as_bool)
                != Some(true)
    })
}

fn validate_controls(
    value: &Value,
    reject_unknown: bool,
    allow_unsupported_active: bool,
) -> Result<(), BackendError> {
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    for (name, value) in fields {
        match name.as_str() {
            "color" | "ircut" | "ir850" => {
                value.as_bool().ok_or(BackendError::Protocol)?;
            }
            "ir940" | "white" => {
                let enabled = value.as_bool().ok_or(BackendError::Protocol)?;
                if enabled && !allow_unsupported_active {
                    return Err(BackendError::Protocol);
                }
            }
            _ if reject_unknown => return Err(BackendError::Protocol),
            _ => {}
        }
    }
    Ok(())
}

fn validate_schedule(value: &Value, reject_unknown: bool) -> Result<(), BackendError> {
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    for (name, value) in fields {
        match name.as_str() {
            "enabled" => {
                value.as_bool().ok_or(BackendError::Protocol)?;
            }
            "start_at" | "stop_at" => {
                let value = value.as_str().ok_or(BackendError::Protocol)?;
                if !valid_hhmm(value) {
                    return Err(BackendError::Protocol);
                }
            }
            _ if reject_unknown => return Err(BackendError::Protocol),
            _ => {}
        }
    }
    Ok(())
}

fn validate_sun(value: &Value, reject_unknown: bool) -> Result<(), BackendError> {
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    for (name, value) in fields {
        match name.as_str() {
            "enabled" => {
                value.as_bool().ok_or(BackendError::Protocol)?;
            }
            "latitude" => {
                bounded_number(value, -90.0, 90.0).ok_or(BackendError::Protocol)?;
            }
            "longitude" => {
                bounded_number(value, -180.0, 180.0).ok_or(BackendError::Protocol)?;
            }
            "sunrise_offset" | "sunset_offset" if !bounded_integer(value, -1_440, 1_440) => {
                return Err(BackendError::Protocol);
            }
            "sunrise_offset" | "sunset_offset" => {}
            _ if reject_unknown => return Err(BackendError::Protocol),
            _ => {}
        }
    }
    Ok(())
}

fn valid_mode(value: &Value) -> bool {
    value
        .as_str()
        .is_some_and(|value| matches!(value, "" | "day" | "night"))
}

fn valid_loglevel(value: &Value) -> bool {
    value.as_str().is_some_and(|value| {
        matches!(
            value,
            "FATAL" | "ERROR" | "WARN" | "INFO" | "DEBUG" | "TRACE"
        )
    })
}

fn valid_script_path(value: &Value) -> bool {
    let Some(value) = value.as_str() else {
        return false;
    };
    value.len() <= MAX_DAYNIGHT_PATH_BYTES && value.starts_with('/') && safe_path_fragment(value)
}

fn valid_hhmm(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 5
        && bytes[0].is_ascii_digit()
        && bytes[1].is_ascii_digit()
        && bytes[2] == b':'
        && bytes[3].is_ascii_digit()
        && bytes[4].is_ascii_digit()
        && value[0..2].parse::<u8>().is_ok_and(|hour| hour < 24)
        && value[3..5].parse::<u8>().is_ok_and(|minute| minute < 60)
}

fn bounded_integer(value: &Value, minimum: i64, maximum: i64) -> bool {
    match value {
        Value::Number(value) => value
            .parse::<i64>()
            .is_ok_and(|value| (minimum..=maximum).contains(&value)),
        _ => false,
    }
}

fn bounded_number(value: &Value, minimum: f64, maximum: f64) -> Option<f64> {
    match value {
        Value::Number(value) => value
            .parse::<f64>()
            .ok()
            .filter(|value| value.is_finite() && (minimum..=maximum).contains(value)),
        _ => None,
    }
}

#[cfg(test)]
#[path = "daynight_tests.rs"]
mod tests;
