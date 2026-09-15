use super::super::tests::{backend, task_temp};
use super::super::timelapse::{Service, storage::Store};
use super::*;
use std::sync::Arc;

struct Fixture {
    path: PathBuf,
    backend: Arc<RaptorBackend>,
}
impl Fixture {
    fn new() -> Self {
        Self::with_fat(false)
    }
    fn with_fat(fat: bool) -> Self {
        let path = task_temp("recording-files");
        let mount = path.join("sd");
        for dir in ["stream0/2026-09-11", "stream1/2026-09-11", "timelapse"] {
            fs::create_dir_all(mount.join("raptor").join(dir)).unwrap();
        }
        let mut backend = backend(&path, "127.0.0.1:9".parse().unwrap());
        backend.timelapse = Arc::new(Service::new(
            path.join("timelapse.json"),
            Store {
                mount,
                fixture: true,
                fixture_fat: fat,
                ..Store::default()
            },
        ));
        Self {
            path,
            backend: Arc::new(backend),
        }
    }
    fn disk(&self, relative: &str) -> PathBuf {
        self.path.join("sd/raptor").join(relative)
    }
    fn write(&self, owner: usize, name: &str, closed: bool) -> String {
        let relative = format!(
            "{}/{}{}",
            OWNERS[owner],
            if owner < 2 { "2026-09-11/" } else { "" },
            name
        );
        let path = self.disk(&relative);
        fs::write(&path, b"recording bytes").unwrap();
        let metadata = path.metadata().unwrap();
        if closed {
            let record = if owner < 2 {
                format!(
                    "RMR1 {owner} {} {} {}\n",
                    metadata.len(),
                    metadata.mtime(),
                    metadata.mtime_nsec()
                )
            } else {
                format!(
                    "TL1 {} {} {} {}\n",
                    jpeg(name).unwrap(),
                    metadata.len(),
                    metadata.mtime(),
                    metadata.mtime_nsec()
                )
            };
            fs::write(
                format!(
                    "{}.{}",
                    path.display(),
                    if owner < 2 { "rmr-owned" } else { "tl-owned" }
                ),
                record,
            )
            .unwrap();
        }
        format!("{ROOT}/{relative}")
    }
    fn list(&self, path: &str) -> Value {
        let result = self
            .backend
            .recording_files(
                "GET",
                &format!("/api/v1/files?cd={path}"),
                b"",
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap();
        crate::json::parse(&result.body).unwrap()
    }
    fn authorize(&self, path: &str) -> Option<MediaFileIdentity> {
        self.backend
            .media_file_identity(
                &format!("/media/v1/file?path={path}&play=1"),
                Instant::now() + Duration::from_secs(1),
            )
            .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.path).unwrap();
    }
}

#[test]
fn lists_only_closed_files_with_correct_owner_while_new_writer_is_active() {
    let f = Fixture::new();
    let main = f.write(0, "12-00-00.mp4", true);
    f.write(0, "12-01-00.mp4", false);
    let sub = f.write(1, "12-00-00.mp4", true);
    fs::write(f.disk("stream0/2026-09-11/private.txt"), b"not a recording").unwrap();
    let entries = f.list(&format!("{ROOT}/stream0/2026-09-11"));
    let entries = entries.get_path("entries").unwrap().as_array().unwrap();
    assert_eq!(entries.len(), 1);
    assert_eq!(
        entries[0].get_path("path").and_then(Value::as_str),
        Some(main.as_str())
    );
    assert_eq!(
        entries[0].get_path("deletable").and_then(Value::as_bool),
        Some(true)
    );
    assert!(f.authorize(&main).is_some());
    assert!(f.authorize(&sub).is_some());
    fs::copy(
        f.disk("stream0/2026-09-11/12-00-00.mp4.rmr-owned"),
        f.disk("stream1/2026-09-11/12-00-00.mp4.rmr-owned"),
    )
    .unwrap();
    assert!(f.authorize(&sub).is_none());
    assert!(f.authorize(&main).is_some());
}

