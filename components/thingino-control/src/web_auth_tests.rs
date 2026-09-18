use std::os::unix::fs::PermissionsExt;
use std::sync::Barrier;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use super::*;

static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn test_paths(name: &str) -> (PathBuf, WebAuthPaths) {
    let unique = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let root = std::env::temp_dir().join(format!(
        "thingino-control-web-auth-{name}-{}-{unique}",
        std::process::id()
    ));
    fs::create_dir_all(&root).unwrap();
    let shadow = root.join("shadow");
    fs::write(&shadow, b"root:$6$test$placeholder:0:0:99999:7:::\n").unwrap();
    (
        root.clone(),
        WebAuthPaths {
            api_key: root.join("api.key"),
            thingino_config: root.join("thingino.json"),
            shadow,
        },
    )
}

struct BarrierVerifier {
    barrier: Arc<Barrier>,
    active: Arc<AtomicUsize>,
    peak: Arc<AtomicUsize>,
}

impl PasswordVerifier for BarrierVerifier {
    fn verify(&self, password: &[u8], _stored: &str) -> bool {
        self.barrier.wait();
        with_crypt_lock(&SYSTEM_CRYPT_LOCK, || {
            let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
            self.peak.fetch_max(active, Ordering::SeqCst);
            thread::sleep(Duration::from_millis(25));
            self.active.fetch_sub(1, Ordering::SeqCst);
            Some(password == b"__SET_LOCALLY__")
        })
        .unwrap_or(false)
    }
}

