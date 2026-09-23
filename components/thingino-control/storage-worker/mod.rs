use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const SOCKET: &str = "/run/thingino-control/storage-v1.sock";
const DEVICE: &str = "/dev/mmcblk0p1";
const MOUNTPOINT: &str = "/mnt/mmcblk0p1";
const CID: &str = "/sys/class/block/mmcblk0/device/cid";
const PROC_MOUNTS: &str = "/proc/self/mounts";
const VERSION: u8 = 1;
const FORMAT_FAT32: u8 = 1;
const MAX_FRAME: usize = 256;

unsafe extern "C" {
    fn sync();
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct FormatRequest {
    cid: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct BlockDeviceIdentity {
    filesystem_device: u64,
    inode: u64,
    special_device: u64,
}

#[derive(Clone, Copy)]
struct StoragePaths<'a> {
    cid: &'a Path,
    proc_mounts: &'a Path,
}

trait StorageCommandRunner {
    fn sync_filesystems(&mut self);
    fn command(
        &mut self,
        program: &str,
        arguments: &[&str],
        timeout: Duration,
    ) -> Result<(), &'static str>;
}

struct SystemCommandRunner;

impl StorageCommandRunner for SystemCommandRunner {
    fn sync_filesystems(&mut self) {
        // SAFETY: sync has no preconditions and flushes filesystems before the ordinary unmount.
        unsafe { sync() };
    }

    fn command(
        &mut self,
        program: &str,
        arguments: &[&str],
        timeout: Duration,
    ) -> Result<(), &'static str> {
        command(program, arguments, timeout)
    }
}

fn decode_request(bytes: &[u8]) -> Result<FormatRequest, &'static str> {
    if bytes.len() < 4 || bytes[0] != VERSION || bytes[1] != FORMAT_FAT32 {
        return Err("unsupported-request");
    }
    let length = usize::from(bytes[2]);
    if !(8..=64).contains(&length) || bytes.len() != length + 3 {
        return Err("invalid-card-generation");
    }
    let cid = std::str::from_utf8(&bytes[3..]).map_err(|_| "invalid-card-generation")?;
    if !cid.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("invalid-card-generation");
    }
    Ok(FormatRequest {
        cid: cid.to_ascii_lowercase(),
    })
}

fn encode_response(success: bool, message: &str) -> Vec<u8> {
    let mut bytes = vec![VERSION, u8::from(success)];
    bytes.extend_from_slice(&message.as_bytes()[..message.len().min(192)]);
    bytes
}

fn read_frame(stream: &mut UnixStream) -> io::Result<Vec<u8>> {
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    let mut header = [0_u8; 4];
    stream.read_exact(&mut header)?;
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 || length > MAX_FRAME {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid frame"));
    }
    let mut body = vec![0_u8; length];
    stream.read_exact(&mut body)?;
    Ok(body)
}

fn write_frame(stream: &mut UnixStream, body: &[u8]) -> io::Result<()> {
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.write_all(&(body.len() as u32).to_be_bytes())?;
    stream.write_all(body)
}

fn read_bounded_text(path: &Path, limit: usize) -> Result<String, &'static str> {
    let metadata = path.symlink_metadata().map_err(|_| "input-unavailable")?;
    // Sysfs attributes can expose a synthetic st_size (commonly PAGE_SIZE).
    // Enforce the bound on the bytes read instead of trusting that metadata.
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("invalid-input");
    }
    let mut bytes = Vec::new();
    File::open(path)
        .and_then(|file| file.take((limit + 1) as u64).read_to_end(&mut bytes))
        .map_err(|_| "input-unavailable")?;
    if bytes.len() > limit {
        return Err("invalid-input");
    }
    String::from_utf8(bytes).map_err(|_| "invalid-input")
}

fn block_device_identity(path: &Path) -> Result<BlockDeviceIdentity, &'static str> {
    let metadata = path.symlink_metadata().map_err(|_| "device-unavailable")?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_block_device() {
        return Err("device-is-not-block-special");
    }
    Ok(BlockDeviceIdentity {
        filesystem_device: metadata.dev(),
        inode: metadata.ino(),
        special_device: metadata.rdev(),
    })
}

