use super::*;
use std::cell::Cell;
use std::fs;
use std::path::PathBuf;

fn parse_object(input: &str) -> Value {
    json::parse(input.as_bytes()).unwrap()
}

fn task_temp(name: &str) -> PathBuf {
    let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
    fs::create_dir_all(&root).unwrap();
    let path = PathBuf::from(root).join(format!("daynight-schema-{name}"));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir(&path).unwrap();
    path
}

#[test]
fn validates_partial_daynight_schema_and_schedule() {
    validate_daynight_object(
        &parse_object(
            r#"{
                "enabled":true,
                "initial_mode":"",
                "force_mode":"night",
                "night_threshold":25,
                "day_threshold":50,
                "night_count_threshold":6,
                "day_count_threshold":4,
                "sample_interval_ms":1000,
                "transition_delay_s":5,
                "controls":{"color":true,"ircut":true,"ir850":true,"ir940":false,"white":false},
                "schedule":{"enabled":true,"start_at":"06:00","stop_at":"22:00"},
                "sun":{"enabled":true,"latitude":60.17,"longitude":24.94,"sunrise_offset":-30,"sunset_offset":45}
            }"#,
        ),
        true,
        false,
    )
    .unwrap();
    let unsafe_script = format!(r#"{{"script_path":"/{}/attacker"}}"#, "tmp");
    for body in [
        r#"{"unknown":true}"#,
        r#"{"schedule":{"start_at":"6:00"}}"#,
        r#"{"schedule":{"stop_at":"24:00"}}"#,
        r#"{"controls":{"ir940":true}}"#,
        r#"{"controls":{"white":true}}"#,
        unsafe_script.as_str(),
        r#"{"sun":{"latitude":90.01}}"#,
        r#"{"sun":{"longitude":-180.01}}"#,
        r#"{"sun":{"sunrise_offset":1441}}"#,
        r#"{"night_threshold":101}"#,
        r#"{"sample_interval_ms":99}"#,
    ] {
        assert!(
            validate_daynight_object(&parse_object(body), true, false).is_err(),
            "{body}"
        );
    }
}

