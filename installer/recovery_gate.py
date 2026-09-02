"""Validate same-device full-flash recovery evidence before any NOR write."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .full_backup import (
    FullBackupError,
    validate_complete_backup_with_manifest,
)
from .layout import TARGET, Target


class RecoveryGateError(ValueError):
    """Recovery evidence or preserved-partition readback is not exact."""


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    mode: str
    preserved_mtd: tuple[int, ...]
    recovery_images: int


def _read_regular(path: Path, label: str, *, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RecoveryGateError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise RecoveryGateError(f"{label} is not a bounded regular file")
        raw = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
        raise RecoveryGateError(f"{label} changed while being read")
    if len(raw) != before.st_size:
        raise RecoveryGateError(f"{label} read was incomplete")
    return raw


def _private_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
        raise RecoveryGateError("recovery manifest contains an unsafe path")
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise RecoveryGateError("recovery evidence contains a symlink")
    return current


def _identity(path: Path, label: str, expected: dict[str, object]) -> str:
    size = expected.get("size")
    digest = expected.get("sha256")
    if not isinstance(size, int) or size <= 0 or not isinstance(digest, str) or len(digest) != 64:
        raise RecoveryGateError("recovery manifest has an invalid file identity")
    raw = _read_regular(path, label, limit=size)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise RecoveryGateError(f"{label} does not match its private manifest")
    return digest


def _target_document(target: Target) -> dict[str, object]:
    return {
        "erase_block_size": target.erase_block_size,
        "flash_size": target.nor_size,
        "hardware_revision": target.hardware_revision,
        "model": target.model,
        "partitions": [
            {
                "mtd": partition.mtd,
                "name": partition.name,
                "offset": partition.offset,
                "size": partition.size,
            }
            for partition in target.partitions
        ],
    }


def validate_existing_recovery_boundary(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    target: Target = TARGET,
) -> RecoveryDecision:
    """Accept an old recovery pair only when current preserved MTDs still match."""

    try:
        decision, manifest = validate_complete_backup_with_manifest(
            recovery_dir, target=target
        )
    except FullBackupError as exc:
        raise RecoveryGateError(str(exc)) from exc
    files = manifest.get("files")
    assert isinstance(files, dict)

    if preserved_readback_dir.is_symlink() or not preserved_readback_dir.is_dir():
        raise RecoveryGateError("preserved readback is not a private directory")
    expected_readback = {"device-layout.private.json", "mtd0.bin", "mtd4.bin", "mtd5.bin"}
    if {entry.name for entry in preserved_readback_dir.iterdir()} != expected_readback:
        raise RecoveryGateError("preserved readback directory is not exact")
    layout_raw = _read_regular(
        preserved_readback_dir / "device-layout.private.json",
        "read-only device layout",
        limit=64 * 1024,
    )
    try:
        layout = json.loads(layout_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryGateError("read-only device layout is invalid") from exc
    if layout != {"read_only": True, "schema_version": 1, "target": _target_document(target)}:
        raise RecoveryGateError("read-only camera identity or partition layout differs")
    for mtd in (0, 4, 5):
        partition = target.partition(mtd)
        current = _read_regular(
            preserved_readback_dir / f"mtd{mtd}.bin",
            f"current preserved mtd{mtd}",
            limit=partition.size,
        )
        if len(current) != partition.size:
            raise RecoveryGateError(f"current preserved mtd{mtd} has the wrong size")
        reference = files[f"copy-a/mtd{mtd}.bin"]
        assert isinstance(reference, dict)
        if hashlib.sha256(current).hexdigest() != reference.get("sha256"):
            raise RecoveryGateError(f"current preserved mtd{mtd} differs from same-device recovery")
    return RecoveryDecision(
        mode="existing-verified-same-device-pair",
        preserved_mtd=(0, 4, 5),
        recovery_images=decision.recovery_images,
    )
