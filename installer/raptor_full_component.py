"""Validate source-built Raptor daemons before final-root composition."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import zlib

from .local_build_acquire import _regular_snapshot
from .raptor_component import LIBRARIES, audit_elf
from .raptor_source import recipe_identity, source_lock


BINARIES = ("rvd", "rhd", "rsd", "ric", "rad", "rod", "rmr", "raptorctl", "rwd")
FONT = "usr/share/fonts/default.ttf"
FONT_LICENSE = "usr/share/licenses/ubuntu-font/LICENCE.txt"
LIBSCHRIFT_LICENSE = "usr/share/licenses/libschrift/LICENSE"
MOTION_CLIP = "usr/share/raptor/audio/motion.pcm"
DATA_FILES = frozenset({FONT, FONT_LICENSE, LIBSCHRIFT_LICENSE, MOTION_CLIP})
PAYLOAD = frozenset(
    {f"usr/bin/{name}" for name in BINARIES}
    | {"usr/lib/librss_common.so", "usr/lib/librss_ipc.so"}
    | DATA_FILES
)
MEMBERS = PAYLOAD | {"component.json"}
FULL_LIBRARIES = LIBRARIES | {
    "libimp.so", "libalog.so", "libaudioProcess.so", "libsysutils.so"
}
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_PAYLOAD = 16 * 1024 * 1024
SOURCE_DATE_EPOCH = 1786006608
KIND = "raptor-full-media-component"
FONT_SHA256 = "52c1afa489ae7bfd893af6cdd9f1af258005703600449e70d338caabcff507e5"
FONT_LICENSE_SHA256 = "2f0015108d68627bd788d313f529c21ff4da2c2c42a5e1f3883acc83480f9002"
LIBSCHRIFT_LICENSE_SHA256 = "13c322598cd5f3615a0e8b2b30cca28f8734435ff6214075f318f2bbe45ec4c3"
MOTION_CLIP_SHA256 = "507c4135606518c8158e2dcb28fe76170df1f7e9b9ad9aef4b267787b7f22fad"


def payload_mode(name: str) -> int:
    return 0o644 if name in DATA_FILES or name == "component.json" else 0o755


def audit_payload(files: dict[str, bytes]) -> dict[str, object]:
    if set(files) != PAYLOAD:
        raise ValueError("full Raptor component must contain all media owners and shared libraries")
    if (
        len(files[MOTION_CLIP]) != 8_000
        or hashlib.sha256(files[MOTION_CLIP]).hexdigest() != MOTION_CLIP_SHA256
    ):
        raise ValueError("full Raptor motion clip identity changed")
    if (
        hashlib.sha256(files[FONT]).hexdigest() != FONT_SHA256
        or hashlib.sha256(files[FONT_LICENSE]).hexdigest() != FONT_LICENSE_SHA256
        or hashlib.sha256(files[LIBSCHRIFT_LICENSE]).hexdigest()
        != LIBSCHRIFT_LICENSE_SHA256
        or len(files[FONT]) != 353_824
        or not files[FONT].startswith(b"\x00\x01\x00\x00")
    ):
        raise ValueError("full Raptor OSD font or licence identity changed")
    return {
        name: audit_elf(
            data, name,
            libraries=FULL_LIBRARIES if name in {"usr/bin/rvd", "usr/bin/rad"} else LIBRARIES,
        )
        for name, data in sorted(files.items()) if name not in DATA_FILES
    }


def pack_component(files: dict[str, bytes], provenance: dict[str, object]) -> bytes:
    """Produce a deterministic regular-file archive, without credentials/config."""
    audit_payload(files)
    if set(provenance) != {"recipe", "source_trees", "build_inputs"}:
        raise ValueError("full Raptor component requires explicit build provenance")
    manifest = {
        "schema_version": 1,
        "kind": KIND,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        **provenance,
    }
    members = {**files, "component.json": (json.dumps(manifest, sort_keys=True) + "\n").encode()}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(members.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = payload_mode(name)
            member.mtime = SOURCE_DATE_EPOCH
            archive.addfile(member, io.BytesIO(data))
    if buffer.tell() > MAX_PAYLOAD:
        raise ValueError("full Raptor component exceeds its unpacked limit")
    result = gzip.compress(buffer.getvalue(), mtime=0)
    if len(result) > MAX_ARCHIVE:
        raise ValueError("full Raptor component exceeds its archive limit")
    return result


def validate_component(
    artifact: Path, *, expected_sha256: str, root: Path,
    expected_build_inputs: dict[str, object],
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Bind binaries to their public source recipe and this build's inputs."""
    try:
        with _regular_snapshot(artifact, "full Raptor component", limit=MAX_ARCHIVE) as (stream, _):
            compressed = stream.read()
        if hashlib.sha256(compressed).hexdigest() != expected_sha256:
            raise ValueError("full Raptor component digest mismatch")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(MAX_PAYLOAD + 1)
        if len(raw) > MAX_PAYLOAD:
            raise ValueError("full Raptor component exceeds its unpacked limit")
        files = {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                if (
                    member.name not in MEMBERS or member.name in files
                    or not member.isfile() or member.uid != 0 or member.gid != 0
                    or member.mode != payload_mode(member.name)
                    or member.mtime != SOURCE_DATE_EPOCH
                ):
                    raise ValueError("full Raptor component member mismatch")
                files[member.name] = archive.extractfile(member).read()
        if set(files) != MEMBERS:
            raise ValueError("full Raptor component is incomplete")
        manifest = json.loads(files.pop("component.json"))
        lock = source_lock(root, full_media=True)
        sdk = json.loads((root / "sources.lock.json").read_bytes())["sources"]["thingino_build_toolchain_aarch64"]
        if (
            set(expected_build_inputs) != {
                "base_rootfs_sha256",
                "builder_image_id",
                "toolchain_sha256",
            }
            or not re.fullmatch(r"[a-f0-9]{64}", str(expected_build_inputs.get("base_rootfs_sha256", "")))
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(expected_build_inputs.get("builder_image_id", "")))
            or expected_build_inputs.get("toolchain_sha256") != sdk["sha256"]
        ):
            raise ValueError("full Raptor build input identities are invalid")
        expected = {
            "schema_version": 1,
            "kind": KIND,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "recipe": recipe_identity(root, full_media=True),
            "source_trees": {name: spec["tree"] for name, spec in lock["sources"].items()},
            "build_inputs": expected_build_inputs,
        }
        if manifest != expected:
            raise ValueError("full Raptor component provenance mismatch")
        audit_payload(files)
        return files, manifest
    except (EOFError, gzip.BadGzipFile, zlib.error, tarfile.TarError,
            UnicodeError, KeyError, TypeError) as exc:
        raise ValueError("invalid full Raptor component encoding") from exc
