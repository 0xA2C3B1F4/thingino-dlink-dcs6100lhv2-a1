"""Single-package staging and recovery passivation transactions."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .media_contracts import (
    DirectorySync,
    PASSIVE_RECOVERY_FILENAME,
    UARTLESS_CAPTURE_PASSIVE_FILENAME,
    ValidateSdRoot,
)
from .media_preflight import MediaError, MediaPreflight
from .sd_package import (
    is_matching_update_filename,
    matching_update_filenames,
    parse_package,
    selected_update_filename,
    validate_bootstrap,
)


def stage_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    output_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    _sync_directory: DirectorySync,
    validate_sd_root: ValidateSdRoot,
) -> str:
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


def deactivate_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    active_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "RECOVERY.OFF",
    existing_passive_bytes: bytes | None = None,
    _sync_directory: DirectorySync,
) -> str:
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
