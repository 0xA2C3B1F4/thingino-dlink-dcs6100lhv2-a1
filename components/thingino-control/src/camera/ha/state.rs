use std::collections::BTreeMap;

use super::super::*;
use super::config::HaConfig;

pub(super) type States = BTreeMap<String, Vec<u8>>;

pub(super) fn collect(backend: &PrudyntBackend, config: &HaConfig) -> Result<States, BackendError> {
    let heartbeat = json::parse(&backend.heartbeat()?.body).map_err(|_| BackendError::Protocol)?;
    let release = os_release(&backend.paths);
    let mut states = States::new();
    let flags = config.entities;
    if flags.motion {
        insert_switch(&mut states, "motion", backend.paths.motion_alarm.is_file());
    }
    if flags.motion_guard {
        insert_switch(
            &mut states,
            "motion_guard",
            bool_path(&heartbeat, "motion_enabled"),
        );
    }
    if flags.ircut {
        insert_optional_switch(
            &mut states,
            "ircut",
            integer_path(&heartbeat, "ircut_state"),
        );
    }
    if flags.daynight {
        insert_optional_state(&mut states, "daynight", daynight_state(&heartbeat));
    }
    if flags.privacy {
        insert_switch(
            &mut states,
            "privacy",
            bool_path(&heartbeat, "privacy_enabled"),
        );
    }
    if flags.color {
        let running_mode = read_bounded(&backend.paths.prudynt_config, FILE_LIMIT)
            .ok()
            .and_then(|raw| json::parse(&raw).ok())
            .and_then(|prudynt| integer_path(&prudynt, "image.running_mode"));
        insert_optional_switch(
            &mut states,
            "color",
            running_mode.map(|value| u64::from(value == 0)),
        );
    }
    if flags.ir850 {
        insert_optional_switch(
            &mut states,
            "ir850",
            integer_path(&heartbeat, "ir850_state"),
        );
    }
    if flags.gain {
        insert_optional_state(&mut states, "gain", scalar_path(&heartbeat, "total_gain"));
    }
    if flags.rssi {
        let value = super::super::network::wpa_signal_rssi(
            &backend.paths.wpa_control,
            Instant::now() + Duration::from_millis(250),
        )
        .ok()
        .or_else(|| wifi_rssi(&backend.paths.proc_net_wireless))
        .map(|value| value.to_string().into_bytes());
        insert_optional_state(&mut states, "rssi", value);
    }
    insert_optional_state(
        &mut states,
        "camera_config",
        release
            .get_path("IMAGE_ID")
            .and_then(Value::as_str)
            .map(|value| value.as_bytes().to_vec()),
    );
    let release_fields = release
        .get_path("COMMIT_ID")
        .and_then(Value::as_str)
        .map(|raw| raw.split_once(", ").unwrap_or((raw, "")));
    if flags.firmware_version {
        insert_optional_state(
            &mut states,
            "firmware_version",
            release_fields.map(|(version, _)| version.as_bytes().to_vec()),
        );
    }
    if flags.firmware_timestamp {
        insert_optional_state(
            &mut states,
            "firmware_timestamp",
            release_fields.and_then(|(_, timestamp)| {
                (!timestamp.is_empty()).then(|| {
                    let timestamp = timestamp.strip_suffix(" +0000").unwrap_or(timestamp);
                    format!("{timestamp} UTC").into_bytes()
                })
            }),
        );
    }
    Ok(states)
}

pub(super) fn os_release(paths: &CameraPaths) -> Value {
    let Ok(raw) = read_bounded(&paths.os_release, FILE_LIMIT) else {
        return object([]);
    };
    let Ok(text) = std::str::from_utf8(&raw) else {
        return object([]);
    };
    let mut values = BTreeMap::new();
    for line in text.lines() {
        let Some((name, raw_value)) = line.split_once('=') else {
            continue;
        };
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
        {
            continue;
        }
        let value = raw_value
            .strip_prefix('"')
            .and_then(|value| value.strip_suffix('"'))
            .unwrap_or(raw_value);
        if value.len() <= 512 && !value.bytes().any(|byte| byte.is_ascii_control()) {
            values.insert(name.to_owned(), Value::String(value.to_owned()));
        }
    }
    Value::Object(values)
}

