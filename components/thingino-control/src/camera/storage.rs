use super::prudynt::prudynt_json_request;
use super::*;
use std::collections::HashSet;

pub(super) fn validated_absolute_path(value: &str) -> Result<PathBuf, BackendError> {
    let path = PathBuf::from(value);
    if value.len() > 512 || !path.is_absolute() {
        return Err(BackendError::Protocol);
    }
    if path.components().any(|component| {
        !matches!(
            component,
            std::path::Component::RootDir | std::path::Component::Normal(_)
        )
    }) {
        return Err(BackendError::Protocol);
    }
    Ok(path)
}

pub(super) fn path_is_within_roots(path: &Path, roots: &[PathBuf]) -> bool {
    roots.iter().any(|root| {
        fs::canonicalize(root)
            .map(|root| path.starts_with(root))
            .unwrap_or_else(|_| path.starts_with(root))
    })
}

pub(super) fn canonical_media_path(
    value: &str,
    roots: &[PathBuf],
) -> Result<PathBuf, BackendError> {
    let requested = validated_absolute_path(value)?;
    let metadata = requested
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if metadata.file_type().is_symlink() {
        return Err(BackendError::Protocol);
    }
    let canonical = fs::canonicalize(&requested).map_err(|_| BackendError::Unavailable)?;
    if !path_is_within_roots(&canonical, roots) {
        return Err(BackendError::Protocol);
    }
    Ok(canonical)
}

pub(super) fn file_directory(
    value: &str,
    roots: &[PathBuf],
) -> Result<BackendResponse, BackendError> {
    if value == "/" {
        let mut entries = Vec::new();
        for root in roots {
            let Ok(path) = fs::canonicalize(root) else {
                continue;
            };
            let Ok(metadata) = path.symlink_metadata() else {
                continue;
            };
            if !metadata.is_dir() || metadata.file_type().is_symlink() {
                continue;
            }
            let name = path
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_else(|| path.to_string_lossy().into_owned());
            entries.push(object([
                ("name", Value::String(name)),
                ("path", Value::String(path.to_string_lossy().into_owned())),
                ("size", Value::String("-".to_owned())),
                (
                    "perm",
                    Value::String(format!("{:04o}", metadata.permissions().mode() & 0o7777)),
                ),
                ("time", Value::String(metadata.mtime().to_string())),
                ("is_dir", Value::Bool(true)),
                ("is_link", Value::Bool(false)),
                ("link_target", Value::String(String::new())),
                ("deletable", Value::Bool(false)),
            ]));
        }
        return json_response(object([
            ("directory", Value::String("/".to_owned())),
            ("parent", Value::String("/".to_owned())),
            (
                "breadcrumbs",
                Value::Array(vec![object([
                    ("label", Value::String("Home".to_owned())),
                    ("path", Value::String("/".to_owned())),
                ])]),
            ),
            ("entries", Value::Array(entries)),
        ]));
    }
    let directory = canonical_media_path(value, roots)?;
    if !directory.is_dir() {
        return Err(BackendError::Protocol);
    }
    let mut entries = Vec::new();
    for result in fs::read_dir(&directory).map_err(|_| BackendError::Unavailable)? {
        let entry = result.map_err(|_| BackendError::Unavailable)?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.contains(['\0', '\r', '\n']) {
            continue;
        }
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path).map_err(|_| BackendError::Unavailable)?;
        let file_type = metadata.file_type();
        let link_target = if file_type.is_symlink() {
            fs::read_link(&path)
                .ok()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default()
        } else {
            String::new()
        };
        entries.push((
            file_type.is_dir(),
            name.clone(),
            object([
                ("name", Value::String(name)),
                ("path", Value::String(path.to_string_lossy().into_owned())),
                (
                    "size",
                    Value::String(if file_type.is_dir() {
                        "-".to_owned()
                    } else {
                        metadata.len().to_string()
                    }),
                ),
                (
                    "perm",
                    Value::String(format!("{:04o}", metadata.permissions().mode() & 0o7777)),
                ),
                ("time", Value::String(metadata.mtime().to_string())),
                ("is_dir", Value::Bool(file_type.is_dir())),
                ("is_link", Value::Bool(file_type.is_symlink())),
                ("link_target", Value::String(link_target)),
                ("deletable", Value::Bool(path_is_within_roots(&path, roots))),
            ]),
        ));
        if entries.len() >= 512 {
            break;
        }
    }
    entries.sort_by(|left, right| right.0.cmp(&left.0).then(left.1.cmp(&right.1)));
    let parent = directory
        .parent()
        .unwrap_or(&directory)
        .to_string_lossy()
        .into_owned();
    let mut breadcrumbs = vec![object([
        ("label", Value::String("Home".to_owned())),
        ("path", Value::String("/".to_owned())),
    ])];
    let mut accumulated = PathBuf::from("/");
    for component in directory.components() {
        if let std::path::Component::Normal(part) = component {
            accumulated.push(part);
            breadcrumbs.push(object([
                ("label", Value::String(part.to_string_lossy().into_owned())),
                (
                    "path",
                    Value::String(accumulated.to_string_lossy().into_owned()),
                ),
            ]));
        }
    }
    json_response(object([
        (
            "directory",
            Value::String(directory.to_string_lossy().into_owned()),
        ),
        ("parent", Value::String(parent)),
        ("breadcrumbs", Value::Array(breadcrumbs)),
        (
            "entries",
            Value::Array(entries.into_iter().map(|(_, _, value)| value).collect()),
        ),
    ]))
}

