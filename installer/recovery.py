"""Recovery package construction with explicit stock/stage-1 separation."""

from __future__ import annotations

import binascii
import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from .artifacts import ArtifactError, validate_stage1_images
from .full_backup import validate_complete_backup
from .layout import TARGET
from .official_input import OfficialInput, OfficialInputError, require_stock_kernel_rootfs
from .recovery_gate import validate_existing_recovery_boundary
from .sd_package import (
    MAX_SOURCE_PACKAGE_SIZE,
    PackageError,
    atomic_write,
    generate_bootstrap,
    package_manifest,
    parse_package,
    read_snapshot,
    validate_bootstrap,
    validate_stock_source,
)


class RecoveryError(ValueError):
    """A recovery package cannot be proven safe from the supplied inputs."""


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    package: bytes
    manifest: bytes
    kind: str


STOCK_RESTORE_PACKAGE_NAME = "DCS6100LHV2Ax_FW000S02_STOCK_SD.bin"
STOCK_RESTORE_MTD3_NAME = "stock-mtd3.private.bin"
STOCK_RESTORE_MANIFEST_NAME = "stock-restore.private.json"
STOCK_RESTORE_WRITE_SET = (3, 2, 1)
STOCK_RESTORE_PROTECTED_MTD = (0, 4, 5)
LIVE_STOCK_RESTORE_BLOCKER = (
    "live restore is unavailable until an unarmed RAM set is staged and an "
    "exact SD, camera, write-set, protected-set, and activation-last "
    "confirmation is supplied"
)
JFFS2_KNOWN_NODE_TYPES = frozenset(
    {
        0xE001,  # dirent
        0xE002,  # inode
        0x2003,  # cleanmarker
        0x2004,  # padding
        0x2006,  # summary
        0xE008,  # xattr
        0xE009,  # xref
    }
)


@dataclass(frozen=True, slots=True)
class SameDeviceStockRestorePlan:
    package: bytes
    mtd1: bytes
    mtd2: bytes
    mtd3: bytes
    protected: tuple[bytes, bytes, bytes]
    write_set: tuple[int, ...] = STOCK_RESTORE_WRITE_SET
    prepare_only: bool = True


def _partition_snapshot(recovery_dir: Path, mtd: int) -> bytes:
    path = recovery_dir / f"copy-a/mtd{mtd}.bin"
    partition = TARGET.partition(mtd)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RecoveryError(f"cannot read private mtd{mtd} recovery image") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != partition.size
        ):
            raise RecoveryError(f"private mtd{mtd} recovery image is not exact")
        raw = os.read(descriptor, partition.size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != partition.size
    ):
        raise RecoveryError(f"private mtd{mtd} recovery image changed while read")
    return raw


def validate_stock_mtd1(raw: bytes) -> int:
    """Accept one exact stock Linux/MIPS uImage partition snapshot."""

    partition = TARGET.partition(1)
    if len(raw) != partition.size or len(raw) < 64:
        raise RecoveryError("stock mtd1 has the wrong partition size")
    fields = struct.unpack(">7I4B32s", raw[:64])
    (
        magic,
        header_crc,
        _timestamp,
        payload_size,
        load_address,
        entry_point,
        payload_crc,
        os_id,
        architecture,
        image_type,
        _compression,
        _name,
    ) = fields
    if magic != 0x27051956:
        raise RecoveryError("stock mtd1 is not a uImage")
    header = bytearray(raw[:64])
    struct.pack_into(">I", header, 4, 0)
    if binascii.crc32(header) & 0xFFFFFFFF != header_crc:
        raise RecoveryError("stock mtd1 uImage header CRC differs")
    image_end = 64 + payload_size
    if payload_size <= 0 or image_end > len(raw):
        raise RecoveryError("stock mtd1 uImage payload length is invalid")
    if binascii.crc32(raw[64:image_end]) & 0xFFFFFFFF != payload_crc:
        raise RecoveryError("stock mtd1 uImage payload CRC differs")
    if (os_id, architecture, image_type) != (5, 5, 2):
        raise RecoveryError("stock mtd1 is not a Linux/MIPS kernel uImage")
    if not (0x80000000 <= load_address < 0xA0000000):
        raise RecoveryError("stock mtd1 has an unexpected MIPS load address")
    if not (0x80000000 <= entry_point < 0xA0000000):
        raise RecoveryError("stock mtd1 has an unexpected MIPS entry point")
    if any(byte != 0xFF for byte in raw[image_end:]):
        raise RecoveryError("stock mtd1 has non-erased bytes after its uImage")
    return image_end


