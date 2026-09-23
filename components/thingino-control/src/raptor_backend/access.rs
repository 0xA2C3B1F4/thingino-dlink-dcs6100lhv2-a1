use super::*;
use std::collections::BTreeMap;

const ACCESS_FIELDS: &[&str] = &[
    "status",
    "port",
    "auth_enabled",
    "username",
    "password_set",
    "main",
    "sub",
    "mic",
    "tls",
];
const ACCESS_KEYS: [(&str, &str, &str); 5] = [
    ("username", "username", "username"),
    ("password", "password", "password"),
    ("rtsp_port", "port", "port"),
    ("rtsp_ch0", "main", "endpoint_main"),
    ("rtsp_ch1", "sub", "endpoint_sub"),
];

pub(super) fn endpoint_valid(value: &str, id: usize) -> bool {
    if value == if id == 0 { "stream0" } else { "stream1" } {
        return true;
    }
    !value.is_empty()
        && value.len() < 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_.-".contains(&byte))
        && ![
            "main",
            "sub",
            "jpeg",
            "jpeg_sub",
            "audio",
            "backchannel",
            ".",
            "..",
        ]
        .contains(&value)
        && !value
            .strip_prefix("stream")
            .is_some_and(|tail| !tail.is_empty() && tail.bytes().all(|byte| byte.is_ascii_digit()))
}

fn ini_text(value: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&value.len())
        && !value.starts_with(' ')
        && !value.ends_with(' ')
        && !value.contains(" #")
        && value.bytes().all(|byte| (32..=126).contains(&byte))
}

impl RaptorBackend {
    fn access_observation(&self, deadline: Instant) -> Result<Value, BackendError> {
        self.raw_access_observation(deadline)
            .map_err(|error| match error {
                BackendError::Protocol => BackendError::Upstream(502),
                other => other,
            })
    }

