use super::*;

pub(super) fn request_overlay_reset(path: &Path) -> io::Result<()> {
    let metadata = path.symlink_metadata()?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "overlay reset path is not a directory",
        ));
    }
    let marker = path.join(".thingino-factory-reset");
    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .custom_flags(O_NOFOLLOW)
        .open(&marker)?;
    file.write_all(b"reset\n")?;
    file.sync_all()?;
    File::open(path)?.sync_all()
}

pub(super) fn reboot_now() {
    // SAFETY: sync has no preconditions; reboot is invoked only after an authenticated action.
    unsafe {
        sync();
        let _ = reboot(RB_AUTOBOOT);
    }
}

pub(super) fn maintenance_loop(backend: Arc<PrudyntBackend>) {
    let paths = &backend.paths;
    let mut applied_recorder = None;
    let mut last_timelapse = 0_u64;
    let mut last_cleanup = 0_u64;
    let mut last_daynight_attempt = None;
    loop {
        backend.process_storage_format();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_secs())
            .unwrap_or(0);
        if let Ok(config_bytes) = read_bounded(&paths.prudynt_config, FILE_LIMIT) {
            backend.motion.refresh_config();
            if applied_recorder.as_ref() != Some(&config_bytes)
                && apply_recorder_config(paths, &config_bytes).is_ok()
            {
                applied_recorder = Some(config_bytes);
            }
        }
        backend.motion.reconcile_prudynt(
            process_matches(&paths.prudynt_pid, &paths.prudynt_executable)
                && paths.motion_active.is_file(),
        );
        backend.motion.poll_media_lifecycle();
        if let Ok(config_bytes) = read_bounded(&paths.timelapse_config, FILE_LIMIT)
            && let Ok(config) = json::parse(&config_bytes)
            && config
                .get_path("timelapse.enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        {
            let interval = config
                .get_path("timelapse.interval")
                .and_then(value_u64)
                .unwrap_or(1)
                .clamp(1, 1440)
                .saturating_mul(60);
            if now.saturating_sub(last_timelapse) >= interval
                && capture_timelapse(paths, &config, now).is_ok()
            {
                last_timelapse = now;
            }
        }
        if now.saturating_sub(last_cleanup) >= 60 {
            let _ = cleanup_recording_storage(paths, now);
            last_cleanup = now;
        }
        let requested_mode =
            read_text_value(&paths.daynight_mode, 16).and_then(|value| match value.as_str() {
                "day" => Some(DayNightMode::Day),
                "night" => Some(DayNightMode::Night),
                _ => None,
            });
        let reapply_due = backend
            .daynight_reapply
            .lock()
            .ok()
            .and_then(|mut pending| {
                pending
                    .is_some_and(|not_before| Instant::now() >= not_before)
                    .then(|| pending.take())
                    .flatten()
            })
            .is_some();
        if reapply_due {
            let reapplied = requested_mode.is_none_or(|mode| {
                backend.config_lock.lock().is_ok_and(|_guard| {
                    backend
                        .reapply_prudynt_daynight(mode, Instant::now() + Duration::from_secs(3))
                        .is_ok()
                })
            });
            if !reapplied
                && let Ok(mut pending) = backend.daynight_reapply.lock()
                && pending.is_none()
            {
                *pending = Some(Instant::now() + Duration::from_secs(5));
            }
        }
        let applied_day =
            read_text_value(&paths.ircut_state, 8).and_then(|value| match value.as_str() {
                "1" => Some(true),
                "0" => Some(false),
                _ => None,
            });
        let automatic_daynight = read_bounded(&paths.thingino_config, FILE_LIMIT)
            .ok()
            .and_then(|bytes| json::parse(&bytes).ok())
            .and_then(|config| config.get_path("daynight.enabled").and_then(Value::as_bool))
            .unwrap_or(false);
        let transition_needed = automatic_daynight
            && match (requested_mode, applied_day) {
                (Some(DayNightMode::Day), Some(true))
                | (Some(DayNightMode::Night), Some(false)) => false,
                (Some(_), _) => true,
                _ => false,
            };
        let retry_ready = last_daynight_attempt
            .is_none_or(|attempt: Instant| attempt.elapsed() >= Duration::from_secs(60));
        if transition_needed && retry_ready {
            last_daynight_attempt = Some(Instant::now());
            if let Some(mode) = requested_mode
                && let Ok(_guard) = backend.config_lock.lock()
            {
                let _ = backend.apply_daynight_mode(mode, Instant::now() + Duration::from_secs(3));
            }
        }
        thread::sleep(Duration::from_secs(5));
    }
}
