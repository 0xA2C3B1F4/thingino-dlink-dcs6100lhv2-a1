//! Request, recover and finalize recorded clips using the bounded private manifest.

use std::collections::VecDeque;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use super::super::motion_datagram::number_u64;
use super::super::motion_events::{MediaArtifact, MediaArtifactKind, MotionState};
use super::super::storage::{mounted_writable, safe_mount_path, safe_storage_component};
use super::super::{CameraPaths, number, object, read_bounded, write_fifo};
use super::{FILE_LIMIT, MAX_PENDING_CLIPS, MotionService, PendingClip, unix_milliseconds};
use crate::json::{self, Value};

const CLIP_MANIFEST_LIMIT: u64 = 16 * 1024;

impl MotionService {
    pub(super) fn dispatch_storage_clip(&self, prudynt: &Value, channel: u8) {
        let send = read_bounded(&self.paths.send2_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok());
        let Some(send) = send else {
            self.record_clip(false, false);
            return;
        };
        if send
            .get_path("storage.send_photo")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.unsupported_destination_events =
                runtime.unsupported_destination_events.saturating_add(1);
        }
        if !send
            .get_path("storage.send_video")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            return;
        }
        let mount = send
            .get_path("storage.mount")
            .and_then(Value::as_str)
            .unwrap_or("");
        let directory = send
            .get_path("storage.device_path")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !safe_mount_path(mount)
            || (!directory.is_empty() && !safe_storage_component(directory))
            || !mounted_writable(&self.paths.proc_mounts, Path::new(mount))
        {
            self.record_clip(false, false);
            return;
        }
        let destination = match secure_storage_directory(Path::new(mount), directory) {
            Ok(path) => path,
            Err(_) => {
                self.record_clip(false, false);
                return;
            }
        };
        let created_unix_ms = unix_milliseconds();
        let event_sequence = self
            .next_event_sequence
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .saturating_add(1);
        let id = format!("motion-{created_unix_ms}-{event_sequence}");
        let final_path = destination.join(format!("{id}.mp4"));
        let partial_path = destination.join(format!(".{id}.partial"));
        if final_path.exists() || partial_path.exists() {
            self.record_clip(false, false);
            return;
        }
        let duration = prudynt
            .get_path("motion.video_length")
            .and_then(number_u64)
            .unwrap_or(10)
            .clamp(1, 86_400);
        let mut pending = self
            .pending_clips
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if pending.len() >= MAX_PENDING_CLIPS {
            drop(pending);
            self.record_clip(false, false);
            return;
        }
        let command = format!(
            "START path={} dur={duration} ch={channel} loop=0\n",
            partial_path.to_string_lossy()
        );
        if command.len() >= 512 || write_fifo(&self.paths.recorder_ctl, command.as_bytes()).is_err()
        {
            drop(pending);
            self.record_clip(false, false);
            return;
        }
        pending.push_back(PendingClip {
            id,
            partial_path,
            final_path,
            channel,
            created_unix_ms,
            deadline_unix_ms: created_unix_ms
                .saturating_add(duration.saturating_add(30).saturating_mul(1_000)),
        });
        if self.persist_pending_clips(&pending).is_err() {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.clip_manifest_errors = runtime.clip_manifest_errors.saturating_add(1);
        }
        drop(pending);
        self.record_clip(true, false);
    }

    fn record_clip(&self, requested: bool, ready: bool) {
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if requested {
            runtime.clips_requested = runtime.clips_requested.saturating_add(1);
        } else if ready {
            runtime.clips_ready = runtime.clips_ready.saturating_add(1);
        } else {
            runtime.clips_dropped = runtime.clips_dropped.saturating_add(1);
        }
    }

    pub(in crate::camera) fn poll_media_lifecycle(&self) {
        let now = unix_milliseconds();
        let mut ready = Vec::new();
        let mut dropped = 0_u64;
        {
            let mut pending = self
                .pending_clips
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            let mut remaining = VecDeque::with_capacity(pending.len());
            while let Some(clip) = pending.pop_front() {
                let active_path = if clip.channel == 0 {
                    &self.paths.recorder_ch0_active
                } else {
                    &self.paths.recorder_ch1_active
                };
                if active_path.exists() && now < clip.deadline_unix_ms {
                    remaining.push_back(clip);
                    continue;
                }
                let completed = clip
                    .partial_path
                    .symlink_metadata()
                    .ok()
                    .filter(|metadata| {
                        metadata.is_file()
                            && !metadata.file_type().is_symlink()
                            && metadata.len() > 0
                    });
                if let Some(metadata) = completed
                    && !clip.final_path.exists()
                    && fs::rename(&clip.partial_path, &clip.final_path).is_ok()
                {
                    ready.push((clip, metadata.len()));
                    continue;
                }
                if now < clip.deadline_unix_ms {
                    remaining.push_back(clip);
                } else {
                    let _ = fs::remove_file(&clip.partial_path);
                    dropped = dropped.saturating_add(1);
                }
            }
            *pending = remaining;
            if self.persist_pending_clips(&pending).is_err() {
                let mut runtime = self
                    .runtime
                    .lock()
                    .unwrap_or_else(|error| error.into_inner());
                runtime.clip_manifest_errors = runtime.clip_manifest_errors.saturating_add(1);
            }
        }
        if dropped > 0 {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.clips_dropped = runtime.clips_dropped.saturating_add(dropped);
        }
        for (clip, size_bytes) in ready {
            self.record_clip(false, true);
            self.publish_with_artifacts(
                MotionState::Resync,
                clip.channel,
                self.snapshot().last_observation_monotonic_ms.unwrap_or(0),
                Some(unix_milliseconds()),
                None,
                Some(MediaArtifact {
                    kind: MediaArtifactKind::Clip,
                    id: clip.id,
                    local_path: Some(clip.final_path),
                    content_type: "video/mp4".to_owned(),
                    size_bytes,
                    created_unix_ms: clip.created_unix_ms,
                }),
            );
        }
    }

    pub(super) fn recover_pending_clips(&self) {
        let Some(destination) = self.storage_destination() else {
            if self.paths.motion_clip_manifest.exists() {
                self.record_manifest_error();
            }
            return;
        };
        self.recover_pending_clips_from(&destination);
    }

    pub(super) fn recover_pending_clips_from(&self, destination: &Path) {
        let Ok(bytes) = read_bounded(&self.paths.motion_clip_manifest, CLIP_MANIFEST_LIMIT) else {
            return;
        };
        let Ok(document) = json::parse(&bytes) else {
            self.record_manifest_error();
            return;
        };
        if document.get_path("version").and_then(number_u64) != Some(1) {
            self.record_manifest_error();
            return;
        }
        let Some(clips) = document.get_path("clips").and_then(Value::as_array) else {
            self.record_manifest_error();
            return;
        };
        if clips.len() > MAX_PENDING_CLIPS {
            self.record_manifest_error();
            return;
        }
        let now = unix_milliseconds();
        let mut recovered = VecDeque::with_capacity(clips.len());
        for value in clips {
            let Some(clip) = parse_pending_clip(value, destination, now, &self.paths) else {
                self.record_manifest_error();
                return;
            };
            recovered.push_back(clip);
        }
        let recovered_count = recovered.len() as u64;
        *self
            .pending_clips
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = recovered;
        if recovered_count > 0 {
            let mut runtime = self
                .runtime
                .lock()
                .unwrap_or_else(|error| error.into_inner());
            runtime.clips_recovered = runtime.clips_recovered.saturating_add(recovered_count);
        }
    }

    pub(super) fn persist_pending_clips(&self, clips: &VecDeque<PendingClip>) -> io::Result<()> {
        if clips.is_empty() {
            return match fs::remove_file(&self.paths.motion_clip_manifest) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
                Err(error) => Err(error),
            };
        }
        let manifest = object([
            ("version", number(1)),
            (
                "clips",
                Value::Array(
                    clips
                        .iter()
                        .map(|clip| {
                            object([
                                ("id", Value::String(clip.id.clone())),
                                (
                                    "partial_path",
                                    Value::String(clip.partial_path.to_string_lossy().into_owned()),
                                ),
                                (
                                    "final_path",
                                    Value::String(clip.final_path.to_string_lossy().into_owned()),
                                ),
                                ("channel", number(u64::from(clip.channel))),
                                ("created_unix_ms", number(clip.created_unix_ms)),
                                ("deadline_unix_ms", number(clip.deadline_unix_ms)),
                            ])
                        })
                        .collect(),
                ),
            ),
        ]);
        let mut bytes = manifest.to_json().into_bytes();
        bytes.push(b'\n');
        if bytes.len() as u64 > CLIP_MANIFEST_LIMIT {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "motion clip manifest exceeds limit",
            ));
        }
        atomic_write_private(&self.paths.motion_clip_manifest, &bytes)
    }

    fn storage_destination(&self) -> Option<PathBuf> {
        let send = read_bounded(&self.paths.send2_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok())?;
        let mount = send.get_path("storage.mount").and_then(Value::as_str)?;
        let directory = send
            .get_path("storage.device_path")
            .and_then(Value::as_str)
            .unwrap_or("");
        if !safe_mount_path(mount)
            || (!directory.is_empty() && !safe_storage_component(directory))
            || !mounted_writable(&self.paths.proc_mounts, Path::new(mount))
        {
            return None;
        }
        secure_storage_directory(Path::new(mount), directory).ok()
    }

    fn record_manifest_error(&self) {
        let mut runtime = self
            .runtime
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        runtime.clip_manifest_errors = runtime.clip_manifest_errors.saturating_add(1);
    }
}

