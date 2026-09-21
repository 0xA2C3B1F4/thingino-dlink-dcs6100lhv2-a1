use std::env;
use std::fs::{self, File};
use std::io::{self, Read};
use std::net::{SocketAddr, TcpListener};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use thingino_control::{
    Backend, HttpBackend, RaptorBackend, WebAuth, WebAuthPaths, WhipProxy,
    control_token_from_bytes, serve_with_web_auth_and_whip,
};

#[path = "../storage-worker/mod.rs"]
mod storage_worker;

struct Options {
    listen: SocketAddr,
    backend: Option<SocketAddr>,
    camera: bool,
    token_file: PathBuf,
    whip_backend: Option<SocketAddr>,
}

fn parse_options() -> Result<Options, String> {
    parse_arguments(env::args().skip(1))
}

fn parse_arguments(arguments: impl IntoIterator<Item = String>) -> Result<Options, String> {
    let mut listen = None;
    let mut backend = None;
    let mut camera = false;
    let mut token_file = None;
    let mut whip_backend = None;
    let mut media_backend_selected = false;
    let mut seen = std::collections::HashSet::new();
    let mut arguments = arguments.into_iter();
    while let Some(argument) = arguments.next() {
        if !seen.insert(argument.clone()) {
            return Err(format!("duplicate option: {argument}"));
        }
        match argument.as_str() {
            "--listen" => {
                let value = arguments.next().ok_or("--listen requires an address")?;
                listen = Some(value.parse().map_err(|_| "invalid --listen address")?);
            }
            "--backend" => {
                let value = arguments.next().ok_or("--backend requires an address")?;
                backend = Some(value.parse().map_err(|_| "invalid --backend address")?);
            }
            "--camera" => camera = true,
            "--media-backend" => {
                let value = arguments.next().ok_or("--media-backend requires a name")?;
                if value != "raptor" {
                    return Err("unsupported --media-backend; only raptor is available".to_owned());
                }
                media_backend_selected = true;
            }
            "--token-file" => {
                token_file = Some(PathBuf::from(
                    arguments.next().ok_or("--token-file requires a path")?,
                ));
            }
            "--whip-backend" => {
                let value = arguments
                    .next()
                    .ok_or("--whip-backend requires an address")?;
                let address: SocketAddr = value
                    .parse()
                    .map_err(|_| "invalid --whip-backend address")?;
                if !address.ip().is_loopback() {
                    return Err("--whip-backend must use a loopback address".to_owned());
                }
                whip_backend = Some(address);
            }
            "--help" | "-h" => {
                return Err(
                    "usage: thingino-controld --listen ADDR (--camera [--media-backend NAME] | --backend ADDR) --token-file PATH [--whip-backend LOOPBACK_ADDR]".to_owned(),
                );
            }
            _ => return Err(format!("unknown argument: {argument}")),
        }
    }
    if camera == backend.is_some() {
        return Err("exactly one of --camera or --backend is required".to_owned());
    }
    if media_backend_selected && !camera {
        return Err("--media-backend requires --camera".to_owned());
    }
    if camera && whip_backend.is_some_and(|address: SocketAddr| address.port() == 8554) {
        return Err(
            "Raptor RSD owns port 8554; configure RWD and --whip-backend on a distinct port"
                .to_owned(),
        );
    }
    let listen: SocketAddr = listen.ok_or("--listen is required")?;
    if camera && !listen.ip().is_loopback() {
        return Err("--camera listener must use a loopback address".to_owned());
    }
    Ok(Options {
        listen,
        backend,
        camera,
        token_file: token_file.ok_or("--token-file is required")?,
        whip_backend,
    })
}

