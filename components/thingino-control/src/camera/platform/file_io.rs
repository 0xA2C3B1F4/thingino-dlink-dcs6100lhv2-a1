use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use super::abi::{FILE_LIMIT, O_NOFOLLOW};
use crate::BackendError;

pub(in crate::camera) fn read_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, BackendError> {
    let file = File::open(path).map_err(|_| BackendError::Unavailable)?;
    let metadata = file.metadata().map_err(|_| BackendError::Unavailable)?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err(BackendError::Protocol);
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| BackendError::Unavailable)?;
    if bytes.len() as u64 > limit {
        return Err(BackendError::Protocol);
    }
    Ok(bytes)
}

pub(in crate::camera) fn read_virtual_bounded(
    path: &Path,
    limit: usize,
) -> Result<Vec<u8>, BackendError> {
    let file = File::open(path).map_err(|_| BackendError::Unavailable)?;
    let mut bytes = Vec::with_capacity(limit.min(4096));
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| BackendError::Unavailable)?;
    if bytes.len() > limit {
        return Err(BackendError::Protocol);
    }
    Ok(bytes)
}

pub(in crate::camera) fn read_virtual_text_value(path: &Path, limit: usize) -> Option<String> {
    String::from_utf8(read_virtual_bounded(path, limit).ok()?)
        .ok()
        .map(|value| value.trim().to_owned())
}

pub(in crate::camera) fn write_in_place(path: &Path, content: &[u8]) -> Result<(), BackendError> {
    write_atomic_replace(path, content)
}

pub(in crate::camera) fn write_atomic_replace(
    path: &Path,
    content: &[u8],
) -> Result<(), BackendError> {
    let metadata = path
        .symlink_metadata()
        .map_err(|_| BackendError::Unavailable)?;
    if !metadata.is_file() || content.len() as u64 > FILE_LIMIT {
        return Err(BackendError::Protocol);
    }
    let parent = path.parent().ok_or(BackendError::Protocol)?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or(BackendError::Protocol)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| BackendError::Unavailable)?
        .as_nanos();
    let temporary = parent.join(format!(
        ".{name}.thingino-control-{}-{nonce}.tmp",
        std::process::id()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(metadata.mode() & 0o777)
            .custom_flags(O_NOFOLLOW)
            .open(&temporary)
            .map_err(|_| BackendError::Unavailable)?;
        file.write_all(content)
            .and_then(|_| file.sync_all())
            .map_err(|_| BackendError::Unavailable)?;
        fs::rename(&temporary, path).map_err(|_| BackendError::Unavailable)?;
        // Once rename succeeds, the new canonical bytes and the already
        // applied live state form one logical commit. A directory fsync error
        // cannot safely be reported as a failed write: the caller would roll
        // back live state while the new file is already visible. Keep the
        // committed state aligned and report only the reduced crash-durability
        // guarantee.
        if File::open(parent)
            .and_then(|directory| directory.sync_all())
            .is_err()
        {
            eprintln!("thingino-controld: Prudynt config committed but directory sync failed");
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub(in crate::camera) fn write_config_file(
    path: &Path,
    content: &[u8],
    mode: u32,
) -> Result<(), BackendError> {
    if content.len() as u64 > FILE_LIMIT {
        return Err(BackendError::Protocol);
    }
    if path.exists() {
        return write_in_place(path, content);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(O_NOFOLLOW)
        .open(path)
        .map_err(|_| BackendError::Unavailable)?;
    file.write_all(content)
        .and_then(|_| file.sync_all())
        .map_err(|_| BackendError::Unavailable)
}

pub(in crate::camera) fn first_number(path: &Path) -> Option<f64> {
    read_numbers(path, 1).into_iter().next()
}

pub(in crate::camera) fn read_text_value(path: &Path, limit: u64) -> Option<String> {
    String::from_utf8(read_bounded(path, limit).ok()?)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

pub(in crate::camera) fn read_numbers(path: &Path, count: usize) -> Vec<f64> {
    read_bounded(path, 512)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .map(|text| {
            text.split_ascii_whitespace()
                .take(count)
                .filter_map(|value| value.parse().ok())
                .collect()
        })
        .unwrap_or_default()
}

#[derive(Default)]
pub(in crate::camera) struct Memory {
    pub(in crate::camera) total: u64,
    pub(in crate::camera) active: u64,
    pub(in crate::camera) free: u64,
    pub(in crate::camera) buffers: u64,
    pub(in crate::camera) cached: u64,
}

pub(in crate::camera) fn read_memory(path: &Path) -> Memory {
    let mut memory = Memory::default();
    let Some(text) = read_bounded(path, 16 * 1024)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
    else {
        return memory;
    };
    for line in text.lines() {
        let mut fields = line.split_ascii_whitespace();
        let key = fields.next().unwrap_or_default();
        let value = fields
            .next()
            .and_then(|field| field.parse().ok())
            .unwrap_or(0);
        match key {
            "MemTotal:" => memory.total = value,
            "Active:" => memory.active = value,
            "MemFree:" => memory.free = value,
            "Buffers:" => memory.buffers = value,
            "Cached:" => memory.cached = value,
            _ => {}
        }
    }
    memory
}
