//! Filesystem accounting, mount listing and overlay diagnostics.

use super::*;

#[derive(Default)]
pub(in crate::camera) struct FilesystemStats {
    pub(in crate::camera) total: u64,
    pub(in crate::camera) used: u64,
    pub(in crate::camera) free: u64,
}

pub(in crate::camera) fn filesystem_value(path: &Path) -> Value {
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
pub(in crate::camera) fn filesystem_stats(path: &Path) -> Option<FilesystemStats> {
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
pub(in crate::camera) fn filesystem_stats(_path: &Path) -> Option<FilesystemStats> {
    None
}

impl HostBackend {
    pub(in crate::camera) fn overlay(&self) -> Result<BackendResponse, BackendError> {
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
}