#[test]
fn timelapse_pending_and_internal_markers_are_never_offered_even_after_restart() {
    let f = Fixture::new();
    let good = f.write(2, "100-1.jpg", true);
    let pending = f.write(2, "100-2.jpg", true);
    fs::write(f.disk("timelapse/.pending-100-2"), b"staged").unwrap();
    f.write(2, "100-3.jpg", false);
    assert!(f.authorize(&good).is_some());
    assert!(f.authorize(&pending).is_none());
    assert!(f.authorize(&format!("{good}.tl-owned")).is_none());
    assert!(
        f.authorize(&format!("{ROOT}/timelapse/.pending-100-2"))
            .is_none()
    );
    let list = f.list(&format!("{ROOT}/timelapse"));
    assert_eq!(
        list.get_path("entries").unwrap().as_array().unwrap().len(),
        1
    );
    // A fresh backend has no in-memory publication state; the pending file still blocks access.
    let mut restarted = backend(&f.path, "127.0.0.1:9".parse().unwrap());
    restarted.timelapse = Arc::new(Service::new(
        f.path.join("restart.json"),
        Store {
            mount: f.path.join("sd"),
            fixture: true,
            ..Store::default()
        },
    ));
    assert!(
        restarted
            .recording_identity(
                &format!("/media/v1/file?path={pending}"),
                Instant::now() + Duration::from_secs(1)
            )
            .unwrap()
            .is_none()
    );
}

#[test]
fn rejects_altered_hardlinked_symlinked_and_outside_paths() {
    let f = Fixture::new();
    let main = f.write(0, "12-00-00.mp4", true);
    let id = f.authorize(&main).unwrap();
    assert!(id.header().starts_with("v1 "));
    fs::write(f.disk("stream0/2026-09-11/12-00-00.mp4"), b"changed").unwrap();
    assert!(f.authorize(&main).is_none());
    let main = f.write(0, "12-00-01.mp4", true);
    fs::hard_link(
        f.disk("stream0/2026-09-11/12-00-01.mp4"),
        f.path.join("link"),
    )
    .unwrap();
    assert!(f.authorize(&main).is_none());
    for path in [
        "/etc/passwd",
        "/mnt/mmcblk0p1/other.mp4",
        "/mnt/mmcblk0p1/raptor/stream2/2026-09-11/12-00-00.mp4",
        "/mnt/mmcblk0p1/raptor/stream0/../timelapse/100-1.jpg",
        "/mnt/mmcblk0p1/raptor/stream0//2026-09-11/12-00-00.mp4",
    ] {
        assert!(f.authorize(path).is_none(), "{path}");
    }
    fs::rename(f.disk("stream0/2026-09-11"), f.path.join("day")).unwrap();
    std::os::unix::fs::symlink(f.path.join("day"), f.disk("stream0/2026-09-11")).unwrap();
    assert!(f.authorize(&main).is_none());
    assert!(
        f.backend
            .recording_files(
                "GET",
                &format!("/api/v1/files?cd={ROOT}/stream0/2026-09-11"),
                b"",
                Instant::now() + Duration::from_secs(1)
            )
            .is_err()
    );
}

#[test]
fn strict_query_deletion_and_storage_format_refusal() {
    let f = Fixture::new();
    let path = f.write(0, "12-00-00.mp4", true);
    for suffix in [
        "&stream=1",
        "&play=1&download=1",
        "&path=/etc/passwd",
        "&unknown=1",
        "&",
        "&download=0",
    ] {
        assert!(
            f.backend
                .recording_identity(
                    &format!("/media/v1/file?path={path}{suffix}"),
                    Instant::now() + Duration::from_secs(1)
                )
                .unwrap()
                .is_none()
        );
    }
    assert!(
        f.backend
            .recording_files(
                "POST",
                &format!("/api/v1/files?rm={path}"),
                b"",
                Instant::now() + Duration::from_secs(1),
            )
            .is_ok()
    );
    assert!(!f.disk("stream0/2026-09-11/12-00-00.mp4").exists());
    assert!(!f.disk("stream0/2026-09-11/12-00-00.mp4.rmr-owned").exists());
    assert!(
        f.backend
            .recording_files(
                "POST",
                &format!("/api/v1/files/text?file={path}"),
                b"",
                Instant::now() + Duration::from_secs(1),
            )
            .is_err()
    );
    assert!(
        f.backend
            .api_request(
                "POST",
                "/api/v1/storage/sd",
                br#"{"action":"format","confirm":"erase","filesystem":"fat32"}"#,
                Instant::now()
            )
            .unwrap()
            .is_err()
    );
    let sd = f
        .backend
        .recording_sd(Instant::now() + Duration::from_secs(1))
        .unwrap();
    let sd = crate::json::parse(&sd.body).unwrap();
    assert_eq!(
        sd.get_path("data.format.supported")
            .and_then(Value::as_bool),
        Some(false)
    );
    assert_eq!(
        sd.get_path("data.has_sdcard").and_then(Value::as_bool),
        Some(true)
    );
    assert!(!sd.to_json().contains("cid"));
}

