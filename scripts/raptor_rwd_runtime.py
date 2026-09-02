#!/usr/bin/env python3
"""Stage and control the bounded Prudynt plus rwd candidate in camera RAM."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import ipaddress
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from installer.recovery_ap.host import (  # noqa: E402
    RecoveryApHostError,
    collect_runtime_snapshot,
    load_host_session,
)
from installer.runtime_diagnostics import (  # noqa: E402
    RuntimeDiagnosticsError,
    validate_runtime_snapshot,
)


REMOTE_ROOT = "/run/raptor-rwd"
HOST_KEY_ALIAS = "192.168.88.1"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024

REGULAR_MEMBERS = {
    "S13prudynt-rwd",
    "S95thingino-control",
    "SHA256SUMS",
    "etc/raptor.conf",
    "raptor-lock.json",
    "usr/bin/prudynt",
    "usr/bin/rwd",
    "usr/bin/uhttpd",
    "usr/lib/libmbedcrypto.so.3.6.6",
    "usr/lib/libmbedtls.so.3.6.6",
    "usr/lib/libmbedx509.so.3.6.6",
    "usr/lib/librss_common.so",
    "usr/lib/librss_ipc.so",
    "usr/sbin/thingino-controld",
    "var/www/assets/app.css",
    "var/www/assets/app.js",
    "var/www/index.html",
    "var/www/manifest.webmanifest",
}
SYMLINK_MEMBERS = {
    "usr/lib/libmbedcrypto.so": "libmbedcrypto.so.16",
    "usr/lib/libmbedcrypto.so.16": "libmbedcrypto.so.3.6.6",
    "usr/lib/libmbedtls.so": "libmbedtls.so.21",
    "usr/lib/libmbedtls.so.21": "libmbedtls.so.3.6.6",
    "usr/lib/libmbedx509.so": "libmbedx509.so.7",
    "usr/lib/libmbedx509.so.7": "libmbedx509.so.3.6.6",
}


class RaptorRwdRuntimeError(RuntimeError):
    pass


def _private_ipv4(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise RaptorRwdRuntimeError("station host is not an IPv4 address") from exc
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_multicast
        or address.is_unspecified
    ):
        raise RaptorRwdRuntimeError("station host is not a private unicast IPv4 address")
    return str(address)


def _read_private(path: Path, limit: int) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > limit
            or metadata.st_mode & 0o077
        ):
            raise RaptorRwdRuntimeError("session private file violates its policy")
    finally:
        os.close(descriptor)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalized_member(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RaptorRwdRuntimeError("artifact contains an unsafe member path")
    parts = path.parts
    if parts[0] != "raptor-rwd":
        raise RaptorRwdRuntimeError("artifact has the wrong top-level directory")
    if len(parts) == 1:
        return ""
    return str(PurePosixPath(*parts[1:]))


def validate_artifact(
    artifact: Path, *, expected_sha256: str, supervisor: Path
) -> tuple[bytes, dict[str, str]]:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise RaptorRwdRuntimeError("expected artifact digest is invalid")
    if artifact.is_symlink() or not artifact.is_file():
        raise RaptorRwdRuntimeError("artifact is not a regular file")
    if artifact.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RaptorRwdRuntimeError("artifact exceeds its compressed size limit")
    compressed = artifact.read_bytes()
    if _sha256(compressed) != expected_sha256:
        raise RaptorRwdRuntimeError("artifact digest does not match")
    try:
        raw_tar = gzip.decompress(compressed)
    except (OSError, EOFError) as exc:
        raise RaptorRwdRuntimeError("artifact is not a valid gzip stream") from exc
    if len(raw_tar) > MAX_PAYLOAD_BYTES:
        raise RaptorRwdRuntimeError("artifact exceeds its unpacked size limit")

    regular: dict[str, bytes] = {}
    symlinks: dict[str, str] = {}
    normalized: list[tuple[tarfile.TarInfo, bytes | None]] = []
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
            for member in archive.getmembers():
                name = _normalized_member(member.name.rstrip("/"))
                if not name:
                    continue
                if name in seen:
                    raise RaptorRwdRuntimeError("artifact contains a duplicate member")
                seen.add(name)
                if member.isdir():
                    normalized.append((member, None))
                    continue
                if member.isfile():
                    source = archive.extractfile(member)
                    if source is None:
                        raise RaptorRwdRuntimeError("artifact member cannot be read")
                    payload = source.read()
                    regular[name] = payload
                    normalized.append((member, payload))
                    continue
                if member.issym():
                    symlinks[name] = member.linkname
                    normalized.append((member, None))
                    continue
                raise RaptorRwdRuntimeError("artifact contains an unsupported member type")
    except tarfile.TarError as exc:
        raise RaptorRwdRuntimeError("artifact tar stream is invalid") from exc

    if set(regular) != REGULAR_MEMBERS or symlinks != SYMLINK_MEMBERS:
        raise RaptorRwdRuntimeError("artifact member set does not match the runtime contract")
    if supervisor.is_symlink() or not supervisor.is_file():
        raise RaptorRwdRuntimeError("checkout supervisor is not a regular file")
    if regular["S13prudynt-rwd"] != supervisor.read_bytes():
        raise RaptorRwdRuntimeError("artifact supervisor does not match the checkout")

    expected_lines = {
        f"{_sha256(payload)}  {name}" for name, payload in regular.items() if name != "SHA256SUMS"
    }
    try:
        manifest_lines = {
            line for line in regular["SHA256SUMS"].decode("ascii").splitlines() if line
        }
    except UnicodeDecodeError as exc:
        raise RaptorRwdRuntimeError("artifact checksum manifest is not ASCII") from exc
    if manifest_lines != expected_lines:
        raise RaptorRwdRuntimeError("artifact checksum manifest is invalid")

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for original, payload in normalized:
            name = _normalized_member(original.name.rstrip("/"))
            if not name:
                continue
            member = tarfile.TarInfo(name)
            member.mode = original.mode & 0o777
            member.mtime = 0
            if original.isdir():
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            elif original.issym():
                member.type = tarfile.SYMTYPE
                member.linkname = original.linkname
                archive.addfile(member)
            else:
                assert payload is not None
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
    return output.getvalue(), {name: _sha256(payload) for name, payload in regular.items()}


class Camera:
    def __init__(self, session_dir: Path, host: str) -> None:
        session = load_host_session(session_dir)
        self.host = _private_ipv4(host)
        self.identity = session.identity
        self.known_hosts = session.known_hosts
        _read_private(self.identity, 16 * 1024)
        _read_private(self.known_hosts, 2048)
        self.base = [
            "ssh",
            "-T",
            "-i",
            str(self.identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            f"HostKeyAlias={HOST_KEY_ALIAS}",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ConnectTimeout=10",
            f"root@{self.host}",
        ]

    def run(self, command: str, *, payload: bytes | None = None, timeout: int = 30) -> bytes:
        result = subprocess.run(
            [*self.base, command],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RaptorRwdRuntimeError(
                f"camera rejected the bounded runtime command with exit {result.returncode}"
            )
        return result.stdout


def baseline(session_dir: Path, host: str) -> dict[str, object]:
    snapshot = validate_runtime_snapshot(
        collect_runtime_snapshot(session_dir=session_dir, station_ipv4=host)
    )
    memory = snapshot.get("memory")
    prudynt = snapshot.get("prudynt")
    processes = snapshot.get("processes")
    if (
        not isinstance(memory, dict)
        or memory.get("available_proxy_kib", 0) < 8 * 1024
        or not isinstance(prudynt, dict)
        or prudynt.get("media_ready") is not True
        or not isinstance(processes, dict)
        or not isinstance(processes.get("prudynt"), dict)
    ):
        raise RaptorRwdRuntimeError("camera runtime baseline is outside the rwd gate")
    return snapshot


def remote_preflight(camera: Camera) -> None:
    camera.run(
        "set -eu\n"
        "for token in $(cat /proc/cmdline); do [ \"$token\" = 'mem=42M@0x0' ] && mem=yes; "
        "[ \"$token\" = 'rmem=22M@0x2a00000' ] && rmem=yes; done\n"
        "[ \"${mem:-no}\" = yes ] && [ \"${rmem:-no}\" = yes ]\n"
        "awk '$2 == \"/run\" && $3 == \"tmpfs\" { found=1 } END { exit !found }' /proc/mounts\n"
        "[ \"$(cat /run/prudynt-dlink-media.ready)\" = 1080p-started ]\n"
        "set -- $(pidof prudynt); [ \"$#\" -eq 1 ]\n"
        "for active in /run/prudynt/mp4ctl-ch*.active; do [ ! -e \"$active\" ]; done\n"
        "[ ! -e /run/raptor-rwd-webui.mounted ]\n"
        "[ ! -e /run/raptor-rwd-ui-binaries.mounted ]\n"
        "! awk '$2 == \"/var/www\" || $2 == \"/usr/bin/uhttpd\" || "
        "$2 == \"/usr/sbin/thingino-controld\" { found=1 } END { exit !found }' /proc/mounts\n"
        "[ -z \"$(pidof rwd 2>/dev/null || true)\" ]\n"
        "printf 'preflight=passed\\n'",
        timeout=20,
    )


def stage(camera: Camera, payload: bytes) -> None:
    camera.run(
        "set -eu\numask 077\n"
        "[ \"$(cat /run/prudynt-dlink-media.ready)\" = 1080p-started ]\n"
        "rm -rf /run/raptor-rwd.new\n"
        "mkdir -p /run/raptor-rwd.new\n"
        "tar -xf - -C /run/raptor-rwd.new\n"
        "cd /run/raptor-rwd.new\n"
        "sha256sum -c SHA256SUMS >/dev/null\n"
        "[ -x S13prudynt-rwd ] && [ -x S95thingino-control ] && "
        "[ -x usr/bin/prudynt ] && [ -x usr/bin/rwd ] && [ -x usr/bin/uhttpd ] && "
        "[ -x usr/sbin/thingino-controld ]\n"
        "rm -rf /run/raptor-rwd\n"
        "mv /run/raptor-rwd.new /run/raptor-rwd\n"
        "printf 'stage=passed\\n'",
        payload=payload,
        timeout=45,
    )


def start(camera: Camera) -> None:
    camera.run(
        f"set -eu\nPRUDYNT_RWD_ROOT={REMOTE_ROOT} {REMOTE_ROOT}/S13prudynt-rwd start\n"
        "[ \"$(cat /run/prudynt-dlink-media.ready)\" = 1080p-started ]\n"
        "[ -e /dev/shm/rss_ring_main ]\n"
        "[ -s /run/rwd.pid ] && kill -0 \"$(cat /run/rwd.pid)\"\n"
        "printf 'start=passed\\n'",
        timeout=45,
    )


def stop(camera: Camera) -> None:
    camera.run(
        f"set -eu\nif [ -x {REMOTE_ROOT}/S13prudynt-rwd ] && "
        "{ [ -e /run/raptor-rwd-webui.mounted ] || [ -s /run/rwd.pid ] || "
        f"{{ [ -x /proc/$(cat /run/prudynt.pid 2>/dev/null || echo 0)/exe ] && "
        f"[ \"$(readlink /proc/$(cat /run/prudynt.pid 2>/dev/null || echo 0)/exe 2>/dev/null || true)\" = {REMOTE_ROOT}/usr/bin/prudynt ]; }}; }}; then "
        f"PRUDYNT_RWD_ROOT={REMOTE_ROOT} {REMOTE_ROOT}/S13prudynt-rwd stop; fi\n"
        "i=0; while [ \"$i\" -lt 45 ]; do "
        "[ \"$(cat /run/prudynt-dlink-media.ready 2>/dev/null || true)\" = 1080p-started ] && break; "
        "sleep 1; i=$((i + 1)); done\n"
        "[ \"$(cat /run/prudynt-dlink-media.ready)\" = 1080p-started ]\n"
        "[ -z \"$(pidof rwd 2>/dev/null || true)\" ]\n"
        "[ ! -e /run/raptor-rwd-webui.mounted ]\n"
        "[ ! -e /run/raptor-rwd-ui-binaries.mounted ]\n"
        "! awk '$2 == \"/var/www\" || $2 == \"/usr/bin/uhttpd\" || "
        "$2 == \"/usr/sbin/thingino-controld\" { found=1 } END { exit !found }' /proc/mounts\n"
        "printf 'stop=passed\\n'",
        timeout=70,
    )


def rwd_metrics(camera: Camera, session_dir: Path, host: str) -> dict[str, object]:
    snapshot = validate_runtime_snapshot(
        collect_runtime_snapshot(session_dir=session_dir, station_ipv4=host)
    )
    raw = camera.run(
        "set -eu\n"
        "pid=$(cat /run/rwd.pid); [ -r /proc/$pid/status ]\n"
        "rss=$(awk '/^VmRSS:/ {print $2}' /proc/$pid/status)\n"
        "threads=$(awk '/^Threads:/ {print $2}' /proc/$pid/status)\n"
        "pss=$(awk '/^Pss:/ {sum += $2} END {print sum+0}' /proc/$pid/smaps)\n"
        "private=$(awk '/^Private_(Clean|Dirty):/ {sum += $2} END {print sum+0}' /proc/$pid/smaps)\n"
        "shared=$(awk '/^Shared_(Clean|Dirty):/ {sum += $2} END {print sum+0}' /proc/$pid/smaps)\n"
        "cpu=$(awk '{print $14+$15}' /proc/$pid/stat)\n"
        "fds=$(ls /proc/$pid/fd | wc -l)\n"
        "ring=$(stat -c %s /dev/shm/rss_ring_main)\n"
        "clients=$(awk '$2 ~ /:216A$/ && $4 == \"01\" {n++} END {print n+0}' /proc/net/tcp)\n"
        "printf 'rss_kib=%s\\npss_kib=%s\\nprivate_kib=%s\\nshared_kib=%s\\nthreads=%s\\n' "
        "\"$rss\" \"$pss\" \"$private\" \"$shared\" \"$threads\"\n"
        "printf 'cpu_ticks=%s\\nfd_count=%s\\nring_bytes=%s\\nclient_tcp_established=%s\\n' "
        "\"$cpu\" \"$fds\" \"$ring\" \"$clients\"",
        timeout=20,
    )
    metrics: dict[str, int] = {}
    for line in raw.decode("ascii").splitlines():
        key, value = line.split("=", 1)
        metrics[key] = int(value)
    return {"runtime": snapshot, "rwd": metrics}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "stage", "start", "stop", "metrics"))
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--station-ipv4", required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--expected-sha256")
    arguments = parser.parse_args()
    host = _private_ipv4(arguments.station_ipv4)
    camera = Camera(arguments.session_dir, host)
    result: dict[str, object] = {"action": arguments.action, "nor_writes": False, "ok": True}
    if arguments.action == "preflight":
        snapshot = baseline(arguments.session_dir, host)
        remote_preflight(camera)
        result["available_proxy_kib"] = snapshot["memory"]["available_proxy_kib"]
    elif arguments.action == "stage":
        if arguments.artifact is None or arguments.expected_sha256 is None:
            parser.error("stage requires --artifact and --expected-sha256")
        supervisor = Path(__file__).resolve().parents[1] / "components/raptor-rwd/S13prudynt-rwd"
        payload, _ = validate_artifact(
            arguments.artifact,
            expected_sha256=arguments.expected_sha256,
            supervisor=supervisor,
        )
        baseline(arguments.session_dir, host)
        remote_preflight(camera)
        stage(camera, payload)
        result["artifact_sha256"] = arguments.expected_sha256
    elif arguments.action == "start":
        baseline(arguments.session_dir, host)
        remote_preflight(camera)
        start(camera)
    elif arguments.action == "stop":
        stop(camera)
    else:
        result["metrics"] = rwd_metrics(camera, arguments.session_dir, host)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        RecoveryApHostError,
        RuntimeDiagnosticsError,
        RaptorRwdRuntimeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"error": str(exc), "nor_writes": False, "ok": False}, sort_keys=True))
        raise SystemExit(2)
