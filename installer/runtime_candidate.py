"""Volatile media and control-plane staging for one authenticated camera."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .recovery_ap.host import (
    RUNTIME_ACTIVATE_COMMAND,
    RUNTIME_RECEIVE_COMMAND,
    RUNTIME_ROLLBACK_COMMAND,
    RUNTIME_STATUS_COMMAND,
    RecoveryApHostError,
    _exchange,
    load_host_session,
    probe_recovery_ap,
    resolve_recovery_ap_station_candidates,
)


class RuntimeCandidateError(ValueError):
    """A volatile runtime candidate failed validation, staging, or rollback."""


TEMPLATE_PATH = Path(__file__).with_name("templates") / "runtime-candidate.sh"
ROOTFS_LIMIT = 8 * 1024 * 1024
BINARY_LIMIT = 2 * 1024 * 1024
MEMBERS = {
    "prudynt": "usr/bin/prudynt",
    "thingino_control": "usr/sbin/thingino-controld",
    "uhttpd": "usr/bin/uhttpd",
}


def _regular_bytes(path: Path, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RuntimeCandidateError(f"cannot read {label}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > limit
        ):
            raise RuntimeCandidateError(f"{label} violates its size or file policy")
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise RuntimeCandidateError(f"{label} changed while being read")
    return raw


def _validate_provenance(rootfs: bytes, provenance_path: Path) -> str:
    raw = _regular_bytes(provenance_path, "final-root provenance", 64 * 1024)
    try:
        document = json.loads(raw)
        policies = document["policies"]
        system = document["system"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeCandidateError("final-root provenance is invalid") from exc
    digest = hashlib.sha256(rootfs).hexdigest()
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("status") != "private final-root input; not an install authorization"
        or not isinstance(policies, dict)
        or not isinstance(system, dict)
        or system.get("filename") != "system.private.squashfs"
        or system.get("sha256") != digest
        or system.get("size") != len(rootfs)
        or policies.get("automatic_default_route") is not False
        or policies.get("generic_updater_removed") is not True
        or policies.get("media_runtime") != "source-built-prudynt-global-glibc-c1-closure"
        or policies.get("media_start") != "automatic-S31prudynt"
        or policies.get("native_media") != "hash-locked-c1-tx-isp-sensor-iq"
        or policies.get("polluted_module_paths_removed") is not True
        or policies.get("sensor_unknown_fallback") != "json-os02g10"
    ):
        raise RuntimeCandidateError("final-root provenance does not bind the supplied rootfs")
    return hashlib.sha256(raw).hexdigest()


def _unsquashfs(tool: Path | None) -> Path:
    candidate = tool
    if candidate is None:
        found = shutil.which("unsquashfs")
        if found is None:
            raise RuntimeCandidateError("required host tool is missing: unsquashfs")
        candidate = Path(found)
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimeCandidateError("unsquashfs is missing") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RuntimeCandidateError("unsquashfs is not executable")
    return resolved


def _extract_member(*, tool: Path, rootfs_path: Path, relative: str) -> bytes:
    try:
        completed = subprocess.run(
            [str(tool), "-cat", str(rootfs_path), relative],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeCandidateError("cannot inspect the candidate rootfs") from exc
    if completed.returncode != 0 or not completed.stdout or len(completed.stdout) > BINARY_LIMIT:
        raise RuntimeCandidateError(f"candidate rootfs member is invalid: {relative}")
    return completed.stdout


def _validate_mips_elf(raw: bytes, label: str) -> None:
    if len(raw) < 52 or raw[:6] != b"\x7fELF\x01\x01":
        raise RuntimeCandidateError(f"candidate {label} is not a 32-bit little-endian ELF")
    if int.from_bytes(raw[16:18], "little") != 3:
        raise RuntimeCandidateError(f"candidate {label} is not a dynamic executable")
    if int.from_bytes(raw[18:20], "little") != 8:
        raise RuntimeCandidateError(f"candidate {label} does not target MIPS")
    flags = int.from_bytes(raw[36:40], "little")
    if flags & 0xF0000000 != 0x70000000 or flags & 0x0000F000 != 0x00001000:
        raise RuntimeCandidateError(f"candidate {label} is not MIPS32r2 O32")
    if b"/lib/ld.so.1\0" not in raw:
        raise RuntimeCandidateError(f"candidate {label} lacks the pinned glibc loader")


def _tar_member(package: tarfile.TarFile, name: str, raw: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(raw)
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    package.addfile(info, io.BytesIO(raw))


def build_runtime_candidate_package(
    *,
    rootfs_path: Path,
    provenance_path: Path,
    unsquashfs: Path | None = None,
) -> tuple[bytes, dict[str, object]]:
    """Extract the three coupled runtime ELFs and package a fixed activator."""

    rootfs = _regular_bytes(rootfs_path, "candidate rootfs", ROOTFS_LIMIT)
    provenance_sha256 = _validate_provenance(rootfs, provenance_path)
    tool = _unsquashfs(unsquashfs)
    with tempfile.TemporaryDirectory(
        prefix="thingino-runtime-candidate-",
        dir=os.environ.get("TMPDIR"),
    ) as name:
        snapshot = Path(name) / "candidate.squashfs"
        snapshot.write_bytes(rootfs)
        snapshot.chmod(0o600)
        binaries = {
            member: _extract_member(tool=tool, rootfs_path=snapshot, relative=relative)
            for member, relative in MEMBERS.items()
        }
    for name, raw in binaries.items():
        _validate_mips_elf(raw, name)
    activator = _regular_bytes(TEMPLATE_PATH, "runtime candidate activator", 32 * 1024)
    digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in binaries.items()}
    manifest = (
        f"{hashlib.sha256(activator).hexdigest()}  activate.sh\n"
        f"{digests['prudynt']}  {MEMBERS['prudynt']}\n"
        f"{digests['thingino_control']}  {MEMBERS['thingino_control']}\n"
        f"{digests['uhttpd']}  {MEMBERS['uhttpd']}\n"
    ).encode("ascii")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:", format=tarfile.USTAR_FORMAT) as package:
        _tar_member(package, "activate.sh", activator, 0o700)
        _tar_member(package, MEMBERS["prudynt"], binaries["prudynt"], 0o700)
        _tar_member(
            package,
            MEMBERS["thingino_control"],
            binaries["thingino_control"],
            0o700,
        )
        _tar_member(package, MEMBERS["uhttpd"], binaries["uhttpd"], 0o700)
        _tar_member(package, "candidate.sha256", manifest, 0o600)
    archive = output.getvalue()
    return archive, {
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "archive_size": len(archive),
        "activator_sha256": hashlib.sha256(activator).hexdigest(),
        "control_sha256": digests["thingino_control"],
        "control_size": len(binaries["thingino_control"]),
        "nor_writes": False,
        "provenance_sha256": provenance_sha256,
        "prudynt_sha256": digests["prudynt"],
        "prudynt_size": len(binaries["prudynt"]),
        "rootfs_sha256": hashlib.sha256(rootfs).hexdigest(),
        "rootfs_size": len(rootfs),
        "uhttpd_sha256": digests["uhttpd"],
        "uhttpd_size": len(binaries["uhttpd"]),
    }


def _parse_state(raw: bytes, allowed: set[str]) -> dict[str, object]:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeCandidateError("runtime candidate response is not ASCII") from exc
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.count("=") != 1:
            raise RuntimeCandidateError("runtime candidate response framing is invalid")
        key, value = line.split("=", 1)
        if key in fields:
            raise RuntimeCandidateError("runtime candidate response has duplicate fields")
        fields[key] = value
    if fields.get("schema") != "1" or fields.get("state") not in allowed:
        raise RuntimeCandidateError("runtime candidate state is invalid")
    base_fields = {"schema", "state"}
    legacy_digest_fields = base_fields | {"prudynt_sha256", "control_sha256"}
    digest_fields = base_fields | {
        "prudynt_sha256",
        "control_sha256",
        "uhttpd_sha256",
    }
    if fields["state"] == "active":
        valid_fields = set(fields) == digest_fields
    else:
        valid_fields = frozenset(fields) in {
            frozenset(base_fields),
            frozenset(legacy_digest_fields),
            frozenset(digest_fields),
        }
    if not valid_fields:
        raise RuntimeCandidateError("runtime candidate response field set is invalid")
    for key, value in fields.items():
        if key.endswith("_sha256") and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise RuntimeCandidateError("runtime candidate digest is invalid")
    return {**fields, "nor_writes": False}


def stage_runtime_candidate(
    *,
    session_dir: Path,
    host: str,
    rootfs_path: Path,
    provenance_path: Path,
    expected_mtd3_sha256: str,
    unsquashfs: Path | None = None,
) -> dict[str, object]:
    """Upload and activate the exact candidate with rollback on activation failure."""

    archive, metadata = build_runtime_candidate_package(
        rootfs_path=rootfs_path,
        provenance_path=provenance_path,
        unsquashfs=unsquashfs,
    )
    session = load_host_session(session_dir)
    try:
        _, resolved_hosts = resolve_recovery_ap_station_candidates(session_dir)
        if host not in resolved_hosts:
            raise RuntimeCandidateError("runtime candidate host is not the current session station")
        identity = probe_recovery_ap(
            session_dir=session_dir,
            host=host,
            expected_state="station",
        )
        if identity.get("mtd3_sha256") != expected_mtd3_sha256:
            raise RuntimeCandidateError("runtime candidate target mtd3 identity changed")
        _parse_state(
            _exchange(session, host=host, command=RUNTIME_STATUS_COMMAND),
            {"absent", "rolled-back"},
        )
        received = _exchange(
            session,
            host=host,
            command=RUNTIME_RECEIVE_COMMAND,
            payload=archive,
            timeout=60.0,
        )
        if received != b"received\n":
            raise RuntimeCandidateError("camera did not accept the runtime candidate")
        state = _parse_state(
            _exchange(
                session,
                host=host,
                command=RUNTIME_ACTIVATE_COMMAND,
                timeout=90.0,
            ),
            {"active"},
        )
    except RecoveryApHostError as exc:
        raise RuntimeCandidateError("authenticated runtime staging failed") from exc
    if (
        state.get("prudynt_sha256") != metadata["prudynt_sha256"]
        or state.get("control_sha256") != metadata["control_sha256"]
        or state.get("uhttpd_sha256") != metadata["uhttpd_sha256"]
    ):
        raise RuntimeCandidateError("camera activated different runtime binaries")
    return {**metadata, **state, "rollback_required": True}


def runtime_candidate_status(*, session_dir: Path, host: str) -> dict[str, object]:
    session = load_host_session(session_dir)
    try:
        raw = _exchange(session, host=host, command=RUNTIME_STATUS_COMMAND)
    except RecoveryApHostError as exc:
        raise RuntimeCandidateError("authenticated runtime status failed") from exc
    return _parse_state(raw, {"absent", "active", "partial", "rolled-back"})


def rollback_runtime_candidate(*, session_dir: Path, host: str) -> dict[str, object]:
    session = load_host_session(session_dir)
    try:
        raw = _exchange(
            session,
            host=host,
            command=RUNTIME_ROLLBACK_COMMAND,
            timeout=90.0,
        )
        state = _parse_state(raw, {"absent", "rolled-back"})
        verified = _parse_state(
            _exchange(session, host=host, command=RUNTIME_STATUS_COMMAND),
            {"absent", "rolled-back"},
        )
    except RecoveryApHostError as exc:
        raise RuntimeCandidateError("authenticated runtime rollback failed") from exc
    if verified["state"] != state["state"]:
        raise RuntimeCandidateError("runtime rollback state did not remain stable")
    return verified
