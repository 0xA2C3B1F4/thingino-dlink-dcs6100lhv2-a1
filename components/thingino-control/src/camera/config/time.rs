use super::super::*;
use std::time::{SystemTime, UNIX_EPOCH};

const TIMEZONE_CATALOG_LIMIT: u64 = 32 * 1024;
const TIMEZONE_NAME_LIMIT: usize = 128;
const TIMEZONE_DATA_LIMIT: usize = 256;

#[derive(Clone, Debug, Eq, PartialEq)]
struct TimezoneEntry {
    name: String,
    data: String,
}

fn parse_timezone_catalog(bytes: &[u8]) -> Result<Vec<TimezoneEntry>, BackendError> {
    let value = json::parse(bytes).map_err(|_| BackendError::Protocol)?;
    let entries = value.as_array().ok_or(BackendError::Protocol)?;
    if entries.is_empty() || entries.len() > 1024 {
        return Err(BackendError::Protocol);
    }
    let mut catalog = Vec::with_capacity(entries.len());
    for entry in entries {
        let fields = entry.as_object().ok_or(BackendError::Protocol)?;
        let name = fields
            .get("n")
            .and_then(Value::as_str)
            .filter(|value| {
                !value.is_empty()
                    && value.len() <= TIMEZONE_NAME_LIMIT
                    && !value.bytes().any(|byte| byte.is_ascii_control())
            })
            .ok_or(BackendError::Protocol)?;
        let data = fields
            .get("v")
            .and_then(Value::as_str)
            .filter(|value| {
                !value.is_empty()
                    && value.len() <= TIMEZONE_DATA_LIMIT
                    && !value.bytes().any(|byte| byte.is_ascii_control())
            })
            .ok_or(BackendError::Protocol)?;
        if catalog
            .iter()
            .any(|candidate: &TimezoneEntry| candidate.name == name)
        {
            return Err(BackendError::Protocol);
        }
        catalog.push(TimezoneEntry {
            name: name.to_owned(),
            data: data.to_owned(),
        });
    }
    Ok(catalog)
}

fn catalog_for(paths: &CameraPaths) -> Result<Vec<TimezoneEntry>, BackendError> {
    parse_timezone_catalog(&read_bounded(
        &paths.timezone_catalog,
        TIMEZONE_CATALOG_LIMIT,
    )?)
}

fn find_timezone<'a>(catalog: &'a [TimezoneEntry], name: &str) -> Option<&'a TimezoneEntry> {
    catalog.iter().find(|entry| entry.name == name)
}

fn configured_timezone(paths: &CameraPaths, catalog: &[TimezoneEntry]) -> TimezoneEntry {
    if let Some(name) = read_text_value(&paths.timezone, TIMEZONE_NAME_LIMIT as u64)
        && let Some(entry) = find_timezone(catalog, &name)
    {
        return entry.clone();
    }
    // Do not reverse-map /etc/TZ: multiple IANA zones can share the same
    // POSIX rule string.  The canonical name is persisted in /etc/timezone;
    // an invalid or legacy-only installation falls back deterministically.
    find_timezone(catalog, "Etc/GMT")
        .or_else(|| catalog.first())
        .expect("validated timezone catalog is not empty")
        .clone()
}

fn current_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or_default()
}

