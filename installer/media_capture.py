"""Inert UARTless capture staging and selector activation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .media_contracts import (
    DirectorySync,
    UARTLESS_CAPTURE_ACTIVE_FILENAME,
    UARTLESS_CAPTURE_PASSIVE_FILENAME,
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


def stage_passive_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = "UARTCAP.PSV",
    _sync_directory: DirectorySync,
    _write_verified_temporary: WriteTemporary,
    validate_sd_root: ValidateSdRoot,
) -> str:

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


def activate_passive_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    active_name: str,
    passive_name: str = "UARTCAP.PSV",
    _sync_directory: DirectorySync,
    validate_sd_root: ValidateSdRoot,
) -> str:

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
