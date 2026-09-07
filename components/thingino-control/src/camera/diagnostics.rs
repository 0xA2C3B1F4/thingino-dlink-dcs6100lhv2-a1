use super::*;

impl PrudyntBackend {
    pub(super) fn diagnostics_info(&self, target: &str) -> Result<BackendResponse, BackendError> {
        let section = target
            .split_once('?')
            .map(|(_, value)| url_decode(value))
            .transpose()?
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "system".to_owned());
        let mut entries = Vec::new();
        let mut add_output = |label: &str, output: Vec<u8>| {
            entries.push(object([
                ("command", Value::String(label.to_owned())),
                ("output_base64", Value::String(base64_encode(&output))),
            ]));
        };
        let mut add = |label: &str, path: &Path, redact_json: bool| {
            let output = read_bounded(path, FILE_LIMIT)
                .and_then(|bytes| {
                    if !redact_json {
                        return Ok(bytes);
                    }
                    let mut value = json::parse(&bytes).map_err(|_| BackendError::Protocol)?;
                    mask_secret_fields(&mut value);
                    let mut output = value.to_json().into_bytes();
                    output.push(b'\n');
                    Ok(output)
                })
                .unwrap_or_else(|_| format!("{label}: unavailable\n").into_bytes());
            add_output(label, output);
        };
        match section.as_str() {
            "crontab" => add("cat /etc/cron/crontabs/root", &self.paths.crontab, false),
            "onvif" => add("cat /etc/onvif.json", &self.paths.onvif_config, true),
            "prudynt" => add("cat /etc/prudynt.json", &self.paths.prudynt_config, true),
            "thingino" => add("cat /etc/thingino.json", &self.paths.thingino_config, true),
            "logread" => {
                let output = read_system_log(&self.paths.var_log_messages)
                    .unwrap_or_else(|_| b"system log: unavailable\n".to_vec());
                add_output("system log", output);
            }
            "dmesg" => {
                let output =
                    read_kernel_log().unwrap_or_else(|_| b"kernel log: unavailable\n".to_vec());
                add_output("kernel log", output);
            }
            "logcat" => {
                let output = read_streamer_log(&self.paths.log_main)
                    .unwrap_or_else(|_| b"streamer log: unavailable\n".to_vec());
                add_output("streamer log", output);
            }
            "lsmod" => add("cat /proc/modules", &self.paths.proc_modules, false),
            "netstat" => {
                let output = format_socket_tables(
                    &read_bounded(&self.paths.proc_tcp, FILE_LIMIT).unwrap_or_default(),
                    &read_bounded(&self.paths.proc_udp, FILE_LIMIT).unwrap_or_default(),
                    &read_bounded(&self.paths.proc_unix, FILE_LIMIT).unwrap_or_default(),
                )
                .unwrap_or_else(|_| b"network sockets: unavailable\n".to_vec());
                add_output("network sockets", output);
            }
            "release" => add("cat /etc/os-release", &self.paths.os_release, false),
            "top" => {
                let output = process_snapshot(&self.paths.proc_root)
                    .unwrap_or_else(|_| b"process snapshot: unavailable\n".to_vec());
                add_output("process snapshot", output);
            }
            "status" | "system" => {
                add("cat /proc/uptime", &self.paths.uptime, false);
                add("cat /proc/loadavg", &self.paths.loadavg, false);
                add("cat /proc/meminfo", &self.paths.meminfo, false);
            }
            _ => return Err(BackendError::Protocol),
        }
        json_response(object([
            ("commands", Value::Array(entries)),
            ("extras_html_base64", Value::String(String::new())),
        ]))
    }
    pub(super) fn diagnostics(&self, body: &[u8]) -> Result<BackendResponse, BackendError> {
        let mut sections = vec![
            "release".to_owned(),
            "system".to_owned(),
            "modules".to_owned(),
            "network".to_owned(),
        ];
        if body
            .iter()
            .copied()
            .find(|byte| !byte.is_ascii_whitespace())
            == Some(b'{')
        {
            let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
            let request = request.as_object().ok_or(BackendError::Protocol)?;
            if request.keys().any(|key| key != "sections") {
                return Err(BackendError::Protocol);
            }
            if let Some(values) = request.get("sections") {
                sections = values
                    .as_array()
                    .ok_or(BackendError::Protocol)?
                    .iter()
                    .map(|value| {
                        value
                            .as_str()
                            .map(str::to_owned)
                            .ok_or(BackendError::Protocol)
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                if sections.is_empty()
                    || sections.iter().any(|value| {
                        !matches!(value.as_str(), "release" | "system" | "modules" | "network")
                    })
                {
                    return Err(BackendError::Protocol);
                }
            }
        } else {
            let form = parse_form(body)?;
            if form_bool(&form, "direct_upload") || form.contains_key("upload_data") {
                return Err(BackendError::Unavailable);
            }
        }
        let mut report = Vec::new();
        for section in sections {
            let entries: &[(&str, &Path)] = match section.as_str() {
                "release" => &[("os-release", &self.paths.os_release)],
                "system" => &[
                    ("uptime", &self.paths.uptime),
                    ("loadavg", &self.paths.loadavg),
                    ("meminfo", &self.paths.meminfo),
                ],
                "modules" => &[("modules", &self.paths.proc_modules)],
                "network" => &[("tcp", &self.paths.proc_tcp), ("udp", &self.paths.proc_udp)],
                _ => return Err(BackendError::Protocol),
            };
            for (heading, path) in entries {
                report.extend_from_slice(format!("\n## {heading}\n").as_bytes());
                match read_bounded(path, 16 * 1024) {
                    Ok(bytes) => report.extend_from_slice(&bytes),
                    Err(_) => report.extend_from_slice(b"unavailable\n"),
                }
                if report.len() > 56 * 1024 {
                    break;
                }
            }
            if report.len() > 56 * 1024 {
                break;
            }
        }
        json_response(object([(
            "output_b64",
            Value::String(base64_encode(&report)),
        )]))
    }
}

pub(super) fn process_snapshot(root: &Path) -> Result<Vec<u8>, BackendError> {
    let mut pids = fs::read_dir(root)
        .map_err(|_| BackendError::Unavailable)?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let pid = entry.file_name().to_string_lossy().parse::<u32>().ok()?;
            Some((pid, entry.path()))
        })
        .collect::<Vec<_>>();
    pids.sort_unstable_by_key(|(pid, _)| *pid);
    let mut output = String::from("PID    STATE RSS_KIB  THREADS COMMAND\n");
    for (pid, path) in pids.into_iter().take(512) {
        let status = match read_virtual_bounded(&path.join("status"), 32 * 1024) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let status = std::str::from_utf8(&status).map_err(|_| BackendError::Protocol)?;
        let field = |name: &str| {
            status
                .lines()
                .find_map(|line| line.strip_prefix(name))
                .map(str::trim)
                .unwrap_or("-")
        };
        let state = field("State:")
            .split_ascii_whitespace()
            .next()
            .unwrap_or("-");
        let rss = field("VmRSS:")
            .split_ascii_whitespace()
            .next()
            .unwrap_or("0");
        let threads = field("Threads:");
        let name = field("Name:");
        // Command arguments can contain credentials, so diagnostics expose
        // only the kernel's short process name rather than /proc/PID/cmdline.
        let command = name
            .chars()
            .map(|value| if value.is_control() { '?' } else { value })
            .take(160)
            .collect::<String>();
        output.push_str(&format!(
            "{pid:<6} {state:<5} {rss:<8} {threads:<7} {command}\n"
        ));
        if output.len() > 64 * 1024 {
            break;
        }
    }
    Ok(output.into_bytes())
}

