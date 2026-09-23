"""Resume archival of a completed restore and verify its passive closure."""

from __future__ import annotations

import os
from pathlib import Path

from installer.media import MediaPreflight

from .contract import (
    AUTH_PRIVATE_NAME, AUTH_SD_NAME, COMPLETE_SD_NAME, COMPLETE_STATUS,
    PASSIVE_DIR_NAME, PASSIVE_MARKER_NAME, PASSIVE_STATUS, RUN_SD_NAME, RUN_STATUS,
    StockRestoreBootstrapSet, StockRestorePassivation, StockRestoreRamSet,
    StockRestoreSetError,
)
from .file_io import _fsync_directory, _read_regular, _status_sidecars, _write_exclusive
from .media_policy import _check_media, _reject_matching_update_names


def passivate_completed_stock_restore(
    *,
    prepared: StockRestoreRamSet | StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> StockRestorePassivation:
    """Move one completed restore set out of the SD root without deleting it.

    The operation accepts only the terminal success state produced by the
    bounded restorer. It can resume after interruption because every expected
    file must exist either in the SD root or in the passive directory, never
    both. The one-use authorization must already be absent.
    """

    _check_media(root, preflight, confirmed_physical_device)
    passive = root / PASSIVE_DIR_NAME
    if passive.is_symlink():
        raise StockRestoreSetError("passive restore directory is a symlink")
    if passive.exists() and not passive.is_dir():
        raise StockRestoreSetError("passive restore path is not a directory")

    expected = {
        name: raw
        for name, raw in prepared.files.items()
        if name != AUTH_PRIVATE_NAME
    }
    if isinstance(prepared, StockRestoreBootstrapSet):
        _reject_matching_update_names(root, set())
    expected[RUN_SD_NAME] = RUN_STATUS
    expected[COMPLETE_SD_NAME] = COMPLETE_STATUS

    for candidate in (root / AUTH_SD_NAME, *_status_sidecars(root, AUTH_SD_NAME)):
        if candidate.exists() or candidate.is_symlink():
            raise StockRestoreSetError(
                "completed restore still has a live authorization"
            )
    for name in (*expected, PASSIVE_DIR_NAME):
        for candidate in _status_sidecars(root, name):
            if candidate.exists() or candidate.is_symlink():
                raise StockRestoreSetError("restore passivation sidecar exists")

    marker_existed = False
    if passive.exists():
        for name in (*expected, PASSIVE_MARKER_NAME):
            for candidate in _status_sidecars(passive, name):
                if candidate.exists() or candidate.is_symlink():
                    raise StockRestoreSetError(
                        "passive restore directory has a reserved sidecar"
                    )
        allowed = {*expected, PASSIVE_MARKER_NAME}
        if {entry.name for entry in passive.iterdir()} - allowed:
            raise StockRestoreSetError("passive restore directory has extra files")
        marker = passive / PASSIVE_MARKER_NAME
        if marker.exists() or marker.is_symlink():
            if _read_regular(marker, len(PASSIVE_STATUS)) != PASSIVE_STATUS:
                raise StockRestoreSetError("passive restore marker differs")
            marker_existed = True

    root_expected: list[str] = []
    for name, raw in expected.items():
        active = root / name
        archived = passive / name
        active_exists = active.exists() or active.is_symlink()
        archived_exists = archived.exists() or archived.is_symlink()
        if active_exists == archived_exists:
            raise StockRestoreSetError(
                "restore artifact must exist in exactly one passivation location"
            )
        selected = active if active_exists else archived
        if _read_regular(selected, len(raw)) != raw:
            raise StockRestoreSetError("restore artifact differs before passivation")
        if active_exists:
            root_expected.append(name)

    if not passive.exists():
        passive.mkdir(mode=0o700)
        for sidecar in _status_sidecars(root, PASSIVE_DIR_NAME):
            sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if any(
            sidecar.exists()
            for sidecar in _status_sidecars(root, PASSIVE_DIR_NAME)
        ):
            raise StockRestoreSetError("passive directory sidecar cleanup failed")

    move_order = (
        RUN_SD_NAME,
        COMPLETE_SD_NAME,
        *(name for name in sorted(expected) if name not in {RUN_SD_NAME, COMPLETE_SD_NAME}),
    )
    moved: list[str] = []
    for name in move_order:
        if name not in root_expected:
            continue
        source = root / name
        destination = passive / name
        raw = expected[name]
        if _read_regular(source, len(raw)) != raw or destination.exists():
            raise StockRestoreSetError("restore artifact changed during passivation")
        os.replace(source, destination)
        for directory in (root, passive):
            for sidecar in _status_sidecars(directory, name):
                sidecar.unlink(missing_ok=True)
        _fsync_directory(passive)
        _fsync_directory(root)
        if _read_regular(destination, len(raw)) != raw:
            raise StockRestoreSetError("passive restore readback differs")
        if any(
            sidecar.exists()
            for directory in (root, passive)
            for sidecar in _status_sidecars(directory, name)
        ):
            raise StockRestoreSetError("passive restore sidecar cleanup failed")
        moved.append(name)

    marker = passive / PASSIVE_MARKER_NAME
    if not marker_existed:
        _write_exclusive(marker, PASSIVE_STATUS)
        for sidecar in _status_sidecars(passive, PASSIVE_MARKER_NAME):
            sidecar.unlink(missing_ok=True)
        _fsync_directory(passive)
        _fsync_directory(root)
    if _read_regular(marker, len(PASSIVE_STATUS)) != PASSIVE_STATUS:
        raise StockRestoreSetError("passive restore marker readback differs")
    if any(
        sidecar.exists()
        for sidecar in _status_sidecars(passive, PASSIVE_MARKER_NAME)
    ):
        raise StockRestoreSetError("passive restore marker sidecar cleanup failed")
    for name, raw in expected.items():
        if (root / name).exists() or (root / name).is_symlink():
            raise StockRestoreSetError("restore artifact remains active in SD root")
        if _read_regular(passive / name, len(raw)) != raw:
            raise StockRestoreSetError("passive restore closure differs")
        if any(
            sidecar.exists()
            for directory in (root, passive)
            for sidecar in _status_sidecars(directory, name)
        ):
            raise StockRestoreSetError("passive restore closure has a sidecar")

    reserved_root_names = {
        *expected,
        AUTH_SD_NAME,
        PASSIVE_DIR_NAME,
    }
    unrelated = sum(
        1 for entry in root.iterdir() if entry.name not in reserved_root_names
    )
    return StockRestorePassivation(
        already_passivated=marker_existed and not moved,
        moved_files=tuple(moved),
        preserved_unrelated_entries=unrelated,
    )