fn validate_cid(request: &FormatRequest, path: &Path) -> Result<(), &'static str> {
    let cid = read_bounded_text(path, 128)?;
    if cid.trim().to_ascii_lowercase() != request.cid {
        return Err("card-generation-changed");
    }
    Ok(())
}

fn current_mount(proc_mounts: &Path) -> Result<(String, bool), &'static str> {
    let mounts = read_bounded_text(proc_mounts, 256 * 1024)?;
    let mut match_value = None;
    for line in mounts.lines() {
        let fields = line.split_ascii_whitespace().collect::<Vec<_>>();
        if fields.len() < 4 || fields[0] != DEVICE {
            continue;
        }
        if match_value.is_some() || fields[1] != MOUNTPOINT {
            return Err("ambiguous-mount");
        }
        if !matches!(fields[2], "vfat" | "exfat") {
            return Err("unsupported-current-filesystem");
        }
        match_value = Some((
            fields[2].to_owned(),
            fields[3].split(',').any(|option| option == "rw"),
        ));
    }
    match_value.ok_or("card-not-mounted")
}

fn command(program: &str, arguments: &[&str], timeout: Duration) -> Result<(), &'static str> {
    let mut child = Command::new(program)
        .args(arguments)
        .env_clear()
        .current_dir("/")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "helper-start-failed")?;
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success().then_some(()).ok_or("helper-failed"),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("helper-timeout");
            }
            Err(_) => return Err("helper-wait-failed"),
        }
    }
}

