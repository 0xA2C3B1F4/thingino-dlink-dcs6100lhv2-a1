"""Current removable-media choices for CLI and GUI clients; never writes."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

from scripts.platform.media_preflight import create_preflight_document, PreflightError
from .install_project import ProjectError
from .media_preflight import MediaPreflight, validate_media_preflight_document


def _query(command: list[str]) -> bytes:
    try:
        return subprocess.run(command, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectError("media_discovery_failed", "cannot enumerate current removable media") from exc


def discover_media() -> tuple[MediaPreflight, ...]:
    try:
        return _discover_media()
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ProjectError("media_discovery_failed", "host media inventory is malformed") from exc


def _discover_media() -> tuple[MediaPreflight, ...]:
    candidates = []
    if sys.platform == "darwin":
        document = plistlib.loads(_query(["diskutil", "list", "-plist", "external", "physical"]))
        for disk in document.get("AllDisksAndPartitions", []):
            whole = "/dev/" + disk["DeviceIdentifier"]
            for partition in disk.get("Partitions", []):
                info = plistlib.loads(_query(["diskutil", "info", "-plist", "/dev/" + partition["DeviceIdentifier"]]))
                if info.get("MountPoint"):
                    candidates.append((whole, Path(info["MountPoint"])))
    elif sys.platform.startswith("linux"):
        document = json.loads(_query(["lsblk", "--json", "--paths", "--output", "PATH,TYPE,MOUNTPOINT,RM,HOTPLUG"]))
        def visit(node, whole=None):
            if node.get("type") == "disk":
                whole = node.get("path")
            if whole and node.get("mountpoint"):
                candidates.append((whole, Path(node["mountpoint"])))
            for child in node.get("children", []):
                visit(child, whole)
        for node in document.get("blockdevices", []):
            visit(node)
    elif sys.platform == "win32":
        script = "Get-Partition | Where-Object DriveLetter | Select-Object DiskNumber,DriveLetter | ConvertTo-Json -Compress"
        document = json.loads(_query(["powershell", "-NoProfile", "-NonInteractive", "-Command", script]))
        for item in document if isinstance(document, list) else [document]:
            candidates.append(("\\\\.\\PHYSICALDRIVE" + str(item["DiskNumber"]), Path(item["DriveLetter"] + ":\\")))
    else:
        raise ProjectError("media_discovery_unsupported", "this host has no reviewed removable-media discovery")
    accepted = []
    for whole, root in candidates:
        try:
            preflight = create_preflight_document(whole_device=whole, mount_root=root)
            accepted.append(validate_media_preflight_document(preflight, expected_root=root))
        except (PreflightError, ValueError, OSError):
            continue
    return tuple(accepted)
