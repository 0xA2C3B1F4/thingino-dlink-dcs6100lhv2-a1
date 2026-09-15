//! Per-owner recorder policy. No request changes another writer or Prudynt config.
use super::*;
use std::collections::BTreeMap;

const NUMBERS: [(&str, u64, u64, u64); 4] = [
    ("duration", 1, 86400, 300),
    ("limit", 1, 1000, 15),
    ("min_free_mb", 1, 1000000, 1),
    ("check_interval", 1, 86400, 60),
];
const FLAGS: [&str; 2] = ["autostart", "cleanup_enabled"];

fn owner(channel: u64) -> Result<RaptorDaemon, BackendError> {
    match channel {
        0 => Ok(RaptorDaemon::Rmr0),
        1 => Ok(RaptorDaemon::Rmr1),
        _ => Err(BackendError::Protocol),
    }
}

fn validate_video(value: &Value, channel: u64) -> Result<(), BackendError> {
    let fields = value
        .as_object()
        .filter(|fields| fields.len() == 10)
        .ok_or(BackendError::Protocol)?;
    if value_u64(fields.get("channel").ok_or(BackendError::Protocol)?)? != channel {
        return Err(BackendError::Protocol);
    }
    for (field, expected) in [
        ("mount", "/mnt/mmcblk0p1".to_owned()),
        ("device_path", format!("raptor/stream{channel}")),
        ("filename", "%Y-%m-%d/%H-%M-%S".to_owned()),
    ] {
        if fields.get(field).and_then(Value::as_str) != Some(expected.as_str()) {
            return Err(BackendError::Protocol);
        }
    }
    for field in FLAGS {
        fields
            .get(field)
            .and_then(Value::as_bool)
            .ok_or(BackendError::Protocol)?;
    }
    for (field, minimum, maximum, _) in NUMBERS {
        let number = value_u64(fields.get(field).ok_or(BackendError::Protocol)?)?;
        if !(minimum..=maximum).contains(&number) {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

fn checked(reply: &RaptorReply, channel: u64) -> Result<&Value, BackendError> {
    require_ok(reply)?;
    reply.require_only_fields(&[
        "status",
        "persistence",
        "settings",
        "boot_pending",
        "cleanup_ok",
        "storage_available",
        "free_mb",
    ])?;
    if reply.value.get_path("persistence").and_then(Value::as_str) != Some("checked-config") {
        return Err(BackendError::Unavailable);
    }
    let settings = reply
        .value
        .get_path("settings")
        .ok_or(BackendError::Upstream(502))?;
    validate_video(settings, channel).map_err(|_| BackendError::Upstream(502))?;
    for field in ["boot_pending", "cleanup_ok", "storage_available"] {
        reply
            .value
            .get_path(field)
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
    }
    let free = reply
        .value
        .get_path("free_mb")
        .ok_or(BackendError::Upstream(502))?;
    if reply
        .value
        .get_path("storage_available")
        .and_then(Value::as_bool)
        == Some(true)
    {
        value_u64(free).map_err(|_| BackendError::Upstream(502))?;
    } else if free != &Value::Null {
        return Err(BackendError::Upstream(502));
    }
    Ok(settings)
}

impl RaptorBackend {
    fn recorder_observation(
        &self,
        channel: u64,
        deadline: Instant,
    ) -> Result<RaptorReply, BackendError> {
        let reply = self.command(
            owner(channel)?,
            br#"{"cmd":"get-recorder-config"}"#,
            deadline,
        )?;
        checked(&reply, channel)?;
        Ok(reply)
    }

    fn recorder_disk(&self, channel: u64, deadline: Instant) -> Result<Value, BackendError> {
        let section = format!("recorder{channel}");
        let command = format!(r#"{{"cmd":"config-read-section","section":"{section}"}}"#);
        let reply = self.command(owner(channel)?, command.as_bytes(), deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str) != Some(section.as_str()) {
            return Err(BackendError::Upstream(502));
        }
        let keys = reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        if keys.keys().any(|key| {
            !FLAGS.contains(&key.as_str()) && !NUMBERS.iter().any(|(field, _, _, _)| field == key)
        }) {
            return Err(BackendError::Upstream(502));
        }
        let mut fields = BTreeMap::from([
            ("channel".to_owned(), Value::Number(channel.to_string())),
            (
                "mount".to_owned(),
                Value::String("/mnt/mmcblk0p1".to_owned()),
            ),
            (
                "device_path".to_owned(),
                Value::String(format!("raptor/stream{channel}")),
            ),
            (
                "filename".to_owned(),
                Value::String("%Y-%m-%d/%H-%M-%S".to_owned()),
            ),
        ]);
        for field in FLAGS {
            let value = match keys.get(field) {
                None => false,
                Some(Value::String(text)) if text == "true" => true,
                Some(Value::String(text)) if text == "false" => false,
                _ => return Err(BackendError::Upstream(502)),
            };
            fields.insert(field.to_owned(), Value::Bool(value));
        }
        for (field, minimum, maximum, default) in NUMBERS {
            let number = match keys.get(field) {
                None => default,
                Some(Value::String(text))
                    if !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit()) =>
                {
                    text.parse::<u64>()
                        .map_err(|_| BackendError::Upstream(502))?
                }
                _ => return Err(BackendError::Upstream(502)),
            };
            if !(minimum..=maximum).contains(&number) {
                return Err(BackendError::Upstream(502));
            }
            fields.insert(field.to_owned(), Value::Number(number.to_string()));
        }
        Ok(Value::Object(fields))
    }

    pub(super) fn recorder_config(
        &self,
        channel: u64,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let observed = self.recorder_observation(channel, deadline)?;
        let video = checked(&observed, channel)?.clone();
        let saved = self.recorder_disk(channel, deadline).ok();
        let runtime = self.recording_observation(channel, deadline).ok();
        let data = Value::Object(BTreeMap::from([
            ("source".to_owned(), Value::String("raptor".to_owned())),
            ("persistent".to_owned(), Value::Bool(true)),
            (
                "matches_saved".to_owned(),
                Value::Bool(saved.as_ref() == Some(&video)),
            ),
            ("video".to_owned(), video),
            ("saved_video".to_owned(), saved.unwrap_or(Value::Null)),
            (
                "runtime".to_owned(),
                runtime.map_or(Value::Null, |reply| reply.value),
            ),
            (
                "storage_available".to_owned(),
                observed
                    .value
                    .get_path("storage_available")
                    .unwrap()
                    .clone(),
            ),
            (
                "free_mb".to_owned(),
                observed.value.get_path("free_mb").unwrap().clone(),
            ),
            (
                "boot_pending".to_owned(),
                observed.value.get_path("boot_pending").unwrap().clone(),
            ),
            (
                "cleanup_ok".to_owned(),
                observed.value.get_path("cleanup_ok").unwrap().clone(),
            ),
            ("timelapse".to_owned(), self.timelapse.policy_value()),
            ("timelapse_supported".to_owned(), Value::Bool(true)),
            (
                "mounts".to_owned(),
                Value::Array(vec![Value::String("/mnt/mmcblk0p1".to_owned())]),
            ),
        ]));
        Ok(BackendResponse::json(
            Value::Object(BTreeMap::from([
                ("ok".to_owned(), Value::Bool(true)),
                ("data".to_owned(), data),
            ]))
            .to_json()
            .into_bytes(),
        ))
    }

    pub(super) fn update_recorder_config(
        &self,
        selected: Option<u64>,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let root = request
            .as_object()
            .filter(|fields| fields.len() == 1)
            .ok_or(BackendError::Protocol)?;
        let patch = root
            .get("video")
            .and_then(Value::as_object)
            .ok_or(BackendError::Protocol)?;
        let channel = value_u64(patch.get("channel").ok_or(BackendError::Protocol)?)?;
        let target = owner(channel)?;
        if selected.is_some_and(|selected| selected != channel) || patch.is_empty() {
            return Err(BackendError::Protocol);
        }
        let _guard = self.lock_mutation()?;
        let before = self.recorder_observation(channel, deadline)?;
        let mut video = checked(&before, channel)?.clone();
        video
            .merge(&Value::Object(patch.clone()))
            .map_err(|_| BackendError::Protocol)?;
        validate_video(&video, channel)?;
        if channel == 1
            && video.get_path("autostart").and_then(Value::as_bool) == Some(true)
            && !self.stream_configured_enabled(1, deadline)?
        {
            return Err(BackendError::Unavailable);
        }
        let mut settings = video.as_object().unwrap().clone();
        for immutable in ["channel", "mount", "device_path", "filename"] {
            settings.remove(immutable);
        }
        let command = format!(
            r#"{{"cmd":"set-recorder-config","settings":{}}}"#,
            Value::Object(settings).to_json()
        );
        let apply = || -> Result<(), BackendError> {
            require_ok(&self.command(target, command.as_bytes(), deadline)?)?;
            let live = self.recorder_observation(channel, deadline)?;
            if checked(&live, channel)? != &video {
                return Err(BackendError::Upstream(502));
            }
            require_ok(&self.command(target, br#"{"cmd":"config-save"}"#, deadline)?)?;
            if self.recorder_disk(channel, deadline)? != video {
                return Err(BackendError::Upstream(502));
            }
            let after = self.recorder_observation(channel, deadline)?;
            if checked(&after, channel)? != &video {
                return Err(BackendError::Upstream(502));
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("Recorder policy may have changed but save/readback was not confirmed. Reload the selected stream before retrying."))?;
        Ok(BackendResponse::json(
            br#"{"status":"accepted","persistent":true}"#.to_vec(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::thread;

    fn video(channel: u64, duration: u64) -> Value {
        crate::json::parse(format!(r#"{{"autostart":false,"cleanup_enabled":false,"duration":{duration},"limit":15,"min_free_mb":1,"check_interval":60,"channel":{channel},"mount":"/mnt/mmcblk0p1","device_path":"raptor/stream{channel}","filename":"%Y-%m-%d/%H-%M-%S"}}"#).as_bytes()).unwrap()
    }

    fn observation(channel: u64, duration: u64) -> Vec<u8> {
        format!(r#"{{"status":"ok","persistence":"checked-config","settings":{},"boot_pending":false,"cleanup_ok":true,"storage_available":false,"free_mb":null}}"#, video(channel, duration).to_json()).into_bytes()
    }

    #[test]
    fn recorder_policy_save_is_selected_owner_only_and_checks_disk() {
        for channel in 0..=1 {
            for disk_duration in [300, 600] {
                let root = task_temp("recorder-policy-save");
                let listener = UnixListener::bind(root.join(format!("rmr{channel}.sock"))).unwrap();
                let other =
                    UnixListener::bind(root.join(format!("rmr{}.sock", 1 - channel))).unwrap();
                other.set_nonblocking(true).unwrap();
                let mut commands = vec![
                    ("get-recorder-config", observation(channel, 300)),
                    ("set-recorder-config", br#"{"status":"ok"}"#.to_vec()),
                    ("get-recorder-config", observation(channel, 600)),
                    ("config-save", br#"{"status":"ok"}"#.to_vec()),
                    ("config-read-section", format!(r#"{{"status":"ok","section":"recorder{channel}","keys":{{"duration":"{disk_duration}"}}}}"#).into_bytes()),
                ];
                if disk_duration == 600 {
                    commands.push(("get-recorder-config", observation(channel, 600)));
                }
                listener.set_nonblocking(true).unwrap();
                let server = thread::spawn(move || {
                    for (expected, response) in commands {
                        let deadline = Instant::now() + Duration::from_secs(3);
                        let mut socket = loop {
                            match listener.accept() {
                                Ok((socket, _)) => break socket,
                                Err(error)
                                    if error.kind() == io::ErrorKind::WouldBlock
                                        && Instant::now() < deadline =>
                                {
                                    thread::sleep(Duration::from_millis(2))
                                }
                                other => panic!("missing policy command: {other:?}"),
                            }
                        };
                        let request = crate::json::parse(&read_request(&mut socket)).unwrap();
                        assert_eq!(
                            request.get_path("cmd").and_then(Value::as_str),
                            Some(expected)
                        );
                        if expected == "set-recorder-config" {
                            assert_eq!(
                                request
                                    .get_path("settings.duration")
                                    .map(value_u64)
                                    .transpose()
                                    .unwrap(),
                                Some(600)
                            );
                        }
                        socket.write_all(&framed(&response)).unwrap();
                    }
                });
                let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
                let request = format!(r#"{{"video":{{"channel":{channel},"duration":600}}}}"#);
                let result = adapter.update_recorder_config(
                    Some(channel),
                    request.as_bytes(),
                    Instant::now() + Duration::from_secs(2),
                );
                if disk_duration == 600 {
                    assert!(result.is_ok(), "{result:?}");
                } else {
                    assert!(matches!(result, Err(BackendError::PartialApply(_))));
                }
                server.join().unwrap();
                assert_eq!(
                    other.accept().unwrap_err().kind(),
                    io::ErrorKind::WouldBlock
                );
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn recorder_policy_rejects_cross_channel_and_fixed_path_edits() {
        let mut value = video(0, 300);
        assert!(validate_video(&value, 1).is_err());
        value
            .merge(&crate::json::parse(br#"{"mount":"/etc"}"#).unwrap())
            .unwrap();
        assert!(validate_video(&value, 0).is_err());
        let root = task_temp("recorder-policy-invalid");
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.update_recorder_config(Some(0), br#"{"video":{"channel":1}}"#, Instant::now()),
            Err(BackendError::Protocol)
        ));
        assert!(matches!(
            adapter.update_recorder_config(
                None,
                br#"{"timelapse":{"enabled":true}}"#,
                Instant::now()
            ),
            Err(BackendError::Protocol)
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pending_substream_disable_blocks_recorder_autostart() {
        let root = task_temp("recorder-policy-disabled-substream");
        let listener = UnixListener::bind(root.join("rmr1.sock")).unwrap();
        let recorder = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let request = crate::json::parse(&read_request(&mut socket)).unwrap();
            assert_eq!(
                request.get_path("cmd").and_then(Value::as_str),
                Some("get-recorder-config")
            );
            socket.write_all(&framed(&observation(1, 300))).unwrap();
        });
        let enabled = super::super::recorder::tests::enabled_sequence(&root, 1, false);
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.update_recorder_config(
                Some(1),
                br#"{"video":{"channel":1,"autostart":true}}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Unavailable)
        ));
        recorder.join().unwrap();
        enabled.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
