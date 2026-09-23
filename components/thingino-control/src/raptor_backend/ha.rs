use super::*;
use crate::camera::ha::{Command, DayNightCommand, HaService};
use std::sync::Arc;

impl RaptorBackend {
    #[cfg(test)]
    pub(crate) fn ha_fixture(root: &std::path::Path, address: SocketAddr) -> Self {
        let mut backend = Self::new(
            root.to_path_buf(),
            address,
            root.join("prudynt.sock"),
            root.join("prudynt.pid"),
        )
        .unwrap();
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: root.join("thingino.json"),
            hostname: root.join("hostname"),
            uptime: root.join("uptime"),
            os_release: root.join("os-release"),
            proc_net_wireless: root.join("wireless"),
            wpa_control: root.join("wpa"),
            ..crate::camera::CameraPaths::default()
        });
        backend
    }

    pub fn start_ha(self: &Arc<Self>) {
        self.ha.start_raptor(Arc::clone(self));
    }
    pub fn shutdown_ha(&self) -> io::Result<()> {
        self.ha.shutdown()
    }
    pub(crate) fn ha_paths(&self) -> &crate::camera::CameraPaths {
        self.host.paths()
    }
    pub(crate) fn ha_service(&self) -> &Arc<HaService> {
        &self.ha
    }

    pub(super) fn ha_request(
        &self,
        method: &str,
        target: &str,
        body: &[u8],
    ) -> Option<Result<BackendResponse, BackendError>> {
        if !matches!(
            target,
            "/api/v1/config/ha" | "/api/v1/runtime/ha" | "/api/v1/actions/ha"
        ) {
            return None;
        }
        Some((|| {
            self.ensure_exclusive_owner()?;
            match target {
                "/api/v1/config/ha" => {
                    let _mutation = if method == "POST" {
                        Some(self.lock_mutation()?)
                    } else {
                        None
                    };
                    let response = self.host.ha_config_request(method, body)?;
                    if method == "POST" {
                        self.ha.reconfigure();
                    }
                    Ok(response)
                }
                "/api/v1/runtime/ha" if method == "GET" && body.is_empty() => self.ha.runtime(),
                "/api/v1/actions/ha" if method == "POST" && !body.is_empty() => {
                    self.ha.action(body)
                }
                _ => Err(BackendError::Protocol),
            }
        })())
    }
    pub(crate) fn ha_heartbeat(&self) -> Result<Value, BackendError> {
        let reply = self.heartbeat(Instant::now() + Duration::from_secs(2))?;
        crate::json::parse(&reply.body).map_err(|_| BackendError::Protocol)
    }
    pub(crate) fn ha_jpeg_available(&self) -> bool {
        self.ha_jpeg_ready(Instant::now() + Duration::from_millis(300))
            .unwrap_or(false)
    }

    pub(super) fn ha_jpeg_ready(&self, deadline: Instant) -> Result<bool, BackendError> {
        let media = self.media_state(deadline)?;
        if !media.configured[0]
            || !media.jpeg_requestable[0]
            || !media.streams[0].is_some_and(|stream| stream.available)
        {
            return Ok(false);
        }
        let reader = self.command(RaptorDaemon::Rhd, br#"{"cmd":"status"}"#, deadline)?;
        Ok(validate_rhd_status(&reader)?[0])
    }

    pub(crate) fn ha_motion_active(&self) -> Option<bool> {
        let state = self.motion_lifecycle.snapshot();
        if state.running {
            return state.available.then_some(state.active);
        }
        let reply = self
            .motion_status(Instant::now() + Duration::from_millis(150))
            .ok()?;
        let (available, active) = Self::observed_motion(&reply);
        available.then_some(active)
    }
    pub(crate) fn ha_execute_command(
        &self,
        command: Command,
    ) -> Result<Option<Vec<u8>>, BackendError> {
        self.ensure_exclusive_owner()?;
        let deadline = Instant::now() + Duration::from_secs(3);
        let body = match command {
            Command::MotionGuard(on) => format!(r#"{{"motion":{{"enabled":{on}}}}}"#),
            Command::Privacy(on) => format!(r#"{{"privacy":{{"enabled":{on}}}}}"#),
            Command::IrCut(on) => format!(r#"{{"cmd":"ircut","val":{}}}"#, u8::from(on)),
            Command::Ir850(on) => format!(r#"{{"cmd":"ir850","val":{}}}"#, u8::from(on)),
            Command::Color(on) => format!(r#"{{"cmd":"color","val":{}}}"#, u8::from(on)),
            Command::DayNight(mode) => {
                self.set_daynight_mode(
                    match mode {
                        DayNightCommand::Auto => DayNightMode::Auto,
                        DayNightCommand::Day => DayNightMode::Day,
                        DayNightCommand::Night => DayNightMode::Night,
                    },
                    deadline,
                )?;
                return Ok(None);
            }
            Command::Snapshot => return Err(BackendError::Protocol), // Uses the guarded publication path.
            Command::Reboot => {
                self.host
                    .api_request("POST", "/api/v1/actions/reboot", b"", deadline)
                    .ok_or(BackendError::Unavailable)??;
                return Ok(None);
            }
        };
        self.live_control(body.as_bytes(), deadline)?;
        Ok(None)
    }
    pub(crate) fn ha_publish_snapshot(
        &self,
        publish: impl FnOnce(&[u8]) -> Result<(), BackendError>,
    ) -> Result<(), BackendError> {
        let generation = self.ha_privacy_generation.load(Ordering::Acquire);
        let deadline = Instant::now() + Duration::from_secs(2);
        if self.privacy_state(deadline)? {
            return Err(BackendError::Unavailable);
        }
        // Require the JPEG slot, including idle on-demand capability. The HTTP
        // request supplies demand; only its fresh bounded response may publish.
        if !self.ha_jpeg_ready(deadline)? {
            return Err(BackendError::Unavailable);
        }
        let response = self.http_snapshot_bounded(0, deadline, 256 * 1024)?;
        // HTTP capture does not reserve the Preview mutation lock. Serialize only
        // the final bounded privacy check and MQTT write against new mutations.
        let _guard = self.lock_mutation()?;
        if self.ha_privacy_generation.load(Ordering::Acquire) != generation
            || self.privacy_state(Instant::now() + Duration::from_millis(150))?
        {
            return Err(BackendError::Unavailable);
        }
        publish(&response.body)
    }
}

#[cfg(test)]
mod tests {
    use super::super::motion::tests::sequence;
    use super::super::tests::{backend, serve_daemon, task_temp};
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    const PRIVACY_OFF: &[u8] = br#"{"status":"ok","supported":true,"video":[false,false],"jpeg_required":false,"audio_required":false}"#;
    const PRIVACY_ON: &[u8] = br#"{"status":"ok","supported":true,"video":[true,true],"jpeg_required":false,"audio_required":false}"#;
    const VIDEO_JPEG: &[u8] = br#"{"status":"ok","configured":[true,false],"streams":[{"chn":0,"stream_id":0,"jpeg":false,"available":true,"w":1920,"h":1080,"codec":0,"bitrate":750000,"avg_bitrate":0,"gop":25,"fps":15},{"chn":2,"stream_id":0,"jpeg":true,"available":true,"w":640,"h":360,"codec":2,"bitrate":0,"avg_bitrate":0,"gop":0,"fps":1}]}"#;
    const RHD: &[u8] = br#"{"status":"ok","clients":0,"mjpeg":0,"audio":0,"port":8080,"jpeg_rings":2,"jpeg_available":[true,true],"exif_timestamp":false,"sign_snapshots":false,"privacy":false,"tls":false}"#;
    const MOTION_ON: &[u8] =
        br#"{"status":"ok","supported":true,"active":true,"receiving":true,"motion":true}"#;

    fn idle_jpeg() -> String {
        String::from_utf8(VIDEO_JPEG.to_vec()).unwrap().replace(
            "\"jpeg\":true,\"available\":true",
            "\"jpeg\":true,\"available\":false,\"fps_recovery_required\":false,\"fps_persistence_pending\":false",
        )
    }

    #[test]
    fn idle_jpeg_requires_explicit_identity_confirmed_fps_and_live_video() {
        let idle = idle_jpeg();
        for (video, expected) in [
            (idle.clone(), true),
            (
                idle.replace(
                    ",\"fps_recovery_required\":false,\"fps_persistence_pending\":false",
                    "",
                ),
                false,
            ),
            (
                idle.replace(
                    "\"fps_recovery_required\":false",
                    "\"fps_recovery_required\":true",
                )
                .replace("\"fps\":1}", "\"fps\":null}"),
                false,
            ),
            (
                idle.replace(
                    "\"fps_persistence_pending\":false",
                    "\"fps_persistence_pending\":true",
                )
                .replace("\"fps\":1}", "\"fps\":null}"),
                false,
            ),
            (idle.replace("\"fps_recovery_required\":false,", ""), false),
            (idle.replace("\"stream_id\":0,\"jpeg\":true,", ""), false),
            (
                idle.replace(
                    "\"configured\":[true,false]",
                    "\"configured\":[false,false]",
                ),
                false,
            ),
            (
                idle.replace(
                    "\"jpeg\":false,\"available\":true",
                    "\"jpeg\":false,\"available\":false",
                ),
                false,
            ),
        ] {
            let root = task_temp("idle-jpeg-gate");
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"status"}"#, video.as_bytes());
            let rhd =
                expected.then(|| serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, RHD));
            let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
            assert_eq!(
                backend
                    .ha_jpeg_ready(Instant::now() + Duration::from_secs(1))
                    .unwrap_or(false),
                expected,
                "{video}"
            );
            rvd.join().unwrap();
            if let Some(rhd) = rhd {
                rhd.join().unwrap();
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn idle_jpeg_requires_the_matching_rhd_slot_and_exposes_snapshot_url() {
        for (reader, expected) in [
            (String::from_utf8(RHD.to_vec()).unwrap(), true),
            (
                String::from_utf8(RHD.to_vec())
                    .unwrap()
                    .replace("[true,true]", "[false,true]"),
                false,
            ),
            (
                String::from_utf8(RHD.to_vec())
                    .unwrap()
                    .replace(",\"jpeg_available\":[true,true]", ""),
                false,
            ),
            (
                String::from_utf8(RHD.to_vec())
                    .unwrap()
                    .replace("[true,true]", "[true]"),
                false,
            ),
        ] {
            for view in ["ha", "runtime", "health"] {
                let root = task_temp("idle-jpeg-reader");
                let rvd = serve_daemon(
                    &root,
                    "rvd.sock",
                    br#"{"cmd":"status"}"#,
                    idle_jpeg().as_bytes(),
                );
                let rhd =
                    serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, reader.as_bytes());
                let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
                let deadline = Instant::now() + Duration::from_secs(1);
                if view == "runtime" {
                    let response = backend.media_runtime(deadline).unwrap();
                    let value = crate::json::parse(&response.body).unwrap();
                    assert_eq!(
                        value
                            .get_path("streams.ch0.snapshot_url")
                            .and_then(Value::as_str)
                            .is_some(),
                        expected
                    );
                } else if view == "health" {
                    let healthy = backend.media_health(deadline).ok().is_some_and(|response| {
                        crate::json::parse(&response.body)
                            .unwrap()
                            .get_path("healthy")
                            == Some(&Value::Bool(true))
                    });
                    assert_eq!(healthy, expected);
                } else {
                    assert_eq!(backend.ha_jpeg_ready(deadline).unwrap_or(false), expected);
                }
                rvd.join().unwrap();
                rhd.join().unwrap();
                fs::remove_dir_all(root).unwrap();
            }
        }
    }

    #[test]
    fn ha_routes_preserve_secrets_and_reject_invalid_updates_before_write() {
        let root = task_temp("ha-config");
        let config = root.join("thingino.json");
        fs::write(
            &config,
            br#"{"ha":{"enabled":false,"mqtt":{"host":"","password":"__SET_LOCALLY__"}}}"#,
        )
        .unwrap();
        let mut backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
            thingino_config: config.clone(),
            hostname: root.join("hostname"),
            ..crate::camera::CameraPaths::default()
        });
        let backend = Arc::new(backend);
        backend.start_ha();
        let get = backend
            .ha_request("GET", "/api/v1/config/ha", b"")
            .unwrap()
            .unwrap();
        let json = crate::json::parse(&get.body).unwrap();
        assert_eq!(json.get_path("mqtt.password"), Some(&Value::Null));
        assert_eq!(json.get_path("mqtt.password_set"), Some(&Value::Bool(true)));
        backend
            .ha_request(
                "POST",
                "/api/v1/config/ha",
                br#"{"mqtt":{"password":null},"device_name":"Fixture"}"#,
            )
            .unwrap()
            .unwrap();
        let stored = fs::read(&config).unwrap();
        assert_eq!(
            crate::json::parse(&stored)
                .unwrap()
                .get_path("ha.mqtt.password")
                .and_then(Value::as_str),
            Some("__SET_LOCALLY__")
        );
        for body in [
            br#"{"enabled":true}"#.as_slice(),
            br#"{"mqtt":{"port":0}}"#,
            br#"{"enable_ir940":true}"#,
        ] {
            assert!(
                backend
                    .ha_request("POST", "/api/v1/config/ha", body)
                    .unwrap()
                    .is_err()
            );
            assert_eq!(fs::read(&config).unwrap(), stored);
        }
        assert!(
            backend
                .ha_request("POST", "/api/v1/runtime/ha", b"{}")
                .unwrap()
                .is_err()
        );
        assert!(
            backend
                .ha_request(
                    "POST",
                    "/api/v1/actions/ha",
                    br#"{"action":"publish_state"}"#
                )
                .unwrap()
                .is_err()
        );
        assert!(
            backend
                .ha_request("POST", "/api/v1/actions/ha", br#"{"action":"reboot"}"#)
                .unwrap()
                .is_err()
        );
        backend.shutdown_ha().unwrap();
        let state = crate::json::parse(&backend.ha.runtime().unwrap().body).unwrap();
        assert_eq!(state.get_path("connected"), Some(&Value::Bool(false)));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ha_motion_reads_current_owner_and_never_maps_loss_to_off() {
        for (reply, expected) in [
            (MOTION_ON, Some(true)),
            (br#"{"status":"ok","supported":true,"active":false,"receiving":false,"motion":true}"#, Some(false)),
            (br#"{"status":"ok","supported":true,"active":true,"receiving":false,"motion":true}"#, None),
            (br#"{"status":"ok","supported":true,"active":true,"motion":true}"#, None),
        ] {
            let root = task_temp("ha-motion");
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"ivs-status"}"#, reply);
            let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
            assert_eq!(backend.ha_motion_active(), expected);
            rvd.join().unwrap();
            assert_eq!(backend.ha_motion_active(), None);
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn ha_motion_command_requires_preview_readback() {
        for confirmed in [true, false] {
            let root = task_temp("ha-command");
            let readback = if confirmed {
                MOTION_ON.to_vec()
            } else {
                String::from_utf8(MOTION_ON.to_vec())
                    .unwrap()
                    .replace("\"receiving\":true", "\"receiving\":false")
                    .into_bytes()
            };
            let rvd = sequence(
                &root,
                vec![
                    (br#"{"cmd":"ivs-status"}"#.to_vec(), MOTION_ON.to_vec()),
                    (
                        br#"{"cmd":"ivs-enable","value":true}"#.to_vec(),
                        br#"{"status":"ok","active":true}"#.to_vec(),
                    ),
                    (br#"{"cmd":"ivs-status"}"#.to_vec(), readback),
                ],
            );
            let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
            assert_eq!(
                backend
                    .ha_execute_command(Command::MotionGuard(true))
                    .is_ok(),
                confirmed
            );
            rvd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn ha_snapshot_rejects_privacy_unknown_and_missing_jpeg_without_http() {
        for privacy in [PRIVACY_ON, b"{}"] {
            let root = task_temp("ha-private-jpeg");
            let rvd = serve_daemon(&root, "rvd.sock", br#"{"cmd":"privacy-status"}"#, privacy);
            let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
            assert!(
                backend
                    .ha_publish_snapshot(|_| panic!("private image published"))
                    .is_err()
            );
            rvd.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
        let root = task_temp("ha-no-jpeg");
        let video = String::from_utf8(VIDEO_JPEG.to_vec()).unwrap().replace(
            "\"jpeg\":true,\"available\":true",
            "\"jpeg\":true,\"available\":false",
        );
        let rvd = sequence(
            &root,
            vec![
                (
                    br#"{"cmd":"privacy-status"}"#.to_vec(),
                    PRIVACY_OFF.to_vec(),
                ),
                (br#"{"cmd":"status"}"#.to_vec(), video.into_bytes()),
            ],
        );
        let backend = backend(&root, "127.0.0.1:9".parse().unwrap());
        assert!(
            backend
                .ha_publish_snapshot(|_| panic!("missing image published"))
                .is_err()
        );
        rvd.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ha_snapshot_rechecks_privacy_and_holds_mutation_lock_during_publish() {
        for (idle, after) in [
            (false, PRIVACY_OFF),
            (false, PRIVACY_ON),
            (true, PRIVACY_OFF),
            (true, PRIVACY_ON),
        ] {
            let video = if idle {
                idle_jpeg().into_bytes()
            } else {
                VIDEO_JPEG.to_vec()
            };
            let root = task_temp("ha-jpeg-publish");
            let rvd = sequence(
                &root,
                vec![
                    (
                        br#"{"cmd":"privacy-status"}"#.to_vec(),
                        PRIVACY_OFF.to_vec(),
                    ),
                    (br#"{"cmd":"status"}"#.to_vec(), video),
                    (br#"{"cmd":"privacy-status"}"#.to_vec(), after.to_vec()),
                ],
            );
            let rhd = serve_daemon(&root, "rhd.sock", br#"{"cmd":"status"}"#, RHD);
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let backend = backend(&root, listener.local_addr().unwrap());
            let http = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut request = [0; 1024];
                let count = socket.read(&mut request).unwrap();
                assert!(request[..count].starts_with(b"GET /snap.jpg?stream=0 "));
                socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n\xff\xd8\xff\xd9").unwrap();
            });
            let mut published = false;
            let result = backend.ha_publish_snapshot(|image| {
                assert_eq!(image, b"\xff\xd8\xff\xd9");
                assert!(matches!(backend.lock_mutation(), Err(BackendError::Busy)));
                published = true;
                Ok(())
            });
            assert_eq!(result.is_ok(), after == PRIVACY_OFF);
            assert_eq!(published, after == PRIVACY_OFF);
            rvd.join().unwrap();
            rhd.join().unwrap();
            http.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn ha_snapshot_rejects_large_content_length_before_reading_body() {
        let root = task_temp("ha-jpeg-limit");
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let backend = backend(&root, listener.local_addr().unwrap());
        let http = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut request = [0; 1024];
            assert!(socket.read(&mut request).unwrap() > 0);
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 262145\r\n\r\n").unwrap();
            assert_eq!(socket.read(&mut request).unwrap(), 0);
        });
        assert!(matches!(
            backend.http_snapshot_bounded(0, Instant::now() + Duration::from_secs(1), 256 * 1024),
            Err(BackendError::Protocol)
        ));
        http.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
