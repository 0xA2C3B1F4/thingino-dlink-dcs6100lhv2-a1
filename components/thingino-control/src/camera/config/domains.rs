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
const MAX_WEBHOOK_URL: usize = 1_024;

const HA_INTERVAL_MINIMUMS: &[(&str, u64)] = &[
    ("state_interval", ha::MIN_STATE_INTERVAL),
    ("discovery_interval", ha::MIN_DISCOVERY_INTERVAL),
    ("camera_interval", ha::MIN_CAMERA_INTERVAL),
];

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

impl HostBackend {
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

    pub(crate) fn motion_webhook_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("motion_webhook"),
            "POST" if !body.is_empty() => {
                self.update_validated_config_domain("motion_webhook", body)
            }
            _ => Err(BackendError::Protocol),
        }
    }

    pub(crate) fn motion_webhook_config(&self) -> Result<Value, BackendError> {
        let document = read_domain_document(&self.paths.thingino_config)?;
        let value = document
            .get_path("motion_webhook")
            .cloned()
            .unwrap_or_else(empty_object);
        validate_domain("motion_webhook", &value, false)?;
        Ok(value)
    }

    pub(crate) fn motion_ntfy_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("motion_ntfy"),
            "POST" if !body.is_empty() => self.update_validated_config_domain("motion_ntfy", body),
            _ => Err(BackendError::Protocol),
        }
    }

    pub(crate) fn motion_ntfy_config(&self) -> Result<Value, BackendError> {
        let document = read_domain_document(&self.paths.thingino_config)?;
        let value = document
            .get_path("motion_ntfy")
            .cloned()
            .unwrap_or_else(empty_object);
        validate_domain("motion_ntfy", &value, false)?;
        Ok(value)
    }

    pub(crate) fn motion_gotify_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("motion_gotify"),
            "POST" if !body.is_empty() => {
                self.update_validated_config_domain("motion_gotify", body)
            }
            _ => Err(BackendError::Protocol),
        }
    }

    pub(crate) fn motion_gotify_config(&self) -> Result<Value, BackendError> {
        let document = read_domain_document(&self.paths.thingino_config)?;
        let value = document
            .get_path("motion_gotify")
            .cloned()
            .unwrap_or_else(empty_object);
        validate_domain("motion_gotify", &value, false)?;
        Ok(value)
    }

    pub(crate) fn motion_email_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("motion_email"),
            "POST" if !body.is_empty() => self.update_validated_config_domain("motion_email", body),
            _ => Err(BackendError::Protocol),
        }
    }

    pub(crate) fn motion_email_config(&self) -> Result<Value, BackendError> {
        let document = read_domain_document(&self.paths.thingino_config)?;
        let value = document
            .get_path("motion_email")
            .cloned()
            .unwrap_or_else(empty_object);
        validate_domain("motion_email", &value, false)?;
        Ok(value)
    }

    pub(crate) fn motion_ftp_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("motion_ftp"),
            "POST" if !body.is_empty() => self.update_validated_config_domain("motion_ftp", body),
            _ => Err(BackendError::Protocol),
        }
    }

    pub(crate) fn motion_ftp_config(&self) -> Result<Value, BackendError> {
        let document = read_domain_document(&self.paths.thingino_config)?;
        let value = document
            .get_path("motion_ftp")
            .cloned()
            .unwrap_or_else(empty_object);
        validate_domain("motion_ftp", &value, false)?;
        Ok(value)
    }

    pub(crate) fn motion_telegram_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("motion_telegram"),
            "POST" if !body.is_empty() => {
                self.update_validated_config_domain("motion_telegram", body)
            }
            _ => Err(BackendError::Protocol),
        }
    }

    pub(crate) fn motion_telegram_config(&self) -> Result<Value, BackendError> {
        let document = read_domain_document(&self.paths.thingino_config)?;
        let value = document
            .get_path("motion_telegram")
            .cloned()
            .unwrap_or_else(empty_object);
        validate_domain("motion_telegram", &value, false)?;
        Ok(value)
    }
}

