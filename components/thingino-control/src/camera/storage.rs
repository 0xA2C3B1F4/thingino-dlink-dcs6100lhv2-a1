//! Storage responsibilities retain their camera-facing names through this module.

use super::*;

mod filesystem;
mod format;
mod paths;
mod sd;

pub(super) use filesystem::*;
#[cfg(feature = "raptor-backend")]
pub(crate) use format::RaptorStorageWorkerError;
pub(super) use paths::*;
// Keep the existing camera-level facade, including test and diagnostic helpers.
#[allow(unused_imports)]
pub(super) use sd::sd_mounts;

pub(super) fn os_release_value(path: &Path, key: &str) -> Option<String> {
    let content = read_text_value(path, 16 * 1024)?;
    content.lines().find_map(|line| {
        let (name, value) = line.split_once('=')?;
        (name == key).then(|| value.trim_matches('"').to_owned())
    })
}

pub(crate) fn base64_encode(bytes: &[u8]) -> String {
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
