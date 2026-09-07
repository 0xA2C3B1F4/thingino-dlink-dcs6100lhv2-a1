use std::collections::BTreeMap;
use std::net::IpAddr;
use std::path::Path;

use super::super::*;

type Object = BTreeMap<String, Value>;

const MAX_ADMIN_STRING: usize = 256;
const MAX_WEBUI_USERNAME: usize = 64;
const MAX_WEBUI_BYPASS: usize = 4_096;
const MAX_HOST: usize = 255;
const MAX_MQTT_USERNAME: usize = 128;
const MAX_MQTT_CLIENT_ID: usize = 128;
const MAX_MQTT_TOPIC: usize = 512;
const MAX_SUBSCRIPTIONS: usize = 64;
const MAX_INTERVAL: i64 = 604_800;

const HA_BOOLEAN_FIELDS: &[&str] = &[
    "enable_motion",
    "enable_motion_guard",
    "enable_doorbell",
    "enable_live_view",
    "enable_daynight",
    "enable_privacy",
    "enable_snapshot",
    "enable_ircut",
    "enable_ir850",
    "enable_color",
    "enable_gain",
    "enable_rssi",
    "enable_firmware_version",
    "enable_firmware_timestamp",
    "enable_ota",
    "enable_reboot",
    "enable_ptz",
];

const ADMIN_STRING_FIELDS: &[&str] = &["name", "email", "telegram", "discord"];

impl PrudyntBackend {
    /// Return one validated and redacted Thingino configuration domain.
    ///
    /// Legacy MQTT action strings are deliberately removed from the response
    /// and replaced with `legacy_action: true`; they are never returned as
    /// executable strings.
    pub(in crate::camera) fn validated_config_domain(
        &self,
        domain: &str,
    ) -> Result<BackendResponse, BackendError> {
        validated_config_domain(self, domain)
    }

    /// Validate and persist a partial Thingino configuration domain.
    pub(in crate::camera) fn update_validated_config_domain(
        &self,
        domain: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        update_validated_config_domain(self, domain, body)
    }
}

pub(in crate::camera) fn validated_config_domain(
    backend: &PrudyntBackend,
    domain: &str,
) -> Result<BackendResponse, BackendError> {
    let document = read_domain_document(&backend.paths.thingino_config)?;
    let mut value = document
        .get_path(domain)
        .cloned()
        .unwrap_or_else(empty_object);
    if value.as_object().is_none() {
        return Err(BackendError::Protocol);
    }

    validate_domain(domain, &value, false)?;
    sanitize_stored_domain(domain, &mut value)?;
    synthesize_stored_domain(domain, &mut value)?;
    mask_secret_fields(&mut value);
    json_response(value)
}

pub(in crate::camera) fn update_validated_config_domain(
    backend: &PrudyntBackend,
    domain: &str,
    body: &[u8],
) -> Result<BackendResponse, BackendError> {
    let mut document = read_domain_document(&backend.paths.thingino_config)?;
    let mut update = json::parse(body).map_err(|_| BackendError::Protocol)?;
    validate_domain(domain, &update, true)?;

    let mut current = document
        .get_path(domain)
        .cloned()
        .unwrap_or_else(empty_object);
    if current.as_object().is_none() {
        return Err(BackendError::Protocol);
    }
    // Do not merge a validated patch into an already malformed or unknown
    // stored domain.  Legacy MQTT action strings and unsupported D-Link HA
    // fields are explicitly accepted by the read-only validation path.
    validate_domain(domain, &current, false)?;

    // D-Link has no 940 nm or white-light output. New attempts to enable
    // either entity are rejected by validate_ha. Do not rewrite an unrelated
    // legacy document as a side effect of a partial update.
    if domain == "rsyslog" {
        preserve_rsyslog_alias(&mut current, &mut update)?;
    }
    remove_unchanged_secret_fields(&mut update);
    current.merge(&update).map_err(|_| BackendError::Protocol)?;
    validate_domain(domain, &current, false)?;
    if domain == "ha"
        && current.get_path("enabled").and_then(Value::as_bool) == Some(true)
        && current
            .get_path("mqtt.host")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
    {
        return Err(BackendError::Protocol);
    }
    document
        .set_path(domain, current)
        .map_err(|_| BackendError::Protocol)?;

    let mut serialized = document.to_json().into_bytes();
    serialized.push(b'\n');
    write_in_place(&backend.paths.thingino_config, &serialized)?;
    Ok(BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec()))
}

