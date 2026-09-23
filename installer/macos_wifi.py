"""Secret-safe macOS association with one private recovery-AP session."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path


class MacosWifiError(ValueError):
    """The macOS recovery Wi-Fi helper failed closed."""


def wait_for_control(host: str, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, 22), timeout=1.0):
                return
        except OSError:
            time.sleep(0.5)
    raise MacosWifiError("recovery AP control did not become reachable")


def read_private_file(path: Path, *, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise MacosWifiError(f"cannot read private {label}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= limit
            or metadata.st_mode & 0o077
        ):
            raise MacosWifiError(f"private {label} violates its file policy")
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise MacosWifiError(f"private {label} changed while being read")
    return raw


def _helper_command(helper: Path, label: str) -> tuple[list[str], str]:
    if helper.is_symlink() or not helper.is_file():
        raise MacosWifiError(f"macOS {label} Wi-Fi helper is invalid")
    if helper.suffix == ".swift":
        if helper.stat().st_size > 16 * 1024:
            raise MacosWifiError(f"macOS {label} Wi-Fi helper is invalid")
        swift = Path("/usr/bin/swift")
        if not swift.is_file() or not os.access(swift, os.X_OK):
            raise MacosWifiError("macOS Swift interpreter is unavailable")
        module_cache = Path(tempfile.gettempdir()) / "thingino-dlink-swift-module-cache"
        return [
            str(swift),
            "-module-cache-path",
            str(module_cache),
            str(helper),
        ], "swift-stdin"
    if helper.stat().st_size > 256 * 1024 or not os.access(helper, os.X_OK):
        raise MacosWifiError(f"macOS {label} Wi-Fi helper is not executable")
    return [str(helper)], "helper-stdin"


def recovery_input(session_dir: Path) -> tuple[str, str, str]:
    """Read and validate one private recovery network without logging it."""

    manifest_raw = read_private_file(
        session_dir / "host/session.json", label="session manifest", limit=4096
    )
    psk_raw = read_private_file(
        session_dir / "media/RECOVERY/AP.PSK", label="setup AP key", limit=128
    )
    try:
        manifest = json.loads(manifest_raw)
        psk = psk_raw.decode("ascii").strip()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MacosWifiError("private recovery Wi-Fi input is invalid") from exc
    ssid = manifest.get("setup_ssid") if isinstance(manifest, dict) else None
    ap_address = manifest.get("ap_address") if isinstance(manifest, dict) else None
    if not isinstance(ssid, str) or re.fullmatch(r"DCS6100-[0-9a-f]{8}", ssid) is None:
        raise MacosWifiError("private recovery Wi-Fi SSID is invalid")
    if ap_address != "192.168.88.1":
        raise MacosWifiError("private recovery AP address is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", psk) is None:
        raise MacosWifiError("private recovery Wi-Fi key is invalid")
    return ssid, psk, ap_address


def join_recovery_ap(
    *,
    session_dir: Path,
    helper: Path,
    association_timeout: float = 45.0,
    control_timeout: float = 20.0,
) -> dict[str, object]:
    """Pass the unique SSID and raw WPA2 PSK only through helper stdin."""

    command, transport = _helper_command(helper, "recovery")
    ssid, psk, ap_address = recovery_input(session_dir)
    try:
        result = subprocess.run(
            command,
            input=f"{ssid}\n{psk}\n".encode("ascii"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=association_timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MacosWifiError("macOS recovery Wi-Fi association failed") from exc
    if result.returncode or result.stdout != f"joined {ssid}\n".encode("ascii"):
        raise MacosWifiError("macOS recovery Wi-Fi helper rejected association")
    wait_for_control(ap_address, timeout=control_timeout)
    return {
        "associated": True,
        "credentials_logged": False,
        "setup_ssid": ssid,
        "transport": transport,
    }


def station_input(raw: bytes) -> tuple[bytes, str]:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise MacosWifiError("private station Wi-Fi input is invalid") from exc
    lines = [line.strip() for line in text.splitlines()]
    ssids = [line[5:] for line in lines if line.startswith("ssid=")]
    psks = [line[4:] for line in lines if line.startswith("psk=")]
    if len(ssids) != 1 or len(psks) != 1 or re.fullmatch(r"[0-9a-f]{64}", psks[0]) is None:
        raise MacosWifiError("private station Wi-Fi input is invalid")
    encoded = ssids[0]
    if encoded.startswith('"') and encoded.endswith('"'):
        source = encoded[1:-1]
        value = bytearray()
        index = 0
        while index < len(source):
            character = source[index]
            if character != "\\":
                value.append(ord(character))
                index += 1
                continue
            if index + 1 >= len(source):
                raise MacosWifiError("private station SSID escape is invalid")
            escaped = source[index + 1]
            if escaped in ('"', "\\"):
                value.append(ord(escaped))
                index += 2
            elif escaped == "x" and index + 3 < len(source) and re.fullmatch(
                r"[0-9a-fA-F]{2}", source[index + 2 : index + 4]
            ):
                value.append(int(source[index + 2 : index + 4], 16))
                index += 4
            else:
                raise MacosWifiError("private station SSID escape is invalid")
        ssid = bytes(value)
    else:
        if re.fullmatch(r"[0-9a-fA-F]{2,64}", encoded) is None or len(encoded) % 2:
            raise MacosWifiError("private station SSID encoding is invalid")
        ssid = bytes.fromhex(encoded)
    if not 1 <= len(ssid) <= 32 or any(byte < 0x20 or byte == 0x7F for byte in ssid):
        raise MacosWifiError("private station SSID is outside policy")
    return ssid, psks[0]


def join_station_wifi(*, wpa_config: Path, helper: Path) -> dict[str, object]:
    """Join the configured normal WLAN with both private values on stdin."""

    command, transport = _helper_command(helper, "station")
    raw = read_private_file(wpa_config, label="station configuration", limit=4096)
    ssid, psk = station_input(raw)
    try:
        result = subprocess.run(
            command,
            input=ssid.hex().encode("ascii") + b"\n" + psk.encode("ascii") + b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MacosWifiError("macOS station Wi-Fi association failed") from exc
    if result.returncode or result.stdout != b"associated\n":
        raise MacosWifiError("macOS station Wi-Fi helper rejected association")
    return {
        "associated": True,
        "credentials_logged": False,
        "network_identity_logged": False,
        "transport": transport,
    }
