"""One-use authorization, updater selection, handoff and interrupted retry."""

from __future__ import annotations

import os
from pathlib import Path

from installer.media import MediaPreflight
from installer.sd_package import selected_update_filename

from .contract import (
    AUTH_PRIVATE_NAME, AUTH_SD_NAME, BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME,
    COMPLETE_SD_NAME, RUN_SD_NAME, RUN_STATUS, StockRestoreBootstrapSet,
    StockRestoreRamSet, StockRestoreSetError,
)
from .file_io import _fsync_directory, _read_regular, _status_sidecars, _write_exclusive
from .media_policy import _check_media, _reject_matching_update_names


def expected_live_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"RESTORE {preflight.physical_device} DCS-6100LHV2-A1 "
        "WRITE=mtd3,mtd2,mtd1 PROTECT=mtd0,mtd4,mtd5 ACTIVATION=last"
    )


def expected_bootstrap_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"RESTORE {preflight.physical_device} DCS-6100LHV2-A1 "
        "TRANSPORT=stock-uboot WRITE=mtd3,mtd2,mtd1 "
        "PROTECT=mtd0,mtd4,mtd5 ACTIVATION=last"
    )


def expected_retry_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"RETRY RESTORE {preflight.physical_device} DCS-6100LHV2-A1 "
        "WRITE=mtd3,mtd2,mtd1 PROTECT=mtd0,mtd4,mtd5 FROM=start"
    )


def expected_handoff_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"HANDOFF {preflight.physical_device} DCS-6100LHV2-A1 "
        "STOCK-UBOOT-WRITE=mtd1,mtd2 LED=complete READBACK=not-proven"
    )