fn load_token(path: &PathBuf) -> io::Result<Vec<u8>> {
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "token input must not be a symbolic link",
        ));
    }
    let file = File::open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 65536 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "token input must contain 1 to 65536 bytes",
        ));
    }
    let mut token = Vec::with_capacity(metadata.len() as usize);
    file.take(65537).read_to_end(&mut token)?;
    if token.len() > 65536 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "token input must contain 1 to 65536 bytes",
        ));
    }
    if token.first() == Some(&b'{') {
        return control_token_from_bytes(&token).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "Thingino config lacks a valid Control token",
            )
        });
    }
    while token
        .last()
        .is_some_and(|byte| matches!(byte, b'\n' | b'\r'))
    {
        token.pop();
    }
    if token.is_empty() || token.len() > 4096 || token.iter().any(|byte| byte.is_ascii_whitespace())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "raw token must contain 1 to 4096 non-whitespace bytes",
        ));
    }
    Ok(token)
}

fn run() -> Result<(), String> {
    #[cfg(feature = "raptor-backend")]
    if env::args().nth(1).as_deref() == Some("--quiesce-raptor-storage") {
        if env::args().count() != 2 {
            return Err("--quiesce-raptor-storage accepts no arguments".to_owned());
        }
        quiesce_raptor_storage(
            std::path::Path::new("/run"),
            0,
            std::time::Instant::now() + std::time::Duration::from_secs(5),
        )
        .map_err(|_| "Raptor storage quiescence was not confirmed".to_owned())?;
        println!("raptor_storage=quiesced");
        return Ok(());
    }
    #[cfg(feature = "raptor-backend")]
    if env::args().nth(1).as_deref() == Some("--check-raptor-runtime") {
        if env::args().count() != 2 {
            return Err("--check-raptor-runtime accepts no arguments".to_owned());
        }
        let backend = thingino_control::RaptorBackend::camera_defaults()
            .map_err(|_| "Raptor runtime is unavailable".to_owned())?;
        check_raptor_runtime(&backend).map_err(|_| "Raptor runtime is not ready".to_owned())?;
        println!("raptor_runtime=ready");
        return Ok(());
    }
    #[cfg(feature = "raptor-backend")]
    if env::args().nth(1).as_deref() == Some("--check-raptor-config") {
        if env::args().count() != 2 {
            return Err("--check-raptor-config accepts no arguments".to_owned());
        }
        check_raptor_config(&PathBuf::from("/etc/thingino.json"))
            .map_err(|_| "Raptor configuration schema is incompatible".to_owned())?;
        println!("raptor_config_schema=compatible");
        return Ok(());
    }
    if env::args().nth(1).as_deref() == Some("--storage-worker") {
        return storage_worker::run_default();
    }
    let options = parse_options()?;
    let token = load_token(&options.token_file).map_err(|error| error.to_string())?;
    if options.camera && (token.len() != 64 || !token.iter().all(|byte| byte.is_ascii_hexdigit())) {
        return Err("camera token must contain exactly 64 hexadecimal characters".to_owned());
    }
    let listener = TcpListener::bind(options.listen).map_err(|error| error.to_string())?;
    let address = listener.local_addr().map_err(|error| error.to_string())?;
    println!("listening on {address}");
    let backend: Arc<dyn Backend> = if options.camera {
        let backend =
            Arc::new(RaptorBackend::camera_defaults().map_err(|error| error.to_string())?);
        backend
            .start_motion_lifecycle()
            .map_err(|error| error.to_string())?;
        backend.start_ha();
        backend
            .start_timelapse()
            .map_err(|error| error.to_string())?;
        backend
            .start_storage_reader()
            .map_err(|error| error.to_string())?;
        backend
            .start_storage_format()
            .map_err(|error| error.to_string())?;
        backend
    } else {
        Arc::new(HttpBackend::new(
            options.backend.expect("validated fixture backend"),
        ))
    };
    let web_auth = options
        .camera
        .then(|| WebAuth::new(WebAuthPaths::default()));
    let whip = options.whip_backend.map(WhipProxy::new);
    serve_with_web_auth_and_whip(
        listener,
        backend,
        token,
        web_auth,
        whip,
        Arc::new(AtomicBool::new(false)),
    )
    .map_err(|error| error.to_string())
}

