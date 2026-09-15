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
