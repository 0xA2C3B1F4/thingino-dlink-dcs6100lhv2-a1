use super::*;

impl HostBackend {
    #[cfg_attr(not(feature = "raptor-backend"), allow(dead_code))]
    pub(crate) fn paths(&self) -> &CameraPaths {
        &self.paths
    }

    #[cfg_attr(not(feature = "raptor-backend"), allow(dead_code))]
    pub(crate) fn ha_config_request(
        &self,
        method: &str,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let _guard = self
            .config_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        match method {
            "GET" if body.is_empty() => self.validated_config_domain("ha"),
            "POST" if !body.is_empty() => self.update_validated_config_domain("ha", body),
            _ => Err(BackendError::Protocol),
        }
    }

    /// Dispatch only host operations shared by the media backends.
    #[cfg_attr(not(feature = "raptor-backend"), allow(dead_code))]
    pub(crate) fn api_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Option<Result<BackendResponse, BackendError>> {
        let serialized = matches!(method, "POST" | "PUT") && target.starts_with("/api/v1/config/");
        let _guard = if serialized {
            match self.config_lock.lock() {
                Ok(guard) => Some(guard),
                Err(_) => return Some(Err(BackendError::Unavailable)),
            }
        } else {
            None
        };
        match (method, target) {
            ("GET", "/api/v1/config/admin") if body.is_empty() => {
                Some(self.validated_config_domain("admin"))
            }
            ("POST", "/api/v1/config/admin") if !body.is_empty() => {
                Some(self.update_validated_config_domain("admin", body))
            }
            ("GET", "/api/v1/config/webui") if body.is_empty() => {
                Some(self.validated_config_domain("webui"))
            }
            ("POST", "/api/v1/config/webui") if !body.is_empty() => {
                Some(self.update_validated_config_domain("webui", body))
            }
            ("GET", "/api/v1/config/rsyslog") if body.is_empty() => {
                Some(self.validated_config_domain("rsyslog"))
            }
            ("POST", "/api/v1/config/rsyslog") if !body.is_empty() => {
                Some(self.update_validated_config_domain("rsyslog", body))
            }
            ("GET", "/api/v1/config/crontab") if body.is_empty() => Some(self.crontab()),
            ("POST", "/api/v1/config/crontab") if !body.is_empty() => {
                Some(self.update_crontab(body))
            }
            ("GET", "/api/v1/config/gpio") if body.is_empty() => Some(self.gpio_config()),
            ("POST", "/api/v1/config/gpio") if !body.is_empty() => {
                Some(self.update_gpio_config(body))
            }
            ("GET", "/api/v1/config/time") if body.is_empty() => Some(self.time_config()),
            ("POST", "/api/v1/actions/time/sync") if body.is_empty() => {
                Some(self.sync_time(deadline))
            }
            ("GET", "/api/v1/config/network") if body.is_empty() => Some(self.network_config()),
            ("POST", "/api/v1/config/network") if !body.is_empty() => {
                Some(self.update_network_config(body))
            }
            ("GET", "/api/v1/network/probe") if body.is_empty() => {
                Some(self.network_probe_metadata())
            }
            ("POST", "/api/v1/network/probe") if !body.is_empty() => {
                Some(self.network_probe(body, deadline))
            }
            ("GET", "/api/v1/network/wifi-scan") if body.is_empty() => {
                Some(self.wifi_scan(deadline))
            }
            ("GET", "/api/v1/storage/overlay") if body.is_empty() => Some(self.overlay()),
            ("GET", target)
                if target.starts_with("/api/v1/diagnostics/info?") && body.is_empty() =>
            {
                Some(self.diagnostics_info(target))
            }
            ("POST", "/api/v1/diagnostics") if !body.is_empty() => Some(self.diagnostics(body)),
            ("GET", "/api/v1/runtime/sensor" | "/api/v1/sensor/iq") if body.is_empty() => {
                Some(self.sensor_iq())
            }
            ("GET", "/api/v1/actions/reset") if body.is_empty() => Some(self.reset_actions()),
            ("POST", "/api/v1/actions/factory-reset") if !body.is_empty() => {
                Some(self.factory_reset(body))
            }
            ("POST", "/api/v1/actions/reboot") if body.is_empty() => Some(self.reboot()),
            ("GET", "/api/v1/config/time") => Some(Err(BackendError::Protocol)),
            (_, "/api/v1/config/time") => None,
            (_, target) if host_target(target) => Some(Err(BackendError::Protocol)),
            _ => None,
        }
    }
}

