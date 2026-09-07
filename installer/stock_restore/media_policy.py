"""Media identity and updater-name closure checks for stock restore."""

from __future__ import annotations

from pathlib import Path

from installer.media import MediaPreflight, validate_sd_root
from installer.sd_package import is_matching_update_filename

from .contract import StockRestoreSetError


def _check_media(
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    *,
    allowed_matching_filename: str | None = None,
) -> None:
    if confirmed_physical_device != preflight.physical_device:
        raise StockRestoreSetError("exact SD device confirmation differs")
    if root.resolve(strict=True) != preflight.mount_root:
        raise StockRestoreSetError("SD root changed after preflight")
    validate_sd_root(root, allowed_matching_filename=allowed_matching_filename)


def _reject_matching_update_names(root: Path, allowed: set[str]) -> None:
    matches = {
        entry.name for entry in root.iterdir() if is_matching_update_filename(entry.name)
    }
    if matches != allowed:
        raise StockRestoreSetError("SD stock updater selector closure differs")