def validate_stock_mtd2(raw: bytes) -> int:
    partition = TARGET.partition(2)
    if len(raw) != partition.size:
        raise RecoveryError("stock mtd2 has the wrong partition size")
    if len(raw) < 48 or raw[:4] != b"hsqs":
        raise RecoveryError("stock mtd2 is not a little-endian SquashFS")
    major = struct.unpack_from("<H", raw, 28)[0]
    bytes_used = struct.unpack_from("<Q", raw, 40)[0]
    if major != 4 or bytes_used < 48 or bytes_used > len(raw):
        raise RecoveryError("stock mtd2 SquashFS superblock is invalid")
    trailing = raw[bytes_used:]
    padded_end = (bytes_used + 4095) & ~4095
    zero_page_padding_then_erased = (
        padded_end <= len(raw)
        and raw[bytes_used:padded_end] == b"\x00" * (padded_end - bytes_used)
        and raw[padded_end:] == b"\xff" * (len(raw) - padded_end)
    )
    if trailing and not (
        trailing == b"\x00" * len(trailing)
        or trailing == b"\xff" * len(trailing)
        or zero_page_padding_then_erased
    ):
        raise RecoveryError("stock mtd2 has unexpected bytes after SquashFS")
    return bytes_used


def validate_stock_mtd3(raw: bytes) -> None:
    partition = TARGET.partition(3)
    if len(raw) != partition.size:
        raise RecoveryError("stock mtd3 has the wrong partition size")
    magic, node_type, total_length, header_crc = struct.unpack_from("<HHII", raw, 0)
    expected_crc = (binascii.crc32(raw[:8], -1) ^ -1) & 0xFFFFFFFF
    if (
        magic != 0x1985
        or node_type != 0x2003
        or node_type not in JFFS2_KNOWN_NODE_TYPES
        or total_length != 12
        or header_crc != expected_crc
    ):
        raise RecoveryError(
            "stock mtd3 does not begin with an exact JFFS2 cleanmarker"
        )


def validate_same_device_stock_images(
    mtd1: bytes, mtd2: bytes, mtd3: bytes
) -> None:
    validate_stock_mtd1(mtd1)
    validate_stock_mtd2(mtd2)
    validate_stock_mtd3(mtd3)


def _restore_manifest(plan: SameDeviceStockRestorePlan) -> bytes:
    package = parse_package(plan.package, require_project_header=True)
    manifest = {
        "schema_version": 1,
        "purpose": "same-device-stock-1.02.02-prepare-only",
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
            "nor_size": TARGET.nor_size,
        },
        "files": {
            STOCK_RESTORE_PACKAGE_NAME: {
                "sha256": hashlib.sha256(plan.package).hexdigest(),
                "size": len(plan.package),
            },
            STOCK_RESTORE_MTD3_NAME: {
                "sha256": hashlib.sha256(plan.mtd3).hexdigest(),
                "size": len(plan.mtd3),
            },
        },
        "checks": {
            "duplicate_backup_accepted": True,
            "full_flash_reconstruction_accepted": True,
            "same_device_binding_accepted": True,
            "stock_mtd1_linux_mips_uimage_accepted": True,
            "stock_mtd2_squashfs_accepted": True,
            "stock_mtd3_jffs2_accepted": True,
        },
        "package_records": [
            {
                "erase_span": record.erase_span,
                "flash_offset": record.flash_offset,
                "mtd": mtd,
                "payload_size": record.payload_length,
            }
            for mtd, record in zip((1, 2), package.records)
        ],
        "protected_mtd": list(STOCK_RESTORE_PROTECTED_MTD),
        "restore_order": [
            "mtd3-full-and-readback",
            "mtd2-full-and-readback",
            "mtd1-tail-and-readback",
            "mtd1-activation-block-and-readback-last",
            "complete-mtd1-mtd2-mtd3-readback",
        ],
        "write_set": list(STOCK_RESTORE_WRITE_SET),
        "prepare_only": True,
        "physical_restore_proven": False,
        "stock_uboot_success_is_readback": False,
        "live_restore_available": True,
        "live_restore_requirements": LIVE_STOCK_RESTORE_BLOCKER,
    }
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def _write_restore_output(output_dir: Path, plan: SameDeviceStockRestorePlan) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        raise RecoveryError("refusing to overwrite an existing restore preparation")
    output_dir.mkdir(mode=0o700, parents=False)
    artifacts = {
        STOCK_RESTORE_PACKAGE_NAME: plan.package,
        STOCK_RESTORE_MTD3_NAME: plan.mtd3,
    }
    try:
        for name, raw in artifacts.items():
            atomic_write(output_dir / name, raw)
            if (output_dir / name).read_bytes() != raw:
                raise RecoveryError(f"restore preparation readback failed for {name}")
        manifest = _restore_manifest(plan)
        atomic_write(output_dir / STOCK_RESTORE_MANIFEST_NAME, manifest)
        if (output_dir / STOCK_RESTORE_MANIFEST_NAME).read_bytes() != manifest:
            raise RecoveryError("restore preparation manifest readback failed")
    except BaseException:
        raise


