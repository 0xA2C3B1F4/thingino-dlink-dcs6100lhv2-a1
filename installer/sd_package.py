"""Strict parser and generator for the stock U-Boot SD record container.

The production U-Boot parser is intentionally treated only as a transport.
Every condition that it omits is enforced here before a package is emitted or
accepted.  This module performs no disk, camera, or MTD operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .layout import TARGET, Partition


HEADER_SIZE = 0x100
RECORD_HEADER_SIZE = 0x20
RECORD_TAIL_SIZE = 0x04
RECORD_OVERHEAD = RECORD_HEADER_SIZE + RECORD_TAIL_SIZE
END_MARKER = b"FEOF"
PACKER_MAGIC = b"\xff\xff\xff\xff"
SD_FILENAME_PREFIX = "DCS6100LHV2Ax_FW"
SD_FILENAME_SUFFIX = "SD.bin"
SD_LOAD_ADDRESS = 0x82000000

# The largest accepted source package is the stock packer's exact boot,
# kernel, and rootfs coverage plus framing.  The public bootstrap/recovery
# policies are smaller because they categorically exclude mtd0.
MAX_SOURCE_PACKAGE_SIZE = (
    HEADER_SIZE
    + 3 * RECORD_OVERHEAD
    + TARGET.partition(0).size
    + TARGET.partition(1).size
    + TARGET.partition(2).size
    + len(END_MARKER)
)
MAX_BOOTSTRAP_PACKAGE_SIZE = (
    HEADER_SIZE
    + 2 * RECORD_OVERHEAD
    + TARGET.partition(1).size
    + TARGET.partition(2).size
    + len(END_MARKER)
)

_PROJECT_HEADER_PREFIX = (
    b"THINGINO-DCS6100LHV2-A1\x00"
    b"PUBLIC-SD-CONTAINER\x00SCHEMA=1\n"
)
PROJECT_HEADER = _PROJECT_HEADER_PREFIX.ljust(HEADER_SIZE, b"\x00")


class PackageError(ValueError):
    """The package violates the public fail-closed format policy."""


@dataclass(frozen=True, slots=True)
class Record:
    magic: bytes
    reserved: bytes
    erase_span: int
    flash_offset: int
    payload: bytes
    checksum: int
    package_offset: int

    @property
    def payload_length(self) -> int:
        return len(self.payload)

    @property
    def flash_end(self) -> int:
        return self.flash_offset + self.erase_span


@dataclass(frozen=True, slots=True)
class Package:
    header: bytes
    records: tuple[Record, ...]
    raw: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def is_matching_update_filename(name: str) -> bool:
    """Reproduce the stock case-sensitive substring selector."""

    return SD_FILENAME_PREFIX in name and SD_FILENAME_SUFFIX in name


def matching_update_filenames(names: Iterable[str]) -> list[str]:
    return sorted(name for name in names if is_matching_update_filename(name))


def selected_update_filename(names: Iterable[str]) -> str | None:
    matches = matching_update_filenames(names)
    return matches[-1] if matches else None


def additive_complement(payload: bytes) -> int:
    return (-sum(payload)) & 0xFFFFFFFF


def _u32(value: int, label: str) -> int:
    if not 0 <= value <= 0xFFFFFFFF:
        raise PackageError(f"{label} does not fit in 32 bits")
    return value


def _checked_end(start: int, length: int, limit: int, label: str) -> int:
    if start < 0 or length < 0:
        raise PackageError(f"negative {label}")
    end = start + length
    if end < start or end > limit:
        raise PackageError(f"{label} extends beyond its enclosing range")
    return end


def parse_package(
    raw: bytes,
    *,
    max_size: int = MAX_SOURCE_PACKAGE_SIZE,
    require_project_header: bool = False,
) -> Package:
    """Parse one in-memory byte snapshot and validate all framing/checksums."""

    if not isinstance(raw, bytes):
        raise TypeError("package input must be an immutable bytes snapshot")
    if len(raw) < HEADER_SIZE + len(END_MARKER):
        raise PackageError("package is shorter than 0x104 bytes")
    if len(raw) > max_size:
        raise PackageError(
            f"package exceeds the safe whole-file load cap ({max_size} bytes)"
        )

    header = raw[:HEADER_SIZE]
    if require_project_header and header != PROJECT_HEADER:
        raise PackageError("package does not use the deterministic public header")

    cursor = HEADER_SIZE
    records: list[Record] = []
    while True:
        remaining = len(raw) - cursor
        if remaining < len(END_MARKER):
            raise PackageError("missing FEOF end marker")
        if raw[cursor : cursor + 4] == END_MARKER:
            if cursor + 4 != len(raw):
                raise PackageError("trailing bytes after FEOF")
            break
        if remaining < RECORD_HEADER_SIZE:
            raise PackageError("incomplete record header")

        magic = raw[cursor : cursor + 4]
        reserved = raw[cursor + 4 : cursor + 12]
        erase_span = struct.unpack_from(">I", raw, cursor + 12)[0]
        flash_offset_64 = struct.unpack_from(">Q", raw, cursor + 16)[0]
        payload_length_64 = struct.unpack_from(">Q", raw, cursor + 24)[0]
        if flash_offset_64 > 0xFFFFFFFF:
            raise PackageError("flash offset has non-zero ignored high bits")
        if payload_length_64 > 0xFFFFFFFF:
            raise PackageError("payload length has non-zero ignored high bits")
        flash_offset = int(flash_offset_64)
        payload_length = int(payload_length_64)
        if payload_length == 0:
            raise PackageError("zero-length records are rejected")

        payload_start = cursor + RECORD_HEADER_SIZE
        payload_end = _checked_end(
            payload_start, payload_length, len(raw), "record payload"
        )
        checksum_end = _checked_end(
            payload_end, RECORD_TAIL_SIZE, len(raw), "record checksum"
        )
        payload = raw[payload_start:payload_end]
        checksum = struct.unpack_from(">I", raw, payload_end)[0]
        expected = additive_complement(payload)
        if checksum != expected:
            raise PackageError(
                f"record checksum mismatch at 0x{cursor:x}: "
                f"expected 0x{expected:08x}, got 0x{checksum:08x}"
            )

        records.append(
            Record(
                magic=magic,
                reserved=reserved,
                erase_span=erase_span,
                flash_offset=flash_offset,
                payload=payload,
                checksum=checksum,
                package_offset=cursor,
            )
        )
        cursor = checksum_end

    return Package(header=header, records=tuple(records), raw=raw)


def _validate_record_bounds(record: Record) -> None:
    if record.magic != PACKER_MAGIC:
        raise PackageError("record magic must be exactly 0xffffffff")
    if record.reserved != b"\x00" * 8:
        raise PackageError("record reserved bytes must be zero")
    if record.payload_length % 4:
        raise PackageError("record payload length must be four-byte aligned")
    if record.erase_span == 0 or record.erase_span % TARGET.erase_block_size:
        raise PackageError("record erase span is not erase-block aligned")
    if record.flash_offset % TARGET.erase_block_size:
        raise PackageError("record flash offset is not erase-block aligned")
    if record.payload_length > record.erase_span:
        raise PackageError("record payload is larger than its erase span")
    if record.flash_end > TARGET.nor_size:
        raise PackageError("record erase range extends beyond the 16 MiB NOR")


def _reject_overlaps(records: Sequence[Record]) -> None:
    ordered = sorted(records, key=lambda record: record.flash_offset)
    for previous, current in zip(ordered, ordered[1:]):
        if current.flash_offset < previous.flash_end:
            raise PackageError("duplicate or overlapping record erase ranges")


def _validate_exact_partitions(
    package: Package,
    expected: Sequence[Partition],
    *,
    require_project_header: bool,
) -> None:
    if require_project_header and package.header != PROJECT_HEADER:
        raise PackageError("package does not use the deterministic public header")
    if len(package.records) != len(expected):
        raise PackageError("package does not contain the exact allowed record count")
    _reject_overlaps(package.records)
    for index, (record, partition) in enumerate(zip(package.records, expected)):
        _validate_record_bounds(record)
        if record.flash_offset != partition.offset:
            raise PackageError(
                f"record {index} has wrong offset for mtd{partition.mtd}"
            )
        if record.erase_span != partition.size:
            raise PackageError(
                f"record {index} has wrong erase span for mtd{partition.mtd}"
            )


def validate_bootstrap(package: Package) -> None:
    if len(package.raw) > MAX_BOOTSTRAP_PACKAGE_SIZE:
        raise PackageError("bootstrap exceeds its exact whole-file load cap")
    boot = TARGET.partition(0)
    for record in package.records:
        if record.flash_offset < boot.end and record.flash_end > boot.offset:
            raise PackageError("bootstrap record overlaps protected mtd0")
    _validate_exact_partitions(
        package,
        (TARGET.partition(1), TARGET.partition(2)),
        require_project_header=True,
    )


def validate_stock_source(package: Package) -> None:
    """Validate a stock-style source package for offline extraction only.

    Passing this policy never marks the input safe to present to a camera: the
    record set includes mtd0 by definition.
    """

    _validate_exact_partitions(
        package,
        (TARGET.partition(0), TARGET.partition(1), TARGET.partition(2)),
        require_project_header=False,
    )


def _pad_payload(payload: bytes) -> bytes:
    padding = (-len(payload)) % 4
    return payload + b"\xff" * padding


def encode_record(partition: Partition, payload: bytes) -> bytes:
    padded = _pad_payload(payload)
    if not padded:
        raise PackageError(f"mtd{partition.mtd} payload is empty")
    if len(padded) > partition.size:
        raise PackageError(
            f"mtd{partition.mtd} payload is {len(padded)} bytes; "
            f"limit is {partition.size}"
        )
    header = b"".join(
        (
            PACKER_MAGIC,
            b"\x00" * 8,
            struct.pack(">I", _u32(partition.size, "erase span")),
            struct.pack(">Q", _u32(partition.offset, "flash offset")),
            struct.pack(">Q", _u32(len(padded), "payload length")),
        )
    )
    if len(header) != RECORD_HEADER_SIZE:
        raise AssertionError("internal record-header length error")
    return header + padded + struct.pack(">I", additive_complement(padded))


def generate_bootstrap(kernel: bytes, rootfs: bytes) -> bytes:
    raw = b"".join(
        (
            PROJECT_HEADER,
            encode_record(TARGET.partition(1), kernel),
            encode_record(TARGET.partition(2), rootfs),
            END_MARKER,
        )
    )
    package = parse_package(
        raw,
        max_size=MAX_BOOTSTRAP_PACKAGE_SIZE,
        require_project_header=True,
    )
    validate_bootstrap(package)
    return raw


def read_snapshot(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PackageError(f"input is not a regular file: {path}")
    return path.read_bytes()


def atomic_write(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def package_manifest(package: Package, *, purpose: str) -> bytes:
    records = []
    for record in package.records:
        records.append(
            {
                "checksum": f"{record.checksum:08x}",
                "erase_span": record.erase_span,
                "flash_offset": record.flash_offset,
                "payload_length": record.payload_length,
                "payload_sha256": hashlib.sha256(record.payload).hexdigest(),
            }
        )
    document = {
        "schema_version": 1,
        "purpose": purpose,
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
            "nor_size": TARGET.nor_size,
        },
        "package": {
            "load_address": SD_LOAD_ADDRESS,
            "load_end": SD_LOAD_ADDRESS + len(package.raw),
            "sha256": package.sha256,
            "size": len(package.raw),
        },
        "records": records,
        "writes_mtd0": False,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
