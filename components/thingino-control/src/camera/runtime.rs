use super::*;

impl PrudyntBackend {
    pub(super) fn health(&self) -> Result<BackendResponse, BackendError> {
        let uptime = first_number(&self.paths.uptime).unwrap_or(0.0);
        let loads = read_numbers(&self.paths.loadavg, 3);
        let memory = read_memory(&self.paths.meminfo);
        let hostname = read_bounded(&self.paths.hostname, 255)
            .ok()
            .and_then(|bytes| String::from_utf8(bytes).ok())
            .map(|value| value.trim().to_owned())
            .filter(|value| safe_hostname(value))
            .unwrap_or_else(|| "thingino-camera".to_owned());
        let ip_address = local_ipv4(&self.paths.fib_trie).unwrap_or_default();
        let online = !ip_address.is_empty();
        let prudynt_available =
            process_matches(&self.paths.prudynt_pid, &self.paths.prudynt_executable)
                && self
                    .paths
                    .prudynt_socket
                    .symlink_metadata()
                    .is_ok_and(|metadata| metadata.file_type().is_socket());
        let running =
            prudynt_available && read_exact_line(&self.paths.media_ready, "1080p-started");
        let controllable = self.paths.streaming_init.is_file();
        let healthy = running && online;
        let available = memory.free + memory.buffers + memory.cached;
        let used = memory.total.saturating_sub(available);
        let body = object([
            (
                "control_api",
                object([
                    ("name", Value::String("Thingino Control".to_owned())),
                    ("version", number(1)),
                ]),
            ),
            (
                "status",
                Value::String(if healthy { "ok" } else { "degraded" }.to_owned()),
            ),
            ("healthy", Value::Bool(healthy)),
            (
                "backend",
                object([
                    ("name", Value::String("prudynt".to_owned())),
                    ("available", Value::Bool(prudynt_available)),
                ]),
            ),
            (
                "checks",
                object([
                    (
                        "system",
                        object([
                            ("uptime_seconds", number(uptime as u64)),
                            ("hostname", Value::String(hostname)),
                            ("streamer_running", Value::Bool(running)),
                            (
                                "load_average_1m",
                                decimal(loads.first().copied().unwrap_or(0.0)),
                            ),
                            (
                                "load_average_5m",
                                decimal(loads.get(1).copied().unwrap_or(0.0)),
                            ),
                            (
                                "load_average_15m",
                                decimal(loads.get(2).copied().unwrap_or(0.0)),
                            ),
                            ("memory_total_kib", number(memory.total)),
                            ("memory_free_kib", number(memory.free)),
                            ("memory_available_kib", number(available)),
                            ("memory_used_kib", number(used)),
                        ]),
                    ),
                    (
                        "network",
                        object([
                            ("ip", Value::String(ip_address)),
                            ("online", Value::Bool(online)),
                        ]),
                    ),
                    (
                        "streaming",
                        object([
                            ("name", Value::String("streaming".to_owned())),
                            ("running", Value::Bool(running)),
                            ("healthy", Value::Bool(running)),
                            ("controllable", Value::Bool(controllable)),
                        ]),
                    ),
                ]),
            ),
        ]);
        let mut body = body.to_json().into_bytes();
        body.push(b'\n');
        Ok(BackendResponse::json(body))
    }
    pub(super) fn runtime_media(&self) -> Result<BackendResponse, BackendError> {
        let config_bytes = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)?;
        let config = json::parse(&config_bytes).map_err(|_| BackendError::Protocol)?;
        let available = process_matches(&self.paths.prudynt_pid, &self.paths.prudynt_executable)
            && self
                .paths
                .prudynt_socket
                .symlink_metadata()
                .map(|metadata| metadata.file_type().is_socket())
                .unwrap_or(false);
        let stream0 = stream_effectively_enabled(&config, "stream0");
        let stream1 = stream_effectively_enabled(&config, "stream1");
        let stream = |stream_id: u8, enabled: bool| {
            object([
                ("available", Value::Bool(available)),
                ("enabled", Value::Bool(enabled)),
                (
                    "snapshot_url",
                    if available && enabled {
                        Value::String(format!("/api/v1/actions/snapshot?stream_id={stream_id}"))
                    } else {
                        Value::Null
                    },
                ),
            ])
        };
        let body = object([(
            "streams",
            object([("ch0", stream(0, stream0)), ("ch1", stream(1, stream1))]),
        )]);
        let mut body = body.to_json().into_bytes();
        body.push(b'\n');
        Ok(BackendResponse::json(body))
    }
    pub(super) fn heartbeat(&self) -> Result<BackendResponse, BackendError> {
        let thingino = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let timelapse = read_json_or_empty(&self.paths.timelapse_config)?;
        let mode = read_text_value(&self.paths.daynight_mode, 32)
            .filter(|value| matches!(value.as_str(), "day" | "night"))
            .unwrap_or_else(|| "unknown".to_owned());
        let brightness =
            read_number_value(&self.paths.daynight_brightness).unwrap_or_else(|| "null".to_owned());
        let sensor_data = read_bounded(&self.paths.daynight_sensors, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok());
        let total_gain = sensor_data
            .as_ref()
            .and_then(|value| value.get_path("total_gain").cloned())
            .unwrap_or(Value::Null);
        let brightness_value = brightness
            .parse::<f64>()
            .ok()
            .map(decimal)
            .unwrap_or(Value::Null);
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_secs())
            .unwrap_or(0);
        let uptime = first_number(&self.paths.uptime).unwrap_or(0.0) as u64;
        let ircut_state = read_text_value(&self.paths.ircut_state, 8)
            .and_then(|value| match value.as_str() {
                "0" => Some(number(0)),
                "1" => Some(number(1)),
                _ => None,
            })
            .unwrap_or(Value::Null);
        let audio_state = *self
            .audio_state
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        let motion = self.motion.snapshot();
        let value = object([
            ("time_now", number(now)),
            ("uptime", number(uptime)),
            ("daynight_brightness", brightness_value),
            ("total_gain", total_gain),
            ("daynight_mode", Value::String(mode.clone())),
            (
                "rec_ch0",
                Value::Bool(self.paths.recorder_ch0_active.is_file()),
            ),
            (
                "rec_ch1",
                Value::Bool(self.paths.recorder_ch1_active.is_file()),
            ),
            ("motion_enabled", Value::Bool(motion.monitoring)),
            ("motion_active", Value::Bool(motion.active)),
            ("motion_ingress_ready", Value::Bool(motion.ingress_ready)),
            (
                "privacy_enabled",
                Value::Bool(self.paths.privacy_active.is_file()),
            ),
            (
                "color_mode",
                match mode.as_str() {
                    "day" => number(0),
                    "night" => number(1),
                    _ => Value::Null,
                },
            ),
            ("mic_enabled", Value::Bool(audio_state.microphone)),
            ("spk_enabled", Value::Bool(audio_state.speaker)),
            (
                "daynight_enabled",
                value_or(&thingino, "daynight.enabled", Value::Bool(false)),
            ),
            (
                "timelapse_enabled",
                value_or(&timelapse, "timelapse.enabled", Value::Bool(false)),
            ),
            ("ircut_state", ircut_state),
            ("ir850_state", self.light_state(&thingino, "ir850")),
            ("ir940_state", self.light_state(&thingino, "ir940")),
            ("white_state", self.light_state(&thingino, "white")),
            ("wg_status", number(0)),
        ]);
        if let Ok(mut history) = self.daynight_history.lock() {
            history.push(value.clone());
            if history.len() > 300 {
                history.remove(0);
            }
        }
        json_response(value)
    }
    pub(super) fn daynight_history(&self) -> Result<BackendResponse, BackendError> {
        let history = self
            .daynight_history
            .lock()
            .map_err(|_| BackendError::Unavailable)?
            .clone();
        json_response(Value::Array(history))
    }
    pub(super) fn daynight_sensors(&self) -> Result<BackendResponse, BackendError> {
        let thingino = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let current = read_bounded(&self.paths.daynight_sensors, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok())
            .unwrap_or(Value::Null);
        let night_threshold = thingino
            .get_path("daynight.night_threshold_pct")
            .or_else(|| thingino.get_path("daynight.night_threshold"))
            .cloned()
            .unwrap_or_else(|| number(20));
        let day_threshold = thingino
            .get_path("daynight.day_threshold_pct")
            .or_else(|| thingino.get_path("daynight.day_threshold"))
            .cloned()
            .unwrap_or_else(|| number(30));
        json_response(object([
            ("night_threshold_pct", night_threshold),
            ("day_threshold_pct", day_threshold),
            ("current", current),
        ]))
    }
    pub(super) fn prudynt_domain(&self, domain: &str) -> Result<BackendResponse, BackendError> {
        if !matches!(
            domain,
            "audio" | "image" | "stream0" | "stream1" | "osd" | "motion" | "privacy" | "rtsp"
        ) {
            return Err(BackendError::Protocol);
        }
        let document = json::parse(&read_bounded(&self.paths.prudynt_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let mut value = if domain == "privacy" {
            let enabled = document
                .get_path("osd.privacy.enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            object([
                ("enabled", Value::Bool(enabled)),
                ("stream0_enabled", Value::Bool(enabled)),
                ("stream1_enabled", Value::Bool(enabled)),
            ])
        } else {
            document
                .get_path(domain)
                .cloned()
                .unwrap_or_else(|| Value::Object(BTreeMap::new()))
        };
        if value.as_object().is_none() {
            return Err(BackendError::Protocol);
        }
        if domain == "motion" {
            // script_path is a stored service implementation detail, not a
            // client-writable setting. Do not expose it for GET -> POST echo.
            if let Value::Object(fields) = &mut value {
                fields.remove("script_path");
            }
        }
        if domain == "osd" {
            for (path, default) in [
                ("burnin.enabled", Value::Bool(false)),
                ("burnin.format", Value::String("%F %T".to_owned())),
                ("burnin.scale", number(0)),
                ("burnin.fill_color", Value::String("#ffffffff".to_owned())),
                (
                    "burnin.outline_color",
                    Value::String("#000000ff".to_owned()),
                ),
                (
                    "burnin.background_color",
                    Value::String("#00000080".to_owned()),
                ),
                ("sei.enabled", Value::Bool(false)),
                ("sei.entries", Value::Object(BTreeMap::new())),
            ] {
                if value.get_path(path).is_none() {
                    value
                        .set_path(path, default)
                        .map_err(|_| BackendError::Protocol)?;
                }
            }
        }
        super::config::validate_prudynt_domain(domain, &value)?;
        mask_secret_fields(&mut value);
        json_response(value)
    }
    pub(super) fn runtime_system(&self) -> Result<BackendResponse, BackendError> {
        let memory = read_memory(&self.paths.meminfo);
        let prudynt = read_json_or_empty(&self.paths.prudynt_config)?;
        let ip = local_ipv4(&self.paths.fib_trie).unwrap_or_default();
        let prudynt_running =
            process_matches(&self.paths.prudynt_pid, &self.paths.prudynt_executable);
        let data = object([
            (
                "memory",
                object([
                    ("total", number(memory.total)),
                    ("active", number(memory.active)),
                    ("buffers", number(memory.buffers)),
                    ("cached", number(memory.cached)),
                    ("free", number(memory.free)),
                ]),
            ),
            ("overlay", filesystem_value(&self.paths.overlay)),
            ("extras", filesystem_value(&self.paths.extras)),
            (
                "network",
                object([
                    ("ip", Value::String(ip.clone())),
                    ("online", Value::Bool(!ip.is_empty())),
                    (
                        "interfaces",
                        object([
                            (
                                "eth0",
                                json::parse(self.interface_runtime("eth0", &ip).as_bytes())
                                    .unwrap_or(Value::Null),
                            ),
                            (
                                "wlan0",
                                json::parse(self.interface_runtime("wlan0", &ip).as_bytes())
                                    .unwrap_or(Value::Null),
                            ),
                            (
                                "usb0",
                                json::parse(self.interface_runtime("usb0", &ip).as_bytes())
                                    .unwrap_or(Value::Null),
                            ),
                        ]),
                    ),
                ]),
            ),
            (
                "media",
                object([
                    ("prudynt_running", Value::Bool(prudynt_running)),
                    (
                        "media_ready",
                        Value::Bool(read_exact_line(&self.paths.media_ready, "1080p-started")),
                    ),
                    (
                        "stream0_enabled",
                        Value::Bool(stream_effectively_enabled(&prudynt, "stream0")),
                    ),
                    (
                        "stream1_enabled",
                        Value::Bool(stream_effectively_enabled(&prudynt, "stream1")),
                    ),
                ]),
            ),
            (
                "timestamp",
                number(
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .map(|value| value.as_secs())
                        .unwrap_or(0),
                ),
            ),
        ]);
        json_response(object([
            ("code", number(200)),
            ("result", Value::String("success".to_owned())),
            ("data", data),
        ]))
    }
    pub(super) fn sensor_iq(&self) -> Result<BackendResponse, BackendError> {
        let sensor =
            read_text_value(&self.paths.sensor_name, 128).unwrap_or_else(|| "os02g10".to_owned());
        let soc =
            os_release_value(&self.paths.os_release, "SOC").unwrap_or_else(|| "t31n".to_owned());
        let family = soc
            .chars()
            .take_while(|value| !value.is_ascii_digit() && *value != 'x')
            .collect::<String>()
            .to_ascii_lowercase();
        json_response(object([
            ("sensor_model", Value::String(sensor.clone())),
            ("soc_model", Value::String(soc.clone())),
            ("soc_family", Value::String(family)),
            (
                "file_path",
                Value::String(format!("/usr/share/sensor/{sensor}-{soc}.bin")),
            ),
            (
                "md5",
                Value::String("not computed in request path".to_owned()),
            ),
        ]))
    }
}

