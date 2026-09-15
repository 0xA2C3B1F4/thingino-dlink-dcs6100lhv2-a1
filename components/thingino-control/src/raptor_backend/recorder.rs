use super::*;

fn daemon(channel: u64) -> Result<RaptorDaemon, BackendError> {
    match channel {
        0 => Ok(RaptorDaemon::Rmr0),
        1 => Ok(RaptorDaemon::Rmr1),
        _ => Err(BackendError::Protocol),
    }
}

fn validate(reply: &RaptorReply, channel: u64) -> Result<(), BackendError> {
    require_ok(reply)?;
    reply.require_only_fields(&[
        "status",
        "stream_id",
        "available",
        "recording",
        "file_closed",
        "reason",
    ])?;
    if reply
        .value
        .get_path("stream_id")
        .map(value_u64)
        .transpose()?
        != Some(channel)
    {
        return Err(BackendError::Upstream(502));
    }
    let available = reply
        .value
        .get_path("available")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let closed = reply
        .value
        .get_path("file_closed")
        .and_then(Value::as_bool)
        .ok_or(BackendError::Upstream(502))?;
    let recording = reply
        .value
        .get_path("recording")
        .ok_or(BackendError::Upstream(502))?;
    let reason = reply
        .value
        .get_path("reason")
        .ok_or(BackendError::Upstream(502))?;
    if !matches!(recording, Value::Bool(_) | Value::Null)
        || (recording == &Value::Bool(true) && closed)
        || (recording == &Value::Bool(false) && !closed)
        || !(reason == &Value::Null
            || matches!(
                reason.as_str(),
                Some(
                    "no_sd"
                        | "storage_unknown"
                        | "no_space"
                        | "video_unavailable"
                        | "writer_error"
                        | "starting"
                        | "stopping"
                )
            ))
        || (!available && reason == &Value::Null)
    {
        return Err(BackendError::Upstream(502));
    }
    Ok(())
}

