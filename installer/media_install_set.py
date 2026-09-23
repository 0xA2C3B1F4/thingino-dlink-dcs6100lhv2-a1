"""Two-artifact install-set staging, activation and deactivation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .media_contracts import (
    DirectorySync,
    PASSIVE_BOOTSTRAP_FILENAME,
    RECOVERY_CHECKPOINT_FILENAME,
    ValidateInstallSet,
    ValidateRecoveryCheckpoint,
    ValidateSdRoot,
    WriteTemporary,
)
from .media_preflight import MediaError, MediaPreflight
from .sd_package import (
    is_matching_update_filename,
    matching_update_filenames,
    parse_package,
    selected_update_filename,
    validate_bootstrap,
)
from .stage2 import FILENAME as STAGE2_FILENAME


def stage_verified_install_set(
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    _sync_directory: DirectorySync,
    _write_verified_temporary: WriteTemporary,
    validate_install_set: ValidateInstallSet,
    validate_sd_root: ValidateSdRoot,
) -> dict[str, str]:
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


def activate_staged_install_set(
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "STAGE1.PKG",
    recovery_authorization_bytes: bytes | None = None,
    recovery_provisioning_bytes: bytes | None = None,
    _sync_directory: DirectorySync,
    _validate_recovery_checkpoint: ValidateRecoveryCheckpoint,
    validate_install_set: ValidateInstallSet,
    validate_sd_root: ValidateSdRoot,
) -> dict[str, str]:
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
        _validate_recovery_checkpoint(
            root,
            stage2_bytes,
            authorization_bytes=recovery_authorization_bytes,
            provisioning_bytes=recovery_provisioning_bytes,
        )

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


def deactivate_staged_install_set(
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
    _sync_directory: DirectorySync,
    validate_install_set: ValidateInstallSet,
) -> dict[str, str]:
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