fn wait_mount(proc_mounts: &Path, present: bool, filesystem: &str) -> bool {
    for _ in 0..50 {
        let current = current_mount(proc_mounts);
        if present
            && current
                .as_ref()
                .is_ok_and(|(kind, writable)| kind == filesystem && *writable)
            || !present && current == Err("card-not-mounted")
        {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

fn format_card_with<R, I>(
    request: &FormatRequest,
    paths: StoragePaths<'_>,
    runner: &mut R,
    device_identity: &mut I,
) -> Result<&'static str, &'static str>
where
    R: StorageCommandRunner,
    I: FnMut(&Path) -> Result<BlockDeviceIdentity, &'static str>,
{
    validate_cid(request, paths.cid)?;
    let expected_device = device_identity(Path::new(DEVICE))?;
    let (previous_filesystem, writable) = current_mount(paths.proc_mounts)?;
    if !writable {
        return Err("card-is-read-only");
    }
    runner.sync_filesystems();
    runner.command("/bin/umount", &[MOUNTPOINT], Duration::from_secs(10))?;
    if !wait_mount(paths.proc_mounts, false, "") {
        return Err("unmount-not-confirmed");
    }
    validate_cid(request, paths.cid)?;
    if device_identity(Path::new(DEVICE))? != expected_device {
        return Err("device-identity-changed");
    }
    if let Err(error) = runner.command(
        "/sbin/mkfs.vfat",
        &["-F", "32", "-n", "THINGINO", DEVICE],
        Duration::from_secs(60),
    ) {
        let _ = runner.command(
            "/bin/mount",
            &["-t", &previous_filesystem, DEVICE, MOUNTPOINT],
            Duration::from_secs(10),
        );
        return Err(error);
    }
    runner.command(
        "/bin/mount",
        &[
            "-t",
            "vfat",
            "-o",
            "rw,sync,noatime,nosuid,nodev,noexec,fmask=0000,dmask=0000,shortname=mixed,errors=remount-ro",
            DEVICE,
            MOUNTPOINT,
        ],
        Duration::from_secs(10),
    )?;
    if !wait_mount(paths.proc_mounts, true, "vfat") {
        return Err("mount-not-confirmed");
    }
    Ok("formatted-fat32")
}

fn format_card(request: &FormatRequest) -> Result<&'static str, &'static str> {
    let paths = StoragePaths {
        cid: Path::new(CID),
        proc_mounts: Path::new(PROC_MOUNTS),
    };
    let mut runner = SystemCommandRunner;
    let mut identity_reader = block_device_identity;
    format_card_with(request, paths, &mut runner, &mut identity_reader)
}

fn handle(mut stream: UnixStream) {
    let response = match read_frame(&mut stream)
        .map_err(|_| "invalid-frame")
        .and_then(|bytes| decode_request(&bytes))
        .and_then(|request| format_card(&request))
    {
        Ok(message) => encode_response(true, message),
        Err(message) => encode_response(false, message),
    };
    let _ = write_frame(&mut stream, &response);
}

fn prepare_socket(path: &Path) -> Result<UnixListener, String> {
    let parent = path.parent().ok_or("storage worker socket has no parent")?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
        .map_err(|error| error.to_string())?;
    if let Ok(metadata) = path.symlink_metadata() {
        if !metadata.file_type().is_socket() {
            return Err("refusing a non-socket storage worker path".to_owned());
        }
        if UnixStream::connect(path).is_ok() {
            return Err("storage worker is already running".to_owned());
        }
        fs::remove_file(path).map_err(|error| error.to_string())?;
    }
    let listener = UnixListener::bind(path).map_err(|error| error.to_string())?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|error| error.to_string())?;
    Ok(listener)
}

pub fn run_default() -> Result<(), String> {
    let listener = prepare_socket(Path::new(SOCKET))?;
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => handle(stream),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::path::PathBuf;
    use std::rc::Rc;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_SEQUENCE: AtomicU64 = AtomicU64::new(0);

    struct TestStorage {
        root: PathBuf,
        cid: PathBuf,
        mounts: PathBuf,
    }

    impl TestStorage {
        fn new(cid: &str) -> Self {
            let sequence = TEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
            let root = std::env::temp_dir()
                .join(format!("storage-worker-{}-{sequence}", std::process::id()));
            fs::create_dir(&root).unwrap();
            let cid_path = root.join("cid");
            let mounts = root.join("mounts");
            fs::write(&cid_path, format!("{cid}\n")).unwrap();
            fs::write(
                &mounts,
                format!("{DEVICE} {MOUNTPOINT} vfat rw,nosuid 0 0\n"),
            )
            .unwrap();
            Self {
                root,
                cid: cid_path,
                mounts,
            }
        }

        fn paths(&self) -> StoragePaths<'_> {
            StoragePaths {
                cid: &self.cid,
                proc_mounts: &self.mounts,
            }
        }
    }

    impl Drop for TestStorage {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    struct FakeCommandRunner {
        mounts: PathBuf,
        events: Rc<RefCell<Vec<String>>>,
        after_unmount: Option<Box<dyn FnMut()>>,
    }

    impl StorageCommandRunner for FakeCommandRunner {
        fn sync_filesystems(&mut self) {
            self.events.borrow_mut().push("sync".to_owned());
        }

        fn command(
            &mut self,
            program: &str,
            arguments: &[&str],
            _timeout: Duration,
        ) -> Result<(), &'static str> {
            self.events
                .borrow_mut()
                .push(format!("{program} {}", arguments.join(" ")));
            if program == "/bin/umount" {
                fs::write(&self.mounts, "").unwrap();
                if let Some(after_unmount) = self.after_unmount.as_mut() {
                    after_unmount();
                }
            } else if program == "/bin/mount" {
                fs::write(
                    &self.mounts,
                    format!("{DEVICE} {MOUNTPOINT} vfat rw,nosuid 0 0\n"),
                )
                .unwrap();
            }
            Ok(())
        }
    }

    struct FailedUnmountRunner {
        events: Rc<RefCell<Vec<String>>>,
    }

    impl StorageCommandRunner for FailedUnmountRunner {
        fn sync_filesystems(&mut self) {
            self.events.borrow_mut().push("sync".to_owned());
        }

        fn command(
            &mut self,
            program: &str,
            arguments: &[&str],
            _timeout: Duration,
        ) -> Result<(), &'static str> {
            self.events
                .borrow_mut()
                .push(format!("{program} {}", arguments.join(" ")));
            if program == "/bin/umount" {
                Err("helper-exit-failed")
            } else {
                Ok(())
            }
        }
    }

    fn identity(value: u64) -> BlockDeviceIdentity {
        BlockDeviceIdentity {
            filesystem_device: 10,
            inode: value,
            special_device: value,
        }
    }

    fn request() -> FormatRequest {
        FormatRequest {
            cid: "deadbeef".to_owned(),
        }
    }

    fn assert_no_mkfs(events: &RefCell<Vec<String>>) {
        assert!(
            events
                .borrow()
                .iter()
                .all(|event| !event.starts_with("/sbin/mkfs.vfat "))
        );
    }

    #[test]
    fn protocol_is_versioned_bounded_and_card_bound() {
        assert_eq!(
            decode_request(b"\x01\x01\x08deadbeef"),
            Ok(FormatRequest {
                cid: "deadbeef".to_owned()
            })
        );
        assert!(decode_request(b"\x02\x01\x08deadbeef").is_err());
        assert!(decode_request(b"\x01\x01\x08not-a-c!").is_err());
        assert!(decode_request(b"\x01\x01\x04dead").is_err());
    }

    #[test]
    fn bounded_text_reader_enforces_the_actual_byte_limit() {
        let sequence = TEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "storage-worker-bounded-text-{}-{sequence}",
            std::process::id()
        ));
        fs::write(&path, vec![b'a'; 129]).unwrap();
        assert_eq!(read_bounded_text(&path, 128), Err("invalid-input"));
        fs::write(&path, vec![b'a'; 128]).unwrap();
        assert_eq!(read_bounded_text(&path, 128).unwrap().len(), 128);
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn mount_parser_requires_one_exact_dlink_partition() {
        let root =
            std::env::temp_dir().join(format!("storage-worker-mount-{}", std::process::id()));
        let _ = fs::remove_file(&root);
        fs::write(&root, format!("{DEVICE} {MOUNTPOINT} vfat rw,nosuid 0 0\n")).unwrap();
        assert_eq!(current_mount(&root), Ok(("vfat".to_owned(), true)));
        fs::write(&root, format!("{DEVICE} /media/other vfat rw 0 0\n")).unwrap();
        assert_eq!(current_mount(&root), Err("ambiguous-mount"));
        fs::remove_file(root).unwrap();
    }

    #[test]
    fn unchanged_card_is_revalidated_immediately_before_mkfs() {
        let storage = TestStorage::new("deadbeef");
        let events = Rc::new(RefCell::new(Vec::new()));
        let identity_value = Rc::new(Cell::new(1));
        let mut runner = FakeCommandRunner {
            mounts: storage.mounts.clone(),
            events: Rc::clone(&events),
            after_unmount: None,
        };
        let mut identity_reader = {
            let events = Rc::clone(&events);
            let identity_value = Rc::clone(&identity_value);
            move |_: &Path| {
                let value = identity_value.get();
                events.borrow_mut().push(format!("identity:{value}"));
                Ok(identity(value))
            }
        };

        assert_eq!(
            format_card_with(
                &request(),
                storage.paths(),
                &mut runner,
                &mut identity_reader,
            ),
            Ok("formatted-fat32")
        );
        assert_eq!(
            *events.borrow(),
            [
                "identity:1",
                "sync",
                "/bin/umount /mnt/mmcblk0p1",
                "identity:1",
                "/sbin/mkfs.vfat -F 32 -n THINGINO /dev/mmcblk0p1",
                "/bin/mount -t vfat -o rw,sync,noatime,nosuid,nodev,noexec,fmask=0000,dmask=0000,shortname=mixed,errors=remount-ro /dev/mmcblk0p1 /mnt/mmcblk0p1",
            ]
        );
    }

    #[test]
    fn busy_unmount_from_an_external_reader_stops_before_mkfs() {
        let storage = TestStorage::new("deadbeef");
        let events = Rc::new(RefCell::new(Vec::new()));
        let mut runner = FailedUnmountRunner {
            events: Rc::clone(&events),
        };
        let mut identity_reader = |_: &Path| Ok(identity(1));

        assert_eq!(
            format_card_with(
                &request(),
                storage.paths(),
                &mut runner,
                &mut identity_reader,
            ),
            Err("helper-exit-failed")
        );
        assert_eq!(*events.borrow(), ["sync", "/bin/umount /mnt/mmcblk0p1"]);
        assert_no_mkfs(&events);
    }

    #[test]
    fn cid_change_after_unmount_stops_before_mkfs() {
        let storage = TestStorage::new("deadbeef");
        let events = Rc::new(RefCell::new(Vec::new()));
        let cid = storage.cid.clone();
        let mut runner = FakeCommandRunner {
            mounts: storage.mounts.clone(),
            events: Rc::clone(&events),
            after_unmount: Some(Box::new(move || fs::write(&cid, "cafebabe\n").unwrap())),
        };
        let mut identity_reader = |_: &Path| Ok(identity(1));

        assert_eq!(
            format_card_with(
                &request(),
                storage.paths(),
                &mut runner,
                &mut identity_reader,
            ),
            Err("card-generation-changed")
        );
        assert_no_mkfs(&events);
    }

    #[test]
    fn device_identity_change_after_unmount_stops_before_mkfs() {
        let storage = TestStorage::new("deadbeef");
        let events = Rc::new(RefCell::new(Vec::new()));
        let identity_value = Rc::new(Cell::new(1));
        let mut runner = FakeCommandRunner {
            mounts: storage.mounts.clone(),
            events: Rc::clone(&events),
            after_unmount: Some(Box::new({
                let identity_value = Rc::clone(&identity_value);
                move || identity_value.set(2)
            })),
        };
        let mut identity_reader = {
            let identity_value = Rc::clone(&identity_value);
            move |_: &Path| Ok(identity(identity_value.get()))
        };

        assert_eq!(
            format_card_with(
                &request(),
                storage.paths(),
                &mut runner,
                &mut identity_reader,
            ),
            Err("device-identity-changed")
        );
        assert_no_mkfs(&events);
    }

    #[test]
    fn missing_cid_after_unmount_stops_before_mkfs() {
        let storage = TestStorage::new("deadbeef");
        let events = Rc::new(RefCell::new(Vec::new()));
        let cid = storage.cid.clone();
        let mut runner = FakeCommandRunner {
            mounts: storage.mounts.clone(),
            events: Rc::clone(&events),
            after_unmount: Some(Box::new(move || fs::remove_file(&cid).unwrap())),
        };
        let mut identity_reader = |_: &Path| Ok(identity(1));

        assert_eq!(
            format_card_with(
                &request(),
                storage.paths(),
                &mut runner,
                &mut identity_reader,
            ),
            Err("input-unavailable")
        );
        assert_no_mkfs(&events);
    }

    #[test]
    fn failed_identity_read_runs_no_command_after_unmount() {
        let storage = TestStorage::new("deadbeef");
        let events = Rc::new(RefCell::new(Vec::new()));
        let identity_reads = Rc::new(Cell::new(0));
        let mut runner = FakeCommandRunner {
            mounts: storage.mounts.clone(),
            events: Rc::clone(&events),
            after_unmount: None,
        };
        let mut identity_reader = {
            let identity_reads = Rc::clone(&identity_reads);
            move |_: &Path| {
                let read = identity_reads.get();
                identity_reads.set(read + 1);
                (read == 0).then(|| identity(1)).ok_or("device-unavailable")
            }
        };

        assert_eq!(
            format_card_with(
                &request(),
                storage.paths(),
                &mut runner,
                &mut identity_reader,
            ),
            Err("device-unavailable")
        );
        assert_eq!(*events.borrow(), ["sync", "/bin/umount /mnt/mmcblk0p1"]);
        assert_no_mkfs(&events);
    }
}