def prepare_same_device_stock_restore(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    output_dir: Path,
) -> SameDeviceStockRestorePlan:
    """Prepare private stock artifacts; never touch a camera, SD card, or NOR."""

    validate_complete_backup(recovery_dir)
    validate_existing_recovery_boundary(
        recovery_dir=recovery_dir,
        preserved_readback_dir=preserved_readback_dir,
    )
    mtd1 = _partition_snapshot(recovery_dir, 1)
    mtd2 = _partition_snapshot(recovery_dir, 2)
    mtd3 = _partition_snapshot(recovery_dir, 3)
    validate_same_device_stock_images(mtd1, mtd2, mtd3)
    package_raw = generate_bootstrap(mtd1, mtd2)
    package = parse_package(package_raw, require_project_header=True)
    validate_bootstrap(package)
    if tuple(record.flash_offset for record in package.records) != (
        TARGET.partition(1).offset,
        TARGET.partition(2).offset,
    ):
        raise RecoveryError("stock restore package has an unsafe write set")
    plan = SameDeviceStockRestorePlan(
        package=package_raw,
        mtd1=mtd1,
        mtd2=mtd2,
        mtd3=mtd3,
        protected=tuple(
            _partition_snapshot(recovery_dir, mtd)
            for mtd in STOCK_RESTORE_PROTECTED_MTD
        ),
    )
    _write_restore_output(output_dir, plan)
    return plan


