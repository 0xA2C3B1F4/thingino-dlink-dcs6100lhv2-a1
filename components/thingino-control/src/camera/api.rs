use super::*;

pub(super) fn dispatch(
    backend: &PrudyntBackend,
    method: &str,
    target: &str,
    body: &[u8],
    deadline: Instant,
) -> Option<Result<BackendResponse, BackendError>> {
    if let Some(domain) = target.strip_prefix("/api/v1/config/")
        && domain == "daynight"
    {
        return match method {
            "GET" if body.is_empty() => Some(backend.daynight_config()),
            "POST" if !body.is_empty() => Some(backend.update_daynight_config(body)),
            _ => Some(Err(BackendError::Protocol)),
        };
    }
    if let Some(domain) = target.strip_prefix("/api/v1/config/")
        && matches!(domain, "admin" | "webui" | "rsyslog" | "ha" | "mqtt_sub")
    {
        return match method {
            "GET" if body.is_empty() => Some(backend.validated_config_domain(domain)),
            "POST" if !body.is_empty() => {
                let result = backend.update_validated_config_domain(domain, body);
                if domain == "ha" && result.is_ok() {
                    backend.ha.reconfigure();
                }
                Some(result)
            }
            _ => Some(Err(BackendError::Protocol)),
        };
    }
    match (method, target) {
        ("GET", "/api/v1/config/crontab") if body.is_empty() => Some(backend.crontab()),
        ("POST", "/api/v1/config/crontab") if !body.is_empty() => {
            Some(backend.update_crontab(body))
        }
        ("GET", "/api/v1/config/gpio") if body.is_empty() => Some(backend.gpio_config()),
        ("POST", "/api/v1/config/gpio") if !body.is_empty() => {
            Some(backend.update_gpio_config(body))
        }
        ("GET", "/api/v1/config/time") if body.is_empty() => Some(backend.time_config()),
        ("POST", "/api/v1/config/time") if !body.is_empty() => {
            Some(backend.update_time_config(body, deadline))
        }
        ("POST", "/api/v1/actions/time/sync") if body.is_empty() => {
            Some(backend.sync_time(deadline))
        }
        ("GET", "/api/v1/config/network") if body.is_empty() => Some(backend.network_config()),
        ("POST", "/api/v1/config/network") if !body.is_empty() => {
            Some(backend.update_network_config(body))
        }
        ("GET", "/api/v1/network/probe") if body.is_empty() => {
            Some(backend.network_probe_metadata())
        }
        ("POST", "/api/v1/network/probe") if !body.is_empty() => {
            Some(backend.network_probe(body, deadline))
        }
        ("GET", "/api/v1/network/wifi-scan") if body.is_empty() => {
            Some(backend.wifi_scan(deadline))
        }
        ("GET", "/api/v1/config/access") if body.is_empty() => Some(backend.access_config()),
        ("POST", "/api/v1/config/access") if !body.is_empty() => {
            Some(backend.update_access_config(body, deadline))
        }
        ("GET", "/api/v1/services/send/config") if body.is_empty() => Some(backend.send2_config()),
        ("POST", "/api/v1/services/send/config") if !body.is_empty() => {
            Some(backend.update_send2_config(body, deadline))
        }
        ("POST", "/api/v1/prudynt") if !body.is_empty() => {
            Some(backend.update_prudynt_config(body, deadline))
        }
        ("GET", target) if target.starts_with("/api/v1/prudynt/") && body.is_empty() => {
            Some(backend.prudynt_domain(target.trim_start_matches("/api/v1/prudynt/")))
        }
        ("GET", "/api/v1/imaging") if body.is_empty() => Some(backend.imaging()),
        ("POST", "/api/v1/imaging") if !body.is_empty() => Some(backend.update_imaging(body)),
        ("POST", "/api/v1/actions/control") if !body.is_empty() => {
            Some(backend.control(body, deadline))
        }
        ("GET", "/api/v1/recorder") if body.is_empty() => Some(backend.recorder()),
        ("POST", "/api/v1/recorder") if !body.is_empty() => Some(backend.update_recorder(body)),
        ("GET", "/api/v1/runtime/system") if body.is_empty() => Some(backend.runtime_system()),
        ("GET", "/api/v1/runtime/media/metrics") if body.is_empty() => {
            Some(backend.prudynt_metrics(deadline))
        }
        ("GET", "/api/v1/runtime/motion") if body.is_empty() => {
            Some(backend.motion.runtime_response())
        }
        ("GET", "/api/v1/storage/overlay") if body.is_empty() => Some(backend.overlay()),
        ("GET", "/api/v1/storage/sd") if body.is_empty() => Some(backend.sd_state()),
        ("POST", "/api/v1/storage/sd") if !body.is_empty() => Some(backend.queue_sd_format(body)),
        ("GET", target) if target.starts_with("/api/v1/files/text?") && body.is_empty() => {
            Some(backend.text_file(method, target, body))
        }
        ("POST", target) if target.starts_with("/api/v1/files/text?") => {
            Some(backend.text_file(method, target, body))
        }
        ("GET" | "POST", target) if target.starts_with("/api/v1/files?") => {
            Some(backend.files(method, target, body))
        }
        ("GET", target) if target.starts_with("/api/v1/diagnostics/info?") && body.is_empty() => {
            Some(backend.diagnostics_info(target))
        }
        ("POST", "/api/v1/diagnostics") if !body.is_empty() => Some(backend.diagnostics(body)),
        ("GET", "/api/v1/runtime/daynight/history") if body.is_empty() => {
            Some(backend.daynight_history())
        }
        ("GET", "/api/v1/runtime/daynight/sensors") if body.is_empty() => {
            Some(backend.daynight_sensors())
        }
        ("GET", "/api/v1/sensor/iq") if body.is_empty() => Some(backend.sensor_iq()),
        ("GET", "/api/v1/runtime/sensor") if body.is_empty() => Some(backend.sensor_iq()),
        ("GET", "/api/v1/actions/reset") if body.is_empty() => Some(backend.reset_actions()),
        ("POST", "/api/v1/actions/factory-reset") if !body.is_empty() => {
            Some(backend.factory_reset(body))
        }
        ("POST", "/api/v1/actions/reboot") if body.is_empty() => Some(backend.reboot()),
        ("POST", "/api/v1/actions/prudynt/restart") if body.is_empty() => {
            Some(backend.restart_prudynt(deadline))
        }
        ("GET", "/api/v1/runtime/heartbeat") if body.is_empty() => Some(backend.heartbeat()),
        ("GET", "/api/v1/runtime/ha") if body.is_empty() => Some(backend.ha.runtime()),
        ("POST", "/api/v1/actions/ha") if !body.is_empty() => Some(backend.ha.action(body)),
        _ if known_api_target(target) => Some(Err(BackendError::Protocol)),
        _ => None,
    }
}