    fn raw_access_observation(&self, deadline: Instant) -> Result<Value, BackendError> {
        let reply = self.command(RaptorDaemon::Rsd, br#"{"cmd":"access-status"}"#, deadline)?;
        require_ok(&reply)?;
        reply.require_only_fields(ACCESS_FIELDS)?;
        let object = reply.value.as_object().ok_or(BackendError::Upstream(502))?;
        if object.len() != ACCESS_FIELDS.len() {
            return Err(BackendError::Upstream(502));
        }
        let port = value_u64(object.get("port").ok_or(BackendError::Upstream(502))?)?;
        let username = object
            .get("username")
            .and_then(Value::as_str)
            .ok_or(BackendError::Upstream(502))?;
        let authenticated = object
            .get("auth_enabled")
            .and_then(Value::as_bool)
            .ok_or(BackendError::Upstream(502))?;
        if !(1..=65535).contains(&port)
            || object.get("tls").and_then(Value::as_bool) != Some(false)
            || object.get("password_set").and_then(Value::as_bool) != Some(authenticated)
            || (authenticated && !ini_text(username, 1, 127))
            || (!authenticated && !username.is_empty())
            || object.get("mic").and_then(Value::as_str) != Some("audio")
        {
            return Err(BackendError::Upstream(502));
        }
        for (id, name) in ["main", "sub"].iter().enumerate() {
            if !object
                .get(*name)
                .and_then(Value::as_str)
                .is_some_and(|value| endpoint_valid(value, id))
            {
                return Err(BackendError::Upstream(502));
            }
        }
        if object.get("main") == object.get("sub") {
            return Err(BackendError::Upstream(502));
        }
        Ok(reply.value)
    }

    pub(super) fn access_config(&self, deadline: Instant) -> Result<BackendResponse, BackendError> {
        let observed = self.access_observation(deadline)?;
        let saved = self
            .saved_access_paths(deadline.min(Instant::now() + Duration::from_millis(500)))
            .unwrap_or([None, None]);
        let mut result = BTreeMap::new();
        for (public, daemon) in [
            ("username", "username"),
            ("password_set", "password_set"),
            ("rtsp_port", "port"),
            ("rtsp_ch0", "main"),
            ("rtsp_ch1", "sub"),
            ("rtsp_mic", "mic"),
            ("auth_enabled", "auth_enabled"),
        ] {
            result.insert(public.into(), observed.get_path(daemon).unwrap().clone());
        }
        for key in ["password", "onvif_port", "onvif_enabled", "onvif_ingress"] {
            result.insert(key.into(), Value::Null);
        }
        result.insert("source".into(), Value::String("raptor".into()));
        let mut paths_match = true;
        for (id, name) in ["main", "sub"].iter().enumerate() {
            paths_match &= saved[id]
                .as_deref()
                .is_some_and(|path| observed.get_path(name).and_then(Value::as_str) == Some(path));
            result.insert(
                format!("saved_rtsp_ch{id}"),
                saved[id].clone().map_or(Value::Null, Value::String),
            );
        }
        result.insert("rtsp_paths_match_saved".into(), Value::Bool(paths_match));
        Ok(BackendResponse::json(
            Value::Object(result).to_json().into_bytes(),
        ))
    }

    fn saved_access_paths(&self, deadline: Instant) -> Result<[Option<String>; 2], BackendError> {
        let reply = self.command(
            RaptorDaemon::Rsd,
            br#"{"cmd":"config-read-section","section":"rtsp"}"#,
            deadline,
        )?;
        require_ok(&reply)?;
        reply.require_only_fields(&["status", "section", "keys"])?;
        if reply.value.get_path("section").and_then(Value::as_str) != Some("rtsp") {
            return Err(BackendError::Upstream(502));
        }
        let keys = reply
            .value
            .get_path("keys")
            .and_then(Value::as_object)
            .ok_or(BackendError::Upstream(502))?;
        let mut paths = [None, None];
        for (id, key) in ["endpoint_main", "endpoint_sub"].iter().enumerate() {
            let saved = match keys.get(*key) {
                None => "",
                Some(Value::String(value)) => value,
                _ => continue,
            };
            let path = if saved.is_empty() {
                format!("stream{id}")
            } else {
                saved.to_owned()
            };
            if endpoint_valid(&path, id) {
                paths[id] = Some(path);
            }
        }
        Ok(paths)
    }

    pub(super) fn update_access(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = crate::json::parse(body).map_err(|_| BackendError::Protocol)?;
        let fields = request
            .as_object()
            .filter(|fields| !fields.is_empty())
            .ok_or(BackendError::Protocol)?;
        let mut command = BTreeMap::new();
        command.insert("cmd".into(), Value::String("access-apply".into()));
        let mut expected_disk = Vec::new();
        for (name, value) in fields {
            let (_, daemon, disk) =
                ACCESS_KEYS
                    .iter()
                    .find(|key| key.0 == name)
                    .ok_or(BackendError::Unsupported(
                        "This access setting is not mapped to RSD",
                    ))?;
            let valid = match name.as_str() {
                "rtsp_port" => value_u64(value).is_ok_and(|port| {
                    (1..=65535).contains(&port) && ![80, 443, 8080, 8555].contains(&port)
                }),
                "username" => value
                    .as_str()
                    .is_some_and(|value| ini_text(value, 1, 64) && !value.contains(' ')),
                "password" => value.as_str().is_some_and(|value| ini_text(value, 10, 127)),
                "rtsp_ch0" => value.as_str().is_some_and(|value| endpoint_valid(value, 0)),
                "rtsp_ch1" => value.as_str().is_some_and(|value| endpoint_valid(value, 1)),
                _ => false,
            };
            if !valid {
                return Err(BackendError::Protocol);
            }
            command.insert((*daemon).into(), value.clone());
            let text = match value {
                Value::String(text) => text.clone(),
                Value::Number(text) => text.clone(),
                _ => return Err(BackendError::Protocol),
            };
            let text = if (name == "rtsp_ch0" && text == "stream0")
                || (name == "rtsp_ch1" && text == "stream1")
            {
                String::new()
            } else {
                text
            };
            expected_disk.push((*disk, text));
        }
        let _mutation = self.lock_mutation()?;
        let before = self.access_observation(deadline)?;
        let main = fields.get("rtsp_ch0").or_else(|| before.get_path("main"));
        let sub = fields.get("rtsp_ch1").or_else(|| before.get_path("sub"));
        if main == sub
            || (before.get_path("auth_enabled") == Some(&Value::Bool(false))
                && (!fields.contains_key("username") || !fields.contains_key("password")))
        {
            return Err(BackendError::Protocol);
        }
        let apply = || -> Result<(), BackendError> {
            require_ok(&self.command(
                RaptorDaemon::Rsd,
                Value::Object(command).to_json().as_bytes(),
                deadline,
            )?)?;
            let live = self.access_observation(deadline)?;
            if live.get_path("auth_enabled") != Some(&Value::Bool(true)) {
                return Err(BackendError::Upstream(502));
            }
            for (public, daemon, _) in ACCESS_KEYS {
                if public != "password"
                    && let Some(expected) = fields.get(public)
                    && live.get_path(daemon) != Some(expected)
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            require_ok(&self.command(RaptorDaemon::Rsd, br#"{"cmd":"config-save"}"#, deadline)?)?;
            let disk = self.command(
                RaptorDaemon::Rsd,
                br#"{"cmd":"config-read-section","section":"rtsp"}"#,
                deadline,
            )?;
            require_ok(&disk)?;
            disk.require_only_fields(&["status", "section", "keys"])?;
            if disk.value.get_path("section").and_then(Value::as_str) != Some("rtsp") {
                return Err(BackendError::Upstream(502));
            }
            for (key, expected) in expected_disk {
                if disk
                    .value
                    .get_path(&format!("keys.{key}"))
                    .and_then(Value::as_str)
                    != Some(expected.as_str())
                {
                    return Err(BackendError::Upstream(502));
                }
            }
            Ok(())
        };
        apply().map_err(|_| BackendError::PartialApply("RTSP access may have changed, but apply, save or readback was incomplete. Reload, re-enter a changed password, and explicitly retry saving."))?;
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

    fn observation(port: u16, user: &str, main: &str) -> Vec<u8> {
        format!(r#"{{"status":"ok","port":{port},"auth_enabled":true,"username":"{user}","password_set":true,"main":"{main}","sub":"stream1","mic":"audio","tls":false}}"#).into_bytes()
    }

    fn sequence(
        root: &std::path::Path,
        replies: Vec<(&'static [u8], Vec<u8>)>,
    ) -> thread::JoinHandle<()> {
        let listener = UnixListener::bind(root.join("rsd.sock")).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            for (expected, reply) in replies {
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error)
                            if error.kind() == io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            thread::sleep(Duration::from_millis(2));
                        }
                        other => panic!("missing expected RSD request: {other:?}"),
                    }
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                assert_eq!(read_request(&mut socket), expected);
                socket.write_all(&framed(&reply)).unwrap();
            }
        })
    }

    #[test]
    fn access_retries_identical_live_values_after_save_or_disk_readback_failure() {
        const STATUS: &[u8] = br#"{"cmd":"access-status"}"#;
        const APPLY: &[u8] = br#"{"cmd":"access-apply","main":"front-door","password":"__SET_LOCALLY__","port":9554,"username":"viewer"}"#;
        const SAVE: &[u8] = br#"{"cmd":"config-save"}"#;
        const READ: &[u8] = br#"{"cmd":"config-read-section","section":"rtsp"}"#;
        const UPDATE: &[u8] = br#"{"username":"viewer","password":"__SET_LOCALLY__","rtsp_port":9554,"rtsp_ch0":"front-door"}"#;
        for fault in ["save", "empty", "wrong-password"] {
            let root = task_temp("rsd-retry");
            let ok = br#"{"status":"ok"}"#.to_vec();
            let disk = br#"{"status":"ok","section":"rtsp","keys":{"username":"viewer","password":"__SET_LOCALLY__","port":"9554","endpoint_main":"front-door"}}"#.to_vec();
            let mut replies = vec![
                (STATUS, observation(8554, "old-viewer", "stream0")),
                (APPLY, ok.clone()),
                (STATUS, observation(9554, "viewer", "front-door")),
            ];
            if fault == "save" {
                replies.push((SAVE, br#"{"status":"error"}"#.to_vec()));
            } else {
                replies.push((SAVE, ok.clone()));
                replies.push((
                    READ,
                    if fault == "empty" {
                        br#"{"status":"ok","section":"rtsp","keys":{}}"#.to_vec()
                    } else {
                        String::from_utf8(disk.clone())
                            .unwrap()
                            .replace("__SET_LOCALLY__", "__SET_LOCALLY__-wrong")
                            .into_bytes()
                    },
                ));
            }
            replies.extend([
                (STATUS, observation(9554, "viewer", "front-door")),
                (APPLY, ok.clone()),
                (STATUS, observation(9554, "viewer", "front-door")),
                (SAVE, ok),
                (READ, disk),
            ]);
            let peer = sequence(&root, replies);
            let backend = backend(&root, "127.0.0.1:8080".parse().unwrap());
            assert!(matches!(
                backend.update_access(UPDATE, Instant::now() + Duration::from_secs(2)),
                Err(BackendError::PartialApply(_))
            ));
            let response = backend
                .update_access(UPDATE, Instant::now() + Duration::from_secs(2))
                .unwrap();
            assert_eq!(response.body, br#"{"status":"accepted","persistent":true}"#);
            peer.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn invalid_access_fields_fail_before_any_daemon_request() {
        let root = task_temp("rsd-invalid");
        let backend = backend(&root, "127.0.0.1:8080".parse().unwrap());
        for body in [
            br#"{"rtsp_port":8080}"#.as_slice(),
            br#"{"rtsp_port":8555}"#,
            br#"{"rtsp_port":1.5}"#,
            br#"{"username":"viewer","password":""}"#,
            br#"{"rtsp_ch0":"audio"}"#,
            br#"{"rtsp_ch1":"stream0"}"#,
        ] {
            assert!(matches!(
                backend.update_access(body, Instant::now() + Duration::from_secs(1)),
                Err(BackendError::Protocol)
            ));
        }
        assert!(matches!(
            backend.update_access(
                br#"{"username":"viewer","onvif_enabled":true}"#,
                Instant::now() + Duration::from_secs(1)
            ),
            Err(BackendError::Unsupported(_))
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn access_get_redacts_password_and_leaves_onvif_unknown() {
        let root = task_temp("rsd-redacted");
        let peer = sequence(
            &root,
            vec![(
                br#"{"cmd":"access-status"}"#,
                observation(9554, "viewer", "front-door"),
            )],
        );
        let backend = backend(&root, "127.0.0.1:8080".parse().unwrap());
        let response = backend
            .access_config(Instant::now() + Duration::from_secs(2))
            .unwrap();
        let body = crate::json::parse(&response.body).unwrap();
        assert_eq!(body.get_path("password"), Some(&Value::Null));
        assert_eq!(body.get_path("onvif_enabled"), Some(&Value::Null));
        assert_eq!(
            body.get_path("rtsp_port"),
            Some(&Value::Number("9554".into()))
        );
        peer.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn access_paths_compare_live_names_with_independent_saved_aliases() {
        for (keys, main, matches) in [
            (
                r#"{"endpoint_main":"front-door","endpoint_sub":"","password":"__SET_LOCALLY__"}"#,
                Some("front-door"),
                true,
            ),
            (r#"{}"#, Some("stream0"), false),
            (
                r#"{"endpoint_main":"older","endpoint_sub":""}"#,
                Some("older"),
                false,
            ),
            (r#"{"endpoint_main":true,"endpoint_sub":""}"#, None, false),
            (
                r#"{"endpoint_main":"audio","endpoint_sub":""}"#,
                None,
                false,
            ),
        ] {
            let root = task_temp("rsd-saved-paths");
            let peer = sequence(
                &root,
                vec![
                    (
                        br#"{"cmd":"access-status"}"#,
                        observation(9554, "viewer", "front-door"),
                    ),
                    (
                        br#"{"cmd":"config-read-section","section":"rtsp"}"#,
                        format!(r#"{{"status":"ok","section":"rtsp","keys":{keys}}}"#).into_bytes(),
                    ),
                ],
            );
            let response = backend(&root, "127.0.0.1:8080".parse().unwrap())
                .access_config(Instant::now() + Duration::from_secs(2))
                .unwrap();
            let body = crate::json::parse(&response.body).unwrap();
            assert_eq!(
                body.get_path("saved_rtsp_ch0").and_then(Value::as_str),
                main
            );
            assert_eq!(
                body.get_path("saved_rtsp_ch1").and_then(Value::as_str),
                Some("stream1")
            );
            assert_eq!(
                body.get_path("rtsp_paths_match_saved"),
                Some(&Value::Bool(matches))
            );
            assert_eq!(body.get_path("password"), Some(&Value::Null));
            assert!(
                !String::from_utf8(response.body)
                    .unwrap()
                    .contains("__SET_LOCALLY__")
            );
            peer.join().unwrap();
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn stream_path_only_update_preserves_viewer_identity_and_listener_port() {
        let root = task_temp("rsd-path-only");
        let peer = sequence(
            &root,
            vec![
                (
                    br#"{"cmd":"access-status"}"#,
                    observation(9554, "viewer", "stream0"),
                ),
                (
                    br#"{"cmd":"access-apply","main":"front-door"}"#,
                    br#"{"status":"ok"}"#.to_vec(),
                ),
                (
                    br#"{"cmd":"access-status"}"#,
                    observation(9554, "viewer", "front-door"),
                ),
                (br#"{"cmd":"config-save"}"#, br#"{"status":"ok"}"#.to_vec()),
                (
                    br#"{"cmd":"config-read-section","section":"rtsp"}"#,
                    br#"{"status":"ok","section":"rtsp","keys":{"endpoint_main":"front-door"}}"#
                        .to_vec(),
                ),
            ],
        );
        let response = backend(&root, "127.0.0.1:8080".parse().unwrap())
            .update_access(
                br#"{"rtsp_ch0":"front-door"}"#,
                Instant::now() + Duration::from_secs(2),
            )
            .unwrap();
        assert_eq!(response.body, br#"{"status":"accepted","persistent":true}"#);
        peer.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