fn empty_object() -> Value {
    Value::Object(BTreeMap::new())
}

fn read_domain_document(path: &Path) -> Result<Value, BackendError> {
    json::parse(&read_bounded(path, FILE_LIMIT)?).map_err(|_| BackendError::Protocol)
}

fn fields(value: &Value) -> Result<&Object, BackendError> {
    value.as_object().ok_or(BackendError::Protocol)
}

fn fields_mut(value: &mut Value) -> Result<&mut Object, BackendError> {
    match value {
        Value::Object(fields) => Ok(fields),
        _ => Err(BackendError::Protocol),
    }
}

fn reject_unknown(fields: &Object, allowed: &[&str]) -> Result<(), BackendError> {
    if fields
        .keys()
        .any(|name| !allowed.iter().any(|candidate| *candidate == name))
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Number(raw) => raw.parse::<i64>().ok(),
        _ => None,
    }
}

fn require_integer(value: &Value, minimum: i64, maximum: i64) -> Result<(), BackendError> {
    integer(value)
        .filter(|value| (minimum..=maximum).contains(value))
        .map(|_| ())
        .ok_or(BackendError::Protocol)
}

fn require_bool(value: &Value) -> Result<(), BackendError> {
    if matches!(value, Value::Bool(_)) {
        Ok(())
    } else {
        Err(BackendError::Protocol)
    }
}

fn require_enum(value: &Value, allowed: &[&str]) -> Result<(), BackendError> {
    match value {
        Value::String(value) if allowed.iter().any(|candidate| *candidate == value) => Ok(()),
        _ => Err(BackendError::Protocol),
    }
}

fn require_text(value: &Value, maximum: usize, allow_empty: bool) -> Result<(), BackendError> {
    let Value::String(value) = value else {
        return Err(BackendError::Protocol);
    };
    if value.len() > maximum
        || (!allow_empty && value.is_empty())
        || value
            .bytes()
            .any(|byte| byte == 0 || byte == b'\r' || byte == b'\n')
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn require_host(value: &Value, maximum: usize, allow_empty: bool) -> Result<(), BackendError> {
    require_text(value, maximum, allow_empty)?;
    if let Value::String(value) = value
        && value
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte == b'/')
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn require_secret(value: &Value, maximum: usize) -> Result<(), BackendError> {
    match value {
        Value::Null => Ok(()),
        Value::String(_) => require_text(value, maximum, true),
        _ => Err(BackendError::Protocol),
    }
}

fn valid_ip_entry(value: &str) -> bool {
    if value.is_empty() || value.len() > 64 || value.bytes().any(|byte| byte.is_ascii_control()) {
        return false;
    }
    if let Some((address, prefix)) = value.split_once('/') {
        let Ok(address) = address.parse::<IpAddr>() else {
            return false;
        };
        let Ok(prefix) = prefix.parse::<u8>() else {
            return false;
        };
        let maximum = if address.is_ipv4() { 32 } else { 128 };
        return prefix > 0 && prefix <= maximum;
    }
    value.parse::<IpAddr>().is_ok()
}

fn validate_bypass_string(value: &str) -> Result<(), BackendError> {
    if value.len() > MAX_WEBUI_BYPASS
        || value
            .bytes()
            .any(|byte| byte == 0 || byte == b'\r' || byte == b'\n')
    {
        return Err(BackendError::Protocol);
    }
    if value
        .split(|character: char| character == ',' || character.is_ascii_whitespace())
        .filter(|entry| !entry.is_empty())
        .any(|entry| !valid_ip_entry(entry))
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

fn validate_bypass(value: &Value) -> Result<(), BackendError> {
    match value {
        Value::String(value) => validate_bypass_string(value),
        Value::Array(entries) if entries.len() <= 64 => entries.iter().try_for_each(|entry| {
            let Value::String(entry) = entry else {
                return Err(BackendError::Protocol);
            };
            if !valid_ip_entry(entry) {
                return Err(BackendError::Protocol);
            }
            Ok(())
        }),
        _ => Err(BackendError::Protocol),
    }
}

fn validate_domain(domain: &str, value: &Value, update: bool) -> Result<(), BackendError> {
    let fields = fields(value)?;
    if update && fields.is_empty() {
        return Err(BackendError::Protocol);
    }
    match domain {
        "admin" => validate_admin(fields),
        "webui" => validate_webui(fields, update),
        "rsyslog" => validate_rsyslog(fields, update),
        "ha" => validate_ha(fields, update),
        "mqtt_sub" => validate_mqtt_sub(fields, update),
        _ => Err(BackendError::Protocol),
    }
}

fn validate_admin(fields: &Object) -> Result<(), BackendError> {
    reject_unknown(fields, ADMIN_STRING_FIELDS)?;
    for value in fields.values() {
        require_text(value, MAX_ADMIN_STRING, true)?;
    }
    Ok(())
}

fn validate_webui(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "username",
            "theme",
            "level",
            "paranoid",
            "track_focus",
            "focus_timeout",
            "auth_bypass_ips",
        ],
    )?;
    if update && fields.contains_key("username") {
        return Err(BackendError::Protocol);
    }
    if let Some(value) = fields.get("username") {
        require_text(value, MAX_WEBUI_USERNAME, false)?;
    }
    if let Some(value) = fields.get("theme") {
        require_enum(value, &["auto", "light", "dark"])?;
    }
    if let Some(value) = fields.get("level") {
        require_text(value, 32, false)?;
    }
    for name in ["paranoid", "track_focus"] {
        if let Some(value) = fields.get(name) {
            require_bool(value)?;
        }
    }
    if let Some(value) = fields.get("focus_timeout") {
        require_integer(value, 0, 300)?;
    }
    if let Some(value) = fields.get("auth_bypass_ips") {
        validate_bypass(value)?;
    }
    Ok(())
}

