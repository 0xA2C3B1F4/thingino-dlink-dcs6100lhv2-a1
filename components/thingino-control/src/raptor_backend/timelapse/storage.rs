//! Descriptor-anchored, exclusive files. Only matching closed-file markers are retained.
use super::*;
use std::ffi::CString;
use std::fs::{File, Metadata, OpenOptions};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

// AArch64 uses the ARM fcntl layout, unlike MIPS and x86_64.
#[cfg(all(target_os = "linux", target_arch = "aarch64"))]
const NOFOLLOW: i32 = 0x8000;
#[cfg(all(target_os = "linux", not(target_arch = "aarch64")))]
const NOFOLLOW: i32 = 0x20000;
#[cfg(target_os = "macos")]
const NOFOLLOW: i32 = 0x100;
#[cfg(all(target_os = "linux", target_arch = "aarch64"))]
const DIRECTORY: i32 = 0x4000;
#[cfg(all(target_os = "linux", not(target_arch = "aarch64")))]
const DIRECTORY: i32 = 0x10000;
#[cfg(target_os = "macos")]
const DIRECTORY: i32 = 0x100000;
#[cfg(target_os = "linux")]
const CLOEXEC: i32 = 0x80000;
#[cfg(target_os = "macos")]
const CLOEXEC: i32 = 0x1000000;
#[cfg(all(target_os = "linux", target_arch = "mips"))]
const CREATE_EXCLUSIVE: i32 = 0x100 | 0x400;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const CREATE_EXCLUSIVE: i32 = 0x40 | 0x80;
#[cfg(target_os = "macos")]
const CREATE_EXCLUSIVE: i32 = 0x200 | 0x800;

#[cfg(all(target_os = "linux", target_arch = "mips"))]
const NONBLOCK: i32 = 0x80;
#[cfg(all(target_os = "linux", not(target_arch = "mips")))]
const NONBLOCK: i32 = 0x800;
#[cfg(target_os = "macos")]
const NONBLOCK: i32 = 4;

unsafe extern "C" {
    fn openat(fd: i32, path: *const std::os::raw::c_char, flags: i32, ...) -> i32;
    fn mkdirat(fd: i32, path: *const std::os::raw::c_char, mode: u32) -> i32;
    fn unlinkat(fd: i32, path: *const std::os::raw::c_char, flags: i32) -> i32;
}

