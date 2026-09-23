//! Card-bound format queue and storage worker protocol.

use super::*;

use super::sd::{exact_storage_mount, storage_card_cid};

#[cfg(feature = "raptor-backend")]
pub(crate) enum RaptorStorageWorkerError {
    BeforeDispatch,
    Rejected,
    Uncertain,
}

#[cfg(feature = "raptor-backend")]
fn raptor_storage_worker_request(
    paths: &CameraPaths,
    cid: &str,
    deadline: Instant,
) -> Result<String, RaptorStorageWorkerError> {
    let mut stream = connect_socket(&paths.storage_worker_socket, deadline)
        .map_err(|_| RaptorStorageWorkerError::BeforeDispatch)?;
    let cid = cid.as_bytes();
    let mut request = Vec::with_capacity(cid.len() + 3);
    request.extend_from_slice(&[1, 1, cid.len() as u8]);
    request.extend_from_slice(cid);
    let mut frame = Vec::with_capacity(request.len() + 4);
    frame.extend_from_slice(&(request.len() as u32).to_be_bytes());
    frame.extend_from_slice(&request);
    write_deadline(
        &mut stream,
        &frame,
        deadline.min(Instant::now() + Duration::from_secs(2)),
    )
    .map_err(|_| RaptorStorageWorkerError::Uncertain)?;
    let mut header = [0_u8; 4];
    read_exact_deadline(&mut stream, &mut header, deadline)
        .map_err(|_| RaptorStorageWorkerError::Uncertain)?;
    let length = u32::from_be_bytes(header) as usize;
    if !(2..=256).contains(&length) {
        return Err(RaptorStorageWorkerError::Uncertain);
    }
    let mut response = vec![0_u8; length];
    read_exact_deadline(&mut stream, &mut response, deadline)
        .map_err(|_| RaptorStorageWorkerError::Uncertain)?;
    if response[0] != 1 || !matches!(response[1], 0 | 1) {
        return Err(RaptorStorageWorkerError::Uncertain);
    }
    let message = std::str::from_utf8(&response[2..])
        .map_err(|_| RaptorStorageWorkerError::Uncertain)?
        .to_owned();
    if response[1] == 1 {
        Ok(message)
    } else {
        Err(RaptorStorageWorkerError::Rejected)
    }
}

#[cfg(feature = "raptor-backend")]
impl HostBackend {
    pub(crate) fn raptor_storage_format_target(&self) -> Result<String, BackendError> {
        let mount = exact_storage_mount(&self.paths).ok_or(BackendError::Unavailable)?;
        if !mount.writable
            || !self
                .paths
                .storage_worker_socket
                .symlink_metadata()
                .is_ok_and(|metadata| {
                    !metadata.file_type().is_symlink() && metadata.file_type().is_socket()
                })
        {
            return Err(BackendError::Unavailable);
        }
        storage_card_cid(&self.paths).ok_or(BackendError::Unavailable)
    }

    pub(crate) fn format_raptor_storage(
        &self,
        cid: &str,
        deadline: Instant,
    ) -> Result<String, RaptorStorageWorkerError> {
        if self
            .raptor_storage_format_target()
            .map_err(|_| RaptorStorageWorkerError::BeforeDispatch)?
            .as_str()
            != cid
        {
            return Err(RaptorStorageWorkerError::BeforeDispatch);
        }
        raptor_storage_worker_request(&self.paths, cid, deadline)
    }
}

#[cfg(test)]
mod request_validation_tests {
    use super::*;
    use std::io::Read;

    struct Fixture {
        root: PathBuf,
        backend: HostBackend,
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
            let backend = HostBackend::new(CameraPaths {
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
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
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
            (3, vec![1, 1], true),
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
            let result = fixture
                .backend
                .format_raptor_storage("0123456789abcdef", Instant::now() + Duration::from_secs(2));
            worker.join().unwrap();
            if protocol {
                assert!(matches!(result, Err(RaptorStorageWorkerError::Uncertain)));
            } else {
                assert!(matches!(result, Err(RaptorStorageWorkerError::Rejected)));
            }
        }
    }
}