fn validate_rsyslog(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &["host", "port", "enabled", "file", "local", "local_enabled"],
    )?;
    if update
        && ["file", "local", "local_enabled"]
            .iter()
            .filter(|name| fields.contains_key(**name))
            .count()
            > 1
    {
        return Err(BackendError::Protocol);
    }
    if let Some(value) = fields.get("host") {
        require_host(value, MAX_HOST, true)?;
    }
    if let Some(value) = fields.get("port") {
        require_integer(value, 1, 65_535)?;
    }
    for name in ["enabled", "file", "local", "local_enabled"] {
        if let Some(value) = fields.get(name) {
            require_bool(value)?;
        }
    }
    Ok(())
}

fn validate_ha(root: &Object, update: bool) -> Result<(), BackendError> {
    let mut allowed = vec![
        "enabled",
        "mqtt",
        "device_name",
        "device_model",
        "discovery_prefix",
        "state_interval",
        "discovery_interval",
        "camera_interval",
        "ota_check_interval",
        "enable_ir940",
        "enable_white_light",
    ];
    allowed.extend_from_slice(HA_BOOLEAN_FIELDS);
    reject_unknown(root, &allowed)?;
    if let Some(value) = root.get("enabled") {
        require_bool(value)?;
    }
    for name in ["device_name", "device_model"] {
        if let Some(value) = root.get(name) {
            require_text(value, MAX_ADMIN_STRING, true)?;
        }
    }
    if let Some(value) = root.get("discovery_prefix") {
        require_text(value, 128, false)?;
        let prefix = value.as_str().ok_or(BackendError::Protocol)?;
        if !valid_topic(prefix) || prefix.contains(['+', '#']) {
            return Err(BackendError::Protocol);
        }
    }
    for name in [
        "state_interval",
        "discovery_interval",
        "camera_interval",
        "ota_check_interval",
    ] {
        if let Some(value) = root.get(name) {
            require_integer(value, 1, MAX_INTERVAL)?;
        }
    }
    for name in HA_BOOLEAN_FIELDS {
        if let Some(value) = root.get(*name) {
            require_bool(value)?;
            if update
                && matches!(*name, "enable_doorbell" | "enable_ota" | "enable_ptz")
                && value.as_bool() == Some(true)
            {
                return Err(BackendError::Protocol);
            }
        }
    }
    for name in ["enable_ir940", "enable_white_light"] {
        if let Some(value) = root.get(name) {
            require_bool(value)?;
            if update && value.as_bool() == Some(true) {
                return Err(BackendError::Protocol);
            }
        }
    }
    if let Some(value) = root.get("mqtt") {
        validate_ha_mqtt(fields(value)?)?;
    }
    Ok(())
}