def inspect_same_device_stock_restore(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    output_dir: Path,
) -> SameDeviceStockRestorePlan:
    validate_complete_backup(recovery_dir)
    validate_existing_recovery_boundary(
        recovery_dir=recovery_dir,
        preserved_readback_dir=preserved_readback_dir,
    )
    expected_names = {
        STOCK_RESTORE_PACKAGE_NAME,
        STOCK_RESTORE_MTD3_NAME,
        STOCK_RESTORE_MANIFEST_NAME,
    }
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise RecoveryError("restore preparation is not a private directory")
    if {entry.name for entry in output_dir.iterdir()} != expected_names:
        raise RecoveryError("restore preparation file closure is not exact")
    try:
        manifest_raw = read_snapshot(output_dir / STOCK_RESTORE_MANIFEST_NAME)
        if len(manifest_raw) > 128 * 1024:
            raise RecoveryError("restore preparation manifest is too large")
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("restore preparation manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise RecoveryError("restore preparation manifest is invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {
        STOCK_RESTORE_PACKAGE_NAME,
        STOCK_RESTORE_MTD3_NAME,
    }:
        raise RecoveryError("restore preparation manifest file closure differs")
    package_raw = read_snapshot(output_dir / STOCK_RESTORE_PACKAGE_NAME)
    mtd3 = read_snapshot(output_dir / STOCK_RESTORE_MTD3_NAME)
    for name, raw in (
        (STOCK_RESTORE_PACKAGE_NAME, package_raw),
        (STOCK_RESTORE_MTD3_NAME, mtd3),
    ):
        record = files.get(name)
        if (
            not isinstance(record, dict)
            or record.get("size") != len(raw)
            or record.get("sha256") != hashlib.sha256(raw).hexdigest()
        ):
            raise RecoveryError("restore preparation file identity differs")
    package = parse_package(package_raw, require_project_header=True)
    validate_bootstrap(package)
    mtd1 = _partition_snapshot(recovery_dir, 1)
    mtd2 = _partition_snapshot(recovery_dir, 2)
    reference_mtd3 = _partition_snapshot(recovery_dir, 3)
    if tuple(record.payload for record in package.records) != (mtd1, mtd2):
        raise RecoveryError("restore package does not contain the same-device mtd1/mtd2")
    if mtd3 != reference_mtd3:
        raise RecoveryError("restore preparation mtd3 is not the same-device image")
    validate_same_device_stock_images(mtd1, mtd2, mtd3)
    if (
        manifest.get("write_set") != list(STOCK_RESTORE_WRITE_SET)
        or manifest.get("protected_mtd") != list(STOCK_RESTORE_PROTECTED_MTD)
        or manifest.get("prepare_only") is not True
        or manifest.get("physical_restore_proven") is not False
        or manifest.get("live_restore_available") is not True
    ):
        raise RecoveryError("restore preparation safety contract differs")
    return SameDeviceStockRestorePlan(
        package=package_raw,
        mtd1=mtd1,
        mtd2=mtd2,
        mtd3=mtd3,
        protected=tuple(
            _partition_snapshot(recovery_dir, mtd)
            for mtd in STOCK_RESTORE_PROTECTED_MTD
        ),
    )


def build_stage1_recovery(kernel: bytes, rootfs: bytes) -> RecoveryResult:
    """Build a repeatable mtd1/mtd2 stage-1 reinstall package.

    This is deliberately not called stock recovery.  It restores installer
    reachability while mtd0 remains intact, but it cannot recreate the original
    D-Link kernel/rootfs.
    """

    try:
        validate_stage1_images(kernel, rootfs)
    except ArtifactError as exc:
        raise RecoveryError(f"stage-1 image validation failed: {exc}") from exc
    package_bytes = generate_bootstrap(kernel, rootfs)
    package = parse_package(package_bytes, require_project_header=True)
    validate_bootstrap(package)
    manifest = json.loads(
        package_manifest(package, purpose="stage1-recovery").decode("utf-8")
    )
    manifest["recovery"] = {
        "restores": ["mtd1", "mtd2"],
        "stock_restore": False,
        "requires_intact_stock_mtd0": True,
    }
    return RecoveryResult(
        package=package_bytes,
        manifest=(json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        kind="stage1-recovery",
    )


def build_stock_recovery_from_approved_sd(
    source: OfficialInput,
    *,
    approved_stock_sd_sha256: str,
) -> RecoveryResult:
    """Extract mtd1/mtd2 from an independently approved stock SD package.

    The current catalog intentionally has no such input.  This function exists
    so the recovery path is reviewable and testable once an official raw SD
    package identity is established; it cannot be reached from the known
    encrypted application update.
    """

    try:
        require_stock_kernel_rootfs(source)
    except OfficialInputError as exc:
        raise RecoveryError(str(exc)) from exc
    digest = hashlib.sha256(source.snapshot).hexdigest()
    if digest != approved_stock_sd_sha256:
        raise RecoveryError("stock SD source digest is not the approved digest")
    try:
        stock = parse_package(source.snapshot, max_size=MAX_SOURCE_PACKAGE_SIZE)
        validate_stock_source(stock)
        package_bytes = generate_bootstrap(
            stock.records[1].payload,
            stock.records[2].payload,
        )
        package = parse_package(package_bytes, require_project_header=True)
        validate_bootstrap(package)
    except PackageError as exc:
        raise RecoveryError(f"stock SD source failed strict validation: {exc}") from exc
    manifest = json.loads(
        package_manifest(package, purpose="stock-mtd1-mtd2-recovery").decode("utf-8")
    )
    manifest["source"] = {
        "filename": source.filename,
        "sha256": source.sha256,
        "version": source.version,
    }
    manifest["recovery"] = {
        "restores": ["mtd1", "mtd2"],
        "stock_restore": True,
        "requires_intact_stock_mtd0": True,
    }
    return RecoveryResult(
        package=package_bytes,
        manifest=(json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        kind="stock-recovery",
    )


def write_recovery_result(
    result: RecoveryResult,
    *,
    package_path: Path,
    manifest_path: Path,
) -> None:
    if package_path.parent != manifest_path.parent:
        raise RecoveryError("package and private manifest must share one output directory")
    if package_path.exists() or manifest_path.exists():
        raise RecoveryError("refusing to overwrite an existing recovery result")
    atomic_write(package_path, result.package)
    try:
        atomic_write(manifest_path, result.manifest)
    except BaseException:
        package_path.unlink(missing_ok=True)
        raise
