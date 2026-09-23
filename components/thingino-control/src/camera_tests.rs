use super::*;
use std::os::unix::net::UnixListener;
use std::process;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn task_temp(name: &str) -> PathBuf {
    let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
    fs::create_dir_all(&root).unwrap();
    let path = PathBuf::from(root).join(format!(
        "ta-{}-{}-{name}",
        process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
    ));
    fs::create_dir(&path).unwrap();
    path
}

#[test]
fn host_socket_connect_waits_for_a_transient_restart() {
    let root = task_temp("prudynt-restart-connect");
    let socket = root.join("prudynt.sock");
    let server_socket = socket.clone();
    let server = thread::spawn(move || {
        thread::sleep(Duration::from_millis(80));
        let listener = UnixListener::bind(server_socket).unwrap();
        let _ = listener.accept().unwrap();
    });

    let started = Instant::now();
    let stream = connect_socket(&socket, started + Duration::from_secs(1)).unwrap();
    assert!(started.elapsed() >= Duration::from_millis(60));
    drop(stream);
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn host_socket_connect_honors_the_request_deadline() {
    let root = task_temp("prudynt-missing-connect");
    let socket = root.join("missing.sock");
    let started = Instant::now();
    assert!(matches!(
        connect_socket(&socket, started + Duration::from_millis(60)),
        Err(BackendError::Timeout)
    ));
    assert!(started.elapsed() >= Duration::from_millis(40));
    assert!(started.elapsed() < Duration::from_millis(250));
    fs::remove_dir_all(root).unwrap();
}

#[cfg(target_os = "linux")]
#[test]
fn statvfs_layout_matches_pinned_glibc_abi() {
    #[cfg(target_pointer_width = "32")]
    assert_eq!(std::mem::size_of::<Statvfs>(), 72);
    #[cfg(target_pointer_width = "64")]
    assert_eq!(std::mem::size_of::<Statvfs>(), 112);
}

#[test]
fn network_probe_reports_reachability_failures_as_diagnostic_results() {
    let backend = HostBackend::new(CameraPaths::default());
    let resolved = backend
        .network_probe(
            b"action=resolve&target=localhost",
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert_eq!(
        json::parse(&resolved.body)
            .unwrap()
            .get_path("success")
            .and_then(Value::as_bool),
        Some(true)
    );

    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let failed = backend
        .network_probe(
            format!("action=connect&target=127.0.0.1:{port}").as_bytes(),
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    let failed = json::parse(&failed.body).unwrap();
    assert_eq!(
        failed.get_path("success").and_then(Value::as_bool),
        Some(false)
    );
    let output = failed
        .get_path("output_b64")
        .and_then(Value::as_str)
        .unwrap();
    assert!(!output.is_empty());
}

#[test]
fn sd_mount_parser_rejects_non_mmc_and_malformed_device_names() {
    let root = task_temp("sd-mount-parser");
    let safe = root.join("mnt/card one");
    let escaped = safe.to_string_lossy().replace(' ', "\\040");
    let input = format!(
        "/dev/mmcblk0p1 {escaped} vfat ro,nosuid 0 0\n/dev/sda1 /mnt/usb ext4 rw 0 0\n/dev/mmcblk1p1 {escaped} vfat rw 0 0\n/dev/mmcblk0boot0 {escaped} raw rw 0 0\n/dev/mmcblk0pX /mnt/bad vfat rw 0 0\n/dev/mmcblk0p2 /etc vfat rw 0 0\n"
    );
    let mounts = sd_mounts(input.as_bytes(), &[root.join("mnt")]);
    assert_eq!(mounts.len(), 1);
    assert_eq!(mounts[0].device, "/dev/mmcblk0p1");
    assert_eq!(mounts[0].mountpoint, safe);
    assert!(!mounts[0].writable);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn busybox_syslog_ring_parser_returns_complete_messages_across_wrap() {
    let mut data = vec![b'x'; 32];
    data[23] = 0;
    data[24..32].copy_from_slice(b"wrapped ");
    data[..6].copy_from_slice(b"line\n\0");
    data[6..12].copy_from_slice(b"next\n\0");
    assert_eq!(
        parse_busybox_syslog_ring(32, 12, &data).unwrap(),
        b"wrapped line\nnext\n"
    );
    assert!(parse_busybox_syslog_ring(32, 32, &data).is_err());
    assert!(parse_busybox_syslog_ring(65 * 1024, 0, &data).is_err());
}

#[test]
fn android_streamer_log_parser_keeps_timestamp_priority_tag_and_message() {
    let payload = b"\x04prudynt\0stream ready\0";
    let mut entry = vec![0u8; 20 + payload.len()];
    entry[0..2].copy_from_slice(&(payload.len() as u16).to_le_bytes());
    entry[2..4].copy_from_slice(&0x8103u16.to_le_bytes());
    entry[4..8].copy_from_slice(&42i32.to_le_bytes());
    entry[12..16].copy_from_slice(&123i32.to_le_bytes());
    entry[16..20].copy_from_slice(&456i32.to_le_bytes());
    entry[20..].copy_from_slice(payload);
    assert_eq!(
        parse_android_log_entry(&entry).unwrap(),
        "123.000000456 I/prudynt(   42): stream ready\n"
    );
    entry[0..2].copy_from_slice(&u16::MAX.to_le_bytes());
    assert!(parse_android_log_entry(&entry).is_err());
}

#[test]
fn local_ipv4_ignores_loopback_and_finds_lan_address() {
    let root = task_temp("fib");
    let fib = root.join("fib_trie");
    fs::write(
        &fib,
        " |-- 127.0.0.1\n /32 host LOCAL\n |-- 192.0.2.103\n /32 host LOCAL\n",
    )
    .unwrap();
    assert_eq!(local_ipv4(&fib).as_deref(), Some("192.0.2.103"));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn runtime_interface_uses_kernel_link_and_route_state() {
    let root = task_temp("runtime-interface");
    let network_dir = root.join("network");
    let sys_class_net = root.join("net");
    let wlan = sys_class_net.join("wlan0");
    fs::create_dir_all(&network_dir).unwrap();
    fs::create_dir_all(&wlan).unwrap();
    fs::write(
        network_dir.join("wlan0"),
        b"auto wlan0\niface wlan0 inet dhcp\n",
    )
    .unwrap();
    fs::write(wlan.join("operstate"), b"up\n").unwrap();
    fs::write(wlan.join("carrier"), b"1\n").unwrap();
    fs::write(wlan.join("address"), b"02:00:00:00:00:01\n").unwrap();
    let proc_net_route = root.join("route");
    fs::write(
        &proc_net_route,
        b"Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n\
wlan0 000200C0 00000000 0001 0 0 0 00FFFFFF 0 0 0\n\
wlan0 00000000 010200C0 0003 0 0 0 00000000 0 0 0\n",
    )
    .unwrap();
    let backend = HostBackend::new(CameraPaths {
        network_dir,
        sys_class_net,
        proc_net_route,
        ..CameraPaths::default()
    });
    let runtime =
        json::parse(backend.interface_runtime("wlan0", "192.0.2.103").as_bytes()).unwrap();
    assert_eq!(runtime.get_path("link_up"), Some(&Value::Bool(true)));
    assert_eq!(
        runtime.get_path("address").and_then(Value::as_str),
        Some("192.0.2.103")
    );
    assert_eq!(
        runtime.get_path("netmask").and_then(Value::as_str),
        Some("255.255.255.0")
    );
    assert_eq!(
        runtime.get_path("gateway").and_then(Value::as_str),
        Some("192.0.2.1")
    );
    assert_eq!(
        runtime.get_path("broadcast").and_then(Value::as_str),
        Some("192.0.2.255")
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn network_config_masks_stored_passwords() {
    let root = task_temp("masked-passwords");
    let wpa_config = root.join("wpa.conf");
    let hostname = root.join("hostname");
    let resolv_config = root.join("resolv.conf");
    fs::write(
        &wpa_config,
        b"network={\n\tssid=66697874757265\n\tpsk=\"__SET_LOCALLY__\"\n}\n",
    )
    .unwrap();
    fs::write(&hostname, b"original\n").unwrap();
    fs::write(&resolv_config, b"nameserver 192.0.2.1\n").unwrap();
    let backend = HostBackend::new(CameraPaths {
        wpa_config,
        hostname: hostname.clone(),
        resolv_config: resolv_config.clone(),
        network_dir: root.join("network"),
        sys_class_net: root.join("net"),
        ..CameraPaths::default()
    });

    let network = backend.network_config().unwrap();
    let network_json = json::parse(&network.body).unwrap();
    assert_eq!(
        network_json.get_path("wifi.ssid"),
        Some(&Value::String("fixture".to_owned()))
    );
    assert_eq!(network_json.get_path("wifi.password"), Some(&Value::Null));
    assert_eq!(
        network_json.get_path("wifi.password_set"),
        Some(&Value::Bool(true))
    );
    let secret = b"__SET_LOCALLY__".as_slice();
    assert!(
        !network
            .body
            .windows(secret.len())
            .any(|value| value == secret)
    );

    assert!(
        backend
            .update_network_config(
                br#"{"hostname":"changed","dns":{"primary":"198.51.100.1","secondary":""},"wifi":{"ssid":"fixture","password":null,"bssid":"invalid"},"interfaces":{}}"#,
            )
            .is_err()
    );
    assert_eq!(fs::read(&hostname).unwrap(), b"original\n");
    assert_eq!(fs::read(&resolv_config).unwrap(), b"nameserver 192.0.2.1\n");

    let network_dir = root.join("network");
    fs::create_dir_all(&network_dir).unwrap();
    fs::write(
        network_dir.join("eth0"),
        b"auto eth0\niface eth0 inet static\n\taddress 192.0.2.20\n\tnetmask 255.255.255.0\n\tgateway 192.0.2.1\n\tbroadcast 192.0.2.255\n",
    )
    .unwrap();
    backend
        .update_network_config(br#"{"interfaces":{"eth0":{"enabled":true}}}"#)
        .unwrap();
    let preserved = fs::read_to_string(network_dir.join("eth0")).unwrap();
    for expected in [
        "address 192.0.2.20",
        "netmask 255.255.255.0",
        "gateway 192.0.2.1",
        "broadcast 192.0.2.255",
    ] {
        assert!(
            preserved.contains(expected),
            "missing {expected}: {preserved}"
        );
    }
    assert!(
        backend
            .update_network_config(br#"{"interfaces":{"eth0":{"dhcp":"false"}}}"#,)
            .is_err()
    );
    assert_eq!(
        fs::read_to_string(network_dir.join("eth0")).unwrap(),
        preserved
    );

    fs::remove_dir_all(root).unwrap();
}

#[test]
fn wpa_quoted_uses_wpa_supplicant_escapes_and_rejects_controls() {
    assert_eq!(
        wpa_quoted("camera \\\" lab"),
        Some("\"camera \\\\\\\" lab\"".to_owned())
    );
    assert_eq!(wpa_quoted("line\nbreak"), None);
    assert!(!valid_wifi_password("password\u{0008}"));
}

#[test]
fn diagnostics_config_info_masks_nested_secrets() {
    let root = task_temp("diagnostics-secrets");
    let thingino_config = root.join("thingino.json");
    fs::write(
        &thingino_config,
        br#"{"rtsp":{"password":"__SET_LOCALLY__"},"integration":{"token":"__SET_LOCALLY__"},"keep":7}"#,
    )
    .unwrap();
    let backend = HostBackend::new(CameraPaths {
        thingino_config,
        ..CameraPaths::default()
    });

    let response = backend
        .diagnostics_info("/api/v1/diagnostics/info?thingino")
        .unwrap();
    let response = json::parse(&response.body).unwrap();
    let encoded = response
        .get_path("commands")
        .and_then(Value::as_array)
        .and_then(|commands| commands.first())
        .and_then(|command| command.get_path("output_base64"))
        .and_then(Value::as_str)
        .unwrap();
    assert_eq!(
        encoded,
        base64_encode(
            br#"{"integration":{"token":null,"token_set":true},"keep":7,"rtsp":{"password":null,"password_set":true}}
"#
        )
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn diagnostics_netstat_formats_inet_and_unix_sockets() {
    let tcp = b"  sl  local_address rem_address   st\n   0: 0100007F:0050 00000000:0000 0A\n   1: 6700000A:1F90 08080808:01BB 01\n";
    let udp = b"  sl  local_address rem_address   st\n   0: 00000000:0044 00000000:0000 07\n";
    let unix = b"Num RefCount Protocol Flags Type St Inode Path\n0000: 00000002 00000000 00010000 0001 01 1234 /run/listener.sock\n0001: 00000003 00000000 00000000 0001 03 5678 /run/client.sock\n";

    let output = diagnostics::format_socket_tables(tcp, udp, unix).unwrap();
    let output = std::str::from_utf8(&output).unwrap();
    assert!(output.contains("tcp   127.0.0.1:80"));
    assert!(output.contains("10.0.0.103:8080"));
    assert!(output.contains("8.8.8.8:443"));
    assert!(output.contains("ESTABLISHED"));
    assert!(output.contains("udp   0.0.0.0:68"));
    assert!(output.contains("UNCONN"));
    assert!(output.contains("LISTENING  1234       /run/listener.sock"));
    assert!(output.contains("CONNECTED  5678       /run/client.sock"));
}

#[test]
fn diagnostics_process_snapshot_reads_proc_without_a_helper() {
    let root = task_temp("process-snapshot");
    let process = root.join("42");
    fs::create_dir_all(&process).unwrap();
    fs::write(
        process.join("status"),
        b"Name:\tfixture\nState:\tS (sleeping)\nVmRSS:\t1234 kB\nThreads:\t3\n",
    )
    .unwrap();
    let output = diagnostics::process_snapshot(&root).unwrap();
    let output = std::str::from_utf8(&output).unwrap();
    assert!(output.contains("PID    STATE RSS_KIB  THREADS COMMAND"));
    assert!(output.contains("42     S     1234     3       fixture"));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn gpio_api_normalizes_and_validates_config() {
    let root = task_temp("gpio");
    let thingino_config = root.join("thingino.json");
    let sys_class_gpio = root.join("gpio");
    for pin in [49, 50, 61] {
        fs::create_dir_all(sys_class_gpio.join(format!("gpio{pin}"))).unwrap();
        fs::write(sys_class_gpio.join(format!("gpio{pin}/value")), b"0\n").unwrap();
    }
    fs::write(
        &thingino_config,
        br#"{"gpio":{"ir850":61,"ircut":"50 49"},"keep":7}"#,
    )
    .unwrap();
    let backend = HostBackend::new(CameraPaths {
        thingino_config: thingino_config.clone(),
        sys_class_gpio: sys_class_gpio.clone(),
        ircut_state: root.join("ircut_mode"),
        ..CameraPaths::default()
    });

    let config = json::parse(&backend.gpio_config().unwrap().body).unwrap();
    assert_eq!(
        config.get_path("gpio.ircut"),
        Some(&Value::String("50 49".to_owned()))
    );
    assert_eq!(config.get_path("gpio.ir850"), Some(&number(61)));
    assert_eq!(
        config.get_path("available_startup_indicators"),
        Some(&Value::String("green,red".to_owned()))
    );
    assert_eq!(
        config
            .get_path("hardware_io")
            .and_then(Value::as_array)
            .map(|entries| entries.len()),
        Some(10)
    );
    for pin in [18, 49, 50, 52, 54, 57, 59, 60, 61, 63] {
        assert!(config.to_json().contains(&format!("\"gpio\":{pin}")));
    }
    assert_eq!(
        config.get_path("pwm_pins"),
        Some(&Value::String(String::new()))
    );

    backend
        .update_gpio_config(br#"{"startup_indicator":"green"}"#)
        .unwrap();
    let saved = json::parse(&fs::read(&thingino_config).unwrap()).unwrap();
    assert_eq!(
        saved.get_path("gpio.ircut"),
        Some(&Value::String("50 49".to_owned()))
    );
    assert_eq!(saved.get_path("gpio.ir850"), Some(&number(61)));
    assert_eq!(
        saved.get_path("led.startup_indicator"),
        Some(&Value::String("green".to_owned()))
    );
    assert_eq!(saved.get_path("keep"), Some(&number(7)));
    for invalid in [
        br#"{"startup_indicator":"blue"}"#.as_slice(),
        br#"{"ircut_pin1":50,"ircut_pin2":49}"#,
        br#"{"ir850":{"pin":61}}"#,
        br#"{"ir940":{"pin":62}}"#,
        br#"{"white":{"pin":64}}"#,
    ] {
        assert!(backend.update_gpio_config(invalid).is_err());
    }

    fs::remove_dir_all(root).unwrap();
}

#[test]
fn storage_paths_are_fail_closed() {
    let roots = vec![PathBuf::from("/mnt"), PathBuf::from("/media")];
    for valid in ["/mnt/mmcblk0p1", "/media/card"] {
        let path = validated_absolute_path(valid).unwrap();
        assert!(path_is_within_roots(&path, &roots));
    }
    for invalid in ["/mnt/../etc", "camera/records", "../records"] {
        assert!(validated_absolute_path(invalid).is_err());
    }
    assert!(!path_is_within_roots(Path::new("/overlay"), &roots));
    assert!(!path_is_within_roots(Path::new("/mnt-other"), &roots));
}

#[test]
fn factory_reset_request_is_durable_and_never_deletes_live_overlay() {
    let root = task_temp("factory-reset-marker");
    fs::write(root.join("persisted-setting"), b"keep-until-reboot").unwrap();
    request_overlay_reset(&root).unwrap();
    assert_eq!(
        fs::read(root.join(".thingino-factory-reset")).unwrap(),
        b"reset\n"
    );
    assert_eq!(
        fs::read(root.join("persisted-setting")).unwrap(),
        b"keep-until-reboot"
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn management_credential_update_shares_the_config_lock() {
    let root = task_temp("management-config-lock");
    let onvif_config = root.join("onvif.json");
    fs::write(
        &onvif_config,
        br#"{"server":{"username":"root","password":"__SET_LOCALLY__","port":1999}}"#,
    )
    .unwrap();
    let backend = Arc::new(HostBackend::new(CameraPaths {
        onvif_config: onvif_config.clone(),
        ..CameraPaths::default()
    }));
    let guard = backend.config_lock.lock().unwrap();
    let worker_backend = Arc::clone(&backend);
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (result_tx, result_rx) = std::sync::mpsc::channel();
    let worker = thread::spawn(move || {
        started_tx.send(()).unwrap();
        let result = HostBackend::update_management_credential(
            worker_backend.as_ref(),
            "root",
            "new-management-secret",
            Instant::now() + Duration::from_secs(1),
        );
        result_tx.send(result).unwrap();
    });
    started_rx.recv_timeout(Duration::from_secs(1)).unwrap();
    assert!(result_rx.recv_timeout(Duration::from_millis(100)).is_err());
    drop(guard);
    assert!(
        result_rx
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .is_ok()
    );
    worker.join().unwrap();
    let onvif = json::parse(&fs::read(&onvif_config).unwrap()).unwrap();
    assert_eq!(
        onvif.get_path("server.password"),
        Some(&Value::String("new-management-secret".to_owned()))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn config_domains_reject_undocumented_put() {
    let backend = HostBackend::new(CameraPaths::default());
    for target in [
        "/api/v1/config/admin",
        "/api/v1/config/webui",
        "/api/v1/config/rsyslog",
    ] {
        assert!(matches!(
            backend.api_request(
                "PUT",
                target,
                br#"{"enabled":false}"#,
                Instant::now() + Duration::from_secs(1),
            ),
            Some(Err(BackendError::Protocol))
        ));
    }
}

#[test]
fn approved_route_policy_distinguishes_protocol_errors_from_unknown_targets() {
    let root = task_temp("route-policy");
    let crontab = root.join("root.crontab");
    fs::write(&crontab, b"# camera schedule\n").unwrap();
    let backend = HostBackend::new(CameraPaths {
        crontab: crontab.clone(),
        ..CameraPaths::default()
    });
    let deadline = Instant::now() + Duration::from_secs(1);

    assert!(matches!(
        backend.api_request("GET", "/api/v1/config/crontab", b"", deadline),
        Some(Ok(_))
    ));
    assert!(matches!(
        backend.api_request(
            "POST",
            "/api/v1/config/crontab",
            br#"{"content":"0 * * * * /bin/true\n"}"#,
            deadline,
        ),
        Some(Ok(_))
    ));
    assert_eq!(fs::read(&crontab).unwrap(), b"0 * * * * /bin/true\n");

    assert!(matches!(
        backend.api_request("GET", "/api/v1/sensor/iq", b"", deadline),
        Some(Ok(_))
    ));
    for (method, target, body) in [
        ("GET", "/api/v1/actions/reboot", b"".as_slice()),
        ("POST", "/api/v1/actions/reboot", b"{}".as_slice()),
        ("GET", "/api/v1/actions/time/sync", b"".as_slice()),
        ("POST", "/api/v1/actions/time/sync", b"{}".as_slice()),
        ("PUT", "/api/v1/config/crontab", b"{}".as_slice()),
        ("GET", "/api/v1/config/crontab", b"{}".as_slice()),
        ("POST", "/api/v1/config/crontab", b"".as_slice()),
        ("POST", "/api/v1/sensor/iq", b"{}".as_slice()),
        ("GET", "/api/v1/sensor/iq", b"{}".as_slice()),
    ] {
        assert!(
            matches!(
                backend.api_request(method, target, body, deadline),
                Some(Err(BackendError::Protocol))
            ),
            "{method} {target}"
        );
    }
    assert!(
        backend
            .api_request("GET", "/api/v1/actions/time/sync?legacy=1", b"", deadline,)
            .is_none()
    );
    assert!(
        backend
            .api_request("GET", "/api/v1/unknown", b"", deadline)
            .is_none()
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn thingino_domain_routes_use_the_validated_public_schema() {
    let root = task_temp("validated-domain-route");
    let thingino_config = root.join("thingino.json");
    fs::write(
        &thingino_config,
        br#"{"webui":{"level":"user","paranoid":false,"theme":"dark"}}"#,
    )
    .unwrap();
    let backend = HostBackend::new(CameraPaths {
        thingino_config: thingino_config.clone(),
        ..CameraPaths::default()
    });

    let response = backend
        .api_request(
            "GET",
            "/api/v1/config/webui",
            b"",
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap()
        .unwrap();
    let response = json::parse(&response.body).unwrap();
    assert_eq!(
        response.get_path("username"),
        Some(&Value::String("root".to_owned()))
    );
    assert_eq!(response.get_path("track_focus"), Some(&Value::Bool(false)));

    assert_eq!(
        backend
            .api_request(
                "POST",
                "/api/v1/config/webui",
                br#"{"username":"admin"}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap(),
        Err(BackendError::Protocol)
    );
    backend
        .api_request(
            "POST",
            "/api/v1/config/webui",
            br#"{"theme":"light","track_focus":true,"focus_timeout":15}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap()
        .unwrap();
    let stored = json::parse(&fs::read(&thingino_config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("webui.theme"),
        Some(&Value::String("light".to_owned()))
    );
    assert_eq!(stored.get_path("webui.username"), None);
    fs::remove_dir_all(root).unwrap();
}
