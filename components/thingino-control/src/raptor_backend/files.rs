//! Confined access to the three fixed recording roots. Large bodies stay in uhttpd.
use super::timelapse::storage::Directory;
use super::*;
use crate::MediaFileIdentity;
mod worker;
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use worker::Reader;
pub(super) use worker::Reads;

const MOUNT: &str = "/mnt/mmcblk0p1";
const ROOT: &str = "/mnt/mmcblk0p1/raptor";
const OWNERS: [&str; 3] = ["stream0", "stream1", "timelapse"];
const LABELS: [&str; 3] = ["Main recordings", "Sub recordings", "Timelapse"];
fn obj<const N: usize>(fields: [(&str, Value); N]) -> Value {
    Value::Object(fields.into_iter().map(|(k, v)| (k.to_owned(), v)).collect())
}
fn text(value: impl Into<String>) -> Value {
    Value::String(value.into())
}
fn number(value: u64) -> Value {
    Value::Number(value.to_string())
}
fn response(value: Value) -> Result<BackendResponse, BackendError> {
    Ok(BackendResponse::json(value.to_json().into_bytes()))
}
fn unavailable(_: io::Error) -> BackendError {
    BackendError::Unavailable
}
fn identity(metadata: &fs::Metadata) -> MediaFileIdentity {
    MediaFileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
        size: metadata.len(),
        modified: metadata.mtime(),
        modified_nanos: metadata.mtime_nsec(),
    }
}

fn day(value: &str) -> bool {
    value.len() == 10
        && value.bytes().enumerate().all(|(i, c)| {
            if i == 4 || i == 7 {
                c == b'-'
            } else {
                c.is_ascii_digit()
            }
        })
}
fn clip(value: &str) -> bool {
    matches!(value.len(), 12 | 16)
        && value.ends_with(".mp4")
        && value[..value.len() - 4].bytes().enumerate().all(|(i, c)| {
            if matches!(i, 2 | 5 | 8) {
                c == b'-'
            } else {
                c.is_ascii_digit()
            }
        })
}
fn jpeg(value: &str) -> Option<u64> {
    let (time, serial) = value.strip_suffix(".jpg")?.split_once('-')?;
    if time.is_empty()
        || serial.is_empty()
        || !time
            .bytes()
            .chain(serial.bytes())
            .all(|c| c.is_ascii_digit())
    {
        return None;
    }
    serial.parse::<u32>().ok()?;
    time.parse().ok()
}
fn parts(path: &str) -> Result<(usize, Vec<&str>), BackendError> {
    if path.len() > 256 || !path.is_ascii() || path.bytes().any(|c| c < 32 || c == 127) {
        return Err(BackendError::Protocol);
    }
    let tail = path
        .strip_prefix(ROOT)
        .and_then(|v| v.strip_prefix('/'))
        .ok_or(BackendError::Protocol)?;
    let mut parts = tail.split('/');
    let owner = parts
        .next()
        .and_then(|v| OWNERS.iter().position(|owner| *owner == v))
        .ok_or(BackendError::Protocol)?;
    let parts = parts.collect::<Vec<_>>();
    if parts.len() > 2
        || parts
            .iter()
            .any(|v| v.is_empty() || *v == "." || *v == "..")
    {
        return Err(BackendError::Protocol);
    }
    Ok((owner, parts))
}
fn media_path(target: &str) -> Option<String> {
    let query = target.strip_prefix("/media/v1/file?")?;
    let mut path = None;
    let mut disposition = false;
    for pair in query.split('&') {
        let (key, value) = pair.split_once('=')?;
        match key {
            "path" if path.is_none() => path = Some(crate::decode::percent_decode_path(value)?),
            "download" | "play" if !disposition && value == "1" => disposition = true,
            _ => return None,
        }
    }
    path
}

const PAGE_TTL: Duration = Duration::from_secs(120);
fn directory_stamp(m: &fs::Metadata) -> (u64, u64, i64, i64, i64, i64) {
    (
        m.dev(),
        m.ino(),
        m.mtime(),
        m.mtime_nsec(),
        m.ctime(),
        m.ctime_nsec(),
    )
}
struct Page {
    directory: Directory,
    iter: fs::ReadDir,
    stamp: (u64, u64, i64, i64, i64, i64),
    path: String,
    expires: Instant,
}

