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

fn assert_prudynt_json_frame(frame: &[u8], payload: &[u8]) {
    assert_eq!(frame, prudynt::framed_prudynt_json(payload));
}

fn prudynt_json_frame_payload(frame: &[u8]) -> &[u8] {
    let newline = frame.iter().position(|value| *value == b'\n').unwrap();
    let header = std::str::from_utf8(&frame[..newline]).unwrap();
    let length = header
        .strip_prefix("PRUDYNT/1 JSON ")
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap();
    let payload = &frame[newline + 1..];
    assert_eq!(payload.len(), length);
    payload
}

#[test]
fn prudynt_socket_connect_waits_for_a_transient_restart() {
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
fn prudynt_socket_connect_honors_the_request_deadline() {
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
fn snapshot_uses_prudynt_framing_without_a_child_process() {
    let root = task_temp("snapshot");
    let socket = root.join("p");
    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut command = String::new();
        stream.read_to_string(&mut command).unwrap();
        assert_eq!(command, "SNAPSHOT ch=1\n");
        stream.write_all(b"OK 6\n\xff\xd8ab\xff\xd9").unwrap();
    });
    let paths = CameraPaths {
        prudynt_socket: socket,
        ..CameraPaths::default()
    };
    let response = PrudyntBackend::new(paths)
        .snapshot(1, Instant::now() + Duration::from_secs(1))
        .unwrap();
    assert_eq!(response.content_type, "image/jpeg");
    assert_eq!(response.body, b"\xff\xd8ab\xff\xd9");
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn network_probe_reports_reachability_failures_as_diagnostic_results() {
    let backend = PrudyntBackend::new(CameraPaths::default());
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
fn media_authorization_allows_only_canonical_snapshot_urls() {
    let backend = PrudyntBackend::new(CameraPaths::default());
    assert!(backend.authorize_media("/api/v1/actions/snapshot?stream_id=0"));
    assert!(backend.authorize_media("/api/v1/actions/snapshot?stream_id=1"));
    assert!(backend.authorize_media("/onvif/image.cgi"));
    assert!(backend.authorize_media("/onvif/image1.cgi"));
    assert!(!backend.authorize_media("/api/v1/actions/snapshot?stream_id=2"));
    assert!(!backend.authorize_media("/api/v1/actions/snapshot?stream_id=0&download=1"));
    assert!(!backend.authorize_media("/onvif/image.cgi?stream=1"));
}

#[test]
fn audio_runtime_state_starts_from_prudynt_config() {
    let root = task_temp("audio-runtime-state");
    let prudynt_config = root.join("prudynt.json");
    fs::write(
        &prudynt_config,
        br#"{"audio":{"mic_enabled":true,"spk_enabled":false}}"#,
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config,
        ..CameraPaths::default()
    });
    assert_eq!(
        *backend.audio_state.lock().unwrap(),
        AudioRuntimeState {
            microphone: true,
            speaker: false,
        }
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn prudynt_restart_schedules_daynight_runtime_reapply() {
    let root = task_temp("restart-daynight-reapply");
    let prudynt_socket = root.join("prudynt.sock");
    let prudynt_config = root.join("prudynt.json");
    let motion_alarm = root.join("run/motion/motion_alarm");
    let motion_detected = root.join("run/prudynt/motion_detected.active");
    fs::create_dir_all(motion_alarm.parent().unwrap()).unwrap();
    fs::create_dir_all(motion_detected.parent().unwrap()).unwrap();
    fs::write(&motion_alarm, b"active\n").unwrap();
    fs::write(&motion_detected, b"active\n").unwrap();
    fs::write(
        &prudynt_config,
        br#"{"audio":{"mic_enabled":true,"spk_enabled":false}}"#,
    )
    .unwrap();
    let listener = UnixListener::bind(&prudynt_socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut command = Vec::new();
        stream.read_to_end(&mut command).unwrap();
        assert_prudynt_json_frame(&command, br#"{"action":{"restart_thread":7}}"#);
        stream.write_all(b"{\"code\":200}\n").unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_socket,
        prudynt_config,
        motion_alarm: motion_alarm.clone(),
        motion_detected: motion_detected.clone(),
        ..CameraPaths::default()
    });
    assert!(backend.motion.snapshot().active);
    let before = Instant::now();
    backend
        .restart_prudynt(Instant::now() + Duration::from_secs(1))
        .unwrap();
    let scheduled = backend.daynight_reapply.lock().unwrap().unwrap();
    assert!(scheduled >= before + Duration::from_secs(2));
    assert!(!backend.motion.snapshot().active);
    assert!(!backend.motion.snapshot().monitoring);
    assert!(!motion_alarm.exists());
    assert!(!motion_detected.exists());
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn control_validates_actions_and_keeps_rapid_motion_toggles_in_process() {
    let root = task_temp("control-actions");
    let socket = root.join("prudynt.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let expected = [
        br#"{"motion":{"enabled":true}}"#.as_slice(),
        br#"{"motion":{"enabled":false}}"#,
        br#"{"motion":{"enabled":true}}"#,
        br#"{"privacy":{"enabled":false}}"#,
        br#"{"audio":{"mic_enabled":true,"spk_enabled":false}}"#,
    ];
    let server = thread::spawn(move || {
        for expected in expected {
            let (mut stream, _) = listener.accept().unwrap();
            let mut command = Vec::new();
            stream.read_to_end(&mut command).unwrap();
            assert_prudynt_json_frame(&command, expected);
            stream
                .write_all(b"{\"code\":200,\"result\":\"success\"}\n")
                .unwrap();
        }
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_socket: socket,
        ..CameraPaths::default()
    });
    for body in expected {
        let response = backend
            .control(body, Instant::now() + Duration::from_secs(1))
            .unwrap();
        assert_eq!(response.body, b"{\"status\":\"ok\"}\n");
    }
    assert_eq!(
        *backend.audio_state.lock().unwrap(),
        AudioRuntimeState {
            microphone: true,
            speaker: false,
        }
    );
    assert!(
        backend
            .control(
                br#"{"audio":{"unknown":true}}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .is_err()
    );
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn recorder_actions_validate_config_and_use_framed_prudynt_ipc() {
    let root = task_temp("recorder-actions");
    let mount = root.join("recordings");
    let prudynt_socket = root.join("prudynt.sock");
    let prudynt_config = root.join("prudynt.json");
    let hostname = root.join("hostname");
    let proc_mounts = root.join("mounts");
    fs::create_dir(&mount).unwrap();
    fs::write(&hostname, b"fixture-camera\n").unwrap();
    fs::write(
        &proc_mounts,
        format!("/dev/mmcblk0p1 {} vfat rw,relatime 0 0\n", mount.display()),
    )
    .unwrap();
    fs::write(
        &prudynt_config,
        format!(
            "{{\"recorder\":{{\"mount\":{},\"device_path\":\"%hostname/clips\",\"filename\":\"%Y%m%dT%H%M%S\",\"duration\":30}}}}",
            Value::String(mount.to_string_lossy().into_owned()).to_json()
        ),
    )
    .unwrap();
    let listener = UnixListener::bind(&prudynt_socket).unwrap();
    let server = thread::spawn(move || {
        for (expected, response) in [
            (
                br#"{"mp4":{"start":{"channel":1}}}"#.as_slice(),
                br#"{"mp4":{"start":"ok"}}"#.as_slice(),
            ),
            (
                br#"{"mp4":{"stop":{"channel":1}}}"#.as_slice(),
                br#"{"mp4":{"stop":"ok"}}"#.as_slice(),
            ),
        ] {
            let (mut stream, _) = listener.accept().unwrap();
            let mut frame = Vec::new();
            stream.read_to_end(&mut frame).unwrap();
            assert_prudynt_json_frame(&frame, expected);
            stream.write_all(response).unwrap();
        }
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config,
        prudynt_socket,
        hostname,
        proc_mounts: proc_mounts.clone(),
        media_roots: vec![root.clone()],
        ..CameraPaths::default()
    });

    let response = backend
        .control(
            br#"{"mp4":{"start":{"channel":1}}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert_eq!(response.body, b"{\"status\":\"ok\"}\n");

    backend
        .control(
            br#"{"mp4":{"stop":{"channel":1}}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    server.join().unwrap();

    fs::write(
        &proc_mounts,
        format!("/dev/mmcblk0p1 {} vfat ro,relatime 0 0\n", mount.display()),
    )
    .unwrap();
    let config = json::parse(&fs::read(&backend.paths.prudynt_config).unwrap()).unwrap();
    assert!(matches!(
        recorder_start_command(&backend.paths, &config, 0),
        Err(BackendError::Unavailable)
    ));

    let outside = task_temp("recorder-outside-root");
    fs::write(
        &backend.paths.prudynt_config,
        format!(
            "{{\"recorder\":{{\"mount\":{}}}}}",
            Value::String(outside.to_string_lossy().into_owned()).to_json()
        ),
    )
    .unwrap();
    let config = json::parse(&fs::read(&backend.paths.prudynt_config).unwrap()).unwrap();
    assert!(matches!(
        recorder_start_command(&backend.paths, &config, 0),
        Err(BackendError::Protocol)
    ));

    let link = root.join("recordings-link");
    std::os::unix::fs::symlink(&mount, &link).unwrap();
    fs::write(
        &backend.paths.prudynt_config,
        format!(
            "{{\"recorder\":{{\"mount\":{}}}}}",
            Value::String(link.to_string_lossy().into_owned()).to_json()
        ),
    )
    .unwrap();
    let config = json::parse(&fs::read(&backend.paths.prudynt_config).unwrap()).unwrap();
    assert!(matches!(
        recorder_start_command(&backend.paths, &config, 0),
        Err(BackendError::Unavailable)
    ));

    fs::remove_dir_all(outside).unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn sd_state_uses_a_live_mmc_mount_when_the_upstream_sysfs_probe_is_empty() {
    let root = task_temp("sd-mounted-fallback");
    let mountpoint = root.join("mnt/mmcblk0p1");
    let mmc_root = root.join("sys/bus/mmc/devices");
    let block_root = root.join("sys/class/block");
    let proc_mounts = root.join("proc-mounts");
    fs::create_dir_all(&mountpoint).unwrap();
    fs::create_dir_all(&mmc_root).unwrap();
    fs::create_dir_all(block_root.join("mmcblk0/queue")).unwrap();
    fs::write(block_root.join("mmcblk0/size"), b"65536\n").unwrap();
    fs::write(block_root.join("mmcblk0/queue/hw_sector_size"), b"512\n").unwrap();
    fs::write(
        &proc_mounts,
        format!(
            "/dev/mmcblk0p1 {} vfat rw,sync,noatime 0 0\n",
            mountpoint.display()
        ),
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        proc_mounts,
        sys_bus_mmc: mmc_root,
        sys_class_block: block_root,
        media_roots: vec![root.join("mnt")],
        ..CameraPaths::default()
    });

    let response = backend.sd_state().unwrap();
    let value = json::parse(&response.body).unwrap();
    assert_eq!(value.get_path("data.has_sdcard"), Some(&Value::Bool(true)));
    assert_eq!(
        value.get_path("data.device.node").and_then(Value::as_str),
        Some("/dev/mmcblk0")
    );
    assert_eq!(
        value.get_path("data.device.size_bytes").and_then(value_u64),
        Some(33_554_432)
    );
    assert_eq!(
        value
            .get_path("data.debug.detection")
            .and_then(Value::as_str),
        Some("mount-table")
    );
    let filesystem = &value
        .get_path("data.filesystems")
        .and_then(Value::as_array)
        .unwrap()[0];
    assert_eq!(
        filesystem.get_path("device").and_then(Value::as_str),
        Some("/dev/mmcblk0p1")
    );
    assert_eq!(
        filesystem.get_path("filesystem").and_then(Value::as_str),
        Some("vfat")
    );
    assert_eq!(
        filesystem.get_path("writable").and_then(Value::as_bool),
        Some(true)
    );
    fs::remove_dir_all(root).unwrap();
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
fn sd_format_is_queued_and_uses_the_card_bound_worker_protocol() {
    let root = task_temp("sd-format-worker");
    let mountpoint = root.join("mnt/mmcblk0p1");
    let proc_mounts = root.join("proc-mounts");
    let block_root = root.join("sys/class/block");
    let socket = root.join("storage.sock");
    fs::create_dir_all(&mountpoint).unwrap();
    fs::create_dir_all(block_root.join("mmcblk0/device")).unwrap();
    fs::write(block_root.join("mmcblk0/device/cid"), b"0123456789abcdef\n").unwrap();
    fs::write(
        &proc_mounts,
        format!(
            "/dev/mmcblk0p1 {} vfat rw,sync,noatime 0 0\n",
            mountpoint.display()
        ),
    )
    .unwrap();
    let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
    let worker = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut header = [0_u8; 4];
        stream.read_exact(&mut header).unwrap();
        let mut request = vec![0_u8; u32::from_be_bytes(header) as usize];
        stream.read_exact(&mut request).unwrap();
        assert_eq!(&request[..3], &[1, 1, 16]);
        assert_eq!(&request[3..], b"0123456789abcdef");
        let response = b"\x01\x01formatted-fat32";
        stream
            .write_all(&(response.len() as u32).to_be_bytes())
            .unwrap();
        stream.write_all(response).unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        proc_mounts,
        sys_bus_mmc: root.join("missing-sysfs"),
        sys_class_block: block_root,
        storage_worker_socket: socket,
        storage_mountpoint: mountpoint,
        media_roots: vec![root.join("mnt")],
        prudynt_config: root.join("prudynt.json"),
        timelapse_config: root.join("timelapse.json"),
        recorder_ch0_active: root.join("rec0"),
        recorder_ch1_active: root.join("rec1"),
        motion_active: root.join("motion"),
        ..CameraPaths::default()
    });
    assert!(matches!(
        backend.queue_sd_format(br#"{"action":"format","filesystem":"exfat","confirm":"erase"}"#),
        Err(BackendError::Protocol)
    ));
    let queued = backend
        .queue_sd_format(br#"{"action":"format","filesystem":"fat32","confirm":"erase"}"#)
        .unwrap();
    assert_eq!(
        json::parse(&queued.body)
            .unwrap()
            .get_path("status")
            .and_then(Value::as_str),
        Some("queued")
    );
    backend.process_storage_format();
    worker.join().unwrap();
    let state = json::parse(&backend.sd_state().unwrap().body).unwrap();
    assert_eq!(
        state.get_path("data.format.status").and_then(Value::as_str),
        Some("succeeded")
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn recorder_cleanup_protects_only_live_regular_active_paths() {
    let root = task_temp("recorder-cleanup-active");
    let active_recording = root.join("active.mp4");
    let inactive_recording = root.join("inactive.mp4");
    let active_state = root.join("mp4ctl-ch0.active");
    let bad_state = root.join("mp4ctl-ch1.active");
    fs::write(&active_recording, b"active").unwrap();
    fs::write(&inactive_recording, b"inactive").unwrap();
    fs::write(
        &active_state,
        format!("path={}\nduration=30\n", active_recording.display()),
    )
    .unwrap();
    fs::write(&bad_state, b"path=relative.mp4\n").unwrap();

    let protected = active_recorder_paths(&CameraPaths {
        recorder_ch0_active: active_state,
        recorder_ch1_active: bad_state,
        ..CameraPaths::default()
    });
    assert_eq!(protected.len(), 1);
    assert!(is_protected_storage_path(&active_recording, &protected));
    assert!(!is_protected_storage_path(&inactive_recording, &protected));

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
    let backend = PrudyntBackend::new(CameraPaths {
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
fn config_uses_public_shape_and_omits_raw_credentials() {
    let root = task_temp("config");
    let prudynt_config = root.join("prudynt.json");
    let thingino_config = root.join("thingino.json");
    fs::write(
        &prudynt_config,
        br#"{"http":{"password":"__SET_LOCALLY__"},"image":{"brightness":42},"motion":{"enabled":true},"recorder":{"mount":""},"stream0":{"enabled":true},"stream1":{"enabled":false}}"#,
    )
    .unwrap();
    fs::write(
        &thingino_config,
        br#"{"daynight":{"enabled":true,"force_mode":""}}"#,
    )
    .unwrap();
    let paths = CameraPaths {
        prudynt_config,
        thingino_config,
        privacy_active: root.join("privacy.active"),
        ..CameraPaths::default()
    };
    let response = PrudyntBackend::new(paths).config().unwrap();
    let config = json::parse(&response.body).unwrap();
    assert_eq!(config.get_path("backend.raw"), None);
    assert_eq!(
        config.get_path("daynight.enabled"),
        Some(&Value::Bool(true))
    );
    assert_eq!(config.get_path("daynight.force_mode"), Some(&Value::Null));
    assert_eq!(config.get_path("storage.mount"), Some(&Value::Null));
    assert!(
        !response
            .body
            .windows(b"must-not-leak".len())
            .any(|value| value == b"must-not-leak")
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn access_and_network_config_mask_stored_passwords() {
    let root = task_temp("masked-passwords");
    let prudynt_config = root.join("prudynt.json");
    let onvif_config = root.join("onvif.json");
    let wpa_config = root.join("wpa.conf");
    let prudynt_socket = root.join("prudynt.sock");
    let hostname = root.join("hostname");
    let resolv_config = root.join("resolv.conf");
    fs::write(
        &prudynt_config,
        br#"{"rtsp":{"username":"thingino","password":"__SET_LOCALLY__","port":554}}"#,
    )
    .unwrap();
    fs::write(
        &onvif_config,
        br#"{"server":{"username":"thingino","password":"__SET_LOCALLY__","port":1999},"profiles":{"stream0":{"url":"rtsp://127.0.0.1/ch0"},"stream1":{"url":"rtsp://127.0.0.1/ch1"}}}"#,
    )
    .unwrap();
    fs::write(
        &wpa_config,
        b"network={\n\tssid=66697874757265\n\tpsk=\"__SET_LOCALLY__\"\n}\n",
    )
    .unwrap();
    fs::write(&hostname, b"original\n").unwrap();
    fs::write(&resolv_config, b"nameserver 192.0.2.1\n").unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config,
        onvif_config,
        prudynt_socket: prudynt_socket.clone(),
        wpa_config,
        hostname: hostname.clone(),
        resolv_config: resolv_config.clone(),
        network_dir: root.join("network"),
        sys_class_net: root.join("net"),
        ..CameraPaths::default()
    });

    let access = backend.access_config().unwrap();
    let access_json = json::parse(&access.body).unwrap();
    assert_eq!(access_json.get_path("password"), Some(&Value::Null));
    assert_eq!(
        access_json.get_path("password_set"),
        Some(&Value::Bool(true))
    );
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
        !access
            .body
            .windows(secret.len())
            .any(|value| value == secret)
    );
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

    let response = backend
        .update_access_config(
            br#"{"rtsp":{"password":""}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert!(
        response
            .body
            .windows(b"\"password_changed\":false".len())
            .any(|value| value == b"\"password_changed\":false")
    );

    let listener = UnixListener::bind(&prudynt_socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut command = Vec::new();
        stream.read_to_end(&mut command).unwrap();
        let command = json::parse(prudynt_json_frame_payload(&command)).unwrap();
        assert_eq!(
            command.get_path("rtsp.username"),
            Some(&Value::String("viewer".to_owned()))
        );
        assert_eq!(command.get_path("rtsp.port"), Some(&number(8554)));
        assert_eq!(
            command.get_path("stream0.rtsp_endpoint"),
            Some(&Value::String("main".to_owned()))
        );
        stream.write_all(b"{\"code\":200}\n").unwrap();
    });
    let response = backend
        .update_access_config(
            br#"{"username":"viewer","rtsp_port":8554,"rtsp_ch0":"main","rtsp_ch1":"sub","rtsp_mic":"audio","onvif_port":80,"onvif_enabled":true}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert!(
        response
            .body
            .windows(b"\"password_changed\":false".len())
            .any(|value| value == b"\"password_changed\":false")
    );
    server.join().unwrap();
    let onvif = json::parse(&fs::read(&backend.paths.onvif_config).unwrap()).unwrap();
    assert_eq!(
        onvif.get_path("profiles.stream0.url"),
        Some(&Value::String("rtsp://127.0.0.1/main".to_owned()))
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
    let prudynt_config = root.join("prudynt.json");
    fs::write(
        &prudynt_config,
        br#"{"rtsp":{"password":"__SET_LOCALLY__"},"integration":{"token":"__SET_LOCALLY__"},"keep":7}"#,
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config,
        ..CameraPaths::default()
    });

    let response = backend
        .diagnostics_info("/api/v1/diagnostics/info?prudynt")
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
fn daynight_config_matches_webui_semantics() {
    let original = br#"{"daynight":{"enabled":true,"force_mode":"night"},"keep":7}"#;
    for (mode, enabled, force_mode) in [
        (DayNightMode::Day, false, "day"),
        (DayNightMode::Night, false, "night"),
        (DayNightMode::Auto, true, ""),
    ] {
        let updated = json::parse(&daynight_config(original, mode).unwrap()).unwrap();
        assert_eq!(
            updated.get_path("daynight.enabled"),
            Some(&Value::Bool(enabled))
        );
        assert_eq!(
            updated.get_path("daynight.force_mode"),
            Some(&Value::String(force_mode.to_owned()))
        );
        assert_eq!(
            updated.get_path("keep"),
            Some(&Value::Number("7".to_owned()))
        );
    }
}

#[test]
fn forced_daynight_remains_available_without_the_sensing_daemon() {
    let root = task_temp("forced-daynight-without-daemon");
    let thingino_config = root.join("thingino.json");
    let prudynt_config = root.join("prudynt.json");
    let prudynt_socket = root.join("prudynt.sock");
    let daynight_mode = root.join("daynight_mode");
    fs::write(
        &thingino_config,
        br#"{"daynight":{"enabled":true,"force_mode":"","controls":{"color":false,"ircut":false,"ir850":false,"ir940":false,"white":false}},"gpio":{}}"#,
    )
    .unwrap();
    fs::write(&prudynt_config, b"{}\n").unwrap();
    let listener = UnixListener::bind(&prudynt_socket).unwrap();
    let server = thread::spawn(move || {
        for payload in [
            br#"{"image":{"running_mode":0}}"#.as_slice(),
            br#"{"image":{"running_mode":1}}"#.as_slice(),
        ] {
            let expected = prudynt::framed_prudynt_json(payload);
            let (mut stream, _) = listener.accept().unwrap();
            let mut command = Vec::new();
            stream.read_to_end(&mut command).unwrap();
            assert_eq!(command, expected);
            stream.write_all(b"{\"code\":200}\n").unwrap();
        }
    });
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: thingino_config.clone(),
        prudynt_config,
        prudynt_socket,
        daynight_mode: daynight_mode.clone(),
        daynight_pid: root.join("missing-daynightd.pid"),
        daynight_executable: root.join("missing-daynightd"),
        sys_class_gpio: root.join("gpio"),
        ..CameraPaths::default()
    });

    for (mode, expected_mode) in [(DayNightMode::Day, "day"), (DayNightMode::Night, "night")] {
        let response = backend
            .daynight(mode, Instant::now() + Duration::from_secs(1))
            .unwrap();
        let response = json::parse(&response.body).unwrap();
        assert_eq!(
            response.get_path("mode").and_then(Value::as_str),
            Some(expected_mode)
        );
        let config = json::parse(&fs::read(&thingino_config).unwrap()).unwrap();
        assert_eq!(
            config.get_path("daynight.enabled"),
            Some(&Value::Bool(false))
        );
        assert_eq!(
            config
                .get_path("daynight.force_mode")
                .and_then(Value::as_str),
            Some(expected_mode)
        );
        assert_eq!(
            fs::read_to_string(&daynight_mode).unwrap().trim(),
            expected_mode
        );
    }
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn automatic_daynight_still_requires_the_sensing_daemon() {
    let root = task_temp("automatic-daynight-without-daemon");
    let thingino_config = root.join("thingino.json");
    let original = b"{\"daynight\":{\"enabled\":false,\"force_mode\":\"day\"}}\n";
    fs::write(&thingino_config, original).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: thingino_config.clone(),
        daynight_pid: root.join("missing-daynightd.pid"),
        daynight_executable: root.join("missing-daynightd"),
        ..CameraPaths::default()
    });

    assert!(matches!(
        backend.daynight(DayNightMode::Auto, Instant::now() + Duration::from_secs(1)),
        Err(BackendError::Unavailable)
    ));
    assert_eq!(fs::read(&thingino_config).unwrap(), original);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn color_control_matches_the_ingenic_isp_mode_contract() {
    assert_eq!(actions::color_running_mode(true), 0);
    assert_eq!(actions::color_running_mode(false), 1);
    assert_eq!(actions::IRCUT_PULSE_DURATION, Duration::from_millis(100));
}

#[test]
fn utc_template_expansion_is_bounded_and_rejects_traversal() {
    assert_eq!(
        expand_utc_template("%Y%m%d/%H/%Y%m%dT%H%M%S.jpg", 0).unwrap(),
        PathBuf::from("19700101/00/19700101T000000.jpg")
    );
    assert!(expand_utc_template("../escape.jpg", 0).is_err());
    assert!(expand_utc_template("%Q.jpg", 0).is_err());
}

#[test]
fn file_api_lists_and_edits_without_following_symlinks() {
    let root = task_temp("files");
    let text_path = root.join("notes.txt");
    fs::write(&text_path, b"one\ntwo\n").unwrap();
    let listing = file_directory(root.to_str().unwrap(), std::slice::from_ref(&root)).unwrap();
    let listing = json::parse(&listing.body).unwrap();
    assert_eq!(
        listing.get_path("entries.0.name"),
        None,
        "the JSON helper deliberately has no array path traversal"
    );
    assert!(listing.to_json().contains("notes.txt"));

    let paths = CameraPaths {
        media_roots: vec![root.clone()],
        ..CameraPaths::default()
    };
    let backend = PrudyntBackend::new(paths);
    let target = format!("/api/v1/files/text?file={}", text_path.to_string_lossy());
    let response = backend.text_file("GET", &target, b"").unwrap();
    assert!(
        response
            .body
            .windows(12)
            .any(|value| value == b"b25lCnR3bwo=")
    );
    backend.text_file("POST", &target, b"updated\n").unwrap();
    assert_eq!(fs::read(&text_path).unwrap(), b"updated\n");

    let link = root.join("link.txt");
    std::os::unix::fs::symlink(&text_path, &link).unwrap();
    let link_target = format!("/api/v1/files/text?file={}", link.to_string_lossy());
    assert!(backend.text_file("GET", &link_target, b"").is_err());
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn gpio_api_normalizes_config_and_toggles_without_helpers() {
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
    let backend = PrudyntBackend::new(CameraPaths {
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

    backend
        .control(
            br#"{"cmd":"ir850","val":"toggle"}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert_eq!(
        fs::read(sys_class_gpio.join("gpio61/value")).unwrap(),
        b"1\n"
    );
    assert_eq!(backend.light_state(&saved, "ir850"), number(1));
    backend.set_ircut(true).unwrap();
    assert_eq!(
        fs::read(sys_class_gpio.join("gpio50/value")).unwrap(),
        b"0\n"
    );
    assert_eq!(
        fs::read(sys_class_gpio.join("gpio49/value")).unwrap(),
        b"0\n"
    );
    assert_eq!(fs::read(root.join("ircut_mode")).unwrap(), b"1\n");
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn day_mode_engages_the_filter_before_the_single_isp_command() {
    let root = task_temp("day-mode-order");
    let thingino_config = root.join("thingino.json");
    let prudynt_socket = root.join("prudynt.sock");
    let sys_class_gpio = root.join("gpio");
    let ircut_state = root.join("ircut_mode");
    let daynight_mode = root.join("daynight_mode");
    for pin in [49, 50, 61] {
        fs::create_dir_all(sys_class_gpio.join(format!("gpio{pin}"))).unwrap();
        fs::write(sys_class_gpio.join(format!("gpio{pin}/value")), b"0\n").unwrap();
    }
    fs::write(sys_class_gpio.join("gpio61/value"), b"1\n").unwrap();
    fs::write(
        &thingino_config,
        br#"{"daynight":{"controls":{"color":false}},"gpio":{"ir850":61,"ircut":"50 49"}}"#,
    )
    .unwrap();
    let listener = UnixListener::bind(&prudynt_socket).unwrap();
    let observed_gpio = sys_class_gpio.clone();
    let observed_ircut = ircut_state.clone();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut command = Vec::new();
        stream.read_to_end(&mut command).unwrap();
        assert_prudynt_json_frame(&command, br#"{"image":{"running_mode":0}}"#);
        assert_eq!(
            fs::read(observed_gpio.join("gpio61/value")).unwrap(),
            b"0\n"
        );
        assert!(!observed_ircut.exists());
        stream.write_all(b"{\"code\":200}\n").unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_socket,
        thingino_config,
        sys_class_gpio,
        ircut_state,
        daynight_mode: daynight_mode.clone(),
        ..CameraPaths::default()
    });
    backend
        .apply_daynight_mode(DayNightMode::Day, Instant::now() + Duration::from_secs(1))
        .unwrap();
    server.join().unwrap();
    assert_eq!(fs::read(daynight_mode).unwrap(), b"day\n");
    assert_eq!(fs::read(root.join("ircut_mode")).unwrap(), b"1\n");
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn storage_paths_are_fail_closed() {
    assert!(safe_mount_path("/mnt/mmcblk0p1"));
    assert!(safe_mount_path("/media/card"));
    assert!(!safe_mount_path("/overlay"));
    assert!(!safe_mount_path("/mnt/../etc"));
    assert!(safe_storage_component("camera/records"));
    assert!(!safe_storage_component("../records"));
    assert!(!safe_storage_component("/absolute"));
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
fn persistent_mutations_share_the_config_lock() {
    for target in [
        "/api/v1/config/network",
        "/api/v1/config/crontab",
        "/api/v1/config/daynight",
        "/api/v1/prudynt",
        "/api/v1/imaging",
        "/api/v1/recorder",
        "/api/v1/services/send/config",
        "/api/v1/files?rm=%2Fmnt%2Fclip.mp4",
        "/api/v1/files/text?file=%2Fmnt%2Fnotes.txt",
        "/api/v1/actions/factory-reset",
    ] {
        assert!(serialized_mutation("POST", target), "{target}");
    }
    assert!(!serialized_mutation("GET", "/api/v1/config/network"));
    assert!(!serialized_mutation("POST", "/api/v1/actions/control"));
    assert!(!serialized_mutation("POST", "/api/v1/diagnostics"));
}

#[test]
fn config_domains_reject_undocumented_put() {
    let backend = PrudyntBackend::new(CameraPaths::default());
    for target in [
        "/api/v1/config/admin",
        "/api/v1/config/webui",
        "/api/v1/config/rsyslog",
        "/api/v1/config/ha",
        "/api/v1/config/mqtt_sub",
        "/api/v1/config/daynight",
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
    let backend = PrudyntBackend::new(CameraPaths {
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
fn prudynt_config_route_rejects_action_and_unknown_domains() {
    let backend = PrudyntBackend::new(CameraPaths::default());
    for body in [
        br#"{"action":{"restart_thread":7}}"#.as_slice(),
        br#"{"unknown":{}}"#.as_slice(),
        br#"{"image":1}"#.as_slice(),
    ] {
        assert!(matches!(
            backend.update_prudynt_config(body, Instant::now()),
            Err(BackendError::Protocol)
        ));
    }
}

#[test]
fn unchanged_prudynt_fields_are_removed_recursively() {
    let current =
        json::parse(br#"{"image":{"brightness":42,"contrast":7},"stream0":{"enabled":true}}"#)
            .unwrap();
    let mut update = json::parse(
        br#"{"image":{"brightness":42,"contrast":8},"stream0":{"enabled":true},"osd":{"enabled":false}}"#,
    )
    .unwrap();
    assert!(retain_changed_fields(&mut update, Some(&current)));
    assert_eq!(
        update,
        json::parse(br#"{"image":{"contrast":8},"osd":{"enabled":false}}"#).unwrap()
    );

    let mut unchanged = current.clone();
    assert!(!retain_changed_fields(&mut unchanged, Some(&current)));
    assert_eq!(unchanged, json::parse(br#"{}"#).unwrap());
}

#[test]
fn prudynt_config_skips_unchanged_runtime_calls_and_sends_only_delta() {
    let root = task_temp("prudynt-config-delta");
    let prudynt_config = root.join("prudynt.json");
    let prudynt_socket = root.join("prudynt.sock");
    fs::write(
        &prudynt_config,
        b"{\"image\":{\"brightness\":42,\"contrast\":7},\"stream0\":{\"enabled\":true,\"fps\":15,\"buffers\":1}}\n",
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: prudynt_config.clone(),
        prudynt_socket: prudynt_socket.clone(),
        ..CameraPaths::default()
    });

    let response = backend
        .update_prudynt_config(
            br#"{"image":{"brightness":42,"contrast":7},"stream0":{"enabled":true,"fps":15,"buffers":1}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert_eq!(response.body, b"{\"status\":\"ok\"}\n");

    let listener = UnixListener::bind(&prudynt_socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut command = Vec::new();
        stream.read_to_end(&mut command).unwrap();
        assert_prudynt_json_frame(&command, br#"{"image":{"contrast":8}}"#);
        stream.write_all(b"{\"code\":200}\n").unwrap();
    });
    let response = backend
        .update_prudynt_config(
            br#"{"image":{"brightness":42,"contrast":8},"stream0":{"enabled":true,"fps":15,"buffers":1}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert_eq!(response.body, b"{\"status\":\"ok\"}\n");
    server.join().unwrap();
    let stored = json::parse(&fs::read(&prudynt_config).unwrap()).unwrap();
    assert_eq!(stored.get_path("image.brightness"), Some(&number(42)));
    assert_eq!(stored.get_path("image.contrast"), Some(&number(8)));
    assert_eq!(stored.get_path("stream0.enabled"), Some(&Value::Bool(true)));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn daynight_route_keeps_canonical_get_and_strict_post_validation() {
    let root = task_temp("daynight-route");
    let thingino_config = root.join("thingino.json");
    let original = br#"{"daynight":{"enabled":true,"secret":"hidden","controls":{"color":false}}}"#;
    fs::write(&thingino_config, original).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config: thingino_config.clone(),
        ..CameraPaths::default()
    });

    let response = backend
        .api_request(
            "GET",
            "/api/v1/config/daynight",
            b"",
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap()
        .unwrap();
    let response = json::parse(&response.body).unwrap();
    assert_eq!(response.get_path("enabled"), Some(&Value::Bool(true)));
    assert_eq!(
        response.get_path("controls.ircut"),
        Some(&Value::Bool(true))
    );
    assert_eq!(response.get_path("sun.sunrise_offset"), Some(&number(0)));
    assert_eq!(response.get_path("secret"), None);
    assert_eq!(response.get_path("script_path"), None);

    assert_eq!(
        backend
            .api_request(
                "POST",
                "/api/v1/config/daynight",
                br#"{"unknown":true}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap(),
        Err(BackendError::Protocol)
    );
    assert_eq!(
        backend
            .api_request(
                "POST",
                "/api/v1/config/daynight",
                br#"{"script_path":"/run/attacker"}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap(),
        Err(BackendError::Protocol)
    );
    assert_eq!(fs::read(&thingino_config).unwrap(), original);
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
    let backend = PrudyntBackend::new(CameraPaths {
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

#[test]
fn daynight_sensor_runtime_uses_active_dlink_threshold_names() {
    let root = task_temp("daynight-sensor-thresholds");
    let thingino_config = root.join("thingino.json");
    let daynight_sensors = root.join("daynight-sensors.json");
    fs::write(
        &thingino_config,
        br#"{"daynight":{"night_threshold":25,"day_threshold":50}}"#,
    )
    .unwrap();
    fs::write(&daynight_sensors, br#"{"total_gain":17.5}"#).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        thingino_config,
        daynight_sensors,
        ..CameraPaths::default()
    });

    let response = backend.daynight_sensors().unwrap();
    let response = json::parse(&response.body).unwrap();
    assert_eq!(
        response.get_path("night_threshold_pct"),
        Some(&Value::Number("25".to_owned()))
    );
    assert_eq!(
        response.get_path("day_threshold_pct"),
        Some(&Value::Number("50".to_owned()))
    );
    assert_eq!(
        response.get_path("current.total_gain"),
        Some(&Value::Number("17.5".to_owned()))
    );
    fs::remove_dir_all(root).unwrap();
}
