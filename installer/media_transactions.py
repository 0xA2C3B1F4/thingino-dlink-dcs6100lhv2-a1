"""Verified media staging, readback, activation, and rollback transactions."""

from __future__ import annotations


def _write_verified_temporary(facade: object, temporary: Path, raw: bytes) -> None:
    MediaError = getattr(facade, 'MediaError')
    hashlib = getattr(facade, 'hashlib')
    os = getattr(facade, 'os')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    readback = temporary.read_bytes()
    if len(readback) != len(raw) or hashlib.sha256(readback).digest() != hashlib.sha256(
        raw
    ).digest():
        raise MediaError("SD temporary-file readback mismatch")

def _sync_directory(facade: object, root: Path) -> None:
    os = getattr(facade, 'os')
    if os.name == "nt":
        return
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def stage_verified_install_set(facade: object,
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> dict[str, str]:
    MediaError = getattr(facade, 'MediaError')
    STAGE2_FILENAME = getattr(facade, 'STAGE2_FILENAME')
    _sync_directory = getattr(facade, '_sync_directory')
    _write_verified_temporary = getattr(facade, '_write_verified_temporary')
    hashlib = getattr(facade, 'hashlib')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    selected_update_filename = getattr(facade, 'selected_update_filename')
    validate_install_set = getattr(facade, 'validate_install_set')
    validate_sd_root = getattr(facade, 'validate_sd_root')
    validate_install_set(
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        bootstrap_name=bootstrap_name,
    )
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("staging root changed after preflight")
    root_identity = root.stat(follow_symlinks=False)
    if (
        getattr(preflight, "mount_device_id", 0)
        and (
            root_identity.st_dev != preflight.mount_device_id
            or root_identity.st_ino != preflight.mount_inode
        )
    ):
        raise MediaError("staging media identity changed after preflight")
    validate_sd_root(root)

    stage2_temporary = root / ".thingino-stage2-upload.part"
    bootstrap_temporary = root / ".thingino-installer-upload.part"
    stage2_destination = root / STAGE2_FILENAME
    bootstrap_destination = root / bootstrap_name
    owned_sidecars = tuple(
        root / ("._" + entry.name)
        for entry in (
            stage2_temporary,
            bootstrap_temporary,
            stage2_destination,
            bootstrap_destination,
        )
    )
    reserved = (
        stage2_temporary,
        bootstrap_temporary,
        stage2_destination,
        bootstrap_destination,
        *owned_sidecars,
    )
    if any(entry.exists() for entry in reserved):
        raise MediaError("reserved install-set path already exists")

    stage2_activated = False
    bootstrap_activated = False
    try:
        _write_verified_temporary(stage2_temporary, stage2_bytes)
        _write_verified_temporary(bootstrap_temporary, bootstrap_bytes)
        os.replace(stage2_temporary, stage2_destination)
        stage2_activated = True
        _sync_directory(root)
        os.replace(bootstrap_temporary, bootstrap_destination)
        bootstrap_activated = True
        for sidecar in owned_sidecars:
            sidecar.unlink(missing_ok=True)


        _sync_directory(root)

        if stage2_destination.read_bytes() != stage2_bytes:
            raise MediaError("activated stage-2 readback mismatch")
        if bootstrap_destination.read_bytes() != bootstrap_bytes:
            raise MediaError("activated bootstrap readback mismatch")
        if (
            not stage2_destination.is_file()
            or stage2_destination.is_symlink()
            or not bootstrap_destination.is_file()
            or bootstrap_destination.is_symlink()
        ):
            raise MediaError("activated install-set paths are not regular files")
        entry_names = [entry.name for entry in root.iterdir()]
        matches = matching_update_filenames(entry_names)
        if (
            matches != [bootstrap_name]
            or selected_update_filename(matches) != bootstrap_name
        ):
            casefold_matches = sum(
                name.casefold() == bootstrap_name.casefold() for name in entry_names
            )
            raise MediaError(
                "SD root does not contain exactly the activated bootstrap "
                f"(selector_matches={len(matches)}, casefold_matches={casefold_matches})"
            )
        return {
            bootstrap_name: hashlib.sha256(bootstrap_bytes).hexdigest(),
            STAGE2_FILENAME: hashlib.sha256(stage2_bytes).hexdigest(),
        }
    except BaseException:
        if bootstrap_activated:
            bootstrap_destination.unlink(missing_ok=True)
        if stage2_activated:
            stage2_destination.unlink(missing_ok=True)
        raise
    finally:
        stage2_temporary.unlink(missing_ok=True)
        bootstrap_temporary.unlink(missing_ok=True)
        for sidecar in owned_sidecars:
            sidecar.unlink(missing_ok=True)

def activate_staged_install_set(facade: object,
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "STAGE1.PKG",
) -> dict[str, str]:
    MediaError = getattr(facade, 'MediaError')
    PASSIVE_BOOTSTRAP_FILENAME = getattr(facade, 'PASSIVE_BOOTSTRAP_FILENAME')
    Path = getattr(facade, 'Path')
    RECOVERY_CHECKPOINT_FILENAME = getattr(facade, 'RECOVERY_CHECKPOINT_FILENAME')
    STAGE2_FILENAME = getattr(facade, 'STAGE2_FILENAME')
    _sync_directory = getattr(facade, '_sync_directory')
    _validate_recovery_checkpoint = getattr(facade, '_validate_recovery_checkpoint')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    selected_update_filename = getattr(facade, 'selected_update_filename')
    validate_install_set = getattr(facade, 'validate_install_set')
    validate_sd_root = getattr(facade, 'validate_sd_root')
    """Atomically activate one already-staged, byte-verified installer set."""

    validate_install_set(
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        bootstrap_name=bootstrap_name,
    )
    if (
        passive_name != PASSIVE_BOOTSTRAP_FILENAME
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("passive bootstrap filename is not the reviewed fixed name")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("activation root changed after preflight")
    validate_sd_root(root)

    checkpoint = root / RECOVERY_CHECKPOINT_FILENAME
    checkpoint_sidecar = root / ("._" + checkpoint.name)
    if checkpoint.exists() or checkpoint.is_symlink() or checkpoint_sidecar.exists():
        _validate_recovery_checkpoint(root, stage2_bytes)

    passive = root / passive_name
    stage2 = root / STAGE2_FILENAME
    destination = root / bootstrap_name
    destination_sidecar = root / ("._" + bootstrap_name)
    stage2_sidecar = root / ("._" + stage2.name)
    if (
        passive.is_symlink()
        or stage2.is_symlink()
        or not passive.is_file()
        or not stage2.is_file()
        or destination.exists()
        or destination_sidecar.exists()
    ):
        raise MediaError("passive install-set paths are missing, linked, or ambiguous")
    passive_snapshot = passive.read_bytes()
    stage2_snapshot = stage2.read_bytes()
    if passive_snapshot != bootstrap_bytes or stage2_snapshot != stage2_bytes:
        raise MediaError("passive install-set readback differs from the reviewed artifacts")
    if matching_update_filenames(entry.name for entry in root.iterdir()):
        raise MediaError("stock selector is not empty before activation")

    activated = False
    try:
        os.replace(passive, destination)
        activated = True
        destination_sidecar.unlink(missing_ok=True)
        stage2_sidecar.unlink(missing_ok=True)
        _sync_directory(root)
        active_snapshot = destination.read_bytes()
        if active_snapshot != bootstrap_bytes or stage2.read_bytes() != stage2_bytes:
            raise MediaError("activated install-set readback mismatch")
        entry_names = [entry.name for entry in root.iterdir()]
        matches = matching_update_filenames(entry_names)
        if matches != [bootstrap_name] or selected_update_filename(matches) != bootstrap_name:
            raise MediaError(
                "stock selector does not resolve to exactly the activated bootstrap: "
                + repr(matches)
            )
        return {
            bootstrap_name: hashlib.sha256(active_snapshot).hexdigest(),
            STAGE2_FILENAME: hashlib.sha256(stage2_snapshot).hexdigest(),
        }
    except BaseException:
        destination_sidecar.unlink(missing_ok=True)
        if activated and destination.exists() and not passive.exists():
            os.replace(destination, passive)
            _sync_directory(root)
        raise

def deactivate_staged_install_set(facade: object,
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "STAGE1.PKG",
    existing_passive_bytes: bytes | None = None,
) -> dict[str, str]:
    MediaError = getattr(facade, 'MediaError')
    PASSIVE_BOOTSTRAP_FILENAME = getattr(facade, 'PASSIVE_BOOTSTRAP_FILENAME')
    Path = getattr(facade, 'Path')
    STAGE2_FILENAME = getattr(facade, 'STAGE2_FILENAME')
    _sync_directory = getattr(facade, '_sync_directory')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    parse_package = getattr(facade, 'parse_package')
    selected_update_filename = getattr(facade, 'selected_update_filename')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_install_set = getattr(facade, 'validate_install_set')
    """Make a verified stage-1 bootstrap inert while retaining stage 2."""

    package = parse_package(bootstrap_bytes, require_project_header=True)
    validate_bootstrap(package)
    if existing_passive_bytes is not None:
        existing_package = parse_package(
            existing_passive_bytes, require_project_header=True
        )
        validate_bootstrap(existing_package)
    validate_install_set(
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        bootstrap_name=bootstrap_name,
    )
    if (
        passive_name != PASSIVE_BOOTSTRAP_FILENAME
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("passive bootstrap filename is not the reviewed fixed name")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("deactivation root changed after preflight")
    if root.is_symlink() or not root.is_dir() or root.resolve() == Path("/"):
        raise MediaError("SD root is not a safe real directory")

    active = root / bootstrap_name
    passive = root / passive_name
    stage2 = root / STAGE2_FILENAME
    rollback = root / ".thingino-stage1-deactivate-rollback.part"
    active_sidecar = root / ("._" + bootstrap_name)
    passive_sidecar = root / ("._" + passive_name)
    rollback_sidecar = root / ("._" + rollback.name)
    entry_names = [entry.name for entry in root.iterdir()]
    matches = matching_update_filenames(entry_names)
    if matches != [bootstrap_name] or selected_update_filename(matches) != bootstrap_name:
        raise MediaError("stock selector does not resolve to exactly the active bootstrap")
    if (
        active.is_symlink()
        or stage2.is_symlink()
        or not active.is_file()
        or not stage2.is_file()
        or active_sidecar.exists()
        or passive_sidecar.exists()
        or rollback.exists()
        or rollback_sidecar.exists()
    ):
        raise MediaError("active install-set paths are missing, linked, or ambiguous")
    if existing_passive_bytes is None:
        if passive.exists():
            raise MediaError("an unreviewed passive bootstrap already exists")
    elif (
        passive.is_symlink()
        or not passive.is_file()
        or passive.read_bytes() != existing_passive_bytes
    ):
        raise MediaError("existing passive bootstrap differs from the reviewed artifact")
    active_snapshot = active.read_bytes()
    stage2_snapshot = stage2.read_bytes()
    if active_snapshot != bootstrap_bytes or stage2_snapshot != stage2_bytes:
        raise MediaError("active install-set readback differs from the reviewed artifacts")

    rollback_created = False
    deactivated = False
    try:
        if existing_passive_bytes is not None:
            os.replace(passive, rollback)
            rollback_created = True
            _sync_directory(root)
        os.replace(active, passive)
        deactivated = True
        _sync_directory(root)
        if passive.read_bytes() != bootstrap_bytes or stage2.read_bytes() != stage2_bytes:
            raise MediaError("passive install-set readback mismatch")
        if active.exists() or matching_update_filenames(
            entry.name for entry in root.iterdir()
        ):
            raise MediaError("stock selector is not empty after deactivation")
        if rollback_created:
            rollback.unlink()
            rollback_created = False
            _sync_directory(root)
        return {
            passive_name: hashlib.sha256(active_snapshot).hexdigest(),
            STAGE2_FILENAME: hashlib.sha256(stage2_snapshot).hexdigest(),
        }
    except BaseException:
        if deactivated and passive.exists() and not active.exists():
            os.replace(passive, active)
            _sync_directory(root)
        if rollback_created and rollback.exists() and not passive.exists():
            os.replace(rollback, passive)
            _sync_directory(root)
        raise
    finally:
        rollback_sidecar.unlink(missing_ok=True)

def replace_passive_bootstrap(facade: object,
    *,
    old_bootstrap_bytes: bytes,
    old_stage2_bytes: bytes | None = None,
    legacy_migration_profile_bytes: bytes | None = None,
    new_bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "STAGE1.PKG",
) -> dict[str, str]:
    MediaError = getattr(facade, 'MediaError')
    PASSIVE_BOOTSTRAP_FILENAME = getattr(facade, 'PASSIVE_BOOTSTRAP_FILENAME')
    Path = getattr(facade, 'Path')
    STAGE2_FILENAME = getattr(facade, 'STAGE2_FILENAME')
    Stage2Error = getattr(facade, 'Stage2Error')
    _sync_directory = getattr(facade, '_sync_directory')
    _validate_legacy_migration_profile = getattr(facade, '_validate_legacy_migration_profile')
    _write_verified_temporary = getattr(facade, '_write_verified_temporary')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    parse_package = getattr(facade, 'parse_package')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_install_set = getattr(facade, 'validate_install_set')
    validate_legacy_stage2_v1 = getattr(facade, 'validate_legacy_stage2_v1')
    validate_sd_root = getattr(facade, 'validate_sd_root')
    validate_stage2 = getattr(facade, 'validate_stage2')
    """Replace one verified passive bootstrap with rollback until readback."""

    old_package = parse_package(old_bootstrap_bytes, require_project_header=True)
    new_package = parse_package(new_bootstrap_bytes, require_project_header=True)
    validate_bootstrap(old_package)
    validate_bootstrap(new_package)
    if old_stage2_bytes is None:
        old_stage2_bytes = stage2_bytes
    try:
        validate_stage2(old_stage2_bytes)
    except Stage2Error:
        _validate_legacy_migration_profile(
            legacy_migration_profile_bytes,
            bootstrap_name=bootstrap_name,
            old_bootstrap_bytes=old_bootstrap_bytes,
            old_stage2_bytes=old_stage2_bytes,
        )
        try:
            validate_legacy_stage2_v1(old_stage2_bytes)
        except (Stage2Error, ValueError) as exc:
            raise MediaError(
                "old passive stage-2 is neither current schema 2 nor reviewed schema 1"
            ) from exc
    validate_install_set(
        bootstrap_bytes=new_bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        bootstrap_name=bootstrap_name,
    )
    if old_bootstrap_bytes == new_bootstrap_bytes:
        raise MediaError("passive bootstrap replacement is byte-identical")
    if (
        passive_name != PASSIVE_BOOTSTRAP_FILENAME
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("passive bootstrap filename is not the reviewed fixed name")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("replacement root changed after preflight")
    validate_sd_root(root)

    passive = root / passive_name
    stage2 = root / STAGE2_FILENAME
    temporary = root / ".thingino-stage1-replacement.part"
    stage2_temporary = root / ".thingino-stage2-replacement.part"
    rollback = root / ".thingino-stage1-rollback.part"
    stage2_rollback = root / ".thingino-stage2-rollback.part"
    owned_sidecars = tuple(
        root / ("._" + path.name)
        for path in (temporary, stage2_temporary, rollback, stage2_rollback)
    )
    output_sidecars = (
        root / ("._" + passive.name),
        root / ("._" + stage2.name),
    )
    if (
        passive.is_symlink()
        or stage2.is_symlink()
        or not passive.is_file()
        or not stage2.is_file()
        or temporary.exists()
        or stage2_temporary.exists()
        or rollback.exists()
        or stage2_rollback.exists()
        or any(sidecar.exists() for sidecar in owned_sidecars)
    ):
        raise MediaError("passive replacement paths are missing, linked, or ambiguous")
    if passive.read_bytes() != old_bootstrap_bytes:
        raise MediaError("old passive bootstrap differs from the reviewed artifact")
    if stage2.read_bytes() != old_stage2_bytes:
        raise MediaError("passive stage-2 readback differs from the reviewed artifact")

    bootstrap_rollback_created = False
    stage2_rollback_created = False
    bootstrap_activated = False
    stage2_activated = False
    replacement_committed = False
    try:
        _write_verified_temporary(temporary, new_bootstrap_bytes)
        _write_verified_temporary(stage2_temporary, stage2_bytes)
        os.replace(passive, rollback)
        bootstrap_rollback_created = True
        _sync_directory(root)
        os.replace(stage2, stage2_rollback)
        stage2_rollback_created = True
        _sync_directory(root)
        os.replace(stage2_temporary, stage2)
        stage2_activated = True
        _sync_directory(root)
        os.replace(temporary, passive)
        bootstrap_activated = True
        _sync_directory(root)
        if passive.read_bytes() != new_bootstrap_bytes:
            raise MediaError("replacement passive bootstrap readback mismatch")
        if stage2.read_bytes() != stage2_bytes:
            raise MediaError("stage-2 changed during passive bootstrap replacement")
        for sidecar in output_sidecars:
            sidecar.unlink(missing_ok=True)
        _sync_directory(root)
        if matching_update_filenames(entry.name for entry in root.iterdir()):
            raise MediaError("stock selector is not empty after passive replacement")
        replacement_committed = True
        cleanup_error: OSError | None = None
        for rollback_path in (rollback, stage2_rollback):
            try:
                rollback_path.unlink()
                _sync_directory(root)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            raise MediaError(
                "passive replacement is active and verified, but old-file "
                "cleanup failed"
            ) from cleanup_error
        bootstrap_rollback_created = False
        stage2_rollback_created = False
        return {
            passive_name: hashlib.sha256(new_bootstrap_bytes).hexdigest(),
            STAGE2_FILENAME: hashlib.sha256(stage2_bytes).hexdigest(),
        }
    except BaseException:
        if not replacement_committed:
            if bootstrap_rollback_created and rollback.exists():
                if bootstrap_activated:
                    passive.unlink(missing_ok=True)
                os.replace(rollback, passive)
                _sync_directory(root)
            if stage2_rollback_created and stage2_rollback.exists():
                if stage2_activated:
                    stage2.unlink(missing_ok=True)
                os.replace(stage2_rollback, stage2)
                _sync_directory(root)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        stage2_temporary.unlink(missing_ok=True)
        for sidecar in owned_sidecars:
            sidecar.unlink(missing_ok=True)

def stage_passive_verified_package(facade: object,
    package_bytes: bytes,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "UARTCAP.PSV",
) -> str:
    MediaError = getattr(facade, 'MediaError')
    Path = getattr(facade, 'Path')
    UARTLESS_CAPTURE_PASSIVE_FILENAME = getattr(
        facade, 'UARTLESS_CAPTURE_PASSIVE_FILENAME'
    )
    _sync_directory = getattr(facade, '_sync_directory')
    _write_verified_temporary = getattr(facade, '_write_verified_temporary')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    parse_package = getattr(facade, 'parse_package')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_sd_root = getattr(facade, 'validate_sd_root')

    package = parse_package(package_bytes, require_project_header=True)
    validate_bootstrap(package)
    if (
        passive_name != UARTLESS_CAPTURE_PASSIVE_FILENAME
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("UARTless passive filename is not the reviewed fixed name")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("UARTless staging root changed after preflight")
    validate_sd_root(root)
    if matching_update_filenames(entry.name for entry in root.iterdir()):
        raise MediaError("stock selector is not empty before UARTless staging")

    passive = root / passive_name
    temporary = root / ".uartless-capture-upload.part"
    sidecars = (root / ("._" + passive.name), root / ("._" + temporary.name))
    if (
        passive.exists()
        or passive.is_symlink()
        or temporary.exists()
        or temporary.is_symlink()
        or any(sidecar.exists() or sidecar.is_symlink() for sidecar in sidecars)
    ):
        raise MediaError("UARTless passive staging path already exists")
    activated = False
    try:
        _write_verified_temporary(temporary, package_bytes)
        os.replace(temporary, passive)
        activated = True
        for sidecar in sidecars:
            sidecar.unlink(missing_ok=True)
        _sync_directory(root)
        if (
            passive.is_symlink()
            or not passive.is_file()
            or passive.read_bytes() != package_bytes
        ):
            raise MediaError("UARTless passive package readback differs")
        if matching_update_filenames(entry.name for entry in root.iterdir()):
            raise MediaError("UARTless passive staging unexpectedly armed stock selector")
        return hashlib.sha256(package_bytes).hexdigest()
    except BaseException:
        if activated:
            passive.unlink(missing_ok=True)
            _sync_directory(root)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        for sidecar in sidecars:
            sidecar.unlink(missing_ok=True)


def activate_passive_verified_package(facade: object,
    package_bytes: bytes,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    active_name: str,
    passive_name: str = "UARTCAP.PSV",
) -> str:
    MediaError = getattr(facade, 'MediaError')
    Path = getattr(facade, 'Path')
    UARTLESS_CAPTURE_ACTIVE_FILENAME = getattr(
        facade, 'UARTLESS_CAPTURE_ACTIVE_FILENAME'
    )
    UARTLESS_CAPTURE_PASSIVE_FILENAME = getattr(
        facade, 'UARTLESS_CAPTURE_PASSIVE_FILENAME'
    )
    _sync_directory = getattr(facade, '_sync_directory')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    parse_package = getattr(facade, 'parse_package')
    selected_update_filename = getattr(facade, 'selected_update_filename')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_sd_root = getattr(facade, 'validate_sd_root')

    validate_bootstrap(parse_package(package_bytes, require_project_header=True))
    if (
        active_name != UARTLESS_CAPTURE_ACTIVE_FILENAME
        or Path(active_name).name != active_name
        or not is_matching_update_filename(active_name)
        or passive_name != UARTLESS_CAPTURE_PASSIVE_FILENAME
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("UARTless activation filenames differ from the fixed contract")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("UARTless activation root changed after preflight")
    validate_sd_root(root)

    passive = root / passive_name
    active = root / active_name
    sidecars = (root / ("._" + passive.name), root / ("._" + active.name))
    if (
        passive.is_symlink()
        or not passive.is_file()
        or passive.read_bytes() != package_bytes
        or active.exists()
        or active.is_symlink()
        or any(sidecar.exists() or sidecar.is_symlink() for sidecar in sidecars)
        or matching_update_filenames(entry.name for entry in root.iterdir())
    ):
        raise MediaError("UARTless passive package is missing, changed, or ambiguous")
    activated = False
    try:
        os.replace(passive, active)
        activated = True
        for sidecar in sidecars:
            sidecar.unlink(missing_ok=True)
        _sync_directory(root)
        matches = matching_update_filenames(entry.name for entry in root.iterdir())
        if (
            active.is_symlink()
            or not active.is_file()
            or active.read_bytes() != package_bytes
            or matches != [active_name]
            or selected_update_filename(matches) != active_name
        ):
            raise MediaError("UARTless activated package readback differs")
        return hashlib.sha256(package_bytes).hexdigest()
    except BaseException:
        if activated and active.exists() and not passive.exists():
            os.replace(active, passive)
            _sync_directory(root)
        raise


def stage_verified_package(facade: object,
    package_bytes: bytes,
    *,
    root: Path,
    output_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> str:
    MediaError = getattr(facade, 'MediaError')
    Path = getattr(facade, 'Path')
    _sync_directory = getattr(facade, '_sync_directory')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    parse_package = getattr(facade, 'parse_package')
    selected_update_filename = getattr(facade, 'selected_update_filename')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_sd_root = getattr(facade, 'validate_sd_root')
    package = parse_package(package_bytes, require_project_header=True)
    validate_bootstrap(package)
    if not is_matching_update_filename(output_name) or Path(output_name).name != output_name:
        raise MediaError("output filename does not match the stock root selector")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("staging root changed after preflight")
    validate_sd_root(root, allowed_matching_filename=output_name)

    temporary = root / ".thingino-installer-upload.part"
    rollback = root / ".thingino-installer-rollback.part"
    destination = root / output_name
    destination_sidecar = root / ("._" + output_name)
    rollback_sidecar = root / ("._" + rollback.name)
    if (
        temporary.exists()
        or rollback.exists()
        or destination.is_symlink()
        or (destination.exists() and not destination.is_file())
        or destination_sidecar.exists()
        or rollback_sidecar.exists()
    ):
        raise MediaError("reserved staging path already exists")
    existing_snapshot = destination.read_bytes() if destination.exists() else None
    if existing_snapshot is not None:
        validate_bootstrap(
            parse_package(existing_snapshot, require_project_header=True)
        )
    descriptor = -1
    activated = False
    rollback_created = False
    committed = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(package_bytes)
            output.flush()
            os.fsync(output.fileno())
        readback = temporary.read_bytes()
        if len(readback) != len(package_bytes) or not hashlib.sha256(
            readback
        ).digest() == hashlib.sha256(package_bytes).digest():
            raise MediaError("SD temporary-file readback mismatch")
        if existing_snapshot is not None:
            os.replace(destination, rollback)
            rollback_created = True
            _sync_directory(root)
        os.replace(temporary, destination)
        activated = True
        # macOS may create an AppleDouble file for the destination while
        # applying FAT metadata. It is owned by this transaction and would
        # otherwise also match stock U-Boot's substring filename selector.
        destination_sidecar.unlink(missing_ok=True)
        directory_descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        final_readback = destination.read_bytes()
        if len(final_readback) != len(package_bytes) or hashlib.sha256(
            final_readback
        ).digest() != hashlib.sha256(package_bytes).digest():
            raise MediaError("activated SD package readback mismatch")
        matches = matching_update_filenames(
            entry.name for entry in root.iterdir() if entry.is_file()
        )
        if matches != [output_name] or selected_update_filename(matches) != output_name:
            raise MediaError("SD root does not contain exactly the activated package")
        committed = True
        if rollback_created:
            if rollback.read_bytes() != existing_snapshot:
                raise MediaError("old SD package rollback copy changed before cleanup")
            rollback.unlink()
            rollback_created = False
            _sync_directory(root)
        return hashlib.sha256(package_bytes).hexdigest()
    except BaseException:
        if not committed:
            try:
                if activated:
                    destination.unlink(missing_ok=True)
                    activated = False
                    _sync_directory(root)
                if rollback_created:
                    if rollback.read_bytes() != existing_snapshot:
                        raise MediaError("old SD package rollback copy changed")
                    os.replace(rollback, destination)
                    rollback_created = False
                    _sync_directory(root)
            except BaseException as rollback_error:
                raise MediaError("SD package rollback is uncertain") from rollback_error
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        destination_sidecar.unlink(missing_ok=True)
        rollback_sidecar.unlink(missing_ok=True)

def deactivate_verified_package(facade: object,
    package_bytes: bytes,
    *,
    root: Path,
    active_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "RECOVERY.OFF",
    existing_passive_bytes: bytes | None = None,
) -> str:
    MediaError = getattr(facade, 'MediaError')
    PASSIVE_RECOVERY_FILENAME = getattr(facade, 'PASSIVE_RECOVERY_FILENAME')
    UARTLESS_CAPTURE_PASSIVE_FILENAME = getattr(
        facade, 'UARTLESS_CAPTURE_PASSIVE_FILENAME'
    )
    Path = getattr(facade, 'Path')
    _sync_directory = getattr(facade, '_sync_directory')
    hashlib = getattr(facade, 'hashlib')
    is_matching_update_filename = getattr(facade, 'is_matching_update_filename')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    os = getattr(facade, 'os')
    parse_package = getattr(facade, 'parse_package')
    selected_update_filename = getattr(facade, 'selected_update_filename')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    """Make one verified recovery package inert without losing its readback."""

    package = parse_package(package_bytes, require_project_header=True)
    validate_bootstrap(package)
    if existing_passive_bytes is not None:
        existing_package = parse_package(
            existing_passive_bytes, require_project_header=True
        )
        validate_bootstrap(existing_package)
    if not is_matching_update_filename(active_name) or Path(active_name).name != active_name:
        raise MediaError("active filename does not match the stock root selector")
    if (
        passive_name
        not in {PASSIVE_RECOVERY_FILENAME, UARTLESS_CAPTURE_PASSIVE_FILENAME}
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("passive recovery filename is not the reviewed fixed name")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("deactivation root changed after preflight")
    if root.is_symlink() or not root.is_dir() or root.resolve() == Path("/"):
        raise MediaError("SD root is not a safe real directory")

    active = root / active_name
    passive = root / passive_name
    active_sidecar = root / ("._" + active_name)
    passive_sidecar = root / ("._" + passive_name)
    rollback = root / ".thingino-recovery-deactivate-rollback.part"
    rollback_sidecar = root / ("._" + rollback.name)
    matches = matching_update_filenames(
        entry.name for entry in root.iterdir() if entry.is_file()
    )
    if matches != [active_name] or selected_update_filename(matches) != active_name:
        raise MediaError("stock selector does not resolve to exactly the active package")
    if (
        active.is_symlink()
        or not active.is_file()
        or active_sidecar.exists()
        or passive_sidecar.exists()
        or passive.is_symlink()
        or (passive.exists() and not passive.is_file())
        or rollback.exists()
        or rollback_sidecar.exists()
    ):
        raise MediaError("recovery package paths are missing, linked, or ambiguous")
    active_snapshot = active.read_bytes()
    if active_snapshot != package_bytes:
        raise MediaError("active recovery package differs from the reviewed artifact")
    if existing_passive_bytes is None:
        if passive.exists() and passive.read_bytes() != package_bytes:
            raise MediaError("passive recovery package differs from the reviewed artifact")
    elif not passive.is_file() or passive.read_bytes() != existing_passive_bytes:
        raise MediaError("existing passive recovery differs from the reviewed artifact")

    rollback_created = False
    deactivated = False
    replacement_committed = False
    fail_inert = passive_name == UARTLESS_CAPTURE_PASSIVE_FILENAME
    try:
        if existing_passive_bytes is not None:
            os.replace(passive, rollback)
            rollback_created = True
            _sync_directory(root)
        if passive.exists():
            active.unlink()
        else:
            os.replace(active, passive)
        deactivated = True
        _sync_directory(root)
        if active.exists() or passive.read_bytes() != package_bytes:
            raise MediaError("passive recovery package readback mismatch")
        if matching_update_filenames(
            entry.name for entry in root.iterdir() if entry.is_file()
        ):
            raise MediaError("stock selector is not empty after recovery deactivation")
        replacement_committed = True
        if rollback_created:
            try:
                rollback.unlink()
                rollback_created = False
                _sync_directory(root)
            except OSError as exc:
                raise MediaError(
                    "recovery package is passive and verified, but old passive "
                    "cleanup failed"
                ) from exc
        return hashlib.sha256(active_snapshot).hexdigest()
    except BaseException:
        if not replacement_committed:
            if deactivated and passive.exists() and not active.exists():
                if fail_inert:
                    # A failed UARTless handoff must never re-arm the stock
                    # selector. The operator must re-inspect the inert card,
                    # but a post-rename error cannot cause another mtd1/mtd2
                    # updater pass.
                    active_sidecar.unlink(missing_ok=True)
                else:
                    os.replace(passive, active)
                    _sync_directory(root)
            if rollback_created and rollback.exists() and not passive.exists():
                os.replace(rollback, passive)
                _sync_directory(root)
        raise
    finally:
        rollback_sidecar.unlink(missing_ok=True)
