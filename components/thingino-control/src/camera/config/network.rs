use super::super::*;

impl PrudyntBackend {
    pub(in crate::camera) fn network_config(&self) -> Result<BackendResponse, BackendError> {
        let hostname = read_text_value(&self.paths.hostname, 255).unwrap_or_default();
        let resolver = read_text_value(&self.paths.resolv_config, 4096).unwrap_or_default();
        let mut nameservers = resolver.lines().filter_map(|line| {
            line.split_once("nameserver ")
                .map(|(_, value)| value.trim().to_owned())
        });
        let dns_primary = nameservers.next().unwrap_or_default();
        let dns_secondary = nameservers.next().unwrap_or_default();
        let wpa = read_text_value(&self.paths.wpa_config, 16 * 1024).unwrap_or_default();
        let ssid = config_ssid_assignment(&wpa).unwrap_or_default();
        let password_set = config_assignment(&wpa, "psk").is_some_and(|value| !value.is_empty());
        let bssid = config_assignment(&wpa, "bssid").unwrap_or_default();
        let interfaces = ["eth0", "wlan0", "usb0"]
            .into_iter()
            .map(|name| (name, self.interface_config(name)))
            .collect::<Vec<_>>();
        let body = format!(
            "{{\"hostname\":{},\"dns\":{{\"primary\":{},\"secondary\":{}}},\"wifi\":{{\"ssid\":{},\"password\":null,\"password_set\":{},\"bssid\":{}}},\"wifi_ap\":{{\"enabled\":false}},\"interfaces\":{{\"eth0\":{},\"wlan0\":{},\"usb0\":{}}}}}\n",
            Value::String(hostname).to_json(),
            Value::String(dns_primary).to_json(),
            Value::String(dns_secondary).to_json(),
            Value::String(ssid).to_json(),
            bool_json(password_set),
            Value::String(bssid).to_json(),
            interfaces[0].1,
            interfaces[1].1,
            interfaces[2].1,
        );
        Ok(BackendResponse::json(body.into_bytes()))
    }
    pub(in crate::camera) fn network_probe_metadata(
        &self,
    ) -> Result<BackendResponse, BackendError> {
        let interfaces = ["eth0", "wlan0", "usb0"]
            .into_iter()
            .filter(|name| self.paths.sys_class_net.join(name).is_dir())
            .map(|name| Value::String(name.to_owned()))
            .collect::<Vec<_>>();
        json_response(object([
            (
                "actions",
                Value::Array(vec![
                    object([
                        ("id", Value::String("resolve".to_owned())),
                        ("label", Value::String("DNS resolve".to_owned())),
                        (
                            "description",
                            Value::String("Resolve a host with the system resolver.".to_owned()),
                        ),
                    ]),
                    object([
                        ("id", Value::String("connect".to_owned())),
                        ("label", Value::String("TCP connect".to_owned())),
                        (
                            "description",
                            Value::String(
                                "Resolve a host and measure a bounded TCP connection.".to_owned(),
                            ),
                        ),
                    ]),
                ]),
            ),
            ("interfaces", Value::Array(interfaces)),
            (
                "defaults",
                object([
                    ("action", Value::String("resolve".to_owned())),
                    ("interface", Value::String("auto".to_owned())),
                    ("packet_size", Value::Number("56".to_owned())),
                    ("count", Value::Number("1".to_owned())),
                ]),
            ),
            (
                "limits",
                object([
                    (
                        "packet_size",
                        object([
                            ("min", Value::Number("1".to_owned())),
                            ("max", Value::Number("65507".to_owned())),
                        ]),
                    ),
                    (
                        "count",
                        object([
                            ("min", Value::Number("1".to_owned())),
                            ("max", Value::Number("1".to_owned())),
                        ]),
                    ),
                ]),
            ),
        ]))
    }
    pub(in crate::camera) fn wifi_scan(
        &self,
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let networks = wpa_scan_results(&self.paths.wpa_control, deadline)?;
        json_response(object([("networks", Value::Array(networks))]))
    }
    pub(in crate::camera) fn network_probe(
        &self,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        let action = form_param(body, "action").ok_or(BackendError::Protocol)?;
        let target = form_param(body, "target").ok_or(BackendError::Protocol)?;
        if target.is_empty()
            || target.len() > 253
            || target.contains(['\0', '\r', '\n', '/', '\\', ' ', '\t'])
        {
            return Err(BackendError::Protocol);
        }
        let started = Instant::now();
        let (host, port) = split_host_port(&target, 80)?;
        let command = match action.as_str() {
            "resolve" => format!("resolve {host}"),
            "connect" => format!("connect {host}:{port}"),
            _ => return Err(BackendError::Protocol),
        };
        let mut output = String::new();
        output.push_str(&format!("Host: {host}\n"));
        let addresses = match (host.as_str(), port).to_socket_addrs() {
            Ok(addresses) => addresses.collect::<Vec<_>>(),
            Err(_) => {
                output.push_str("Result: name resolution failed\n");
                return network_probe_response(command, output, false);
            }
        };
        if addresses.is_empty() {
            output.push_str("Result: the resolver returned no addresses\n");
            return network_probe_response(command, output, false);
        }
        for address in &addresses {
            output.push_str(&format!("Address: {}\n", address.ip()));
        }
        if action == "connect" {
            let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
                output.push_str("Result: connection deadline expired\n");
                return network_probe_response(command, output, false);
            };
            let remaining = remaining.min(Duration::from_secs(1));
            if let Err(error) = TcpStream::connect_timeout(&addresses[0], remaining) {
                output.push_str(
                    if matches!(
                        error.kind(),
                        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
                    ) {
                        "Result: connection timed out\n"
                    } else {
                        "Result: connection failed\n"
                    },
                );
                return network_probe_response(command, output, false);
            }
            output.push_str(&format!(
                "Connected to {} in {} ms\n",
                addresses[0],
                started.elapsed().as_millis()
            ));
        }
        network_probe_response(command, output, true)
    }
    pub(in crate::camera) fn interface_config(&self, name: &str) -> String {
        let config = read_text_value(&self.paths.network_dir.join(name), 4096).unwrap_or_default();
        let enabled = config.lines().any(|line| {
            line.split_ascii_whitespace().collect::<Vec<_>>().as_slice() == ["auto", name]
        });
        let dhcp = config.lines().any(|line| {
            line.split_ascii_whitespace().collect::<Vec<_>>().as_slice()
                == ["iface", name, "inet", "dhcp"]
        });
        let sys = self.paths.sys_class_net.join(name);
        let mac = read_virtual_text_value(&sys.join("address"), 64).unwrap_or_default();
        let link_up = read_virtual_text_value(&sys.join("operstate"), 32)
            .is_some_and(|value| value == "up")
            || read_virtual_text_value(&sys.join("carrier"), 8).is_some_and(|value| value == "1");
        format!(
            "{{\"enabled\":{},\"dhcp\":{},\"ipv6\":false,\"mac\":{},\"address\":{},\"netmask\":{},\"gateway\":{},\"broadcast\":{},\"link_up\":{}}}",
            bool_json(enabled),
            bool_json(dhcp),
            Value::String(mac).to_json(),
            Value::String(interface_value(&config, "address")).to_json(),
            Value::String(interface_value(&config, "netmask")).to_json(),
            Value::String(interface_value(&config, "gateway")).to_json(),
            Value::String(interface_value(&config, "broadcast")).to_json(),
            bool_json(link_up),
        )
    }

    pub(in crate::camera) fn interface_runtime(&self, name: &str, local_ip: &str) -> String {
        let mut value = json::parse(self.interface_config(name).as_bytes())
            .unwrap_or_else(|_| Value::Object(BTreeMap::new()));
        let routes = read_virtual_text_value(&self.paths.proc_net_route, 64 * 1024)
            .map(|text| parse_interface_routes(&text, name))
            .unwrap_or_default();
        if let Some(runtime) = runtime_ipv4_values(local_ip, &routes) {
            for (field, field_value) in [
                ("address", runtime.address),
                ("netmask", runtime.netmask),
                ("gateway", runtime.gateway),
                ("broadcast", runtime.broadcast),
            ] {
                let _ = value.set_path(field, Value::String(field_value));
            }
            let _ = value.set_path("link_up", Value::Bool(true));
        }
        value.to_json()
    }
    pub(in crate::camera) fn update_network_config(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let root = request.as_object().ok_or(BackendError::Protocol)?;
        if root.keys().any(|key| {
            !matches!(
                key.as_str(),
                "hostname" | "dns" | "wifi" | "wifi_ap" | "interfaces"
            )
        }) {
            return Err(BackendError::Protocol);
        }
        if let Some(wifi_ap) = root.get("wifi_ap") {
            let wifi_ap = wifi_ap.as_object().ok_or(BackendError::Protocol)?;
            if wifi_ap.keys().any(|key| key != "enabled")
                || wifi_ap
                    .get("enabled")
                    .is_some_and(|value| value.as_bool() != Some(false))
            {
                return Err(BackendError::Protocol);
            }
        }
        if let Some(interfaces) = root.get("interfaces") {
            let interfaces = interfaces.as_object().ok_or(BackendError::Protocol)?;
            if interfaces
                .keys()
                .any(|key| !matches!(key.as_str(), "eth0" | "wlan0" | "usb0"))
            {
                return Err(BackendError::Protocol);
            }
        }
        let mut writes = Vec::new();
        if let Some(hostname) = request.get_path("hostname") {
            let hostname = hostname
                .as_str()
                .filter(|value| safe_hostname(value))
                .ok_or(BackendError::Protocol)?;
            writes.push((
                self.paths.hostname.clone(),
                format!("{hostname}\n").into_bytes(),
                0o600,
            ));
        }

        if let Some(dns) = request.get_path("dns") {
            let dns = dns.as_object().ok_or(BackendError::Protocol)?;
            if dns
                .keys()
                .any(|key| !matches!(key.as_str(), "primary" | "secondary"))
            {
                return Err(BackendError::Protocol);
            }
            let current_resolver =
                read_text_value(&self.paths.resolv_config, 4096).unwrap_or_default();
            let current = current_resolver
                .lines()
                .filter_map(|line| {
                    line.split_once("nameserver ")
                        .map(|(_, value)| value.trim())
                })
                .collect::<Vec<_>>();
            let mut resolver = String::from("# managed by Thingino Control\n");
            for (index, key) in ["primary", "secondary"].into_iter().enumerate() {
                let value = dns
                    .get(key)
                    .map(|value| value.as_str().ok_or(BackendError::Protocol))
                    .transpose()?
                    .unwrap_or_else(|| current.get(index).copied().unwrap_or(""));
                if !value.is_empty() {
                    value
                        .parse::<std::net::IpAddr>()
                        .map_err(|_| BackendError::Protocol)?;
                    resolver.push_str("nameserver ");
                    resolver.push_str(value);
                    resolver.push('\n');
                }
            }
            writes.push((
                self.paths.resolv_config.clone(),
                resolver.into_bytes(),
                0o600,
            ));
        }

        if let Some(wifi) = request.get_path("wifi") {
            let wifi = wifi.as_object().ok_or(BackendError::Protocol)?;
            if wifi
                .keys()
                .any(|key| !matches!(key.as_str(), "ssid" | "password" | "password_set" | "bssid"))
            {
                return Err(BackendError::Protocol);
            }
            if wifi
                .get("password")
                .is_some_and(|value| !matches!(value, Value::Null | Value::String(_)))
                || wifi
                    .get("password_set")
                    .is_some_and(|value| value.as_bool().is_none())
            {
                return Err(BackendError::Protocol);
            }
            let current_wpa =
                read_text_value(&self.paths.wpa_config, 16 * 1024).unwrap_or_default();
            let ssid = wifi
                .get("ssid")
                .map(|value| value.as_str().ok_or(BackendError::Protocol))
                .transpose()?
                .map(str::to_owned)
                .or_else(|| config_ssid_assignment(&current_wpa))
                .unwrap_or_default();
            let supplied_password = wifi
                .get("password")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            let password = supplied_password
                .map(str::to_owned)
                .or_else(|| config_assignment(&current_wpa, "psk"))
                .ok_or(BackendError::Protocol)?;
            if ssid.is_empty()
                || ssid.len() > 32
                || ssid.chars().any(char::is_control)
                || !valid_wifi_password(&password)
            {
                return Err(BackendError::Protocol);
            }
            let psk = if password.len() == 64
                && password.bytes().all(|value| value.is_ascii_hexdigit())
            {
                password.to_owned()
            } else {
                wpa_quoted(&password).ok_or(BackendError::Protocol)?
            };
            let mut wpa = format!(
                "ctrl_interface=/run/wpa_supplicant\nupdate_config=1\nnetwork={{\n\tssid={}\n\tpsk={}\n",
                wpa_quoted(&ssid).ok_or(BackendError::Protocol)?,
                psk,
            );
            let bssid = wifi
                .get("bssid")
                .map(|value| value.as_str().ok_or(BackendError::Protocol))
                .transpose()?
                .map(str::to_owned)
                .or_else(|| config_assignment(&current_wpa, "bssid"))
                .unwrap_or_default();
            if !bssid.is_empty() {
                if !valid_mac(&bssid) {
                    return Err(BackendError::Protocol);
                }
                wpa.push_str("\tbssid=");
                wpa.push_str(&bssid);
                wpa.push('\n');
            }
            wpa.push_str("}\n");
            writes.push((self.paths.wpa_config.clone(), wpa.into_bytes(), 0o600));
        }

        for name in ["eth0", "wlan0", "usb0"] {
            let Some(update) = request.get_path(&format!("interfaces.{name}")) else {
                continue;
            };
            let update_object = update.as_object().ok_or(BackendError::Protocol)?;
            if update_object.keys().any(|key| {
                !matches!(
                    key.as_str(),
                    "enabled"
                        | "dhcp"
                        | "ipv6"
                        | "mac"
                        | "address"
                        | "netmask"
                        | "gateway"
                        | "broadcast"
                        | "link_up"
                )
            }) {
                return Err(BackendError::Protocol);
            }
            for key in ["enabled", "dhcp", "ipv6", "link_up"] {
                if update_object
                    .get(key)
                    .is_some_and(|value| value.as_bool().is_none())
                {
                    return Err(BackendError::Protocol);
                }
            }
            for key in ["mac", "address", "netmask", "gateway", "broadcast"] {
                if update_object
                    .get(key)
                    .is_some_and(|value| value.as_str().is_none())
                {
                    return Err(BackendError::Protocol);
                }
            }
            let current = self.interface_config(name);
            let mut interface =
                json::parse(current.as_bytes()).map_err(|_| BackendError::Protocol)?;
            interface
                .merge(update)
                .map_err(|_| BackendError::Protocol)?;
            let enabled = interface
                .get_path("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let dhcp = interface
                .get_path("dhcp")
                .and_then(Value::as_bool)
                .unwrap_or(true);
            if interface.get_path("ipv6").and_then(Value::as_bool) == Some(true) {
                return Err(BackendError::Protocol);
            }
            let mut config = String::new();
            if enabled {
                config.push_str(&format!("auto {name}\n"));
            }
            config.push_str(&format!(
                "iface {name} inet {}\n",
                if dhcp { "dhcp" } else { "static" }
            ));
            if !dhcp {
                for key in ["address", "netmask", "gateway", "broadcast"] {
                    if let Some(value) = interface.get_path(key).and_then(Value::as_str)
                        && !value.is_empty()
                    {
                        value
                            .parse::<std::net::Ipv4Addr>()
                            .map_err(|_| BackendError::Protocol)?;
                        config.push_str(&format!("\t{key} {value}\n"));
                    }
                }
            }
            writes.push((
                self.paths.network_dir.join(name),
                config.into_bytes(),
                0o600,
            ));
        }
        if writes.is_empty() {
            return Err(BackendError::Protocol);
        }
        for (path, contents, mode) in writes {
            write_config_file(&path, &contents, mode)?;
        }
        Ok(BackendResponse::json(b"{\"status\":\"ok\"}\n".to_vec()))
    }
}

fn network_probe_response(
    command: String,
    output: String,
    success: bool,
) -> Result<BackendResponse, BackendError> {
    json_response(object([
        ("command", Value::String(command)),
        ("success", Value::Bool(success)),
        (
            "output_b64",
            Value::String(base64_encode(output.as_bytes())),
        ),
    ]))
}