#[cfg(feature = "raptor-backend")]
fn quiesce_raptor_storage(
    run_root: &std::path::Path,
    owner: u32,
    deadline: std::time::Instant,
) -> io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    use std::time::{Duration, Instant};

    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "storage barrier rejected");
    let budget = || {
        deadline
            .checked_duration_since(Instant::now())
            .filter(|left| !left.is_zero())
            .ok_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "storage barrier deadline"))
    };
    budget()?;
    for (directory, private) in [
        (run_root.to_path_buf(), false),
        (run_root.join("thingino-control"), true),
        (run_root.join("raptor-full"), true),
        (run_root.join("raptor-full/state"), true),
    ] {
        let metadata = fs::symlink_metadata(directory)?;
        if !metadata.is_dir()
            || metadata.file_type().is_symlink()
            || metadata.uid() != owner
            || (private && metadata.mode() & 0o7777 != 0o700)
            || (!private && metadata.mode() & 0o022 != 0)
        {
            return Err(invalid());
        }
    }
    let source = run_root.join("thingino-control/storage-v1.sock");
    let destination = run_root.join("raptor-full/state/storage.sock");
    let identity = |path: &std::path::Path| -> io::Result<(u64, u64)> {
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_socket()
            || metadata.uid() != owner
            || metadata.mode() & 0o7777 != 0o600
        {
            return Err(invalid());
        }
        Ok((metadata.dev(), metadata.ino()))
    };
    let original = identity(&source)?;
    match fs::symlink_metadata(&destination) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        _ => return Err(invalid()),
    }
    budget()?;
    // The caller owns the private lifecycle directory and has stopped Control.
    // Detach before connecting so no later request can enter the original name.
    // Keep the socket detached on every failure for identity-aware recovery.
    fs::rename(&source, &destination)?;
    let source_absent = || matches!(fs::symlink_metadata(&source), Err(error) if error.kind() == io::ErrorKind::NotFound);
    if identity(&destination)? != original || !source_absent() {
        return Err(invalid());
    }
    let mut stream = thingino_control::connect_raptor_unix_deadline(&destination, deadline)
        .map_err(|_| {
            io::Error::new(io::ErrorKind::TimedOut, "storage barrier connection failed")
        })?;
    // Invalid version AND opcode; never a format-card request. The sequential
    // worker's unsupported-request response is a barrier behind earlier work.
    let request = [0, 0, 0, 4, 0, 0, 0, 0];
    let mut written = 0;
    while written < request.len() {
        budget()?;
        match stream.write(&request[written..]) {
            Ok(0) => return Err(invalid()),
            Ok(count) => written += count,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(budget()?.min(Duration::from_millis(5)));
            }
            Err(error) => return Err(error),
        }
    }
    let expected_body = b"\x01\x00unsupported-request";
    let mut expected = (expected_body.len() as u32).to_be_bytes().to_vec();
    expected.extend_from_slice(expected_body);
    let mut received = 0;
    loop {
        budget()?;
        let mut bytes = [0; 32];
        match stream.read(&mut bytes) {
            Ok(0) if received == expected.len() => break,
            Ok(0) => return Err(invalid()),
            Ok(count) => {
                if received + count > expected.len()
                    || bytes[..count] != expected[received..received + count]
                {
                    return Err(invalid());
                }
                received += count;
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(budget()?.min(Duration::from_millis(5)));
            }
            Err(error) => return Err(error),
        }
    }
    if identity(&destination)? != original || !source_absent() {
        return Err(invalid());
    }
    Ok(())
}

#[cfg(feature = "raptor-backend")]
fn check_raptor_runtime(backend: &dyn Backend) -> Result<(), thingino_control::BackendError> {
    use thingino_control::BackendRoute;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    for route in [
        BackendRoute::Health,
        BackendRoute::RuntimeMedia,
        BackendRoute::Snapshot(0),
        BackendRoute::Snapshot(1),
    ] {
        backend.request(route, deadline)?;
    }
    Ok(())
}

