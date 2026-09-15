"""Fixed offline stage-2 payload consumed by the permanent mtd2 bootstrap."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .artifacts import validate_squashfs, validate_uimage_command_line
from .mtd3_split import derive_final_layout, final_kernel_command_line
from .layout import ERASE_BLOCK_SIZE, TARGET, align_up


MAGIC = b"THINGINO-STAGE2\0"
HEADER_SIZE = 0x1000
FILENAME = "THINGINO2.BIN"
_FORMAT = ">16sII32s8sQQQ32sQQ32sIIIII32s"
_STRUCT_SIZE = struct.calcsize(_FORMAT)
_LEGACY_V1_FORMAT = ">16sII32s8sQQQ32sQQ32sIIII32s"
_LEGACY_V1_STRUCT_SIZE = struct.calcsize(_LEGACY_V1_FORMAT)
DATA_MODES = {"initialize": 0, "preserve": 1, "factory-reset": 2}
DATA_MODES_BY_ID = {value: key for key, value in DATA_MODES.items()}


class Stage2Error(ValueError):
    """The offline final-install payload violates its fixed contract."""


@dataclass(frozen=True, slots=True)
class Stage2Payload:
    raw: bytes
    kernel: bytes
    system: bytes
    kernel_offset: int
    system_offset: int
    system_flash_offset: int
    system_flash_span: int
    data_flash_offset: int
    data_flash_span: int
    data_mode: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyStage2V1Payload:
    """Strictly validated predecessor payload accepted only for migration."""

    raw: bytes
    kernel: bytes
    system: bytes
    kernel_offset: int
    system_offset: int
    system_flash_offset: int
    system_flash_span: int
    data_flash_offset: int
    data_flash_span: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def _header_digest(header: bytes) -> bytes:
    mutable = bytearray(header)
    digest_offset = _STRUCT_SIZE - 32
    mutable[digest_offset : digest_offset + 32] = b"\0" * 32
    return hashlib.sha256(mutable).digest()


def _legacy_v1_header_digest(header: bytes) -> bytes:
    mutable = bytearray(header)
    digest_offset = _LEGACY_V1_STRUCT_SIZE - 32
    mutable[digest_offset : digest_offset + 32] = b"\0" * 32
    return hashlib.sha256(mutable).digest()


def _legacy_v1_layout(system_size: int) -> tuple[int, int, int, int]:
    """Reproduce the immutable schema-1 derived mtd3 layout contract."""

    mtd3 = TARGET.partition(3)
    system_span = align_up(system_size)
    data_offset = mtd3.offset + system_span
    data_span = mtd3.end - data_offset
    if system_size <= 0 or system_span > mtd3.size:
        raise Stage2Error("legacy stage-2 system does not fit in physical mtd3")
    if data_span < 4 * ERASE_BLOCK_SIZE:
        raise Stage2Error("legacy stage-2 data region is smaller than 256 KiB")
    return mtd3.offset, system_span, data_offset, data_span


def _legacy_v1_kernel_command_line(
    system_span: int, data_span: int
) -> str:
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=39M@0x0",
            "rmem=25M@0x2700000",
            "init=/sbin/init",
            "root=/dev/mtdblock2",
            "rootfstype=squashfs",
            "ro",
            "panic=10",
            "mtdparts=jz_sfc:256k(boot)ro,1792k(kernel)ro,"
            "4608k(bootstrap)ro,"
            f"{system_span // 1024}k(system)ro,"
            f"{data_span // 1024}k(data),"
            "1536k(vendor)ro,256k(factory)ro",
        )
    )


def validate_legacy_stage2_v1(raw: bytes) -> LegacyStage2V1Payload:
    """Validate the retired schema-1 format without making it installable."""

    if not isinstance(raw, bytes):
        raise TypeError("legacy stage-2 input must be an immutable bytes snapshot")
    if len(raw) < HEADER_SIZE:
        raise Stage2Error("legacy stage-2 payload is shorter than its fixed header")
    fields = struct.unpack(_LEGACY_V1_FORMAT, raw[:_LEGACY_V1_STRUCT_SIZE])
    (
        magic,
        schema,
        header_size,
        model,
        revision,
        file_size,
        kernel_offset,
        kernel_size,
        kernel_digest,
        system_offset,
        system_size,
        system_digest,
        system_flash_offset,
        system_flash_span,
        data_flash_offset,
        data_flash_span,
        header_digest,
    ) = fields
    if magic != MAGIC or schema != 1 or header_size != HEADER_SIZE:
        raise Stage2Error("legacy stage-2 header identity changed")
    if model.rstrip(b"\0") != TARGET.model.encode() or revision.rstrip(
        b"\0"
    ) != TARGET.hardware_revision.encode():
        raise Stage2Error("legacy stage-2 payload targets the wrong camera")
    if file_size != len(raw) or kernel_offset != HEADER_SIZE:
        raise Stage2Error("legacy stage-2 file size or first payload offset changed")
    if system_offset != kernel_offset + kernel_size:
        raise Stage2Error("legacy stage-2 payloads are not contiguous")
    if system_offset + system_size != len(raw):
        raise Stage2Error("legacy stage-2 payload has truncation or trailing bytes")
    if any(raw[_LEGACY_V1_STRUCT_SIZE:HEADER_SIZE]):
        raise Stage2Error("legacy stage-2 reserved header bytes are not zero")
    if _legacy_v1_header_digest(raw[:HEADER_SIZE]) != header_digest:
        raise Stage2Error("legacy stage-2 header digest mismatch")
    kernel = raw[kernel_offset:system_offset]
    system = raw[system_offset:]
    if hashlib.sha256(kernel).digest() != kernel_digest:
        raise Stage2Error("legacy stage-2 kernel digest mismatch")
    if hashlib.sha256(system).digest() != system_digest:
        raise Stage2Error("legacy stage-2 system digest mismatch")
    expected_layout = _legacy_v1_layout(len(system))
    if (
        system_flash_offset,
        system_flash_span,
        data_flash_offset,
        data_flash_span,
    ) != expected_layout:
        raise Stage2Error("legacy stage-2 physical layout does not match its system image")
    validate_uimage_command_line(
        kernel,
        _legacy_v1_kernel_command_line(system_flash_span, data_flash_span),
        partition_limit=TARGET.partition(1).size,
    )
    validate_squashfs(system)
    return LegacyStage2V1Payload(
        raw=raw,
        kernel=kernel,
        system=system,
        kernel_offset=kernel_offset,
        system_offset=system_offset,
        system_flash_offset=system_flash_offset,
        system_flash_span=system_flash_span,
        data_flash_offset=data_flash_offset,
        data_flash_span=data_flash_span,
    )


def build_stage2(
    *, final_kernel: bytes, system_rootfs: bytes, data_mode: str = "initialize",
    development_profile: str | None = None,
) -> bytes:
    if data_mode not in DATA_MODES:
        raise Stage2Error("stage-2 data mode is invalid")
    layout = derive_final_layout(len(system_rootfs))
    command_line = final_kernel_command_line(layout)
    if development_profile is not None:
        from .raptor_development import validate_candidate

        command_line = validate_candidate(profile=development_profile,
            kernel=final_kernel, system=system_rootfs, data_mode=data_mode)
    validate_uimage_command_line(
        final_kernel,
        command_line,
        partition_limit=TARGET.partition(1).size,
        # The compressed-kernel entry is link-layout dependent. The split
        # release binds the complete uImage by SHA-256 and independently
        # requires the entry to fall inside the validated expanded payload.
        expected_entry=None,
    )
    validate_squashfs(system_rootfs)
    kernel_offset = HEADER_SIZE
    system_offset = kernel_offset + len(final_kernel)
    file_size = system_offset + len(system_rootfs)
    header = struct.pack(
        _FORMAT,
        MAGIC,
        2,
        HEADER_SIZE,
        TARGET.model.encode("ascii").ljust(32, b"\0"),
        TARGET.hardware_revision.encode("ascii").ljust(8, b"\0"),
        file_size,
        kernel_offset,
        len(final_kernel),
        hashlib.sha256(final_kernel).digest(),
        system_offset,
        len(system_rootfs),
        hashlib.sha256(system_rootfs).digest(),
        layout.system_offset,
        layout.system_span,
        layout.data_offset,
        layout.data_span,
        DATA_MODES[data_mode],
        b"\0" * 32,
    ).ljust(HEADER_SIZE, b"\0")
    digest = _header_digest(header)
    digest_offset = _STRUCT_SIZE - 32
    header = header[:digest_offset] + digest + header[digest_offset + 32 :]
    raw = header + final_kernel + system_rootfs
    validate_stage2_for_profile(raw, development_profile)
    return raw


def _retired_v2_kernel_command_line(layout: object) -> str:
    """Keep the old 39/25 layout readable for passive SD-file replacement."""

    current = final_kernel_command_line(layout)
    current_memory = "mem=42M@0x0 rmem=22M@0x2a00000"
    if current.count(current_memory) != 1:
        raise AssertionError("current final memory split identity changed")
    return current.replace(
        current_memory,
        "mem=39M@0x0 rmem=25M@0x2700000",
    )


def _validate_stage2(
    raw: bytes,
    *,
    accept_retired_v2_memory_split: bool,
    accept_retired_ipv6_disabled: bool = False,
) -> Stage2Payload:
    if not isinstance(raw, bytes):
        raise TypeError("stage-2 input must be an immutable bytes snapshot")
    if len(raw) < HEADER_SIZE:
        raise Stage2Error("stage-2 payload is shorter than its fixed header")
    fields = struct.unpack(_FORMAT, raw[:_STRUCT_SIZE])
    (
        magic,
        schema,
        header_size,
        model,
        revision,
        file_size,
        kernel_offset,
        kernel_size,
        kernel_digest,
        system_offset,
        system_size,
        system_digest,
        system_flash_offset,
        system_flash_span,
        data_flash_offset,
        data_flash_span,
        data_mode_id,
        header_digest,
    ) = fields
    if magic != MAGIC or schema != 2 or header_size != HEADER_SIZE:
        raise Stage2Error("stage-2 header identity changed")
    if data_mode_id not in DATA_MODES_BY_ID:
        raise Stage2Error("stage-2 data mode is invalid")
    if model.rstrip(b"\0") != TARGET.model.encode() or revision.rstrip(
        b"\0"
    ) != TARGET.hardware_revision.encode():
        raise Stage2Error("stage-2 payload targets the wrong camera")
    if file_size != len(raw) or kernel_offset != HEADER_SIZE:
        raise Stage2Error("stage-2 file size or first payload offset changed")
    if system_offset != kernel_offset + kernel_size:
        raise Stage2Error("stage-2 payloads are not contiguous")
    if system_offset + system_size != len(raw):
        raise Stage2Error("stage-2 payload has truncation or trailing bytes")
    if any(raw[_STRUCT_SIZE:HEADER_SIZE]):
        raise Stage2Error("stage-2 reserved header bytes are not zero")
    if _header_digest(raw[:HEADER_SIZE]) != header_digest:
        raise Stage2Error("stage-2 header digest mismatch")
    kernel = raw[kernel_offset:system_offset]
    system = raw[system_offset:]
    if hashlib.sha256(kernel).digest() != kernel_digest:
        raise Stage2Error("stage-2 kernel digest mismatch")
    if hashlib.sha256(system).digest() != system_digest:
        raise Stage2Error("stage-2 system digest mismatch")
    layout = derive_final_layout(len(system))
    if (
        system_flash_offset,
        system_flash_span,
        data_flash_offset,
        data_flash_span,
    ) != (
        layout.system_offset,
        layout.system_span,
        layout.data_offset,
        layout.data_span,
    ):
        raise Stage2Error("stage-2 physical layout does not match its system image")
    command_line = (
        _retired_v2_kernel_command_line(layout)
        if accept_retired_v2_memory_split
        else final_kernel_command_line(layout)
    )
    if accept_retired_ipv6_disabled:
        command_line = command_line.replace(
            " init=/sbin/init", " ipv6.disable=1 init=/sbin/init"
        )
    validate_uimage_command_line(
        kernel,
        command_line,
        partition_limit=TARGET.partition(1).size,
        expected_entry=None,
    )
    validate_squashfs(system)
    return Stage2Payload(
        raw=raw,
        kernel=kernel,
        system=system,
        kernel_offset=kernel_offset,
        system_offset=system_offset,
        system_flash_offset=system_flash_offset,
        system_flash_span=system_flash_span,
        data_flash_offset=data_flash_offset,
        data_flash_span=data_flash_span,
        data_mode=DATA_MODES_BY_ID[data_mode_id],
    )


def validate_stage2(raw: bytes) -> Stage2Payload:
    """Validate only the current schema-2 install payload."""

    return _validate_stage2(raw, accept_retired_v2_memory_split=False)


def validate_stage2_for_profile(raw: bytes, profile: str | None) -> Stage2Payload:
    """Keep normal admission unchanged; a named experiment binds exact bytes."""
    if profile is None:
        return validate_stage2(raw)
    from .raptor_development import PROFILES, validate_candidate

    if profile not in PROFILES:
        raise Stage2Error("unknown development media profile")
    parsed = _validate_stage2(raw, accept_retired_v2_memory_split=False,
                             accept_retired_ipv6_disabled=True)
    validate_candidate(profile=profile, kernel=parsed.kernel, system=parsed.system,
                       data_mode=parsed.data_mode)
    return parsed


def validate_retired_stage2_v2_39_25(raw: bytes) -> Stage2Payload:
    """Validate the exact prior schema-2 payload only for passive replacement."""

    return _validate_stage2(raw, accept_retired_v2_memory_split=True)


def validate_retired_stage2_v2_ipv6_disabled(raw: bytes) -> Stage2Payload:
    """Accept the old IPv6-disabled ABI only when replacing a passive pair."""

    try:
        return _validate_stage2(
            raw,
            accept_retired_v2_memory_split=False,
            accept_retired_ipv6_disabled=True,
        )
    except ValueError:
        return _validate_stage2(
            raw,
            accept_retired_v2_memory_split=True,
            accept_retired_ipv6_disabled=True,
        )
