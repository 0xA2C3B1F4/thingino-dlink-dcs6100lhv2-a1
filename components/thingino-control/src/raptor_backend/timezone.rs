use super::*;

const SERVICES: [(&str, RaptorDaemon); 10] = [
    ("rvd", RaptorDaemon::Rvd),
    ("rsd", RaptorDaemon::Rsd),
    ("rhd", RaptorDaemon::Rhd),
    ("rad", RaptorDaemon::Rad),
    ("ric", RaptorDaemon::Ric),
    ("rod", RaptorDaemon::Rod),
    ("rmr", RaptorDaemon::Rmr),
    ("rmr0", RaptorDaemon::Rmr0),
    ("rmr1", RaptorDaemon::Rmr1),
    ("rwd", RaptorDaemon::Rwd),
];

fn observation(reply: &RaptorReply) -> Result<(), BackendError> {
    require_ok(reply)?;
    if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-timezone") {
        return Err(BackendError::Unavailable);
    }
    let available = reply
        .value
        .get_path("available")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    for (field, limit) in [("rule", 256), ("january", 319), ("july", 319)] {
        let value = reply.value.get_path(field);
        if available {
            if value.and_then(Value::as_str).is_none_or(|s| {
                s.is_empty() || s.len() > limit || !s.bytes().all(|b| (0x20..=0x7e).contains(&b))
            }) {
                return Err(BackendError::Upstream(502));
            }
        } else if value != Some(&Value::Null) {
            return Err(BackendError::Upstream(502));
        }
    }
    Ok(())
}

fn participants(reply: &RaptorReply) -> Result<Vec<(&'static str, RaptorDaemon)>, BackendError> {
    observation(reply)?;
    let required = reply
        .value
        .get_path("required")
        .and_then(Value::as_object)
        .ok_or(BackendError::Upstream(502))?;
    if required.len() != SERVICES.len()
        || required.get("rvd").and_then(Value::as_bool) != Some(true)
    {
        return Err(BackendError::Upstream(502));
    }
    let mut result = Vec::new();
    for (name, daemon) in SERVICES {
        if required
            .get(name)
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?
        {
            result.push((name, daemon));
        }
    }
    Ok(result)
}