fn name(value: &str) -> io::Result<CString> {
    if value.is_empty() || value.contains('/') || value == "." || value == ".." {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    CString::new(value).map_err(|_| io::ErrorKind::InvalidInput.into())
}
fn same(a: &Metadata, b: &Metadata) -> bool {
    a.dev() == b.dev()
        && a.ino() == b.ino()
        && a.len() == b.len()
        && a.mtime() == b.mtime()
        && a.mtime_nsec() == b.mtime_nsec()
}

pub(in crate::raptor_backend) struct Directory {
    file: File,
    path: PathBuf,
    device: u64,
    fat_mtime: bool,
}
impl Directory {
    fn open(path: &Path) -> io::Result<Self> {
        if !path.is_absolute() {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(NOFOLLOW | DIRECTORY)
            .open("/")?;
        for component in path.components() {
            let component = match component {
                std::path::Component::RootDir => continue,
                std::path::Component::Normal(value) => {
                    value.to_str().ok_or(io::ErrorKind::InvalidInput)?
                }
                _ => return Err(io::ErrorKind::InvalidInput.into()),
            };
            let name = name(component)?;
            // SAFETY: the parent fd stays live and the component is NUL-terminated.
            let fd = unsafe {
                openat(
                    file.as_raw_fd(),
                    name.as_ptr(),
                    NOFOLLOW | DIRECTORY | CLOEXEC,
                    0_u32,
                )
            };
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            // SAFETY: openat returned a newly owned directory descriptor.
            file = unsafe { File::from_raw_fd(fd) };
        }
        let meta = file.metadata()?;
        if !meta.is_dir() {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(Self {
            file,
            path: path.to_owned(),
            device: meta.dev(),
            fat_mtime: false,
        })
    }
    pub(in crate::raptor_backend) fn marker_time(&self, metadata: &Metadata) -> (i64, i64) {
        let seconds = metadata.mtime();
        let nanos = metadata.mtime_nsec();
        if self.fat_mtime && seconds >= 0 && nanos == 0 {
            (seconds & !1, 0)
        } else {
            (seconds, nanos)
        }
    }
    pub(in crate::raptor_backend) fn marker_matches(
        &self,
        record: &str,
        prefix: &str,
        metadata: &Metadata,
    ) -> bool {
        let exact = format!(
            "{prefix} {} {} {}\n",
            metadata.len(),
            metadata.mtime(),
            metadata.mtime_nsec()
        );
        if record == exact {
            return true;
        }
        if !self.fat_mtime || metadata.mtime() < 0 || metadata.mtime_nsec() != 0 {
            return false;
        }
        // FAT persists mtime in two-second slots. Accept only the other second
        // of this same slot, including old odd-second markers after remount.
        record == format!("{prefix} {} {} 0\n", metadata.len(), metadata.mtime() ^ 1)
    }
    pub(in crate::raptor_backend) fn metadata(&self) -> io::Result<Metadata> {
        self.file.metadata()
    }
    pub(in crate::raptor_backend) fn fd(&self) -> i32 {
        self.file.as_raw_fd()
    }
    pub(in crate::raptor_backend) fn existing_child(&self, child: &str) -> io::Result<Self> {
        let c = name(child)?;
        // SAFETY: the held parent fd and single component are valid for this call.
        let fd = unsafe {
            openat(
                self.file.as_raw_fd(),
                c.as_ptr(),
                NOFOLLOW | DIRECTORY | CLOEXEC,
                0_u32,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: openat returned a new owned fd.
        let file = unsafe { File::from_raw_fd(fd) };
        let metadata = file.metadata()?;
        if !metadata.is_dir() || metadata.dev() != self.device {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(Self {
            file,
            path: self.path.join(child),
            device: self.device,
            fat_mtime: self.fat_mtime,
        })
    }
    fn child(&self, child: &str) -> io::Result<Self> {
        let c = name(child)?;
        // SAFETY: fd remains owned by self and c is a single NUL-terminated component.
        let mut fd = unsafe {
            openat(
                self.file.as_raw_fd(),
                c.as_ptr(),
                NOFOLLOW | DIRECTORY | CLOEXEC,
                0_u32,
            )
        };
        if fd < 0 && io::Error::last_os_error().kind() == io::ErrorKind::NotFound {
            // SAFETY: same anchored component; existing objects are never replaced.
            if unsafe { mkdirat(self.file.as_raw_fd(), c.as_ptr(), 0o700) } != 0
                && io::Error::last_os_error().kind() != io::ErrorKind::AlreadyExists
            {
                return Err(io::Error::last_os_error());
            }
            // SAFETY: same live directory fd and component.
            fd = unsafe {
                openat(
                    self.file.as_raw_fd(),
                    c.as_ptr(),
                    NOFOLLOW | DIRECTORY | CLOEXEC,
                    0_u32,
                )
            };
        }
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: openat returned a newly owned descriptor.
        let file = unsafe { File::from_raw_fd(fd) };
        let meta = file.metadata()?;
        if !meta.is_dir() || meta.dev() != self.device {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(Self {
            file,
            path: self.path.join(child),
            device: self.device,
            fat_mtime: self.fat_mtime,
        })
    }
    pub(in crate::raptor_backend) fn open_file(
        &self,
        child: &str,
        create: bool,
    ) -> io::Result<File> {
        let c = name(child)?;
        let flags = NOFOLLOW
            | CLOEXEC
            | if create {
                1 | CREATE_EXCLUSIVE
            } else {
                NONBLOCK
            };
        // SAFETY: live directory fd, one component, no final symlink following.
        let fd = unsafe { openat(self.file.as_raw_fd(), c.as_ptr(), flags, 0o600_u32) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: fd is newly owned.
        let file = unsafe { File::from_raw_fd(fd) };
        let meta = file.metadata()?;
        if !meta.is_file() || meta.nlink() != 1 || meta.dev() != self.device {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(file)
    }
    pub(in crate::raptor_backend) fn absent(&self, name: &str) -> bool {
        self.open_file(name, false)
            .is_err_and(|e| e.kind() == io::ErrorKind::NotFound)
    }
    pub(in crate::raptor_backend) fn remove_owned(
        &self,
        child: &str,
        expected: &Metadata,
    ) -> io::Result<()> {
        let current = self.open_file(child, false)?.metadata()?;
        if !same(&current, expected) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let c = name(child)?;
        // SAFETY: checked anchored name; does not follow a replacement symlink.
        if unsafe { unlinkat(self.file.as_raw_fd(), c.as_ptr(), 0) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    pub(in crate::raptor_backend) fn sync(&self) -> io::Result<()> {
        self.file.sync_all()
    }
    pub(in crate::raptor_backend) fn entries(&self) -> io::Result<fs::ReadDir> {
        #[cfg(target_os = "linux")]
        {
            fs::read_dir(format!("/proc/self/fd/{}", self.file.as_raw_fd()))
        }
        #[cfg(target_os = "macos")]
        {
            let current = self.path.symlink_metadata()?;
            let held = self.file.metadata()?;
            if !current.is_dir() || current.dev() != held.dev() || current.ino() != held.ino() {
                return Err(io::ErrorKind::InvalidData.into());
            }
            fs::read_dir(&self.path)
        }
    }
}

pub(in crate::raptor_backend) struct Store {
    pub mount: PathBuf,
    pub mountinfo: PathBuf,
    pub device: PathBuf,
    #[cfg(test)]
    pub fixture: bool,
    #[cfg(test)]
    pub fixture_fat: bool,
}
impl Default for Store {
    fn default() -> Self {
        Self {
            mount: policy::MOUNT.into(),
            mountinfo: "/proc/self/mountinfo".into(),
            device: "/dev/mmcblk0p1".into(),
            #[cfg(test)]
            fixture: false,
            #[cfg(test)]
            fixture_fat: false,
        }
    }
}
impl Store {
    pub fn directory(&self) -> io::Result<Directory> {
        let (root, _, writable) = self.verified_mount()?;
        if !writable {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        root.child("raptor")?.child("timelapse")
    }
    pub(in crate::raptor_backend) fn verified_mount(
        &self,
    ) -> io::Result<(Directory, String, bool)> {
        let mut root = Directory::open(&self.mount)?;
        #[cfg(test)]
        if self.fixture {
            root.fat_mtime = self.fixture_fat;
            return Ok((root, "fixture".to_owned(), true));
        }
        let device = self.device.symlink_metadata()?;
        if !device.file_type().is_block_device() || device.rdev() != root.device {
            return Err(io::ErrorKind::NotConnected.into());
        }
        let text = read_limited(&self.mountinfo, 256 * 1024)?;
        let (filesystem, writable) =
            mount_state(&text, &self.mount, &self.device).ok_or(io::ErrorKind::NotConnected)?;
        root.fat_mtime = filesystem == "vfat";
        Ok((root, filesystem, writable))
    }
}

fn mount_state(bytes: &[u8], mount: &Path, device: &Path) -> Option<(String, bool)> {
    let text = std::str::from_utf8(bytes).ok()?;
    let mut observed = None;
    for line in text.lines() {
        let Some((left, right)) = line.split_once(" - ") else {
            continue;
        };
        let l = left.split_ascii_whitespace().collect::<Vec<_>>();
        let r = right.split_ascii_whitespace().collect::<Vec<_>>();
        if l.len() < 6 || r.len() < 3 || Some(l[4]) != mount.to_str() {
            continue;
        }
        if observed.is_some()
            || l[3] != "/"
            || Some(r[1]) != device.to_str()
            || !matches!(r[0], "vfat" | "exfat" | "ext4")
        {
            return None;
        }
        let mode = |text: &str| {
            let rw = text.split(',').filter(|v| *v == "rw").count();
            let ro = text.split(',').filter(|v| *v == "ro").count();
            match (rw, ro) {
                (1, 0) => Some(true),
                (0, 1) => Some(false),
                _ => None,
            }
        };
        let local_writable = mode(l[5])?;
        let super_writable = mode(r[2])?;
        observed = Some((r[0].to_owned(), local_writable && super_writable));
    }
    observed
}

#[cfg(test)]
pub(super) fn valid_mount(bytes: &[u8], mount: &Path, device: &Path) -> bool {
    mount_state(bytes, mount, device).is_some_and(|(_, writable)| writable)
}

pub(super) fn read_limited(path: &Path, limit: usize) -> io::Result<Vec<u8>> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW | NONBLOCK)
        .open(path)?;
    if !file.metadata()?.is_file() {
        return Err(io::ErrorKind::InvalidData.into());
    }
    let mut bytes = Vec::new();
    file.take(limit as u64 + 1).read_to_end(&mut bytes)?;
    if bytes.len() > limit {
        return Err(io::ErrorKind::InvalidData.into());
    }
    Ok(bytes)
}

struct OwnedFile {
    name: String,
    file: File,
    completed: Metadata,
}
impl OwnedFile {
    fn create(dir: &Directory, name: String) -> io::Result<Self> {
        let file = dir.open_file(&name, true)?;
        let completed = file.metadata()?;
        Ok(Self {
            name,
            file,
            completed,
        })
    }
    fn write(&mut self, bytes: &[u8]) -> io::Result<()> {
        let result = self
            .file
            .write_all(bytes)
            .and_then(|_| self.file.sync_all());
        self.completed = self.file.metadata()?;
        result
    }
}

pub(super) struct Image {
    dir: Directory,
    stage: Option<OwnedFile>,
    final_image: Option<OwnedFile>,
    marker: Option<OwnedFile>,
    pub captured: u64,
}
impl Image {
    pub fn stage(
        dir: Directory,
        captured: u64,
        serial: u32,
        jpeg: &[u8],
        during_io: impl FnOnce(),
    ) -> Result<Self, (io::Error, Option<Box<Self>>)> {
        if jpeg.len() > 256 * 1024
            || jpeg.len() < 4
            || !jpeg.starts_with(&[0xff, 0xd8])
            || !jpeg.ends_with(&[0xff, 0xd9])
        {
            return Err((io::ErrorKind::InvalidData.into(), None));
        }
        let stage = OwnedFile::create(&dir, format!(".pending-{captured}-{serial}"))
            .map_err(|error| (error, None))?;
        let mut image = Self {
            dir,
            stage: Some(stage),
            final_image: None,
            marker: None,
            captured,
        };
        during_io();
        if let Err(error) = image.stage.as_mut().unwrap().write(jpeg) {
            return Err((error, Some(Box::new(image))));
        }
        Ok(image)
    }
    pub fn verify_mount(&self, fresh: &Directory) -> io::Result<()> {
        let held = self.dir.file.metadata()?;
        let current = fresh.file.metadata()?;
        if held.dev() != current.dev() || held.ino() != current.ino() {
            return Err(io::ErrorKind::NotConnected.into());
        }
        Ok(())
    }
    pub fn publish(&mut self, serial: u32) -> io::Result<()> {
        let target = format!("{}-{serial}.jpg", self.captured);
        let stage = self.stage.as_ref().unwrap();
        let mut source = self.dir.open_file(&stage.name, false)?;
        if !same(&source.metadata()?, &stage.completed) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        self.final_image = Some(OwnedFile::create(&self.dir, target.clone())?);
        let image = self.final_image.as_mut().unwrap();
        let result = io::copy(&mut (&mut source).take(256 * 1024 + 1), &mut image.file)
            .and_then(|_| image.file.sync_all());
        image.completed = image.file.metadata()?;
        result?;
        if !same(&source.metadata()?, &stage.completed)
            || image.completed.len() != stage.completed.len()
        {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let metadata = &self.final_image.as_ref().unwrap().completed;
        if !same(&self.dir.open_file(&target, false)?.metadata()?, metadata) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let (modified, nanos) = self.dir.marker_time(metadata);
        let record = format!(
            "TL1 {} {} {} {}\n",
            self.captured,
            metadata.len(),
            modified,
            nanos
        );
        self.marker = Some(OwnedFile::create(&self.dir, format!("{target}.tl-owned"))?);
        self.marker.as_mut().unwrap().write(record.as_bytes())
    }
    pub(super) fn complete(&mut self) -> io::Result<()> {
        self.remove_stage()
    }
    fn remove_stage(&mut self) -> io::Result<()> {
        if let Some(file) = self.stage.as_ref() {
            self.dir.remove_owned(&file.name, &file.completed)?;
        }
        self.stage = None;
        Ok(())
    }
    pub fn discard(&mut self) -> io::Result<()> {
        // Completed metadata is frozen before another operation can replace or alter the file.
        if let Some(file) = self.marker.as_ref() {
            self.dir.remove_owned(&file.name, &file.completed)?;
        }
        self.marker = None;
        if let Some(file) = self.final_image.as_ref() {
            self.dir.remove_owned(&file.name, &file.completed)?;
        }
        self.final_image = None;
        self.remove_stage()
    }
}

fn owned(dir: &Directory, filename: &str) -> io::Result<(File, File, u64)> {
    let stem = filename
        .strip_suffix(".jpg")
        .ok_or(io::ErrorKind::InvalidInput)?;
    let (time, sequence) = stem.split_once('-').ok_or(io::ErrorKind::InvalidInput)?;
    if time.is_empty()
        || sequence.is_empty()
        || !time
            .bytes()
            .chain(sequence.bytes())
            .all(|v| v.is_ascii_digit())
    {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    if !dir.absent(&format!(".pending-{stem}")) {
        return Err(io::ErrorKind::WouldBlock.into());
    }
    let image = dir.open_file(filename, false)?;
    let mut marker = dir.open_file(&format!("{filename}.tl-owned"), false)?;
    if marker.metadata()?.len() > 160 {
        return Err(io::ErrorKind::InvalidData.into());
    }
    let mut record = String::new();
    (&mut marker).take(160).read_to_string(&mut record)?;
    let metadata = image.metadata()?;
    if !dir.marker_matches(&record, &format!("TL1 {time}"), &metadata) {
        return Err(io::ErrorKind::InvalidData.into());
    }
    Ok((
        image,
        marker,
        time.parse().map_err(|_| io::ErrorKind::InvalidData)?,
    ))
}

pub(super) fn cleanup(
    dir: &Directory,
    keep_days: u64,
    now: u64,
    cancelled: impl Fn() -> bool,
) -> io::Result<u32> {
    if keep_days == 0 {
        return Ok(0);
    }
    let mut deleted = 0;
    for entry in dir.entries()? {
        if cancelled() {
            break;
        }
        let entry = entry?;
        let Some(filename) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let Ok((image, marker, captured)) = owned(dir, &filename) else {
            continue;
        };
        if now.saturating_sub(captured) < keep_days * 86400 {
            continue;
        }
        dir.remove_owned(&filename, &image.metadata()?)?;
        dir.remove_owned(&format!("{filename}.tl-owned"), &marker.metadata()?)?;
        deleted += 1;
        if deleted == 32 {
            break;
        }
    }
    Ok(deleted)
}

#[cfg(test)]
mod read_mount_tests {
    use super::*;
    #[test]
    fn read_only_mount_is_readable_but_not_a_capture_destination() {
        let mount = Path::new("/mnt/mmcblk0p1");
        let device = Path::new("/dev/mmcblk0p1");
        for options in ["ro - vfat /dev/mmcblk0p1 rw", "rw - vfat /dev/mmcblk0p1 ro"] {
            let line = format!("1 2 179:1 / /mnt/mmcblk0p1 {options}\n");
            assert_eq!(
                mount_state(line.as_bytes(), mount, device),
                Some(("vfat".into(), false))
            );
            assert!(!valid_mount(line.as_bytes(), mount, device));
        }
        for options in [
            "ro,rw - vfat /dev/mmcblk0p1 rw",
            "ro - vfat /dev/mmcblk0p1 ro,rw",
            "rw - tmpfs /dev/mmcblk0p1 rw",
            "rw - vfat /dev/mmcblk1p1 rw",
        ] {
            let line = format!("1 2 179:1 / /mnt/mmcblk0p1 {options}\n");
            assert!(mount_state(line.as_bytes(), mount, device).is_none());
        }
    }
}

#[cfg(test)]
mod fat_marker_tests {
    use super::*;
    #[test]
    fn canonical_time_and_cleanup_keep_other_buckets_unowned() {
        for fat in [false, true] {
            let root = crate::raptor_backend::tests::task_temp("fat-tl-marker");
            let mut dir = Directory::open(&root).unwrap();
            dir.fat_mtime = fat;
            for (name, marker_time) in [("1-0.jpg", 101), ("1-1.jpg", 103)] {
                fs::write(root.join(name), b"jpeg").unwrap();
                let file = File::open(root.join(name)).unwrap();
                file.set_times(
                    fs::FileTimes::new()
                        .set_modified(std::time::UNIX_EPOCH + std::time::Duration::from_secs(101)),
                )
                .unwrap();
                assert_eq!(
                    dir.marker_time(&file.metadata().unwrap()),
                    (if fat { 100 } else { 101 }, 0)
                );
                fs::write(
                    root.join(format!("{name}.tl-owned")),
                    format!("TL1 1 4 {marker_time} 0\n"),
                )
                .unwrap();
                file.set_times(
                    fs::FileTimes::new()
                        .set_modified(std::time::UNIX_EPOCH + std::time::Duration::from_secs(100)),
                )
                .unwrap();
            }
            fs::write(root.join("user.jpg"), b"unowned").unwrap();
            assert_eq!(cleanup(&dir, 1, 100000, || false).unwrap(), u32::from(fat));
            assert!(root.join("1-1.jpg").exists());
            assert!(root.join("user.jpg").exists());
            fs::remove_dir_all(root).unwrap();
        }
    }
}