fn secure_storage_directory(mount: &Path, relative: &str) -> io::Result<PathBuf> {
    let metadata = mount.symlink_metadata()?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "storage mount is not a directory",
        ));
    }
    let mut current = mount.to_path_buf();
    for component in relative
        .split('/')
        .filter(|component| !component.is_empty())
    {
        if matches!(component, "." | "..")
            || !component
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "unsafe storage component",
            ));
        }
        current.push(component);
        match current.symlink_metadata() {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "storage component is not a directory",
                ));
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                fs::create_dir(&current)?;
                fs::set_permissions(&current, fs::Permissions::from_mode(0o750))?;
            }
            Err(error) => return Err(error),
        }
    }
    Ok(current)
}

fn parse_pending_clip(
    value: &Value,
    destination: &Path,
    now: u64,
    paths: &CameraPaths,
) -> Option<PendingClip> {
    let fields = value.as_object()?;
    let allowed = [
        "id",
        "partial_path",
        "final_path",
        "channel",
        "created_unix_ms",
        "deadline_unix_ms",
    ];
    if fields.len() != allowed.len() || fields.keys().any(|key| !allowed.contains(&key.as_str())) {
        return None;
    }
    let id = fields.get("id")?.as_str()?;
    if id.len() > 128
        || !id.starts_with("motion-")
        || !id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return None;
    }
    let partial_path = PathBuf::from(fields.get("partial_path")?.as_str()?);
    let final_path = PathBuf::from(fields.get("final_path")?.as_str()?);
    if partial_path.parent() != Some(destination)
        || final_path.parent() != Some(destination)
        || partial_path.file_name()?.to_str()? != format!(".{id}.partial")
        || final_path.file_name()?.to_str()? != format!("{id}.mp4")
        || final_path.exists()
    {
        return None;
    }
    let channel = u8::try_from(number_u64(fields.get("channel")?)?).ok()?;
    if channel > 1 {
        return None;
    }
    let active_path = if channel == 0 {
        &paths.recorder_ch0_active
    } else {
        &paths.recorder_ch1_active
    };
    let active_duration = recorder_active_duration(active_path, &partial_path);
    match partial_path.symlink_metadata() {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound && active_duration.is_some() => {}
        _ => return None,
    }
    let persisted_deadline = number_u64(fields.get("deadline_unix_ms")?)?;
    let deadline_unix_ms = active_duration.map_or(persisted_deadline, |duration| {
        persisted_deadline
            .max(now.saturating_add(duration.saturating_add(30).saturating_mul(1_000)))
    });
    Some(PendingClip {
        id: id.to_owned(),
        partial_path,
        final_path,
        channel,
        created_unix_ms: number_u64(fields.get("created_unix_ms")?)?,
        deadline_unix_ms,
    })
}

fn recorder_active_duration(active_path: &Path, expected_path: &Path) -> Option<u64> {
    let bytes = read_bounded(active_path, 1_024).ok()?;
    let text = std::str::from_utf8(&bytes).ok()?;
    let mut path = None;
    let mut duration = None;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("path=") {
            if path.replace(value).is_some() {
                return None;
            }
        } else if let Some(value) = line.strip_prefix("duration=") {
            if duration
                .replace(value.parse::<u64>().ok()?.clamp(1, 86_400))
                .is_some()
            {
                return None;
            }
        } else if !line.is_empty() {
            return None;
        }
    }
    (path? == expected_path.to_string_lossy()).then_some(duration.unwrap_or(10))
}

fn atomic_write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "manifest has no parent"))?;
    fs::create_dir_all(parent)?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o755))?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid manifest name"))?,
        std::process::id()
    ));
    if let Err(error) = fs::remove_file(&temporary)
        && error.kind() != io::ErrorKind::NotFound
    {
        return Err(error);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)?;
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    drop(file);
    if let Err(error) = fs::rename(&temporary, path) {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }
    Ok(())
}