pub(super) fn os_release_value(path: &Path, key: &str) -> Option<String> {
    let content = read_text_value(path, 16 * 1024)?;
    content.lines().find_map(|line| {
        let (name, value) = line.split_once('=')?;
        (name == key).then(|| value.trim_matches('"').to_owned())
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct SdMount {
    pub(super) device: String,
    pub(super) mountpoint: PathBuf,
    pub(super) filesystem: String,
    pub(super) options: String,
    pub(super) writable: bool,
}

#[derive(Debug)]
pub(super) struct StorageFormatState {
    phase: &'static str,
    cid: Option<String>,
    last_output: String,
}

impl Default for StorageFormatState {
    fn default() -> Self {
        Self {
            phase: "idle",
            cid: None,
            last_output: String::new(),
        }
    }
}

fn valid_mmc_name(name: &str) -> bool {
    name == "mmcblk0"
        || name.strip_prefix("mmcblk0p").is_some_and(|partition| {
            !partition.is_empty() && partition.bytes().all(|byte| byte.is_ascii_digit())
        })
}

fn whole_mmc_name(name: &str) -> Option<&str> {
    if !valid_mmc_name(name) {
        return None;
    }
    Some(name.split_once('p').map_or(name, |(device, _)| device))
}

fn decode_mount_field(value: &str) -> String {
    value
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
}

pub(super) fn sd_mounts(bytes: &[u8], allowed_roots: &[PathBuf]) -> Vec<SdMount> {
    String::from_utf8_lossy(bytes)
        .lines()
        .filter_map(|line| {
            let fields = line.split_ascii_whitespace().collect::<Vec<_>>();
            if fields.len() < 4 {
                return None;
            }
            let name = fields[0].strip_prefix("/dev/")?;
            if !valid_mmc_name(name) {
                return None;
            }
            let mountpoint = PathBuf::from(decode_mount_field(fields[1]));
            if validated_absolute_path(&mountpoint.to_string_lossy()).is_err()
                || !path_is_within_roots(&mountpoint, allowed_roots)
            {
                return None;
            }
            Some(SdMount {
                device: fields[0].to_owned(),
                mountpoint,
                filesystem: fields[2].to_owned(),
                options: fields[3].to_owned(),
                writable: fields[3].split(',').any(|option| option == "rw"),
            })
        })
        .take(16)
        .collect()
}

fn sd_device_value(name: &str, block_root: &Path, fallback_size_bytes: u64) -> Value {
    let sys = block_root.join(name);
    let sectors = read_virtual_text_value(&sys.join("size"), 32)
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(0);
    let sector_size = read_virtual_text_value(&sys.join("queue/hw_sector_size"), 32)
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(512);
    let size_bytes = sectors
        .checked_mul(sector_size)
        .filter(|value| *value > 0)
        .unwrap_or(fallback_size_bytes);
    object([
        ("name", Value::String(name.to_owned())),
        ("node", Value::String(format!("/dev/{name}"))),
        (
            "vendor",
            Value::String(
                read_virtual_text_value(&sys.join("device/vendor"), 128).unwrap_or_default(),
            ),
        ),
        (
            "model",
            Value::String(
                read_virtual_text_value(&sys.join("device/name"), 128)
                    .or_else(|| read_virtual_text_value(&sys.join("device/model"), 128))
                    .unwrap_or_default(),
            ),
        ),
        ("size_bytes", number(size_bytes)),
    ])
}

pub(super) fn detect_sd_device(mmc_root: &Path, block_root: &Path) -> Option<Value> {
    for device in fs::read_dir(mmc_root).ok()?.flatten() {
        let device_path = device.path();
        if read_virtual_text_value(&device_path.join("type"), 32).as_deref() != Some("SD") {
            continue;
        }
        let Ok(blocks) = fs::read_dir(device_path.join("block")) else {
            continue;
        };
        for block in blocks.flatten() {
            let name = block.file_name().to_string_lossy().into_owned();
            if name != "mmcblk0" {
                continue;
            }
            return Some(sd_device_value(&name, block_root, 0));
        }
    }
    None
}

fn mounted_sd_device(mounts: &[SdMount], block_root: &Path) -> Option<Value> {
    let mount = mounts.first()?;
    let partition = mount.device.strip_prefix("/dev/")?;
    let name = whole_mmc_name(partition)?;
    let fallback_size = filesystem_stats(&mount.mountpoint)
        .map(|stats| stats.total.saturating_mul(1024))
        .unwrap_or(0);
    Some(sd_device_value(name, block_root, fallback_size))
}

fn storage_card_cid(paths: &CameraPaths) -> Option<String> {
    // Sysfs attributes can report a synthetic st_size (commonly PAGE_SIZE),
    // so the regular-file helper would reject a bounded CID before reading it.
    read_virtual_text_value(&paths.sys_class_block.join("mmcblk0/device/cid"), 128)
        .map(|value| value.trim().to_ascii_lowercase())
        .filter(|value| {
            (8..=64).contains(&value.len()) && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        })
}

fn exact_storage_mount(paths: &CameraPaths) -> Option<SdMount> {
    let mounts = read_bounded(&paths.proc_mounts, FILE_LIMIT).ok()?;
    let mut matches = sd_mounts(&mounts, &paths.media_roots)
        .into_iter()
        .filter(|mount| {
            mount.device == "/dev/mmcblk0p1" && mount.mountpoint == paths.storage_mountpoint
        });
    let mount = matches.next()?;
    matches.next().is_none().then_some(mount)
}

fn storage_workloads_inactive(paths: &CameraPaths) -> bool {
    if paths.recorder_ch0_active.is_file()
        || paths.recorder_ch1_active.is_file()
        || paths.motion_active.is_file()
    {
        return false;
    }
    let prudynt = read_json_or_empty(&paths.prudynt_config).unwrap_or(Value::Null);
    if prudynt
        .get_path("motion.enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || prudynt
            .get_path("recorder.autostart")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        return false;
    }
    let timelapse = read_json_or_empty(&paths.timelapse_config).unwrap_or(Value::Null);
    !timelapse
        .get_path("timelapse.enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn storage_worker_request(paths: &CameraPaths, cid: &str) -> Result<String, BackendError> {
    let mut stream =
        UnixStream::connect(&paths.storage_worker_socket).map_err(|_| BackendError::Unavailable)?;
    stream
        .set_read_timeout(Some(Duration::from_secs(90)))
        .map_err(|_| BackendError::Unavailable)?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| BackendError::Unavailable)?;
    let cid = cid.as_bytes();
    let mut request = Vec::with_capacity(cid.len() + 3);
    request.extend_from_slice(&[1, 1, cid.len() as u8]);
    request.extend_from_slice(cid);
    stream
        .write_all(&(request.len() as u32).to_be_bytes())
        .and_then(|_| stream.write_all(&request))
        .map_err(|_| BackendError::Unavailable)?;
    let mut header = [0_u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|_| BackendError::Unavailable)?;
    let length = u32::from_be_bytes(header) as usize;
    if !(2..=256).contains(&length) {
        return Err(BackendError::Protocol);
    }
    let mut response = vec![0_u8; length];
    stream
        .read_exact(&mut response)
        .map_err(|_| BackendError::Unavailable)?;
    if response[0] != 1 || !matches!(response[1], 0 | 1) {
        return Err(BackendError::Protocol);
    }
    let message = std::str::from_utf8(&response[2..])
        .map_err(|_| BackendError::Protocol)?
        .to_owned();
    if response[1] == 1 {
        Ok(message)
    } else {
        Err(BackendError::Unavailable)
    }
}

pub(super) fn apply_recorder_config(paths: &CameraPaths, bytes: &[u8]) -> Result<(), BackendError> {
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

pub(super) fn control_recorder(
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

pub(super) fn recorder_start_command(
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

pub(super) fn capture_timelapse(
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

pub(super) fn snapshot_bytes(
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

pub(super) fn value_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(value) | Value::String(value) => value.parse().ok(),
        _ => None,
    }
}

pub(super) fn safe_storage_component(value: &str) -> bool {
    !value.is_empty()
        && safe_path_fragment(value)
        && !value.contains(['\0', '\r', '\n'])
        && !Path::new(value).is_absolute()
}

pub(super) fn safe_mount_path(value: &str) -> bool {
    validated_absolute_path(value)
        .map(|path| path.starts_with("/mnt") || path.starts_with("/media"))
        .unwrap_or(false)
}

pub(super) fn mounted_writable(proc_mounts: &Path, mount: &Path) -> bool {
    let Ok(bytes) = read_bounded(proc_mounts, FILE_LIMIT) else {
        return false;
    };
    let mount = mount.to_string_lossy();
    String::from_utf8_lossy(&bytes).lines().any(|line| {
        let fields = line.split_ascii_whitespace().collect::<Vec<_>>();
        fields.len() >= 4 && fields[1] == mount && fields[3].split(',').any(|option| option == "rw")
    })
}

pub(super) fn expand_utc_template(template: &str, timestamp: u64) -> Result<PathBuf, BackendError> {
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

pub(super) fn utc_parts(timestamp: u64) -> (i64, u64, u64, u64, u64, u64) {
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

pub(super) fn cleanup_recording_storage(paths: &CameraPaths, now: u64) -> Result<(), BackendError> {
    let prudynt = read_json_or_empty(&paths.prudynt_config)?;
    if prudynt
        .get_path("recorder.cleanup_enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        let mount = prudynt
            .get_path("recorder.mount")
            .and_then(Value::as_str)
            .ok_or(BackendError::Protocol)?;
        let directory = prudynt
            .get_path("recorder.device_path")
            .and_then(Value::as_str)
            .unwrap_or("records")
            .replace(
                "%hostname",
                &read_text_value(&paths.hostname, 255).unwrap_or_else(|| "thingino".to_owned()),
            );
        let limit = prudynt
            .get_path("recorder.limit")
            .and_then(value_u64)
            .unwrap_or(15)
            .saturating_mul(1024 * 1024 * 1024);
        let minimum_free = prudynt
            .get_path("recorder.min_free_mb")
            .and_then(value_u64)
            .unwrap_or(500)
            .saturating_mul(1024 * 1024);
        let protected = active_recorder_paths(paths);
        cleanup_storage_tree(
            paths,
            Path::new(mount),
            &directory,
            limit,
            minimum_free,
            None,
            &protected,
        )?;
    }
    if let Ok(timelapse) = read_json_or_empty(&paths.timelapse_config)
        && timelapse
            .get_path("timelapse.enabled")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        let mount = timelapse
            .get_path("timelapse.mount")
            .and_then(Value::as_str)
            .ok_or(BackendError::Protocol)?;
        let directory = timelapse
            .get_path("timelapse.filepath")
            .and_then(Value::as_str)
            .unwrap_or("timelapses");
        let keep_days = timelapse
            .get_path("timelapse.keep_days")
            .and_then(value_u64)
            .unwrap_or(7);
        cleanup_storage_tree(
            paths,
            Path::new(mount),
            directory,
            u64::MAX,
            500 * 1024 * 1024,
            Some(now.saturating_sub(keep_days.max(1).saturating_mul(86_400)) as i64),
            &HashSet::new(),
        )?;
    }
    Ok(())
}

pub(super) fn active_recorder_paths(paths: &CameraPaths) -> HashSet<PathBuf> {
    let mut protected = HashSet::new();
    for state_path in [&paths.recorder_ch0_active, &paths.recorder_ch1_active] {
        let Some(state) = read_text_value(state_path, 4096) else {
            continue;
        };
        for line in state.lines() {
            let Some(value) = line.strip_prefix("path=") else {
                continue;
            };
            let Ok(path) = validated_absolute_path(value) else {
                continue;
            };
            let Ok(metadata) = path.symlink_metadata() else {
                continue;
            };
            if !metadata.is_file() || metadata.file_type().is_symlink() {
                continue;
            }
            if let Ok(canonical) = fs::canonicalize(path) {
                protected.insert(canonical);
            }
        }
    }
    protected
}

pub(super) fn is_protected_storage_path(path: &Path, protected: &HashSet<PathBuf>) -> bool {
    fs::canonicalize(path)
        .map(|canonical| protected.contains(&canonical))
        .unwrap_or(false)
}

pub(super) fn cleanup_storage_tree(
    paths: &CameraPaths,
    mount: &Path,
    directory: &str,
    limit: u64,
    minimum_free: u64,
    older_than: Option<i64>,
    protected: &HashSet<PathBuf>,
) -> Result<(), BackendError> {
    if !safe_mount_path(&mount.to_string_lossy())
        || !mounted_writable(&paths.proc_mounts, mount)
        || !safe_storage_component(directory)
    {
        return Err(BackendError::Protocol);
    }
    let root = mount.join(directory);
    if !root.is_dir() || !root.starts_with(mount) {
        return Ok(());
    }
    let mut files = Vec::new();
    collect_storage_files(&root, &mut files, 0)?;
    files.sort_by(|left, right| left.2.cmp(&right.2).then(left.0.cmp(&right.0)));
    let mut used = files.iter().map(|item| item.1).sum::<u64>();
    let mut free = filesystem_stats(mount)
        .map(|value| value.free.saturating_mul(1024))
        .unwrap_or(0);
    for (path, size, modified) in files {
        if is_protected_storage_path(&path, protected) {
            continue;
        }
        if older_than.is_none_or(|cutoff| modified > cutoff)
            && free >= minimum_free
            && used <= limit
        {
            continue;
        }
        if fs::remove_file(&path).is_ok() {
            used = used.saturating_sub(size);
            free = free.saturating_add(size);
        }
    }
    Ok(())
}

pub(super) fn collect_storage_files(
    root: &Path,
    output: &mut Vec<(PathBuf, u64, i64)>,
    depth: usize,
) -> Result<(), BackendError> {
    if depth > 12 || output.len() >= 20_000 {
        return Ok(());
    }
    for entry in fs::read_dir(root).map_err(|_| BackendError::Unavailable)? {
        let entry = entry.map_err(|_| BackendError::Unavailable)?;
        let file_type = entry.file_type().map_err(|_| BackendError::Unavailable)?;
        if file_type.is_symlink() {
            continue;
        }
        let path = entry.path();
        if file_type.is_dir() {
            collect_storage_files(&path, output, depth + 1)?;
        } else if file_type.is_file()
            && path
                .extension()
                .and_then(|value| value.to_str())
                .is_some_and(|value| {
                    matches!(
                        value.to_ascii_lowercase().as_str(),
                        "mp4"
                            | "ts"
                            | "mkv"
                            | "avi"
                            | "mov"
                            | "h264"
                            | "h265"
                            | "hevc"
                            | "jpg"
                            | "jpeg"
                    )
                })
        {
            let metadata = entry.metadata().map_err(|_| BackendError::Unavailable)?;
            output.push((path, metadata.len(), metadata.mtime()));
        }
    }
    Ok(())
}
pub(super) fn storage_mounts(path: &Path) -> Vec<Value> {
    let Some(text) = read_text_value(path, FILE_LIMIT) else {
        return Vec::new();
    };
    text.lines()
        .filter_map(|line| {
            let mut fields = line.split_ascii_whitespace();
            fields.next()?;
            let mount = fields.next()?;
            let filesystem = fields.next()?;
            matches!(filesystem, "vfat" | "exfat" | "nfs" | "nfs4" | "cifs")
                .then(|| Value::String(mount.replace("\\040", " ")))
        })
        .collect()
}

#[derive(Default)]
pub(super) struct FilesystemStats {
    pub(super) total: u64,
    pub(super) used: u64,
    pub(super) free: u64,
}

pub(super) fn filesystem_value(path: &Path) -> Value {
    let stats = filesystem_stats(path).unwrap_or_default();
    object([
        ("total", number(stats.total)),
        ("used", number(stats.used)),
        ("free", number(stats.free)),
    ])
}

#[cfg(target_os = "linux")]
fn blocks_to_kibibytes(blocks: impl Into<u64>, fragment_size: impl Into<u64>) -> u64 {
    blocks.into().saturating_mul(fragment_size.into()) / 1024
}

#[cfg(target_os = "linux")]
pub(super) fn filesystem_stats(path: &Path) -> Option<FilesystemStats> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let path = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut raw = Statvfs::default();
    // SAFETY: path is a NUL-terminated C string and raw is valid writable storage.
    if unsafe { statvfs(path.as_ptr(), &raw mut raw) } != 0 {
        return None;
    }
    let fragment = raw.fragment_size.max(1);
    let total = blocks_to_kibibytes(raw.blocks, fragment);
    let free = blocks_to_kibibytes(raw.blocks_available, fragment);
    Some(FilesystemStats {
        total,
        used: total.saturating_sub(free),
        free,
    })
}

#[cfg(not(target_os = "linux"))]
pub(super) fn filesystem_stats(_path: &Path) -> Option<FilesystemStats> {
    None
}

pub(super) fn directory_listing(
    path: &Path,
    depth: usize,
    limit: usize,
) -> Result<String, BackendError> {
    fn visit(
        root: &Path,
        path: &Path,
        depth: usize,
        output: &mut String,
        limit: usize,
    ) -> Result<(), BackendError> {
        if depth == 0 || output.len() >= limit {
            return Ok(());
        }
        let mut entries = fs::read_dir(path)
            .map_err(|_| BackendError::Unavailable)?
            .filter_map(Result::ok)
            .collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let metadata = entry
                .path()
                .symlink_metadata()
                .map_err(|_| BackendError::Unavailable)?;
            let relative = entry
                .path()
                .strip_prefix(root)
                .unwrap_or(&entry.path())
                .to_owned();
            let line = format!(
                "{}\t{}\t{}\n",
                if metadata.is_dir() {
                    "d"
                } else if metadata.file_type().is_symlink() {
                    "l"
                } else {
                    "f"
                },
                metadata.len(),
                relative.display()
            );
            if output.len().saturating_add(line.len()) > limit {
                output.push_str("... listing truncated ...\n");
                return Ok(());
            }
            output.push_str(&line);
            if metadata.is_dir() {
                visit(root, &entry.path(), depth - 1, output, limit)?;
            }
        }
        Ok(())
    }

    let mut output = String::new();
    visit(path, path, depth, &mut output, limit)?;
    Ok(output)
}

pub(super) fn base64_encode(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let value = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        output.push(TABLE[((value >> 18) & 0x3f) as usize] as char);
        output.push(TABLE[((value >> 12) & 0x3f) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[((value >> 6) & 0x3f) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(value & 0x3f) as usize] as char
        } else {
            '='
        });
    }
    output
}

impl PrudyntBackend {
    pub(super) fn storage_format_busy(&self) -> bool {
        self.storage_format
            .lock()
            .map(|state| matches!(state.phase, "queued" | "running"))
            .unwrap_or(true)
    }

    pub(super) fn queue_sd_format(&self, body: &[u8]) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let request = request.as_object().ok_or(BackendError::Protocol)?;
        if request
            .keys()
            .any(|key| !matches!(key.as_str(), "action" | "filesystem" | "confirm"))
            || request.get("action").and_then(Value::as_str) != Some("format")
            || request.get("filesystem").and_then(Value::as_str) != Some("fat32")
            || request.get("confirm").and_then(Value::as_str) != Some("erase")
        {
            return Err(BackendError::Protocol);
        }
        if !storage_workloads_inactive(&self.paths) {
            return Err(BackendError::Unavailable);
        }
        let mount = exact_storage_mount(&self.paths).ok_or(BackendError::Unavailable)?;
        if !mount.writable {
            return Err(BackendError::Unavailable);
        }
        let cid = storage_card_cid(&self.paths).ok_or(BackendError::Unavailable)?;
        if !self
            .paths
            .storage_worker_socket
            .symlink_metadata()
            .is_ok_and(|metadata| {
                !metadata.file_type().is_symlink() && metadata.file_type().is_socket()
            })
        {
            return Err(BackendError::Unavailable);
        }
        let mut state = self
            .storage_format
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        if matches!(state.phase, "queued" | "running") {
            return Err(BackendError::Unavailable);
        }
        state.phase = "queued";
        state.cid = Some(cid);
        state.last_output.clear();
        json_response(object([
            ("status", Value::String("queued".to_owned())),
            ("filesystem", Value::String("fat32".to_owned())),
        ]))
    }

    pub(super) fn process_storage_format(&self) {
        let cid = {
            let Ok(mut state) = self.storage_format.lock() else {
                return;
            };
            if state.phase != "queued" {
                return;
            }
            state.phase = "running";
            state.cid.take()
        };
        let result = cid.ok_or(BackendError::Protocol).and_then(|cid| {
            if !storage_workloads_inactive(&self.paths)
                || storage_card_cid(&self.paths).as_deref() != Some(cid.as_str())
                || !exact_storage_mount(&self.paths).is_some_and(|mount| mount.writable)
            {
                return Err(BackendError::Unavailable);
            }
            storage_worker_request(&self.paths, &cid)
        });
        if let Ok(mut state) = self.storage_format.lock() {
            match result {
                Ok(message) => {
                    state.phase = "succeeded";
                    state.last_output = message;
                }
                Err(_) => {
                    state.phase = "failed";
                    state.last_output =
                        "Formatting failed safely; refresh the card state before retrying."
                            .to_owned();
                }
            }
        }
    }

    pub(super) fn overlay(&self) -> Result<BackendResponse, BackendError> {
        let stats = filesystem_stats(&self.paths.overlay).unwrap_or_default();
        let percent = stats
            .used
            .saturating_mul(100)
            .checked_div(stats.total)
            .unwrap_or(0);
        let listing = directory_listing(&self.paths.overlay, 3, 96 * 1024)?;
        let body = object([
            (
                "usage",
                object([
                    ("label", Value::String(format!("{percent}%"))),
                    ("percent", number(percent)),
                    (
                        "state",
                        Value::String(if percent >= 75 { "danger" } else { "primary" }.to_owned()),
                    ),
                ]),
            ),
            (
                "listing_base64",
                Value::String(base64_encode(listing.as_bytes())),
            ),
            ("path", Value::String("/overlay".to_owned())),
        ]);
        json_response(body)
    }
    pub(super) fn sd_state(&self) -> Result<BackendResponse, BackendError> {
        let mounts = read_bounded(&self.paths.proc_mounts, FILE_LIMIT).unwrap_or_default();
        let sd_mounts = sd_mounts(&mounts, &self.paths.media_roots);
        let sysfs_device = detect_sd_device(&self.paths.sys_bus_mmc, &self.paths.sys_class_block);
        let detection = if sysfs_device.is_some() {
            "sysfs"
        } else if !sd_mounts.is_empty() {
            "mount-table"
        } else {
            "none"
        };
        let device =
            sysfs_device.or_else(|| mounted_sd_device(&sd_mounts, &self.paths.sys_class_block));
        let mount_report = sd_mounts
            .iter()
            .map(|mount| {
                format!(
                    "{} {} {} {}",
                    mount.device,
                    mount.mountpoint.display(),
                    mount.filesystem,
                    mount.options
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        let filesystems = sd_mounts
            .iter()
            .map(|mount| {
                let stats = filesystem_stats(&mount.mountpoint).unwrap_or_default();
                object([
                    ("device", Value::String(mount.device.clone())),
                    (
                        "mountpoint",
                        Value::String(mount.mountpoint.to_string_lossy().into_owned()),
                    ),
                    ("filesystem", Value::String(mount.filesystem.clone())),
                    ("writable", Value::Bool(mount.writable)),
                    ("total_kib", number(stats.total)),
                    ("used_kib", number(stats.used)),
                    ("free_kib", number(stats.free)),
                ])
            })
            .collect::<Vec<_>>();
        let mut partition_report = String::new();
        if !filesystems.is_empty() {
            partition_report.push_str("DEVICE TYPE MODE TOTAL_KIB USED_KIB FREE_KIB MOUNTPOINT\n");
        }
        for filesystem in &filesystems {
            let text = |path: &str| {
                filesystem
                    .get_path(path)
                    .and_then(Value::as_str)
                    .unwrap_or("-")
            };
            let raw = |path: &str| {
                filesystem
                    .get_path(path)
                    .map(Value::to_json)
                    .unwrap_or_else(|| "0".to_owned())
            };
            partition_report.push_str(&format!(
                "{} {} {} {} {} {} {}\n",
                text("device"),
                text("filesystem"),
                if filesystem
                    .get_path("writable")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    "rw"
                } else {
                    "ro"
                },
                raw("total_kib"),
                raw("used_kib"),
                raw("free_kib"),
                text("mountpoint"),
            ));
        }
        let format_supported = storage_card_cid(&self.paths).is_some()
            && exact_storage_mount(&self.paths).is_some_and(|mount| mount.writable)
            && self
                .paths
                .storage_worker_socket
                .symlink_metadata()
                .is_ok_and(|metadata| {
                    !metadata.file_type().is_symlink() && metadata.file_type().is_socket()
                });
        let (format_status, format_output) = self
            .storage_format
            .lock()
            .map(|state| (state.phase, state.last_output.clone()))
            .unwrap_or(("failed", String::new()));
        let data = object([
            ("has_sdcard", Value::Bool(device.is_some())),
            ("device", device.unwrap_or(Value::Null)),
            (
                "reports",
                object([
                    (
                        "partitions_b64",
                        Value::String(base64_encode(partition_report.as_bytes())),
                    ),
                    (
                        "mounts_b64",
                        Value::String(base64_encode(mount_report.as_bytes())),
                    ),
                ]),
            ),
            (
                "format",
                object([
                    ("supported", Value::Bool(format_supported)),
                    (
                        "options",
                        Value::Array(if format_supported {
                            vec![object([
                                ("id", Value::String("fat32".to_owned())),
                                ("label", Value::String("FAT32".to_owned())),
                                (
                                    "description",
                                    Value::String("Best compatibility for camera recordings.".to_owned()),
                                ),
                            ])]
                        } else {
                            Vec::new()
                        }),
                    ),
                    ("status", Value::String(format_status.to_owned())),
                    (
                        "last_output_b64",
                        Value::String(base64_encode(format_output.as_bytes())),
                    ),
                ]),
            ),
            ("filesystems", Value::Array(filesystems)),
            (
                "messages",
                object([
                    (
                        "format_warning",
                        Value::String(
                            "Formatting permanently erases every file on /dev/mmcblk0p1. Motion, recorder and timelapse must be disabled first."
                                .to_owned(),
                        ),
                    ),
                    (
                        "not_present",
                        Value::String("Insert or reseat the SD card to manage it here.".to_owned()),
                    ),
                ]),
            ),
            (
                "debug",
                object([("detection", Value::String(detection.to_owned()))]),
            ),
        ]);
        json_response(object([("ok", Value::Bool(true)), ("data", data)]))
    }
}

#[cfg(test)]
mod request_validation_tests {
    use super::*;

    #[test]
    fn malformed_sd_format_requests_preserve_idle_state() {
        let backend = PrudyntBackend::new(CameraPaths::default());
        for invalid in [
            b"[]".as_slice(),
            br#"{}"#.as_slice(),
            br#"{"action":"erase","filesystem":"fat32","confirm":"erase"}"#.as_slice(),
            br#"{"action":"format","filesystem":"ext4","confirm":"erase"}"#.as_slice(),
            br#"{"action":"format","filesystem":"fat32","confirm":"yes"}"#.as_slice(),
            br#"{"action":"format","filesystem":"fat32","confirm":"erase","extra":true}"#
                .as_slice(),
            br#"{"action":"format","action":"format","filesystem":"fat32","confirm":"erase"}"#
                .as_slice(),
        ] {
            assert!(matches!(
                backend.queue_sd_format(invalid),
                Err(BackendError::Protocol)
            ));
            let state = backend.storage_format.lock().unwrap();
            assert_eq!(state.phase, "idle");
            assert_eq!(state.cid, None);
            assert!(state.last_output.is_empty());
        }
        assert!(!backend.storage_format_busy());
    }
}