fn validate_ha_mqtt(root: &Object) -> Result<(), BackendError> {
    reject_unknown(
        root,
        &[
            "host",
            "port",
            "username",
            "password",
            "client_id_prefix",
            "use_ssl",
            "tls_skip_verify",
        ],
    )?;
    if let Some(value) = root.get("host") {
        require_host(value, MAX_HOST, true)?;
    }
    if let Some(value) = root.get("port") {
        require_integer(value, 1, 65_535)?;
    }
    if let Some(value) = root.get("username") {
        require_text(value, MAX_MQTT_USERNAME, true)?;
    }
    if let Some(value) = root.get("password") {
        require_secret(value, MAX_MQTT_USERNAME)?;
    }
    if let Some(value) = root.get("client_id_prefix") {
        require_text(value, MAX_MQTT_CLIENT_ID, true)?;
    }
    for name in ["use_ssl", "tls_skip_verify"] {
        if let Some(value) = root.get(name) {
            require_bool(value)?;
        }
    }
    Ok(())
}

fn validate_mqtt_sub(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "enabled",
            "host",
            "port",
            "username",
            "password",
            "use_ssl",
            "tls_skip_verify",
            "subscriptions",
        ],
    )?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    if let Some(value) = fields.get("host") {
        require_host(value, MAX_HOST, true)?;
    }
    if let Some(value) = fields.get("port") {
        require_integer(value, 1, 65_535)?;
    }
    if let Some(value) = fields.get("username") {
        require_text(value, MAX_MQTT_USERNAME, true)?;
    }
    if let Some(value) = fields.get("password") {
        require_secret(value, MAX_MQTT_USERNAME)?;
    }
    for name in ["use_ssl", "tls_skip_verify"] {
        if let Some(value) = fields.get(name) {
            require_bool(value)?;
        }
    }
    if let Some(value) = fields.get("subscriptions") {
        if update {
            // mqtt-sub-dispatcher still executes only the legacy shell
            // action string.  Control has no action executor yet, so a POST
            // may not replace subscriptions with an object action (or an
            // action-less array that would discard the consumed command).
            return Err(BackendError::Protocol);
        }
        validate_subscriptions(value)?;
    }
    Ok(())
}

fn valid_topic(value: &str) -> bool {
    if value.is_empty()
        || value.len() > MAX_MQTT_TOPIC
        || value
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return false;
    }
    let levels = value.split('/').collect::<Vec<_>>();
    for (index, level) in levels.iter().enumerate() {
        if level.contains('#') && !(*level == "#" && index + 1 == levels.len()) {
            return false;
        }
        if level.contains('+') && *level != "+" {
            return false;
        }
    }
    true
}

