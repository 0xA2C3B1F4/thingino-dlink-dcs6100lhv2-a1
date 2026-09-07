use super::*;

pub(super) fn framed_prudynt_json(body: &[u8]) -> Vec<u8> {
    let header = format!("PRUDYNT/1 JSON {}\n", body.len());
    let mut frame = Vec::with_capacity(header.len() + body.len());
    frame.extend_from_slice(header.as_bytes());
    frame.extend_from_slice(body);
    frame
}

pub(super) fn prudynt_json_request(
    path: &Path,
    body: &[u8],
    deadline: Instant,
) -> Result<Vec<u8>, BackendError> {
    let mut stream = connect_socket(path, deadline)?;
    let frame = framed_prudynt_json(body);
    write_deadline(&mut stream, &frame, deadline)?;
    stream
        .shutdown(std::net::Shutdown::Write)
        .map_err(|_| BackendError::Connection)?;
    let response = read_to_end_deadline(&mut stream, deadline, FILE_LIMIT as usize)?;
    let response_value = json::parse(&response).map_err(|_| BackendError::Protocol)?;
    if response_value.get_path("error").is_some() {
        return Err(BackendError::Unavailable);
    }
    Ok(response)
}

impl PrudyntBackend {
    pub(super) fn prudynt_metrics(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let mut stream = connect_socket(&self.paths.prudynt_socket, deadline)?;
        write_deadline(&mut stream, b"METRICS\n", deadline)?;
        stream
            .shutdown(std::net::Shutdown::Write)
            .map_err(|_| BackendError::Connection)?;
        let response = read_to_end_deadline(&mut stream, deadline, FILE_LIMIT as usize)?;
        validate_prudynt_metrics(&response)?;
        Ok(BackendResponse::prometheus(response))
    }