fn bool_path(value: &Value, path: &str) -> bool {
    value
        .get_path(path)
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn integer_path(value: &Value, path: &str) -> Option<u64> {
    value.get_path(path).and_then(value_u64)
}

fn scalar_path(value: &Value, path: &str) -> Option<Vec<u8>> {
    match value.get_path(path)? {
        Value::Number(value) => Some(value.as_bytes().to_vec()),
        Value::String(value) => Some(value.as_bytes().to_vec()),
        _ => None,
    }
}

fn daynight_state(heartbeat: &Value) -> Option<Vec<u8>> {
    if bool_path(heartbeat, "daynight_enabled") {
        return Some(b"auto".to_vec());
    }
    heartbeat
        .get_path("daynight_mode")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "day" | "night"))
        .map(|value| value.as_bytes().to_vec())
}

fn insert_optional_state(states: &mut States, entity: &str, value: Option<Vec<u8>>) {
    states.insert(entity.to_owned(), value.unwrap_or_default());
}

fn insert_switch(states: &mut States, entity: &str, enabled: bool) {
    states.insert(
        entity.to_owned(),
        if enabled {
            b"ON".to_vec()
        } else {
            b"OFF".to_vec()
        },
    );
}

fn insert_optional_switch(states: &mut States, entity: &str, enabled: Option<u64>) {
    match enabled {
        Some(enabled) => insert_switch(states, entity, enabled != 0),
        None => insert_optional_state(states, entity, None),
    }
}

fn wifi_rssi(path: &Path) -> Option<i32> {
    let raw = read_bounded(path, 16 * 1024).ok()?;
    let text = std::str::from_utf8(&raw).ok()?;
    text.lines().skip(2).find_map(|line| {
        let (_, values) = line.split_once(':')?;
        let mut columns = values.split_ascii_whitespace();
        let _status = columns.next()?;
        let _quality = columns.next()?;
        let level = columns.next()?.trim_end_matches('.').parse::<i32>().ok()?;
        (level != 0 && (-127..=0).contains(&level)).then_some(level)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = PathBuf::from(root).join(format!(
            "ha-state-{}-{}-{name}",
            process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn proc_wireless_parser_is_bounded_and_strict() {
        let root = task_temp("ha-rssi");
        let path = root.join("wireless");
        fs::write(
            &path,
            "Inter-| sta\n face | quality\nwlan0: 0000   70.  -41.  -256        0      0\n",
        )
        .unwrap();
        assert_eq!(wifi_rssi(&path), Some(-41));
        fs::write(&path, "bad\n").unwrap();
        assert_eq!(wifi_rssi(&path), None);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn wpa_signal_poll_parser_accepts_only_real_dbm_values() {
        assert_eq!(
            super::super::super::network::parse_wpa_signal_poll(
                b"RSSI=-44\nLINKSPEED=72\nNOISE=9999\n"
            ),
            Some(-44)
        );
        assert_eq!(
            super::super::super::network::parse_wpa_signal_poll(b"RSSI=56\n"),
            None
        );
        assert_eq!(
            super::super::super::network::parse_wpa_signal_poll(b"RSSI=-200\n"),
            None
        );
        assert_eq!(
            super::super::super::network::parse_wpa_signal_poll(b"RSSI=0\n"),
            None
        );
    }

    #[test]
    fn daynight_state_reports_auto_policy_and_physical_forced_modes() {
        assert_eq!(
            daynight_state(&object([
                ("daynight_enabled", Value::Bool(true)),
                ("daynight_mode", Value::String("night".to_owned())),
            ])),
            Some(b"auto".to_vec())
        );
        assert_eq!(
            daynight_state(&object([
                ("daynight_enabled", Value::Bool(false)),
                ("daynight_mode", Value::String("day".to_owned())),
            ])),
            Some(b"day".to_vec())
        );
        assert_eq!(daynight_state(&object([])), None);
    }

    #[test]
    fn unavailable_optional_states_clear_stale_retained_payloads() {
        let mut states = States::new();
        insert_optional_state(&mut states, "rssi", None);
        insert_optional_switch(&mut states, "ircut", None);
        assert_eq!(states.get("rssi"), Some(&Vec::new()));
        assert_eq!(states.get("ircut"), Some(&Vec::new()));
    }

    #[test]
    fn os_release_parser_keeps_only_bounded_named_values() {
        let root = task_temp("ha-os-release");
        let path = root.join("os-release");
        fs::write(
            &path,
            "IMAGE_ID=\"dlink\"\nCOMMIT_ID=\"r5, 2026-08-23 10:00:00 +0000\"\nbad=x\n",
        )
        .unwrap();
        let parsed = os_release(&CameraPaths {
            os_release: path,
            ..CameraPaths::default()
        });
        assert_eq!(
            parsed.get_path("IMAGE_ID"),
            Some(&Value::String("dlink".to_owned()))
        );
        assert_eq!(parsed.get_path("bad"), None);
        fs::remove_dir_all(root).unwrap();
    }
}
