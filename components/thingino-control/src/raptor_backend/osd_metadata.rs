use super::*;
use std::collections::BTreeMap;

const MAX_ENTRIES: usize = 16;
const MAX_NAME_BYTES: usize = 31;
const MAX_FORMAT_BYTES: usize = 95;
const MAX_COORDINATE: i64 = 8192;

fn valid_text(value: &str, maximum: usize) -> bool {
    !value.is_empty() && value.len() <= maximum && !value.chars().any(char::is_control)
}

fn valid_name(value: &str) -> bool {
    valid_text(value, MAX_NAME_BYTES)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-.".contains(&byte))
}

fn valid_position(value: &str) -> bool {
    let Some((x, y)) = value.split_once(',') else {
        return false;
    };
    !x.is_empty()
        && !y.is_empty()
        && !x.starts_with('+')
        && !y.starts_with('+')
        && x.parse::<i64>()
            .is_ok_and(|number| (-MAX_COORDINATE..=MAX_COORDINATE).contains(&number))
        && y.parse::<i64>()
            .is_ok_and(|number| (-MAX_COORDINATE..=MAX_COORDINATE).contains(&number))
}

fn scalar_format(value: &str) -> bool {
    let mut replacements = 0;
    let mut chars = value.chars();
    while let Some(character) = chars.next() {
        if character != '%' {
            continue;
        }
        match chars.next() {
            Some('%') => {}
            Some('s') => replacements += 1,
            _ => return false,
        }
    }
    replacements == 1
}

fn timestamp_format(value: &str) -> bool {
    let mut chars = value.chars();
    while let Some(character) = chars.next() {
        if character == '%'
            && chars
                .next()
                .is_none_or(|token| token != '%' && !"FTYmdHMSZzjabB".contains(token))
        {
            return false;
        }
    }
    true
}

fn uptime_format(value: &str) -> bool {
    let bytes = value.as_bytes();
    let mut offset = 0;
    let mut replacements = 0;
    while offset < bytes.len() {
        if bytes[offset] != b'%' {
            offset += 1;
            continue;
        }
        if bytes.get(offset + 1) == Some(&b'%') {
            offset += 2;
            continue;
        }
        let length = if bytes.get(offset + 1..offset + 5) == Some(b"02lu") {
            5
        } else if bytes.get(offset + 1..offset + 3) == Some(b"lu") {
            3
        } else {
            return false;
        };
        replacements += 1;
        offset += length;
    }
    replacements == 3
}

fn valid_format(kind: &str, value: &str) -> bool {
    if !valid_text(value, MAX_FORMAT_BYTES) {
        return false;
    }
    match kind {
        "text" => true,
        "gain" | "hostname" | "ipaddress" => scalar_format(value),
        "timestamp" => timestamp_format(value),
        "uptime" => uptime_format(value),
        _ => false,
    }
}

