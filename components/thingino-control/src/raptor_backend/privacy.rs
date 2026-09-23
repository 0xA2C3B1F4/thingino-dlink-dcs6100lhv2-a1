use super::*;

impl RaptorBackend {
    pub(super) fn privacy_state(&self, deadline: Instant) -> Result<bool, BackendError> {
        let reply = self.command(RaptorDaemon::Rvd, br#"{"cmd":"privacy-status"}"#, deadline)?;
        require_ok(&reply)?;
        if reply.value.get_path("supported").and_then(Value::as_bool) != Some(true) {
            return Err(BackendError::Unsupported(
                "Privacy masks are not configured in RVD",
            ));
        }
        let video = reply
            .value
            .get_path("video")
            .and_then(Value::as_array)
            .ok_or(BackendError::Upstream(502))?;
        let enabled = video
            .first()
            .and_then(Value::as_bool)
            .ok_or(BackendError::PartialApply(
                "Privacy video protection is not fully observed",
            ))?;
        if video.iter().any(|state| state.as_bool() != Some(enabled)) {
            return Err(BackendError::PartialApply(
                "Privacy is partial; video paths do not have matching verified protection",
            ));
        }
        for (field, daemon, state) in [
            ("jpeg_required", RaptorDaemon::Rhd, "privacy"),
            ("audio_required", RaptorDaemon::Rad, "muted"),
        ] {
            let required = reply
                .value
                .get_path(field)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?;
            if required {
                let participant = self.command(daemon, br#"{"cmd":"status"}"#, deadline)?;
                require_ok(&participant)?;
                if participant.value.get_path(state).and_then(Value::as_bool) != Some(enabled) {
                    return Err(BackendError::PartialApply(
                        "Privacy is partial; a required JPEG or audio path does not match",
                    ));
                }
            }
        }
        Ok(enabled)
    }

    pub(super) fn set_privacy(
        &self,
        enabled: bool,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        // Invalidate captures even if this transition later fails or is undone.
        self.ha_privacy_generation.fetch_add(1, Ordering::AcqRel);
        let command = format!(
            "{{\"cmd\":\"privacy\",\"value\":\"{}\"}}",
            if enabled { "on" } else { "off" }
        );
        let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
        if require_ok(&reply).is_err()
            || reply.value.get_path("complete").and_then(Value::as_bool) != Some(true)
        {
            return Err(BackendError::PartialApply(
                "Privacy apply was incomplete; protection may differ between media paths. Retry protection or explicitly select off",
            ));
        }
        if self.privacy_state(deadline)? != enabled {
            return Err(BackendError::Upstream(502));
        }
        Ok(BackendResponse::json(b"{\"status\":\"accepted\"}".to_vec()))
    }
}

struct PrivacyObservation {
    supported: bool,
    enabled: Option<bool>,
    participants: Vec<RaptorDaemon>,
}

impl RaptorBackend {
    fn privacy_config_observation(
        &self,
        deadline: Instant,
    ) -> Result<PrivacyObservation, BackendError> {
        let reply = self.command(RaptorDaemon::Rvd, br#"{"cmd":"privacy-status"}"#, deadline)?;
        require_ok(&reply)?;
        if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-startup") {
            return Err(BackendError::Unavailable);
        }
        let supported = reply
            .value
            .get_path("supported")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let video = reply
            .value
            .get_path("video")
            .and_then(Value::as_array)
            .filter(|video| video.len() <= 6)
            .ok_or(BackendError::Upstream(502))?;
        if video
            .iter()
            .any(|state| state != &Value::Null && state.as_bool().is_none())
        {
            return Err(BackendError::Upstream(502));
        }
        let mut enabled = video.first().and_then(Value::as_bool);
        if !supported || video.iter().any(|state| state.as_bool() != enabled) {
            enabled = None;
        }
        let mut participants = Vec::new();
        for (key, daemon) in [
            ("jpeg_required", RaptorDaemon::Rhd),
            ("audio_required", RaptorDaemon::Rad),
        ] {
            if !reply
                .value
                .get_path(key)
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?
            {
                continue;
            }
            participants.push(daemon);
            let peer = self.command(daemon, br#"{"cmd":"get-privacy-config"}"#, deadline)?;
            require_ok(&peer)?;
            if peer.value.get_path("persistence").and_then(Value::as_str) != Some("checked-startup")
            {
                return Err(BackendError::Unavailable);
            }
            let available = peer
                .value
                .get_path("available")
                .and_then(Value::as_bool)
                .ok_or(BackendError::Upstream(502))?;
            let observed = peer.value.get_path("enabled");
            if available {
                let value = observed
                    .and_then(Value::as_bool)
                    .ok_or(BackendError::Upstream(502))?;
                if Some(value) != enabled {
                    enabled = None;
                }
            } else {
                if observed != Some(&Value::Null) {
                    return Err(BackendError::Upstream(502));
                }
                enabled = None;
            }
        }
        Ok(PrivacyObservation {
            supported,
            enabled,
            participants,
        })
    }

    fn privacy_disk_enabled(
        &self,
        deadline: Instant,
        allow_default: bool,
    ) -> Result<bool, BackendError> {
        let reply = self.command(
            RaptorDaemon::Rvd,
            br#"{"cmd":"config-read-section","section":"privacy"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str) != Some("privacy")
            || reply
                .value
                .get_path("keys")
                .and_then(Value::as_object)
                .is_none()
        {
            return Err(BackendError::Upstream(502));
        }
        match reply.value.get_path("keys.enabled") {
            None if allow_default => Ok(false),
            Some(Value::String(value)) if value == "true" => Ok(true),
            Some(Value::String(value)) if value == "false" => Ok(false),
            _ => Err(BackendError::Upstream(502)),
        }
    }

    pub(super) fn privacy_config(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let state = self.privacy_config_observation(deadline)?;
        let saved = self.privacy_disk_enabled(deadline, true).ok();
        Ok(BackendResponse::json(format!(
            r#"{{"source":"raptor","persistent":true,"supported":{},"available":{},"enabled":{},"saved_enabled":{},"matches_saved":{}}}"#,
            state.supported, state.enabled.is_some(),
            state.enabled.map_or("null".into(), |value| value.to_string()),
            saved.map_or("null".into(), |value| value.to_string()),
            state.enabled.is_some() && state.enabled == saved
        ).into_bytes()))
    }

    pub(super) fn update_privacy_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let value = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let root = value
            .as_object()
            .filter(|fields| fields.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let fields = root
            .get("privacy")
            .and_then(Value::as_object)
            .filter(|fields| fields.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let enabled = fields
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
        let _mutation = self.lock_mutation()?;
        let before = self.privacy_config_observation(deadline)?;
        if !before.supported {
            return Err(BackendError::Unavailable);
        }
        self.ha_privacy_generation.fetch_add(1, Ordering::AcqRel);
        let apply = || -> Result<(), BackendError> {
            let command = format!(r#"{{"cmd":"set-privacy-config","enabled":{enabled}}}"#);
            let reply = self.command(RaptorDaemon::Rvd, command.as_bytes(), deadline)?;
            require_ok(&reply)?;
            if reply.value.get_path("complete").and_then(Value::as_bool) != Some(true) {
                return Err(BackendError::Upstream(502));
            }
            let live = self.privacy_config_observation(deadline)?;
            if live.enabled != Some(enabled) || live.participants != before.participants {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(RaptorDaemon::Rvd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            if self.privacy_disk_enabled(deadline, false)? != enabled {
                return Err(BackendError::Upstream(502));
            }
            let after = self.privacy_config_observation(deadline)?;
            if after.enabled != Some(enabled) || after.participants != before.participants {
                return Err(BackendError::Upstream(502));
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Privacy may have changed, but application or saving was not confirmed. Reload and explicitly retry privacy protection or off."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true,"applied":true}"#.to_vec(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::io::Write;
    use std::os::unix::net::UnixListener;
    use std::{fs, thread};

    fn video(enabled: Option<bool>, peers: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-startup","supported":true,"video":[{0},{0}],"jpeg_required":{peers},"audio_required":{peers}}}"#,
            enabled.map_or("null".into(), |value| value.to_string())
        )
    }

    fn peer(enabled: bool) -> String {
        format!(
            r#"{{"status":"ok","persistence":"checked-startup","available":true,"enabled":{enabled}}}"#
        )
    }

    fn sequence(
        root: &std::path::Path,
        name: &str,
        pairs: Vec<(&str, String)>,
    ) -> thread::JoinHandle<()> {
        let pairs: Vec<_> = pairs
            .into_iter()
            .map(|(cmd, reply)| (cmd.to_owned(), reply))
            .collect();
        let listener = UnixListener::bind(root.join(name)).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for (command, response) in pairs {
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing privacy request: {other:?}"),
                    }
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                let request = crate::json::parse(&read_request(&mut socket)).unwrap();
                assert_eq!(
                    request.get_path("cmd").and_then(Value::as_str),
                    Some(command.as_str())
                );
                socket.write_all(&framed(response.as_bytes())).unwrap();
            }
        })
    }

    #[test]
    fn privacy_persistence_requires_apply_all_peers_disk_and_final_observation() {
        for enabled in [false, true] {
            for fault in [
                "none",
                "setter",
                "video",
                "peer",
                "save",
                "disk",
                "final",
                "participants",
            ] {
                let root = task_temp("privacy-save");
                let mut rvd = vec![("privacy-status", video(Some(!enabled), true))];
                let mut rhd = vec![("get-privacy-config", peer(!enabled))];
                let mut rad = rhd.clone();
                rvd.push((
                    "set-privacy-config",
                    if fault == "setter" {
                        r#"{"status":"error","complete":false}"#.into()
                    } else {
                        r#"{"status":"ok","complete":true}"#.into()
                    },
                ));
                if fault != "setter" {
                    rvd.push((
                        "privacy-status",
                        video(
                            if fault == "video" {
                                None
                            } else {
                                Some(enabled)
                            },
                            fault != "participants",
                        ),
                    ));
                    if fault != "participants" {
                        rhd.push(("get-privacy-config", peer(enabled)));
                        rad.push((
                            "get-privacy-config",
                            peer(if fault == "peer" { !enabled } else { enabled }),
                        ));
                    }
                    if !matches!(fault, "video" | "peer" | "participants") {
                        rvd.push((
                            "config-save",
                            if fault == "save" {
                                r#"{"status":"error"}"#.into()
                            } else {
                                r#"{"status":"ok"}"#.into()
                            },
                        ));
                        if fault != "save" {
                            rvd.push(("config-read-section", format!(r#"{{"status":"ok","section":"privacy","keys":{{"enabled":"{}"}}}}"#, if fault == "disk" { !enabled } else { enabled })));
                            if fault != "disk" {
                                rvd.push((
                                    "privacy-status",
                                    video(
                                        Some(if fault == "final" { !enabled } else { enabled }),
                                        true,
                                    ),
                                ));
                                rhd.push(("get-privacy-config", peer(enabled)));
                                rad.push(("get-privacy-config", peer(enabled)));
                            }
                        }
                    }
                }
                let video_server = sequence(&root, "rvd.sock", rvd);
                let http_server = sequence(&root, "rhd.sock", rhd);
                let audio_server = sequence(&root, "rad.sock", rad);
                let request = format!(r#"{{"privacy":{{"enabled":{enabled}}}}}"#);
                let result = backend(&root, "127.0.0.1:1".parse().unwrap()).update_privacy_config(
                    request.as_bytes(),
                    Instant::now() + Duration::from_secs(2),
                );
                if fault == "none" {
                    let reply = crate::json::parse(&result.unwrap().body).unwrap();
                    assert_eq!(reply.get_path("persistent"), Some(&Value::Bool(true)));
                    assert_eq!(reply.get_path("applied"), Some(&Value::Bool(true)));
                } else {
                    assert!(
                        matches!(result, Err(BackendError::PartialApply(_))),
                        "{fault}: {result:?}"
                    );
                }
                video_server.join().unwrap();
                http_server.join().unwrap();
                audio_server.join().unwrap();
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn privacy_preflight_refuses_legacy_or_missing_participant_before_writes() {
        for missing in ["rvd-marker", "rhd-marker", "rad-missing"] {
            let root = task_temp("privacy-preflight");
            let rvd = sequence(
                &root,
                "rvd.sock",
                vec![(
                    "privacy-status",
                    if missing == "rvd-marker" {
                        r#"{"status":"ok","supported":true,"video":[false],"jpeg_required":true,"audio_required":true}"#.into()
                    } else {
                        video(Some(false), true)
                    },
                )],
            );
            let rhd = (missing != "rvd-marker").then(|| {
                sequence(
                    &root,
                    "rhd.sock",
                    vec![(
                        "get-privacy-config",
                        if missing == "rhd-marker" {
                            r#"{"status":"ok","privacy":false}"#.into()
                        } else {
                            peer(false)
                        },
                    )],
                )
            });
            let result = backend(&root, "127.0.0.1:1".parse().unwrap()).update_privacy_config(
                br#"{"privacy":{"enabled":true}}"#,
                Instant::now() + Duration::from_secs(1),
            );
            assert!(result.is_err() && !matches!(result, Err(BackendError::PartialApply(_))));
            rvd.join().unwrap();
            if let Some(rhd) = rhd {
                rhd.join().unwrap();
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn privacy_get_keeps_unknown_saved_and_partial_live_states_distinct() {
        for (live, disk, saved) in [
            (
                Some(true),
                r#"{"status":"ok","section":"privacy","keys":{"enabled":"false"}}"#,
                Some(false),
            ),
            (
                None,
                r#"{"status":"ok","section":"privacy","keys":{"enabled":"true"}}"#,
                Some(true),
            ),
            (Some(true), r#"{"status":"error"}"#, None),
            (
                Some(false),
                r#"{"status":"ok","section":"privacy","keys":{}}"#,
                Some(false),
            ),
        ] {
            let root = task_temp("privacy-get");
            let server = sequence(
                &root,
                "rvd.sock",
                vec![
                    ("privacy-status", video(live, false)),
                    ("config-read-section", disk.into()),
                ],
            );
            let result = backend(&root, "127.0.0.1:1".parse().unwrap())
                .privacy_config(Instant::now() + Duration::from_secs(1))
                .unwrap();
            let reply = crate::json::parse(&result.body).unwrap();
            assert_eq!(
                reply.get_path("enabled"),
                Some(&live.map_or(Value::Null, Value::Bool))
            );
            assert_eq!(
                reply.get_path("saved_enabled"),
                Some(&saved.map_or(Value::Null, Value::Bool))
            );
            assert_eq!(
                reply.get_path("matches_saved"),
                Some(&Value::Bool(live.is_some() && live == saved))
            );
            server.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn privacy_rejects_mixed_domains_and_implicit_or_extra_fields() {
        let root = task_temp("privacy-invalid");
        let backend = backend(&root, "127.0.0.1:1".parse().unwrap());
        for body in [
            r#"{"privacy":{"enabled":true},"motion":{"enabled":true}}"#,
            r#"{"privacy":{"enabled":1}}"#,
            r#"{"privacy":{}}"#,
            r#"{"privacy":{"enabled":true,"stream0_enabled":true}}"#,
        ] {
            assert!(matches!(
                backend.update_privacy_config(
                    body.as_bytes(),
                    Instant::now() + Duration::from_secs(1)
                ),
                Err(BackendError::Protocol)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }
}
