//! Retention traversal and protection of active recordings.

use super::*;

use std::collections::HashSet;

pub(in crate::camera) fn cleanup_recording_storage(
    paths: &CameraPaths,
    now: u64,
) -> Result<(), BackendError> {
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

pub(in crate::camera) fn active_recorder_paths(paths: &CameraPaths) -> HashSet<PathBuf> {
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

pub(in crate::camera) fn is_protected_storage_path(
    path: &Path,
    protected: &HashSet<PathBuf>,
) -> bool {
    fs::canonicalize(path)
        .map(|canonical| protected.contains(&canonical))
        .unwrap_or(false)
}

pub(in crate::camera) fn cleanup_storage_tree(
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

pub(in crate::camera) fn collect_storage_files(
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
