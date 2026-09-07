//! Recorder commands, timelapse capture and snapshot transport.

use super::*;

use super::super::prudynt::prudynt_json_request;

pub(in crate::camera) fn apply_recorder_config(
    paths: &CameraPaths,
    bytes: &[u8],
) -> Result<(), BackendError> {
    let config = json::parse(bytes).map_err(|_| BackendError::Protocol)?;
    let channel = config
        .get_path("recorder.channel")
        .and_then(value_u64)
        .unwrap_or(0);
    if channel > 1 {
        return Err(BackendError::Protocol);
    }
    write_fifo(&paths.recorder_ctl, b"STOP ch=0\n")?;
    thread::sleep(Duration::from_millis(20));
    write_fifo(&paths.recorder_ctl, b"STOP ch=1\n")?;
    if !config
        .get_path("recorder.autostart")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Ok(());
    }
    let command = recorder_start_command(paths, &config, channel)?;
    thread::sleep(Duration::from_millis(20));
    write_fifo(&paths.recorder_ctl, command.as_bytes())
}

pub(in crate::camera) fn control_recorder(
    paths: &CameraPaths,
    channel: u64,
    start: bool,
    deadline: Instant,
) -> Result<(), BackendError> {
    if channel > 1 {
        return Err(BackendError::Protocol);
    }
    if start {
        let config = json::parse(&read_bounded(&paths.prudynt_config, FILE_LIMIT)?)
            .map_err(|_| BackendError::Protocol)?;
        // Preserve Control's fail-closed mount and path checks before asking
        // Prudynt to use the same persisted recorder configuration.
        recorder_start_command(paths, &config, channel)?;
    }
    let operation = if start { "start" } else { "stop" };
    let body = format!(r#"{{"mp4":{{"{operation}":{{"channel":{channel}}}}}}}"#);
    let response = prudynt_json_request(&paths.prudynt_socket, body.as_bytes(), deadline)?;
    let response = json::parse(&response).map_err(|_| BackendError::Protocol)?;
    match response
        .get_path(&format!("mp4.{operation}"))
        .and_then(Value::as_str)
    {
        Some("ok") => Ok(()),
        _ => Err(BackendError::Unavailable),
    }
}

pub(in crate::camera) fn recorder_start_command(
    paths: &CameraPaths,
    config: &Value,
    channel: u64,
) -> Result<String, BackendError> {
    let mount = config
        .get_path("recorder.mount")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or(BackendError::Unavailable)?;
    let mount_path = Path::new(mount);
    let mount_metadata = mount_path
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if !mount_path.is_absolute()
        || !mount_metadata.is_dir()
        || mount_metadata.file_type().is_symlink()
    {
        return Err(BackendError::Unavailable);
    }
    let canonical_mount = fs::canonicalize(mount_path).map_err(|_| BackendError::Unavailable)?;
    if canonical_mount != mount_path || !path_is_within_roots(&canonical_mount, &paths.media_roots)
    {
        return Err(BackendError::Protocol);
    }
    if !mounted_writable(&paths.proc_mounts, &canonical_mount) {
        return Err(BackendError::Unavailable);
    }
    let hostname = read_text_value(&paths.hostname, 255)
        .filter(|value| safe_hostname(value))
        .unwrap_or_else(|| "thingino-camera".to_owned());
    let directory = config
        .get_path("recorder.device_path")
        .and_then(Value::as_str)
        .unwrap_or("%hostname/records")
        .replace("%hostname", &hostname);
    let name = config
        .get_path("recorder.filename")
        .and_then(Value::as_str)
        .unwrap_or("%Y%m%d/%H/%Y%m%dT%H%M%S");
    let duration = config
        .get_path("recorder.duration")
        .and_then(value_u64)
        .unwrap_or(60)
        .clamp(1, 86_400);
    for value in [mount, directory.as_str(), name] {
        if value.is_empty()
            || !safe_path_fragment(value)
            || value.bytes().any(|byte| byte.is_ascii_whitespace())
        {
            return Err(BackendError::Protocol);
        }
    }
    let command = format!(
        "START mount={mount} dir={directory} name={name} dur={duration} ch={channel} loop=1\n"
    );
    if command.len() >= 512 {
        return Err(BackendError::Protocol);
    }
    Ok(command)
}

pub(in crate::camera) fn capture_timelapse(
    paths: &CameraPaths,
    config: &Value,
    now: u64,
) -> Result<(), BackendError> {
    let mount = config
        .get_path("timelapse.mount")
        .and_then(Value::as_str)
        .ok_or(BackendError::Protocol)?;
    let directory = config
        .get_path("timelapse.filepath")
        .and_then(Value::as_str)
        .unwrap_or("timelapses");
    let template = config
        .get_path("timelapse.filename")
        .and_then(Value::as_str)
        .unwrap_or("%Y%m%d/%Y%m%dT%H%M%S.jpg");
    if !safe_mount_path(mount)
        || !safe_storage_component(directory)
        || !safe_storage_component(template)
    {
        return Err(BackendError::Protocol);
    }
    let mount_path = Path::new(mount);
    if !mounted_writable(&paths.proc_mounts, mount_path) {
        return Err(BackendError::Unavailable);
    }
    let relative = expand_utc_template(template, now)?;
    let target = mount_path.join(directory).join(relative);
    if !target.starts_with(mount_path) {
        return Err(BackendError::Protocol);
    }
    let parent = target.parent().ok_or(BackendError::Protocol)?;
    fs::create_dir_all(parent).map_err(|_| BackendError::Unavailable)?;
    let jpeg = snapshot_bytes(
        &paths.prudynt_socket,
        0,
        Instant::now() + Duration::from_secs(2),
    )?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(O_NOFOLLOW)
        .open(target)
        .map_err(|_| BackendError::Unavailable)?;
    file.write_all(&jpeg)
        .and_then(|_| file.sync_all())
        .map_err(|_| BackendError::Unavailable)
}

pub(in crate::camera) fn snapshot_bytes(
    path: &Path,
    stream_id: u8,
    deadline: Instant,
) -> Result<Vec<u8>, BackendError> {
    let command = format!("SNAPSHOT ch={stream_id}\n");
    let mut stream = connect_socket(path, deadline)?;
    write_deadline(&mut stream, command.as_bytes(), deadline)?;
    stream
        .shutdown(std::net::Shutdown::Write)
        .map_err(|_| BackendError::Connection)?;
    let header = read_line(&mut stream, deadline, 64)?;
    let length = header
        .strip_prefix("OK ")
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|length| *length > 0 && *length <= MAX_SNAPSHOT_BYTES)
        .ok_or(BackendError::Protocol)?;
    let mut jpeg = vec![0_u8; length];
    read_exact_deadline(&mut stream, &mut jpeg, deadline)?;
    if jpeg.len() < 4 || jpeg[..2] != [0xff, 0xd8] || jpeg[jpeg.len() - 2..] != [0xff, 0xd9] {
        return Err(BackendError::Protocol);
    }
    Ok(jpeg)
}