#[test]
fn deletion_requires_an_unchanged_closed_owned_file_and_exact_request() {
    let f = Fixture::new();
    let open = f.write(0, "12-00-00.mp4", false);
    let linked = f.write(0, "12-00-01.mp4", true);
    fs::hard_link(
        f.disk("stream0/2026-09-11/12-00-01.mp4"),
        f.path.join("outside-link"),
    )
    .unwrap();
    let pending = f.write(2, "100-1.jpg", true);
    fs::write(f.disk("timelapse/.pending-100-1"), b"pending").unwrap();
    for target in [open, linked, pending] {
        assert!(matches!(
            f.backend.recording_files(
                "POST",
                &format!("/api/v1/files?rm={target}"),
                b"",
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Unavailable)
        ));
    }
    let closed = f.write(1, "12-00-00.mp4", true);
    for (target, body) in [
        (format!("/api/v1/files?rm={closed}&extra=1"), b"".as_slice()),
        (format!("/api/v1/files?rm={closed}"), b"x".as_slice()),
        ("/api/v1/files?rm=/etc/passwd".to_owned(), b"".as_slice()),
    ] {
        assert_eq!(
            f.backend.recording_files(
                "POST",
                &target,
                body,
                Instant::now() + Duration::from_secs(1),
            ),
            Err(BackendError::Protocol)
        );
    }
    assert!(f.disk("stream1/2026-09-11/12-00-00.mp4").exists());
}

#[test]
fn root_breadcrumbs_do_not_escape_recordings_and_listing_is_explicitly_bounded() {
    let f = Fixture::new();
    let roots = f.list("/");
    assert_eq!(
        roots.get_path("entries").unwrap().as_array().unwrap().len(),
        3
    );
    for i in 0..520 {
        f.write(2, &format!("100-{i}.jpg"), true);
    }
    let list = f.list(&format!("{ROOT}/timelapse"));
    assert_eq!(list.get_path("parent").and_then(Value::as_str), Some("/"));
    assert_eq!(
        list.get_path("entries").unwrap().as_array().unwrap().len(),
        512
    );
    assert_eq!(
        list.get_path("truncated").and_then(Value::as_bool),
        Some(true)
    );
}

