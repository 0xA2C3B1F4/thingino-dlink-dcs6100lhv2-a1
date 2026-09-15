use super::*;
use std::fs;
use std::process;
use std::sync::atomic::{AtomicUsize, Ordering};

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn task_temp(name: &str) -> std::path::PathBuf {
    let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
    fs::create_dir_all(&root).unwrap();
    let path = std::path::PathBuf::from(root).join(format!(
        "ta-domain-{}-{}-{name}",
        process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
    ));
    fs::create_dir(&path).unwrap();
    path
}

fn backend_with(domain: &str, value: &str) -> (HostBackend, std::path::PathBuf) {
    let root = task_temp(domain);
    let path = root.join("thingino.json");
    fs::write(&path, format!("{{\"{domain}\":{value}}}\n")).unwrap();
    let backend = HostBackend::new(CameraPaths {
        thingino_config: path,
        ..CameraPaths::default()
    });
    (backend, root)
}

#[test]
fn motion_webhook_is_partial_write_only_and_rejects_unsafe_urls() {
    let (backend, root) = backend_with(
        "motion_webhook",
        r#"{"enabled":false,"url":"https://alerts.example.test/motion?key=secret"}"#,
    );
    let response = backend.validated_config_domain("motion_webhook").unwrap();
    let public = json::parse(&response.body).unwrap();
    assert_eq!(public.get_path("url"), Some(&Value::Null));
    assert_eq!(public.get_path("url_set"), Some(&Value::Bool(true)));
    assert!(!String::from_utf8(response.body).unwrap().contains("secret"));

    backend
        .update_validated_config_domain("motion_webhook", br#"{"enabled":true,"url":null}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("motion_webhook.enabled"),
        Some(&Value::Bool(true))
    );
    assert_eq!(
        stored.get_path("motion_webhook.url"),
        Some(&Value::String(
            "https://alerts.example.test/motion?key=secret".to_owned()
        ))
    );

    for invalid in [
        "file:///etc/passwd",
        "https://user:password@example.test/hook",
        "https://example.test/hook#fragment",
        "http://",
        "https://example.test/a b",
        "https://example.test:0/hook",
        "https://example.test:not-a-port/hook",
        "https://[::1]trailing/hook",
    ] {
        let body = format!(r#"{{"url":"{invalid}"}}"#);
        assert!(
            backend
                .update_validated_config_domain("motion_webhook", body.as_bytes())
                .is_err(),
            "accepted unsafe webhook URL {invalid}"
        );
    }
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_ntfy_preserves_or_explicitly_clears_secrets_and_rejects_unsafe_tokens() {
    let (backend, root) = backend_with(
        "motion_ntfy",
        r#"{"enabled":true,"url":"https://ntfy.example.test/private-topic","token":"__SET_LOCALLY__"}"#,
    );
    let response = backend.validated_config_domain("motion_ntfy").unwrap();
    let public = json::parse(&response.body).unwrap();
    assert_eq!(public.get_path("url"), Some(&Value::Null));
    assert_eq!(public.get_path("token"), Some(&Value::Null));
    assert_eq!(public.get_path("url_set"), Some(&Value::Bool(true)));
    assert_eq!(public.get_path("token_set"), Some(&Value::Bool(true)));
    assert!(
        !String::from_utf8(response.body)
            .unwrap()
            .contains("private-topic")
    );
    let control_token = format!("{{\"{}\":\"line\\rbreak\"}}", "token");
    assert!(
        backend
            .update_validated_config_domain("motion_ntfy", control_token.as_bytes())
            .is_err()
    );
    assert!(
        backend
            .update_validated_config_domain(
                "motion_ntfy",
                br#"{"url":"http://127.0.0.1/topic","token":"__SET_LOCALLY__"}"#,
            )
            .is_err()
    );

    backend
        .update_validated_config_domain("motion_ntfy", br#"{"url":"","token":""}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("motion_ntfy.token"),
        Some(&Value::String("__SET_LOCALLY__".to_owned()))
    );

    backend
        .update_validated_config_domain("motion_ntfy", br#"{"clear_token":true}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(stored.get_path("motion_ntfy.token"), None);
    assert!(
        backend
            .update_validated_config_domain(
                "motion_ntfy",
                br#"{"clear_token":true,"token":"__GENERATE_LOCALLY__"}"#,
            )
            .is_err()
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_gotify_requires_verified_https_for_its_write_only_token() {
    let (backend, root) = backend_with(
        "motion_gotify",
        r#"{"enabled":true,"endpoint":"https://gotify.example.test/message","token":"__SET_LOCALLY__"}"#,
    );
    let response = backend.validated_config_domain("motion_gotify").unwrap();
    let public = json::parse(&response.body).unwrap();
    assert_eq!(public.get_path("endpoint"), Some(&Value::Null));
    assert_eq!(public.get_path("token"), Some(&Value::Null));
    assert_eq!(public.get_path("endpoint_set"), Some(&Value::Bool(true)));
    assert_eq!(public.get_path("token_set"), Some(&Value::Bool(true)));
    let body = String::from_utf8(response.body).unwrap();
    assert!(!body.contains("gotify.example"));
    assert!(!body.contains("__SET_LOCALLY__"));
    assert!(
        backend
            .update_validated_config_domain(
                "motion_gotify",
                br#"{"endpoint":"http://127.0.0.1/message"}"#
            )
            .is_err()
    );
    backend
        .update_validated_config_domain("motion_gotify", br#"{"clear_token":true,"enabled":false}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(stored.get_path("motion_gotify.token"), None);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_telegram_redacts_token_and_chat_and_rejects_url_material() {
    let (backend, root) = backend_with(
        "motion_telegram",
        r#"{"enabled":true,"bot_token":"123:ABC_def","chat_id":"-100123"}"#,
    );
    let response = backend.validated_config_domain("motion_telegram").unwrap();
    let public = json::parse(&response.body).unwrap();
    for name in ["bot_token", "chat_id"] {
        assert_eq!(public.get_path(name), Some(&Value::Null));
    }
    for name in ["bot_token_set", "chat_id_set"] {
        assert_eq!(public.get_path(name), Some(&Value::Bool(true)));
    }
    let body = String::from_utf8(response.body).unwrap();
    assert!(!body.contains("123:ABC"));
    assert!(!body.contains("-100123"));
    assert!(
        backend
            .update_validated_config_domain(
                "motion_telegram",
                br#"{"bot_token":"https://evil.example/token"}"#
            )
            .is_err()
    );
    assert!(
        backend
            .update_validated_config_domain(
                "motion_telegram",
                br#"{"chat_id":"@alerts?redirect=1"}"#
            )
            .is_err()
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_email_preserves_password_and_rejects_unsafe_smtp_fields() {
    let (backend, root) = backend_with(
        "motion_email",
        r#"{"enabled":true,"host":"smtp.example.test","port":587,"tls_mode":"starttls","username":"camera","password":"__SET_LOCALLY__","from_address":"camera@example.test","to_address":"owner@example.test"}"#,
    );
    let response = backend.validated_config_domain("motion_email").unwrap();
    let body = String::from_utf8(response.body).unwrap();
    assert!(body.contains("\"password\":null"));
    assert!(body.contains("\"password_set\":true"));
    assert!(!body.contains("__SET_LOCALLY__"));

    backend
        .update_validated_config_domain(
            "motion_email",
            br#"{"host":"mail.example.test","password":""}"#,
        )
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("motion_email.password"),
        Some(&Value::String("__SET_LOCALLY__".to_owned()))
    );
    assert_eq!(
        stored.get_path("motion_email.host"),
        Some(&Value::String("mail.example.test".to_owned()))
    );

    for body in [
        br#"{"tls_mode":"none"}"#.as_slice(),
        br#"{"host":"smtp.example.test/path"}"#.as_slice(),
        br#"{"from_address":"camera@example.test\r\nBcc: x@example.test"}"#.as_slice(),
        br#"{"enabled":true,"username":"camera","clear_password":true}"#.as_slice(),
        br#"{"subject":"custom"}"#.as_slice(),
        br#"{"send_photo":true}"#.as_slice(),
    ] {
        assert!(
            backend
                .update_validated_config_domain("motion_email", body)
                .is_err()
        );
    }

    backend
        .update_validated_config_domain(
            "motion_email",
            br#"{"enabled":false,"clear_password":true}"#,
        )
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(stored.get_path("motion_email.password"), None);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_ftp_preserves_password_and_rejects_unsafe_targets() {
    let (backend, root) = backend_with(
        "motion_ftp",
        r#"{"enabled":true,"host":"ftp.example.test","port":21,"tls_mode":"explicit","username":"camera","password":"__SET_LOCALLY__","path":"motion/events"}"#,
    );
    let response = backend.validated_config_domain("motion_ftp").unwrap();
    let body = String::from_utf8(response.body).unwrap();
    assert!(body.contains("\"password\":null"));
    assert!(body.contains("\"password_set\":true"));
    assert!(!body.contains("__SET_LOCALLY__"));

    backend
        .update_validated_config_domain("motion_ftp", br#"{"path":"alerts","password":""}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("motion_ftp.password"),
        Some(&Value::String("__SET_LOCALLY__".to_owned()))
    );
    for body in [
        br#"{"tls_mode":"plain"}"#.as_slice(),
        br#"{"host":"ftp.example.test/path"}"#.as_slice(),
        br#"{"host":"user@ftp.example.test"}"#.as_slice(),
        br#"{"path":"../private"}"#.as_slice(),
        br#"{"path":"motion//events"}"#.as_slice(),
        br#"{"filename":"custom.jpg"}"#.as_slice(),
        br#"{"send_video":true}"#.as_slice(),
        br#"{"enabled":true,"clear_password":true}"#.as_slice(),
    ] {
        assert!(
            backend
                .update_validated_config_domain("motion_ftp", body)
                .is_err()
        );
    }
    backend
        .update_validated_config_domain("motion_ftp", br#"{"enabled":false,"clear_password":true}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(stored.get_path("motion_ftp.password"), None);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn webui_rejects_username_changes_and_unsafe_bypass_entries() {
    let (backend, root) = backend_with(
        "webui",
        r#"{"level":"user","paranoid":false,"theme":"dark"}"#,
    );
    let response = backend.validated_config_domain("webui").unwrap();
    let redacted = json::parse(&response.body).unwrap();
    assert_eq!(
        redacted.get_path("username"),
        Some(&Value::String("root".to_owned()))
    );
    assert_eq!(
        redacted.get_path("theme"),
        Some(&Value::String("dark".to_owned()))
    );
    assert_eq!(redacted.get_path("track_focus"), Some(&Value::Bool(false)));
    assert_eq!(
        redacted.get_path("focus_timeout"),
        Some(&Value::Number("0".to_owned()))
    );
    assert_eq!(
        redacted.get_path("auth_bypass_ips"),
        Some(&Value::String(String::new()))
    );
    assert!(
        backend
            .update_validated_config_domain("webui", br#"{"username":"admin"}"#)
            .is_err()
    );
    assert!(
        backend
            .update_validated_config_domain(
                "webui",
                br#"{"auth_bypass_ips":["192.0.2.0/24","not-an-ip"]}"#,
            )
            .is_err()
    );
    for invalid in ["10", "192.168.1.", "0.0.0.0/0", "::/0"] {
        assert!(
            backend
                .update_validated_config_domain(
                    "webui",
                    format!(r#"{{"auth_bypass_ips":["{invalid}"]}}"#).as_bytes(),
                )
                .is_err(),
            "accepted unsafe bypass entry {invalid}"
        );
    }
    assert!(
        backend
            .update_validated_config_domain("webui", br#"{"focus_timeout":301}"#)
            .is_err()
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn admin_get_uses_nested_legacy_shape_and_read_only_defaults() {
    let (backend, root) = backend_with("admin", "{}");
    let response = backend.validated_config_domain("admin").unwrap();
    let value = json::parse(&response.body).unwrap();
    assert_eq!(
        value.get_path("name"),
        Some(&Value::String("Thingino Camera Admin".to_owned()))
    );
    assert_eq!(value.get_path("email"), Some(&Value::String(String::new())));
    assert_eq!(
        value.get_path("telegram"),
        Some(&Value::String(String::new()))
    );
    assert_eq!(
        value.get_path("discord"),
        Some(&Value::String(String::new()))
    );
    assert!(
        backend
            .update_validated_config_domain("admin", br#"{"name":"Lab camera"}"#)
            .is_ok()
    );
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("admin.name"),
        Some(&Value::String("Lab camera".to_owned()))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn ha_rejects_unsupported_outputs_and_preserves_mqtt_password() {
    let (backend, root) = backend_with(
        "ha",
        r#"{"enabled":false,"mqtt":{"host":"ha.local","port":1883,"username":"camera","password":"__SET_LOCALLY__","use_ssl":false,"tls_skip_verify":false},"state_interval":30,"discovery_interval":300,"camera_interval":10,"ota_check_interval":21600,"enable_motion":true,"enable_ir940":true,"enable_white_light":true}"#,
    );
    let response = backend.validated_config_domain("ha").unwrap();
    let redacted = json::parse(&response.body).unwrap();
    assert_eq!(redacted.get_path("mqtt.password"), Some(&Value::Null));
    assert_eq!(
        redacted.get_path("mqtt.password_set"),
        Some(&Value::Bool(true))
    );
    assert_eq!(redacted.get_path("enable_ir940"), Some(&Value::Bool(false)));
    assert!(
        backend
            .update_validated_config_domain("ha", br#"{"enable_ir940":true}"#)
            .is_err()
    );
    backend
        .update_validated_config_domain("ha", br#"{"mqtt":{"password":null},"enabled":true}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("ha.mqtt.password"),
        Some(&Value::String("__SET_LOCALLY__".to_owned()))
    );
    assert_eq!(
        stored.get_path("ha.enable_white_light"),
        Some(&Value::Bool(true))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn ha_intervals_report_runtime_minimums_and_reject_silently_clamped_updates() {
    let (backend, root) = backend_with(
        "ha",
        r#"{"enabled":false,"mqtt":{"host":"ha.local"},"state_interval":1,"discovery_interval":1,"camera_interval":1,"ota_check_interval":21600}"#,
    );
    let original = fs::read(&backend.paths.thingino_config).unwrap();
    let response = backend.validated_config_domain("ha").unwrap();
    let effective = json::parse(&response.body).unwrap();
    for (name, minimum) in HA_INTERVAL_MINIMUMS {
        assert_eq!(
            effective.get_path(name),
            Some(&Value::Number(minimum.to_string()))
        );
        for invalid in [minimum - 1, MAX_INTERVAL as u64 + 1] {
            let body = format!(r#"{{"{name}":{invalid}}}"#);
            assert!(
                backend
                    .update_validated_config_domain("ha", body.as_bytes())
                    .is_err()
            );
            assert_eq!(fs::read(&backend.paths.thingino_config).unwrap(), original);
        }
    }
    // Reading effective values and saving an unrelated field leave the legacy
    // interval values on disk intact. Only an explicit interval update replaces them.
    backend
        .update_validated_config_domain("ha", br#"{"enabled":true}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("ha.state_interval"),
        Some(&Value::Number("1".to_owned()))
    );
    backend
        .update_validated_config_domain(
            "ha",
            br#"{"state_interval":8,"discovery_interval":120,"camera_interval":10}"#,
        )
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    let effective = json::parse(&backend.validated_config_domain("ha").unwrap().body).unwrap();
    for (name, value) in [
        ("state_interval", 8),
        ("discovery_interval", 120),
        ("camera_interval", 10),
    ] {
        assert_eq!(
            stored.get_path(&format!("ha.{name}")),
            Some(&Value::Number(value.to_string()))
        );
        assert_eq!(
            effective.get_path(name),
            Some(&Value::Number(value.to_string()))
        );
    }
    assert_eq!(
        stored.get_path("ha.ota_check_interval"),
        Some(&Value::Number("21600".to_owned()))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn mqtt_sub_rejects_shell_actions_and_redacts_legacy_values_on_get() {
    let (backend, root) = backend_with(
        "mqtt_sub",
        r#"{"subscriptions":[{"action":"telegram-cam-agent \"$MQTT_PAYLOAD\"","enabled":true,"qos":0,"topic":"thingino/cam/%id/cmd"}],"tls_skip_verify":false}"#,
    );
    let response = backend.validated_config_domain("mqtt_sub").unwrap();
    let redacted = json::parse(&response.body).unwrap();
    let subscriptions = redacted
        .get_path("subscriptions")
        .and_then(Value::as_array)
        .expect("subscriptions array");
    assert_eq!(
        subscriptions[0].get_path("legacy_action"),
        Some(&Value::Bool(true))
    );
    assert!(
        !response
            .body
            .windows(b"telegram-cam-agent".len())
            .any(|part| part == b"telegram-cam-agent")
    );
    // An unrelated partial update must not erase the legacy action from
    // the stored document; only the response is sanitized.
    backend
        .update_validated_config_domain("mqtt_sub", br#"{"tls_skip_verify":true}"#)
        .unwrap();
    let stored = fs::read(&backend.paths.thingino_config).unwrap();
    assert!(
        stored
            .windows(b"telegram-cam-agent".len())
            .any(|part| part == b"telegram-cam-agent")
    );
    assert!(backend
        .update_validated_config_domain(
            "mqtt_sub",
            br#"{"subscriptions":[{"topic":"thingino/cam/%id/cmd","qos":0,"enabled":true,"action":"reboot"}]}"#,
        )
        .is_err());
    // No structured action can be persisted until a Rust action executor
    // replaces the legacy shell consumer.
    assert!(backend
        .update_validated_config_domain(
            "mqtt_sub",
            br#"{"subscriptions":[{"topic":"thingino/cam/%id/cmd","qos":1,"enabled":true,"action":{"type":"snapshot","stream":0}}]}"#,
        )
        .is_err());
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn mqtt_sub_secret_is_redacted_and_null_preserves_the_stored_value() {
    let (backend, root) = backend_with(
        "mqtt_sub",
        r#"{"host":"mqtt.local","port":1883,"username":"camera","password":"__SET_LOCALLY__","use_ssl":false,"tls_skip_verify":false,"subscriptions":[]}"#,
    );
    let response = backend.validated_config_domain("mqtt_sub").unwrap();
    let redacted = json::parse(&response.body).unwrap();
    assert_eq!(redacted.get_path("password"), Some(&Value::Null));
    assert_eq!(redacted.get_path("password_set"), Some(&Value::Bool(true)));
    backend
        .update_validated_config_domain("mqtt_sub", br#"{"password":null}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("mqtt_sub.password"),
        Some(&Value::String("__SET_LOCALLY__".to_owned()))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn rsyslog_explicit_local_setting_matches_startup_consumers() {
    for stored_keys in [
        r#""local_enabled":true"#,
        r#""local":true"#,
        r#""file":false,"local":true,"local_enabled":true"#,
    ] {
        for supplied in ["file", "local", "local_enabled"] {
            for enabled in [false, true] {
                let initial =
                    format!(r#"{{"host":"log.local","port":1514,"enabled":true,{stored_keys}}}"#);
                let (backend, root) = backend_with("rsyslog", &initial);
                let before = fs::read(&backend.paths.thingino_config).unwrap();
                let response = backend.validated_config_domain("rsyslog").unwrap();
                let observed = json::parse(&response.body).unwrap();
                assert_eq!(observed.get_path("file"), Some(&Value::Bool(true)));
                assert_eq!(fs::read(&backend.paths.thingino_config).unwrap(), before);
                backend
                    .update_validated_config_domain(
                        "rsyslog",
                        format!(r#"{{"{supplied}":{enabled}}}"#).as_bytes(),
                    )
                    .unwrap();
                let stored =
                    json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
                // S01syslogd consumes file/local, never local_enabled. Both
                // recognized aliases must not disagree after an explicit save.
                assert_eq!(stored.get_path("rsyslog.file"), Some(&Value::Bool(enabled)));
                assert_eq!(stored.get_path("rsyslog.local"), None);
                assert_eq!(stored.get_path("rsyslog.local_enabled"), None);
                assert_eq!(
                    stored.get_path("rsyslog.port"),
                    Some(&Value::Number("1514".into())),
                );
                assert_eq!(stored.get_path("rsyslog.enabled"), Some(&Value::Bool(true)));
                fs::remove_dir_all(root).unwrap();
            }
        }
    }
}

#[test]
fn rsyslog_read_is_nonmutating_and_explicit_save_uses_canonical_key() {
    let (backend, root) = backend_with("rsyslog", r#"{"host":"","local":false,"port":514}"#);
    let response = backend.validated_config_domain("rsyslog").unwrap();
    let redacted = json::parse(&response.body).unwrap();
    assert_eq!(
        redacted.get_path("host"),
        Some(&Value::String(String::new()))
    );
    assert_eq!(
        redacted.get_path("port"),
        Some(&Value::Number("514".to_owned()))
    );
    assert_eq!(redacted.get_path("enabled"), Some(&Value::Bool(false)));
    assert_eq!(redacted.get_path("file"), Some(&Value::Bool(false)));
    assert_eq!(redacted.get_path("local"), None);
    assert!(
        backend
            .update_validated_config_domain("rsyslog", br#"{"file":true,"local":false}"#,)
            .is_err()
    );
    backend
        .update_validated_config_domain("rsyslog", br#"{"file":true}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(stored.get_path("rsyslog.local"), None);
    assert_eq!(stored.get_path("rsyslog.file"), Some(&Value::Bool(true)));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn rsyslog_unrelated_save_preserves_legacy_local_setting() {
    let (backend, root) = backend_with(
        "rsyslog",
        r#"{"host":"log.local","port":514,"enabled":false,"local_enabled":true}"#,
    );
    backend
        .update_validated_config_domain("rsyslog", br#"{"port":1514}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("rsyslog.local_enabled"),
        Some(&Value::Bool(true))
    );
    assert_eq!(stored.get_path("rsyslog.file"), None);
    fs::remove_dir_all(root).unwrap();
}
