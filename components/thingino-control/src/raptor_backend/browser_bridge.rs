//! Opt-in host bridge: real Control routing/adapter and stateful Unix IPC
//! fault doubles. It does not start a camera daemon or touch production paths.
use super::*;
use std::net::TcpListener;
use std::os::unix::net::UnixListener;
use std::sync::{Arc, atomic::AtomicBool};
use std::thread;

fn timezone_bridge(request: &Value, root: &std::path::Path, daemon: &str) -> String {
    let path = root.join(format!("timezone-{daemon}"));
    if request.get_path("cmd").and_then(Value::as_str) == Some("timezone-reload") {
        let expected = request
            .get_path("expected_rule")
            .and_then(Value::as_str)
            .unwrap();
        assert_eq!(
            fs::read_to_string(root.join("TZ")).unwrap(),
            format!("{expected}\n")
        );
        if daemon == "rod" && !root.join("timezone-save-failed").exists() {
            fs::write(root.join("timezone-save-failed"), "1").unwrap();
            return r#"{"status":"error"}"#.into();
        }
        fs::write(&path, expected).unwrap();
    }
    let rule = fs::read_to_string(path).unwrap_or("UTC0".into());
    timezone::tests::reply(&rule, &["rvd", "rhd", "rad", "ric", "rod"])
}

fn privacy_bridge(root: &std::path::Path) -> String {
    let enabled = fs::read_to_string(root.join("privacy-live")).unwrap_or("false".into());
    format!(
        r#"{{"status":"ok","persistence":"checked-startup","available":true,"enabled":{enabled}}}"#
    )
}