    pub(super) fn snapshot(
        &self,
        stream_id: u8,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let command = format!("SNAPSHOT ch={stream_id}\n");
        let mut stream = connect_socket(&self.paths.prudynt_socket, deadline)?;
        write_deadline(&mut stream, command.as_bytes(), deadline)?;
        stream
            .shutdown(std::net::Shutdown::Write)
            .map_err(|_| BackendError::Connection)?;
        let header = read_line(&mut stream, deadline, 64)?;
        let length = header
            .strip_prefix("OK ")
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|length| *length > 0 && *length <= MAX_SNAPSHOT_BYTES)
            .ok_or(BackendError::Protocol)?;
        let mut body = vec![0_u8; length];
        read_exact_deadline(&mut stream, &mut body, deadline)?;
        if body.len() < 4 || body[..2] != [0xff, 0xd8] || body[body.len() - 2..] != [0xff, 0xd9] {
            return Err(BackendError::Protocol);
        }
        Ok(BackendResponse::jpeg(body))
    }

    pub(super) fn prudynt_json(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let value = json::parse(body).map_err(|_| BackendError::Protocol)?;
        if value.as_object().is_none() {
            return Err(BackendError::Protocol);
        }
        if let Some(fields) = value.as_object() {
            for (domain, value) in fields {
                if matches!(
                    domain.as_str(),
                    "audio"
                        | "image"
                        | "stream0"
                        | "stream1"
                        | "osd"
                        | "motion"
                        | "privacy"
                        | "rtsp"
                ) {
                    super::config::validate_prudynt_domain(domain, value)?;
                }
            }
        }
        let response = prudynt_json_request(&self.paths.prudynt_socket, body, deadline)?;
        Ok(BackendResponse::json(response))
    }

    pub(super) fn update_prudynt_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let mut update = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = update.as_object().ok_or(BackendError::Protocol)?;
        if fields.is_empty()
            || fields.iter().any(|(name, value)| {
                !matches!(
                    name.as_str(),
                    "audio"
                        | "image"
                        | "stream0"
                        | "stream1"
                        | "osd"
                        | "motion"
                        | "privacy"
                        | "rtsp"
                ) || value.as_object().is_none()
            })
        {
            return Err(BackendError::Protocol);
        }
        super::config::validate_prudynt_update(&update)?;
        let privacy_enabled = normalize_privacy_update(&mut update)?;
        super::config::validate_prudynt_update(&update)?;
        let requested_motion_enabled = update.get_path("motion.enabled").and_then(Value::as_bool);
        if requested_motion_enabled == Some(true) && self.storage_format_busy() {
            return Err(BackendError::Unavailable);
        }
        remove_unchanged_secret_fields(&mut update);
        let original = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)?;
        let current = json::parse(&original).map_err(|_| BackendError::Protocol)?;
        let mut effective = current.clone();
        effective
            .merge(&update)
            .map_err(|_| BackendError::Protocol)?;
        super::config::validate_prudynt_effective_streams(&effective)?;
        let config_changed = retain_changed_fields(&mut update, Some(&current));
        let motion_reconcile = requested_motion_enabled
            .filter(|enabled| self.motion.snapshot().monitoring != *enabled);
        if !config_changed && privacy_enabled.is_none() && motion_reconcile.is_none() {
            return Ok(action_ok());
        }
        let mut live_update = update.clone();
        if let Some(enabled) = privacy_enabled {
            live_update
                .set_path("privacy.enabled", Value::Bool(enabled))
                .map_err(|_| BackendError::Protocol)?;
        }
        if let Some(enabled) = motion_reconcile {
            live_update
                .set_path("motion.enabled", Value::Bool(enabled))
                .map_err(|_| BackendError::Protocol)?;
        }
        let rollback_steps = self.apply_live_transaction(&live_update, &current, deadline)?;
        if config_changed {
            let mut serialized = effective.to_json().into_bytes();
            serialized.push(b'\n');
            if let Err(error) = write_atomic_replace(&self.paths.prudynt_config, &serialized) {
                if !self.rollback_live_transaction(&rollback_steps, deadline) {
                    eprintln!(
                        "thingino-controld: Prudynt live state rollback failed after config write"
                    );
                    return Err(BackendError::Unavailable);
                }
                return Err(error);
            }
        }
        if config_changed {
            self.motion.refresh_config();
        }
        Ok(action_ok())
    }

    fn apply_live_transaction(
        &self,
        update: &Value,
        current: &Value,
        deadline: Instant,
    ) -> Result<Vec<Value>, BackendError> {
        let steps = prudynt_live_steps(update, current)?;
        let mut rollback_steps = Vec::with_capacity(steps.len());
        for (forward, rollback) in steps {
            // A failed Prudynt command is not guaranteed to be side-effect
            // free. In particular, privacy updates change the in-memory
            // configuration before the hardware transaction can fail. Admit
            // the inverse before sending so both the current and all earlier
            // steps are rolled back in reverse order on any error.
            rollback_steps.push(rollback);
            if let Err(error) = self.prudynt_json(forward.to_json().as_bytes(), deadline) {
                if !self.rollback_live_transaction(&rollback_steps, deadline) {
                    eprintln!("thingino-controld: Prudynt partial live update rollback failed");
                    return Err(BackendError::Unavailable);
                }
                return Err(error);
            }
        }
        Ok(rollback_steps)
    }

    fn rollback_live_transaction(&self, steps: &[Value], deadline: Instant) -> bool {
        let mut ok = true;
        for rollback in steps.iter().rev() {
            if self
                .prudynt_json(rollback.to_json().as_bytes(), deadline)
                .is_err()
            {
                ok = false;
            }
        }
        ok
    }

    pub(super) fn merge_config_file(
        &self,
        path: &Path,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let mut current =
            json::parse(&read_bounded(path, FILE_LIMIT)?).map_err(|_| BackendError::Protocol)?;
        let mut update = json::parse(body).map_err(|_| BackendError::Protocol)?;
        if path == self.paths.prudynt_config.as_path() {
            super::config::validate_prudynt_update(&update)?;
        }
        remove_unchanged_secret_fields(&mut update);
        current.merge(&update).map_err(|_| BackendError::Protocol)?;
        if path == self.paths.prudynt_config.as_path() {
            super::config::validate_prudynt_effective_streams(&current)?;
        }
        let mut serialized = current.to_json().into_bytes();
        serialized.push(b'\n');
        write_in_place(path, &serialized)?;
        Ok(BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec()))
    }

    pub(super) fn merge_config_domain(
        &self,
        path: &Path,
        domain: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let mut document =
            json::parse(&read_bounded(path, FILE_LIMIT)?).map_err(|_| BackendError::Protocol)?;
        let mut update = json::parse(body).map_err(|_| BackendError::Protocol)?;
        if update.as_object().is_none() {
            return Err(BackendError::Protocol);
        }
        if path == self.paths.prudynt_config.as_path()
            && matches!(
                domain,
                "audio" | "image" | "stream0" | "stream1" | "osd" | "motion" | "privacy" | "rtsp"
            )
        {
            super::config::validate_prudynt_domain(domain, &update)?;
        }
        remove_unchanged_secret_fields(&mut update);
        let mut current = document
            .get_path(domain)
            .cloned()
            .unwrap_or_else(|| Value::Object(BTreeMap::new()));
        current.merge(&update).map_err(|_| BackendError::Protocol)?;
        document
            .set_path(domain, current)
            .map_err(|_| BackendError::Protocol)?;
        let mut serialized = document.to_json().into_bytes();
        serialized.push(b'\n');
        write_in_place(path, &serialized)?;
        Ok(BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec()))
    }
}