pub(in crate::camera) fn value_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(value) | Value::String(value) => value.parse().ok(),
        _ => None,
    }
}

pub(in crate::camera) fn expand_utc_template(
    template: &str,
    timestamp: u64,
) -> Result<PathBuf, BackendError> {
    let (year, month, day, hour, minute, second) = utc_parts(timestamp);
    let mut output = String::with_capacity(template.len() + 16);
    let mut chars = template.chars();
    while let Some(value) = chars.next() {
        if value != '%' {
            output.push(value);
            continue;
        }
        match chars.next().ok_or(BackendError::Protocol)? {
            '%' => output.push('%'),
            'Y' => output.push_str(&format!("{year:04}")),
            'm' => output.push_str(&format!("{month:02}")),
            'd' => output.push_str(&format!("{day:02}")),
            'H' => output.push_str(&format!("{hour:02}")),
            'M' => output.push_str(&format!("{minute:02}")),
            'S' => output.push_str(&format!("{second:02}")),
            _ => return Err(BackendError::Protocol),
        }
    }
    if !safe_path_fragment(&output) || Path::new(&output).is_absolute() {
        return Err(BackendError::Protocol);
    }
    Ok(PathBuf::from(output))
}

pub(in crate::camera) fn utc_parts(timestamp: u64) -> (i64, u64, u64, u64, u64, u64) {
    let days = (timestamp / 86_400) as i64;
    let seconds = timestamp % 86_400;
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let mut year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (
        year,
        month as u64,
        day as u64,
        seconds / 3_600,
        seconds % 3_600 / 60,
        seconds % 60,
    )
}