fn stream_effectively_enabled(config: &Value, name: &str) -> bool {
    if config
        .get_path(&format!("{name}.enabled"))
        .and_then(Value::as_bool)
        != Some(true)
    {
        return false;
    }
    let produces_frames = matches!(
        config.get_path(&format!("{name}.fps")),
        Some(Value::Number(raw)) if raw.parse::<f64>().is_ok_and(|fps| fps > 0.0)
    );
    let buffers_disabled = matches!(
        config.get_path(&format!("{name}.buffers")),
        Some(Value::Number(raw)) if raw.parse::<i64>() == Ok(-1)
    );
    produces_frames && !buffers_disabled
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn effective_stream_state_requires_frames_and_enabled_buffers() {
        let active = json::parse(br#"{"stream0":{"enabled":true,"fps":15,"buffers":3}}"#).unwrap();
        assert!(stream_effectively_enabled(&active, "stream0"));

        for disabled in [
            br#"{"stream0":{"enabled":false,"fps":15,"buffers":3}}"#.as_slice(),
            br#"{"stream0":{"enabled":true,"fps":0,"buffers":3}}"#,
            br#"{"stream0":{"enabled":true,"fps":15,"buffers":-1}}"#,
            br#"{"stream0":{"enabled":true,"buffers":3}}"#,
        ] {
            let config = json::parse(disabled).unwrap();
            assert!(!stream_effectively_enabled(&config, "stream0"));
        }
    }
}