fn validate_prudynt_metrics(response: &[u8]) -> Result<(), BackendError> {
    let text = std::str::from_utf8(response).map_err(|_| BackendError::Protocol)?;
    if text.is_empty()
        || !text.ends_with('\n')
        || response
            .iter()
            .any(|byte| (*byte < b' ' && !matches!(*byte, b'\n' | b'\t')) || *byte == 0x7f)
    {
        return Err(BackendError::Protocol);
    }
    let mut required = [false; 3];
    for line in text.lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split_ascii_whitespace();
        let sample = fields.next().ok_or(BackendError::Protocol)?;
        let value = fields.next().ok_or(BackendError::Protocol)?;
        if fields.next().is_some() || value.parse::<f64>().is_err() {
            return Err(BackendError::Protocol);
        }
        let name = sample.split_once('{').map_or(sample, |(name, _)| name);
        if !sample
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_{}=\",.-".contains(&byte))
            || !name.starts_with("prudynt_")
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        {
            return Err(BackendError::Protocol);
        }
        required[0] |= name == "prudynt_rtsp_clients";
        required[1] |= name == "prudynt_rtsp_queue_bytes";
        required[2] |= name == "prudynt_mjpeg_rejections_total";
    }
    if required.into_iter().all(|seen| seen) {
        Ok(())
    } else {
        Err(BackendError::Protocol)
    }
}

fn prudynt_live_update(update: &Value) -> Option<Value> {
    let mut live_update = update.clone();
    let Value::Object(domains) = &mut live_update else {
        return None;
    };
    let Some(Value::Object(motion)) = domains.get("motion") else {
        return Some(live_update);
    };

    // Prudynt's native JSON endpoint accepts the detector fields below using
    // their canonical names. Lifecycle fields use incompatible `_s`/`_ms`
    // names there, while destination and recorder fields are not live fields.
    // Keep those changes in the canonical file: Control refreshes its state
    // machine immediately and Prudynt reloads the completed IN_CLOSE_WRITE
    // document without a second writer racing the update.
    const LIVE_MOTION_FIELDS: &[&str] = &[
        "enabled",
        "frame_height",
        "frame_width",
        "monitor_stream",
        "motor_settle_ms",
        "roi_0_x",
        "roi_0_y",
        "roi_1_x",
        "roi_1_y",
        "roi_count",
        "sensitivity",
        "skip_frame_count",
    ];
    let live_motion = motion
        .iter()
        .filter(|(name, _)| LIVE_MOTION_FIELDS.contains(&name.as_str()))
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect::<BTreeMap<_, _>>();
    if live_motion.is_empty() {
        domains.remove("motion");
    } else {
        domains.insert("motion".to_owned(), Value::Object(live_motion));
    }
    (!domains.is_empty()).then_some(live_update)
}

