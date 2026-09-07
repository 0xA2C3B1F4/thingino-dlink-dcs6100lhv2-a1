//! Card-bound format queue and storage worker protocol.

use super::*;

use super::sd::{exact_storage_mount, storage_card_cid};

fn storage_workloads_inactive(paths: &CameraPaths) -> bool {
    if paths.recorder_ch0_active.is_file()
        || paths.recorder_ch1_active.is_file()
        || paths.motion_active.is_file()
    {
        return false;
    }
    let prudynt = read_json_or_empty(&paths.prudynt_config).unwrap_or(Value::Null);
    if prudynt
        .get_path("motion.enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || prudynt
            .get_path("recorder.autostart")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        return false;
    }
    let timelapse = read_json_or_empty(&paths.timelapse_config).unwrap_or(Value::Null);
    !timelapse
        .get_path("timelapse.enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn storage_worker_request(paths: &CameraPaths, cid: &str) -> Result<String, BackendError> {
    let mut stream =
        UnixStream::connect(&paths.storage_worker_socket).map_err(|_| BackendError::Unavailable)?;
    stream
        .set_read_timeout(Some(Duration::from_secs(90)))
        .map_err(|_| BackendError::Unavailable)?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .map_err(|_| BackendError::Unavailable)?;
    let cid = cid.as_bytes();
    let mut request = Vec::with_capacity(cid.len() + 3);
    request.extend_from_slice(&[1, 1, cid.len() as u8]);
    request.extend_from_slice(cid);
    stream
        .write_all(&(request.len() as u32).to_be_bytes())
        .and_then(|_| stream.write_all(&request))
        .map_err(|_| BackendError::Unavailable)?;
    let mut header = [0_u8; 4];
    stream
        .read_exact(&mut header)
        .map_err(|_| BackendError::Unavailable)?;
    let length = u32::from_be_bytes(header) as usize;
    if !(2..=256).contains(&length) {
        return Err(BackendError::Protocol);
    }
    let mut response = vec![0_u8; length];
    stream
        .read_exact(&mut response)
        .map_err(|_| BackendError::Unavailable)?;
    if response[0] != 1 || !matches!(response[1], 0 | 1) {
        return Err(BackendError::Protocol);
    }
    let message = std::str::from_utf8(&response[2..])
        .map_err(|_| BackendError::Protocol)?
        .to_owned();
    if response[1] == 1 {
        Ok(message)
    } else {
        Err(BackendError::Unavailable)
    }
}

impl PrudyntBackend {
    pub(in crate::camera) fn storage_format_busy(&self) -> bool {
        self.storage_format
            .lock()
            .map(|state| matches!(state.phase, "queued" | "running"))
            .unwrap_or(true)
    }

    pub(in crate::camera) fn queue_sd_format(
        &self,
        body: &[u8],
    ) -> Result<BackendResponse, BackendError> {
        let request = json::parse(body).map_err(|_| BackendError::Protocol)?;
        let request = request.as_object().ok_or(BackendError::Protocol)?;
        if request
            .keys()
            .any(|key| !matches!(key.as_str(), "action" | "filesystem" | "confirm"))
            || request.get("action").and_then(Value::as_str) != Some("format")
            || request.get("filesystem").and_then(Value::as_str) != Some("fat32")
            || request.get("confirm").and_then(Value::as_str) != Some("erase")
        {
            return Err(BackendError::Protocol);
        }
        if !storage_workloads_inactive(&self.paths) {
            return Err(BackendError::Unavailable);
        }
        let mount = exact_storage_mount(&self.paths).ok_or(BackendError::Unavailable)?;
        if !mount.writable {
            return Err(BackendError::Unavailable);
        }
        let cid = storage_card_cid(&self.paths).ok_or(BackendError::Unavailable)?;
        if !self
            .paths
            .storage_worker_socket
            .symlink_metadata()
            .is_ok_and(|metadata| {
                !metadata.file_type().is_symlink() && metadata.file_type().is_socket()
            })
        {
            return Err(BackendError::Unavailable);
        }
        let mut state = self
            .storage_format
            .lock()
            .map_err(|_| BackendError::Unavailable)?;
        if matches!(state.phase, "queued" | "running") {
            return Err(BackendError::Unavailable);
        }
        state.phase = "queued";
        state.cid = Some(cid);
        state.last_output.clear();
        json_response(object([
            ("status", Value::String("queued".to_owned())),
            ("filesystem", Value::String("fat32".to_owned())),
        ]))
    }

    pub(in crate::camera) fn process_storage_format(&self) {
        let cid = {
            let Ok(mut state) = self.storage_format.lock() else {
                return;
            };
            if state.phase != "queued" {
                return;
            }
            state.phase = "running";
            state.cid.take()
        };
        let result = cid.ok_or(BackendError::Protocol).and_then(|cid| {
            if !storage_workloads_inactive(&self.paths)
                || storage_card_cid(&self.paths).as_deref() != Some(cid.as_str())
                || !exact_storage_mount(&self.paths).is_some_and(|mount| mount.writable)
            {
                return Err(BackendError::Unavailable);
            }
            storage_worker_request(&self.paths, &cid)
        });
        if let Ok(mut state) = self.storage_format.lock() {
            match result {
                Ok(message) => {
                    state.phase = "succeeded";
                    state.last_output = message;
                }
                Err(_) => {
                    state.phase = "failed";
                    state.last_output =
                        "Formatting failed safely; refresh the card state before retrying."
                            .to_owned();
                }
            }
        }
    }
}

#[cfg(test)]
mod request_validation_tests {
    use super::*;

    struct Fixture {
        root: PathBuf,
        backend: PrudyntBackend,
        listener: std::os::unix::net::UnixListener,
    }

    impl Fixture {
        fn new() -> Self {
            static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
            let root = PathBuf::from(std::env::var_os("TMPDIR").unwrap()).join(format!(
                "sf-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            let mountpoint = root.join("card");
            fs::create_dir(&mountpoint).unwrap();
            let block_root = root.join("block");
            fs::create_dir_all(block_root.join("mmcblk0/device")).unwrap();
            fs::write(block_root.join("mmcblk0/device/cid"), b"0123456789ABCDEF\n").unwrap();
            let proc_mounts = root.join("mounts");
            fs::write(
                &proc_mounts,
                format!("/dev/mmcblk0p1 {} vfat rw 0 0\n", mountpoint.display()),
            )
            .unwrap();
            let socket = root.join("worker");
            let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
            let backend = PrudyntBackend::new(CameraPaths {
                proc_mounts,
                sys_class_block: block_root,
                storage_worker_socket: socket,
                storage_mountpoint: mountpoint,
                media_roots: vec![root.clone()],
                prudynt_config: root.join("prudynt.json"),
                timelapse_config: root.join("timelapse.json"),
                recorder_ch0_active: root.join("rec0"),
                recorder_ch1_active: root.join("rec1"),
                motion_active: root.join("motion"),
                ..CameraPaths::default()
            });
            Self {
                root,
                backend,
                listener,
            }
        }

        fn queue(&self) -> Result<BackendResponse, BackendError> {
            self.backend
                .queue_sd_format(br#"{"action":"format","filesystem":"fat32","confirm":"erase"}"#)
        }

        fn assert_no_worker_request(&self) {
            self.listener.set_nonblocking(true).unwrap();
            assert_eq!(
                self.listener.accept().unwrap_err().kind(),
                io::ErrorKind::WouldBlock
            );
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    #[test]
    fn format_rechecks_card_mount_and_workloads_before_worker_request() {
        for change in 0..9 {
            let fixture = Fixture::new();
            fixture.queue().unwrap();
            assert!(fixture.backend.storage_format_busy());
            assert!(matches!(fixture.queue(), Err(BackendError::Unavailable)));
            fixture.assert_no_worker_request();
            let paths = &fixture.backend.paths;
            match change {
                0 => fs::write(
                    paths.sys_class_block.join("mmcblk0/device/cid"),
                    b"fedcba9876543210",
                )
                .unwrap(),
                1 => fs::write(&paths.proc_mounts, b"").unwrap(),
                2 => fs::write(
                    &paths.proc_mounts,
                    format!(
                        "/dev/mmcblk0p1 {} vfat ro 0 0\n",
                        paths.storage_mountpoint.display()
                    ),
                )
                .unwrap(),
                3 => {
                    let mount = fs::read_to_string(&paths.proc_mounts).unwrap();
                    fs::write(&paths.proc_mounts, format!("{mount}{mount}")).unwrap();
                }
                4 => fs::write(&paths.recorder_ch0_active, b"active").unwrap(),
                5 => fs::write(&paths.recorder_ch1_active, b"active").unwrap(),
                6 => fs::write(&paths.motion_active, b"active").unwrap(),
                7 => {
                    fs::write(&paths.prudynt_config, br#"{"recorder":{"autostart":true}}"#).unwrap()
                }
                8 => fs::write(
                    &paths.timelapse_config,
                    br#"{"timelapse":{"enabled":true}}"#,
                )
                .unwrap(),
                _ => unreachable!(),
            }
            fixture.backend.process_storage_format();
            fixture.assert_no_worker_request();
            assert!(!fixture.backend.storage_format_busy());
            let state = fixture.backend.storage_format.lock().unwrap();
            assert_eq!(state.phase, "failed", "change {change}");
            assert_eq!(state.cid, None);
            assert_eq!(
                state.last_output,
                "Formatting failed safely; refresh the card state before retrying."
            );
        }
    }

    #[test]
    fn format_rejects_enabled_workloads_before_queueing() {
        for change in 0..6 {
            let fixture = Fixture::new();
            let paths = &fixture.backend.paths;
            match change {
                0 => fs::write(&paths.recorder_ch0_active, b"active").unwrap(),
                1 => fs::write(&paths.recorder_ch1_active, b"active").unwrap(),
                2 => fs::write(&paths.motion_active, b"active").unwrap(),
                3 => fs::write(&paths.prudynt_config, br#"{"motion":{"enabled":true}}"#).unwrap(),
                4 => {
                    fs::write(&paths.prudynt_config, br#"{"recorder":{"autostart":true}}"#).unwrap()
                }
                5 => fs::write(
                    &paths.timelapse_config,
                    br#"{"timelapse":{"enabled":true}}"#,
                )
                .unwrap(),
                _ => unreachable!(),
            }
            assert!(matches!(fixture.queue(), Err(BackendError::Unavailable)));
            assert_eq!(fixture.backend.storage_format.lock().unwrap().phase, "idle");
            fixture.assert_no_worker_request();
        }
    }

    #[test]
    fn storage_worker_response_validation_preserves_error_classes() {
        for (length, response, protocol) in [
            (1_u32, vec![], true),
            (257, vec![], true),
            (2, vec![2, 1], true),
            (2, vec![1, 2], true),
            (3, vec![1, 1, 0xff], true),
            (2, vec![1, 0], false),
            (3, vec![1, 1], false),
        ] {
            let fixture = Fixture::new();
            let listener = fixture.listener.try_clone().unwrap();
            let worker = thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0_u8; 23];
                stream.read_exact(&mut request).unwrap();
                assert_eq!(&request[..7], &[0, 0, 0, 19, 1, 1, 16]);
                assert_eq!(&request[7..], b"0123456789abcdef");
                stream.write_all(&length.to_be_bytes()).unwrap();
                stream.write_all(&response).unwrap();
            });
            let result = storage_worker_request(&fixture.backend.paths, "0123456789abcdef");
            worker.join().unwrap();
            if protocol {
                assert!(matches!(result, Err(BackendError::Protocol)));
            } else {
                assert!(matches!(result, Err(BackendError::Unavailable)));
            }
        }
    }

    #[test]
    fn malformed_sd_format_requests_preserve_idle_state() {
        let backend = PrudyntBackend::new(CameraPaths::default());
        for invalid in [
            b"[]".as_slice(),
            br#"{}"#.as_slice(),
            br#"{"action":"erase","filesystem":"fat32","confirm":"erase"}"#.as_slice(),
            br#"{"action":"format","filesystem":"ext4","confirm":"erase"}"#.as_slice(),
            br#"{"action":"format","filesystem":"fat32","confirm":"yes"}"#.as_slice(),
            br#"{"action":"format","filesystem":"fat32","confirm":"erase","extra":true}"#
                .as_slice(),
            br#"{"action":"format","action":"format","filesystem":"fat32","confirm":"erase"}"#
                .as_slice(),
        ] {
            assert!(matches!(
                backend.queue_sd_format(invalid),
                Err(BackendError::Protocol)
            ));
            let state = backend.storage_format.lock().unwrap();
            assert_eq!(state.phase, "idle");
            assert_eq!(state.cid, None);
            assert!(state.last_output.is_empty());
        }
        assert!(!backend.storage_format_busy());
    }
}