#[cfg(feature = "raptor-backend")]
fn check_raptor_config(path: &std::path::Path) -> io::Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    // Linux AArch64 retains the ARM fcntl layout; MIPS uses 0x20000.
    #[cfg(all(target_os = "linux", target_arch = "aarch64"))]
    const NOFOLLOW: i32 = 0x8000;
    #[cfg(all(target_os = "linux", not(target_arch = "aarch64")))]
    const NOFOLLOW: i32 = 0x20000;
    #[cfg(target_os = "macos")]
    const NOFOLLOW: i32 = 0x100;
    #[cfg(all(target_os = "linux", target_arch = "mips"))]
    const NONBLOCK: i32 = 0x80;
    #[cfg(all(target_os = "linux", not(target_arch = "mips")))]
    const NONBLOCK: i32 = 0x800;
    #[cfg(target_os = "macos")]
    const NONBLOCK: i32 = 4;
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW | NONBLOCK)
        .open(path)?;
    let metadata = file.metadata()?;
    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "incompatible config schema");
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 65536 {
        return Err(invalid());
    }
    let mut raw = Vec::with_capacity(metadata.len() as usize);
    file.take(65537).read_to_end(&mut raw)?;
    if raw.len() != metadata.len() as usize
        || raw.first() != Some(&b'{')
        || control_token_from_bytes(&raw).is_none()
    {
        return Err(invalid());
    }
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("thingino-controld: {message}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod option_tests {
    use super::*;

    #[cfg(feature = "raptor-backend")]
    fn storage_fixture() -> (PathBuf, u32, std::os::unix::net::UnixListener) {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        static NEXT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
        let parent = PathBuf::from(env::var_os("TMPDIR").expect("task TMPDIR required"))
            .canonicalize()
            .unwrap();
        let root = parent.join(format!(
            "sq{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        for path in [
            &root,
            &root.join("thingino-control"),
            &root.join("raptor-full"),
            &root.join("raptor-full/state"),
        ] {
            if path != &root {
                fs::create_dir(path).unwrap();
            }
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let socket = root.join("thingino-control/storage-v1.sock");
        let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        fs::set_permissions(socket, fs::Permissions::from_mode(0o600)).unwrap();
        let owner = fs::metadata(&root).unwrap().uid();
        (root, owner, listener)
    }

    #[cfg(feature = "raptor-backend")]
    fn storage_reply() -> Vec<u8> {
        let body = b"\x01\x00unsupported-request";
        let mut reply = (body.len() as u32).to_be_bytes().to_vec();
        reply.extend_from_slice(body);
        reply
    }

    #[cfg(feature = "raptor-backend")]
    #[test]
    fn storage_quiesce_detaches_before_connect_and_waits_for_prior_work() {
        use std::io::Write;
        use std::os::unix::fs::MetadataExt;
        use std::time::{Duration, Instant};
        let (root, owner, listener) = storage_fixture();
        let source = root.join("thingino-control/storage-v1.sock");
        let before = fs::metadata(&source).unwrap();
        let prior = std::os::unix::net::UnixStream::connect(&source).unwrap();
        let thread_root = root.clone();
        let worker = std::thread::spawn(move || {
            let (old, _) = listener.accept().unwrap();
            std::thread::sleep(Duration::from_millis(30));
            drop(old);
            let (mut stream, _) = listener.accept().unwrap();
            assert!(
                !thread_root
                    .join("thingino-control/storage-v1.sock")
                    .exists()
            );
            assert!(thread_root.join("raptor-full/state/storage.sock").exists());
            let mut request = [255; 8];
            stream.read_exact(&mut request).unwrap();
            assert_eq!(request, [0, 0, 0, 4, 0, 0, 0, 0]);
            for byte in storage_reply() {
                stream.write_all(&[byte]).unwrap();
            }
        });
        quiesce_raptor_storage(&root, owner, Instant::now() + Duration::from_secs(1)).unwrap();
        worker.join().unwrap();
        drop(prior);
        let after = fs::metadata(root.join("raptor-full/state/storage.sock")).unwrap();
        assert_eq!((before.dev(), before.ino()), (after.dev(), after.ino()));
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(feature = "raptor-backend")]
    #[test]
    fn storage_quiesce_rejects_malformed_or_trailing_reply_and_retains_socket() {
        use std::io::Write;
        use std::time::{Duration, Instant};
        let mut trailing = storage_reply();
        trailing.push(0);
        for reply in [vec![255; 4], vec![0, 0, 0, 1, 1], trailing] {
            let (root, owner, listener) = storage_fixture();
            let worker = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0; 8];
                stream.read_exact(&mut request).unwrap();
                stream.write_all(&reply).unwrap();
            });
            assert!(
                quiesce_raptor_storage(&root, owner, Instant::now() + Duration::from_secs(1))
                    .is_err()
            );
            worker.join().unwrap();
            assert!(root.join("raptor-full/state/storage.sock").exists());
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[cfg(feature = "raptor-backend")]
    #[test]
    fn storage_quiesce_has_one_deadline_and_requires_eof() {
        use std::io::Write;
        use std::time::{Duration, Instant};
        let (root, owner, listener) = storage_fixture();
        let worker = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0; 8];
            stream.read_exact(&mut request).unwrap();
            stream.write_all(&storage_reply()).unwrap();
            std::thread::sleep(Duration::from_millis(100));
        });
        let began = Instant::now();
        assert_eq!(
            quiesce_raptor_storage(&root, owner, began + Duration::from_millis(40))
                .unwrap_err()
                .kind(),
            io::ErrorKind::TimedOut
        );
        assert!(began.elapsed() < Duration::from_millis(90));
        worker.join().unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(feature = "raptor-backend")]
    #[test]
    fn storage_quiesce_refuses_bad_owner_mode_links_and_existing_destination() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        use std::time::{Duration, Instant};
        for case in 0..7 {
            let (root, owner, _listener) = storage_fixture();
            let source = root.join("thingino-control/storage-v1.sock");
            let destination = root.join("raptor-full/state/storage.sock");
            match case {
                0 => fs::write(&destination, b"preserve").unwrap(),
                1 => symlink(&source, &destination).unwrap(),
                2 => fs::set_permissions(&source, fs::Permissions::from_mode(0o666)).unwrap(),
                3 => {
                    fs::rename(&source, root.join("real.sock")).unwrap();
                    symlink(root.join("real.sock"), &source).unwrap();
                }
                5 => fs::set_permissions(
                    root.join("thingino-control"),
                    fs::Permissions::from_mode(0o777),
                )
                .unwrap(),
                6 => {
                    fs::rename(root.join("thingino-control"), root.join("real-control")).unwrap();
                    symlink(root.join("real-control"), root.join("thingino-control")).unwrap();
                }
                _ => {}
            }
            let expected_owner = if case == 4 {
                owner.wrapping_add(1)
            } else {
                owner
            };
            assert!(
                quiesce_raptor_storage(
                    &root,
                    expected_owner,
                    Instant::now() + Duration::from_secs(1)
                )
                .is_err()
            );
            assert!(fs::symlink_metadata(source).is_ok());
            if case == 0 {
                assert_eq!(fs::read(destination).unwrap(), b"preserve");
            }
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[cfg(feature = "raptor-backend")]
    #[test]
    fn readiness_requires_every_probe_with_one_deadline_and_stops_on_failure() {
        use std::sync::Mutex;
        use std::time::Instant;
        use thingino_control::{BackendError, BackendResponse, BackendRoute};
        struct Probe {
            seen: Mutex<Vec<(BackendRoute, Instant)>>,
            fail_at: usize,
        }
        impl Backend for Probe {
            fn request(
                &self,
                route: BackendRoute,
                deadline: Instant,
            ) -> Result<BackendResponse, BackendError> {
                let mut seen = self.seen.lock().unwrap();
                seen.push((route, deadline));
                if seen.len() == self.fail_at {
                    return Err(BackendError::Unavailable);
                }
                Ok(BackendResponse::json(b"{}".to_vec()))
            }
        }
        for fail_at in 1..=5 {
            let probe = Probe {
                seen: Mutex::new(Vec::new()),
                fail_at,
            };
            let result = check_raptor_runtime(&probe);
            let seen = probe.seen.lock().unwrap();
            assert_eq!(result.is_ok(), fail_at == 5);
            assert_eq!(seen.len(), fail_at.min(4));
            assert!(seen.iter().all(|(_, deadline)| *deadline == seen[0].1));
            if fail_at == 5 {
                assert!(matches!(seen[2].0, BackendRoute::Snapshot(0)));
                assert!(matches!(seen[3].0, BackendRoute::Snapshot(1)));
            }
        }
    }

    #[cfg(feature = "raptor-backend")]
    #[test]
    fn raptor_config_check_is_bounded_read_only_and_rejects_links_and_raw_tokens() {
        use std::os::unix::fs::symlink;
        let root = PathBuf::from(env::var_os("TMPDIR").expect("task TMPDIR required"))
            .join(format!("raptor-config-check-{}", std::process::id()));
        fs::create_dir(&root).unwrap();
        let path = root.join("config");
        let raw = format!("{{\"control\":{{\"token\":\"{}\"}}}}", "a".repeat(64));
        fs::write(&path, &raw).unwrap();
        let before = fs::metadata(&path).unwrap().modified().unwrap();
        check_raptor_config(&path).unwrap();
        assert_eq!(fs::read(&path).unwrap(), raw.as_bytes());
        assert_eq!(fs::metadata(&path).unwrap().modified().unwrap(), before);
        let link = root.join("link");
        symlink(&path, &link).unwrap();
        assert!(check_raptor_config(&link).is_err());
        assert!(check_raptor_config(&root).is_err());
        for bytes in [
            Vec::new(),
            b"a".repeat(64),
            b"x".repeat(65537),
            br#"{"control":{"token":12}}"#.to_vec(),
        ] {
            fs::write(&path, bytes).unwrap();
            assert!(check_raptor_config(&path).is_err());
        }
        fs::remove_dir_all(root).unwrap();
    }

    fn options(extra: &[&str]) -> Result<Options, String> {
        parse_arguments(
            ["--listen", "127.0.0.1:9080", "--token-file", "token"]
                .into_iter()
                .chain(extra.iter().copied())
                .map(str::to_owned),
        )
    }

    #[test]
    fn media_selection_defaults_to_raptor_and_requires_camera() {
        assert!(options(&["--camera"]).is_ok());
        assert!(options(&["--camera", "--media-backend", "raptor"]).is_ok());
        assert!(options(&["--camera", "--media-backend", "prudynt"]).is_err());
        assert!(options(&["--backend", "127.0.0.1:9999", "--media-backend", "prudynt"]).is_err());
        assert!(options(&["--camera", "--media-backend", "invalid"]).is_err());
        assert!(options(&["--camera", "--media-backend"]).is_err());
        assert!(options(&["--camera", "--camera"]).is_err());
        assert!(
            options(&[
                "--camera",
                "--media-backend",
                "raptor",
                "--media-backend",
                "raptor"
            ])
            .is_err()
        );
        assert!(options(&["--camera", "--listen", "0.0.0.0:9080"]).is_err());
        assert!(options(&["--camera", "--whip-backend", "192.0.2.1:8080"]).is_err());
    }

    #[test]
    fn raptor_selection_reserves_its_rtsp_port() {
        assert!(options(&["--camera", "--media-backend", "raptor"]).is_ok());
        let signaling = options(&[
            "--camera",
            "--media-backend",
            "raptor",
            "--whip-backend",
            "127.0.0.1:8555",
        ]);
        assert!(signaling.is_ok());
        assert!(
            options(&[
                "--camera",
                "--media-backend",
                "raptor",
                "--whip-backend",
                "127.0.0.1:8554",
            ])
            .is_err()
        );
    }
}
