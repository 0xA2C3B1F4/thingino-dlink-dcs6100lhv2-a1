"""Bounded static-TLS component archive, distinct from the legacy RAM bundle."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import zlib
import struct
import tarfile
from pathlib import Path, PurePosixPath

from .runtime_candidate import _validate_mips_elf

MAX_ARCHIVE = 8 * 1024 * 1024
MAX_PAYLOAD = 16 * 1024 * 1024
MEMBERS = frozenset(
    {
        "component.json",
        "raptor-lock.json",
        "etc/raptor.conf",
        "usr/bin/rwd",
        "usr/lib/librss_common.so",
        "usr/lib/librss_ipc.so",
    }
)
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
        # Both are already pinned members of installer.media_closure.
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
    """Require source component dependencies in the completed overlay image."""
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


def audit_elf(raw: bytes, label: str) -> dict[str, object]:
    if label.endswith("rwd"):
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
        loads = [h for h in headers if h[0] == 1]
        if not loads or any(h[7] != 4096 or h[1] + h[4] > len(raw) for h in loads):
            raise ValueError("component LOAD alignment must be 4 KiB")
        dynamic = [h for h in headers if h[0] == 2]
        if len(dynamic) != 1 or dynamic[0][4] > 65536:
            raise ValueError("invalid component dynamic table")
        h = dynamic[0]
        entries = [
            struct.unpack_from("<II", raw, i) for i in range(h[1], h[1] + h[4], 8)
        ]
        if any(tag in {15, 29} for tag, _ in entries):
            raise ValueError("component has RPATH/RUNPATH")
        addresses = [value for tag, value in entries if tag == 5]
        if len(addresses) != 1:
            raise ValueError("component lacks dynamic string table")
        address = addresses[0]
        spans = [h for h in loads if h[2] <= address < h[2] + h[4]]
        if len(spans) != 1:
            raise ValueError("component string table is outside LOAD")
        h = spans[0]
        start = h[1] + address - h[2]
        needed = []
        for tag, value in entries:
            if tag == 1:
                end = raw.index(
                    b"\0", start + value, min(len(raw), start + value + 256)
                )
                needed.append(raw[start + value : end].decode("ascii"))
        if not needed or set(needed) - LIBRARIES:
            raise ValueError("component has an unapproved dynamic dependency")
        return {"needed": sorted(needed), "load_alignment": 4096, "rpath": False}
    except (struct.error, IndexError, UnicodeError) as exc:
        raise ValueError("invalid component ELF structure") from exc


def _validate_component(
    artifact: Path, *, expected_sha256: str, root: Path
) -> tuple[bytes, dict[str, str]]:
    from .local_build_acquire import _regular_snapshot

    with _regular_snapshot(artifact, "Raptor component", limit=MAX_ARCHIVE) as (
        handle,
        _,
    ):
        compressed = handle.read()
    if hashlib.sha256(compressed).hexdigest() != expected_sha256:
        raise ValueError("component archive digest mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(MAX_PAYLOAD + 1)
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("component exceeds unpacked size limit")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            if (
                member.name not in MEMBERS
                or member.name in files
                or not member.isfile()
            ):
                raise ValueError("component member contract mismatch")
            if member.mode != (0o755 if member.name.startswith("usr/") else 0o644):
                raise ValueError("component member mode mismatch")
            files[member.name] = archive.extractfile(member).read()
    if set(files) != MEMBERS:
        raise ValueError("component is incomplete")
    for name, path in (
        ("raptor-lock.json", "components/raptor-rwd/raptor-lock.json"),
        ("etc/raptor.conf", "components/raptor-rwd/raptor.conf"),
    ):
        if files[name] != (root / path).read_bytes():
            raise ValueError("component checkout binding changed")
    manifest = json.loads(files["component.json"])
    if not isinstance(manifest, dict):
        raise ValueError("component manifest must be an object")
    hashes = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in files.items()
        if name != "component.json"
    }
    from .raptor_source import recipe_identity

    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "raptor-rwd-static-component"
        or manifest.get("files") != hashes
        or manifest.get("recipe") != recipe_identity(root)
    ):
        raise ValueError("component provenance mismatch")
    if not isinstance(manifest.get("builder_image_id"), str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", manifest["builder_image_id"]
    ):
        raise ValueError("component builder identity missing")
    source_lock = json.loads((root / "sources.lock.json").read_bytes())
    toolchain = source_lock["sources"]["thingino_build_toolchain_aarch64"]["sha256"]
    if manifest.get("toolchain_sha256") != toolchain:
        raise ValueError("component toolchain identity changed")
    for name in files:
        if name.startswith("usr/"):
            audit_elf(files[name], name)
    return raw, hashes


def validate_component(
    artifact: Path, *, expected_sha256: str, root: Path
) -> tuple[bytes, dict[str, str]]:
    """Return normalized component bytes or a structured validation error."""
    try:
        return _validate_component(artifact, expected_sha256=expected_sha256, root=root)
    except (
        EOFError,
        gzip.BadGzipFile,
        zlib.error,
        tarfile.TarError,
        UnicodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise ValueError("invalid Raptor component encoding or structure") from exc


def validate_persistent_artifact(
    artifact: Path, *, expected_sha256: str, supervisor: Path
) -> tuple[bytes, dict[str, str]]:
    """Dispatch by archive namespace. A malformed component never falls back."""
    from .local_build_acquire import _regular_snapshot
    from scripts.raptor_rwd_runtime import validate_artifact

    try:
        with _regular_snapshot(artifact, "Raptor artifact", limit=MAX_ARCHIVE) as (
            handle,
            _,
        ):
            compressed = handle.read()
        if hashlib.sha256(compressed).hexdigest() != expected_sha256:
            raise ValueError("Raptor artifact digest mismatch")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError("Raptor artifact exceeds unpacked size limit")
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            first = archive.next()
            if first is None:
                raise ValueError("empty Raptor artifact")
            legacy = first.name == "raptor-rwd" or first.name.startswith("raptor-rwd/")
        if legacy:
            return validate_artifact(
                artifact,
                expected_sha256=expected_sha256,
                supervisor=supervisor,
            )
        return validate_component(
            artifact,
            expected_sha256=expected_sha256,
            root=supervisor.parents[2],
        )
    except (
        EOFError,
        gzip.BadGzipFile,
        zlib.error,
        tarfile.TarError,
        UnicodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise ValueError("invalid Raptor component encoding or structure") from exc