fn page(f: &Fixture, path: &str, cursor: Option<&str>) -> Result<Value, BackendError> {
    let target = format!(
        "/api/v1/files?cd={path}{}",
        cursor.map(|s| format!("&cursor={s}")).unwrap_or_default()
    );
    f.backend
        .recording_files("GET", &target, b"", Instant::now() + Duration::from_secs(1))
        .map(|r| crate::json::parse(&r.body).unwrap())
}
#[test]
fn continuation_reaches_all_images_and_skips_large_mixed_directories_without_duplicates() {
    let f = Fixture::new();
    let path = format!("{ROOT}/timelapse");
    for i in 0..620 {
        f.write(2, &format!("100-{i}.jpg"), true);
    }
    for i in 0..5000 {
        fs::write(f.disk(&format!("timelapse/unrelated-{i}")), b"x").unwrap();
    }
    let mut cursor: Option<String> = None;
    let mut seen = std::collections::BTreeSet::new();
    let mut pages = 0;
    loop {
        let result = page(&f, &path, cursor.as_deref()).unwrap();
        let entries = result.get_path("entries").unwrap().as_array().unwrap();
        assert!(entries.len() <= 512);
        for entry in entries {
            assert!(seen.insert(entry.get_path("name").unwrap().as_str().unwrap().to_owned()));
        }
        if let Some(old) = cursor.as_deref() {
            assert_eq!(page(&f, &path, Some(old)), Err(BackendError::Protocol));
        }
        cursor = result
            .get_path("next_cursor")
            .and_then(Value::as_str)
            .map(str::to_owned);
        pages += 1;
        assert!(pages < 100, "continuation must make progress");
        if cursor.is_none() {
            break;
        }
    }
    assert!(pages >= 2);
    assert_eq!(seen.len(), 620);
    assert!(seen.contains("100-619.jpg"));
}
#[test]
fn continuation_rejects_wrong_folder_changed_folder_and_evicted_cursors() {
    let f = Fixture::new();
    let path = format!("{ROOT}/timelapse");
    for i in 0..520 {
        f.write(2, &format!("100-{i}.jpg"), true);
    }
    let first = page(&f, &path, None).unwrap();
    let cursor = first.get_path("next_cursor").unwrap().as_str().unwrap();
    assert_eq!(
        page(&f, &format!("{ROOT}/stream0"), Some(cursor)),
        Err(BackendError::Protocol)
    );
    for _ in 0..4 {
        assert!(
            page(&f, &path, None)
                .unwrap()
                .get_path("next_cursor")
                .unwrap()
                .as_str()
                .is_some()
        );
    }
    assert_eq!(page(&f, &path, Some(cursor)), Err(BackendError::Protocol));
    let current = page(&f, &path, None).unwrap();
    let cursor = current.get_path("next_cursor").unwrap().as_str().unwrap();
    f.write(2, "101-0.jpg", true);
    assert_eq!(page(&f, &path, Some(cursor)), Err(BackendError::Protocol));
    assert_eq!(
        page(&f, &path, Some("invalid-cursor")),
        Err(BackendError::Protocol)
    );
}