pub(super) fn recording_request(request: &Value) -> Result<(u64, bool), BackendError> {
    let fields = request
        .as_object()
        .filter(|v| v.len() == 1)
        .ok_or(BackendError::Protocol)?;
    let (action, payload) = fields.iter().next().unwrap();
    let enabled = match action.as_str() {
        "start" => true,
        "stop" => false,
        _ => return Err(BackendError::Protocol),
    };
    let payload = payload
        .as_object()
        .filter(|v| v.len() == 1)
        .ok_or(BackendError::Protocol)?;
    let channel = value_u64(payload.get("channel").ok_or(BackendError::Protocol)?)?;
    daemon(channel)?;
    Ok((channel, enabled))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum MotionEventStart {
    NotOwned,
    Owned { generation: u32, confirmed: bool },
}

impl RaptorBackend {
    pub(super) fn recording_observation(
        &self,
        channel: u64,
        deadline: Instant,
    ) -> Result<RaptorReply, BackendError> {
        let reply = self.command(
            daemon(channel)?,
            br#"{"cmd":"get-recording-state"}"#,
            deadline,
        )?;
        validate(&reply, channel)?;
        Ok(reply)
    }

    pub(super) fn set_recording(
        &self,
        request: &Value,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let (channel, enabled) = recording_request(request)?;
        if channel == 1 && enabled && !self.stream_configured_enabled(1, deadline)? {
            return Err(BackendError::Unavailable);
        }
        let target = daemon(channel)?;
        let before = self.recording_observation(channel, deadline)?;
        if enabled && before.value.get_path("available").and_then(Value::as_bool) != Some(true) {
            return Err(BackendError::Unavailable);
        }
        let confirmed = |reply: &RaptorReply| {
            reply.value.get_path("recording").and_then(Value::as_bool) == Some(enabled)
                && (enabled
                    || reply.value.get_path("file_closed").and_then(Value::as_bool) == Some(true))
                && (!enabled || reply.value.get_path("reason") == Some(&Value::Null))
        };
        if confirmed(&before) {
            return Ok(BackendResponse::json(br#"{"status":"accepted"}"#.to_vec()));
        }
        let apply = || -> Result<(), BackendError> {
            let body = format!(r#"{{"cmd":"set-recording","enabled":{enabled}}}"#);
            require_ok(&self.command(target, body.as_bytes(), deadline)?)?;
            // The acknowledgement is intent only. Read actual writer evidence separately.
            let until = deadline.min(Instant::now() + Duration::from_millis(2500));
            loop {
                let after = self.recording_observation(channel, until)?;
                if confirmed(&after) {
                    return Ok(());
                }
                if Instant::now() + Duration::from_millis(40) >= until {
                    return Err(BackendError::Upstream(504));
                }
                std::thread::sleep(Duration::from_millis(40));
            }
        };
        apply().map_err(|_| BackendError::PartialApply("Recording transition or writer readback was incomplete. Reload before explicitly retrying."))?;
        Ok(BackendResponse::json(br#"{"status":"accepted"}"#.to_vec()))
    }

    pub(super) fn motion_event_start_recording(
        &self,
        channel: u64,
        deadline: Instant,
    ) -> Result<MotionEventStart, BackendError> {
        let _mutation = self.lock_mutation()?;
        if channel == 1 && !self.stream_configured_enabled(1, deadline)? {
            return Err(BackendError::Unavailable);
        }
        let generation = self
            .recording_user_generation
            .get(usize::try_from(channel).map_err(|_| BackendError::Protocol)?)
            .ok_or(BackendError::Protocol)?
            .load(Ordering::Acquire);
        let before = self.recording_observation(channel, deadline)?;
        match (
            before.value.get_path("recording").and_then(Value::as_bool),
            before
                .value
                .get_path("file_closed")
                .and_then(Value::as_bool),
        ) {
            (Some(true), Some(false)) => return Ok(MotionEventStart::NotOwned),
            (Some(false), Some(true)) => {}
            _ => return Err(BackendError::Unavailable),
        }
        if before.value.get_path("available").and_then(Value::as_bool) != Some(true) {
            return Err(BackendError::Unavailable);
        }
        let uncertain = || {
            Ok(MotionEventStart::Owned {
                generation,
                confirmed: false,
            })
        };
        let body = br#"{"cmd":"set-recording","enabled":true}"#;
        let applied = match self.command(daemon(channel)?, body, deadline) {
            Ok(reply) => require_ok(&reply).is_ok(),
            Err(_) => false,
        };
        if !applied {
            return uncertain();
        }
        let until = deadline.min(Instant::now() + Duration::from_millis(2500));
        loop {
            let after = match self.recording_observation(channel, until) {
                Ok(after) => after,
                Err(_) => return uncertain(),
            };
            if after.value.get_path("recording").and_then(Value::as_bool) == Some(true)
                && after.value.get_path("reason") == Some(&Value::Null)
            {
                return Ok(MotionEventStart::Owned {
                    generation,
                    confirmed: true,
                });
            }
            if Instant::now() + Duration::from_millis(40) >= until {
                return uncertain();
            }
            std::thread::sleep(Duration::from_millis(40));
        }
    }

    pub(super) fn motion_event_stop_recording(
        &self,
        channel: u64,
        expected_generation: u32,
        deadline: Instant,
    ) -> Result<bool, BackendError> {
        let generation = self
            .recording_user_generation
            .get(usize::try_from(channel).map_err(|_| BackendError::Protocol)?)
            .ok_or(BackendError::Protocol)?;
        if generation.load(Ordering::Acquire) != expected_generation {
            return Ok(false);
        }
        let _mutation = self.lock_mutation()?;
        if generation.load(Ordering::Acquire) != expected_generation {
            return Ok(false);
        }
        let before = self.recording_observation(channel, deadline)?;
        match (
            before.value.get_path("recording").and_then(Value::as_bool),
            before
                .value
                .get_path("file_closed")
                .and_then(Value::as_bool),
        ) {
            (Some(false), Some(true)) => return Ok(false),
            (Some(true), Some(false)) => {}
            _ => {
                return Err(BackendError::PartialApply(
                    "Recording closure is not confirmed. Retry from fresh writer state.",
                ));
            }
        }
        let request =
            crate::json::parse(format!(r#"{{"stop":{{"channel":{channel}}}}}"#).as_bytes())
                .map_err(|_| BackendError::Protocol)?;
        self.set_recording(&request, deadline).map(|_| true)
    }

    fn storage_maintenance_observation(
        &self,
        channel: u64,
        deadline: Instant,
    ) -> Result<(bool, bool), BackendError> {
        let reply = self.command(
            daemon(channel)?,
            br#"{"cmd":"get-storage-maintenance"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "stream_id", "paused", "quiescent"])?;
        if reply
            .value
            .get_path("stream_id")
            .map(value_u64)
            .transpose()?
            != Some(channel)
        {
            return Err(BackendError::Upstream(502));
        }
        let paused = reply
            .value
            .get_path("paused")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        let quiescent = reply
            .value
            .get_path("quiescent")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if !paused && quiescent {
            return Err(BackendError::Upstream(502));
        }
        Ok((paused, quiescent))
    }

    pub(super) fn set_storage_maintenance(
        &self,
        channel: u64,
        paused: bool,
        deadline: Instant,
    ) -> Result<(), BackendError> {
        let request = format!(r#"{{"cmd":"set-storage-maintenance","paused":{paused}}}"#);
        require_ok(&self.command(daemon(channel)?, request.as_bytes(), deadline)?)?;
        let until = deadline.min(Instant::now() + Duration::from_millis(2500));
        loop {
            let (observed, quiescent) = self.storage_maintenance_observation(channel, until)?;
            if observed == paused && (!paused || quiescent) {
                return Ok(());
            }
            if Instant::now() + Duration::from_millis(40) >= until {
                return Err(BackendError::Timeout);
            }
            std::thread::sleep(Duration::from_millis(40));
        }
    }
}

#[cfg(test)]
pub(in crate::raptor_backend) mod tests {
    use super::super::tests::{backend, framed, read_request, task_temp};
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::thread;

    pub(in crate::raptor_backend) fn state(
        channel: u64,
        recording: Option<bool>,
        closed: bool,
        available: bool,
        reason: Option<&str>,
    ) -> Vec<u8> {
        format!(r#"{{"status":"ok","stream_id":{channel},"available":{available},"recording":{},"file_closed":{closed},"reason":{}}}"#,
            recording.map_or("null".to_owned(), |v| v.to_string()), reason.map_or("null".to_owned(), |v| Value::String(v.to_owned()).to_json())).into_bytes()
    }
    pub(in crate::raptor_backend) fn sequence(
        root: &std::path::Path,
        channel: u64,
        pairs: Vec<(Vec<u8>, Vec<u8>)>,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join(format!("rmr{channel}.sock"))).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            for (request, response) in pairs {
                let until = Instant::now() + Duration::from_secs(3);
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(e)
                            if e.kind() == io::ErrorKind::WouldBlock && Instant::now() < until =>
                        {
                            thread::sleep(Duration::from_millis(2))
                        }
                        other => panic!("missing recorder request: {other:?}"),
                    }
                };
                assert_eq!(read_request(&mut socket), request);
                socket.write_all(&framed(&response)).unwrap();
            }
        })
    }
    pub(in crate::raptor_backend) fn get() -> Vec<u8> {
        br#"{"cmd":"get-recording-state"}"#.to_vec()
    }
    pub(in crate::raptor_backend) fn set(on: bool) -> Vec<u8> {
        format!(r#"{{"cmd":"set-recording","enabled":{on}}}"#).into_bytes()
    }
    pub(in crate::raptor_backend) fn enabled_sequence(
        root: &std::path::Path,
        checks: usize,
        configured: bool,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
        thread::spawn(move || {
            for _ in 0..checks {
                for (request, response) in [
                    (
                        br#"{"cmd":"get-stream-enabled","stream_id":1}"#.as_slice(),
                        if configured {
                            br#"{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":1,"supported":true,"editable":true,"required":false,"active_enabled":true,"configured_enabled":true,"pending_restart":false,"motion_blocks_disable":false,"recorder_blocks_disable":false}"#.as_slice()
                        } else {
                            br#"{"status":"ok","persistence":"checked-next-full-stack-restart","stream_id":1,"supported":true,"editable":true,"required":false,"active_enabled":true,"configured_enabled":false,"pending_restart":true,"motion_blocks_disable":false,"recorder_blocks_disable":false}"#.as_slice()
                        },
                    ),
                    (
                        br#"{"cmd":"config-read-section","section":"stream1"}"#.as_slice(),
                        if configured {
                            br#"{"status":"ok","section":"stream1","keys":{"enabled":"true"}}"#
                                .as_slice()
                        } else {
                            br#"{"status":"ok","section":"stream1","keys":{"enabled":"false"}}"#
                                .as_slice()
                        },
                    ),
                ] {
                    let (mut socket, _) = listener.accept().unwrap();
                    assert_eq!(read_request(&mut socket), request);
                    socket.write_all(&framed(response)).unwrap();
                }
            }
        })
    }
    fn request(channel: u64, on: bool) -> Vec<u8> {
        format!(
            r#"{{"mp4":{{"{}":{{"channel":{channel}}}}}}}"#,
            if on { "start" } else { "stop" }
        )
        .into_bytes()
    }

    #[test]
    fn recording_uses_selected_owner_and_fresh_writer_evidence() {
        for channel in 0..=1 {
            for on in [true, false] {
                let root = task_temp("recorder-readback");
                let running = state(channel, Some(true), false, true, None);
                let stopped = state(channel, Some(false), true, true, None);
                let pending = state(
                    channel,
                    None,
                    !on,
                    true,
                    Some(if on { "starting" } else { "stopping" }),
                );
                let target = if on { running.clone() } else { stopped.clone() };
                let server = sequence(
                    &root,
                    channel,
                    vec![
                        (get(), if on { stopped } else { running }),
                        (set(on), target.clone()),
                        (get(), pending),
                        (get(), target),
                    ],
                );
                let enabled = (channel == 1 && on).then(|| enabled_sequence(&root, 1, true));
                let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
                assert!(
                    adapter
                        .live_control(
                            &request(channel, on),
                            Instant::now() + Duration::from_secs(2)
                        )
                        .is_ok()
                );
                server.join().unwrap();
                if let Some(enabled) = enabled {
                    enabled.join().unwrap();
                }
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn recording_no_sd_blocks_start_but_allows_active_stop() {
        let root = task_temp("recorder-no-sd");
        let server = sequence(
            &root,
            0,
            vec![(get(), state(0, Some(false), true, false, Some("no_sd")))],
        );
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.live_control(&request(0, true), Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Unavailable)
        ));
        server.join().unwrap();
        fs::remove_file(root.join("rmr0.sock")).unwrap();
        let off = state(0, Some(false), true, false, Some("no_sd"));
        let server = sequence(
            &root,
            0,
            vec![
                (get(), state(0, Some(true), false, false, Some("no_sd"))),
                (set(false), off.clone()),
                (get(), off),
            ],
        );
        assert!(
            adapter
                .live_control(&request(0, false), Instant::now() + Duration::from_secs(1))
                .is_ok()
        );
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn recording_heartbeat_keeps_missing_owner_unknown_and_no_sd_off() {
        let root = task_temp("recorder-heartbeat");
        let server = sequence(
            &root,
            0,
            vec![(get(), state(0, Some(false), true, false, Some("no_sd")))],
        );
        fs::write(root.join("uptime"), b"123.0 0.0\n").unwrap();
        let mut adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        adapter.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            uptime: root.join("uptime"),
            ..crate::camera::CameraPaths::default()
        });
        let response = adapter
            .heartbeat(Instant::now() + Duration::from_secs(1))
            .unwrap();
        let value = crate::json::parse(&response.body).unwrap();
        assert_eq!(value.get_path("rec_ch0"), Some(&Value::Bool(false)));
        assert_eq!(
            value.get_path("rec_ch0_available"),
            Some(&Value::Bool(false))
        );
        assert_eq!(
            value.get_path("rec_ch0_reason").and_then(Value::as_str),
            Some("no_sd")
        );
        assert_eq!(value.get_path("rec_ch1"), Some(&Value::Null));
        assert!(value.get_path("rec_ch1_available").is_none());
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn recording_unconfirmed_post_command_is_partial() {
        let root = task_temp("recorder-partial");
        let server = sequence(
            &root,
            1,
            vec![
                (get(), state(1, Some(false), true, true, None)),
                (set(true), state(1, Some(true), false, true, None)),
                (get(), b"{\"status\":\"error\"}".to_vec()),
            ],
        );
        let enabled = enabled_sequence(&root, 1, true);
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.live_control(&request(1, true), Instant::now() + Duration::from_secs(1)),
            Err(BackendError::PartialApply(_))
        ));
        server.join().unwrap();
        enabled.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn recording_rejects_bad_requests_and_inconsistent_native_state() {
        let root = task_temp("recorder-invalid");
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        for body in [
            r#"{"mp4":{"start":{"channel":2}}}"#,
            r#"{"mp4":{"start":{"channel":0,"path":"x"}}}"#,
            r#"{"mp4":{"start":{"channel":0},"stop":{"channel":1}}}"#,
        ] {
            assert!(matches!(
                adapter.live_control(body.as_bytes(), Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        for body in [
            state(1, Some(false), true, true, None),
            state(0, Some(true), true, true, None),
            state(0, Some(false), false, true, None),
            state(0, None, false, false, None),
        ] {
            let server = sequence(&root, 0, vec![(get(), body)]);
            assert!(
                adapter
                    .recording_observation(0, Instant::now() + Duration::from_secs(1))
                    .is_err()
            );
            server.join().unwrap();
            fs::remove_file(root.join("rmr0.sock")).unwrap();
        }
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn stale_storage_observation_cannot_start_recording() {
        let root = task_temp("recorder-storage-stale");
        let server = sequence(
            &root,
            0,
            vec![(
                get(),
                state(0, Some(false), true, false, Some("storage_unknown")),
            )],
        );
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.live_control(&request(0, true), Instant::now() + Duration::from_secs(2)),
            Err(BackendError::Unavailable)
        ));
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn user_generation_revokes_event_stop_without_recorder_ipc() {
        let root = task_temp("recorder-event-generation");
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        let server = sequence(
            &root,
            1,
            vec![(get(), state(1, Some(true), false, true, None))],
        );
        let enabled = enabled_sequence(&root, 1, true);
        assert!(
            adapter
                .live_control(&request(1, true), Instant::now() + Duration::from_secs(1))
                .is_ok()
        );
        assert!(
            !adapter
                .motion_event_stop_recording(1, 0, Instant::now() + Duration::from_millis(100))
                .unwrap()
        );
        server.join().unwrap();
        enabled.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pending_substream_disable_blocks_manual_and_event_recording_start() {
        let root = task_temp("recorder-disabled-substream");
        let enabled = enabled_sequence(&root, 2, false);
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.live_control(&request(1, true), Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Unavailable)
        ));
        assert!(matches!(
            adapter.motion_event_start_recording(1, Instant::now() + Duration::from_secs(1),),
            Err(BackendError::Unavailable)
        ));
        enabled.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn event_start_captures_generation_after_a_manual_stop() {
        let root = task_temp("recorder-event-start-generation");
        let stopped = state(1, Some(false), true, true, None);
        let running = state(1, Some(true), false, true, None);
        let server = sequence(
            &root,
            1,
            vec![
                (get(), stopped.clone()),
                (get(), stopped),
                (set(true), running.clone()),
                (get(), running.clone()),
                (get(), running),
                (get(), state(1, Some(true), false, true, None)),
                (set(false), state(1, Some(false), true, true, None)),
                (get(), state(1, Some(false), true, true, None)),
            ],
        );
        let enabled = enabled_sequence(&root, 1, true);
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(
            adapter
                .live_control(
                    br#"{"mp4":{"stop":{"channel":1}}}"#,
                    Instant::now() + Duration::from_secs(1),
                )
                .is_ok()
        );
        assert_eq!(
            adapter
                .motion_event_start_recording(1, Instant::now() + Duration::from_secs(1))
                .unwrap(),
            MotionEventStart::Owned {
                generation: 1,
                confirmed: true,
            }
        );
        assert!(
            adapter
                .motion_event_stop_recording(1, 1, Instant::now() + Duration::from_secs(1))
                .unwrap()
        );
        server.join().unwrap();
        enabled.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn uncertain_event_stop_resolves_from_fresh_state() {
        let root = task_temp("recorder-event-stop-uncertain");
        let stopped = state(1, Some(false), true, true, None);
        let server = sequence(
            &root,
            1,
            vec![
                (get(), state(1, Some(true), false, true, None)),
                (get(), state(1, Some(true), false, true, None)),
                (set(false), stopped.clone()),
                (get(), br#"{"status":"error"}"#.to_vec()),
                (get(), state(1, None, false, true, Some("stopping"))),
                (get(), stopped),
            ],
        );
        let adapter = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(matches!(
            adapter.motion_event_stop_recording(1, 0, Instant::now() + Duration::from_secs(1)),
            Err(BackendError::PartialApply(_))
        ));
        assert!(matches!(
            adapter.motion_event_stop_recording(1, 0, Instant::now() + Duration::from_secs(1)),
            Err(BackendError::PartialApply(_))
        ));
        assert!(
            !adapter
                .motion_event_stop_recording(1, 0, Instant::now() + Duration::from_secs(1))
                .unwrap()
        );
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