impl Reader {
    fn recording_root(&self, owner: usize) -> Result<Directory, BackendError> {
        self.ensure_exclusive_owner()?;
        let (mount, _, _) = self
            .timelapse
            .store()
            .verified_mount()
            .map_err(unavailable)?;
        mount
            .existing_child("raptor")
            .and_then(|base| base.existing_child(OWNERS[owner]))
            .map_err(unavailable)
    }
    fn closed_pair(
        &self,
        directory: &Directory,
        owner: usize,
        name: &str,
    ) -> Option<(fs::Metadata, fs::Metadata)> {
        if owner < 2 && !clip(name)
            || owner == 2 && (jpeg(name).is_none() || !self.timelapse.image_visible(name))
        {
            return None;
        }
        if owner == 2 && !directory.absent(&format!(".pending-{}", name.strip_suffix(".jpg")?)) {
            return None;
        }
        let file = directory.open_file(name, false).ok()?;
        let metadata = file.metadata().ok()?;
        let before = identity(&metadata);
        let marker_name = format!(
            "{name}.{}",
            if owner == 2 { "tl-owned" } else { "rmr-owned" }
        );
        let marker = directory.open_file(&marker_name, false).ok()?;
        let marker_metadata = marker.metadata().ok()?;
        if marker_metadata.len() > 160 {
            return None;
        }
        let mut record = String::new();
        marker.take(161).read_to_string(&mut record).ok()?;
        let prefix = if owner == 2 {
            format!("TL1 {}", jpeg(name)?)
        } else {
            format!("RMR1 {owner}")
        };
        if !directory.marker_matches(&record, &prefix, &metadata)
            || before != identity(&file.metadata().ok()?)
            || owner == 2
                && (!self.timelapse.image_visible(name)
                    || !directory.absent(&format!(".pending-{}", name.strip_suffix(".jpg")?)))
        {
            return None;
        }
        Some((metadata, marker_metadata))
    }
    fn closed_file(&self, directory: &Directory, owner: usize, name: &str) -> Option<fs::Metadata> {
        self.closed_pair(directory, owner, name).map(|pair| pair.0)
    }
    fn recording_identity(&self, target: &str) -> Option<MediaFileIdentity> {
        let path = media_path(target)?;
        let (owner, parts) = parts(&path).ok()?;
        let root = self.recording_root(owner).ok()?;
        match parts.as_slice() {
            [name] if owner == 2 => self.closed_file(&root, owner, name).map(|m| identity(&m)),
            [date, name] if owner < 2 && day(date) => self
                .closed_file(&root.existing_child(date).ok()?, owner, name)
                .map(|m| identity(&m)),
            _ => None,
        }
    }
    fn delete_recording(&mut self, target: &str) -> Result<BackendResponse, BackendError> {
        let encoded = target
            .strip_prefix("/api/v1/files?rm=")
            .filter(|value| !value.is_empty() && !value.contains('&'))
            .ok_or(BackendError::Protocol)?;
        let path = crate::decode::percent_decode_path(encoded).ok_or(BackendError::Protocol)?;
        let (owner, parts) = parts(&path)?;
        let root = self.recording_root(owner)?;
        let (directory, name) = match parts.as_slice() {
            [name] if owner == 2 => (root, *name),
            [date, name] if owner < 2 && day(date) => {
                (root.existing_child(date).map_err(unavailable)?, *name)
            }
            _ => return Err(BackendError::Protocol),
        };
        let (file_metadata, marker_metadata) = self
            .closed_pair(&directory, owner, name)
            .ok_or(BackendError::Unavailable)?;
        let marker_name = format!(
            "{name}.{}",
            if owner == 2 { "tl-owned" } else { "rmr-owned" }
        );
        directory
            .remove_owned(name, &file_metadata)
            .map_err(unavailable)?;
        directory
            .remove_owned(&marker_name, &marker_metadata)
            .and_then(|()| directory.sync())
            .map_err(|_| BackendError::PartialApply(
                "The recording was removed but cleanup readback was incomplete. Reload before retrying.",
            ))?;
        self.pages.clear();
        response(obj([("status", text("accepted")), ("path", text(path))]))
    }
    fn recording_files(
        &mut self,
        method: &str,
        target: &str,
        body: &[u8],
        deadline: Instant,
    ) -> Result<BackendResponse, BackendError> {
        if method != "GET" || !body.is_empty() {
            return Err(BackendError::Unsupported("Recording files are read-only"));
        }
        let query = target
            .strip_prefix("/api/v1/files?cd=")
            .ok_or(BackendError::Protocol)?;
        let (encoded, cursor) = match query.split_once('&') {
            None => (query, None),
            Some((path, tail)) => (
                path,
                Some(
                    tail.strip_prefix("cursor=")
                        .filter(|s| {
                            !s.is_empty()
                                && s.len() <= 64
                                && s.bytes().all(|c| c.is_ascii_hexdigit() || c == b'-')
                        })
                        .ok_or(BackendError::Protocol)?,
                ),
            ),
        };
        let path = crate::decode::percent_decode_path(encoded).ok_or(BackendError::Protocol)?;
        if path == "/" {
            if cursor.is_some() {
                return Err(BackendError::Protocol);
            }
            let mut entries = Vec::new();
            for owner in 0..3 {
                if let Ok(dir) = self.recording_root(owner) {
                    entries.push(entry(
                        LABELS[owner],
                        &format!("{ROOT}/{}", OWNERS[owner]),
                        &dir.metadata().map_err(unavailable)?,
                        true,
                    ));
                }
            }
            return listing(&path, "/", vec![crumb("Home", "/")], entries, None);
        }
        let (owner, parts) = parts(&path)?;
        let root_path = format!("{ROOT}/{}", OWNERS[owner]);
        let mut directory = self.recording_root(owner)?;
        let mut crumbs = vec![crumb("Home", "/"), crumb(LABELS[owner], &root_path)];
        let parent = if parts.is_empty() { "/" } else { &root_path };
        match parts.as_slice() {
            [] => {}
            [date] if owner < 2 && day(date) => {
                directory = directory.existing_child(date).map_err(unavailable)?;
                crumbs.push(crumb(date, &path));
            }
            _ => return Err(BackendError::Protocol),
        }
        let stamp = directory_stamp(&directory.metadata().map_err(unavailable)?);
        let mut page = if let Some(cursor) = cursor {
            let index = self
                .pages
                .iter()
                .position(|(key, p)| key == cursor && p.path == path)
                .ok_or(BackendError::Protocol)?;
            let (_, page) = self.pages.remove(index).unwrap();
            if page.expires <= Instant::now() || page.stamp != stamp {
                return Err(BackendError::Protocol);
            }
            page
        } else {
            Page {
                iter: directory.entries().map_err(unavailable)?,
                directory,
                stamp,
                path: path.clone(),
                expires: Instant::now() + PAGE_TTL,
            }
        };
        let mut entries = Vec::new();
        let mut scanned = 0;
        let mut complete = false;
        let scan_deadline = deadline
            .checked_sub(Duration::from_millis(40))
            .unwrap_or(deadline);
        while entries.len() < 512 && scanned < 4096 && Instant::now() < scan_deadline {
            let Some(item) = page.iter.next() else {
                complete = true;
                break;
            };
            scanned += 1;
            let name = item.map_err(unavailable)?.file_name();
            let Some(name) = name.to_str().filter(|s| s.is_ascii()) else {
                continue;
            };
            let is_dir = owner < 2 && parts.is_empty();
            let metadata = if is_dir && day(name) {
                page.directory
                    .existing_child(name)
                    .and_then(|dir| dir.metadata())
                    .ok()
            } else if !is_dir {
                self.closed_file(&page.directory, owner, name)
            } else {
                None
            };
            if let Some(metadata) = metadata {
                entries.push(entry(name, &format!("{path}/{name}"), &metadata, is_dir));
            }
        }
        // Directory mutation invalidates continuation; a changing folder is not a snapshot.
        if directory_stamp(&page.directory.metadata().map_err(unavailable)?) != page.stamp {
            return Err(BackendError::Protocol);
        }
        let next = if complete {
            None
        } else {
            if self.cursor_nonce.is_empty() {
                let mut nonce = [0_u8; 16];
                fs::File::open("/dev/urandom")
                    .and_then(|mut f| f.read_exact(&mut nonce))
                    .map_err(unavailable)?;
                self.cursor_nonce = nonce.iter().map(|b| format!("{b:02x}")).collect();
            }
            self.cursor_serial = self
                .cursor_serial
                .checked_add(1)
                .ok_or(BackendError::Unavailable)?;
            let token = format!("{}-{:x}", self.cursor_nonce, self.cursor_serial);
            page.expires = Instant::now() + PAGE_TTL;
            if self.pages.len() == 4 {
                self.pages.pop_front();
            }
            self.pages.push_back((token.clone(), page));
            Some(token)
        };
        entries.sort_by(|a, b| {
            a.get_path("name")
                .and_then(Value::as_str)
                .cmp(&b.get_path("name").and_then(Value::as_str))
        });
        listing(&path, parent, crumbs, entries, next)
    }
    fn recording_sd(&self) -> Result<BackendResponse, BackendError> {
        self.ensure_exclusive_owner()?;
        let store = self.timelapse.store();
        let whole = store
            .device
            .parent()
            .unwrap_or(std::path::Path::new("/dev"))
            .join("mmcblk0");
        let block = [&store.device, &whole].into_iter().find(|p| {
            p.symlink_metadata()
                .is_ok_and(|m| m.file_type().is_block_device())
        });
        let mounted = store.verified_mount().ok();
        let present = block.is_some() || mounted.is_some();
        let device_name = if mounted.is_none() && block == Some(&whole) {
            "mmcblk0"
        } else {
            "mmcblk0p1"
        };
        #[cfg(test)]
        let present = present || store.fixture;
        let mut filesystems = Vec::new();
        let mut capacity = Value::Null;
        if let Some((directory, filesystem, writable)) = &mounted {
            let stats = crate::camera::recording_filesystem_stats(directory.fd());
            let (fresh, fresh_filesystem, fresh_writable) =
                store.verified_mount().map_err(unavailable)?;
            let before = directory.metadata().map_err(unavailable)?;
            let after = fresh.metadata().map_err(unavailable)?;
            if before.dev() != after.dev()
                || before.ino() != after.ino()
                || *filesystem != fresh_filesystem
                || *writable != fresh_writable
            {
                return Err(BackendError::Unavailable);
            }
            capacity = stats.map_or(Value::Null, |s| number(s.0.saturating_mul(1024)));
            filesystems.push(obj([
                ("device", text("/dev/mmcblk0p1")),
                ("mountpoint", text(MOUNT)),
                ("filesystem", text(filesystem.clone())),
                ("writable", Value::Bool(*writable)),
                ("total_kib", stats.map_or(Value::Null, |s| number(s.0))),
                ("used_kib", stats.map_or(Value::Null, |s| number(s.1))),
                ("free_kib", stats.map_or(Value::Null, |s| number(s.2))),
            ]));
        }
        response(obj([
            ("ok", Value::Bool(true)),
            (
                "data",
                obj([
                    ("has_sdcard", Value::Bool(present)),
                    (
                        "device",
                        if present {
                            obj([
                                ("name", text(device_name)),
                                ("node", text(format!("/dev/{device_name}"))),
                                ("vendor", text("")),
                                ("model", text("")),
                                ("size_bytes", capacity),
                            ])
                        } else {
                            Value::Null
                        },
                    ),
                    (
                        "reports",
                        obj([("partitions_b64", text("")), ("mounts_b64", text(""))]),
                    ),
                    ("filesystems", Value::Array(filesystems)),
                    (
                        "format",
                        obj([
                            ("supported", Value::Bool(false)),
                            ("options", Value::Array(vec![])),
                            ("status", text("idle")),
                            ("last_output_b64", text("")),
                        ]),
                    ),
                    (
                        "messages",
                        obj([
                            (
                                "format_warning",
                                text(
                                    "Formatting is unavailable until every Raptor writer and maintenance worker can be quiesced and verified.",
                                ),
                            ),
                            ("not_present", text("No verified SD partition is present.")),
                        ]),
                    ),
                    (
                        "debug",
                        obj([(
                            "detection",
                            text(if mounted.is_some() {
                                "mount-table"
                            } else if present {
                                "device-node"
                            } else {
                                "none"
                            }),
                        )]),
                    ),
                ]),
            ),
        ]))
    }
}
fn crumb(label: &str, path: &str) -> Value {
    obj([("label", text(label)), ("path", text(path))])
}
fn entry(name: &str, path: &str, metadata: &fs::Metadata, is_dir: bool) -> Value {
    obj([
        ("name", text(name)),
        ("path", text(path)),
        (
            "size",
            text(if is_dir {
                "-".to_owned()
            } else {
                metadata.len().to_string()
            }),
        ),
        ("perm", text(format!("{:04o}", metadata.mode() & 0o7777))),
        ("time", text(metadata.mtime().to_string())),
        ("is_dir", Value::Bool(is_dir)),
        ("is_link", Value::Bool(false)),
        ("link_target", text("")),
        ("deletable", Value::Bool(!is_dir)),
    ])
}
fn listing(
    path: &str,
    parent: &str,
    breadcrumbs: Vec<Value>,
    entries: Vec<Value>,
    next: Option<String>,
) -> Result<BackendResponse, BackendError> {
    response(obj([
        ("directory", text(path)),
        ("parent", text(parent)),
        ("breadcrumbs", Value::Array(breadcrumbs)),
        ("entries", Value::Array(entries)),
        ("truncated", Value::Bool(next.is_some())),
        ("next_cursor", next.map_or(Value::Null, text)),
    ]))
}

#[cfg(test)]
mod tests;