impl RaptorBackend {
    fn timezone_status(
        &self,
        daemon: RaptorDaemon,
        deadline: Instant,
    ) -> Result<RaptorReply, BackendError> {
        let reply = self.command(daemon, br#"{"cmd":"timezone-status"}"#, deadline)?;
        observation(&reply)?;
        Ok(reply)
    }

    fn timezone_matches(
        &self,
        selected: &[(&str, RaptorDaemon)],
        rule: &str,
        deadline: Instant,
    ) -> Result<bool, BackendError> {
        let first = self.timezone_status(RaptorDaemon::Rvd, deadline)?;
        if participants(&first)? != selected
            || first.value.get_path("available").and_then(Value::as_bool) != Some(true)
            || first.value.get_path("rule").and_then(Value::as_str) != Some(rule)
        {
            return Ok(false);
        }
        for (_, daemon) in selected.iter().skip(1) {
            let state = self.timezone_status(*daemon, deadline)?;
            if state.value.get_path("available").and_then(Value::as_bool) != Some(true)
                || ["rule", "january", "july"]
                    .iter()
                    .any(|key| state.value.get_path(key) != first.value.get_path(key))
            {
                return Ok(false);
            }
        }
        let last = self.timezone_status(RaptorDaemon::Rvd, deadline)?;
        Ok(participants(&last)? == selected
            && ["rule", "january", "july", "available"]
                .iter()
                .all(|key| last.value.get_path(key) == first.value.get_path(key)))
    }

    pub(super) fn time_config(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let host = self
            .host
            .api_request("GET", "/api/v1/config/time", b"", deadline)
            .ok_or(BackendError::Unavailable)??;
        let Value::Object(mut result) =
            crate::json::parse(&host.body).map_err(|_| BackendError::Upstream(502))?
        else {
            return Err(BackendError::Upstream(502));
        };
        let rule = result
            .get("tz_data")
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        let check = || -> Result<bool, BackendError> {
            let selected = participants(&self.timezone_status(RaptorDaemon::Rvd, deadline)?)?;
            for (_, daemon) in &selected {
                self.timezone_status(*daemon, deadline)?;
            }
            self.timezone_matches(&selected, rule, deadline)
        };
        let name = result
            .get("timezone")
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        let state = check().map(|applied| applied && self.host.timezone_files_match(name, rule));
        result.insert("source".into(), Value::String("raptor".into()));
        result.insert(
            "timezone_reload_supported".into(),
            Value::Bool(state.is_ok()),
        );
        result.insert(
            "timezone_applied".into(),
            Value::Bool(state.unwrap_or(false)),
        );
        Ok(BackendResponse::json(
            Value::Object(result).to_json().into_bytes(),
        ))
    }

    pub(super) fn update_time_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let update = self.host.prepare_time_config(body)?;
        let _mutation = self.lock_mutation()?;
        let selected = participants(&self.timezone_status(RaptorDaemon::Rvd, deadline)?)?;
        for (_, daemon) in &selected {
            self.timezone_status(*daemon, deadline)?;
        }
        let apply = || -> Result<(), BackendError> {
            self.host.persist_time_config(&update)?;
            let command = format!(
                r#"{{"cmd":"timezone-reload","expected_rule":{}}}"#,
                Value::String(update.rule.clone()).to_json()
            );
            for (_, daemon) in &selected {
                let reply = self.command(*daemon, command.as_bytes(), deadline)?;
                observation(&reply)?;
                if reply.value.get_path("rule").and_then(Value::as_str)
                    != Some(update.rule.as_str())
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            if !self.timezone_matches(&selected, &update.rule, deadline)? {
                return Err(BackendError::Upstream(502));
            }
            self.host.verify_time_config(&update)?;
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Time settings may be saved, but Raptor timezone application was not confirmed. Reload and explicitly retry saving."))?;
        Ok(BackendResponse::json(br#"{"status":"ok","persistent":true,"applied":true,"message":"Time settings saved and Raptor timezone applied"}"#.to_vec()))
    }
}

#[cfg(test)]
pub(super) mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::sync::{Arc, atomic::AtomicBool};
    use std::thread;

    pub(in crate::raptor_backend) fn reply(rule: &str, enabled: &[&str]) -> String {
        let required = Value::Object(
            SERVICES
                .iter()
                .map(|(name, _)| ((*name).into(), Value::Bool(enabled.contains(name))))
                .collect(),
        );
        let (january, july) = if rule == "UTC0" {
            (
                "2026-01-15T12:00:00+0000 UTC",
                "2026-07-15T12:00:00+0000 UTC",
            )
        } else {
            (
                "2026-01-15T14:00:00+0200 EET",
                "2026-07-15T15:00:00+0300 EEST",
            )
        };
        format!(
            r#"{{"status":"ok","persistence":"checked-timezone","available":true,"rule":{},"january":"{january}","july":"{july}","required":{}}}"#,
            Value::String(rule.into()).to_json(),
            required.to_json()
        )
    }

    #[test]
    fn timezone_persistence_requires_every_participant_and_same_timezone_retries_apply() {
        for owner in ["rmr0", "rmr1", "rwd"] {
            for fault in [
                "none",
                "missing",
                "preflight",
                "reload",
                "readback",
                "sample",
                "membership",
                "file",
            ] {
                let root = super::super::tests::task_temp("timezone");
                fs::write(root.join("timezone"), "Etc/GMT\n").unwrap();
                fs::write(root.join("TZ"), "UTC0\n").unwrap();
                fs::write(root.join("ntp"), "server pool.ntp.org iburst\n").unwrap();
                fs::write(
                    root.join("thingino.json"),
                    r#"{"dhcp":{"ignore_timezone":false}}"#,
                )
                .unwrap();
                fs::write(root.join("tz.json"), r#"[{"n":"Etc/GMT","v":"UTC0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#).unwrap();
                let forbidden = UnixListener::bind(root.join("no-prudynt.sock")).unwrap();
                forbidden.set_nonblocking(true).unwrap();
                let mut backend =
                    super::super::tests::backend(&root, "127.0.0.1:9".parse().unwrap());
                backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
                    timezone: root.join("timezone"),
                    tz: root.join("TZ"),
                    timezone_catalog: root.join("tz.json"),
                    ntp_config: root.join("ntp"),
                    thingino_config: root.join("thingino.json"),
                    prudynt_socket: root.join("no-prudynt.sock"),
                    ..crate::camera::CameraPaths::default()
                });
                let stop = Arc::new(AtomicBool::new(false));
                let fail = Arc::new(AtomicBool::new(fault != "none"));
                let mut threads = Vec::new();
                for name in ["rvd", "rmr0", "rmr1", "rwd"] {
                    if name == owner && fault == "missing" {
                        continue;
                    }
                    let listener = UnixListener::bind(root.join(format!("{name}.sock"))).unwrap();
                    listener.set_nonblocking(true).unwrap();
                    let stop = stop.clone();
                    let fail = fail.clone();
                    let daemon_root = root.clone();
                    threads.push(thread::spawn(move || {
                        let mut rule = "UTC0".to_owned();
                        let mut applied = false;
                        let deadline = Instant::now() + Duration::from_secs(5);
                        while !stop.load(Ordering::Acquire) && Instant::now() < deadline {
                            let (mut socket, _) = match listener.accept() {
                                Ok(socket) => socket,
                                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                                    thread::sleep(Duration::from_millis(1));
                                    continue;
                                }
                                other => panic!("{other:?}"),
                            };
                            socket
                                .set_read_timeout(Some(Duration::from_secs(1)))
                                .unwrap();
                            let request =
                                crate::json::parse(&super::super::tests::read_request(&mut socket))
                                    .unwrap();
                            let command = request.get_path("cmd").and_then(Value::as_str).unwrap();
                            let failing = fail.load(Ordering::Acquire);
                            let response = if name == owner && failing && fault == "preflight" {
                                r#"{"status":"error"}"#.to_owned()
                            } else if command == "timezone-reload" {
                                let expected = request
                                    .get_path("expected_rule")
                                    .and_then(Value::as_str)
                                    .unwrap();
                                if fs::read_to_string(daemon_root.join("TZ")).unwrap()
                                    != format!("{expected}\n")
                                    || (name == owner && failing && fault == "reload")
                                {
                                    r#"{"status":"error"}"#.to_owned()
                                } else {
                                    rule = expected.to_owned();
                                    applied = true;
                                    if name == owner && failing && fault == "file" {
                                        fs::write(daemon_root.join("TZ"), "UTC0\n").unwrap();
                                    }
                                    reply(&rule, &["rvd", "rmr0", "rmr1", "rwd"])
                                }
                            } else {
                                assert_eq!(command, "timezone-status");
                                if applied && failing && name == owner && fault == "readback" {
                                    reply("UTC0", &["rvd", "rmr0", "rmr1", "rwd"])
                                } else if applied && failing && name == owner && fault == "sample" {
                                    reply(&rule, &["rvd", "rmr0", "rmr1", "rwd"])
                                        .replace("15:00:00", "16:00:00")
                                } else if applied
                                    && failing
                                    && name == "rvd"
                                    && fault == "membership"
                                {
                                    reply(&rule, &["rvd"])
                                } else {
                                    reply(&rule, &["rvd", "rmr0", "rmr1", "rwd"])
                                }
                            };
                            socket
                                .write_all(&super::super::tests::framed(response.as_bytes()))
                                .unwrap();
                        }
                    }));
                }
                let body = br#"{"action":"update","timezone":"Europe/Helsinki","ntp_server_0":"pool.ntp.org","dhcp_ignore_timezone":true}"#;
                let result =
                    backend.update_time_config(body, Instant::now() + Duration::from_secs(2));
                if fault == "none" {
                    assert!(result.is_ok(), "{result:?}");
                } else if matches!(fault, "missing" | "preflight") {
                    assert!(result.is_err());
                    assert_eq!(fs::read_to_string(root.join("TZ")).unwrap(), "UTC0\n");
                    assert_eq!(
                        fs::read_to_string(root.join("timezone")).unwrap(),
                        "Etc/GMT\n"
                    );
                } else {
                    assert!(
                        matches!(result, Err(BackendError::PartialApply(_))),
                        "{fault}: {result:?}"
                    );
                    fail.store(false, Ordering::Release);
                    assert!(
                        backend
                            .update_time_config(body, Instant::now() + Duration::from_secs(2))
                            .is_ok()
                    );
                }
                assert!(
                    matches!(forbidden.accept(), Err(error) if error.kind() == io::ErrorKind::WouldBlock)
                );
                stop.store(true, Ordering::Release);
                for thread in threads {
                    thread.join().unwrap();
                }
                fs::remove_dir_all(root).unwrap();
            }
        }
    }
}
