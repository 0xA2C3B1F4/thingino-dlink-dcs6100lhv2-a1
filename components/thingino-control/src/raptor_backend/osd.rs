use super::*;

static APPLY_SEQUENCE: AtomicU32 = AtomicU32::new(0);
const FIELDS: [(&str, &str, u64); 6] = [
    ("format", "time_format", 1),
    ("fill_color", "font_color", 2),
    ("outline_color", "stroke_color", 4),
    ("font_size", "font_size", 8),
    ("enabled", "text_enabled", 16),
    ("background_color", "background_color", 32),
];

fn effective_rgba(value: &Value) -> Result<String, BackendError> {
    let color = value.as_str().ok_or(BackendError::Upstream(502))?;
    if color.len() != 10
        || !color.starts_with("0x")
        || !color[2..].bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err(BackendError::Upstream(502));
    }
    Ok(format!(
        "#{}{}",
        color[4..].to_ascii_lowercase(),
        color[2..4].to_ascii_lowercase()
    ))
}

impl RaptorBackend {
    fn osd_observation(&self, deadline: Instant) -> Result<RaptorReply, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rod,
            br#"{"cmd":"get-osd-settings"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-snapshot") {
            return Err(BackendError::Unavailable);
        }
        for key in ["enabled", "available", "confirmed"] {
            if reply.value.get_path(key).and_then(Value::as_bool).is_none() {
                return Err(BackendError::Upstream(502));
            }
        }
        let bits = reply
            .value
            .get_path("fields")
            .ok_or(BackendError::Upstream(502))
            .and_then(value_u64)?;
        if bits > 255
            || reply.value.get_path("id").and_then(Value::as_str).is_none()
            || reply
                .value
                .get_path("time_format")
                .and_then(Value::as_str)
                .is_none_or(|v| v.len() > 63)
        {
            return Err(BackendError::Upstream(502));
        }
        if bits & 8 != 0 {
            let size = reply
                .value
                .get_path("font_size")
                .ok_or(BackendError::Upstream(502))
                .and_then(value_u64)?;
            if !(16..=48).contains(&size) {
                return Err(BackendError::Upstream(502));
            }
        }
        if bits & 16 != 0 {
            let enabled = reply
                .value
                .get_path("text_enabled")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?;
            if reply.value.get_path("available").and_then(Value::as_bool) == Some(true)
                && reply.value.get_path("enabled").and_then(Value::as_bool) != Some(enabled)
            {
                return Err(BackendError::Upstream(502));
            }
        }
        for key in ["font_color", "stroke_color"] {
            effective_rgba(
                reply
                    .value
                    .get_path(key)
                    .ok_or(BackendError::Upstream(502))?,
            )?;
        }
        if bits & 32 != 0 {
            effective_rgba(
                reply
                    .value
                    .get_path("background_color")
                    .ok_or(BackendError::Upstream(502))?,
            )?;
        }
        Ok(reply)
    }

    pub(super) fn osd_config(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        // This GET has no caller-supplied body. Invalid daemon replies are an
        // upstream failure, not a malformed request from the browser.
        self.osd_config_readback(deadline)
            .map_err(|error| match error {
                BackendError::Protocol => BackendError::Upstream(502),
                other => other,
            })
    }

    fn osd_config_readback(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let reply = self.osd_observation(deadline)?;
        let mut fields = std::collections::BTreeMap::new();
        let bits = value_u64(reply.value.get_path("fields").unwrap())?;
        for (name, _, bit) in FIELDS {
            fields.insert(name.into(), Value::Bool(bits & bit != 0));
        }
        fields.insert("fill_alpha".into(), Value::Bool(bits & 64 != 0));
        fields.insert("outline_alpha".into(), Value::Bool(bits & 128 != 0));
        let mut result = std::collections::BTreeMap::new();
        result.insert("source".into(), Value::String("raptor".into()));
        result.insert("persistent".into(), Value::Bool(true));
        for key in ["enabled", "available"] {
            result.insert(key.into(), reply.value.get_path(key).unwrap().clone());
        }
        result.insert(
            "format".into(),
            reply.value.get_path("time_format").unwrap().clone(),
        );
        for (name, key) in [
            ("fill_color", "font_color"),
            ("outline_color", "stroke_color"),
            ("background_color", "background_color"),
        ] {
            result.insert(
                name.into(),
                if key == "background_color" && bits & 32 == 0 {
                    Value::String("#00000000".into())
                } else {
                    Value::String(effective_rgba(reply.value.get_path(key).unwrap())?)
                },
            );
        }
        result.insert("fields".into(), Value::Object(fields));
        result.insert(
            "font_size".into(),
            if bits & 8 != 0 {
                reply.value.get_path("font_size").unwrap().clone()
            } else {
                Value::Null
            },
        );
        let disk = self.command(
            RaptorDaemon::Rod,
            br#"{"cmd":"config-read-section","section":"osd"}"#,
            deadline,
        )?;
        require_ok(&disk)?;
        disk.require_only_fields(&["status", "section", "keys"])?;
        if disk.value.get_path("section").and_then(Value::as_str) != Some("osd")
            || disk
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        let defaults = [
            "%Y-%m-%d %H:%M:%S",
            "0xFFFFFFFF",
            "0xFF000000",
            "24",
            "true",
            "0x00000000",
        ];
        let mut matches_saved =
            bits != 0 && reply.value.get_path("available").and_then(Value::as_bool) == Some(true);
        for ((name, key, bit), default) in FIELDS.iter().zip(defaults) {
            if bits & bit == 0 {
                continue;
            }
            let saved = disk
                .value
                .get_path(&format!("keys.{key}"))
                .cloned()
                .unwrap_or(Value::String(default.into()));
            let saved = if *key == "time_format" {
                if saved.as_str().is_none_or(|value| value.len() > 63) {
                    return Err(BackendError::Upstream(502));
                }
                saved
            } else if *key == "text_enabled" {
                Value::Bool(match saved.as_str() {
                    Some("true") => true,
                    Some("false") => false,
                    _ => return Err(BackendError::Upstream(502)),
                })
            } else if *key == "font_size" {
                let size = saved
                    .as_str()
                    .and_then(|text| text.parse::<u64>().ok())
                    .filter(|size| (16..=48).contains(size))
                    .ok_or(BackendError::Upstream(502))?;
                Value::Number(size.to_string())
            } else {
                Value::String(effective_rgba(&saved)?)
            };
            if bits & bit != 0 && result.get(*name) != Some(&saved) {
                matches_saved = false;
            }
        }
        result.insert("matches_saved".into(), Value::Bool(matches_saved));
        Ok(BackendResponse::json(
            Value::Object(result).to_json().into_bytes(),
        ))
    }

    pub(super) fn update_osd_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let root = request
            .as_object()
            .filter(|r| r.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let fields = root
            .get("osd")
            .and_then(Value::as_object)
            .filter(|r| !r.is_empty() && r.len() <= FIELDS.len())
            .ok_or(BackendError::Protocol)?;
        let mut values = std::collections::BTreeMap::new();
        let mut mask = 0;
        let mut required_mask = 0;
        for (name, value) in fields {
            let (_, key, bit) = FIELDS
                .iter()
                .find(|(field, _, _)| *field == name)
                .ok_or(BackendError::Protocol)?;
            if *key == "text_enabled" {
                let enabled = value.as_bool().ok_or(BackendError::Protocol)?;
                values.insert((*key).to_owned(), Value::Bool(enabled));
                mask |= bit;
                continue;
            }
            if *key == "font_size" {
                let size = value_u64(value)?;
                if !(16..=48).contains(&size) {
                    return Err(BackendError::Protocol);
                }
                values.insert((*key).to_owned(), Value::Number(size.to_string()));
                mask |= bit;
                continue;
            }
            let value = value.as_str().ok_or(BackendError::Protocol)?;
            let native = if *key == "time_format" {
                if value.is_empty()
                    || value.len() > 63
                    || value.trim() != value
                    || !value
                        .bytes()
                        .all(|b| (0x20..=0x7e).contains(&b) && b != b'#' && b != b';')
                {
                    return Err(BackendError::Protocol);
                }
                let mut chars = value.bytes();
                while let Some(byte) = chars.next() {
                    if byte == b'%'
                        && chars
                            .next()
                            .is_none_or(|c| !b"YymdHMSFTZzabeIpRjuwf%".contains(&c))
                    {
                        return Err(BackendError::Protocol);
                    }
                }
                value.to_owned()
            } else {
                if value.len() != 9
                    || !value.starts_with('#')
                    || !value[1..].bytes().all(|b| b.is_ascii_hexdigit())
                {
                    return Err(BackendError::Protocol);
                }
                format!(
                    "0x{}{}",
                    value[7..].to_ascii_uppercase(),
                    value[1..7].to_ascii_uppercase()
                )
            };
            if *key == "font_color" && !value[7..].eq_ignore_ascii_case("ff") {
                required_mask |= 64;
            }
            if *key == "stroke_color" && !value[7..].eq_ignore_ascii_case("ff") {
                required_mask |= 128;
            }
            values.insert((*key).to_owned(), Value::String(native));
            mask |= bit;
        }
        let _mutation = self.lock_mutation()?;
        let before = self.osd_observation(deadline)?;
        if before.value.get_path("available").and_then(Value::as_bool) != Some(true)
            || value_u64(before.value.get_path("fields").unwrap())? & (mask | required_mask)
                != mask | required_mask
        {
            return Err(BackendError::Unavailable);
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| BackendError::Unavailable)?;
        let id = format!(
            "{:x}{:08x}",
            now.as_nanos(),
            APPLY_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        );
        let command = format!(
            r#"{{"cmd":"set-osd-settings","id":"{id}","values":{}}}"#,
            Value::Object(values.clone()).to_json()
        );
        let apply = || -> Result<(), BackendError> {
            require_ok(&self.command(RaptorDaemon::Rod, command.as_bytes(), deadline)?)?;
            let observed = self.osd_observation(deadline)?;
            if observed
                .value
                .get_path("available")
                .and_then(Value::as_bool)
                != Some(true)
                || observed
                    .value
                    .get_path("confirmed")
                    .and_then(Value::as_bool)
                    != Some(true)
                || observed.value.get_path("id").and_then(Value::as_str) != Some(id.as_str())
            {
                return Err(BackendError::Upstream(502));
            }
            for (key, value) in &values {
                if observed.value.get_path(key) != Some(value) {
                    return Err(BackendError::Upstream(502));
                }
            }
            let receipt =
                self.command(RaptorDaemon::Rvd, br#"{"cmd":"osd-apply-state"}"#, deadline)?;
            require_ok(&receipt)?;
            if receipt.value.get_path("accepted").and_then(Value::as_bool) != Some(true)
                || receipt.value.get_path("id").and_then(Value::as_str) != Some(id.as_str())
            {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rod, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let disk = self.command(
                RaptorDaemon::Rod,
                br#"{"cmd":"config-read-section","section":"osd"}"#,
                deadline,
            )?;
            require_ok(&disk)?;
            disk.require_only_fields(&["status", "section", "keys"])?;
            if disk.value.get_path("section").and_then(Value::as_str) != Some("osd") {
                return Err(BackendError::Upstream(502));
            }
            for (key, value) in &values {
                let saved = disk.value.get_path(&format!("keys.{key}"));
                let expected = if key == "font_size" || key == "text_enabled" {
                    Value::String(value.to_json())
                } else {
                    value.clone()
                };
                if saved != Some(&expected) {
                    return Err(BackendError::Upstream(502));
                }
            }
            let final_live = self.osd_observation(deadline)?;
            if final_live
                .value
                .get_path("available")
                .and_then(Value::as_bool)
                != Some(true)
                || final_live
                    .value
                    .get_path("confirmed")
                    .and_then(Value::as_bool)
                    != Some(true)
                || final_live.value.get_path("id").and_then(Value::as_str) != Some(id.as_str())
            {
                return Err(BackendError::Upstream(502));
            }
            for (key, value) in &values {
                if final_live.value.get_path(key) != Some(value) {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("OSD may have changed, but rendering, SDK application, save or readback was incomplete. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::{
        fs,
        io::Write,
        os::unix::net::UnixListener,
        sync::{Arc, Mutex},
        thread,
    };

    fn observation(id: &str, color: &str) -> Vec<u8> {
        format!(r#"{{"status":"ok","persistence":"checked-snapshot","enabled":true,"available":true,"confirmed":true,"id":"{id}","fields":47,"font_size":40,"time_format":"%H:%M:%S","font_color":"{color}","stroke_color":"0xFF000000","background_color":"0x00000000"}}"#).into_bytes()
    }

    fn alpha_observation(id: &str, enabled: bool) -> Vec<u8> {
        format!(r#"{{"status":"ok","persistence":"checked-snapshot","enabled":{enabled},"text_enabled":{enabled},"available":true,"confirmed":true,"id":"{id}","fields":255,"font_size":40,"time_format":"%H:%M:%S","font_color":"0x80FF0000","stroke_color":"0x0000FF00","background_color":"0xFF0000FF"}}"#).into_bytes()
    }

    fn visibility_observation(id: &str, color: &str, enabled: bool) -> Vec<u8> {
        String::from_utf8(observation(id, color))
            .unwrap()
            .replace("\"fields\":47", "\"fields\":63")
            .replace(
                "\"enabled\":true",
                &format!("\"enabled\":{enabled},\"text_enabled\":{enabled}"),
            )
            .into_bytes()
    }

    #[test]
    fn osd_readback_rejects_broken_daemon_replies_as_upstream_failures() {
        for response in [None, Some(b"{".as_slice()), Some(b"{}".as_slice())] {
            let root = task_temp("osd-broken-readback");
            let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
            let worker = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"get-osd-settings"}"#);
                if let Some(body) = response {
                    socket.write_all(&framed(body)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .osd_config(Instant::now() + Duration::from_secs(1));
            assert!(matches!(result, Err(BackendError::Upstream(502))));
            worker.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn osd_save_requires_both_daemon_receipts_and_independent_disk_readback() {
        for fault in [
            "none",
            "setter",
            "live",
            "live-size",
            "live-enabled",
            "id",
            "receipt",
            "receipt-id",
            "save",
            "disk",
            "disk-size",
            "disk-enabled",
            "disk-background",
            "final-live",
        ] {
            let root = task_temp("osd-save");
            let rod = UnixListener::bind(root.join("rod.sock")).unwrap();
            let rvd = UnixListener::bind(root.join("rvd.sock")).unwrap();
            let id = Arc::new(Mutex::new(String::new()));
            let rod_id = id.clone();
            let worker = thread::spawn(move || {
                let count = match fault {
                    "setter" => 2,
                    "live" | "live-size" | "live-enabled" | "id" | "receipt" | "receipt-id" => 3,
                    "save" => 4,
                    "none" | "final-live" => 6,
                    _ => 5,
                };
                for step in 0..count {
                    let (mut socket, _) = rod.accept().unwrap();
                    socket
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let bytes = read_request(&mut socket);
                    let request = crate::json::parse(&bytes).unwrap();
                    let expected = [
                        "get-osd-settings",
                        "set-osd-settings",
                        "get-osd-settings",
                        "config-save",
                        "config-read-section",
                        "get-osd-settings",
                    ][step];
                    assert_eq!(
                        request.get_path("cmd").and_then(Value::as_str),
                        Some(expected)
                    );
                    let current_id = rod_id.lock().unwrap().clone();
                    let response = match step {
                        0 => alpha_observation("", true),
                        1 => {
                            *rod_id.lock().unwrap() = request
                                .get_path("id")
                                .and_then(Value::as_str)
                                .unwrap()
                                .into();
                            assert_eq!(
                                request
                                    .get_path("values.font_color")
                                    .and_then(Value::as_str),
                                Some("0x80FF0000")
                            );
                            assert_eq!(
                                request.get_path("values.stroke_color").and_then(Value::as_str),
                                Some("0x0000FF00")
                            );
                            assert_eq!(
                                request.get_path("values.background_color").and_then(Value::as_str),
                                Some("0xFF0000FF")
                            );
                            assert_eq!(request.get_path("values.font_size"), Some(&Value::Number("40".into())));
                            assert_eq!(request.get_path("values.text_enabled"), Some(&Value::Bool(false)));
                            if fault == "setter" {
                                br#"{"status":"error"}"#.to_vec()
                            } else {
                                br#"{"status":"ok"}"#.to_vec()
                            }
                        }
                        2 => {
                            let mut live = String::from_utf8(alpha_observation(
                                if fault == "id" { "stale" } else { &current_id },
                                fault == "live-enabled",
                            )).unwrap();
                            if fault == "live" { live = live.replace("0x80FF0000", "0xFFFFFFFF"); }
                            if fault == "live-size" { live = live.replace("\"font_size\":40", "\"font_size\":41"); }
                            live.into_bytes()
                        },
                        3 => {
                            if fault == "save" {
                                br#"{"status":"error"}"#.to_vec()
                            } else {
                                br#"{"status":"ok"}"#.to_vec()
                            }
                        }
                        4 => format!(
                            r#"{{"status":"ok","section":"osd","keys":{{"text_enabled":"{}","font_size":"{}","font_color":"{}","stroke_color":"0x0000FF00","background_color":"{}"}}}}"#,
                            if fault == "disk-enabled" { "true" } else { "false" },
                            if fault == "disk-size" { "41" } else { "40" },
                            if fault == "disk" {
                                "0xFFFFFFFF"
                            } else {
                                "0x80FF0000"
                            },
                            if fault == "disk-background" { "0x00000000" } else { "0xFF0000FF" }
                        )
                        .into_bytes(),
                        _ => {
                            let final_id = if fault == "final-live" { "stale" } else { &current_id };
                            alpha_observation(final_id, false)
                        },
                    };
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let receipt_worker = thread::spawn(move || {
                if ["setter", "live", "live-size", "live-enabled", "id"].contains(&fault) {
                    return;
                }
                let (mut socket, _) = rvd.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                assert_eq!(read_request(&mut socket), br#"{"cmd":"osd-apply-state"}"#);
                let response = format!(
                    r#"{{"status":"ok","accepted":{},"id":"{}"}}"#,
                    fault != "receipt",
                    if fault == "receipt-id" {
                        "stale".into()
                    } else {
                        id.lock().unwrap().clone()
                    }
                );
                socket.write_all(&framed(response.as_bytes())).unwrap();
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_osd_config(
                br##"{"osd":{"fill_color":"#ff000080","outline_color":"#00ff0000","background_color":"#0000ffff","font_size":40,"enabled":false}}"##,
                Instant::now() + Duration::from_secs(5),
            );
            if fault == "none" {
                assert!(result.is_ok(), "{result:?}");
            } else {
                assert!(
                    matches!(result, Err(BackendError::PartialApply(_))),
                    "{fault}: {result:?}"
                );
            }
            worker.join().unwrap();
            receipt_worker.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn osd_font_size_readback_compares_numeric_runtime_with_saved_text() {
        for saved in ["40", "41", "invalid"] {
            let root = task_temp("osd-size-readback");
            let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
            let worker = thread::spawn(move || {
                for step in 0..2 {
                    let (mut socket, _) = listener.accept().unwrap();
                    read_request(&mut socket);
                    let response = if step == 0 {
                        observation("", "0xFFFFFFFF")
                    } else {
                        format!(r#"{{"status":"ok","section":"osd","keys":{{"time_format":"%H:%M:%S","font_size":"{saved}"}}}}"#).into_bytes()
                    };
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .osd_config(Instant::now() + Duration::from_secs(1));
            if saved == "invalid" {
                assert!(matches!(result, Err(BackendError::Upstream(502))));
            } else {
                let value = crate::json::parse(&result.unwrap().body).unwrap();
                assert_eq!(
                    value.get_path("font_size"),
                    Some(&Value::Number("40".into()))
                );
                assert_eq!(value.get_path("fields.font_size"), Some(&Value::Bool(true)));
                assert_eq!(
                    value.get_path("matches_saved"),
                    Some(&Value::Bool(saved == "40"))
                );
            }
            worker.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn osd_alpha_readback_maps_native_argb_and_requires_exact_disk_values() {
        for saved_background in ["0xFF0000FF", "0x800000FF"] {
            let root = task_temp("osd-alpha-readback");
            let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
            let worker = thread::spawn(move || {
                for (request, response) in [
                    (br#"{"cmd":"get-osd-settings"}"#.as_slice(), alpha_observation("", true)),
                    (br#"{"cmd":"config-read-section","section":"osd"}"#.as_slice(), format!(
                        r#"{{"status":"ok","section":"osd","keys":{{"time_format":"%H:%M:%S","font_color":"0x80FF0000","stroke_color":"0x0000FF00","background_color":"{saved_background}","font_size":"40","text_enabled":"true"}}}}"#
                    ).into_bytes()),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let response = backend(&root, "127.0.0.1:9".parse().unwrap())
                .osd_config(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let value = crate::json::parse(&response.body).unwrap();
            assert_eq!(
                value.get_path("fill_color"),
                Some(&Value::String("#ff000080".into()))
            );
            assert_eq!(
                value.get_path("outline_color"),
                Some(&Value::String("#00ff0000".into()))
            );
            assert_eq!(
                value.get_path("background_color"),
                Some(&Value::String("#0000ffff".into()))
            );
            assert_eq!(
                value.get_path("matches_saved"),
                Some(&Value::Bool(saved_background == "0xFF0000FF"))
            );
            worker.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn hidden_osd_remains_available_and_compares_saved_visibility() {
        for saved in ["false", "true", "invalid"] {
            let root = task_temp("osd-hidden-readback");
            let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
            let worker = thread::spawn(move || {
                for (request, response) in [
                    (br#"{"cmd":"get-osd-settings"}"#.to_vec(), visibility_observation("", "0xFFFFFFFF", false)),
                    (br#"{"cmd":"config-read-section","section":"osd"}"#.to_vec(),
                     format!(r#"{{"status":"ok","section":"osd","keys":{{"time_format":"%H:%M:%S","font_size":"40","text_enabled":"{saved}"}}}}"#).into_bytes()),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    socket.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(&response)).unwrap();
                }
            });
            let result = backend(&root, "127.0.0.1:9".parse().unwrap())
                .osd_config(Instant::now() + Duration::from_secs(1));
            if saved == "invalid" {
                assert!(matches!(result, Err(BackendError::Upstream(502))));
            } else {
                let value = crate::json::parse(&result.unwrap().body).unwrap();
                assert_eq!(value.get_path("enabled"), Some(&Value::Bool(false)));
                assert_eq!(value.get_path("available"), Some(&Value::Bool(true)));
                assert_eq!(value.get_path("fields.enabled"), Some(&Value::Bool(true)));
                assert_eq!(
                    value.get_path("matches_saved"),
                    Some(&Value::Bool(saved == "false"))
                );
            }
            worker.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn osd_rejects_unsupported_fields_and_malformed_colors_before_ipc() {
        let root = task_temp("osd-input");
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            r##"{"osd":{"fill_color":"#ffffff8"}}"##,
            r##"{"osd":{"outline_color":"#gggggg80"}}"##,
            r##"{"osd":{"background_color":"#000000000"}}"##,
            r#"{"osd":{"enabled":"false"}}"#,
            r#"{"osd":{"enabled":0}}"#,
            r#"{"osd":{"enabled":null}}"#,
            r#"{"osd":{"format":"%n"}}"#,
            r#"{"osd":{"scale":2}}"#,
            r#"{"osd":{"font_size":15}}"#,
            r#"{"osd":{"font_size":49}}"#,
            r#"{"osd":{"font_size":24.5}}"#,
            r#"{"osd":{"font_size":"24"}}"#,
        ] {
            assert!(matches!(
                backend.update_osd_config(body.as_bytes(), Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn osd_rejects_translucent_text_without_explicit_alpha_capability() {
        let root = task_temp("osd-alpha-capability");
        let listener = UnixListener::bind(root.join("rod.sock")).unwrap();
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            assert_eq!(read_request(&mut socket), br#"{"cmd":"get-osd-settings"}"#);
            socket
                .write_all(&framed(&visibility_observation("", "0xFFFFFFFF", true)))
                .unwrap();
        });
        let result = backend(&root, "127.0.0.1:9".parse().unwrap()).update_osd_config(
            br##"{"osd":{"fill_color":"#ffffff80"}}"##,
            Instant::now() + Duration::from_secs(1),
        );
        assert!(matches!(result, Err(BackendError::Unavailable)));
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