fn prudynt_live_steps(
    update: &Value,
    current: &Value,
) -> Result<Vec<(Value, Value)>, BackendError> {
    let Some(Value::Object(mut pending)) = prudynt_live_update(update) else {
        return Ok(Vec::new());
    };
    let mut steps = Vec::with_capacity(pending.len());

    // The top-level privacy command owns the live OSD cover transaction.  The
    // nested osd.privacy bit is the canonical persisted startup state, not a
    // second live operation.  Coupling both operations made the persistent
    // Control transaction fail on the T31 target even though the live privacy
    // command and the canonical OSD update each succeed independently.
    // Apply privacy first, persist osd.privacy only after every live step has
    // succeeded, and use the inverse privacy command for rollback.
    if let Some(privacy) = pending.remove("privacy") {
        let forward = Value::Object(BTreeMap::from([("privacy".to_owned(), privacy)]));
        steps.push((forward.clone(), previous_live_value(current, &forward)?));

        let remove_osd = if let Some(Value::Object(osd)) = pending.get_mut("osd") {
            osd.remove("privacy");
            osd.is_empty()
        } else {
            false
        };
        if remove_osd {
            pending.remove("osd");
        }
    }

    for (domain, value) in pending {
        let forward = Value::Object(BTreeMap::from([(domain, value)]));
        steps.push((forward.clone(), previous_live_value(current, &forward)?));
    }
    Ok(steps)
}

fn previous_live_value(current: &Value, forward: &Value) -> Result<Value, BackendError> {
    let mut source = current.clone();
    if forward.get_path("privacy.enabled").is_some() {
        let enabled = current
            .get_path("osd.privacy.enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
        source
            .set_path("privacy.enabled", Value::Bool(enabled))
            .map_err(|_| BackendError::Protocol)?;
    }
    previous_shape(&source, forward).ok_or(BackendError::Protocol)
}

fn previous_shape(current: &Value, forward: &Value) -> Option<Value> {
    match forward {
        Value::Object(fields) => {
            let current = current.as_object()?;
            let mut previous = BTreeMap::new();
            for (name, value) in fields {
                previous.insert(name.clone(), previous_shape(current.get(name)?, value)?);
            }
            Some(Value::Object(previous))
        }
        _ => Some(current.clone()),
    }
}

fn normalize_privacy_update(update: &mut Value) -> Result<Option<bool>, BackendError> {
    let privacy = match update {
        Value::Object(fields) => fields.remove("privacy"),
        _ => return Err(BackendError::Protocol),
    };
    let Some(privacy) = privacy else {
        return Ok(None);
    };
    let fields = privacy.as_object().ok_or(BackendError::Protocol)?;
    let mut enabled = None;
    for name in ["enabled", "stream0_enabled", "stream1_enabled"] {
        let Some(value) = fields.get(name) else {
            continue;
        };
        let value = value.as_bool().ok_or(BackendError::Protocol)?;
        if enabled.is_some_and(|current| current != value) {
            return Err(BackendError::Protocol);
        }
        enabled = Some(value);
    }
    let enabled = enabled.ok_or(BackendError::Protocol)?;
    if let Some(existing) = update.get_path("osd.privacy.enabled")
        && existing.as_bool() != Some(enabled)
    {
        return Err(BackendError::Protocol);
    }
    update
        .set_path("osd.privacy.enabled", Value::Bool(enabled))
        .map_err(|_| BackendError::Protocol)?;
    Ok(Some(enabled))
}

#[cfg(test)]
#[path = "prudynt_tests.rs"]
mod tests;