impl PrudyntBackend {
    pub(in crate::camera) fn time_config(&self) -> Result<BackendResponse, BackendError> {
        let _timezone_guard = self
            .timezone_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        let catalog = catalog_for(&self.paths)?;
        let thingino = json::parse(&read_bounded(&self.paths.thingino_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        let ntp = read_text_value(&self.paths.ntp_config, 4096).unwrap_or_default();
        let mut servers = ntp.lines().filter_map(|line| {
            let mut fields = line.split_ascii_whitespace();
            (fields.next()? == "server").then(|| fields.next().unwrap_or_default().to_owned())
        });
        let server = [
            servers.next().unwrap_or_default(),
            servers.next().unwrap_or_default(),
            servers.next().unwrap_or_default(),
            servers.next().unwrap_or_default(),
        ];
        let timezone = configured_timezone(&self.paths, &catalog);
        let options = Value::Array(
            catalog
                .iter()
                .map(|entry| Value::String(entry.name.clone()))
                .collect(),
        )
        .to_json();
        let body = format!(
            "{{\"timezone\":{},\"current_unix_time\":{},\"timezone_options\":{},\"tz_name\":{},\"tz_data\":{},\"ntp_server_0\":{},\"ntp_server_1\":{},\"ntp_server_2\":{},\"ntp_server_3\":{},\"dhcp_ignore_timezone\":{},\"sync_status_raw_base64\":\"\"}}\n",
            Value::String(timezone.name.clone()).to_json(),
            current_epoch(),
            options,
            // These two fields remain read-only compatibility aliases for
            // old clients. New clients use timezone only and never render or
            // submit tz_data as an editable field.
            Value::String(timezone.name).to_json(),
            Value::String(timezone.data).to_json(),
            Value::String(server[0].clone()).to_json(),
            Value::String(server[1].clone()).to_json(),
            Value::String(server[2].clone()).to_json(),
            Value::String(server[3].clone()).to_json(),
            raw_or(&thingino, "dhcp.ignore_timezone", "false"),
        );
        Ok(BackendResponse::json(body.into_bytes()))
    }

    pub(in crate::camera) fn update_time_config(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        if request.get_path("action").and_then(Value::as_str) != Some("update") {
            return Err(BackendError::Protocol);
        }
        let catalog = catalog_for(&self.paths)?;
        let timezone = request
            .get_path("timezone")
            .and_then(Value::as_str)
            .or_else(|| request.get_path("tz_name").and_then(Value::as_str))
            .filter(|value| !value.is_empty() && value.len() <= TIMEZONE_NAME_LIMIT)
            .and_then(|name| find_timezone(&catalog, name))
            .ok_or(BackendError::Protocol)?;
        let timezone_changed = configured_timezone(&self.paths, &catalog).name != timezone.name;
        if let Some(legacy_name) = request.get_path("tz_name").and_then(Value::as_str)
            && legacy_name != timezone.name
        {
            return Err(BackendError::Protocol);
        }
        if let Some(legacy_data) = request.get_path("tz_data").and_then(Value::as_str)
            && legacy_data != timezone.data
        {
            return Err(BackendError::Protocol);
        }
        let mut ntp = String::new();
        for index in 0..4 {
            if let Some(server) = request
                .get_path(&format!("ntp_server_{index}"))
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                if !safe_server_name(server) {
                    return Err(BackendError::Protocol);
                }
                ntp.push_str("server ");
                ntp.push_str(server);
                ntp.push_str(" iburst\n");
            }
        }
        if ntp.is_empty() {
            return Err(BackendError::Protocol);
        }
        let _timezone_guard = self
            .timezone_lock
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        write_config_file(
            &self.paths.timezone,
            format!("{}\n", timezone.name).as_bytes(),
            0o644,
        )?;
        write_config_file(
            &self.paths.tz,
            format!("{}\n", timezone.data).as_bytes(),
            0o644,
        )?;
        write_config_file(&self.paths.ntp_config, ntp.as_bytes(), 0o444)?;
        if let Some(ignore) = request
            .get_path("dhcp_ignore_timezone")
            .and_then(Value::as_bool)
        {
            let update = object([("ignore_timezone", Value::Bool(ignore))]);
            self.merge_config_domain(
                &self.paths.thingino_config,
                "dhcp",
                update.to_json().as_bytes(),
            )?;
        }
        if timezone_changed {
            // The D-Link Prudynt patch turns the existing restart_thread:7
            // action into a bounded in-place process replacement.  It reads
            // the newly written /etc/TZ and execve's with that value, so the
            // OSD timezone changes without a camera reboot.  Propagate any
            // socket/protocol/timeout failure instead of claiming a live
            // apply that did not happen.
            self.restart_prudynt(deadline)
                .map_err(|error| match error {
                    // The hard-coded action is valid; a protocol error here is
                    // therefore a malformed Prudynt response, not a bad client
                    // request.  Keep the live-apply failure in the upstream error
                    // family instead of returning a misleading 400.
                    BackendError::Protocol => BackendError::Unavailable,
                    error => error,
                })?;
        }
        Ok(BackendResponse::json(
            b"{\"status\":\"ok\",\"message\":\"Time configuration updated\"}\n".to_vec(),
        ))
    }

    pub(in crate::camera) fn sync_time(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let ntp = read_text_value(&self.paths.ntp_config, 4096).ok_or(BackendError::Unavailable)?;
        let server = ntp
            .lines()
            .find_map(|line| {
                let mut fields = line.split_ascii_whitespace();
                (fields.next()? == "server").then(|| fields.next().unwrap_or_default().to_owned())
            })
            .filter(|value| safe_server_name(value))
            .ok_or(BackendError::Unavailable)?;
        let address = (server.as_str(), 123)
            .to_socket_addrs()
            .map_err(|_| BackendError::Connection)?
            .next()
            .ok_or(BackendError::Connection)?;
        let socket = UdpSocket::bind(if address.is_ipv4() {
            "0.0.0.0:0"
        } else {
            "[::]:0"
        })
        .map_err(|_| BackendError::Connection)?;
        let budget = deadline
            .checked_duration_since(Instant::now())
            .ok_or(BackendError::Timeout)?;
        socket
            .set_read_timeout(Some(budget))
            .map_err(|_| BackendError::Connection)?;
        socket
            .set_write_timeout(Some(budget))
            .map_err(|_| BackendError::Connection)?;
        let mut request = [0_u8; 48];
        request[0] = 0x23;
        socket
            .send_to(&request, address)
            .map_err(|_| BackendError::Connection)?;
        let mut response = [0_u8; 48];
        let count = socket.recv(&mut response).map_err(|error| {
            if matches!(
                error.kind(),
                io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
            ) {
                BackendError::Timeout
            } else {
                BackendError::Connection
            }
        })?;
        if count < 48 || response[0] >> 6 == 3 || response[1] == 0 {
            return Err(BackendError::Protocol);
        }
        let ntp_seconds = u32::from_be_bytes(response[40..44].try_into().unwrap());
        let unix_seconds = i64::from(ntp_seconds)
            .checked_sub(2_208_988_800)
            .filter(|value| *value > 1_600_000_000)
            .ok_or(BackendError::Protocol)?;
        let value = Timespec {
            seconds: unix_seconds,
            nanoseconds: 0,
        };
        // SAFETY: value is a valid CLOCK_REALTIME timespec and the process runs as root.
        if unsafe { clock_settime(0, &raw const value) } != 0 {
            return Err(BackendError::Unavailable);
        }
        Ok(BackendResponse::json(
            b"{\"status\":\"ok\",\"message\":\"Time synchronized\"}\n".to_vec(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::{Read, Write};
    use std::os::unix::net::UnixListener;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

    fn task_temp(name: &str) -> PathBuf {
        let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
        let path = PathBuf::from(root).join(format!(
            "thingino-control-time-{}-{}-{name}",
            std::process::id(),
            TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn catalog_accepts_the_camera_helsinki_mapping() {
        let catalog = parse_timezone_catalog(
            br#"[{"n":"Etc/GMT","v":"GMT0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#,
        )
        .unwrap();
        assert_eq!(
            find_timezone(&catalog, "Europe/Helsinki").map(|entry| entry.data.as_str()),
            Some("EET-2EEST,M3.5.0/3,M10.5.0/4")
        );
    }

    #[test]
    fn catalog_rejects_duplicates_and_malformed_values() {
        assert!(
            parse_timezone_catalog(
                br#"[{"n":"Europe/Helsinki","v":"EET0"},{"n":"Europe/Helsinki","v":"EET0"}]"#,
            )
            .is_err()
        );
        assert!(parse_timezone_catalog(br#"[{"n":"","v":"GMT0"}]"#).is_err());
        assert!(parse_timezone_catalog(br#"[{"n":"Etc/GMT","v":"GMT0\n"}]"#).is_err());
    }

    #[test]
    fn catalog_enforces_entry_count_and_byte_boundaries() {
        let boundary = format!(
            r#"[{{"n":"{}","v":"{}"}}]"#,
            "n".repeat(TIMEZONE_NAME_LIMIT),
            "v".repeat(TIMEZONE_DATA_LIMIT)
        );
        assert!(parse_timezone_catalog(boundary.as_bytes()).is_ok());
        let long_name = format!(
            r#"[{{"n":"{}","v":"GMT0"}}]"#,
            "n".repeat(TIMEZONE_NAME_LIMIT + 1)
        );
        let long_data = format!(
            r#"[{{"n":"Etc/GMT","v":"{}"}}]"#,
            "v".repeat(TIMEZONE_DATA_LIMIT + 1)
        );
        assert!(parse_timezone_catalog(long_name.as_bytes()).is_err());
        assert!(parse_timezone_catalog(long_data.as_bytes()).is_err());
        assert!(parse_timezone_catalog(b"[]").is_err());

        let entries = (0..1024)
            .map(|index| format!(r#"{{"n":"{index}","v":"x"}}"#))
            .collect::<Vec<_>>();
        assert!(parse_timezone_catalog(format!("[{}]", entries.join(",")).as_bytes()).is_ok());
        let mut over_limit = entries;
        over_limit.push(r#"{"n":"Zone/1024","v":"GMT1024"}"#.to_owned());
        assert!(parse_timezone_catalog(format!("[{}]", over_limit.join(",")).as_bytes()).is_err());
    }

    #[test]
    fn current_unix_time_is_reasonable() {
        assert!(current_epoch() > 1_600_000_000);
    }

    #[test]
    fn get_exposes_catalog_options_and_post_maps_helsinki_without_posix_input() {
        let root = task_temp("get-post");
        let timezone = root.join("timezone");
        let tz = root.join("TZ");
        let catalog = root.join("tz.json");
        let ntp = root.join("ntp.conf");
        let thingino = root.join("thingino.json");
        let prudynt_socket = root.join("prudynt.sock");
        fs::write(&timezone, b"Etc/GMT\n").unwrap();
        fs::write(&tz, b"GMT0\n").unwrap();
        fs::write(
            &catalog,
            br#"[{"n":"Etc/GMT","v":"GMT0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#,
        )
        .unwrap();
        fs::write(&ntp, b"server pool.ntp.org iburst\n").unwrap();
        fs::write(&thingino, br#"{"dhcp":{"ignore_timezone":false}}"#).unwrap();
        let listener = UnixListener::bind(&prudynt_socket).unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut command = Vec::new();
            stream.read_to_end(&mut command).unwrap();
            assert_eq!(
                command,
                crate::camera::prudynt::framed_prudynt_json(br#"{"action":{"restart_thread":7}}"#)
            );
            stream.write_all(b"{\"code\":200}\n").unwrap();
        });
        let backend = PrudyntBackend::new(CameraPaths {
            timezone: timezone.clone(),
            tz: tz.clone(),
            timezone_catalog: catalog,
            ntp_config: ntp.clone(),
            thingino_config: thingino.clone(),
            prudynt_socket,
            ..CameraPaths::default()
        });
        let before = json::parse(&backend.time_config().unwrap().body).unwrap();
        assert_eq!(
            before.get_path("timezone").and_then(Value::as_str),
            Some("Etc/GMT")
        );
        assert_eq!(
            before
                .get_path("timezone_options")
                .and_then(Value::as_array)
                .map(|values| values.len()),
            Some(2)
        );
        let current_unix_time =
            before
                .get_path("current_unix_time")
                .and_then(|value| match value {
                    Value::Number(number) => number.parse::<u64>().ok(),
                    _ => None,
                });
        assert!(current_unix_time.is_some_and(|value| value > 1_600_000_000));
        backend
            .update_time_config(
                br#"{"action":"update","timezone":"Europe/Helsinki","ntp_server_0":"pool.ntp.org"}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();
        let after = json::parse(&backend.time_config().unwrap().body).unwrap();
        assert_eq!(
            after.get_path("timezone").and_then(Value::as_str),
            Some("Europe/Helsinki")
        );
        assert_eq!(
            fs::read_to_string(root.join("TZ")).unwrap(),
            "EET-2EEST,M3.5.0/3,M10.5.0/4\n"
        );
        server.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn post_rejects_unknown_iana_and_mismatched_legacy_posix_values() {
        let root = task_temp("reject");
        let timezone = root.join("timezone");
        let tz = root.join("TZ");
        let catalog = root.join("tz.json");
        let ntp = root.join("ntp.conf");
        let thingino = root.join("thingino.json");
        fs::write(&timezone, b"Etc/GMT\n").unwrap();
        fs::write(&tz, b"GMT0\n").unwrap();
        fs::write(
            &catalog,
            br#"[{"n":"Etc/GMT","v":"GMT0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#,
        )
        .unwrap();
        fs::write(&ntp, b"server pool.ntp.org iburst\n").unwrap();
        fs::write(&thingino, br#"{"dhcp":{}}"#).unwrap();
        let backend = PrudyntBackend::new(CameraPaths {
            timezone: timezone.clone(),
            tz: tz.clone(),
            timezone_catalog: catalog,
            ntp_config: ntp.clone(),
            thingino_config: thingino.clone(),
            ..CameraPaths::default()
        });
        let original_timezone = fs::read(&timezone).unwrap();
        let original_tz = fs::read(&tz).unwrap();
        let original_ntp = fs::read(&ntp).unwrap();
        let original_thingino = fs::read(&thingino).unwrap();
        assert!(matches!(
            backend.update_time_config(
                br#"{"action":"update","timezone":"Europe/NotARealZone","ntp_server_0":"pool.ntp.org"}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Protocol)
        ));
        assert!(matches!(
            backend.update_time_config(
                br#"{"action":"update","tz_name":"Europe/Helsinki","tz_data":"GMT0","ntp_server_0":"pool.ntp.org"}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Protocol)
        ));
        assert_eq!(fs::read(&timezone).unwrap(), original_timezone);
        assert_eq!(fs::read(&tz).unwrap(), original_tz);
        assert_eq!(fs::read(&ntp).unwrap(), original_ntp);
        assert_eq!(fs::read(&thingino).unwrap(), original_thingino);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unchanged_timezone_does_not_contact_prudynt() {
        let root = task_temp("unchanged");
        let timezone = root.join("timezone");
        let tz = root.join("TZ");
        let catalog = root.join("tz.json");
        let ntp = root.join("ntp.conf");
        let thingino = root.join("thingino.json");
        fs::write(&timezone, b"Europe/Helsinki\n").unwrap();
        fs::write(&tz, b"EET-2EEST,M3.5.0/3,M10.5.0/4\n").unwrap();
        fs::write(
            &catalog,
            br#"[{"n":"Etc/GMT","v":"GMT0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#,
        )
        .unwrap();
        fs::write(&ntp, b"server pool.ntp.org iburst\n").unwrap();
        fs::write(&thingino, br#"{"dhcp":{}}"#).unwrap();
        let backend = PrudyntBackend::new(CameraPaths {
            timezone,
            tz,
            timezone_catalog: catalog,
            ntp_config: ntp,
            thingino_config: thingino,
            prudynt_socket: root.join("no-prudynt.sock"),
            ..CameraPaths::default()
        });
        backend
            .update_time_config(
                br#"{"action":"update","timezone":"Europe/Helsinki","ntp_server_0":"pool.ntp.org"}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn changed_timezone_reports_live_reload_failure_after_persisting_files() {
        let root = task_temp("reload-failure");
        let timezone = root.join("timezone");
        let tz = root.join("TZ");
        let catalog = root.join("tz.json");
        let ntp = root.join("ntp.conf");
        let thingino = root.join("thingino.json");
        fs::write(&timezone, b"Etc/GMT\n").unwrap();
        fs::write(&tz, b"GMT0\n").unwrap();
        fs::write(
            &catalog,
            br#"[{"n":"Etc/GMT","v":"GMT0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#,
        )
        .unwrap();
        fs::write(&ntp, b"server pool.ntp.org iburst\n").unwrap();
        fs::write(&thingino, br#"{"dhcp":{}}"#).unwrap();
        let backend = PrudyntBackend::new(CameraPaths {
            timezone: timezone.clone(),
            tz: tz.clone(),
            timezone_catalog: catalog,
            ntp_config: ntp,
            thingino_config: thingino,
            prudynt_socket: root.join("no-prudynt.sock"),
            ..CameraPaths::default()
        });
        assert!(matches!(
            backend.update_time_config(
                br#"{"action":"update","timezone":"Europe/Helsinki","ntp_server_0":"pool.ntp.org"}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Connection | BackendError::Unavailable | BackendError::Timeout)
        ));
        assert_eq!(fs::read_to_string(timezone).unwrap(), "Europe/Helsinki\n");
        assert_eq!(
            fs::read_to_string(tz).unwrap(),
            "EET-2EEST,M3.5.0/3,M10.5.0/4\n"
        );
        fs::remove_dir_all(root).unwrap();
    }
}