#[test]
fn stalled_sd_reads_leave_http_health_and_live_responsive_and_expired_jobs_never_run() {
    use super::super::tests::{RHD_OK, RVD_OK, serve_daemon};
    use std::net::TcpListener;
    use std::sync::{
        Condvar, Mutex,
        atomic::{AtomicBool, AtomicUsize},
        mpsc,
    };
    use std::thread;
    let mut f = Fixture::new();
    Arc::get_mut(&mut f.backend).unwrap().snapshot_address = "127.0.0.1:8080".parse().unwrap();
    let file = f.write(2, "100-1.jpg", true);
    let barrier = Arc::new((Mutex::new(false), Condvar::new()));
    let blocked = Arc::clone(&barrier);
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = Arc::clone(&calls);
    let (started, waiting) = mpsc::sync_channel(1);
    *f.backend.file_reads.hook.lock().unwrap() = Some(Arc::new(move || {
        if observed.fetch_add(1, Ordering::AcqRel) == 0 {
            started.send(()).unwrap();
            let (lock, condition) = &*blocked;
            let mut released = lock.lock().unwrap();
            while !*released {
                released = condition.wait(released).unwrap();
            }
        }
    }));
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let server_stop = Arc::clone(&stop);
    let server_backend = Arc::clone(&f.backend);
    let server = thread::spawn(move || {
        crate::serve(listener, server_backend, b"fixture".to_vec(), server_stop).unwrap()
    });
    let http = move |target: &str| {
        let before = Instant::now();
        let mut socket = TcpStream::connect(address).unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();
        write!(socket, "GET {target} HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer fixture\r\nConnection: close\r\n\r\n").unwrap();
        let mut response = String::new();
        socket.read_to_string(&mut response).unwrap();
        assert!(
            before.elapsed() < Duration::from_millis(500),
            "{target} took {:?}",
            before.elapsed()
        );
        response
    };
    let active = thread::spawn(move || http("/api/v1/files?cd=/"));
    waiting.recv_timeout(Duration::from_secs(1)).unwrap();
    let queued = thread::spawn(move || http("/api/v1/storage/sd"));
    let until = Instant::now() + Duration::from_secs(1);
    while f.backend.file_reads.submitted.load(Ordering::Acquire) < 2 {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(1));
    }
    let identity_target =
        format!("/api/v1/internal/media-authorize?target=/media/v1/file%3Fpath%3D{file}");
    assert!(http(&identity_target).starts_with("HTTP/1.1 503"));
    let rvd = serve_daemon(&f.path, "rvd.sock", br#"{"cmd":"status"}"#, RVD_OK);
    let rhd = serve_daemon(&f.path, "rhd.sock", br#"{"cmd":"status"}"#, RHD_OK);
    assert!(http("/api/v1/health").starts_with("HTTP/1.1 200"));
    assert!(
        http("/api/v1/internal/media-authorize?target=/media/v1/mjpeg%3Fstream%3D0")
            .starts_with("HTTP/1.1 204")
    );
    rvd.join().unwrap();
    rhd.join().unwrap();
    assert!(active.join().unwrap().starts_with("HTTP/1.1 504"));
    assert!(queued.join().unwrap().starts_with("HTTP/1.1 504"));
    assert_eq!(calls.load(Ordering::Acquire), 1);
    {
        let (lock, condition) = &*barrier;
        *lock.lock().unwrap() = true;
        condition.notify_one();
    }
    // The first fresh request can see the expired queue slot until the worker drains it.
    let until = Instant::now() + Duration::from_secs(1);
    loop {
        let result = f
            .backend
            .recording_identity(&format!("/media/v1/file?path={file}"), until);
        match result {
            Err(BackendError::Busy) => {
                assert!(Instant::now() < until);
                thread::yield_now();
            }
            Ok(Some(_)) => break,
            other => panic!("{other:?}"),
        }
    }
    assert_eq!(
        calls.load(Ordering::Acquire),
        2,
        "expired queued SD job must not execute"
    );
    fs::write(
        f.disk("timelapse/100-1.jpg"),
        b"changed after prior authorization",
    )
    .unwrap();
    assert!(
        f.authorize(&file).is_none(),
        "identity must be fresh per request"
    );
    stop.store(true, Ordering::Release);
    server.join().unwrap();
}

#[test]
fn closed_markers_survive_only_fat_same_two_second_slot() {
    for fat in [false, true] {
        let f = Fixture::with_fat(fat);
        for owner in 0..=2 {
            let name = if owner == 2 {
                "1789086100-0.jpg"
            } else {
                "12-00-00.mp4"
            };
            let public = f.write(owner, name, false);
            let relative = public.strip_prefix(&format!("{ROOT}/")).unwrap();
            let path = f.disk(relative);
            let file = fs::File::open(&path).unwrap();
            let stamp = |seconds, nanos| {
                file.set_times(
                    fs::FileTimes::new()
                        .set_modified(std::time::UNIX_EPOCH + Duration::new(seconds, nanos)),
                )
                .unwrap();
            };
            let prefix = if owner == 2 {
                "TL1 1789086100".to_owned()
            } else {
                format!("RMR1 {owner}")
            };
            let marker = format!(
                "{}.{}",
                path.display(),
                if owner == 2 { "tl-owned" } else { "rmr-owned" }
            );
            fs::write(&marker, format!("{prefix} 15 1789086139 0\n")).unwrap();
            assert_eq!(file.metadata().unwrap().len(), 15);
            stamp(1789086139, 0);
            assert!(f.authorize(&public).is_some());
            stamp(1789086138, 0); // remount exposes FAT's persisted even second
            assert_eq!(f.authorize(&public).is_some(), fat);
            // A new canonical marker must also work before remount.
            fs::write(&marker, format!("{prefix} 15 1789086138 0\n")).unwrap();
            stamp(1789086139, 0);
            assert_eq!(f.authorize(&public).is_some(), fat);
            for (seconds, nanos) in [(1789086140, 0), (1789086137, 0), (1789086138, 1)] {
                stamp(seconds, nanos);
                assert!(f.authorize(&public).is_none());
            }
            stamp(1789086138, 0);
            for invalid in [
                format!("{prefix} 16 1789086138 0\n"),
                format!("{prefix} 15 1789086138 1\n"),
                format!("{prefix} 15 -1 0\n"),
                format!("{prefix} 15 1789086138 -1\n"),
                format!("{prefix} 15 1789086138 0\nextra"),
                format!("{prefix}  15 1789086138 0\n"),
                "RMR1 9 15 1789086138 0\n".to_owned(),
                "TL1 0 15 1789086138 0\n".to_owned(),
                "malformed".to_owned(),
            ] {
                fs::write(&marker, invalid).unwrap();
                assert!(f.authorize(&public).is_none());
            }
        }
    }
}
