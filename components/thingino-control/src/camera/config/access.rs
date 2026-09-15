use super::super::*;

impl HostBackend {
    /// Persist the credential used by the backend-neutral ONVIF service.
    ///
    /// The WebUI shadow update is serialized by the router. This second half
    /// independently verifies the ONVIF file before reporting success so the
    /// router can restore shadow if the two credentials were not updated
    /// together.
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
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
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
        let write = |bytes: &[u8]| {
            write_in_place(&self.paths.onvif_config, bytes)?;
            let parent = self
                .paths
                .onvif_config
                .parent()
                .ok_or(BackendError::Protocol)?;
            File::open(parent)
                .and_then(|directory| directory.sync_all())
                .map_err(|_| BackendError::Unavailable)
        };
        let restore = || {
            let restored = write(&original)
                .and_then(|()| read_bounded(&self.paths.onvif_config, FILE_LIMIT))
                .is_ok_and(|bytes| bytes == original);
            if restored {
                Ok(())
            } else {
                Err(BackendError::PartialApply(
                    "ONVIF credential rollback could not be confirmed. Inspect management credentials before retrying.",
                ))
            }
        };
        if let Err(error) = write(&output) {
            restore()?;
            return Err(error);
        }
        let persisted = read_bounded(&self.paths.onvif_config, FILE_LIMIT)
            .and_then(|bytes| json::parse(&bytes).map_err(|_| BackendError::Protocol));
        let persisted = match persisted {
            Ok(value) => value,
            Err(error) => {
                restore()?;
                return Err(error);
            }
        };
        if persisted
            .get_path("server.username")
            .and_then(Value::as_str)
            != Some(username)
            || persisted
                .get_path("server.password")
                .and_then(Value::as_str)
                != Some(password)
        {
            restore()?;
            return Err(BackendError::Unavailable);
        }
        Ok(BackendResponse::json(
            b"{\"status\":\"ok\",\"management_password_changed\":true}\n".to_vec(),
        ))
    }
}