pub(in crate::camera) fn validated_config_domain(
    backend: &HostBackend,
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
    backend: &HostBackend,
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
        normalize_rsyslog_local_setting(&mut current, &mut update)?;
    }
    if domain == "motion_webhook" {
        let update = fields_mut(&mut update)?;
        if update.get("url").is_some_and(|value| {
            matches!(value, Value::Null) || matches!(value, Value::String(text) if text.is_empty())
        }) {
            update.remove("url");
        }
        update.remove("url_set");
    }
    if domain == "motion_ntfy" {
        normalize_motion_ntfy_update(&mut current, &mut update)?;
    }
    if domain == "motion_gotify" {
        normalize_secret_update(&mut current, &mut update, &["endpoint", "token"])?;
    }
    if domain == "motion_email" {
        normalize_secret_update(&mut current, &mut update, &["password"])?;
    }
    if domain == "motion_ftp" {
        normalize_secret_update(&mut current, &mut update, &["password"])?;
    }
    if domain == "motion_telegram" {
        normalize_secret_update(&mut current, &mut update, &["bot_token", "chat_id"])?;
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
        "motion_webhook" => validate_motion_webhook(fields, update),
        "motion_ntfy" => validate_motion_ntfy(fields, update),
        "motion_gotify" => validate_motion_gotify(fields, update),
        "motion_email" => validate_motion_email(fields, update),
        "motion_ftp" => validate_motion_ftp(fields, update),
        "motion_telegram" => validate_motion_telegram(fields, update),
        _ => Err(BackendError::Protocol),
    }
}

fn validate_motion_ftp(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "enabled",
            "host",
            "port",
            "tls_mode",
            "username",
            "password",
            "password_set",
            "clear_password",
            "path",
        ],
    )?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    if let Some(value) = fields.get("host") {
        require_text(value, MAX_HOST, update)?;
        if value
            .as_str()
            .is_some_and(|host| !host.is_empty() && !valid_smtp_host(host))
        {
            return Err(BackendError::Protocol);
        }
    }
    if let Some(value) = fields.get("port") {
        require_integer(value, 1, 65_535)?;
    }
    if let Some(value) = fields.get("tls_mode") {
        require_enum(value, &["explicit"])?;
    }
    if let Some(value) = fields.get("username") {
        require_text(value, 256, update)?;
    }
    if let Some(value) = fields.get("password") {
        require_secret(value, 512)?;
    }
    for name in ["password_set", "clear_password"] {
        if let Some(value) = fields.get(name) {
            if !update {
                return Err(BackendError::Protocol);
            }
            require_bool(value)?;
        }
    }
    if fields.get("clear_password").and_then(Value::as_bool) == Some(true)
        && fields
            .get("password")
            .and_then(Value::as_str)
            .is_some_and(|password| !password.is_empty())
    {
        return Err(BackendError::Protocol);
    }
    if let Some(value) = fields.get("path") {
        require_text(value, 256, true)?;
        let path = value.as_str().ok_or(BackendError::Protocol)?;
        if path.starts_with('/')
            || path.ends_with('/')
            || path.contains("//")
            || path.split('/').any(|part| part == "." || part == "..")
            || !path.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'-' | b'_' | b'.')
            })
        {
            return Err(BackendError::Protocol);
        }
    }
    if !update && fields.get("enabled").and_then(Value::as_bool) == Some(true) {
        for name in ["host", "username", "password"] {
            if fields
                .get(name)
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
            {
                return Err(BackendError::Protocol);
            }
        }
    }
    Ok(())
}

