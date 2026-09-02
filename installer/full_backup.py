"""Private, duplicate, same-device recovery evidence for one camera.

The MTD transport lives in the bounded read-only RAM collector.  This module
validates that collector's closed output and turns it into the canonical
private backup without exposing identifiers or digests through its API.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .layout import MTD_PHYSICAL_ERASE_SIZE, MTD_WRITE_SIZE, TARGET, Target


MANIFEST_NAME = "manifest.private.json"
COPY_NAMES = ("a", "b")
FIRMWARE_RUNTIME = "1.02.02"


class FullBackupError(ValueError):
    """A complete private backup cannot be captured or accepted safely."""


@dataclass(frozen=True, slots=True)
class CompleteBackupDecision:
    duplicate_partitions_accepted: bool
    full_flash_reconstruction_accepted: bool
    partition_count: int
    recovery_images: int


PartitionReader = Callable[[str, int], bytes]
StorageReader = Callable[[Path], bytes]


def target_layout_document(target: Target = TARGET) -> dict[str, object]:
    return {
        "all_partitions_read_only": True,
        "erase_size": MTD_PHYSICAL_ERASE_SIZE,
        "hardware_revision": target.hardware_revision,
        "model": target.model,
        "nor_size": target.nor_size,
        "partition_count": len(target.partitions),
        "partitions": [
            {
                "mtd": partition.mtd,
                "name": partition.name,
                "offset": partition.offset,
                "read_only": True,
                "size": partition.size,
            }
            for partition in target.partitions
        ],
        "write_size": MTD_WRITE_SIZE,
    }


def _identity(raw: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _device_binding(parts: dict[int, bytes]) -> str:
    digest = hashlib.sha256()
    for mtd in (0, 4, 5):
        raw = parts[mtd]
        digest.update(f"mtd{mtd}".encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _read_regular(path: Path, label: str, *, expected_size: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise FullBackupError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != expected_size
        ):
            raise FullBackupError(f"{label} is not one exact unlinked regular file")
        raw = os.read(descriptor, expected_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity:
        raise FullBackupError(f"{label} changed while being read")
    if len(raw) != expected_size:
        raise FullBackupError(f"{label} read was incomplete")
    return raw


def _write_exclusive(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise FullBackupError(f"refusing to replace backup output {path.name}") from exc
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FullBackupError(f"short write for backup output {path.name}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_and_readback(
    path: Path,
    raw: bytes,
    *,
    storage_reader: StorageReader | None,
) -> None:
    _write_exclusive(path, raw)
    actual = (
        storage_reader(path)
        if storage_reader is not None
        else _read_regular(path, path.name, expected_size=len(raw))
    )
    if not isinstance(actual, bytes) or actual != raw:
        raise FullBackupError(f"storage readback mismatch for {path.name}")


def capture_complete_backup(
    *,
    output_dir: Path,
    confirmed_output_dir: Path,
    layout: dict[str, object],
    partition_reader: PartitionReader,
    storage_reader: StorageReader | None = None,
    target: Target = TARGET,
) -> CompleteBackupDecision:
    """Capture two independent reads and publish the private manifest last."""

    if layout != target_layout_document(target):
        raise FullBackupError("camera identity, NOR geometry, or read-only layout differs")
    if (
        not output_dir.is_absolute()
        or confirmed_output_dir != output_dir
        or output_dir.parent.resolve(strict=True) != output_dir.parent
    ):
        raise FullBackupError("backup destination confirmation does not match")
    if output_dir.exists() or output_dir.is_symlink():
        raise FullBackupError("refusing to overwrite an existing backup destination")
    output_dir.mkdir(mode=0o700, parents=False)
    incomplete = output_dir / "capture.incomplete"
    _write_exclusive(incomplete, b"incomplete\n")

    files: dict[str, object] = {}
    copies: dict[str, dict[int, bytes]] = {}
    full_images: dict[str, bytes] = {}
    try:
        for copy in COPY_NAMES:
            copy_dir = output_dir / f"copy-{copy}"
            copy_dir.mkdir(mode=0o700)
            parts: dict[int, bytes] = {}
            for partition in target.partitions:
                raw = partition_reader(copy, partition.mtd)
                if not isinstance(raw, bytes):
                    raise FullBackupError("partition reader did not return immutable bytes")
                if len(raw) != partition.size:
                    raise FullBackupError(
                        f"mtd{partition.mtd} read has the wrong partition size"
                    )
                relative = f"copy-{copy}/mtd{partition.mtd}.bin"
                _write_and_readback(
                    output_dir / relative,
                    raw,
                    storage_reader=storage_reader,
                )
                files[relative] = _identity(raw)
                parts[partition.mtd] = raw
            copies[copy] = parts

        for partition in target.partitions:
            if copies["a"][partition.mtd] != copies["b"][partition.mtd]:
                raise FullBackupError(
                    f"duplicate reads differ for mtd{partition.mtd}"
                )

        for copy in COPY_NAMES:
            full = b"".join(
                copies[copy][partition.mtd] for partition in target.partitions
            )
            if len(full) != target.nor_size:
                raise FullBackupError("full-flash reconstruction is not exactly 16 MiB")
            name = f"full-flash-{copy}.bin"
            _write_and_readback(
                output_dir / name,
                full,
                storage_reader=storage_reader,
            )
            files[name] = _identity(full)
            full_images[copy] = full
        if full_images["a"] != full_images["b"]:
            raise FullBackupError("duplicate full-flash reconstructions differ")

        manifest = {
            "schema": 2,
            "checks": {
                "copy_a_matches_copy_b": True,
                "full_flash_a_matches_full_flash_b": True,
                "full_flash_reconstruction_matches": True,
                "layout_read_only_verified": True,
                "storage_destination_confirmed": True,
                "storage_readback_verified": True,
            },
            "device_binding_sha256": _device_binding(copies["a"]),
            "files": files,
            "firmware_runtime": FIRMWARE_RUNTIME,
            "partition_order": [
                f"mtd{partition.mtd}" for partition in target.partitions
            ],
            "target": layout,
            "total_flash_size": target.nor_size,
        }
        manifest_raw = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _write_and_readback(
            output_dir / MANIFEST_NAME,
            manifest_raw,
            storage_reader=storage_reader,
        )
        incomplete.unlink()
        directory_descriptor = os.open(output_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        raise
    return CompleteBackupDecision(True, True, len(target.partitions), 2)


def capture_complete_backup_from_ram_collector(
    *,
    collector_dir: Path,
    output_dir: Path,
    confirmed_output_dir: Path,
    target: Target = TARGET,
) -> CompleteBackupDecision:
    """Validate two device reads and create the canonical private evidence."""

    if collector_dir.is_symlink() or not collector_dir.is_dir():
        raise FullBackupError("RAM collector output is not a real directory")
    expected_root = {
        "CAPTURE.OK",
        "copy-a",
        "copy-b",
        "device-layout.private.json",
    }
    if {entry.name for entry in collector_dir.iterdir()} != expected_root:
        raise FullBackupError("RAM collector output closure is not exact")
    layout_path = collector_dir / "device-layout.private.json"
    if layout_path.is_symlink() or not layout_path.is_file():
        raise FullBackupError("RAM collector layout is missing")
    layout_raw = layout_path.read_bytes()
    if len(layout_raw) > 128 * 1024:
        raise FullBackupError("RAM collector layout is too large")
    try:
        layout = json.loads(layout_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullBackupError("RAM collector layout is invalid") from exc
    if layout != target_layout_document(target):
        raise FullBackupError("RAM collector target or read-only layout differs")
    completion = _read_regular(
        collector_dir / "CAPTURE.OK",
        "RAM collector completion",
        expected_size=len(
            b"duplicate_reads=complete;host_validation=required;nor_writes=false\n"
        ),
    )
    if completion != (
        b"duplicate_reads=complete;host_validation=required;nor_writes=false\n"
    ):
        raise FullBackupError("RAM collector completion contract differs")
    expected_partition_files = {
        f"mtd{partition.mtd}.bin" for partition in target.partitions
    }
    source_inodes: set[tuple[int, int]] = set()
    for copy in COPY_NAMES:
        directory = collector_dir / f"copy-{copy}"
        if directory.is_symlink() or not directory.is_dir():
            raise FullBackupError("RAM collector copy is not a real directory")
        if {entry.name for entry in directory.iterdir()} != expected_partition_files:
            raise FullBackupError("RAM collector partition closure differs")
        for partition in target.partitions:
            metadata = (directory / f"mtd{partition.mtd}.bin").stat(
                follow_symlinks=False
            )
            identity = (metadata.st_dev, metadata.st_ino)
            if identity in source_inodes:
                raise FullBackupError("RAM collector output contains a hardlink")
            source_inodes.add(identity)

    def partition_reader(copy: str, mtd: int) -> bytes:
        partition = target.partition(mtd)
        return _read_regular(
            collector_dir / f"copy-{copy}/mtd{mtd}.bin",
            f"RAM collector copy-{copy} mtd{mtd}",
            expected_size=partition.size,
        )

    return capture_complete_backup(
        output_dir=output_dir,
        confirmed_output_dir=confirmed_output_dir,
        layout=layout,
        partition_reader=partition_reader,
        target=target,
    )


def load_private_manifest(recovery_dir: Path) -> dict[str, object]:
    manifest_path = recovery_dir / MANIFEST_NAME
    try:
        descriptor = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise FullBackupError("private recovery manifest is missing") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > 128 * 1024
        ):
            raise FullBackupError("private recovery manifest is not exact")
        raw = os.read(descriptor, 128 * 1024 + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != before.st_size
    ):
        raise FullBackupError("private recovery manifest changed while read")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullBackupError("private recovery manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise FullBackupError("private recovery manifest is not an object")
    return manifest


def validate_complete_backup(
    recovery_dir: Path, *, target: Target = TARGET
) -> CompleteBackupDecision:
    """Validate exact directory closure, identities, duplicates, and layout."""

    return validate_complete_backup_with_manifest(
        recovery_dir, target=target
    )[0]


def validate_complete_backup_with_manifest(
    recovery_dir: Path, *, target: Target = TARGET
) -> tuple[CompleteBackupDecision, dict[str, object]]:
    """Return the decision and the one manifest snapshot used to prove it."""

    if recovery_dir.is_symlink() or not recovery_dir.is_dir():
        raise FullBackupError("recovery evidence is not a private directory")
    manifest = load_private_manifest(recovery_dir)
    decision = _validate_complete_backup_manifest(
        recovery_dir, manifest=manifest, target=target
    )
    return decision, manifest


def _validate_complete_backup_manifest(
    recovery_dir: Path,
    *,
    manifest: dict[str, object],
    target: Target,
) -> CompleteBackupDecision:
    schema = manifest.get("schema")
    expected_root = {
        MANIFEST_NAME,
        "copy-a",
        "copy-b",
        "full-flash-a.bin",
        "full-flash-b.bin",
    }
    actual_root = {entry.name for entry in recovery_dir.iterdir()}
    legacy_metadata_present = schema == 1 and "metadata" in actual_root
    if legacy_metadata_present:
        expected_root.add("metadata")
    if actual_root != expected_root:
        raise FullBackupError("recovery directory closure is not exact")
    if legacy_metadata_present:
        metadata_dir = recovery_dir / "metadata"
        expected_metadata = {
            "md5sums.txt",
            "proc-cmdline.txt",
            "proc-mounts.txt",
            "proc-mtd.txt",
            "sizes.txt",
        }
        if metadata_dir.is_symlink() or not metadata_dir.is_dir():
            raise FullBackupError("legacy recovery metadata is not a real directory")
        if {entry.name for entry in metadata_dir.iterdir()} != expected_metadata:
            raise FullBackupError("legacy recovery metadata closure is not exact")
        metadata_inodes: set[tuple[int, int]] = set()
        for name in expected_metadata:
            item = metadata_dir / name
            info = item.stat(follow_symlinks=False)
            identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size <= 0
                or info.st_size > 64 * 1024
                or identity in metadata_inodes
            ):
                raise FullBackupError("legacy recovery metadata is not exact")
            metadata_inodes.add(identity)
    partition_names = [f"mtd{partition.mtd}" for partition in target.partitions]
    for copy in COPY_NAMES:
        copy_dir = recovery_dir / f"copy-{copy}"
        if copy_dir.is_symlink() or not copy_dir.is_dir():
            raise FullBackupError("recovery copy is not a real directory")
        if {entry.name for entry in copy_dir.iterdir()} != {
            f"{name}.bin" for name in partition_names
        }:
            raise FullBackupError("recovery partition closure is not exact")

    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        raise FullBackupError("private recovery manifest lacks checks")
    if schema == 2:
        required_checks = (
            "copy_a_matches_copy_b",
            "full_flash_a_matches_full_flash_b",
            "full_flash_reconstruction_matches",
            "layout_read_only_verified",
            "storage_destination_confirmed",
            "storage_readback_verified",
        )
        if manifest.get("target") != target_layout_document(target):
            raise FullBackupError("private recovery target layout differs")
        binding = manifest.get("device_binding_sha256")
        if not isinstance(binding, str) or len(binding) != 64:
            raise FullBackupError("private recovery device binding is invalid")
    elif schema == 1:
        required_checks = (
            "copy_a_matches_copy_b",
            "device_md5_matches_host",
            "full_flash_a_matches_full_flash_b",
        )
    else:
        raise FullBackupError("private recovery manifest schema is unsupported")
    if not all(checks.get(name) is True for name in required_checks):
        raise FullBackupError("private recovery manifest lacks verified duplicate checks")
    if manifest.get("total_flash_size") != target.nor_size:
        raise FullBackupError("recovery images have the wrong flash size")
    if manifest.get("partition_order") != partition_names:
        raise FullBackupError("recovery manifest has the wrong partition order")
    if manifest.get("firmware_runtime") != FIRMWARE_RUNTIME:
        raise FullBackupError("recovery manifest has the wrong stock firmware version")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise FullBackupError("recovery manifest lacks file identities")
    expected_files = {
        *(f"copy-{copy}/{name}.bin" for copy in COPY_NAMES for name in partition_names),
        "full-flash-a.bin",
        "full-flash-b.bin",
    }
    if set(files) != expected_files:
        raise FullBackupError("recovery manifest file closure is not exact")

    copies: dict[str, dict[int, bytes]] = {}
    fulls: dict[str, bytes] = {}
    inodes: set[tuple[int, int]] = set()
    for copy in COPY_NAMES:
        parts: dict[int, bytes] = {}
        for partition in target.partitions:
            relative = f"copy-{copy}/mtd{partition.mtd}.bin"
            record = files.get(relative)
            if not isinstance(record, dict) or record.get("size") != partition.size:
                raise FullBackupError("recovery partition record has the wrong size")
            path = recovery_dir / relative
            metadata = path.stat(follow_symlinks=False)
            inode = (metadata.st_dev, metadata.st_ino)
            if inode in inodes:
                raise FullBackupError("recovery evidence contains a hardlink")
            inodes.add(inode)
            raw = _read_regular(path, relative, expected_size=partition.size)
            if hashlib.sha256(raw).hexdigest() != record.get("sha256"):
                raise FullBackupError(f"{relative} does not match its private manifest")
            parts[partition.mtd] = raw
        copies[copy] = parts
        full_name = f"full-flash-{copy}.bin"
        full_record = files.get(full_name)
        if not isinstance(full_record, dict) or full_record.get("size") != target.nor_size:
            raise FullBackupError("full-flash recovery record has the wrong size")
        full_path = recovery_dir / full_name
        metadata = full_path.stat(follow_symlinks=False)
        inode = (metadata.st_dev, metadata.st_ino)
        if inode in inodes:
            raise FullBackupError("recovery evidence contains a hardlink")
        inodes.add(inode)
        full = _read_regular(full_path, full_name, expected_size=target.nor_size)
        if hashlib.sha256(full).hexdigest() != full_record.get("sha256"):
            raise FullBackupError("full-flash image does not match its private manifest")
        reconstructed = b"".join(parts[p.mtd] for p in target.partitions)
        if full != reconstructed:
            raise FullBackupError("full-flash image does not reconstruct from its MTD set")
        fulls[copy] = full
    if any(
        copies["a"][partition.mtd] != copies["b"][partition.mtd]
        for partition in target.partitions
    ):
        raise FullBackupError("the two partition copies are not byte-identical")
    if fulls["a"] != fulls["b"]:
        raise FullBackupError("the two recovery images are not byte-identical")
    if schema == 2 and manifest.get("device_binding_sha256") != _device_binding(copies["a"]):
        raise FullBackupError("private recovery device binding differs")
    return CompleteBackupDecision(True, True, len(target.partitions), 2)
