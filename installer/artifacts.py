"""Fail-closed kernel and SquashFS gates for stage-1 package inputs."""

from __future__ import annotations

import binascii
import hashlib
import lzma
import struct
from dataclasses import dataclass

from .layout import TARGET


UIMAGE_MAGIC = 0x27051956
IH_OS_LINUX = 5
IH_ARCH_MIPS = 5
IH_TYPE_KERNEL = 2
IH_COMP_LZMA = 3
EXPECTED_KERNEL_LOAD = 0x80010000
EXPECTED_KERNEL_ENTRY = 0x80399250
SOURCE_DATE_EPOCH = 1_786_006_608
UBOOT_DECOMPRESS_LIMIT = 8 * 1024 * 1024


class ArtifactError(ValueError):
    """An image is not the exact bounded artifact type expected by U-Boot."""


@dataclass(frozen=True, slots=True)
class UImageInfo:
    name: str
    timestamp: int
    payload_size: int
    load_address: int
    entry_point: int
    expanded_size: int
    expanded_sha256: str


def _validate_uimage(
    raw: bytes,
    *,
    partition_limit: int | None = None,
    expected_entry: int | None = EXPECTED_KERNEL_ENTRY,
) -> tuple[UImageInfo, bytes]:
    if len(raw) < 64:
        raise ArtifactError("uImage is shorter than its 64-byte header")
    if partition_limit is not None and len(raw) > partition_limit:
        raise ArtifactError("uImage exceeds its partition size limit")
    fields = struct.unpack(">7I4B32s", raw[:64])
    (
        magic,
        header_crc,
        timestamp,
        payload_size,
        load_address,
        entry_point,
        payload_crc,
        os_id,
        architecture,
        image_type,
        compression,
        name_raw,
    ) = fields
    if magic != UIMAGE_MAGIC:
        raise ArtifactError("unexpected uImage magic")
    header = bytearray(raw[:64])
    struct.pack_into(">I", header, 4, 0)
    if binascii.crc32(header) & 0xFFFFFFFF != header_crc:
        raise ArtifactError("uImage header CRC mismatch")
    if len(raw) != 64 + payload_size:
        raise ArtifactError("uImage payload length mismatch")
    payload = raw[64:]
    if binascii.crc32(payload) & 0xFFFFFFFF != payload_crc:
        raise ArtifactError("uImage payload CRC mismatch")
    if (os_id, architecture, image_type, compression) != (
        IH_OS_LINUX,
        IH_ARCH_MIPS,
        IH_TYPE_KERNEL,
        IH_COMP_LZMA,
    ):
        raise ArtifactError("uImage is not a Linux/MIPS/kernel/LZMA image")
    if load_address != EXPECTED_KERNEL_LOAD:
        raise ArtifactError("unexpected kernel load address")
    if expected_entry is not None and entry_point != expected_entry:
        raise ArtifactError("unexpected kernel entry point")
    if timestamp != SOURCE_DATE_EPOCH:
        raise ArtifactError("unexpected reproducible-build timestamp")
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    try:
        expanded = decompressor.decompress(
            payload, max_length=UBOOT_DECOMPRESS_LIMIT + 1
        )
    except lzma.LZMAError as exc:
        raise ArtifactError(f"invalid kernel LZMA stream: {exc}") from exc
    if not decompressor.eof:
        raise ArtifactError("kernel LZMA stream is incomplete")
    footer = struct.pack("<I", len(expanded))
    if decompressor.unused_data not in (b"", footer):
        raise ArtifactError("kernel LZMA stream has an unexpected trailer")
    if len(expanded) > UBOOT_DECOMPRESS_LIMIT:
        raise ArtifactError("expanded kernel exceeds the stock 8 MiB boot limit")
    if not load_address <= entry_point < load_address + len(expanded):
        raise ArtifactError("kernel entry point is outside expanded data")
    return UImageInfo(
        name=name_raw.split(b"\0", 1)[0].decode("ascii", "replace"),
        timestamp=timestamp,
        payload_size=payload_size,
        load_address=load_address,
        entry_point=entry_point,
        expanded_size=len(expanded),
        expanded_sha256=hashlib.sha256(expanded).hexdigest(),
    ), expanded


def validate_uimage(
    raw: bytes,
    *,
    partition_limit: int | None = None,
    expected_entry: int | None = EXPECTED_KERNEL_ENTRY,
) -> UImageInfo:
    info, _ = _validate_uimage(
        raw,
        partition_limit=partition_limit,
        expected_entry=expected_entry,
    )
    return info


def validate_uimage_command_line(
    raw: bytes,
    expected_command_line: str,
    *,
    partition_limit: int | None = None,
    expected_entry: int | None = EXPECTED_KERNEL_ENTRY,
) -> UImageInfo:
    info, expanded = _validate_uimage(
        raw,
        partition_limit=partition_limit,
        expected_entry=expected_entry,
    )
    marker = expected_command_line.encode("ascii")
    if expanded.count(marker) != 1:
        raise ArtifactError("expanded kernel does not contain the exact final command line once")
    return info


def validate_squashfs(raw: bytes, *, partition_limit: int | None = None) -> int:
    if partition_limit is not None and len(raw) > partition_limit:
        raise ArtifactError("SquashFS exceeds its partition size limit")
    if len(raw) < 48 or raw[:4] != b"hsqs":
        raise ArtifactError("rootfs is not a little-endian SquashFS")
    bytes_used = struct.unpack_from("<Q", raw, 40)[0]
    if bytes_used <= 0 or bytes_used > len(raw):
        raise ArtifactError("invalid SquashFS bytes-used field")
    if any(byte != 0 for byte in raw[bytes_used:]):
        raise ArtifactError("SquashFS has non-zero trailing bytes")
    return bytes_used


def validate_stage1_images(kernel: bytes, rootfs: bytes) -> tuple[UImageInfo, int]:
    kernel_info = validate_uimage(kernel, partition_limit=TARGET.partition(1).size)
    rootfs_used = validate_squashfs(
        rootfs, partition_limit=TARGET.partition(2).size
    )
    return kernel_info, rootfs_used