fn validate_subscriptions(value: &Value) -> Result<(), BackendError> {
    let Value::Array(entries) = value else {
        return Err(BackendError::Protocol);
    };
    if entries.len() > MAX_SUBSCRIPTIONS {
        return Err(BackendError::Protocol);
    }
    for entry in entries {
        let entry = fields(entry)?;
        reject_unknown(entry, &["topic", "qos", "enabled", "action"])?;
        let topic = entry.get("topic").ok_or(BackendError::Protocol)?;
        let Value::String(topic) = topic else {
            return Err(BackendError::Protocol);
        };
        if !valid_topic(topic) {
            return Err(BackendError::Protocol);
        }
        require_integer(entry.get("qos").ok_or(BackendError::Protocol)?, 0, 2)?;
        require_bool(entry.get("enabled").ok_or(BackendError::Protocol)?)?;
        if let Some(action) = entry.get("action")
            && !matches!(action, Value::String(_) | Value::Object(_))
        {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn sanitize_stored_domain(domain: &str, value: &mut Value) -> Result<(), BackendError> {
    if domain == "ha" {
        let root = fields_mut(value)?;
        for name in [
            "enable_doorbell",
            "enable_ir940",
            "enable_ota",
            "enable_ptz",
            "enable_white_light",
        ] {
            if root.contains_key(name) {
                root.insert(name.to_owned(), Value::Bool(false));
            }
        }
    }
    if domain == "mqtt_sub" {
        let root = fields_mut(value)?;
        let Some(Value::Array(entries)) = root.get_mut("subscriptions") else {
            return Ok(());
        };
        for entry in entries {
            let entry = fields_mut(entry)?;
            if entry.contains_key("action") {
                entry.remove("action");
                entry.insert("legacy_action".to_owned(), Value::Bool(true));
            }
        }
    }
    Ok(())
}

fn synthesize_stored_domain(domain: &str, value: &mut Value) -> Result<(), BackendError> {
    let root = fields_mut(value)?;
    match domain {
        "admin" => {
            // json-config-admin.cgi stores this domain as a nested object and
            // creates the default name on GET.  Keep that compatibility in
            // the response without writing a missing domain back to disk.
            root.entry("name".to_owned())
                .or_insert_with(|| Value::String("Thingino Camera Admin".to_owned()));
            for name in ["email", "telegram", "discord"] {
                root.entry(name.to_owned())
                    .or_insert_with(|| Value::String(String::new()));
            }
        }
        "webui" => {
            // Authentication is intentionally fixed to the root account in
            // web_auth.rs.  The stock profile predates these fields, so the
            // API supplies the same defaults as the legacy WebUI instead of
            // making the typed client special-case an incomplete document.
            root.insert("username".to_owned(), Value::String("root".to_owned()));
            root.entry("theme".to_owned())
                .or_insert_with(|| Value::String("auto".to_owned()));
            root.entry("paranoid".to_owned())
                .or_insert(Value::Bool(false));
            root.entry("track_focus".to_owned())
                .or_insert(Value::Bool(false));
            root.entry("focus_timeout".to_owned())
                .or_insert_with(|| Value::Number("0".to_owned()));
            if let Some(Value::Array(entries)) = root.get("auth_bypass_ips").cloned() {
                let entries = entries
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(",");
                root.insert("auth_bypass_ips".to_owned(), Value::String(entries));
            } else {
                root.entry("auth_bypass_ips".to_owned())
                    .or_insert_with(|| Value::String(String::new()));
            }
        }
        "rsyslog" => {
            // The installed profile uses `local`; the public API uses the
            // canonical `file` field.  Keep this translation response-only so
            // a GET never mutates a device's legacy configuration.
            let file = root
                .get("file")
                .cloned()
                .or_else(|| root.get("local_enabled").cloned())
                .or_else(|| root.get("local").cloned())
                .unwrap_or(Value::Bool(false));
            root.remove("local");
            root.remove("local_enabled");
            root.insert("file".to_owned(), file);
            root.entry("host".to_owned())
                .or_insert_with(|| Value::String(String::new()));
            root.entry("port".to_owned())
                .or_insert_with(|| Value::Number("514".to_owned()));
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
        }
        "ha" => {
            root.insert("doorbell_supported".to_owned(), Value::Bool(false));
            root.insert("ota_supported".to_owned(), Value::Bool(false));
        }
        _ => {}
    }
    Ok(())
}

fn preserve_rsyslog_alias(current: &mut Value, update: &mut Value) -> Result<(), BackendError> {
    let current = fields_mut(current)?;
    let update = fields_mut(update)?;
    let aliases = ["file", "local", "local_enabled"];
    let stored_alias = aliases.iter().find(|alias| current.contains_key(**alias));
    if let Some(stored_alias) = stored_alias {
        let supplied = aliases
            .iter()
            .filter(|alias| update.contains_key(**alias))
            .copied()
            .collect::<Vec<_>>();
        if supplied.len() > 1 {
            // Two names for the same setting are ambiguous.  Reject the
            // update rather than silently choosing one and hiding a typo.
            return Err(BackendError::Protocol);
        }
        if let Some(source_alias) = supplied.first()
            && *source_alias != *stored_alias
            && let Some(value) = update.remove(*source_alias)
        {
            update.insert((*stored_alias).to_owned(), value);
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "domains_tests.rs"]
mod tests;
