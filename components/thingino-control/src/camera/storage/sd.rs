//! SD discovery, mount identity and status reporting.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(in crate::camera) struct SdMount {
    pub(in crate::camera) device: String,
    pub(in crate::camera) mountpoint: PathBuf,
    pub(in crate::camera) filesystem: String,
    pub(in crate::camera) options: String,
    pub(in crate::camera) writable: bool,
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

pub(in crate::camera) fn sd_mounts(bytes: &[u8], allowed_roots: &[PathBuf]) -> Vec<SdMount> {
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

pub(in crate::camera) fn detect_sd_device(mmc_root: &Path, block_root: &Path) -> Option<Value> {
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

pub(super) fn storage_card_cid(paths: &CameraPaths) -> Option<String> {
    // Sysfs attributes can report a synthetic st_size (commonly PAGE_SIZE),
    // so the regular-file helper would reject a bounded CID before reading it.
    read_virtual_text_value(&paths.sys_class_block.join("mmcblk0/device/cid"), 128)
        .map(|value| value.trim().to_ascii_lowercase())
        .filter(|value| {
            (8..=64).contains(&value.len()) && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        })
}

pub(super) fn exact_storage_mount(paths: &CameraPaths) -> Option<SdMount> {
    let mounts = read_bounded(&paths.proc_mounts, FILE_LIMIT).ok()?;
    let mut matches = sd_mounts(&mounts, &paths.media_roots)
        .into_iter()
        .filter(|mount| {
            mount.device == "/dev/mmcblk0p1" && mount.mountpoint == paths.storage_mountpoint
        });
    let mount = matches.next()?;
    matches.next().is_none().then_some(mount)
}

impl PrudyntBackend {
    pub(in crate::camera) fn sd_state(&self) -> Result<BackendResponse, BackendError> {
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