fn socket_endpoint(value: &str) -> Result<String, BackendError> {
    let (address, port) = value.split_once(':').ok_or(BackendError::Protocol)?;
    if address.len() != 8 || port.len() != 4 {
        return Err(BackendError::Protocol);
    }
    let address = u32::from_str_radix(address, 16).map_err(|_| BackendError::Protocol)?;
    let port = u16::from_str_radix(port, 16).map_err(|_| BackendError::Protocol)?;
    let address = std::net::Ipv4Addr::from(address.to_le_bytes());
    Ok(if port == 0 {
        format!("{address}:*")
    } else {
        format!("{address}:{port}")
    })
}

fn socket_state(protocol: &str, value: &str) -> &'static str {
    if protocol == "udp" {
        return match value {
            "01" => "ESTABLISHED",
            "07" => "UNCONN",
            _ => "UNKNOWN",
        };
    }
    match value {
        "01" => "ESTABLISHED",
        "02" => "SYN_SENT",
        "03" => "SYN_RECV",
        "04" => "FIN_WAIT1",
        "05" => "FIN_WAIT2",
        "06" => "TIME_WAIT",
        "07" => "CLOSE",
        "08" => "CLOSE_WAIT",
        "09" => "LAST_ACK",
        "0A" => "LISTEN",
        "0B" => "CLOSING",
        _ => "UNKNOWN",
    }
}

fn append_inet_sockets(
    output: &mut String,
    protocol: &str,
    input: &[u8],
) -> Result<(), BackendError> {
    let text = std::str::from_utf8(input).map_err(|_| BackendError::Protocol)?;
    for line in text.lines().skip(1).take(1024) {
        let fields = line.split_ascii_whitespace().collect::<Vec<_>>();
        if fields.len() < 4 {
            continue;
        }
        let local = socket_endpoint(fields[1])?;
        let remote = socket_endpoint(fields[2])?;
        output.push_str(&format!(
            "{protocol:<5} {local:<22} {remote:<22} {}\n",
            socket_state(protocol, fields[3])
        ));
        if output.len() > FILE_LIMIT as usize {
            return Err(BackendError::Protocol);
        }
    }
    Ok(())
}

pub(super) fn format_socket_tables(
    tcp: &[u8],
    udp: &[u8],
    unix: &[u8],
) -> Result<Vec<u8>, BackendError> {
    let mut output = String::from(
        "Active Internet connections\nProto Local Address          Foreign Address        State\n",
    );
    append_inet_sockets(&mut output, "tcp", tcp)?;
    append_inet_sockets(&mut output, "udp", udp)?;
    output.push_str("\nActive UNIX domain sockets\nType       State      Inode      Path\n");
    let unix = std::str::from_utf8(unix).map_err(|_| BackendError::Protocol)?;
    for line in unix.lines().skip(1).take(1024) {
        let fields = line.split_ascii_whitespace().collect::<Vec<_>>();
        if fields.len() < 7 {
            continue;
        }
        let kind = match fields[4] {
            "0001" => "STREAM",
            "0002" => "DGRAM",
            "0005" => "SEQPACKET",
            _ => "UNKNOWN",
        };
        let state = match (fields[3], fields[5]) {
            ("00010000", _) => "LISTENING",
            (_, "01") => "FREE",
            (_, "02") => "UNCONNECTED",
            (_, "03") => "CONNECTED",
            (_, "04") => "DISCONNECTING",
            _ => "UNKNOWN",
        };
        let path = fields.get(7).copied().unwrap_or("-");
        output.push_str(&format!(
            "{kind:<10} {state:<10} {:<10} {path}\n",
            fields[6]
        ));
        if output.len() > FILE_LIMIT as usize {
            return Err(BackendError::Protocol);
        }
    }
    Ok(output.into_bytes())
}
