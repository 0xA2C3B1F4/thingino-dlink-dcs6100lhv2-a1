"""Shared ELF and image-root helpers for source-built Raptor components."""

from __future__ import annotations

import os
import struct
from pathlib import Path, PurePosixPath

from .runtime_candidate import _validate_mips_elf


# Keep this allowlist limited to dependencies that are part of the public
# source-built Raptor image.  Full-media-specific vendor libraries are added
# by raptor_full_component for the two sensor-facing owners that need them.
LIBRARIES = frozenset(
    {
        "librss_common.so",
        "librss_ipc.so",
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
        "librt.so.1",
        "libgcc_s.so.1",
        "libdl.so.2",
        "ld.so.1",
        "libatomic.so.1",
    }
)


def _root_file(root: Path, relative: str) -> Path:
    """Resolve target symlinks inside the image, never against the host root."""

    pending = list(PurePosixPath(relative).parts)
    resolved: list[str] = []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise ValueError("component dependency escapes image root")
            resolved.pop()
            continue
        path = root.joinpath(*resolved, part)
        if path.is_symlink():
            links += 1
            if links > 40:
                raise ValueError("component dependency symlink loop")
            target = PurePosixPath(os.readlink(path))
            if target.is_absolute():
                resolved = []
                pending = list(target.parts[1:]) + pending
            else:
                pending = list(target.parts) + pending
        else:
            resolved.append(part)
    path = root.joinpath(*resolved)
    if not path.is_file():
        raise ValueError("component runtime dependency missing: " + relative)
    return path


def validate_runtime_dependencies(root: Path) -> None:
    """Require the source component dependencies in a completed image."""

    required = set()
    for relative in (
        "usr/bin/rwd",
        "usr/lib/librss_common.so",
        "usr/lib/librss_ipc.so",
    ):
        required.update(
            audit_elf(_root_file(root, relative).read_bytes(), relative)["needed"]
        )
    _root_file(root, "lib/ld.so.1")
    for name in sorted(required):
        for directory in ("usr/lib/raptor", "usr/lib", "lib"):
            try:
                _root_file(root, directory + "/" + name)
                break
            except ValueError:
                continue
        else:
            raise ValueError("component runtime dependency missing: " + name)


def audit_elf(
    raw: bytes, label: str, *, libraries: frozenset[str] = LIBRARIES
) -> dict[str, object]:
    """Validate one Raptor MIPS ELF and return its approved dependencies."""

    if label.endswith("rwd") or label.startswith("usr/bin/"):
        _validate_mips_elf(raw, label)
    try:
        if len(raw) < 52 or raw[:6] != b"\x7fELF\x01\x01":
            raise ValueError("component requires ELF32 little endian")
        flags = struct.unpack_from("<I", raw, 36)[0]
        if (
            struct.unpack_from("<HH", raw, 16) != (3, 8)
            or flags & 0xF0000000 != 0x70000000
            or flags & 0x0000F000 != 0x00001000
        ):
            raise ValueError("component requires MIPS32r2 O32 ELF")
        offset = struct.unpack_from("<I", raw, 28)[0]
        size, count = struct.unpack_from("<HH", raw, 42)
        if size != 32 or not 1 <= count <= 64:
            raise ValueError("invalid component program headers")
        headers = [
            struct.unpack_from("<8I", raw, offset + size * i) for i in range(count)
        ]
        loads = [header for header in headers if header[0] == 1]
        if not loads or any(
            header[7] != 4096 or header[1] + header[4] > len(raw)
            for header in loads
        ):
            raise ValueError("component LOAD alignment must be 4 KiB")
        dynamic = [header for header in headers if header[0] == 2]
        if len(dynamic) != 1 or dynamic[0][4] > 65536:
            raise ValueError("invalid component dynamic table")
        dynamic_header = dynamic[0]
        entries = [
            struct.unpack_from("<II", raw, index)
            for index in range(
                dynamic_header[1], dynamic_header[1] + dynamic_header[4], 8
            )
        ]
        if any(tag in {15, 29} for tag, _ in entries):
            raise ValueError("component has RPATH/RUNPATH")
        addresses = [value for tag, value in entries if tag == 5]
        if len(addresses) != 1:
            raise ValueError("component lacks dynamic string table")
        address = addresses[0]
        spans = [
            header
            for header in loads
            if header[2] <= address < header[2] + header[4]
        ]
        if len(spans) != 1:
            raise ValueError("component string table is outside LOAD")
        load = spans[0]
        start = load[1] + address - load[2]
        needed = []
        for tag, value in entries:
            if tag == 1:
                end = raw.index(
                    b"\0", start + value, min(len(raw), start + value + 256)
                )
                needed.append(raw[start + value : end].decode("ascii"))
        if not needed or set(needed) - libraries:
            raise ValueError("component has an unapproved dynamic dependency")
        return {"needed": sorted(needed), "load_alignment": 4096, "rpath": False}
    except (struct.error, IndexError, UnicodeError) as exc:
        raise ValueError("invalid component ELF structure") from exc