def authorize_stock_restore_bootstrap(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Create the one-use token, then activate the stock selector last."""

    _check_media(
        root,
        preflight,
        confirmed_physical_device,
        allowed_matching_filename=BOOTSTRAP_ACTIVE_NAME,
    )
    if confirmation != expected_bootstrap_confirmation(preflight):
        raise StockRestoreSetError("live restore confirmation differs")
    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    package = prepared.files[BOOTSTRAP_PRIVATE_NAME]
    active_exists = active.exists() or active.is_symlink()
    passive_exists = passive.exists() or passive.is_symlink()
    if active_exists == passive_exists:
        raise StockRestoreSetError("stock restore bootstrap activation state differs")
    _reject_matching_update_names(
        root, {BOOTSTRAP_ACTIVE_NAME} if active_exists else set()
    )
    for name, raw in prepared.files.items():
        if name in {AUTH_PRIVATE_NAME, BOOTSTRAP_PRIVATE_NAME}:
            continue
        if _read_regular(root / name, len(raw)) != raw:
            raise StockRestoreSetError("staged stock restore changed before arming")
    selected_package = active if active_exists else passive
    if _read_regular(selected_package, len(package)) != package:
        raise StockRestoreSetError("staged stock restore package changed before arming")
    for name in (RUN_SD_NAME, COMPLETE_SD_NAME):
        for item in (root / name, *_status_sidecars(root, name)):
            if item.exists() or item.is_symlink():
                raise StockRestoreSetError("live restore status exists")
    for sidecar in _status_sidecars(root, AUTH_SD_NAME):
        if sidecar.exists() or sidecar.is_symlink():
            raise StockRestoreSetError("live restore authorization sidecar exists")
    for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore bootstrap sidecar exists")
    authorization = root / AUTH_SD_NAME
    if authorization.exists() or authorization.is_symlink():
        if _read_regular(authorization, len(prepared.authorization)) != prepared.authorization:
            raise StockRestoreSetError("live restore authorization differs")
        if active_exists:
            return
    elif active_exists:
        raise StockRestoreSetError("active bootstrap lacks exact authorization")
    else:
        _activate_exact_status(root, AUTH_SD_NAME, prepared.authorization)

    _activate_bootstrap_selector(root, package)


def deactivate_stock_restore_bootstrap(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Deactivate the updater after its LED loop so NOR can boot the restorer."""

    _check_media(
        root,
        preflight,
        confirmed_physical_device,
        allowed_matching_filename=BOOTSTRAP_ACTIVE_NAME,
    )
    if confirmation != expected_handoff_confirmation(preflight):
        raise StockRestoreSetError("stock restore handoff confirmation differs")
    package = prepared.files[BOOTSTRAP_PRIVATE_NAME]
    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    active_exists = active.exists() or active.is_symlink()
    passive_exists = passive.exists() or passive.is_symlink()
    if active_exists == passive_exists:
        raise StockRestoreSetError("stock restore handoff package state differs")
    _reject_matching_update_names(
        root, {BOOTSTRAP_ACTIVE_NAME} if active_exists else set()
    )
    selected = active if active_exists else passive
    if _read_regular(selected, len(package)) != package:
        raise StockRestoreSetError("stock restore handoff package differs")
    if _read_regular(root / AUTH_SD_NAME, len(prepared.authorization)) != prepared.authorization:
        raise StockRestoreSetError("stock restore handoff authorization differs")
    for name, raw in prepared.files.items():
        if name in {AUTH_PRIVATE_NAME, BOOTSTRAP_PRIVATE_NAME}:
            continue
        if _read_regular(root / name, len(raw)) != raw:
            raise StockRestoreSetError("stock restore handoff artifact differs")
    complete = root / COMPLETE_SD_NAME
    if complete.exists() or complete.is_symlink():
        raise StockRestoreSetError("completed restore cannot enter bootstrap handoff")
    run = root / RUN_SD_NAME
    if run.exists() or run.is_symlink():
        if _read_regular(run, len(RUN_STATUS)) != RUN_STATUS:
            raise StockRestoreSetError("stock restore handoff run marker differs")
    for name in (AUTH_SD_NAME, RUN_SD_NAME, COMPLETE_SD_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore handoff sidecar exists")
    for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore handoff package sidecar exists")
    if not active_exists:
        return
    moved = False
    try:
        os.replace(active, passive)
        moved = True
        for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
            for sidecar in _status_sidecars(root, name):
                sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if _read_regular(passive, len(package)) != package:
            raise StockRestoreSetError("stock restore handoff readback differs")
        _reject_matching_update_names(root, set())
        if any(
            sidecar.exists()
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME)
            for sidecar in _status_sidecars(root, name)
        ):
            raise StockRestoreSetError("stock restore handoff sidecar cleanup failed")
    except BaseException:
        if moved and passive.exists() and not active.exists():
            os.replace(passive, active)
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
                for sidecar in _status_sidecars(root, name):
                    sidecar.unlink(missing_ok=True)
            _fsync_directory(root)
        raise


def reauthorize_interrupted_stock_restore(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Authorize a full-from-start retry after RUN exists without OK."""

    _check_media(
        root,
        preflight,
        confirmed_physical_device,
        allowed_matching_filename=BOOTSTRAP_ACTIVE_NAME,
    )
    if confirmation != expected_retry_confirmation(preflight):
        raise StockRestoreSetError("live restore retry confirmation differs")
    package = prepared.files[BOOTSTRAP_PRIVATE_NAME]
    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    active_exists = active.exists() or active.is_symlink()
    passive_exists = passive.exists() or passive.is_symlink()
    if active_exists == passive_exists:
        raise StockRestoreSetError("interrupted restore package state differs")
    _reject_matching_update_names(
        root, {BOOTSTRAP_ACTIVE_NAME} if active_exists else set()
    )
    selected = active if active_exists else passive
    if _read_regular(selected, len(package)) != package:
        raise StockRestoreSetError("interrupted restore bootstrap differs")
    expected = {
        name: raw
        for name, raw in prepared.files.items()
        if name not in {AUTH_PRIVATE_NAME, BOOTSTRAP_PRIVATE_NAME}
    }
    for name, raw in expected.items():
        if _read_regular(root / name, len(raw)) != raw:
            raise StockRestoreSetError("interrupted restore artifact differs")
    if _read_regular(root / RUN_SD_NAME, len(RUN_STATUS)) != RUN_STATUS:
        raise StockRestoreSetError("interrupted restore run marker differs")
    for name in (COMPLETE_SD_NAME,):
        for item in (root / name, *_status_sidecars(root, name)):
            if item.exists() or item.is_symlink():
                raise StockRestoreSetError("interrupted restore is not retryable")
    for sidecar in _status_sidecars(root, AUTH_SD_NAME):
        if sidecar.exists() or sidecar.is_symlink():
            raise StockRestoreSetError("interrupted restore authorization sidecar exists")
    authorization = root / AUTH_SD_NAME
    if authorization.exists() or authorization.is_symlink():
        if _read_regular(authorization, len(prepared.authorization)) != prepared.authorization:
            raise StockRestoreSetError("restore retry authorization differs")
    elif active_exists:
        raise StockRestoreSetError("active retry bootstrap lacks authorization")
    else:
        _activate_exact_status(root, AUTH_SD_NAME, prepared.authorization)
    _activate_bootstrap_selector(root, package)


def authorize_stock_restore(
    *,
    prepared: StockRestoreRamSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Arm exactly one boot after a target- and write-set-bound confirmation."""

    _check_media(root, preflight, confirmed_physical_device)
    if confirmation != expected_live_confirmation(preflight):
        raise StockRestoreSetError("live restore confirmation differs")
    for name, raw in prepared.files.items():
        if name == AUTH_PRIVATE_NAME:
            continue
        item = root / name
        if item.is_symlink() or not item.is_file() or item.read_bytes() != raw:
            raise StockRestoreSetError("staged live restore set changed before arming")
    for name in (RUN_SD_NAME, COMPLETE_SD_NAME):
        for item in (root / name, *_status_sidecars(root, name)):
            if item.exists() or item.is_symlink():
                raise StockRestoreSetError("live restore status or authorization exists")
    _activate_exact_status(root, AUTH_SD_NAME, prepared.authorization)


def _activate_exact_status(root: Path, name: str, raw: bytes) -> bool:
    """Atomically activate one exact marker or roll back this transaction."""

    destination = root / name
    temporary = root / f".{name}.part"
    if destination.exists() or destination.is_symlink():
        if _read_regular(destination, len(raw)) != raw:
            raise StockRestoreSetError("live restore status differs")
        return False
    for item in _status_sidecars(root, name):
        if item.exists() or item.is_symlink():
            raise StockRestoreSetError("live restore status sidecar exists")
    temporary_created = False
    activated = False
    try:
        temporary_created = True
        _write_exclusive(temporary, raw)
        if _read_regular(temporary, len(raw)) != raw:
            raise StockRestoreSetError("live restore status temporary readback differs")
        os.replace(temporary, destination)
        activated = True
        for sidecar in _status_sidecars(root, name):
            sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if _read_regular(destination, len(raw)) != raw:
            raise StockRestoreSetError("live restore status activation differs")
        if any(sidecar.exists() for sidecar in _status_sidecars(root, name)):
            raise StockRestoreSetError("live restore status sidecar cleanup failed")
    except BaseException as exc:
        try:
            if activated:
                destination.unlink(missing_ok=True)
            if temporary_created:
                temporary.unlink(missing_ok=True)
            for sidecar in _status_sidecars(root, name):
                sidecar.unlink(missing_ok=True)
            _fsync_directory(root)
            if destination.exists() or destination.is_symlink() or any(
                item.exists() or item.is_symlink()
                for item in _status_sidecars(root, name)
            ):
                raise StockRestoreSetError(
                    "live restore status rollback is uncertain"
                )
        except BaseException as rollback_exc:
            raise StockRestoreSetError(
                "live restore status rollback is uncertain"
            ) from rollback_exc
        raise exc
    return True


def _activate_bootstrap_selector(root: Path, package: bytes) -> bool:
    """Activate only the strict selector and restore the inert name on failure."""

    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    if active.exists() or active.is_symlink():
        if passive.exists() or passive.is_symlink():
            raise StockRestoreSetError("stock restore bootstrap activation is ambiguous")
        if _read_regular(active, len(package)) != package:
            raise StockRestoreSetError("stock restore bootstrap activation differs")
        _reject_matching_update_names(root, {BOOTSTRAP_ACTIVE_NAME})
        return False
    if _read_regular(passive, len(package)) != package:
        raise StockRestoreSetError("stock restore bootstrap changed before activation")
    for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore bootstrap sidecar exists")
    moved = False
    try:
        os.replace(passive, active)
        moved = True
        for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
            for sidecar in _status_sidecars(root, name):
                sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if _read_regular(active, len(package)) != package:
            raise StockRestoreSetError("stock restore bootstrap activation differs")
        _reject_matching_update_names(root, {BOOTSTRAP_ACTIVE_NAME})
        if (
            selected_update_filename(entry.name for entry in root.iterdir())
            != BOOTSTRAP_ACTIVE_NAME
        ):
            raise StockRestoreSetError("stock restore bootstrap is not selected")
        if any(
            sidecar.exists()
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME)
            for sidecar in _status_sidecars(root, name)
        ):
            raise StockRestoreSetError("stock restore bootstrap sidecar cleanup failed")
    except BaseException as exc:
        try:
            if moved and active.exists() and not passive.exists():
                os.replace(active, passive)
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
                for sidecar in _status_sidecars(root, name):
                    sidecar.unlink(missing_ok=True)
            _fsync_directory(root)
            if (
                active.exists()
                or active.is_symlink()
                or _read_regular(passive, len(package)) != package
            ):
                raise StockRestoreSetError(
                    "stock restore bootstrap rollback is uncertain"
                )
            _reject_matching_update_names(root, set())
        except BaseException as rollback_exc:
            raise StockRestoreSetError(
                "stock restore bootstrap rollback is uncertain"
            ) from rollback_exc
        raise exc
    return True
