"""Read-only planning for stock restoration from a functional capture.

This does not arm media or replace the exact-original restore contract. Only
kernel/rootfs bytes come from the explicitly selected stock source. Application
data and every protected partition must come from the current camera capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .full_backup import (
    validate_complete_backup_with_manifest,
    validate_functional_backup_with_manifest,
)
from .recovery import RecoveryError, _partition_snapshot, validate_same_device_stock_images
from .recovery_gate import validate_functional_recovery_boundary


def manifest_identity(manifest: dict[str, object]) -> str:
    """Canonical identity of the validated manifest, not its JSON formatting."""
    return hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")).hexdigest()


def _selected(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RecoveryError("functional stock selection identity is invalid")


def load_functional_stock_selection(path: Path) -> FunctionalStockSelection:
    """Read one bounded private selection; never emit its camera identities."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & 0o077 or not 1 <= info.st_size <= 8192):
                raise RecoveryError("functional stock selection file policy differs")
            raw = os.read(descriptor, 8193)
            after = os.fstat(descriptor)
            if len(raw) != info.st_size or after.st_mtime_ns != info.st_mtime_ns:
                raise RecoveryError("functional stock selection changed while read")
        finally:
            os.close(descriptor)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise RecoveryError("duplicate functional stock selection field")
                result[key] = value
            return result
        doc = json.loads(raw, object_pairs_hook=unique)
    except (OSError, ValueError) as exc:
        raise RecoveryError("cannot read functional stock selection") from exc
    keys = {"schema_version", "stock_source_dir", "expected_camera_identity",
            "expected_capture_identity", "expected_stock_source_identity"}
    if (not isinstance(doc, dict) or set(doc) != keys
            or type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or not isinstance(doc["stock_source_dir"], str)
            or not Path(doc["stock_source_dir"]).is_absolute()):
        raise RecoveryError("functional stock selection schema differs")
    for name in keys - {"schema_version", "stock_source_dir"}:
        _selected(doc[name])
    return FunctionalStockSelection(
        Path(doc["stock_source_dir"]), doc["expected_camera_identity"],
        doc["expected_capture_identity"], doc["expected_stock_source_identity"],
    )


def _snapshot(root: Path, manifest: dict[str, object], mtd: int) -> bytes:
    raw = _partition_snapshot(root, mtd)
    record = manifest["files"][f"copy-a/mtd{mtd}.bin"]
    if len(raw) != record["size"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise RecoveryError("functional stock input changed after validation")
    return raw


@dataclass(frozen=True)
class FunctionalStockSelection:
    stock_source_dir: Path
    expected_camera_identity: str = field(repr=False)
    expected_capture_identity: str
    expected_stock_source_identity: str

    def validate(self, *, recovery_dir: Path, preserved_readback_dir: Path) -> FunctionalStockPlan:
        return plan_functional_stock_restore(
            recovery_dir=recovery_dir, preserved_readback_dir=preserved_readback_dir,
            stock_source_dir=self.stock_source_dir,
            expected_camera_identity=self.expected_camera_identity,
            expected_capture_identity=self.expected_capture_identity,
            expected_stock_source_identity=self.expected_stock_source_identity,
        )


@dataclass(frozen=True)
class FunctionalStockPlan:
    camera_identity: str = field(repr=False)
    capture_identity: str
    stock_source_identity: str
    mtd1: bytes = field(repr=False)
    mtd2: bytes = field(repr=False)
    mtd3: bytes = field(repr=False)
    protected: tuple[bytes, bytes, bytes] = field(repr=False)

    @property
    def write_set(self) -> tuple[int, int, int]:
        return (3, 2, 1)

    def private_origin(self) -> dict[str, object]:
        return {
            "restoration_class": "functional-stock",
            "original_complete_backup_accepted": False,
            "camera_identity": self.camera_identity,
            "capture_identity": self.capture_identity,
            "stock_source_identity": self.stock_source_identity,
            "stock_source_mtd": [1, 2],
            "current_camera_mtd": [0, 3, 4, 5],
        }

    def summary(self) -> dict[str, object]:
        return {
            "restoration_class": "functional-stock",
            "original_complete_backup_accepted": False,
            "input_validation_passed": True,
            "media_prepared": False,
            "physical_restore_proven": False,
            "write_set": [],
            "proposed_restore_write_set": [3, 2, 1],
            "protected_mtd": [0, 4, 5],
        }


def plan_functional_stock_restore(
    *, recovery_dir: Path, preserved_readback_dir: Path,
    stock_source_dir: Path, expected_camera_identity: str,
    expected_capture_identity: str, expected_stock_source_identity: str,
) -> FunctionalStockPlan:
    """Validate explicit source selections and return immutable bytes only.

    The caller must select the three identities before calling. Fresh physical
    binding and media authorization are still required by a future execution path.
    No manifest is rewritten and no filesystem or device write is performed here.
    """
    for value in (expected_camera_identity, expected_capture_identity,
                  expected_stock_source_identity):
        _selected(value)
    _, capture = validate_functional_backup_with_manifest(recovery_dir)
    _, stock = validate_complete_backup_with_manifest(stock_source_dir)
    if (manifest_identity(capture) != expected_capture_identity
            or manifest_identity(stock) != expected_stock_source_identity):
        raise RecoveryError("functional stock source selection differs")
    binding = validate_functional_recovery_boundary(
        recovery_dir=recovery_dir, preserved_readback_dir=preserved_readback_dir,
    )
    if binding.camera_identity_sha256 != expected_camera_identity:
        raise RecoveryError("functional stock camera selection differs")
    current = {i: _snapshot(recovery_dir, capture, i) for i in (0, 3, 4, 5)}
    source = {i: _snapshot(stock_source_dir, stock, i) for i in (0, 1, 2, 4)}
    if any(current[i] != source[i] for i in (0, 4)):
        raise RecoveryError("stock source bootloader or vendor compatibility differs")
    validate_same_device_stock_images(source[1], source[2], current[3])
    return FunctionalStockPlan(
        expected_camera_identity, expected_capture_identity,
        expected_stock_source_identity, source[1], source[2], current[3],
        (current[0], current[4], current[5]),
    )