#[test]
#[ignore = "started by the Raptor browser integration driver"]
fn native_recovery_bridge() {
    let root = PathBuf::from(std::env::var_os("RAPTOR_BROWSER_ROOT").unwrap());
    let listener = UnixListener::bind(root.join("rvd.sock")).unwrap();
    listener.set_nonblocking(true).unwrap();
    let shutdown = Arc::new(AtomicBool::new(false));
    let daemon_shutdown = Arc::clone(&shutdown);
    let daemon_root = root.clone();
    let daemon = thread::spawn(move || {
        let mut stream_gop = 30;
        let mut stream_fps = [20_u64; 2];
        let mut fps_pending = [false; 2];
        let mut fps_recovery = [false; 2];
        let mut fps_sdk_calls = 0;
        let mut gop_save_failed = false;
        fs::write(daemon_root.join("gop-disk"), "30").unwrap();
        let mut privacy: Option<bool> = Some(false);
        let mut motion = true;
        let mut motion_sensitivity = 3;
        let mut motion_roi: Option<[u64; 4]> = None;
        let mut roi_save_failed = false;
        let mut sensitivity_save_failed = false;
        let mut receiving = true;
        let mut privacy_attempts = 0;
        let mut privacy_save_failed = false;
        fs::write(
            daemon_root.join("privacy-disk.json"),
            r#"{"enabled":"false"}"#,
        )
        .unwrap();
        let mut motion_failed = false;
        let mut image_values = std::collections::BTreeMap::from([
            ("brightness".to_owned(), Value::Number("128".into())),
            ("contrast".to_owned(), Value::Number("128".into())),
            ("saturation".to_owned(), Value::Number("128".into())),
            ("sharpness".to_owned(), Value::Number("128".into())),
        ]);
        let mut image_save_failed = false;
        let mut motion_save_failed = false;
        let mut config_domain = "image";
        fs::write(
            daemon_root.join("motion-disk.json"),
            r#"{"enabled":"false"}"#,
        )
        .unwrap();
        while !daemon_shutdown.load(Ordering::Acquire) {
            let (mut socket, _) = match listener.accept() {
                Ok(socket) => socket,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            socket
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            // A cancelled or timed-out client must not kill the daemon double
            // and turn later independent checks into connection failures.
            let mut header = [0; 2];
            if socket.read_exact(&mut header).is_err() {
                continue;
            }
            let mut body = vec![0; u16::from_be_bytes(header) as usize];
            if socket.read_exact(&mut body).is_err() {
                continue;
            }
            let request = crate::json::parse(&body).unwrap();
            let reply = match request.get_path("cmd").and_then(Value::as_str).unwrap() {
                "timezone-status" | "timezone-reload" => {
                    timezone_bridge(&request, &daemon_root, "rvd")
                }
                "osd-apply-state" => {
                    fs::read_to_string(daemon_root.join("osd-receipt.json")).unwrap()
                }
                "status" => {
                    let mode =
                        fs::read_to_string(daemon_root.join("media-case")).unwrap_or("both".into());
                    fs::read_to_string(daemon_root.join(format!("{mode}.rvd.json"))).unwrap()
                }
                "privacy" => {
                    privacy_attempts += 1;
                    if matches!(privacy_attempts, 1 | 3) {
                        privacy = None;
                        r#"{"status":"error","complete":false,"video_ok":false,"audio_ok":true,"jpeg_ok":true}"#.to_owned()
                    } else {
                        privacy =
                            Some(request.get_path("value").and_then(Value::as_str) == Some("on"));
                        r#"{"status":"ok","complete":true}"#.to_owned()
                    }
                }
                "set-privacy-config" => {
                    config_domain = "privacy";
                    let enabled = request
                        .get_path("enabled")
                        .and_then(Value::as_bool)
                        .unwrap();
                    fs::write(daemon_root.join("privacy-live"), enabled.to_string()).unwrap();
                    r#"{"status":"ok","complete":true}"#.into()
                }
                "privacy-status" => {
                    let persistent_case = daemon_root.join("privacy-case").exists();
                    let state = if persistent_case {
                        fs::read_to_string(daemon_root.join("privacy-live"))
                            .unwrap_or("false".into())
                    } else {
                        privacy.map_or("null".into(), |value| value.to_string())
                    };
                    let retained = if persistent_case {
                        state.clone()
                    } else {
                        privacy.unwrap_or(true).to_string()
                    };
                    format!(
                        r#"{{"status":"ok","persistence":"checked-startup","supported":true,"video":[{state},{retained}],"audio_required":{persistent_case},"jpeg_required":{persistent_case}}}"#
                    )
                }
                "ivs-enable" => {
                    let enabled = request.get_path("value").and_then(Value::as_bool).unwrap();
                    motion = enabled;
                    if !enabled && !motion_failed {
                        motion_failed = true;
                        r#"{"status":"error","message":"stop receive failed"}"#.to_owned()
                    } else {
                        receiving = enabled;
                        format!(r#"{{"status":"ok","active":{enabled}}}"#)
                    }
                }
                "ivs-status" => {
                    if let Ok(reply) =
                        fs::read_to_string(daemon_root.join("motion-event-status.json"))
                    {
                        if reply == "disconnect" {
                            continue;
                        }
                        reply
                    } else {
                        format!(
                            r#"{{"status":"ok","supported":true,"active":{motion},"receiving":{receiving},"motion":false}}"#
                        )
                    }
                }
                "get-imaging" => {
                    let fields = image_values.iter().map(|(name, value)| format!(
                        r#""{name}":{{"supported":true,"available":true,"min":0,"max":255,"value":{}}}"#, value.to_json()
                    )).collect::<Vec<_>>().join(",");
                    format!(
                        r#"{{"status":"ok","persistence":"checked-config","fields":{{{fields}}}}}"#
                    )
                }
                "get-stream-fps" | "set-stream-fps" => {
                    if !daemon_root.join("fps-enabled").exists() {
                        r#"{"status":"error","error":"unknown command"}"#.into()
                    } else {
                        let id = request
                            .get_path("stream_id")
                            .unwrap()
                            .to_json()
                            .parse::<usize>()
                            .unwrap();
                        assert!(id <= 1);
                        let set = request.get_path("cmd").and_then(Value::as_str)
                            == Some("set-stream-fps");
                        if set && daemon_root.join("fps-disconnect").exists() {
                            continue;
                        }
                        let mut status = "ok";
                        let mut applied = "true";
                        if set {
                            let fps = request
                                .get_path("fps")
                                .unwrap()
                                .to_json()
                                .parse::<u64>()
                                .unwrap();
                            assert!((1..=30).contains(&fps));
                            if daemon_root.join("fps-recovery").exists() {
                                fps_recovery[id] = true;
                            }
                            if daemon_root.join("fps-sdk-fail").exists() {
                                status = "error";
                                applied = "false";
                            } else if !fps_recovery[id] {
                                if stream_fps[id] != fps {
                                    fps_sdk_calls += 1;
                                }
                                stream_fps[id] = fps;
                                fs::write(
                                    daemon_root.join("fps-sdk-count"),
                                    fps_sdk_calls.to_string(),
                                )
                                .unwrap();
                                fps_pending[id] = daemon_root.join("fps-save-fail").exists();
                                if fps_pending[id] {
                                    status = "error";
                                } else {
                                    fs::write(
                                        daemon_root.join(format!("fps-disk-{id}")),
                                        fps.to_string(),
                                    )
                                    .unwrap();
                                }
                            }
                        }
                        let saved = fs::read_to_string(daemon_root.join(format!("fps-disk-{id}")))
                            .unwrap_or("20".into());
                        let mut persisted = if fps_pending[id] {
                            "null"
                        } else if saved == stream_fps[id].to_string() {
                            "true"
                        } else {
                            "false"
                        };
                        if applied == "false" {
                            persisted = "null";
                        }
                        let paused =
                            fps_pending[id] || daemon_root.join("fps-motion-fail").exists();
                        if set && daemon_root.join("fps-motion-fail").exists() {
                            status = "error";
                        }
                        if fps_recovery[id] {
                            status = "error";
                            applied = "null";
                            persisted = "null";
                        }
                        format!(
                            r#"{{"status":"{status}","stream_id":{id},"recovery_required":{},"persistence_pending":{},"live_applied":{applied},"persisted":{persisted},"fps":{},"evidence":"SDK configuration readback","monitoring":{{"related":true,"active":{},"paused":{paused},"receiving":{},"thread_owned":{}}}}}"#,
                            fps_recovery[id],
                            fps_pending[id],
                            if fps_recovery[id] {
                                "null".into()
                            } else {
                                stream_fps[id].to_string()
                            },
                            !paused,
                            !paused,
                            !paused
                        )
                    }
                }
                "get-stream-buffer-status" => {
                    let id = request.get_path("stream_id").unwrap().to_json();
                    assert!(id == "0" || id == "1");
                    format!(
                        r#"{{"status":"ok","persistence":"read-only-config-and-sdk","stream_id":{id},"supported":true,"available":true,"editable":false,"profile":"dcs6100lhv2-a1-42m-22m-v1","profile_required_buffers":1,"hardware_limit_known":false,"active_buffers":1,"configured_buffers":1,"matches_configured":true,"profile_admitted":true}}"#
                    )
                }
                "get-stream-gop" => {
                    let id = request.get_path("stream_id").unwrap().to_json();
                    let available = id == "0" && !daemon_root.join("gop-unavailable").exists();
                    format!(
                        r#"{{"status":"ok","persistence":"checked-startup-gop","stream_id":{id},"supported":{},"available":{available},"gop":{}}}"#,
                        id == "0",
                        if available {
                            stream_gop.to_string()
                        } else {
                            "null".into()
                        }
                    )
                }
                "set-stream-gop" => {
                    assert_eq!(request.get_path("stream_id").unwrap().to_json(), "0");
                    stream_gop = request
                        .get_path("gop")
                        .unwrap()
                        .to_json()
                        .parse::<u64>()
                        .unwrap();
                    config_domain = "gop";
                    if daemon_root.join("gop-clamp").exists() {
                        stream_gop += 1;
                    }
                    r#"{"status":"ok"}"#.into()
                }
                "config-save" if config_domain == "gop" && !gop_save_failed => {
                    gop_save_failed = true;
                    r#"{"status":"error"}"#.into()
                }
                "config-save" if config_domain == "gop" => {
                    fs::write(daemon_root.join("gop-disk"), stream_gop.to_string()).unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                "config-read-section"
                    if request
                        .get_path("section")
                        .and_then(Value::as_str)
                        .is_some_and(|v| v == "stream0" || v == "stream1") =>
                {
                    let section = request.get_path("section").unwrap().as_str().unwrap();
                    let saved = fs::read_to_string(daemon_root.join("gop-disk")).unwrap();
                    format!(
                        r#"{{"status":"ok","section":"{section}","keys":{{"gop":"{saved}","nr_vbs":"1"}}}}"#
                    )
                }
                "get-motion-config" => {
                    let roi = if daemon_root.join("roi-case").exists() {
                        let single = motion_roi.map_or("null".into(), |[x0, y0, x1, y1]| {
                            format!(
                                r#"{{"roi_0_x":{x0},"roi_0_y":{y0},"roi_1_x":{x1},"roi_1_y":{y1}}}"#
                            )
                        });
                        format!(
                            r#", "roi":{{"contract":"single-region-v1","supported":true,"available":true,"applied":true,"frame_width":640,"frame_height":360,"count":{},"single":{single}}}"#,
                            if motion_roi.is_some() { 1 } else { 16 }
                        )
                    } else {
                        String::new()
                    };
                    format!(
                        r#"{{"status":"ok","persistence":"checked-config","supported":true,"available":{},"active":{motion},"receiving":{receiving},"worker":{motion},"lifecycle":{{"debounce_time":0,"cooldown_time":5,"init_time":5,"min_time":1,"post_time":0}},"sensitivity":{{"supported":true,"available":{motion},"min":0,"max":4,"value":{}}}{roi}}}"#,
                        motion == receiving,
                        if motion {
                            motion_sensitivity.to_string()
                        } else {
                            "null".into()
                        }
                    )
                }
                "set-motion-roi" => {
                    assert!(daemon_root.join("roi-case").exists());
                    assert_eq!(
                        value_u64(request.get_path("frame_width").unwrap()).unwrap(),
                        640
                    );
                    assert_eq!(
                        value_u64(request.get_path("frame_height").unwrap()).unwrap(),
                        360
                    );
                    assert_eq!(
                        value_u64(request.get_path("roi_count").unwrap()).unwrap(),
                        1
                    );
                    let mut edges = [0; 4];
                    for (i, key) in super::motion_roi::ROI_KEYS.iter().enumerate() {
                        edges[i] = value_u64(request.get_path(key).unwrap()).unwrap();
                    }
                    motion_roi = Some(edges);
                    motion = false;
                    receiving = false;
                    config_domain = "motion";
                    r#"{"status":"ok"}"#.into()
                }
                "set-motion-config" => {
                    config_domain = "motion";
                    motion = request
                        .get_path("enabled")
                        .and_then(Value::as_bool)
                        .unwrap();
                    receiving = motion;
                    r#"{"status":"ok"}"#.to_owned()
                }
                "set-motion-sensitivity" => {
                    config_domain = "motion";
                    motion_sensitivity =
                        value_u64(request.get_path("sensitivity").unwrap()).unwrap() as i32;
                    r#"{"status":"ok"}"#.to_owned()
                }
                "set-imaging" => {
                    config_domain = "image";
                    for (name, value) in request
                        .get_path("values")
                        .and_then(Value::as_object)
                        .unwrap()
                    {
                        image_values.insert(name.clone(), value.clone());
                    }
                    r#"{"status":"ok"}"#.to_owned()
                }
                "config-save" if config_domain == "privacy" && !privacy_save_failed => {
                    privacy_save_failed = true;
                    r#"{"status":"error"}"#.into()
                }
                "config-save" if config_domain == "privacy" => {
                    let enabled = fs::read_to_string(daemon_root.join("privacy-live")).unwrap();
                    fs::write(
                        daemon_root.join("privacy-disk.json"),
                        format!(r#"{{"enabled":"{enabled}"}}"#),
                    )
                    .unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                "config-read-section"
                    if request.get_path("section").and_then(Value::as_str) == Some("privacy") =>
                {
                    format!(
                        r#"{{"status":"ok","section":"privacy","keys":{}}}"#,
                        fs::read_to_string(daemon_root.join("privacy-disk.json")).unwrap()
                    )
                }
                "config-save"
                    if config_domain == "motion" && motion_roi.is_some() && !roi_save_failed =>
                {
                    roi_save_failed = true;
                    r#"{"status":"error"}"#.into()
                }
                // ROI persistence owns its fault. Retrying it must not depend
                // on another browser test consuming the generic Motion fault.
                "config-save"
                    if config_domain == "motion" && motion_roi.is_none() && !motion_save_failed =>
                {
                    motion_save_failed = true;
                    r#"{"status":"error"}"#.to_owned()
                }
                "config-save"
                    if config_domain == "motion"
                        && motion_sensitivity == 4
                        && !sensitivity_save_failed =>
                {
                    sensitivity_save_failed = true;
                    r#"{"status":"error"}"#.to_owned()
                }
                "config-save" if config_domain == "motion" => {
                    let roi = motion_roi.map_or(String::new(), |[x0, y0, x1, y1]| {
                        format!(
                            r#", "roi_count":"1","roi0":"{x0},{y0},{},{}""#,
                            x1 - 1,
                            y1 - 1
                        )
                    });
                    fs::write(
                        daemon_root.join("motion-disk.json"),
                        format!(
                            r#"{{"enabled":"{motion}","sensitivity":"{motion_sensitivity}"{roi}}}"#
                        ),
                    )
                    .unwrap();
                    r#"{"status":"ok"}"#.to_owned()
                }
                "config-save" if !image_save_failed => {
                    image_save_failed = true;
                    r#"{"status":"error"}"#.to_owned()
                }
                "config-save" => {
                    let keys = image_values
                        .iter()
                        .map(|(name, value)| (name.clone(), Value::String(value.to_json())))
                        .collect();
                    fs::write(
                        daemon_root.join("image-disk.json"),
                        Value::Object(keys).to_json(),
                    )
                    .unwrap();
                    r#"{"status":"ok"}"#.to_owned()
                }
                "config-read-section"
                    if request.get_path("section").and_then(Value::as_str) == Some("motion") =>
                {
                    format!(
                        r#"{{"status":"ok","section":"motion","keys":{}}}"#,
                        fs::read_to_string(daemon_root.join("motion-disk.json")).unwrap()
                    )
                }
                "config-read-section" => format!(
                    r#"{{"status":"ok","section":"image","keys":{}}}"#,
                    fs::read_to_string(daemon_root.join("image-disk.json")).unwrap()
                ),
                "get-running-mode" => fs::read_to_string(daemon_root.join("daynight-isp.json"))
                    .unwrap_or_else(|_| r#"{"status":"ok","mode":"day"}"#.to_owned()),
                _ => r#"{"status":"error"}"#.to_owned(),
            };
            socket
                .write_all(&(reply.len() as u16).to_be_bytes())
                .unwrap();
            socket.write_all(reply.as_bytes()).unwrap();
        }
    });
    let rhd_listener = UnixListener::bind(root.join("rhd.sock")).unwrap();
    rhd_listener.set_nonblocking(true).unwrap();
    let rhd_root = root.clone();
    let rhd_shutdown = Arc::clone(&shutdown);
    let rhd = thread::spawn(move || {
        while !rhd_shutdown.load(Ordering::Acquire) {
            let (mut socket, _) = match rhd_listener.accept() {
                Ok(socket) => socket,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            let mut header = [0; 2];
            socket
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            if socket.read_exact(&mut header).is_err() {
                continue;
            }
            let mut body = vec![0; u16::from_be_bytes(header) as usize];
            if socket.read_exact(&mut body).is_err() {
                continue;
            }
            let request = crate::json::parse(&body).unwrap();
            let reply = if matches!(
                request.get_path("cmd").and_then(Value::as_str),
                Some("timezone-status" | "timezone-reload")
            ) {
                timezone_bridge(&request, &rhd_root, "rhd")
            } else if request.get_path("cmd").and_then(Value::as_str) == Some("get-privacy-config")
            {
                privacy_bridge(&rhd_root)
            } else {
                assert_eq!(body, br#"{"cmd":"status"}"#);
                let mode = fs::read_to_string(rhd_root.join("media-case")).unwrap_or("both".into());
                fs::read_to_string(rhd_root.join(format!("{mode}.rhd.json")))
                    .unwrap()
                    .replace(
                        "\"privacy\":false",
                        &format!(
                            "\"privacy\":{}",
                            fs::read_to_string(rhd_root.join("privacy-live"))
                                .unwrap_or("false".into())
                        ),
                    )
            };
            socket
                .write_all(&(reply.len() as u16).to_be_bytes())
                .unwrap();
            socket.write_all(reply.as_bytes()).unwrap();
        }
    });
    let rad_listener = UnixListener::bind(root.join("rad.sock")).unwrap();
    rad_listener.set_nonblocking(true).unwrap();
    let rad_shutdown = Arc::clone(&shutdown);
    let rad_root = root.clone();
    let rad = thread::spawn(move || {
        let mut values = [20_i32; 4];
        let mut saved = [20_i32; 4];
        let mut first_save = true;
        let mut input_enabled = true;
        let mut output_enabled = true;
        let mut codec = "l16".to_owned();
        let mut alc = 2;
        let mut saved_input = true;
        let mut saved_output = true;
        let mut saved_codec = codec.clone();
        let mut saved_alc = alc;
        while !rad_shutdown.load(Ordering::Acquire) {
            let (mut socket, _) = match rad_listener.accept() {
                Ok(socket) => socket,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            socket
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let mut header = [0; 2];
            if socket.read_exact(&mut header).is_err() {
                continue;
            }
            let mut body = vec![0; u16::from_be_bytes(header) as usize];
            if socket.read_exact(&mut body).is_err() {
                continue;
            }
            let request = crate::json::parse(&body).unwrap();
            let command = request.get_path("cmd").and_then(Value::as_str).unwrap();
            let reply = match command {
                "get-privacy-config" => privacy_bridge(&rad_root),
                "timezone-status" | "timezone-reload" => {
                    timezone_bridge(&request, &rad_root, "rad")
                }
                "get-input-state" => {
                    String::from_utf8(audio::tests::input_state(input_enabled, &codec, false))
                        .unwrap()
                }
                "get-alc-gain" => {
                    let mut value = crate::json::parse(&audio::tests::alc(alc)).unwrap();
                    if !input_enabled {
                        value
                            .set_path("field.available", Value::Bool(false))
                            .unwrap();
                        value.set_path("field.value", Value::Null).unwrap();
                    }
                    value.to_json()
                }
                "ai-enable" | "ai-disable" => {
                    input_enabled = command == "ai-enable";
                    r#"{"status":"ok"}"#.into()
                }
                "ao-enable" | "ao-disable" => {
                    output_enabled = command == "ao-enable";
                    r#"{"status":"ok"}"#.into()
                }
                "set-codec" => {
                    codec = request
                        .get_path("value")
                        .and_then(Value::as_str)
                        .unwrap()
                        .to_owned();
                    r#"{"status":"ok"}"#.into()
                }
                "set-alc-gain" => {
                    alc = request
                        .get_path("value")
                        .and_then(|v| match v {
                            Value::Number(n) => n.parse().ok(),
                            _ => None,
                        })
                        .unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                "get-audio-levels" | "get-audio-observation-levels" => {
                    let mut value = crate::json::parse(&audio::tests::levels(20)).unwrap();
                    for (name, number) in ["mic_vol", "mic_gain", "spk_vol", "spk_gain"]
                        .iter()
                        .zip(values)
                    {
                        value
                            .set_path(
                                &format!("fields.{name}.value"),
                                Value::Number(number.to_string()),
                            )
                            .unwrap();
                    }
                    value
                        .set_path("ai_enabled", Value::Bool(input_enabled))
                        .unwrap();
                    value
                        .set_path("ao_enabled", Value::Bool(output_enabled))
                        .unwrap();
                    if !output_enabled {
                        for name in ["spk_vol", "spk_gain"] {
                            value
                                .set_path(&format!("fields.{name}.available"), Value::Bool(false))
                                .unwrap();
                            value
                                .set_path(&format!("fields.{name}.value"), Value::Null)
                                .unwrap();
                        }
                    }
                    if !input_enabled {
                        for name in ["mic_vol", "mic_gain"] {
                            value
                                .set_path(&format!("fields.{name}.available"), Value::Bool(false))
                                .unwrap();
                            value
                                .set_path(&format!("fields.{name}.value"), Value::Null)
                                .unwrap();
                        }
                    }
                    value.to_json()
                }
                "status" => {
                    let muted =
                        fs::read_to_string(rad_root.join("privacy-live")).unwrap_or("false".into());
                    format!(r#"{{"status":"ok","muted":{muted},"ao_muted":false}}"#)
                }
                "ao-clip-status" => {
                    r#"{"status":"ok","clip":"motion","playback":"idle","repeats":0,"sample_rate":16000,"max_duration_ms":10000,"talkback_active":false,"generation":0}"#.into()
                }
                "config-save" if first_save => {
                    first_save = false;
                    r#"{"status":"error"}"#.into()
                }
                "config-save" => {
                    saved = values;
                    saved_input = input_enabled;
                    saved_output = output_enabled;
                    saved_codec = codec.clone();
                    saved_alc = alc;
                    r#"{"status":"ok"}"#.into()
                }
                "config-read-section" => format!(
                    r#"{{"status":"ok","section":"audio","keys":{{"volume":"{}","gain":"{}","ao_volume":"{}","ao_gain":"{}","alc_gain":"{saved_alc}","ai_enabled":"{saved_input}","ao_enabled":"{saved_output}","codec":"{saved_codec}","sample_rate":"{}"}}}}"#,
                    saved[0],
                    saved[1],
                    saved[2],
                    saved[3],
                    if saved_codec == "l16" { 16000 } else { 8000 }
                ),
                _ if ["set-volume", "set-gain", "ao-set-volume", "ao-set-gain"]
                    .contains(&command) =>
                {
                    let index = ["set-volume", "set-gain", "ao-set-volume", "ao-set-gain"]
                        .iter()
                        .position(|name| *name == command)
                        .unwrap();
                    let Value::Number(number) = request.get_path("value").unwrap() else {
                        panic!("invalid level")
                    };
                    values[index] = number.parse().unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                other => panic!("unexpected RAD command {other}"),
            };
            socket
                .write_all(&(reply.len() as u16).to_be_bytes())
                .unwrap();
            socket.write_all(reply.as_bytes()).unwrap();
        }
    });
    let rod_listener = UnixListener::bind(root.join("rod.sock")).unwrap();
    rod_listener.set_nonblocking(true).unwrap();
    let rod_root = root.clone();
    let rod_shutdown = Arc::clone(&shutdown);
    let rod = thread::spawn(move || {
        let mut values = std::collections::BTreeMap::from([
            ("time_format".into(), Value::String("%H:%M:%S".into())),
            ("font_color".into(), Value::String("0xFFFFFFFF".into())),
            ("stroke_color".into(), Value::String("0xFF000000".into())),
        ]);
        let mut id = String::new();
        let mut save_failed = false;
        fs::write(
            rod_root.join("osd-disk.json"),
            Value::Object(values.clone()).to_json(),
        )
        .unwrap();
        while !rod_shutdown.load(Ordering::Acquire) {
            let (mut socket, _) = match rod_listener.accept() {
                Ok(socket) => socket,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            socket
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let request = crate::json::parse(&super::tests::read_request(&mut socket)).unwrap();
            let reply = match request.get_path("cmd").and_then(Value::as_str).unwrap() {
                "timezone-status" | "timezone-reload" => {
                    timezone_bridge(&request, &rod_root, "rod")
                }
                "get-osd-settings" => {
                    let mut response = values.clone();
                    for (key, value) in [
                        ("status", Value::String("ok".into())),
                        ("persistence", Value::String("checked-snapshot".into())),
                        ("enabled", Value::Bool(true)),
                        ("available", Value::Bool(true)),
                        ("confirmed", Value::Bool(!id.is_empty())),
                        ("id", Value::String(id.clone())),
                        ("fields", Value::Number("7".into())),
                    ] {
                        response.insert(key.into(), value);
                    }
                    Value::Object(response).to_json()
                }
                "set-osd-settings" => {
                    values.extend(
                        request
                            .get_path("values")
                            .and_then(Value::as_object)
                            .unwrap()
                            .clone(),
                    );
                    id = request
                        .get_path("id")
                        .and_then(Value::as_str)
                        .unwrap()
                        .into();
                    fs::write(
                        rod_root.join("osd-receipt.json"),
                        format!(r#"{{"status":"ok","accepted":true,"id":"{id}"}}"#),
                    )
                    .unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                "config-save" if !save_failed => {
                    save_failed = true;
                    r#"{"status":"error","message":"injected disk failure"}"#.into()
                }
                "config-save" => {
                    fs::write(
                        rod_root.join("osd-disk.json"),
                        Value::Object(values.clone()).to_json(),
                    )
                    .unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                "config-read-section" => format!(
                    r#"{{"status":"ok","section":"osd","keys":{}}}"#,
                    fs::read_to_string(rod_root.join("osd-disk.json")).unwrap()
                ),
                other => panic!("unexpected ROD command {other}"),
            };
            socket
                .write_all(&super::tests::framed(reply.as_bytes()))
                .unwrap();
        }
    });
    let ric_listener = UnixListener::bind(root.join("ric.sock")).unwrap();
    ric_listener.set_nonblocking(true).unwrap();
    let ric_root = root.clone();
    let ric_shutdown = Arc::clone(&shutdown);
    let ric = thread::spawn(move || {
        let mut mode = "auto".to_owned();
        let mut state = "day".to_owned();
        let mut saved = "auto".to_owned();
        let mut save_failed = false;
        let mut thresholds =
            crate::json::parse(br#"{"night_luma":20,"night_gain":80000,"day_gain_pct":25}"#)
                .unwrap();
        let mut saved_thresholds = thresholds.clone();
        while !ric_shutdown.load(Ordering::Acquire) {
            let (mut socket, _) = match ric_listener.accept() {
                Ok(socket) => socket,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                }
                Err(error) => panic!("{error}"),
            };
            socket
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let request = crate::json::parse(&super::tests::read_request(&mut socket)).unwrap();
            let reply = match request.get_path("cmd").and_then(Value::as_str).unwrap() {
                "timezone-status" | "timezone-reload" => {
                    timezone_bridge(&request, &ric_root, "ric")
                }
                // This bridge covers day/night policy, not standalone IR GPIO.
                // A heartbeat read of an unsupported output must not kill RIC.
                "get-output-state" => {
                    r#"{"status":"error","message":"outputs unavailable in this fixture"}"#.into()
                }
                "get-daynight-settings" => format!(
                    r#"{{"status":"ok","persistence":"checked-io","supported":true,"available":true,"mode":"{mode}","state":"{state}","thresholds":{{"trigger":"luma","supported":true,"available":true,"values":{}}}}}"#,
                    thresholds.to_json()
                ),
                "set-daynight-thresholds" => {
                    assert_eq!(
                        request.get_path("trigger").and_then(Value::as_str),
                        Some("luma")
                    );
                    thresholds = request.get_path("values").unwrap().clone();
                    r#"{"status":"ok"}"#.into()
                }
                "set-daynight-settings" => {
                    mode = request
                        .get_path("mode")
                        .and_then(Value::as_str)
                        .unwrap()
                        .into();
                    if mode != "auto" {
                        state = mode.clone();
                    }
                    fs::write(
                        ric_root.join("daynight-isp.json"),
                        format!(r#"{{"status":"ok","mode":"{state}"}}"#),
                    )
                    .unwrap();
                    r#"{"status":"ok"}"#.into()
                }
                "mode" => {
                    if let Some(value) = request.get_path("value").and_then(Value::as_str) {
                        mode = value.into();
                        if mode != "auto" {
                            state = mode.clone();
                        }
                    }
                    format!(r#"{{"status":"ok","mode":"{mode}","state":"{state}"}}"#)
                }
                "config-save" if !save_failed => {
                    save_failed = true;
                    r#"{"status":"error"}"#.into()
                }
                "config-save" => {
                    saved = mode.clone();
                    saved_thresholds = thresholds.clone();
                    r#"{"status":"ok"}"#.into()
                }
                "config-read-section" => {
                    let mut keys = std::collections::BTreeMap::new();
                    keys.insert("mode".into(), Value::String(saved.clone()));
                    for (key, value) in saved_thresholds.as_object().unwrap() {
                        keys.insert(key.clone(), Value::String(value.to_json()));
                    }
                    format!(
                        r#"{{"status":"ok","section":"ircut","keys":{}}}"#,
                        Value::Object(keys).to_json()
                    )
                }
                other => panic!("unexpected RIC command {other}"),
            };
            socket
                .write_all(&super::tests::framed(reply.as_bytes()))
                .unwrap();
        }
    });
    fs::write(root.join("uptime"), b"123.5 0\n").unwrap();
    fs::write(root.join("timezone"), "Etc/GMT\n").unwrap();
    fs::write(root.join("TZ"), "UTC0\n").unwrap();
    fs::write(root.join("ntp"), "server pool.ntp.org iburst\n").unwrap();
    fs::write(
        root.join("thingino.json"),
        r#"{"dhcp":{"ignore_timezone":false}}"#,
    )
    .unwrap();
    fs::write(root.join("tz.json"), r#"[{"n":"Etc/GMT","v":"UTC0"},{"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"}]"#).unwrap();
    let mut backend = RaptorBackend::new(
        root.clone(),
        "127.0.0.1:9".parse().unwrap(),
        root.join("absent.sock"),
        root.join("absent.pid"),
    )
    .unwrap();
    backend.host = crate::camera::HostBackend::new(crate::camera::CameraPaths {
        uptime: root.join("uptime"),
        timezone: root.join("timezone"),
        tz: root.join("TZ"),
        timezone_catalog: root.join("tz.json"),
        ntp_config: root.join("ntp"),
        thingino_config: root.join("thingino.json"),
        prudynt_socket: root.join("never-prudynt.sock"),
        ..crate::camera::CameraPaths::default()
    });
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    fs::write(
        root.join("address"),
        listener.local_addr().unwrap().to_string(),
    )
    .unwrap();
    let monitor_root = root.clone();
    let monitor_shutdown = Arc::clone(&shutdown);
    let monitor = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(120);
        while !monitor_root.join("stop").exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(50));
        }
        monitor_shutdown.store(true, Ordering::Release);
    });
    crate::serve(
        listener,
        Arc::new(backend),
        b"host-fixture-token".to_vec(),
        Arc::clone(&shutdown),
    )
    .unwrap();
    monitor.join().unwrap();
    daemon.join().unwrap();
    rhd.join().unwrap();
    rad.join().unwrap();
    rod.join().unwrap();
    ric.join().unwrap();
}
