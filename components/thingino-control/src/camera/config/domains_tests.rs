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

fn backend_with(domain: &str, value: &str) -> (PrudyntBackend, std::path::PathBuf) {
    let root = task_temp(domain);
    let path = root.join("thingino.json");
    fs::write(&path, format!("{{\"{domain}\":{value}}}\n")).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: path,
        ..CameraPaths::default()
    });
    (backend, root)
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
fn rsyslog_alias_keeps_the_persisted_key_name() {
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
    assert_eq!(stored.get_path("rsyslog.local"), Some(&Value::Bool(true)));
    assert_eq!(stored.get_path("rsyslog.file"), None);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn rsyslog_local_enabled_alias_is_also_preserved() {
    let (backend, root) = backend_with(
        "rsyslog",
        r#"{"host":"log.local","port":514,"enabled":false,"local_enabled":true}"#,
    );
    backend
        .update_validated_config_domain("rsyslog", br#"{"file":false}"#)
        .unwrap();
    let stored = json::parse(&fs::read(&backend.paths.thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("rsyslog.local_enabled"),
        Some(&Value::Bool(false))
    );
    assert_eq!(stored.get_path("rsyslog.file"), None);
    fs::remove_dir_all(root).unwrap();
}
