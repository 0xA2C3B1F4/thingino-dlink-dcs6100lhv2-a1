"""Bounded UARTless capture inventory shared by planning and SD staging."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .media_preflight import MediaError
from .sd_package import is_matching_update_filename, read_snapshot
from .media_contracts import UARTLESS_CAPTURE_ACTIVE_FILENAME as ACTIVE

CAPTURE_ROOT = "DCS6100F"
PASSIVE = "UARTCAP.PSV"
UPLOAD = ".uartless-capture-upload.part"
PENDING = ".uartless-reuse.pending"
DIRECTORIES = {
    CAPTURE_ROOT,
    *(
        CAPTURE_ROOT + "/" + name
        for name in ("copy-a", "copy-b", "preserved", "vendor", "vendor/files")
    ),
}
FILE_LIMITS = {PASSIVE: 8 * 1024 * 1024, UPLOAD: 8 * 1024 * 1024}
for copy in ("copy-a", "copy-b"):
    for index, size in enumerate(
        (0x40000, 0x1C0000, 0x480000, 0x7C0000, 0x180000, 0x40000)
    ):
        FILE_LIMITS[f"{CAPTURE_ROOT}/{copy}/mtd{index}.bin"] = size
for index, size in ((0, 0x40000), (4, 0x180000), (5, 0x40000)):
    FILE_LIMITS[f"{CAPTURE_ROOT}/preserved/mtd{index}.bin"] = size
for name in (
    "device-layout.private.json",
    "CAPTURE.OK",
    "preserved/device-layout.private.json",
    "vendor/vendor-bundle.private.json",
):
    FILE_LIMITS[f"{CAPTURE_ROOT}/{name}"] = 64 * 1024
for name in (
    "libimp.so",
    "libalog.so",
    "libsysutils.so",
    "libaudioProcess.so",
    "tx-isp-t31.ko",
    "sensor_os02g10_t31.ko",
    "os02g10-t31.bin",
):
    FILE_LIMITS[f"{CAPTURE_ROOT}/vendor/files/{name}"] = 8 * 1024 * 1024
# Preserve metadata and interrupted upload bytes too; never interpret them as recovery.
for name in tuple(FILE_LIMITS) + tuple(DIRECTORIES):
    path = Path(name)
    FILE_LIMITS[str(path.parent / ("._" + path.name))] = 1024 * 1024
ROOT_NAMES = {
    PASSIVE,
    UPLOAD,
    CAPTURE_ROOT,
    "._" + PASSIVE,
    "._" + UPLOAD,
    "._" + CAPTURE_ROOT,
}
MAX_TOTAL = 128 * 1024 * 1024
ROLLBACK_NAMES = {
    ".thingino-recovery-deactivate-rollback.part",
    "._.thingino-recovery-deactivate-rollback.part",
}


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if root.is_symlink() or not root.is_dir():
        raise MediaError("capture root is not a real directory")
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink() or not parent.is_dir():
            raise MediaError("capture parent is linked or missing")
    if path.is_symlink():
        raise MediaError("capture file is a symlink: " + relative)
    return path


def file_identity(root: Path, relative: str) -> dict[str, object]:
    if relative not in FILE_LIMITS:
        raise MediaError("unknown capture file: " + relative)
    path = checked_path(root, relative)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode):
        raise MediaError("capture file is not regular: " + relative)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > FILE_LIMITS[relative]
        ):
            raise MediaError("capture file type or size is not allowed: " + relative)
        digest = hashlib.sha256()
        total = 0
        while block := stream.read(1024 * 1024):
            total += len(block)
            if total > FILE_LIMITS[relative]:
                raise MediaError("capture file grew while reading")
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = checked_path(root, relative).stat(follow_symlinks=False)
    fields = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )
    if (
        fields(before) != fields(after)
        or fields(after) != fields(current)
        or total != before.st_size
    ):
        raise MediaError("capture file changed while reading: " + relative)
    return {"kind": "file", "size": total, "sha256": digest.hexdigest()}


def reject_active_updater(root: Path) -> None:
    if any(is_matching_update_filename(entry.name) for entry in root.iterdir()):
        raise MediaError(
            "active stock updater present; use its explicit handoff/passivation workflow first"
        )
    if any(
        entry.name.casefold() in {name.casefold() for name in ROLLBACK_NAMES}
        for entry in root.iterdir()
    ):
        raise MediaError(
            "interrupted updater handoff remains; inspect its original recovery workflow before capture reuse"
        )


def validate_capture_handoff(root: Path, package: bytes) -> None:
    matches = [
        entry.name
        for entry in root.iterdir()
        if is_matching_update_filename(entry.name)
    ]
    if matches != [ACTIVE]:
        raise MediaError("capture handoff requires exactly its active updater")
    for name in {"._" + ACTIVE, "._" + PASSIVE} | ROLLBACK_NAMES:
        if (root / name).exists() or (root / name).is_symlink():
            raise MediaError(
                "capture handoff contains an ambiguous sidecar or interrupted state"
            )
    active = checked_path(root, ACTIVE)
    if (
        not active.is_file()
        or active.stat().st_size != len(package)
        or read_snapshot(active) != package
    ):
        raise MediaError("active capture package is missing or changed")
    passive = root / PASSIVE
    if passive.exists() or passive.is_symlink():
        if (
            file_identity(root, PASSIVE)["sha256"]
            != hashlib.sha256(package).hexdigest()
        ):
            raise MediaError("passive capture package differs from the active package")


def ensure_capture_ready(root: Path, *, prepared: bool = False) -> None:
    """Reject stale collector output before planning or staging another capture."""
    reject_active_updater(root)
    for name in (PENDING, "._" + PENDING):
        if (root / name).exists() or (root / name).is_symlink():
            raise MediaError(
                "capture archival is incomplete; inspect and explicitly resume its original archive"
            )
    allowed = {PASSIVE} if prepared else set()
    for entry in root.iterdir():
        if entry.name.casefold() in {name.casefold() for name in ROOT_NAMES - allowed}:
            raise MediaError(
                "capture data already exists; plan stock-recovery uartless-reuse first: "
                + entry.name
            )


def capture_inventory(root: Path) -> dict[str, dict[str, object]]:
    reject_active_updater(root)
    inventory: dict[str, dict[str, object]] = {}
    pending = []
    for entry in root.iterdir():
        if entry.name.casefold() in {name.casefold() for name in ROOT_NAMES}:
            if entry.name not in ROOT_NAMES:
                raise MediaError(
                    "capture filename case differs from the fixed contract"
                )
            pending.append(entry.name)
    while pending:
        relative = pending.pop()
        path = checked_path(root, relative)
        if path.is_dir():
            if relative not in DIRECTORIES:
                raise MediaError("unknown capture directory: " + relative)
            inventory[relative] = {"kind": "directory"}
            for child in path.iterdir():
                child_name = relative + "/" + child.name
                if child_name not in DIRECTORIES and child_name not in FILE_LIMITS:
                    raise MediaError("unknown capture path: " + child_name)
                pending.append(child_name)
        else:
            inventory[relative] = file_identity(root, relative)
        if len(inventory) > len(FILE_LIMITS) + len(DIRECTORIES):
            raise MediaError("capture inventory is too large")
    if sum(item.get("size", 0) for item in inventory.values()) > MAX_TOTAL:
        raise MediaError("capture inventory exceeds the bounded size")
    return dict(sorted(inventory.items()))
