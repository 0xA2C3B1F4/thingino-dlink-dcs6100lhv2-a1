use super::*;

impl HostBackend {
    #[cfg_attr(not(feature = "raptor-backend"), allow(dead_code))]
    pub(crate) fn host_clock(&self) -> Result<(u64, u64), BackendError> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| BackendError::Unavailable)?;
        let uptime = first_number(&self.paths.uptime)
            .filter(|value| value.is_finite() && *value >= 0.0)
            .ok_or(BackendError::Unavailable)?;
        Ok((now.as_secs(), uptime as u64))
    }
}

impl HostBackend {
    pub(crate) fn runtime_system_with_media(
        &self,
        media: Value,
    ) -> Result<BackendResponse, BackendError> {
        let memory = read_memory(&self.paths.meminfo);
        let ip = local_ipv4(&self.paths.fib_trie).unwrap_or_default();
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
            ("media", media),
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