fn validate_entry(value: &Value, readback: bool) -> Result<(), BackendError> {
    let fields = value.as_object().ok_or(BackendError::Protocol)?;
    let kind = fields
        .get("type")
        .and_then(Value::as_str)
        .ok_or(BackendError::Protocol)?;
    let expected = if readback && kind == "gain" {
        6
    } else if readback {
        5
    } else {
        4
    };
    if fields.len() != expected
        || !fields
            .get("name")
            .and_then(Value::as_str)
            .is_some_and(valid_name)
        || !fields
            .get("format")
            .and_then(Value::as_str)
            .is_some_and(|format| valid_format(kind, format))
        || !fields
            .get("position")
            .and_then(Value::as_str)
            .is_some_and(valid_position)
    {
        return Err(BackendError::Protocol);
    }
    if readback {
        let available = fields
            .get("available")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
        if kind == "gain" {
            if available
                || fields.get("unavailable_reason").and_then(Value::as_str)
                    != Some("gain producer missing")
            {
                return Err(BackendError::Protocol);
            }
        } else if !available || fields.contains_key("unavailable_reason") {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn validate_entries(value: &Value, readback: bool) -> Result<(), BackendError> {
    let entries = value.as_array().ok_or(BackendError::Protocol)?;
    if entries.len() > MAX_ENTRIES {
        return Err(BackendError::Protocol);
    }
    let mut names = std::collections::BTreeSet::new();
    for entry in entries {
        validate_entry(entry, readback)?;
        if !names.insert(entry.get_path("name").unwrap().as_str().unwrap()) {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn valid_id(value: &str, allow_empty: bool) -> bool {
    (allow_empty && value.is_empty())
        || (value.len() == 16 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
}

fn normalized(reply: &RaptorReply) -> Result<Value, BackendError> {
    require_ok(reply)?;
    reply.require_only_fields(&["status", "persistence", "confirmed", "saved", "published"])?;
    if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-snapshot") {
        return Err(BackendError::Unavailable);
    }
    let confirmed = reply
        .value
        .get_path("confirmed")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Protocol)?;
    let saved = reply
        .value
        .get_path("saved")
        .and_then(Value::as_object)
        .filter(|value| value.len() == 4)
        .ok_or(BackendError::Protocol)?;
    let saved_available = saved
        .get("available")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Protocol)?;
    let saved_id = saved
        .get("id")
        .and_then(Value::as_str)
        .ok_or(BackendError::Protocol)?;
    let saved_shape_valid = if saved_available {
        valid_id(saved_id, false) && saved.get("enabled").and_then(Value::as_bool).is_some()
    } else {
        saved_id.is_empty() && saved.get("enabled") == Some(&Value::Null)
    };
    if !saved_shape_valid {
        return Err(BackendError::Protocol);
    }
    validate_entries(saved.get("entries").ok_or(BackendError::Protocol)?, true)?;
    if !saved_available && !saved.get("entries").unwrap().as_array().unwrap().is_empty() {
        return Err(BackendError::Protocol);
    }
    let published = reply
        .value
        .get_path("published")
        .and_then(Value::as_object)
        .filter(|value| value.len() == 5)
        .ok_or(BackendError::Protocol)?;
    let published_id = published
        .get("id")
        .and_then(Value::as_str)
        .ok_or(BackendError::Protocol)?;
    if !valid_id(published_id, true) {
        return Err(BackendError::Protocol);
    }
    value_u64(published.get("generation").ok_or(BackendError::Protocol)?)?;
    let status = match published.get("status") {
        Some(Value::Number(number)) => number.parse::<i32>().map_err(|_| BackendError::Protocol)?,
        _ => return Err(BackendError::Protocol),
    };
    let fresh = published
        .get("fresh")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Protocol)?;
    let matches_saved = published
        .get("matches_saved")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Protocol)?;
    let expected_confirmed =
        saved_available && fresh && matches_saved && status == 0 && saved_id == published_id;
    if confirmed != expected_confirmed || (!saved_available && matches_saved) {
        return Err(BackendError::Protocol);
    }
    let mut result = BTreeMap::new();
    result.insert("source".into(), Value::String("raptor".into()));
    result.insert("persistent".into(), Value::Bool(true));
    result.insert("supported".into(), Value::Bool(true));
    result.insert("confirmed".into(), Value::Bool(confirmed));
    result.insert("saved".into(), Value::Object(saved.clone()));
    result.insert("published".into(), Value::Object(published.clone()));
    Ok(Value::Object(result))
}

fn validate_update(body: &[u8]) -> Result<Value, BackendError> {
    let value = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
    let fields = value
        .as_object()
        .filter(|fields| fields.len() == 2)
        .ok_or(BackendError::Protocol)?;
    fields
        .get("enabled")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Protocol)?;
    validate_entries(fields.get("entries").ok_or(BackendError::Protocol)?, false)?;
    Ok(value)
}

impl RaptorBackend {
    pub(super) fn osd_metadata_config(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rod,
            br#"{"cmd":"get-metadata-settings"}"#,
            deadline,
        )?;
        let value = normalized(&reply).map_err(|error| match error {
            BackendError::Protocol => BackendError::Upstream(502),
            other => other,
        })?;
        Ok(BackendResponse::json(value.to_json().into_bytes()))
    }

    pub(super) fn update_osd_metadata_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let values = validate_update(body)?;
        let _mutation = self.lock_mutation()?;
        let command = format!(
            r#"{{"cmd":"set-metadata-settings","values":{}}}"#,
            values.to_json()
        );
        let apply = || -> Result<Value, BackendError> {
            let reply = self.command(RaptorDaemon::Rod, command.as_bytes(), deadline)?;
            let value = normalized(&reply)?;
            let confirmed = value.get_path("confirmed").and_then(Value::as_bool) == Some(true);
            let saved = value.get_path("saved.available").and_then(Value::as_bool) == Some(true);
            let fresh = value.get_path("published.fresh").and_then(Value::as_bool) == Some(true);
            let matched = value
                .get_path("published.matches_saved")
                .and_then(Value::as_bool)
                == Some(true);
            let status = value.get_path("published.status") == Some(&Value::Number("0".into()));
            if !confirmed || !saved || !fresh || !matched || !status {
                return Err(BackendError::Upstream(502));
            }
            Ok(value)
        };
        let value = apply().map_err(|_| BackendError::PartialApply(
            "Named OSD metadata may have been saved, but publication was not confirmed. Reload and explicitly retry saving.",
        ))?;
        Ok(BackendResponse::json(value.to_json().into_bytes()))
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::{fs, io::Write, os::unix::net::UnixListener, thread};

    const OK: &str = r#"{"status":"ok","persistence":"checked-snapshot","confirmed":true,"saved":{"available":true,"id":"0123456789abcdef","enabled":true,"entries":[{"name":"room","type":"text","format":"Keittiö","position":"1,2","available":true},{"name":"gain","type":"gain","format":"%s","position":"-1,3","available":false,"unavailable_reason":"gain producer missing"}]},"published":{"id":"0123456789abcdef","generation":2,"status":0,"fresh":true,"matches_saved":true}}"#;
    const UNCONFIRMED: &str = r#"{"status":"ok","persistence":"checked-snapshot","confirmed":false,"saved":{"available":true,"id":"0123456789abcdef","enabled":true,"entries":[{"name":"room","type":"text","format":"Keittiö","position":"1,2","available":true},{"name":"gain","type":"gain","format":"%s","position":"-1,3","available":false,"unavailable_reason":"gain producer missing"}]},"published":{"id":"fedcba9876543210","generation":3,"status":-5,"fresh":false,"matches_saved":false}}"#;

    #[test]
    fn metadata_get_preserves_saved_published_and_gain_availability() {
        let root = task_temp("metadata-get");
        let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(
                read_request(&mut socket),
                br#"{"cmd":"get-metadata-settings"}"#
            );
            socket.write_all(&framed(OK.as_bytes())).unwrap();
        });
        let response = backend(&root, "127.0.0.1:9".parse().unwrap())
            .osd_metadata_config(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(
            value.get_path("source").and_then(Value::as_str),
            Some("raptor")
        );
        assert_eq!(
            value.get_path("published.fresh").and_then(Value::as_bool),
            Some(true)
        );
        assert_eq!(
            value
                .get_path("saved.entries")
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            2
        );
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn metadata_post_retries_same_value_and_reports_unconfirmed_as_partial() {
        let root = task_temp("metadata-post");
        let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
        let worker = thread::spawn(move || {
            for response in [OK.as_bytes(), UNCONFIRMED.as_bytes()] {
                let (mut socket, _) = listener.accept().unwrap();
                let request = crate::json::parse(&read_request(&mut socket)).unwrap();
                assert_eq!(
                    request.get_path("cmd").and_then(Value::as_str),
                    Some("set-metadata-settings")
                );
                assert_eq!(
                    request.get_path("values.enabled").and_then(Value::as_bool),
                    Some(true)
                );
                socket.write_all(&framed(response)).unwrap();
            }
        });
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        let body = r#"{"enabled":true,"entries":[{"name":"room","type":"text","format":"Keittiö","position":"1,2"},{"name":"gain","type":"gain","format":"%s","position":"-1,3"}]}"#;
        assert!(
            backend
                .update_osd_metadata_config(
                    body.as_bytes(),
                    Instant::now() + Duration::from_secs(1)
                )
                .is_ok()
        );
        assert!(matches!(
            backend.update_osd_metadata_config(
                body.as_bytes(),
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::PartialApply(_))
        ));
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn metadata_rejects_malformed_input_and_readback() {
        let root = task_temp("metadata-invalid");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            br#"{"enabled":true,"entries":[{"name":"same","type":"text","format":"x","position":"1,2"},{"name":"same","type":"text","format":"y","position":"1,3"}]}"#.as_slice(),
            br#"{"enabled":true,"entries":[{"name":"bad space","type":"text","format":"x","position":"1,2"}]}"#,
            br#"{"enabled":true,"entries":[{"name":"x","type":"gain","format":"%n","position":"1,2"}]}"#,
            br#"{"enabled":true,"entries":[{"name":"x","type":"text","format":"x","position":"8193,2"}]}"#,
            br#"{"enabled":true,"entries":[{"name":"x","type":"text","format":"x","position":"-9223372036854775808,2"}]}"#,
        ] {
            assert!(matches!(
                backend.update_osd_metadata_config(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            read_request(&mut socket);
            socket
                .write_all(&framed(
                    br#"{"status":"ok","persistence":"checked-snapshot"}"#,
                ))
                .unwrap();
        });
        assert!(matches!(
            backend.osd_metadata_config(Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Upstream(502))
        ));
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