fn known_api_target(target: &str) -> bool {
    matches!(
        target,
        "/api/v1/config/crontab"
            | "/api/v1/config/gpio"
            | "/api/v1/config/time"
            | "/api/v1/actions/time/sync"
            | "/api/v1/config/network"
            | "/api/v1/network/probe"
            | "/api/v1/network/wifi-scan"
            | "/api/v1/config/access"
            | "/api/v1/services/send/config"
            | "/api/v1/prudynt"
            | "/api/v1/imaging"
            | "/api/v1/actions/control"
            | "/api/v1/recorder"
            | "/api/v1/runtime/system"
            | "/api/v1/runtime/media/metrics"
            | "/api/v1/runtime/motion"
            | "/api/v1/storage/overlay"
            | "/api/v1/storage/sd"
            | "/api/v1/diagnostics"
            | "/api/v1/runtime/daynight/history"
            | "/api/v1/runtime/daynight/sensors"
            | "/api/v1/sensor/iq"
            | "/api/v1/runtime/sensor"
            | "/api/v1/actions/reset"
            | "/api/v1/actions/factory-reset"
            | "/api/v1/actions/reboot"
            | "/api/v1/actions/prudynt/restart"
            | "/api/v1/runtime/heartbeat"
            | "/api/v1/runtime/ha"
            | "/api/v1/actions/ha"
    ) || target
        .strip_prefix("/api/v1/config/")
        .is_some_and(|domain| {
            matches!(
                domain,
                "admin" | "webui" | "rsyslog" | "ha" | "mqtt_sub" | "daynight"
            )
        })
        || target.starts_with("/api/v1/prudynt/")
        || target.starts_with("/api/v1/files/text?")
        || target.starts_with("/api/v1/files?")
        || target.starts_with("/api/v1/diagnostics/info?")
}

#[cfg(test)]
mod tests {
    use super::known_api_target;

    #[test]
    fn route_classifier_covers_current_exact_and_prefix_targets() {
        let exact_targets = [
            "/api/v1/config/daynight",
            "/api/v1/config/admin",
            "/api/v1/config/webui",
            "/api/v1/config/rsyslog",
            "/api/v1/config/ha",
            "/api/v1/config/mqtt_sub",
            "/api/v1/config/crontab",
            "/api/v1/config/gpio",
            "/api/v1/config/time",
            "/api/v1/actions/time/sync",
            "/api/v1/config/network",
            "/api/v1/network/probe",
            "/api/v1/network/wifi-scan",
            "/api/v1/config/access",
            "/api/v1/services/send/config",
            "/api/v1/prudynt",
            "/api/v1/imaging",
            "/api/v1/actions/control",
            "/api/v1/recorder",
            "/api/v1/runtime/system",
            "/api/v1/runtime/media/metrics",
            "/api/v1/runtime/motion",
            "/api/v1/storage/overlay",
            "/api/v1/storage/sd",
            "/api/v1/diagnostics",
            "/api/v1/runtime/daynight/history",
            "/api/v1/runtime/daynight/sensors",
            "/api/v1/sensor/iq",
            "/api/v1/runtime/sensor",
            "/api/v1/actions/reset",
            "/api/v1/actions/factory-reset",
            "/api/v1/actions/reboot",
            "/api/v1/actions/prudynt/restart",
            "/api/v1/runtime/heartbeat",
            "/api/v1/runtime/ha",
            "/api/v1/actions/ha",
        ];
        for target in exact_targets {
            assert!(known_api_target(target), "expected known target: {target}");
        }

        for target in [
            "/api/v1/prudynt/video0",
            "/api/v1/files?dir=/mnt",
            "/api/v1/files/text?path=/etc/hostname",
            "/api/v1/diagnostics/info?command=ps",
        ] {
            assert!(known_api_target(target), "expected known prefix: {target}");
        }

        for target in [
            "/api/v1/config/unknown",
            "/api/v1/files",
            "/api/v1/diagnostics/info",
            "/api/v1/unknown",
        ] {
            assert!(
                !known_api_target(target),
                "expected unknown target: {target}"
            );
        }
    }
}
