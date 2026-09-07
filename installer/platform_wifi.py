"""Portable Wi-Fi orchestration with bounded automatic and manual paths."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from .macos_wifi import (
    MacosWifiError,
    join_recovery_ap as join_recovery_ap_macos,
    join_station_wifi as join_station_wifi_macos,
    read_private_file,
    recovery_input,
    station_input,
    wait_for_control,
)


class PlatformWifiError(ValueError):
    """The host did not reach the requested private Wi-Fi network."""


def _linux_keyfile(ssid: bytes, psk: str, connection_uuid: str) -> bytes:
    try:
        text = ssid.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PlatformWifiError(
            "automatic Linux association requires an ASCII Wi-Fi name"
        ) from exc
    if re.fullmatch(r"[A-Za-z0-9 _.-]{1,32}", text) is None:
        raise PlatformWifiError(
            "automatic Linux association cannot encode this Wi-Fi name safely"
        )
    return (
        "[connection]\n"
        f"id=thingino-dlink-{connection_uuid[:8]}\n"
        f"uuid={connection_uuid}\n"
        "type=wifi\n"
        "autoconnect=false\n\n"
        "[wifi]\n"
        "mode=infrastructure\n"
        f"ssid={text}\n\n"
        "[wifi-security]\n"
        "key-mgmt=wpa-psk\n"
        f"psk={psk}\n\n"
        "[ipv4]\n"
        "method=auto\n\n"
        "[ipv6]\n"
        "method=disabled\n"
    ).encode("ascii")


def _join_linux(*, ssid: bytes, psk: str, timeout: float) -> dict[str, object]:
    nmcli = shutil.which("nmcli")
    if nmcli is None:
        raise PlatformWifiError("NetworkManager nmcli is unavailable")
    connection_uuid = str(uuid.uuid4())
    raw = _linux_keyfile(ssid, psk, connection_uuid)
    temporary_root = os.environ.get("TMPDIR")
    descriptor, filename = tempfile.mkstemp(
        prefix="thingino-dlink-wifi-", suffix=".nmconnection", dir=temporary_root
    )
    path = Path(filename)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        loaded = subprocess.run(
            [nmcli, "connection", "load", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if loaded.returncode:
            raise PlatformWifiError("NetworkManager rejected the private Wi-Fi profile")
        associated = subprocess.run(
            [nmcli, "--wait", str(max(1, int(timeout))), "connection", "up", "uuid", connection_uuid],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 2.0,
            check=False,
        )
        if associated.returncode:
            raise PlatformWifiError("NetworkManager did not associate with the private Wi-Fi network")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlatformWifiError("Linux Wi-Fi association failed") from exc
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return {
        "associated": True,
        "credentials_logged": False,
        "network_identity_logged": False,
        "transport": "networkmanager-keyfile",
    }


def _automatic_recovery_join(
    *, session_dir: Path, macos_helper: Path, platform: str, timeout: float
) -> dict[str, object]:
    if platform == "darwin":
        return join_recovery_ap_macos(
            session_dir=session_dir,
            helper=macos_helper,
            association_timeout=timeout,
            control_timeout=5.0,
        )
    if platform.startswith("linux"):
        ssid, psk, _ = recovery_input(session_dir)
        result = _join_linux(ssid=ssid.encode("ascii"), psk=psk, timeout=timeout)
        wait_for_control("192.168.88.1", timeout=5.0)
        return result
    raise PlatformWifiError(
        "automatic Wi-Fi association is unavailable on this host"
    )


def join_recovery_ap(
    *,
    session_dir: Path,
    macos_helper: Path,
    mode: str = "auto",
    platform: str | None = None,
    automatic_timeout: float = 15.0,
    manual_timeout: float = 120.0,
    notify: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Try one bounded automatic join, then wait for a manual host join."""

    if mode not in {"auto", "manual"}:
        raise PlatformWifiError("Wi-Fi mode must be auto or manual")
    try:
        ssid, _, ap_address = recovery_input(session_dir)
    except MacosWifiError as exc:
        raise PlatformWifiError("private recovery Wi-Fi input is invalid") from exc
    selected_platform = sys.platform if platform is None else platform
    if mode == "auto":
        try:
            return _automatic_recovery_join(
                session_dir=session_dir,
                macos_helper=macos_helper,
                platform=selected_platform,
                timeout=automatic_timeout,
            )
        except (MacosWifiError, PlatformWifiError):
            pass
    if notify is not None:
        notify(
            f"Join Wi-Fi network {ssid} in the host system settings; waiting up to {int(manual_timeout)} seconds"
        )
    try:
        wait_for_control(ap_address, timeout=manual_timeout)
    except MacosWifiError as exc:
        raise PlatformWifiError(
            "manual recovery Wi-Fi association did not reach pinned SSH control"
        ) from exc
    return {
        "associated": True,
        "credentials_logged": False,
        "setup_ssid": ssid,
        "transport": "manual-system-settings",
    }


def join_station_wifi(
    *,
    wpa_config: Path,
    macos_helper: Path,
    mode: str = "auto",
    platform: str | None = None,
    automatic_timeout: float = 15.0,
    notify: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Associate automatically where supported, or request a manual return."""

    if mode not in {"auto", "manual"}:
        raise PlatformWifiError("Wi-Fi mode must be auto or manual")
    selected_platform = sys.platform if platform is None else platform
    if mode == "auto":
        try:
            if selected_platform == "darwin":
                return join_station_wifi_macos(
                    wpa_config=wpa_config, helper=macos_helper
                )
            if selected_platform.startswith("linux"):
                raw = read_private_file(
                    wpa_config, label="station configuration", limit=4096
                )
                ssid, psk = station_input(raw)
                return _join_linux(ssid=ssid, psk=psk, timeout=automatic_timeout)
        except (MacosWifiError, PlatformWifiError):
            pass
    if notify is not None:
        notify(
            "Return the host to the configured normal Wi-Fi network; station health will be detected automatically"
        )
    return {
        "associated": False,
        "credentials_logged": False,
        "network_identity_logged": False,
        "transport": "manual-system-settings-pending",
    }
