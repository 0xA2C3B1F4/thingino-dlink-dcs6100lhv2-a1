"""Authenticated host control for the bounded recovery-AP protocol."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import queue
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..ram_boot import RamBootError, parse_ramdisk_uimage
from ..mtd3_image import (
    Mtd3ImageError,
    PersonalMtd3Image,
    validate_personal_mtd3_image,
)
from ..vendor_bundle import (
    CATALOG_PATH,
    FILES_DIRECTORY,
    MANIFEST_NAME,
    VendorBundleError,
    load_vendor_bundle,
)
from .nor_state import NorStateError, RecoveryNorState, parse_nor_state


class RecoveryApHostError(ValueError):
    """The private session or authenticated recovery-AP exchange failed."""


RUNTIME_CANDIDATE_DIR = "/run/thingino-runtime-candidate"
RUNTIME_RECEIVE_COMMAND = (
    "awk '$2 == \"/run\" && $3 == \"tmpfs\" { found = 1 } "
    "END { exit !found }' /proc/mounts || exit 1; "
    f"umask 077; /usr/bin/rm -rf {RUNTIME_CANDIDATE_DIR}; "
    f"/usr/bin/mkdir -p {RUNTIME_CANDIDATE_DIR}/usr/bin "
    f"{RUNTIME_CANDIDATE_DIR}/usr/sbin; "
    f"/usr/bin/tar -xf - -C {RUNTIME_CANDIDATE_DIR}; "
    f"/usr/bin/chmod 0700 {RUNTIME_CANDIDATE_DIR}/activate.sh; "
    "printf 'received\\n'"
)
RUNTIME_ACTIVATE_COMMAND = f"/bin/sh {RUNTIME_CANDIDATE_DIR}/activate.sh activate"
RUNTIME_STATUS_COMMAND = (
    f"if [ -x {RUNTIME_CANDIDATE_DIR}/activate.sh ]; then "
    f"/bin/sh {RUNTIME_CANDIDATE_DIR}/activate.sh status; "
    "else printf 'schema=1\\nstate=absent\\n'; fi"
)
RUNTIME_ROLLBACK_COMMAND = (
    f"if [ -x {RUNTIME_CANDIDATE_DIR}/activate.sh ]; then "
    f"/bin/sh {RUNTIME_CANDIDATE_DIR}/activate.sh rollback; "
    "else printf 'schema=1\\nstate=absent\\n'; fi"
)


@dataclass(frozen=True, slots=True)
class RecoveryApHostSession:
    identity: Path
    known_hosts: Path
    station_mdns_name: str


def _private_file(path: Path, label: str, limit: int) -> bytes:
    from .transport import _private_file as _impl

    return _impl(sys.modules[__name__], path, label, limit)


def load_host_session(session_dir: Path) -> RecoveryApHostSession:
    from .transport import load_host_session as _impl

    return _impl(sys.modules[__name__], session_dir)


def load_service_credential(session_dir: Path) -> bytes:
    from .transport import load_service_credential as _impl

    return _impl(sys.modules[__name__], session_dir)


def _host(value: str) -> str:
    from .transport import _host as _impl

    return _impl(sys.modules[__name__], value)


def resolve_recovery_ap_station_candidates(
    session_dir: Path,
) -> tuple[str, tuple[str, ...]]:
    from .transport import resolve_recovery_ap_station_candidates as _impl

    return _impl(sys.modules[__name__], session_dir)


def resolve_recovery_ap_station(session_dir: Path) -> tuple[str, str]:
    from .transport import resolve_recovery_ap_station as _impl

    return _impl(sys.modules[__name__], session_dir)


def ssh_arguments(
    session: RecoveryApHostSession, *, host: str, command: str
) -> list[str]:
    from .transport import ssh_arguments as _impl

    return _impl(sys.modules[__name__], session, host=host, command=command)


def _exchange(
    session: RecoveryApHostSession,
    *,
    host: str,
    command: str,
    payload: bytes = b"",
    timeout: float = 30.0,
) -> bytes:
    from .transport import _exchange as _impl

    return _impl(sys.modules[__name__], session, host=host, command=command, payload=payload, timeout=timeout)


def _parse_nor_state(raw: bytes) -> RecoveryNorState:
    try:
        return parse_nor_state(raw)
    except NorStateError as exc:
        raise RecoveryApHostError(str(exc)) from exc


def _digest(value: str, label: str) -> str:
    """Compatibility façade for the former private digest validator."""

    from .nor_state import _digest as validate_digest

    try:
        return validate_digest(value, label)
    except NorStateError as exc:
        raise RecoveryApHostError(str(exc)) from exc


def inspect_recovery_ap_nor(*, session_dir: Path, host: str) -> RecoveryNorState:
    """Read and classify the exact persistent NOR state without writing it."""

    session = load_host_session(session_dir)
    return _parse_nor_state(_exchange(session, host=host, command="inspect-nor", timeout=90.0))


def diagnose_thingino_failure(
    *, session_dir: Path, host: str
) -> dict[str, object]:
    """Read the bounded volatile failure report after recovery returned to AP."""

    session = load_host_session(session_dir)
    raw = _exchange(session, host=host, command="thingino-failure")
    if len(raw) > 40 * 1024:
        raise RecoveryApHostError("camera Thingino failure report exceeds its cap")
    pattern = re.compile(
        rb"schema=1\n"
        rb"thingino_mounts=(present|absent)\n"
        rb"cleanup=(failed|complete)\n"
        rb"prudynt_log_size=([0-9]+)\n"
        rb"prudynt_log_sha256=(-|[0-9a-f]{64})\n"
        rb"(?:prudynt_log_tail_begin\n(.*)\nprudynt_log_tail_end\n)?",
        re.DOTALL,
    )
    match = pattern.fullmatch(raw)
    if match is None:
        raise RecoveryApHostError("camera Thingino failure report framing is invalid")
    log_size = int(match.group(3))
    log_sha256 = match.group(4).decode("ascii")
    log_tail = match.group(5)
    if log_size == 0:
        if log_sha256 != "-" or log_tail is not None:
            raise RecoveryApHostError("camera empty Prudynt log identity is invalid")
        decoded_tail = ""
    else:
        if log_sha256 == "-" or log_tail is None or len(log_tail) > 32768:
            raise RecoveryApHostError("camera Prudynt log identity is invalid")
        decoded_tail = log_tail.decode("utf-8", errors="replace")
    return {
        "cleanup": match.group(2).decode("ascii"),
        "nor_writes": False,
        "prudynt_log_sha256": None if log_sha256 == "-" else log_sha256,
        "prudynt_log_size": log_size,
        "prudynt_log_tail": decoded_tail,
        "thingino_mounts": match.group(1).decode("ascii"),
    }


def _validate_image_vendor_binding(
    *,
    image_payload_sha256: str,
    image_payload_size: int,
    vendor_bundle_dir: Path | None,
    provenance_path: Path | None,
) -> str | None:
    if vendor_bundle_dir is None and provenance_path is None:
        return None
    if vendor_bundle_dir is None or provenance_path is None:
        raise RecoveryApHostError("mtd3 rewrite requires both vendor bundle and image provenance")
    try:
        bundle = load_vendor_bundle(vendor_bundle_dir)
    except VendorBundleError as exc:
        raise RecoveryApHostError("mtd3 vendor bundle validation failed") from exc
    raw = _private_file(provenance_path, "final-root provenance", 64 * 1024)
    try:
        document = json.loads(raw)
        policies = document["policies"]
        system = document["system"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RecoveryApHostError("mtd3 image provenance is invalid") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(policies, dict)
        or not isinstance(system, dict)
        or policies.get("vendor_bundle_sha256") != bundle.bundle_sha256
        or system.get("sha256") != image_payload_sha256
        or system.get("size") != image_payload_size
    ):
        raise RecoveryApHostError("mtd3 image is not bound to the supplied vendor bundle")
    return bundle.bundle_sha256


def reconcile_recovery_ap_state(
    *,
    session_dir: Path,
    host: str,
    image_path: Path,
    vendor_bundle_dir: Path | None = None,
    provenance_path: Path | None = None,
) -> dict[str, object]:
    """Derive one safe next action from live NOR, never from a stale phase marker."""

    if image_path.is_symlink() or not image_path.is_file():
        raise RecoveryApHostError("personal mtd3 image is not a regular file")
    try:
        image = validate_personal_mtd3_image(image_path.read_bytes())
    except Mtd3ImageError as exc:
        raise RecoveryApHostError(str(exc)) from exc
    return _reconcile_validated_image(
        session_dir=session_dir,
        host=host,
        image=image,
        vendor_bundle_dir=vendor_bundle_dir,
        provenance_path=provenance_path,
    )


def _reconcile_validated_image(
    *,
    session_dir: Path,
    host: str,
    image: PersonalMtd3Image,
    vendor_bundle_dir: Path | None,
    provenance_path: Path | None,
) -> dict[str, object]:
    binding = _validate_image_vendor_binding(
        image_payload_sha256=image.payload_sha256,
        image_payload_size=image.payload_size,
        vendor_bundle_dir=vendor_bundle_dir,
        provenance_path=provenance_path,
    )
    state = inspect_recovery_ap_nor(session_dir=session_dir, host=host)
    session = load_host_session(session_dir)
    runtime_state = _exchange(session, host=host, command="status").decode("ascii").strip()
    if runtime_state not in {"ap", "station", "thingino"}:
        raise RecoveryApHostError("camera recovery runtime state is invalid")
    if state.layout == "unknown":
        action = "stop_unknown_layout"
    elif state.mtd1_kind != "uimage" or state.mtd2_kind != "squashfs":
        action = "reinstall_bootstrap"
    elif state.mtd3_mounted:
        action = "stop_mtd3_busy"
    elif state.mtd3_kind == "personal" and state.mtd3_sha256 == image.image_sha256:
        action = "boot_and_verify" if runtime_state == "thingino" else "activate_existing"
    elif state.mtd3_kind == "stock" and binding is None:
        action = "extract_vendor"
    elif binding is not None:
        action = "install_mtd3"
    else:
        action = "stop_unknown_mtd3"
    return {
        "image_sha256": image.image_sha256,
        "layout": state.layout,
        "mtd1_kind": state.mtd1_kind,
        "mtd1_sha256": state.mtd1_sha256,
        "mtd2_kind": state.mtd2_kind,
        "mtd2_sha256": state.mtd2_sha256,
        "mtd3_mounted": state.mtd3_mounted,
        "mtd3_kind": state.mtd3_kind,
        "mtd3_sha256": state.mtd3_sha256,
        "runtime_state": runtime_state,
        "safe_next_action": action,
        "vendor_bundle_sha256": binding,
    }


def probe_recovery_ap(
    *, session_dir: Path, host: str, expected_state: str
) -> dict[str, object]:
    if expected_state not in {"ap", "station"}:
        raise RecoveryApHostError("expected recovery state is invalid")
    session = load_host_session(session_dir)
    if expected_state == "station":
        payload = secrets.token_bytes(4096)
        digest = hashlib.sha256(payload).hexdigest()
        response = _exchange(
            session,
            host=host,
            command="thingino-health; sha256sum /dev/mtd3",
            payload=payload,
            timeout=20.0,
        )
        prefix = f"healthy {session.station_mdns_name}\n".encode("ascii")
        if not response.startswith(prefix + payload):
            raise RecoveryApHostError("Thingino station health gate failed")
        suffix = response[len(prefix) + len(payload) :]
        match = re.fullmatch(rb"([0-9a-f]{64})  /dev/mtd3\n", suffix)
        if match is None:
            raise RecoveryApHostError("Thingino station mtd3 identity is invalid")
        return {
            "authenticated_control": True,
            "file_transfer_bytes": len(payload),
            "file_transfer_sha256": digest,
            "mtd3_read_back_verified": True,
            "mtd3_sha256": match.group(1).decode("ascii"),
            "nor_writes": False,
            "state": expected_state,
        }
    state = _exchange(session, host=host, command="status")
    if state != f"{expected_state}\n".encode("ascii"):
        raise RecoveryApHostError("camera recovery state is unexpected")
    payload = secrets.token_bytes(4096)
    digest = hashlib.sha256(payload).hexdigest()
    accepted = _exchange(
        session,
        host=host,
        command=f"receive {len(payload)} {digest}",
        payload=payload,
    )
    if accepted != b"accepted\n":
        raise RecoveryApHostError("camera did not accept the test transfer")
    returned = _exchange(session, host=host, command=f"send {digest}")
    if returned != payload:
        raise RecoveryApHostError("authenticated transfer read-back mismatch")
    return {
        "authenticated_control": True,
        "file_transfer_bytes": len(payload),
        "file_transfer_sha256": digest,
        "nor_writes": False,
        "state": expected_state,
    }


def install_recovery_ap(
    *, session_dir: Path, host: str, image_path: Path
) -> dict[str, object]:
    if image_path.is_symlink() or not image_path.is_file():
        raise RecoveryApHostError("recovery image is not a regular file")
    metadata = image_path.stat()
    if metadata.st_size < 4160 or metadata.st_size > 4538432:
        raise RecoveryApHostError("recovery image is outside the RAM-root size limit")
    image = image_path.read_bytes()
    if len(image) != metadata.st_size:
        raise RecoveryApHostError("recovery image changed while being read")
    try:
        parse_ramdisk_uimage(image)
    except RamBootError as exc:
        raise RecoveryApHostError("recovery image failed uImage validation") from exc
    digest = hashlib.sha256(image).hexdigest()
    session = load_host_session(session_dir)
    accepted = _exchange(
        session,
        host=host,
        command=f"receive {len(image)} {digest}",
        payload=image,
        timeout=60.0,
    )
    if accepted != b"accepted\n":
        raise RecoveryApHostError("camera did not accept the recovery image")
    installed = _exchange(
        session,
        host=host,
        command=f"install-recovery {digest}",
        timeout=60.0,
    )
    if installed != b"installed\n":
        raise RecoveryApHostError("camera did not install the recovery image")
    return {
        "image_sha256": digest,
        "image_size": len(image),
        "nor_writes": False,
        "recovery_media_updated": True,
        "read_back_verified": True,
    }


def _station_payload(ssid: str, passphrase: str) -> bytes:
    try:
        ssid_bytes = ssid.encode("utf-8")
        passphrase_bytes = passphrase.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecoveryApHostError("Wi-Fi input is not valid UTF-8") from exc
    if not 1 <= len(ssid_bytes) <= 32 or not 8 <= len(passphrase_bytes) <= 63:
        raise RecoveryApHostError("Wi-Fi input is outside WPA2 size limits")
    if any(byte < 0x20 or byte == 0x7F for byte in ssid_bytes + passphrase_bytes):
        raise RecoveryApHostError("Wi-Fi input contains a control character")
    psk = hashlib.pbkdf2_hmac("sha1", passphrase_bytes, ssid_bytes, 4096, 32)
    return ssid_bytes.hex().encode("ascii") + b"\n" + psk.hex().encode("ascii") + b"\n"


def provision_recovery_ap(
    *, session_dir: Path, host: str, ssid: str, passphrase: str
) -> dict[str, object]:
    session = load_host_session(session_dir)
    accepted = _exchange(
        session,
        host=host,
        command="provision",
        payload=_station_payload(ssid, passphrase),
    )
    if accepted != b"accepted\n":
        raise RecoveryApHostError("camera did not accept Wi-Fi provisioning")
    return {
        "credentials_logged": False,
        "credentials_transport": "ssh-stdin-derived-psk",
        "nor_writes": False,
        "transition_requested": "station",
    }


def extract_camera_vendor_bundle(
    *, session_dir: Path, host: str, output_dir: Path
) -> dict[str, object]:
    """Download only the hash-allowlisted files from camera-mounted stock mtd3."""

    if output_dir.exists() or output_dir.is_symlink():
        raise RecoveryApHostError("refusing to reuse a vendor bundle output directory")
    session = load_host_session(session_dir)
    response = _exchange(session, host=host, command="vendor-export", timeout=60.0)
    match = re.fullmatch(rb"vendor ([1-9][0-9]{0,7}) ([0-9a-f]{64})\n", response)
    if match is None:
        raise RecoveryApHostError("camera vendor export identity is invalid")
    size = int(match.group(1))
    digest = match.group(2).decode("ascii")
    if size > 4 * 1024 * 1024:
        raise RecoveryApHostError("camera vendor export exceeds its fixed cap")
    archive = _exchange(session, host=host, command=f"send {digest}", timeout=60.0)
    if len(archive) != size or hashlib.sha256(archive).hexdigest() != digest:
        raise RecoveryApHostError("camera vendor export read-back mismatch")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    expected = {entry["name"]: entry for entry in catalog["files"]}
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    try:
        work.chmod(0o700)
        files = work / FILES_DIRECTORY
        files.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as package:
            members = package.getmembers()
            names: set[str] = set()
            for member in members:
                name = member.name.removeprefix("./")
                if name in {"", "."} and member.isdir():
                    continue
                if name not in expected or name in names or not member.isfile():
                    raise RecoveryApHostError("camera vendor archive member is invalid")
                source = package.extractfile(member)
                if source is None:
                    raise RecoveryApHostError("camera vendor archive member cannot be read")
                raw = source.read()
                entry = expected[name]
                if (
                    len(raw) != entry["size"]
                    or hashlib.sha256(raw).hexdigest() != entry["sha256"]
                ):
                    raise RecoveryApHostError("camera vendor archive file mismatch")
                destination = files / name
                destination.write_bytes(raw)
                destination.chmod(0o600)
                names.add(name)
        required = {name for name, entry in expected.items() if entry["required"]}
        if not required.issubset(names) or not names.issubset(expected):
            raise RecoveryApHostError("camera vendor archive closure is incomplete")
        manifest = {
            "schema_version": 1,
            "target": catalog["target"],
            "source": {
                "firmware_version": catalog["source"]["firmware_version"],
                "mounted_read_only": True,
                "partition": catalog["source"]["partition"],
            },
            "files": [
                {
                    "name": name,
                    "sha256": expected[name]["sha256"],
                    "size": expected[name]["size"],
                    "source_path": expected[name]["source_path"],
                }
                for name in expected
                if name in names
            ],
        }
        manifest_path = work / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)
        bundle = load_vendor_bundle(work)
        os.replace(work, output_dir)
    except (OSError, tarfile.TarError, VendorBundleError) as exc:
        raise RecoveryApHostError("camera vendor bundle validation failed") from exc
    finally:
        if work.exists():
            shutil.rmtree(work)
    return {
        "archive_sha256": digest,
        "bundle_sha256": bundle.bundle_sha256,
        "files": [artifact.name for artifact in bundle.artifacts],
        "mounted_read_only": True,
        "nor_writes": False,
    }


def install_personal_mtd3(
    *,
    session_dir: Path,
    host: str,
    image_path: Path,
    vendor_bundle_dir: Path | None = None,
    provenance_path: Path | None = None,
    expected_image_sha256: str | None = None,
    progress: Callable[[str, bool], None] | None = None,
) -> dict[str, object]:
    """Upload, write, and fully read back one exact personal physical-mtd3 image."""

    if image_path.is_symlink() or not image_path.is_file():
        raise RecoveryApHostError("personal mtd3 image is not a regular file")
    raw = image_path.read_bytes()
    try:
        image = validate_personal_mtd3_image(raw)
    except Mtd3ImageError as exc:
        raise RecoveryApHostError(str(exc)) from exc
    if (
        expected_image_sha256 is not None
        and image.image_sha256 != expected_image_sha256
    ):
        raise RecoveryApHostError("personal mtd3 image changed before reconciliation")
    decision = _reconcile_validated_image(
        session_dir=session_dir,
        host=host,
        image=image,
        vendor_bundle_dir=vendor_bundle_dir,
        provenance_path=provenance_path,
    )
    if decision["safe_next_action"] in {"activate_existing", "boot_and_verify"}:
        return {
            "already_installed": True,
            "image_sha256": image.image_sha256,
            "image_size": len(raw),
            "payload_sha256": image.payload_sha256,
            "read_back_verified": True,
            "safe_next_action": decision["safe_next_action"],
            "written_mtd": [],
        }
    if decision["safe_next_action"] != "install_mtd3":
        raise RecoveryApHostError(
            f"mtd3 write rejected; safe next action is {decision['safe_next_action']}"
        )
    session = load_host_session(session_dir)
    accepted = _exchange(
        session,
        host=host,
        command=f"receive {len(raw)} {image.image_sha256}",
        payload=raw,
        timeout=120.0,
    )
    if accepted != b"accepted\n":
        raise RecoveryApHostError("camera did not accept the personal mtd3 image")
    if progress is not None:
        progress("Writing only physical mtd3 and reading all of it back", True)
    try:
        installed = _exchange(
            session,
            host=host,
            command=f"install-mtd3 {image.image_sha256}",
            timeout=240.0,
        )
    finally:
        if progress is not None:
            progress("Physical mtd3 write/readback operation has ended", False)
    if installed != b"installed\n":
        raise RecoveryApHostError("camera did not install the personal mtd3 image")
    return {
        "image_sha256": image.image_sha256,
        "image_size": len(raw),
        "payload_sha256": image.payload_sha256,
        "read_back_verified": True,
        "safe_next_action": "activate_existing",
        "written_mtd": [3],
    }


def activate_personal_mtd3(
    *, session_dir: Path, host: str, image_sha256: str
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{64}", image_sha256) is None:
        raise RecoveryApHostError("personal mtd3 image digest is invalid")
    session = load_host_session(session_dir)
    response = _exchange(
        session,
        host=host,
        command=f"activate-mtd3 {image_sha256}",
        timeout=30.0,
    )
    if response != b"activating\n":
        raise RecoveryApHostError("camera did not activate the personal mtd3 image")
    return {"activation_requested": True, "image_sha256": image_sha256}


def _parse_thingino_health(
    *, response: bytes, payload: bytes, mdns_name: str, host: str
) -> dict[str, object]:
    prefix = f"healthy {mdns_name}\n".encode("ascii")
    if not response.startswith(prefix + payload):
        raise RecoveryApHostError("Thingino final health gate failed")
    suffix = response[len(prefix) + len(payload) :]
    match = re.fullmatch(rb"([0-9a-f]{64})  /dev/mtd3\n", suffix)
    if match is None:
        raise RecoveryApHostError("Thingino live mtd3 read-back identity is invalid")
    return {
        "authenticated_control": True,
        "file_transfer_bytes": len(payload),
        "file_transfer_sha256": hashlib.sha256(payload).hexdigest(),
        "health_gate": "passed",
        "mdns_resolution": "session-pinned",
        "mtd3_read_back_verified": True,
        "mtd3_sha256": match.group(1).decode("ascii"),
        "ssh_authentication": "pinned-key-only",
        "station_mdns_name": mdns_name,
        "station_ipv4": _host(host),
    }


def prove_thingino_health(
    *, session_dir: Path, expected_mtd3_sha256: str | None = None
) -> dict[str, object]:
    session = load_host_session(session_dir)
    if expected_mtd3_sha256 is not None and re.fullmatch(
        r"[0-9a-f]{64}", expected_mtd3_sha256
    ) is None:
        raise RecoveryApHostError("expected live mtd3 identity is invalid")
    if expected_mtd3_sha256 is None:
        mdns_name, host = resolve_recovery_ap_station(session_dir)
        hosts = (host,)
    else:
        mdns_name, hosts = resolve_recovery_ap_station_candidates(session_dir)
    payload = secrets.token_bytes(4096)
    deadline = time.monotonic() + 90.0
    while True:
        matches: list[dict[str, object]] = []
        last_error: RecoveryApHostError | None = None
        for host in hosts:
            try:
                response = _exchange(
                    session,
                    host=host,
                    command="thingino-health; sha256sum /dev/mtd3",
                    payload=payload,
                    timeout=20.0,
                )
                result = _parse_thingino_health(
                    response=response,
                    payload=payload,
                    mdns_name=mdns_name,
                    host=host,
                )
                if (
                    expected_mtd3_sha256 is None
                    or result["mtd3_sha256"] == expected_mtd3_sha256
                ):
                    matches.append(result)
            except RecoveryApHostError as exc:
                last_error = exc
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RecoveryApHostError(
                "multiple cameras passed the same pinned session and mtd3 identity"
            )
        if time.monotonic() >= deadline:
            if expected_mtd3_sha256 is not None:
                raise RecoveryApHostError(
                    "no pinned mDNS candidate matched the expected physical mtd3"
                ) from last_error
            raise RecoveryApHostError("Thingino management health timed out") from last_error
        time.sleep(1.0)


def prove_thingino_media(
    *, session_dir: Path, station_ipv4: str | None = None
) -> dict[str, object]:
    """Check the automatic one-stage media runtime after installer health."""

    session = load_host_session(session_dir)
    if station_ipv4 is None:
        mdns_name, host = resolve_recovery_ap_station(session_dir)
    else:
        mdns_name = session.station_mdns_name
        host = _host(station_ipv4)
    response = _exchange(
        session,
        host=host,
        command="dlink-media-verify",
        timeout=50.0,
    )
    lines = response.splitlines(keepends=True)
    ready = b"D-Link media: 1080p-started\n"
    if ready not in lines:
        raise RecoveryApHostError("Thingino one-stage 1080p media acceptance did not pass")
    return {
        "installer_health_dependency": "caller-required",
        "media_acceptance": "one-stage-1080p-started",
        "media_marker": "1080p-started",
        "media_gate": "passed",
        "mdns_resolution": "session-pinned",
        "nor_writes": False,
        "ssh_authentication": "pinned-key-only",
        "station_mdns_name": mdns_name,
        "station_ipv4": _host(host),
    }


def collect_runtime_snapshot(
    *, session_dir: Path, station_ipv4: str | None = None
) -> dict[str, object]:
    """Collect the installed bounded diagnostic JSON without restarting services."""

    session = load_host_session(session_dir)
    if station_ipv4 is None:
        _, host = resolve_recovery_ap_station(session_dir)
    else:
        host = _host(station_ipv4)
    raw = _exchange(
        session,
        host=host,
        command="dlink-runtime-snapshot",
        timeout=90.0,
    )
    if len(raw) > 64 * 1024:
        raise RecoveryApHostError("runtime snapshot exceeds its fixed cap")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryApHostError("runtime snapshot is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise RecoveryApHostError("runtime snapshot schema is invalid")
    return document