#[cfg_attr(not(feature = "raptor-backend"), allow(dead_code))]
fn host_target(target: &str) -> bool {
    matches!(
        target,
        "/api/v1/config/admin"
            | "/api/v1/config/webui"
            | "/api/v1/config/rsyslog"
            | "/api/v1/config/crontab"
            | "/api/v1/config/gpio"
            | "/api/v1/config/time"
            | "/api/v1/actions/time/sync"
            | "/api/v1/config/network"
            | "/api/v1/network/probe"
            | "/api/v1/network/wifi-scan"
            | "/api/v1/storage/overlay"
            | "/api/v1/diagnostics"
            | "/api/v1/runtime/sensor"
            | "/api/v1/sensor/iq"
            | "/api/v1/actions/reset"
            | "/api/v1/actions/factory-reset"
            | "/api/v1/actions/reboot"
    ) || target.starts_with("/api/v1/diagnostics/info?")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    #[test]
    fn time_sync_is_shared_without_claiming_timezone_mutation() {
        let root = task_temp("time-sync-route");
        let backend = HostBackend::new(CameraPaths {
            ntp_config: root.join("missing-ntp.conf"),
            ..CameraPaths::default()
        });
        assert!(matches!(
            backend.api_request("POST", "/api/v1/actions/time/sync", b"", Instant::now()),
            Some(Err(BackendError::Unavailable))
        ));
        for (method, body) in [("GET", b"".as_slice()), ("POST", b"{}".as_slice())] {
            assert!(matches!(
                backend.api_request(method, "/api/v1/actions/time/sync", body, Instant::now()),
                Some(Err(BackendError::Protocol))
            ));
        }
        assert!(
            backend
                .api_request("POST", "/api/v1/config/time", b"{}", Instant::now())
                .is_none()
        );
        fs::remove_dir_all(root).unwrap();
    }

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = PathBuf::from(root).join(format!(
            "thingino-control-host-{}-{}-{name}",
            std::process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn dispatch_does_not_claim_media_owned_routes() {
        let backend = HostBackend::new(CameraPaths::default());
        for target in [
            "/api/v1/prudynt",
            "/api/v1/imaging",
            "/api/v1/recorder",
            "/api/v1/files?dir=/mnt",
            "/api/v1/files/text?path=/etc/hostname",
            "/api/v1/storage/sd",
            "/api/v1/actions/prudynt/restart",
        ] {
            assert!(
                backend
                    .api_request("GET", target, b"", Instant::now())
                    .is_none(),
                "host backend claimed {target}"
            );
        }
    }

    #[test]
    fn dispatch_claims_only_the_shared_host_route_set() {
        let backend = HostBackend::new(CameraPaths::default());
        for target in [
            "/api/v1/config/admin",
            "/api/v1/config/webui",
            "/api/v1/config/rsyslog",
            "/api/v1/config/crontab",
            "/api/v1/config/gpio",
            "/api/v1/config/network",
            "/api/v1/network/probe",
            "/api/v1/network/wifi-scan",
            "/api/v1/storage/overlay",
            "/api/v1/diagnostics",
            "/api/v1/diagnostics/info?system",
            "/api/v1/runtime/sensor",
            "/api/v1/sensor/iq",
            "/api/v1/actions/reboot",
        ] {
            assert!(
                backend
                    .api_request("PUT", target, b"{}", Instant::now())
                    .is_some(),
                "host backend did not claim {target}"
            );
        }
        assert!(
            backend
                .api_request("GET", "/api/v1/config/time", b"x", Instant::now())
                .is_some()
        );
        assert!(
            backend
                .api_request("POST", "/api/v1/config/time", b"{}", Instant::now())
                .is_none()
        );
    }

    #[test]
    fn generic_host_diagnostics_reject_removed_media_sections() {
        let paths = CameraPaths::default();
        let backend = HostBackend::new(paths);

        assert!(matches!(
            backend.diagnostics_info("/api/v1/diagnostics/info?prudynt"),
            Err(BackendError::Protocol)
        ));

        let logcat = backend
            .diagnostics_info("/api/v1/diagnostics/info?logcat")
            .unwrap();
        assert!(
            std::str::from_utf8(&logcat.body)
                .unwrap()
                .contains(&base64_encode(b"streamer log: unavailable\n"))
        );
    }
}
