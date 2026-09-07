use super::super::*;

impl PrudyntBackend {
    pub(in crate::camera) fn access_config(&self) -> Result<BackendResponse, BackendError> {
        let prudynt = json::parse(&read_bounded(&self.paths.prudynt_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let password_set = prudynt
            .get_path("rtsp.password")
            .and_then(Value::as_str)
            .is_some_and(|value| !value.is_empty());
        json_response(object([
            (
                "username",
                prudynt
                    .get_path("rtsp.username")
                    .and_then(Value::as_str)
                    .map(|value| Value::String(value.to_owned()))
                    .unwrap_or(Value::Null),
            ),
            ("password", Value::Null),
            ("password_set", Value::Bool(password_set)),
            // ONVIF shares uhttpd's public HTTP ingress in this profile. The
            // persistent ONVIF service's loopback port is deliberately private.
            ("onvif_port", number(80)),
            ("onvif_enabled", Value::Bool(true)),
            ("onvif_ingress", Value::String("same-origin".to_owned())),
            ("rtsp_port", value_or(&prudynt, "rtsp.port", Value::Null)),
            (
                "rtsp_ch0",
                value_or(&prudynt, "stream0.rtsp_endpoint", Value::Null),
            ),
            (
                "rtsp_ch1",
                value_or(&prudynt, "stream1.rtsp_endpoint", Value::Null),
            ),
            (
                "rtsp_mic",
                value_or(&prudynt, "rtsp.audio_only_endpoint", Value::Null),
            ),
        ]))
    }

    pub(crate) fn update_management_credential(
        &self,
        username: &str,
        password: &str,
        _deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        if !safe_access_name(username)
            || password.len() < 10
            || password.len() > 128
            || password.contains('\0')
        {
            return Err(BackendError::Protocol);
        }
        let original = read_bounded(&self.paths.onvif_config, FILE_LIMIT)?;
        let mut onvif = json::parse(&original).map_err(|_| BackendError::Protocol)?;
        onvif
            .set_path("server.username", Value::String(username.to_owned()))
            .map_err(|_| BackendError::Protocol)?;
        onvif
            .set_path("server.password", Value::String(password.to_owned()))
            .map_err(|_| BackendError::Protocol)?;
        let mut output = onvif.to_json().into_bytes();
        output.push(b'\n');
        write_in_place(&self.paths.onvif_config, &output)?;
        Ok(BackendResponse::json(
            b"{\"status\":\"ok\",\"management_password_changed\":true}\n".to_vec(),
        ))
    }

    pub(in crate::camera) fn update_access_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request.as_object().ok_or(BackendError::Protocol)?;
        if fields.is_empty()
            || fields.keys().any(|key| {
                !matches!(
                    key.as_str(),
                    "username"
                        | "password"
                        | "rtsp_port"
                        | "rtsp_ch0"
                        | "rtsp_ch1"
                        | "rtsp_mic"
                        | "onvif_port"
                        | "onvif_enabled"
                        | "rtsp"
                )
            })
        {
            return Err(BackendError::Protocol);
        }
        if fields
            .get("onvif_port")
            .is_some_and(|value| value_u64(value) != Some(80))
            || fields
                .get("onvif_enabled")
                .is_some_and(|value| value.as_bool() != Some(true))
        {
            return Err(BackendError::Protocol);
        }
        let legacy_password = match fields.get("rtsp") {
            Some(value) => {
                let value = value.as_object().ok_or(BackendError::Protocol)?;
                if value.keys().any(|key| key != "password") {
                    return Err(BackendError::Protocol);
                }
                value.get("password").and_then(Value::as_str)
            }
            None => None,
        };
        if fields.contains_key("password") && legacy_password.is_some() {
            return Err(BackendError::Protocol);
        }
        let password = fields
            .get("password")
            .and_then(Value::as_str)
            .or(legacy_password)
            .filter(|value| !value.is_empty());
        if password
            .is_some_and(|value| value.len() < 4 || value.len() > 128 || value.contains('\0'))
        {
            return Err(BackendError::Protocol);
        }
        let prudynt_original = read_bounded(&self.paths.prudynt_config, FILE_LIMIT)?;
        let update_onvif_endpoints =
            fields.contains_key("rtsp_ch0") || fields.contains_key("rtsp_ch1");
        let onvif_original = if update_onvif_endpoints {
            Some(read_bounded(&self.paths.onvif_config, FILE_LIMIT)?)
        } else {
            None
        };
        let mut prudynt = json::parse(&prudynt_original).map_err(|_| BackendError::Protocol)?;
        let mut onvif = onvif_original
            .as_deref()
            .map(json::parse)
            .transpose()
            .map_err(|_| BackendError::Protocol)?;
        let mut rtsp_update = BTreeMap::new();
        let mut stream0_update = BTreeMap::new();
        let mut stream1_update = BTreeMap::new();
        if let Some(username) = fields.get("username").and_then(Value::as_str) {
            if !safe_access_name(username) {
                return Err(BackendError::Protocol);
            }
            prudynt
                .set_path("rtsp.username", Value::String(username.to_owned()))
                .map_err(|_| BackendError::Protocol)?;
            rtsp_update.insert("username".to_owned(), Value::String(username.to_owned()));
        }
        if let Some(password) = password {
            prudynt
                .set_path("rtsp.password", Value::String(password.to_owned()))
                .map_err(|_| BackendError::Protocol)?;
            rtsp_update.insert("password".to_owned(), Value::String(password.to_owned()));
        }
        if let Some(port) = fields.get("rtsp_port") {
            let port = value_u64(port)
                .filter(|value| (1..=65_535).contains(value))
                .ok_or(BackendError::Protocol)?;
            prudynt
                .set_path("rtsp.port", number(port))
                .map_err(|_| BackendError::Protocol)?;
            rtsp_update.insert("port".to_owned(), number(port));
        }
        for (field, path, update) in [
            ("rtsp_ch0", "stream0.rtsp_endpoint", &mut stream0_update),
            ("rtsp_ch1", "stream1.rtsp_endpoint", &mut stream1_update),
        ] {
            if let Some(endpoint) = fields.get(field).and_then(Value::as_str) {
                if !safe_rtsp_endpoint(endpoint) {
                    return Err(BackendError::Protocol);
                }
                prudynt
                    .set_path(path, Value::String(endpoint.to_owned()))
                    .map_err(|_| BackendError::Protocol)?;
                update.insert(
                    "rtsp_endpoint".to_owned(),
                    Value::String(endpoint.to_owned()),
                );
                let profile = if field == "rtsp_ch0" {
                    "profiles.stream0.url"
                } else {
                    "profiles.stream1.url"
                };
                let onvif = onvif.as_mut().ok_or(BackendError::Protocol)?;
                update_url_endpoint(onvif, profile, endpoint)?;
            }
        }
        if let Some(endpoint) = fields.get("rtsp_mic").and_then(Value::as_str) {
            if !safe_rtsp_endpoint(endpoint) {
                return Err(BackendError::Protocol);
            }
            prudynt
                .set_path(
                    "rtsp.audio_only_endpoint",
                    Value::String(endpoint.to_owned()),
                )
                .map_err(|_| BackendError::Protocol)?;
            rtsp_update.insert(
                "audio_only_endpoint".to_owned(),
                Value::String(endpoint.to_owned()),
            );
        }
        let mut live = BTreeMap::new();
        if !rtsp_update.is_empty() {
            live.insert("rtsp".to_owned(), Value::Object(rtsp_update));
        }
        if !stream0_update.is_empty() {
            live.insert("stream0".to_owned(), Value::Object(stream0_update));
        }
        if !stream1_update.is_empty() {
            live.insert("stream1".to_owned(), Value::Object(stream1_update));
        }
        if live.is_empty() {
            return Ok(BackendResponse::json(
                b"{\"status\":\"ok\",\"password_changed\":false}\n".to_vec(),
            ));
        }
        let live = Value::Object(live);
        self.prudynt_json(live.to_json().as_bytes(), deadline)?;
        let mut prudynt_bytes = prudynt.to_json().into_bytes();
        prudynt_bytes.push(b'\n');
        write_in_place(&self.paths.prudynt_config, &prudynt_bytes)?;
        if let Some(onvif) = onvif {
            let mut onvif_bytes = onvif.to_json().into_bytes();
            onvif_bytes.push(b'\n');
            if let Err(error) = write_in_place(&self.paths.onvif_config, &onvif_bytes) {
                let _ = write_in_place(&self.paths.prudynt_config, &prudynt_original);
                if let Some(onvif_original) = onvif_original.as_deref() {
                    let _ = write_in_place(&self.paths.onvif_config, onvif_original);
                }
                return Err(error);
            }
        }
        Ok(BackendResponse::json(
            format!(
                "{{\"status\":\"ok\",\"password_changed\":{}}}\n",
                bool_json(password.is_some())
            )
            .into_bytes(),
        ))
    }
}