#[test]
fn legacy_unsupported_controls_are_preserved_but_cannot_be_activated() {
    let current = parse_object(
        r#"{"enabled":true,"controls":{"color":false,"ir940":true,"white":false},"script_path":"/sbin/daynight","loglevel":"INFO"}"#,
    );
    validate_daynight_object(&current, false, true).unwrap();
    assert!(
        validate_daynight_object(&parse_object(r#"{"controls":{"ir940":true}}"#), true, false)
            .is_err()
    );
    assert!(!introduces_unsupported_activation(
        &parse_object(r#"{"controls":{"ir940":true}}"#),
        &current
    ));
    assert!(introduces_unsupported_activation(
        &parse_object(r#"{"controls":{"ir940":true}}"#),
        &parse_object(r#"{}"#)
    ));
    validate_daynight_object(
        &parse_object(r#"{"controls":{"ir940":false}}"#),
        true,
        false,
    )
    .unwrap();
}

#[test]
fn malformed_known_legacy_fields_fail_closed() {
    for value in [
        r#"{"enabled":"yes","legacy":"preserved"}"#,
        r#"{"night_threshold":101,"legacy":"preserved"}"#,
        r#"{"script_path":"relative/path","legacy":"preserved"}"#,
        r#"{"schedule":{"start_at":"25:00"},"legacy":"preserved"}"#,
    ] {
        assert!(
            validate_daynight_object(&parse_object(value), false, true).is_err(),
            "{value}"
        );
    }
    validate_daynight_object(
        &parse_object(r#"{"legacy":"preserved","script_path":"/sbin/daynight"}"#),
        false,
        true,
    )
    .unwrap();
}

#[test]
fn canonical_get_supplies_sun_defaults_and_hides_legacy_fields() {
    let root = task_temp("canonical-get");
    let config = root.join("thingino.json");
    let original = br#"{"daynight":{"controls":{"color":false,"ir850":true,"ir940":true,"ircut":true,"white":false},"day_count_threshold":4,"day_threshold":50,"enabled":true,"force_mode":"","initial_mode":"","legacy":"preserve","loglevel":"INFO","night_count_threshold":6,"night_threshold":25,"sample_interval_ms":1000,"schedule":{"enabled":false,"start_at":"06:00","stop_at":"22:00"},"script_path":"/sbin/daynight","transition_delay_s":5}}"#;
    fs::write(&config, original).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        ..CameraPaths::default()
    });

    let response = backend.daynight_config().unwrap();
    let actual = json::parse(&response.body).unwrap();
    let expected = parse_object(
        r#"{"controls":{"color":false,"ir850":true,"ircut":true},"day_count_threshold":4,"day_threshold":50,"enabled":true,"force_mode":"","initial_mode":"","loglevel":"INFO","night_count_threshold":6,"night_threshold":25,"sample_interval_ms":1000,"schedule":{"enabled":false,"start_at":"06:00","stop_at":"22:00"},"sun":{"enabled":false,"latitude":0,"longitude":0,"sunrise_offset":0,"sunset_offset":0},"transition_delay_s":5}"#,
    );
    assert_eq!(actual, expected);
    assert_eq!(fs::read(&config).unwrap(), original);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn canonical_get_round_trips_without_dropping_legacy_config() {
    let root = task_temp("canonical-round-trip");
    let config = root.join("thingino.json");
    fs::write(
        &config,
        br#"{"daynight":{"controls":{"color":false,"ir850":true,"ir940":true,"ircut":true,"white":false},"enabled":true,"initial_mode":"","force_mode":"","script_path":"/sbin/daynight","secret":"stored"}}"#,
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        ..CameraPaths::default()
    });

    let get = backend
        .api_request(
            "GET",
            "/api/v1/config/daynight",
            b"",
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap()
        .unwrap();
    let response = backend
        .update_daynight_config_with(&get.body, |_| Ok(4242), |_| 0)
        .unwrap();
    assert_eq!(response.body, b"{\"status\":\"ok\"}\n");

    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("daynight.script_path"),
        Some(&Value::String("/sbin/daynight".to_owned()))
    );
    assert_eq!(
        stored.get_path("daynight.controls.ir940"),
        Some(&Value::Bool(true))
    );
    assert_eq!(
        stored.get_path("daynight.secret"),
        Some(&Value::String("stored".to_owned()))
    );
    assert_eq!(
        stored.get_path("daynight.sun.enabled"),
        Some(&Value::Bool(false))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn successful_hup_is_the_only_path_to_ok_response() {
    let root = task_temp("hup-ok");
    let config = root.join("thingino.json");
    let original = b"{\"daynight\":{\"enabled\":true,\"controls\":{\"color\":false}}}\n";
    let updated = b"{\"daynight\":{\"enabled\":false,\"controls\":{\"color\":false}}}\n";
    fs::write(&config, original).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        ..CameraPaths::default()
    });
    let seen_pid = Cell::new(0);
    let response = backend
        .persist_daynight_and_hup(
            original,
            updated,
            |_| Ok(4242),
            |pid| {
                seen_pid.set(pid);
                0
            },
        )
        .unwrap();
    assert_eq!(response.body, b"{\"status\":\"ok\"}\n");
    assert_eq!(seen_pid.get(), 4242);
    assert_eq!(fs::read(&config).unwrap(), updated);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn failed_live_apply_rolls_back_persisted_daynight_config() {
    let root = task_temp("hup-fail");
    let config = root.join("thingino.json");
    let original = b"{\"daynight\":{\"enabled\":true}}\n";
    let updated = b"{\"daynight\":{\"enabled\":false}}\n";
    fs::write(&config, original).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        ..CameraPaths::default()
    });
    assert_eq!(
        backend.persist_daynight_and_hup(original, updated, |_| Ok(4242), |_| -1),
        Err(BackendError::Unavailable)
    );
    assert_eq!(fs::read(&config).unwrap(), original);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn update_does_not_write_when_daynightd_is_not_validated() {
    let root = task_temp("service-check");
    let config = root.join("thingino.json");
    let pid = root.join("daynightd.pid");
    let original = b"{\"daynight\":{\"enabled\":true}}\n";
    fs::write(&config, original).unwrap();
    fs::write(&pid, b"1\n").unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: config.clone(),
        daynight_pid: pid,
        daynight_executable: root.join("not-daynightd"),
        ..CameraPaths::default()
    });
    assert_eq!(
        backend.update_daynight_config(br#"{"enabled":false}"#),
        Err(BackendError::Unavailable)
    );
    assert_eq!(fs::read(&config).unwrap(), original);
    fs::remove_dir_all(root).unwrap();
}