fn validate_motion_email(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "enabled",
            "host",
            "port",
            "tls_mode",
            "username",
            "password",
            "password_set",
            "clear_password",
            "from_address",
            "to_address",
        ],
    )?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    if let Some(value) = fields.get("host") {
        require_text(value, MAX_HOST, update)?;
        if value
            .as_str()
            .is_some_and(|host| !host.is_empty() && !valid_smtp_host(host))
        {
            return Err(BackendError::Protocol);
        }
    }
    if let Some(value) = fields.get("port") {
        require_integer(value, 1, 65_535)?;
    }
    if let Some(value) = fields.get("tls_mode") {
        require_enum(value, &["starttls", "implicit"])?;
    }
    if let Some(value) = fields.get("username") {
        require_text(value, 256, true)?;
        reject_mail_control(value.as_str().ok_or(BackendError::Protocol)?)?;
    }
    if let Some(value) = fields.get("password") {
        require_secret(value, 512)?;
    }
    for name in ["password_set", "clear_password"] {
        if let Some(value) = fields.get(name) {
            if !update {
                return Err(BackendError::Protocol);
            }
            require_bool(value)?;
        }
    }
    if fields.get("clear_password").and_then(Value::as_bool) == Some(true)
        && fields
            .get("password")
            .and_then(Value::as_str)
            .is_some_and(|password| !password.is_empty())
    {
        return Err(BackendError::Protocol);
    }
    for name in ["from_address", "to_address"] {
        if let Some(value) = fields.get(name) {
            require_text(value, 320, update)?;
            if value
                .as_str()
                .is_some_and(|address| !address.is_empty() && !valid_mailbox(address))
            {
                return Err(BackendError::Protocol);
            }
        }
    }
    if !update {
        let enabled = fields.get("enabled").and_then(Value::as_bool) == Some(true);
        let required_missing = ["host", "from_address", "to_address"].iter().any(|name| {
            fields
                .get(*name)
                .and_then(Value::as_str)
                .is_none_or(str::is_empty)
        });
        let username_empty = fields
            .get("username")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty);
        let password_empty = fields
            .get("password")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty);
        if enabled && (required_missing || username_empty != password_empty) {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn reject_mail_control(value: &str) -> Result<(), BackendError> {
    if value.bytes().any(|byte| byte.is_ascii_control()) {
        Err(BackendError::Protocol)
    } else {
        Ok(())
    }
}

fn valid_smtp_host(host: &str) -> bool {
    if host.parse::<IpAddr>().is_ok() {
        return true;
    }
    !host.is_empty()
        && host.len() <= MAX_HOST
        && !host.starts_with('.')
        && !host.ends_with('.')
        && host.split('.').all(|label| {
            !label.is_empty()
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        })
}

fn valid_mailbox(address: &str) -> bool {
    if !address.is_ascii()
        || address
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
        || address.contains(['<', '>', ',', ';'])
    {
        return false;
    }
    let mut parts = address.split('@');
    let (Some(local), Some(host), None) = (parts.next(), parts.next(), parts.next()) else {
        return false;
    };
    !local.is_empty() && local.len() <= 64 && valid_smtp_host(host)
}

fn validate_motion_gotify(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "enabled",
            "endpoint",
            "endpoint_set",
            "token",
            "token_set",
            "clear_endpoint",
            "clear_token",
        ],
    )?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    for name in ["endpoint_set", "token_set", "clear_endpoint", "clear_token"] {
        if let Some(value) = fields.get(name) {
            if !update {
                return Err(BackendError::Protocol);
            }
            require_bool(value)?;
        }
    }
    if let Some(value) = fields.get("endpoint")
        && !(update
            && (matches!(value, Value::Null)
                || matches!(value, Value::String(text) if text.is_empty())))
    {
        require_text(value, MAX_WEBHOOK_URL, false)?;
        if !valid_webhook_url(value.as_str().ok_or(BackendError::Protocol)?) {
            return Err(BackendError::Protocol);
        }
    }
    if let Some(value) = fields.get("token") {
        require_secret(value, 512)?;
    }
    for name in ["endpoint", "token"] {
        if fields
            .get(&format!("clear_{name}"))
            .and_then(Value::as_bool)
            == Some(true)
            && fields
                .get(name)
                .and_then(Value::as_str)
                .is_some_and(|text| !text.is_empty())
        {
            return Err(BackendError::Protocol);
        }
    }
    if !update {
        let enabled = fields.get("enabled").and_then(Value::as_bool) == Some(true);
        let endpoint = fields
            .get("endpoint")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let token = fields
            .get("token")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if (enabled && (endpoint.is_empty() || token.is_empty()))
            || (!token.is_empty() && !endpoint.starts_with("https://"))
        {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn validate_motion_telegram(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "enabled",
            "bot_token",
            "bot_token_set",
            "chat_id",
            "chat_id_set",
            "clear_bot_token",
            "clear_chat_id",
        ],
    )?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    for name in [
        "bot_token_set",
        "chat_id_set",
        "clear_bot_token",
        "clear_chat_id",
    ] {
        if let Some(value) = fields.get(name) {
            if !update {
                return Err(BackendError::Protocol);
            }
            require_bool(value)?;
        }
    }
    if let Some(value) = fields.get("bot_token") {
        require_secret(value, 512)?;
        if value.as_str().is_some_and(|text| {
            !text.is_empty()
                && !text
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b':' | b'_' | b'-'))
        }) {
            return Err(BackendError::Protocol);
        }
    }
    if let Some(value) = fields.get("chat_id") {
        require_secret(value, 256)?;
        if value.as_str().is_some_and(|text| {
            !text.is_empty()
                && !text
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'@' | b'_' | b'-'))
        }) {
            return Err(BackendError::Protocol);
        }
    }
    for name in ["bot_token", "chat_id"] {
        if fields
            .get(&format!("clear_{name}"))
            .and_then(Value::as_bool)
            == Some(true)
            && fields
                .get(name)
                .and_then(Value::as_str)
                .is_some_and(|text| !text.is_empty())
        {
            return Err(BackendError::Protocol);
        }
    }
    if !update {
        let enabled = fields.get("enabled").and_then(Value::as_bool) == Some(true);
        if enabled
            && ["bot_token", "chat_id"].iter().any(|name| {
                fields
                    .get(*name)
                    .and_then(Value::as_str)
                    .is_none_or(str::is_empty)
            })
        {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn validate_motion_ntfy(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(
        fields,
        &[
            "enabled",
            "url",
            "url_set",
            "token",
            "token_set",
            "clear_token",
        ],
    )?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    for name in ["url_set", "token_set", "clear_token"] {
        if let Some(value) = fields.get(name) {
            if !update {
                return Err(BackendError::Protocol);
            }
            require_bool(value)?;
        }
    }
    if let Some(value) = fields.get("url") {
        if update
            && (matches!(value, Value::Null) || value.as_str().is_some_and(|url| url.is_empty()))
        {
            // Null or blank preserves the stored write-only endpoint.
        } else {
            require_text(value, MAX_WEBHOOK_URL, false)?;
            if !valid_webhook_url(value.as_str().ok_or(BackendError::Protocol)?) {
                return Err(BackendError::Protocol);
            }
        }
    }
    if let Some(value) = fields.get("token") {
        require_secret(value, 512)?;
    }
    if fields.get("clear_token").and_then(Value::as_bool) == Some(true)
        && fields
            .get("token")
            .and_then(Value::as_str)
            .is_some_and(|token| !token.is_empty())
    {
        return Err(BackendError::Protocol);
    }
    if !update {
        let enabled = fields.get("enabled").and_then(Value::as_bool) == Some(true);
        let url = fields
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let token = fields
            .get("token")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if (enabled && url.is_empty()) || (!token.is_empty() && !url.starts_with("https://")) {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn validate_motion_webhook(fields: &Object, update: bool) -> Result<(), BackendError> {
    reject_unknown(fields, &["enabled", "url", "url_set"])?;
    if let Some(value) = fields.get("enabled") {
        require_bool(value)?;
    }
    if let Some(value) = fields.get("url_set") {
        if !update {
            return Err(BackendError::Protocol);
        }
        require_bool(value)?;
    }
    if let Some(value) = fields.get("url") {
        if update && matches!(value, Value::Null) {
            // Null preserves the stored write-only endpoint.
        } else {
            require_text(value, MAX_WEBHOOK_URL, false)?;
            let url = value.as_str().ok_or(BackendError::Protocol)?;
            if !valid_webhook_url(url) {
                return Err(BackendError::Protocol);
            }
        }
    }
    if !update
        && fields.get("enabled").and_then(Value::as_bool) == Some(true)
        && fields
            .get("url")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
    {
        return Err(BackendError::Protocol);
    }
    Ok(())
}

pub(super) fn valid_webhook_url(url: &str) -> bool {
    let Some(rest) = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
    else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    valid_webhook_authority(authority)
        && !url.contains('#')
        && url.is_ascii()
        && !url
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
}

fn valid_webhook_authority(authority: &str) -> bool {
    if authority.is_empty() || authority.contains('@') {
        return false;
    }
    let (host, port) = if let Some(ipv6) = authority.strip_prefix('[') {
        let Some(close) = ipv6.find(']') else {
            return false;
        };
        let host = &ipv6[..close];
        let suffix = &ipv6[close + 1..];
        if host.parse::<std::net::Ipv6Addr>().is_err() {
            return false;
        }
        let port = if suffix.is_empty() {
            None
        } else if let Some(port) = suffix.strip_prefix(':') {
            Some(port)
        } else {
            return false;
        };
        (host, port)
    } else if authority.matches(':').count() == 1 {
        let (host, port) = authority.rsplit_once(':').unwrap();
        (host, Some(port))
    } else {
        (authority, None)
    };
    !host.is_empty()
        && (authority.starts_with('[')
            || host
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-')))
        && port
            .is_none_or(|port| !port.is_empty() && port.parse::<u16>().is_ok_and(|port| port != 0))
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
    for &(name, minimum) in HA_INTERVAL_MINIMUMS {
        if let Some(value) = root.get(name) {
            // Old stored values remain readable. New writes must match the
            // worker's actual minimum instead of being silently clamped.
            require_integer(value, if update { minimum as i64 } else { 1 }, MAX_INTERVAL)?;
        }
    }
    if let Some(value) = root.get("ota_check_interval") {
        // Retained for old clients and documents; no OTA worker consumes it.
        require_integer(value, 1, MAX_INTERVAL)?;
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
    if domain == "motion_webhook" {
        let root = fields_mut(value)?;
        let is_set = root
            .remove("url")
            .and_then(|value| match value {
                Value::String(value) => Some(!value.is_empty()),
                _ => None,
            })
            .unwrap_or(false);
        root.insert("url".to_owned(), Value::Null);
        root.insert("url_set".to_owned(), Value::Bool(is_set));
    }
    if domain == "motion_ntfy" {
        let root = fields_mut(value)?;
        let is_set = root
            .remove("url")
            .and_then(|value| match value {
                Value::String(text) => Some(!text.is_empty()),
                _ => None,
            })
            .unwrap_or(false);
        root.insert("url".to_owned(), Value::Null);
        root.insert("url_set".to_owned(), Value::Bool(is_set));
    }
    if domain == "motion_gotify" {
        redact_named_fields(value, &["endpoint"])?;
    }
    if domain == "motion_telegram" {
        redact_named_fields(value, &["bot_token", "chat_id"])?;
    }
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
            let file = match (root.get("file"), root.get("local")) {
                (None, None) => root
                    .get("local_enabled")
                    .cloned()
                    .unwrap_or(Value::Bool(false)),
                (file, local) => Value::Bool(
                    file == Some(&Value::Bool(true)) || local == Some(&Value::Bool(true)),
                ),
            };
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
            for &(name, minimum) in HA_INTERVAL_MINIMUMS {
                if let Some(value) = root.get_mut(name) {
                    let Value::Number(text) = value else {
                        return Err(BackendError::Protocol);
                    };
                    let interval = text.parse::<u64>().map_err(|_| BackendError::Protocol)?;
                    *value = Value::Number(interval.max(minimum).to_string());
                }
            }
            root.insert("doorbell_supported".to_owned(), Value::Bool(false));
            root.insert("ota_supported".to_owned(), Value::Bool(false));
        }
        "motion_webhook" => {
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
            root.entry("url".to_owned()).or_insert(Value::Null);
            root.entry("url_set".to_owned())
                .or_insert(Value::Bool(false));
        }
        "motion_ntfy" => {
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
            for name in ["url", "token"] {
                root.entry(name.to_owned()).or_insert(Value::Null);
                root.entry(format!("{name}_set"))
                    .or_insert(Value::Bool(false));
            }
        }
        "motion_gotify" => {
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
            for name in ["endpoint", "token"] {
                root.entry(name.to_owned()).or_insert(Value::Null);
                root.entry(format!("{name}_set"))
                    .or_insert(Value::Bool(false));
            }
        }
        "motion_email" => {
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
            root.entry("host".to_owned())
                .or_insert_with(|| Value::String(String::new()));
            root.entry("port".to_owned())
                .or_insert_with(|| Value::Number("587".to_owned()));
            root.entry("tls_mode".to_owned())
                .or_insert_with(|| Value::String("starttls".to_owned()));
            for name in ["username", "from_address", "to_address"] {
                root.entry(name.to_owned())
                    .or_insert_with(|| Value::String(String::new()));
            }
            root.entry("password".to_owned()).or_insert(Value::Null);
            root.entry("password_set".to_owned())
                .or_insert(Value::Bool(false));
        }
        "motion_ftp" => {
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
            root.entry("host".to_owned())
                .or_insert_with(|| Value::String(String::new()));
            root.entry("port".to_owned())
                .or_insert_with(|| Value::Number("21".to_owned()));
            root.entry("tls_mode".to_owned())
                .or_insert_with(|| Value::String("explicit".to_owned()));
            for name in ["username", "path"] {
                root.entry(name.to_owned())
                    .or_insert_with(|| Value::String(String::new()));
            }
            root.entry("password".to_owned()).or_insert(Value::Null);
            root.entry("password_set".to_owned())
                .or_insert(Value::Bool(false));
        }
        "motion_telegram" => {
            root.entry("enabled".to_owned())
                .or_insert(Value::Bool(false));
            for name in ["bot_token", "chat_id"] {
                root.entry(name.to_owned()).or_insert(Value::Null);
                root.entry(format!("{name}_set"))
                    .or_insert(Value::Bool(false));
            }
        }
        _ => {}
    }
    Ok(())
}

fn normalize_motion_ntfy_update(
    current: &mut Value,
    update: &mut Value,
) -> Result<(), BackendError> {
    let current = fields_mut(current)?;
    let update = fields_mut(update)?;
    let clear_token = update
        .remove("clear_token")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    for name in ["url", "token"] {
        if update.get(name).is_some_and(|value| {
            matches!(value, Value::Null) || matches!(value, Value::String(text) if text.is_empty())
        }) {
            update.remove(name);
        }
    }
    update.remove("url_set");
    update.remove("token_set");
    if clear_token {
        current.remove("token");
        update.remove("token");
    }
    Ok(())
}

fn normalize_secret_update(
    current: &mut Value,
    update: &mut Value,
    names: &[&str],
) -> Result<(), BackendError> {
    let current = fields_mut(current)?;
    let update = fields_mut(update)?;
    for name in names {
        let clear = update
            .remove(&format!("clear_{name}"))
            .and_then(|value| value.as_bool())
            .unwrap_or(false);
        update.remove(&format!("{name}_set"));
        if update.get(*name).is_some_and(|value| {
            matches!(value, Value::Null) || matches!(value, Value::String(text) if text.is_empty())
        }) {
            update.remove(*name);
        }
        if clear {
            current.remove(*name);
            update.remove(*name);
        }
    }
    Ok(())
}

fn redact_named_fields(value: &mut Value, names: &[&str]) -> Result<(), BackendError> {
    let root = fields_mut(value)?;
    for name in names {
        let is_set = root
            .remove(*name)
            .and_then(|value| value.as_str().map(|text| !text.is_empty()))
            .unwrap_or(false);
        root.insert((*name).to_owned(), Value::Null);
        root.insert(format!("{name}_set"), Value::Bool(is_set));
    }
    Ok(())
}

fn normalize_rsyslog_local_setting(
    current: &mut Value,
    update: &mut Value,
) -> Result<(), BackendError> {
    let current = fields_mut(current)?;
    let update = fields_mut(update)?;
    let aliases = ["file", "local", "local_enabled"];
    let supplied = aliases
        .iter()
        .filter(|alias| update.contains_key(**alias))
        .copied()
        .collect::<Vec<_>>();
    if supplied.len() > 1 {
        return Err(BackendError::Protocol);
    }
    if let Some(alias) = supplied.first() {
        let value = update.remove(*alias).ok_or(BackendError::Protocol)?;
        // S01syslogd reads file/local but not local_enabled, and ORs the
        // recognized values. Normalize only an explicitly supplied setting
        // so a stale true alias cannot defeat a requested false value.
        current.remove("local");
        current.remove("local_enabled");
        update.insert("file".to_owned(), value);
    }
    Ok(())
}

#[cfg(test)]
#[path = "domains_tests.rs"]
mod tests;
