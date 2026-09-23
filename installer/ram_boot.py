"""Deterministic legacy-uImage wrapper for a validated SquashFS RAM disk."""

from __future__ import annotations

import binascii
import struct

from .artifacts import ArtifactError, validate_squashfs


UIMAGE_HEADER = struct.Struct(">7I4B32s")
UIMAGE_MAGIC = 0x27051956
IH_OS_LINUX = 5
IH_ARCH_MIPS = 5
IH_TYPE_RAMDISK = 3
IH_COMP_NONE = 0
MAX_RAMDISK_SIZE = 8 * 1024 * 1024


class RamBootError(ValueError):
    """A RAM-disk wrapper violates the fixed boot contract."""


def build_ramdisk_uimage(
    rootfs: bytes, *, name: str = "DCS6100 stage1 RAM boot"
) -> bytes:
    try:
        validate_squashfs(rootfs, partition_limit=MAX_RAMDISK_SIZE)
    except ArtifactError as exc:
        raise RamBootError(str(exc)) from exc
    if len(rootfs) % 4096:
        raise RamBootError("RAM-disk SquashFS size is not page aligned")
    try:
        encoded_name = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RamBootError("RAM-disk uImage name must be ASCII") from exc
    if not encoded_name or len(encoded_name) > 32:
        raise RamBootError("RAM-disk uImage name must contain 1..32 bytes")
    name_field = encoded_name.ljust(32, b"\0")
    data_crc = binascii.crc32(rootfs) & 0xFFFFFFFF
    header = UIMAGE_HEADER.pack(
        UIMAGE_MAGIC,
        0,
        0,
        len(rootfs),
        0,
        0,
        data_crc,
        IH_OS_LINUX,
        IH_ARCH_MIPS,
        IH_TYPE_RAMDISK,
        IH_COMP_NONE,
        name_field,
    )
    header_crc = binascii.crc32(header) & 0xFFFFFFFF
    return UIMAGE_HEADER.pack(
        UIMAGE_MAGIC,
        header_crc,
        0,
        len(rootfs),
        0,
        0,
        data_crc,
        IH_OS_LINUX,
        IH_ARCH_MIPS,
        IH_TYPE_RAMDISK,
        IH_COMP_NONE,
        name_field,
    ) + rootfs


def parse_ramdisk_uimage(wrapper: bytes) -> bytes:
    if len(wrapper) < UIMAGE_HEADER.size:
        raise RamBootError("RAM-disk uImage is truncated")
    fields = UIMAGE_HEADER.unpack_from(wrapper)
    (
        magic,
        header_crc,
        timestamp,
        size,
        load_address,
        entry_point,
        data_crc,
        operating_system,
        architecture,
        image_type,
        compression,
        _name,
    ) = fields
    header = bytearray(wrapper[: UIMAGE_HEADER.size])
    header[4:8] = b"\0\0\0\0"
    payload = wrapper[UIMAGE_HEADER.size :]
    if (
        magic != UIMAGE_MAGIC
        or header_crc != binascii.crc32(header) & 0xFFFFFFFF
        or timestamp != 0
        or size != len(payload)
        or load_address != 0
        or entry_point != 0
        or data_crc != binascii.crc32(payload) & 0xFFFFFFFF
        or (operating_system, architecture, image_type, compression)
        != (IH_OS_LINUX, IH_ARCH_MIPS, IH_TYPE_RAMDISK, IH_COMP_NONE)
    ):
        raise RamBootError("RAM-disk uImage metadata or CRC mismatch")
    try:
        validate_squashfs(payload, partition_limit=MAX_RAMDISK_SIZE)
    except ArtifactError as exc:
        raise RamBootError(str(exc)) from exc
    if len(payload) % 4096:
        raise RamBootError("RAM-disk SquashFS size is not page aligned")
    return payload
