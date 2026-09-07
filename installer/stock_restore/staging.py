"""Stage inert restore inputs with transaction-owned failure cleanup."""

from __future__ import annotations

import os
from pathlib import Path

from installer.live_ram import KERNEL_SD_NAME
from installer.media import MediaPreflight

from .contract import (
    AUTH_PRIVATE_NAME, AUTH_SD_NAME, BOOTSTRAP_ACTIVE_NAME, COMPLETE_SD_NAME,
    PASSIVE_DIR_NAME, RUN_SD_NAME, StockRestoreBootstrapSet, StockRestoreRamSet,
    StockRestoreSetError,
)
from .file_io import _fsync_directory, _read_regular, _status_sidecars, _write_exclusive
from .media_policy import _check_media, _reject_matching_update_names


def stage_stock_restore_ram_set(
    *,
    prepared: StockRestoreRamSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> tuple[str, ...]:
    """Stage an unarmed set. RESTORE.GO is intentionally excluded."""

    _check_media(root, preflight, confirmed_physical_device)
    staged = {
        name: raw
        for name, raw in prepared.files.items()
        if name != AUTH_PRIVATE_NAME
    }
    reserved = {
        *staged,
        AUTH_SD_NAME,
        RUN_SD_NAME,
        COMPLETE_SD_NAME,
        PASSIVE_DIR_NAME,
    }
    reused: set[str] = set()
    for name in reserved:
        for candidate in (
            root / name,
            root / f".{name}.part",
            root / f"._{name}",
            root / f"._.{name}.part",
        ):
            if candidate.exists() or candidate.is_symlink():
                if (
                    name == KERNEL_SD_NAME
                    and candidate == root / name
                    and candidate.is_file()
                    and not candidate.is_symlink()
                    and candidate.read_bytes() == staged[name]
                ):
                    reused.add(name)
                    continue
                raise StockRestoreSetError("SD restore reserved path already exists")
    activated: list[Path] = []
    try:
        for name, raw in staged.items():
            if name in reused:
                continue
            temporary = root / f".{name}.part"
            destination = root / name
            _write_exclusive(temporary, raw)
            if temporary.read_bytes() != raw:
                raise StockRestoreSetError("SD temporary readback differs")
            os.replace(temporary, destination)
            activated.append(destination)
        for name in staged:
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        for name, raw in staged.items():
            if (root / name).read_bytes() != raw:
                raise StockRestoreSetError("SD activated readback differs")
            if (root / f"._{name}").exists() or (
                root / f"._.{name}.part"
            ).exists():
                raise StockRestoreSetError("SD restore AppleDouble cleanup failed")
    except BaseException:
        for item in activated:
            item.unlink(missing_ok=True)
        for name in staged:
            (root / f".{name}.part").unlink(missing_ok=True)
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        raise
    return tuple(staged)


def stage_stock_restore_bootstrap_set(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> tuple[str, ...]:
    """Stage an inert stock-U-Boot package and exact restore inputs."""

    _check_media(root, preflight, confirmed_physical_device)
    _reject_matching_update_names(root, set())
    staged = {
        name: raw for name, raw in prepared.files.items() if name != AUTH_PRIVATE_NAME
    }
    reserved = {
        *staged,
        BOOTSTRAP_ACTIVE_NAME,
        AUTH_SD_NAME,
        RUN_SD_NAME,
        COMPLETE_SD_NAME,
        PASSIVE_DIR_NAME,
    }
    for name in reserved:
        for candidate in (
            root / name,
            *_status_sidecars(root, name),
            root / f"._.{name}.part",
        ):
            if candidate.exists() or candidate.is_symlink():
                raise StockRestoreSetError("SD restore reserved path already exists")
    activated: list[Path] = []
    try:
        for name, raw in staged.items():
            temporary = root / f".{name}.part"
            destination = root / name
            _write_exclusive(temporary, raw)
            if _read_regular(temporary, len(raw)) != raw:
                raise StockRestoreSetError("SD temporary readback differs")
            os.replace(temporary, destination)
            activated.append(destination)
        for name in staged:
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        _fsync_directory(root)
        for name, raw in staged.items():
            if _read_regular(root / name, len(raw)) != raw:
                raise StockRestoreSetError("SD activated readback differs")
            if (root / f"._{name}").exists() or (
                root / f"._.{name}.part"
            ).exists():
                raise StockRestoreSetError("SD restore AppleDouble cleanup failed")
        _reject_matching_update_names(root, set())
    except BaseException:
        for path in activated:
            path.unlink(missing_ok=True)
        for name in staged:
            (root / f".{name}.part").unlink(missing_ok=True)
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        raise
    return tuple(staged)
