use super::*;
use std::io::{Read, Write};
use std::os::unix::net::UnixListener;
use std::process;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn task_temp(name: &str) -> PathBuf {
    let root = std::env::var_os("TMPDIR").expect("TMPDIR is required by repository policy");
    fs::create_dir_all(&root).unwrap();
    let path = PathBuf::from(root).join(format!(
        "ta-prudynt-{}-{}-{name}",
        process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::AcqRel)
    ));
    fs::create_dir(&path).unwrap();
    path
}

#[test]
fn invalid_prudynt_update_is_rejected_before_socket_or_config_write() {
    let root = task_temp("schema-reject");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    let original = b"{\"stream1\":{\"enabled\":false,\"fps\":15,\"buffers\":1}}\n";
    fs::write(&config, original).unwrap();

    let listener = UnixListener::bind(&socket).unwrap();
    listener.set_nonblocking(true).unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    let unsafe_script = format!(r#"{{"motion":{{"script_path":"/{}/x"}}}}"#, "tmp").into_bytes();
    for body in [
        br#"{"stream0":{"format":"VP9"}}"#.as_slice(),
        br#"{"audio":{"mic_vol":121}}"#.as_slice(),
        br#"{"stream1":{"fps":31}}"#.as_slice(),
        unsafe_script.as_slice(),
        br#"{"stream1":{"enabled":true,"fps":0,"buffers":1}}"#.as_slice(),
        br#"{"stream1":{"enabled":true,"fps":15,"buffers":-1}}"#.as_slice(),
    ] {
        assert_eq!(
            backend.update_prudynt_config(body, Instant::now() + Duration::from_secs(1)),
            Err(BackendError::Protocol)
        );
        assert_eq!(fs::read(&config).unwrap(), original);
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    }
    assert_eq!(
        backend.prudynt_json(
            br#"{"stream0":{"mode":"INVALID"}}"#,
            Instant::now() + Duration::from_secs(1)
        ),
        Err(BackendError::Protocol)
    );
    assert!(matches!(
        listener.accept(),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
    ));
    assert_eq!(fs::read(&config).unwrap(), original);

    fs::remove_dir_all(root).unwrap();
}

#[test]
fn media_metrics_route_uses_bounded_prudynt_ipc_without_a_child_process() {
    let root = task_temp("media-metrics");
    let socket = root.join("prudynt.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut client, _) = listener.accept().unwrap();
        let mut command = Vec::new();
        client.read_to_end(&mut command).unwrap();
        assert_eq!(command, b"METRICS\n");
        client
            .write_all(
                b"# TYPE prudynt_rtsp_clients gauge\n\
prudynt_rtsp_clients 2\n\
prudynt_rtsp_queue_bytes 4096\n\
prudynt_mjpeg_rejections_total 3\n\
prudynt_jpeg_worker_running{channel=\"0\"} 1\n",
            )
            .unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_socket: socket,
        ..CameraPaths::default()
    });
    let response = backend
        .api_request(
            "GET",
            "/api/v1/runtime/media/metrics",
            b"",
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap()
        .unwrap();
    assert_eq!(response.content_type, "text/plain; version=0.0.4");
    let expected = b"prudynt_rtsp_queue_bytes 4096";
    assert!(
        response
            .body
            .windows(expected.len())
            .any(|part| part == expected)
    );
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn media_metrics_rejects_non_metric_backend_output() {
    let root = task_temp("invalid-media-metrics");
    let socket = root.join("prudynt.sock");
    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut client, _) = listener.accept().unwrap();
        let mut command = Vec::new();
        client.read_to_end(&mut command).unwrap();
        client.write_all(b"not metrics\n").unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_socket: socket,
        ..CameraPaths::default()
    });
    assert_eq!(
        backend.prudynt_metrics(Instant::now() + Duration::from_secs(1)),
        Err(BackendError::Protocol)
    );
    server.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn prudynt_json_uses_versioned_length_framing() {
    assert_eq!(
        framed_prudynt_json(br#"{"x":1}"#),
        b"PRUDYNT/1 JSON 7\n{\"x\":1}"
    );
}

#[test]
fn prudynt_motion_get_omits_non_writable_script_path() {
    let root = task_temp("motion-get");
    let config = root.join("prudynt.json");
    fs::write(
        &config,
        br#"{"motion":{"enabled":false,"sensitivity":1,"script_path":"/usr/sbin/motion"}}"#,
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config,
        ..CameraPaths::default()
    });

    let response = backend.prudynt_domain("motion").unwrap();
    let value = json::parse(&response.body).unwrap();
    assert_eq!(value.get_path("enabled"), Some(&Value::Bool(false)));
    assert_eq!(
        value.get_path("sensitivity"),
        Some(&Value::Number("1".to_owned()))
    );
    assert_eq!(value.get_path("script_path"), None);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn prudynt_osd_and_privacy_gets_are_canonical_for_the_dlink_profile() {
    let root = task_temp("canonical-osd-privacy");
    let config = root.join("prudynt.json");
    fs::write(
        &config,
        br#"{"osd":{"burnin":{"enabled":false,"format":"%F %T"},"privacy":{"enabled":true},"sei":{"enabled":false,"entries":{}}}}"#,
    )
    .unwrap();
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        ..CameraPaths::default()
    });

    let osd = json::parse(&backend.prudynt_domain("osd").unwrap().body).unwrap();
    assert_eq!(osd.get_path("burnin.scale"), Some(&number(0)));
    assert_eq!(
        osd.get_path("burnin.fill_color"),
        Some(&Value::String("#ffffffff".to_owned()))
    );
    let privacy = json::parse(&backend.prudynt_domain("privacy").unwrap().body).unwrap();
    for field in ["enabled", "stream0_enabled", "stream1_enabled"] {
        assert_eq!(privacy.get_path(field), Some(&Value::Bool(true)));
    }
    assert_eq!(
        fs::read(&config).unwrap(),
        br#"{"osd":{"burnin":{"enabled":false,"format":"%F %T"},"privacy":{"enabled":true},"sei":{"enabled":false,"entries":{}}}}"#
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_partial_update_preserves_stored_script_path() {
    let root = task_temp("motion-script-preserve");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    fs::write(
        &config,
        br#"{"motion":{"enabled":false,"sensitivity":1,"script_path":"/usr/sbin/motion"}}"#,
    )
    .unwrap();

    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = Vec::new();
        stream.read_to_end(&mut request).unwrap();
        let payload = br#"{"motion":{"enabled":true}}"#;
        let expected = framed_prudynt_json(payload);
        assert_eq!(request, expected);
        stream.write_all(br#"{"code":200}"#).unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    backend
        .update_prudynt_config(
            br#"{"motion":{"enabled":true}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    server.join().unwrap();

    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("motion.script_path"),
        Some(&Value::String("/usr/sbin/motion".to_owned()))
    );
    assert_eq!(stored.get_path("motion.enabled"), Some(&Value::Bool(true)));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn unchanged_enabled_motion_reapplies_when_runtime_is_not_monitoring() {
    let root = task_temp("motion-runtime-reconcile");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    let original = b"{\"motion\":{\"enabled\":true,\"sensitivity\":1}}\n";
    fs::write(&config, original).unwrap();

    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = Vec::new();
        stream.read_to_end(&mut request).unwrap();
        assert_eq!(
            request,
            framed_prudynt_json(br#"{"motion":{"enabled":true}}"#)
        );
        stream.write_all(br#"{"code":200}"#).unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });
    assert!(!backend.motion.snapshot().monitoring);

    backend
        .update_prudynt_config(
            br#"{"motion":{"enabled":true}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    server.join().unwrap();
    assert_eq!(fs::read(&config).unwrap(), original);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn motion_lifecycle_fields_persist_without_invalid_prudynt_live_keys() {
    let root = task_temp("motion-lifecycle-fields");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    fs::write(
        &config,
        br#"{"motion":{"cooldown_time":5,"post_time":0,"send2mqtt":false,"sensitivity":1,"video_length":10}}"#,
    )
    .unwrap();

    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = Vec::new();
        stream.read_to_end(&mut request).unwrap();
        let payload = br#"{"motion":{"sensitivity":2}}"#;
        let expected = framed_prudynt_json(payload);
        assert_eq!(request, expected);
        stream.write_all(br#"{"code":200}"#).unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    backend
        .update_prudynt_config(
            br#"{"motion":{"cooldown_time":7,"post_time":10,"send2mqtt":true,"sensitivity":2,"video_length":11}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    server.join().unwrap();
    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(stored.get_path("motion.cooldown_time"), Some(&number(7)));
    assert_eq!(stored.get_path("motion.post_time"), Some(&number(10)));
    assert_eq!(
        stored.get_path("motion.send2mqtt"),
        Some(&Value::Bool(true))
    );
    assert_eq!(stored.get_path("motion.sensitivity"), Some(&number(2)));
    assert_eq!(stored.get_path("motion.video_length"), Some(&number(11)));

    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: root.join("absent.sock"),
        ..CameraPaths::default()
    });
    backend
        .update_prudynt_config(
            br#"{"motion":{"post_time":12,"video_length":12}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(stored.get_path("motion.post_time"), Some(&number(12)));
    assert_eq!(stored.get_path("motion.video_length"), Some(&number(12)));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn disabled_stream_fps_zero_and_buffer_sentinel_are_persisted_after_live_update() {
    let root = task_temp("schema-disabled-stream");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    fs::write(
        &config,
        b"{\"stream1\":{\"enabled\":false,\"fps\":15,\"buffers\":1}}\n",
    )
    .unwrap();

    let listener = UnixListener::bind(&socket).unwrap();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = Vec::new();
        stream.read_to_end(&mut request).unwrap();
        let payload = br#"{"stream1":{"buffers":-1,"fps":0}}"#;
        let expected = framed_prudynt_json(payload);
        assert_eq!(request, expected);
        stream.write_all(b"{\"code\":200}\n").unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    let response = backend
        .update_prudynt_config(
            br#"{"stream1":{"fps":0,"buffers":-1}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    assert_eq!(response.body, b"{\"status\":\"ok\"}\n");
    server.join().unwrap();

    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("stream1.enabled"),
        Some(&Value::Bool(false))
    );
    assert_eq!(
        stored.get_path("stream1.fps"),
        Some(&Value::Number("0".to_owned()))
    );
    assert_eq!(
        stored.get_path("stream1.buffers"),
        Some(&Value::Number("-1".to_owned()))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn canonical_privacy_update_applies_live_and_persists_under_osd() {
    let root = task_temp("privacy-normalize");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    fs::write(&config, b"{\"osd\":{\"privacy\":{\"enabled\":false}}}\n").unwrap();
    let original_inode = fs::metadata(&config).unwrap().ino();
    let listener = UnixListener::bind(&socket).unwrap();
    let config_seen_by_server = config.clone();
    let server = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = Vec::new();
        stream.read_to_end(&mut request).unwrap();
        let payload = br#"{"privacy":{"enabled":true}}"#;
        let expected = framed_prudynt_json(payload);
        assert_eq!(request, expected);
        let stored = json::parse(&fs::read(&config_seen_by_server).unwrap()).unwrap();
        assert_eq!(
            stored.get_path("osd.privacy.enabled"),
            Some(&Value::Bool(false))
        );
        stream.write_all(b"{\"code\":200}\n").unwrap();
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    backend
        .update_prudynt_config(
            br#"{"privacy":{"enabled":true,"stream0_enabled":true,"stream1_enabled":true}}"#,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
    server.join().unwrap();
    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("osd.privacy.enabled"),
        Some(&Value::Bool(true))
    );
    assert_ne!(fs::metadata(&config).unwrap().ino(), original_inode);
    assert_eq!(stored.get_path("privacy"), None);
    assert!(
        backend
            .update_prudynt_config(
                br#"{"privacy":{"stream0_enabled":true,"stream1_enabled":false}}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .is_err()
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn privacy_live_step_keeps_other_osd_changes_separate() {
    let current =
        json::parse(br#"{"osd":{"burnin":{"enabled":true},"privacy":{"enabled":false}}}"#).unwrap();
    let update = json::parse(
        br#"{"osd":{"burnin":{"enabled":false},"privacy":{"enabled":true}},"privacy":{"enabled":true}}"#,
    )
    .unwrap();

    let steps = prudynt_live_steps(&update, &current).unwrap();
    assert_eq!(steps.len(), 2);
    assert_eq!(
        steps[0],
        (
            json::parse(br#"{"privacy":{"enabled":true}}"#).unwrap(),
            json::parse(br#"{"privacy":{"enabled":false}}"#).unwrap(),
        )
    );
    assert_eq!(
        steps[1],
        (
            json::parse(br#"{"osd":{"burnin":{"enabled":false}}}"#).unwrap(),
            json::parse(br#"{"osd":{"burnin":{"enabled":true}}}"#).unwrap(),
        )
    );
}

#[test]
fn failed_live_privacy_apply_rolls_back_persisted_state() {
    let root = task_temp("privacy-live-rollback");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    fs::write(&config, b"{\"osd\":{\"privacy\":{\"enabled\":false}}}\n").unwrap();
    let listener = UnixListener::bind(&socket).unwrap();
    let config_seen_by_server = config.clone();
    let server = thread::spawn(move || {
        for (expected_payload, response) in [
            (
                br#"{"privacy":{"enabled":true}}"#.as_slice(),
                br#"{"error":"privacy_apply_failed"}"#.as_slice(),
            ),
            (
                br#"{"privacy":{"enabled":false}}"#.as_slice(),
                br#"{"code":200}"#.as_slice(),
            ),
        ] {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            stream.read_to_end(&mut request).unwrap();
            assert_eq!(request, framed_prudynt_json(expected_payload));
            let stored = json::parse(&fs::read(&config_seen_by_server).unwrap()).unwrap();
            assert_eq!(
                stored.get_path("osd.privacy.enabled"),
                Some(&Value::Bool(false))
            );
            stream.write_all(response).unwrap();
        }
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    assert!(
        backend
            .update_prudynt_config(
                br#"{"privacy":{"enabled":true,"stream0_enabled":true,"stream1_enabled":true}}"#,
                Instant::now() + Duration::from_secs(1),
            )
            .is_err()
    );
    server.join().unwrap();
    let stored = json::parse(&fs::read(&config).unwrap()).unwrap();
    assert_eq!(
        stored.get_path("osd.privacy.enabled"),
        Some(&Value::Bool(false))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn failed_second_live_domain_rolls_back_first_before_persisting() {
    let root = task_temp("multi-domain-live-rollback");
    let config = root.join("prudynt.json");
    let socket = root.join("prudynt.sock");
    let original = b"{\"image\":{\"brightness\":100},\"stream0\":{\"bitrate\":2000,\"buffers\":1,\"enabled\":true,\"fps\":15}}\n";
    fs::write(&config, original).unwrap();
    let listener = UnixListener::bind(&socket).unwrap();
    let config_seen_by_server = config.clone();
    let server = thread::spawn(move || {
        for (expected_payload, response) in [
            (
                br#"{"image":{"brightness":120}}"#.as_slice(),
                br#"{"code":200}"#.as_slice(),
            ),
            (
                br#"{"stream0":{"bitrate":2500}}"#.as_slice(),
                br#"{"error":"encoder_apply_failed"}"#.as_slice(),
            ),
            (
                br#"{"stream0":{"bitrate":2000}}"#.as_slice(),
                br#"{"code":200}"#.as_slice(),
            ),
            (
                br#"{"image":{"brightness":100}}"#.as_slice(),
                br#"{"code":200}"#.as_slice(),
            ),
        ] {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            stream.read_to_end(&mut request).unwrap();
            assert_eq!(request, framed_prudynt_json(expected_payload));
            assert_eq!(fs::read(&config_seen_by_server).unwrap(), original);
            stream.write_all(response).unwrap();
        }
    });
    let backend = PrudyntBackend::new(CameraPaths {
        prudynt_config: config.clone(),
        prudynt_socket: socket,
        ..CameraPaths::default()
    });

    assert_eq!(
        backend.update_prudynt_config(
            br#"{"image":{"brightness":120},"stream0":{"bitrate":2500}}"#,
            Instant::now() + Duration::from_secs(2),
        ),
        Err(BackendError::Unavailable)
    );
    server.join().unwrap();
    assert_eq!(fs::read(&config).unwrap(), original);
    fs::remove_dir_all(root).unwrap();
}