#[test]
fn concurrent_login_crypt_sections_are_serialized_and_wrong_password_is_rejected() {
    let (root, paths) = test_paths("serialized");
    let active = Arc::new(AtomicUsize::new(0));
    let peak = Arc::new(AtomicUsize::new(0));
    let auth = Arc::new(WebAuth::with_password_verifier(
        paths,
        Arc::new(BarrierVerifier {
            barrier: Arc::new(Barrier::new(2)),
            active: Arc::clone(&active),
            peak: Arc::clone(&peak),
        }),
    ));
    let correct_auth = Arc::clone(&auth);
    let correct = thread::spawn(move || {
        correct_auth.login(br#"{"username":"root","password":"__SET_LOCALLY__"}"#)
    });
    let wrong_auth = Arc::clone(&auth);
    let wrong = thread::spawn(move || {
        wrong_auth.login(br#"{"username":"root","password":"__GENERATE_LOCALLY__"}"#)
    });

    assert!(correct.join().unwrap().is_ok());
    assert_eq!(wrong.join().unwrap(), Err(AuthError::InvalidCredentials));
    assert_eq!(peak.load(Ordering::SeqCst), 1);
    assert_eq!(auth.sessions.lock().unwrap().len(), 1);
    fs::remove_dir_all(root).unwrap();
}

struct LockedVerifier {
    lock: Arc<Mutex<()>>,
    ffi_called: Arc<AtomicBool>,
    ffi_result: Option<bool>,
}

impl PasswordVerifier for LockedVerifier {
    fn verify(&self, _password: &[u8], _stored: &str) -> bool {
        with_crypt_lock(&self.lock, || {
            self.ffi_called.store(true, Ordering::SeqCst);
            self.ffi_result
        })
        .unwrap_or(false)
    }
}

#[test]
fn poisoned_crypt_lock_and_ffi_failure_reject_login() {
    let poisoned_lock = Arc::new(Mutex::new(()));
    let lock_to_poison = Arc::clone(&poisoned_lock);
    let _ = thread::spawn(move || {
        let _guard = lock_to_poison.lock().unwrap();
        panic!("poison test crypt lock");
    })
    .join();
    let poison_ffi_called = Arc::new(AtomicBool::new(false));
    let (poison_root, poison_paths) = test_paths("poisoned");
    let poisoned_auth = WebAuth::with_password_verifier(
        poison_paths,
        Arc::new(LockedVerifier {
            lock: poisoned_lock,
            ffi_called: Arc::clone(&poison_ffi_called),
            ffi_result: Some(true),
        }),
    );
    assert_eq!(
        poisoned_auth.login(br#"{"username":"root","password":"__SET_LOCALLY__"}"#),
        Err(AuthError::InvalidCredentials)
    );
    assert!(!poison_ffi_called.load(Ordering::SeqCst));
    assert!(poisoned_auth.sessions.lock().unwrap().is_empty());

    let ffi_called = Arc::new(AtomicBool::new(false));
    let (ffi_root, ffi_paths) = test_paths("ffi-failure");
    let ffi_failure_auth = WebAuth::with_password_verifier(
        ffi_paths,
        Arc::new(LockedVerifier {
            lock: Arc::new(Mutex::new(())),
            ffi_called: Arc::clone(&ffi_called),
            ffi_result: None,
        }),
    );
    assert_eq!(
        ffi_failure_auth.login(br#"{"username":"root","password":"__SET_LOCALLY__"}"#),
        Err(AuthError::InvalidCredentials)
    );
    assert!(ffi_called.load(Ordering::SeqCst));
    assert!(ffi_failure_auth.sessions.lock().unwrap().is_empty());

    fs::remove_dir_all(poison_root).unwrap();
    fs::remove_dir_all(ffi_root).unwrap();
}

#[cfg(target_os = "linux")]
#[test]
fn system_libcrypt_handles_concurrent_correct_and_wrong_passwords() {
    const CALLS: usize = 8;
    let stored = crypt_password(b"__SET_LOCALLY__", "$6$phase1$")
        .expect("Linux system libcrypt must support SHA-512 crypt");
    let barrier = Arc::new(Barrier::new(CALLS));
    let stored = Arc::new(stored);
    let threads: Vec<_> = (0..CALLS)
        .map(|index| {
            let barrier = Arc::clone(&barrier);
            let stored = Arc::clone(&stored);
            thread::spawn(move || {
                let should_match = index.is_multiple_of(2);
                let password: &[u8] = if should_match {
                    b"__SET_LOCALLY__"
                } else {
                    b"__GENERATE_LOCALLY__"
                };
                barrier.wait();
                assert_eq!(
                    verify_shadow_password(password, stored.as_str()),
                    should_match
                );
            })
        })
        .collect();
    for thread in threads {
        thread.join().unwrap();
    }
}

#[test]
fn base64_decoder_enforces_length_alphabet_padding_and_input_limit() {
    assert_eq!(decode_base64("YQ=="), Some(b"a".to_vec()));
    assert_eq!(decode_base64("YWI="), Some(b"ab".to_vec()));
    assert_eq!(decode_base64("YWJj"), Some(b"abc".to_vec()));
    for invalid in [
        "", "Y", "YQ=", "YW=J", "YWJ!", "====", "YQ==YQ==", "YR==", "YWJ=",
    ] {
        assert_eq!(decode_base64(invalid), None, "accepted {invalid:?}");
    }
    assert!(decode_base64(&"A".repeat(512)).is_some());
    assert_eq!(decode_base64(&"A".repeat(516)), None);
}

#[test]
fn login_limiter_bounds_each_source_and_resets_its_short_window() {
    let mut limiter = LoginRateLimiter::default();
    let start = Instant::now();
    let source = Some("192.0.2.7".parse().unwrap());
    for _ in 0..LOGIN_SOURCE_LIMIT {
        assert!(limiter.admit(source, start));
    }
    assert!(!limiter.admit(source, start));
    assert!(limiter.admit(source, start + LOGIN_WINDOW));
}

#[test]
fn credential_mutations_require_a_recent_but_still_valid_session() {
    let (root, paths) = test_paths("recent-session");
    let auth = WebAuth::new(paths);
    let now = Instant::now();
    let session_id = "0123456789abcdef0123456789abcdef";
    auth.sessions.lock().unwrap().insert(
        session_id.to_owned(),
        Session {
            username: "root".to_owned(),
            is_default_password: false,
            created: now - Duration::from_secs(SESSION_RECENT_SECONDS + 1),
            last_access: now,
        },
    );
    let cookie = format!("{COOKIE_NAME}={session_id}");
    assert!(!auth.authorize_recent_session(Some(cookie.as_bytes())));
    assert!(auth.authorize_session(Some(cookie.as_bytes())));
    auth.sessions
        .lock()
        .unwrap()
        .get_mut(session_id)
        .unwrap()
        .created = now;
    assert!(auth.authorize_recent_session(Some(cookie.as_bytes())));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn credential_writer_atomically_preserves_identity_and_enforces_api_key_mode() {
    let (root, paths) = test_paths("atomic-writer");
    fs::set_permissions(&paths.shadow, fs::Permissions::from_mode(0o640)).unwrap();
    let before = paths.shadow.metadata().unwrap();
    write_existing(&paths.shadow, b"root:$6$replacement:0:0:99999:7:::\n").unwrap();
    let after = paths.shadow.metadata().unwrap();
    assert_ne!(before.ino(), after.ino());
    assert_eq!(after.mode() & 0o777, 0o640);
    assert_eq!((after.uid(), after.gid()), (before.uid(), before.gid()));
    assert_eq!(
        fs::read(&paths.shadow).unwrap(),
        b"root:$6$replacement:0:0:99999:7:::\n"
    );

    write_private_atomic(&paths.api_key, b"0123456789abcdef\n", 0o600, true).unwrap();
    assert_eq!(paths.api_key.metadata().unwrap().mode() & 0o777, 0o600);
    assert!(fs::read_dir(&root).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .contains("thingino-auth")
    }));
    fs::remove_dir_all(root).unwrap();
}

struct ClockTestVerifier;

impl PasswordVerifier for ClockTestVerifier {
    fn verify(&self, password: &[u8], _stored: &str) -> bool {
        password == b"__SET_LOCALLY__"
    }
}

// Keep simulated wall time separate from elapsed time. Neither NTP steps nor
// Time settings are allowed to affect the process-local session clock.
fn clock_test_auth(name: &str) -> (PathBuf, WebAuth, Arc<Mutex<(u64, i64)>>, String) {
    let (root, paths) = test_paths(name);
    let mut auth = WebAuth::with_password_verifier(paths, Arc::new(ClockTestVerifier));
    let clocks = Arc::new(Mutex::new((0_u64, 1_786_006_608_i64)));
    let elapsed = Arc::clone(&clocks);
    let start = Instant::now();
    auth.session_clock = Arc::new(move || start + Duration::from_secs(elapsed.lock().unwrap().0));
    let login = auth
        .login(br#"{"username":"root","password":"__SET_LOCALLY__"}"#)
        .unwrap();
    let cookie = format!("{COOKIE_NAME}={}", login.session_id);
    (root, auth, clocks, cookie)
}

#[test]
fn wall_clock_steps_do_not_expire_or_rejuvenate_sessions() {
    let (root, auth, clocks, cookie) = clock_test_auth("wall-clock-steps");
    for wall_step in [30 * 86400_i64, -60 * 86400, 90 * 86400] {
        clocks.lock().unwrap().1 += wall_step;
        assert!(auth.authorize_session(Some(cookie.as_bytes())));
        assert!(
            auth.session_status(Some(cookie.as_bytes()), None)
                .authenticated
        );
        assert!(auth.authorize_recent_session(Some(cookie.as_bytes())));
    }
    clocks.lock().unwrap().0 = SESSION_RECENT_SECONDS;
    assert!(auth.authorize_recent_session(Some(cookie.as_bytes())));
    {
        let mut time = clocks.lock().unwrap();
        time.0 += 1;
        time.1 -= 365 * 86400;
    }
    assert!(!auth.authorize_recent_session(Some(cookie.as_bytes())));
    assert!(auth.authorize_session(Some(cookie.as_bytes())));
    assert!(
        auth.session_status(Some(cookie.as_bytes()), None)
            .authenticated
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn monotonic_idle_and_maximum_age_expire_on_both_session_paths() {
    for status_path in [false, true] {
        let valid = |auth: &WebAuth, cookie: &str| {
            if status_path {
                auth.session_status(Some(cookie.as_bytes()), None)
                    .authenticated
            } else {
                auth.authorize_session(Some(cookie.as_bytes()))
            }
        };
        let (root, auth, clocks, cookie) = clock_test_auth("monotonic-idle");
        clocks.lock().unwrap().0 = SESSION_IDLE_SECONDS;
        assert!(valid(&auth, &cookie));
        {
            let mut time = clocks.lock().unwrap();
            time.0 += SESSION_IDLE_SECONDS + 1;
            time.1 -= 365 * 86400;
        }
        assert!(!valid(&auth, &cookie));
        assert!(auth.sessions.lock().unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();

        let (root, auth, clocks, cookie) = clock_test_auth("monotonic-max");
        clocks.lock().unwrap().0 = SESSION_IDLE_SECONDS;
        assert!(valid(&auth, &cookie));
        clocks.lock().unwrap().0 = SESSION_MAX_SECONDS;
        assert!(valid(&auth, &cookie));
        {
            let mut time = clocks.lock().unwrap();
            time.0 += 1;
            time.1 += 365 * 86400;
        }
        assert!(!valid(&auth, &cookie));
        assert!(auth.sessions.lock().unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }
}

#[test]
fn credential_updates_share_one_transaction_lock() {
    let (root, paths) = test_paths("credential-lock");
    let auth = Arc::new(WebAuth::new(paths));
    let guard = auth.lock_credential_update().unwrap();
    let (started_tx, started_rx) = mpsc::channel();
    let (acquired_tx, acquired_rx) = mpsc::channel();
    let contender = Arc::clone(&auth);
    let thread = thread::spawn(move || {
        started_tx.send(()).unwrap();
        let _guard = contender.lock_credential_update().unwrap();
        acquired_tx.send(()).unwrap();
    });
    started_rx.recv().unwrap();
    assert!(acquired_rx.recv_timeout(Duration::from_millis(25)).is_err());
    drop(guard);
    acquired_rx.recv_timeout(Duration::from_secs(1)).unwrap();
    thread.join().unwrap();
    fs::remove_dir_all(root).unwrap();
}
