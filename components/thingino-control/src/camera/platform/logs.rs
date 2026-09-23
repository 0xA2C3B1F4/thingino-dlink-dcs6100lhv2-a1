use super::{abi::FILE_LIMIT, file_io::read_bounded, read_busybox_syslog};
use crate::BackendError;
#[cfg(target_os = "linux")]
use std::os::raw::{c_char, c_int};
use std::path::Path;

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
pub(super) const SYSLOG_LIMIT: usize = 64 * 1024;

#[cfg(any(
    test,
    all(target_os = "linux", any(target_arch = "x86_64", target_arch = "mips"))
))]
pub(in crate::camera) fn parse_busybox_syslog_ring(
    size: usize,
    tail: usize,
    data: &[u8],
) -> Result<Vec<u8>, BackendError> {
    if size == 0 || size > SYSLOG_LIMIT || tail >= size || data.len() < size {
        return Err(BackendError::Protocol);
    }
    let mut current = tail;
    current += data[current..size]
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(size - current);
    if current >= size {
        current = data[..tail]
            .iter()
            .position(|byte| *byte == 0)
            .unwrap_or(tail);
        if current == tail {
            return Ok(Vec::new());
        }
    }
    current += 1;
    if current >= size {
        current = 0;
    }

    let mut output = Vec::with_capacity(size);
    let mut traversed = 0usize;
    while current != tail && traversed <= size {
        let end = if current < tail { tail } else { size };
        if let Some(length) = data[current..end].iter().position(|byte| *byte == 0) {
            output.extend_from_slice(&data[current..current + length]);
            traversed = traversed.saturating_add(length + 1);
            current += length + 1;
            if current >= size {
                current = 0;
            }
        } else if current >= tail {
            output.extend_from_slice(&data[current..end]);
            traversed = traversed.saturating_add(end - current);
            current = 0;
        } else {
            return Err(BackendError::Protocol);
        }
        if output.len() > SYSLOG_LIMIT {
            return Err(BackendError::Protocol);
        }
    }
    if current != tail || traversed > size {
        return Err(BackendError::Protocol);
    }
    Ok(output)
}
pub(in crate::camera) fn read_system_log(path: &Path) -> Result<Vec<u8>, BackendError> {
    read_bounded(path, FILE_LIMIT).or_else(|_| read_busybox_syslog())
}

#[cfg(target_os = "linux")]
pub(in crate::camera) fn read_kernel_log() -> Result<Vec<u8>, BackendError> {
    const SYSLOG_ACTION_READ_ALL: c_int = 3;
    const SYSLOG_ACTION_SIZE_BUFFER: c_int = 10;
    unsafe extern "C" {
        fn klogctl(kind: c_int, buffer: *mut c_char, length: c_int) -> c_int;
    }
    // SAFETY: SIZE_BUFFER does not dereference the null buffer and only reports
    // the kernel ring capacity.
    let available = unsafe { klogctl(SYSLOG_ACTION_SIZE_BUFFER, std::ptr::null_mut(), 0) };
    if available <= 0 {
        return Err(BackendError::Unavailable);
    }
    let capacity = usize::try_from(available)
        .map_err(|_| BackendError::Protocol)?
        .min(64 * 1024);
    let mut output = vec![0u8; capacity];
    let length = c_int::try_from(capacity).map_err(|_| BackendError::Protocol)?;
    // SAFETY: output owns `length` writable bytes. READ_ALL is non-destructive
    // and does not change the kernel log ring cursor.
    let read = unsafe { klogctl(SYSLOG_ACTION_READ_ALL, output.as_mut_ptr().cast(), length) };
    if read < 0 {
        return Err(BackendError::Unavailable);
    }
    output.truncate(usize::try_from(read).map_err(|_| BackendError::Protocol)?);
    Ok(output)
}

#[cfg(not(target_os = "linux"))]
pub(in crate::camera) fn read_kernel_log() -> Result<Vec<u8>, BackendError> {
    Err(BackendError::Unavailable)
}

#[cfg(test)]
pub(in crate::camera) fn parse_android_log_entry(entry: &[u8]) -> Result<String, BackendError> {
    const LEGACY_HEADER: usize = 20;
    if entry.len() < LEGACY_HEADER {
        return Err(BackendError::Protocol);
    }
    let payload_len = usize::from(u16::from_le_bytes([entry[0], entry[1]]));
    // /dev/log_main returns the legacy logger_entry layout unless the reader
    // first selects a newer ABI with LOGGER_SET_VERSION. The second u16 is
    // padding in that layout and is not initialized by this kernel, so it must
    // never be interpreted as a header length.
    let header_len = LEGACY_HEADER;
    if header_len > entry.len() || payload_len > entry.len().saturating_sub(header_len) {
        return Err(BackendError::Protocol);
    }
    let pid = i32::from_le_bytes(entry[4..8].try_into().map_err(|_| BackendError::Protocol)?);
    let seconds = i32::from_le_bytes(
        entry[12..16]
            .try_into()
            .map_err(|_| BackendError::Protocol)?,
    );
    let nanoseconds = i32::from_le_bytes(
        entry[16..20]
            .try_into()
            .map_err(|_| BackendError::Protocol)?,
    );
    let payload = &entry[header_len..header_len + payload_len];
    if payload.is_empty() {
        return Err(BackendError::Protocol);
    }
    let priority = *b"??VDIWEFS".get(usize::from(payload[0])).unwrap_or(&b'?') as char;
    let fields = &payload[1..];
    let tag_end = fields
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(fields.len());
    let message_start = (tag_end + 1).min(fields.len());
    let message_end = fields[message_start..]
        .iter()
        .position(|byte| *byte == 0)
        .map(|value| message_start + value)
        .unwrap_or(fields.len());
    let clean = |bytes: &[u8]| {
        String::from_utf8_lossy(bytes)
            .chars()
            .map(|value| {
                if value == '\n' || value == '\t' || !value.is_control() {
                    value
                } else {
                    '?'
                }
            })
            .collect::<String>()
    };
    let tag = clean(&fields[..tag_end]);
    let message = clean(&fields[message_start..message_end]);
    Ok(format!(
        "{seconds}.{nanoseconds:09} {priority}/{tag}({pid:>5}): {message}\n"
    ))
}
