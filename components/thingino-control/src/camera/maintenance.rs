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
