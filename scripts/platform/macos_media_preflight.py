#!/usr/bin/env python3
"""Produce a fail-closed media preflight from macOS diskutil plists."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import sys
from xml.parsers import expat
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.platform.media_preflight_common import PreflightError, atomic_write_new


ROOT = Path(__file__).resolve().parents[2]
WHOLE_DISK = re.compile(r"/dev/(disk[1-9][0-9]*)")


def _diskutil_info(target: str | Path) -> dict[str, object]:
    result = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise PreflightError("diskutil could not resolve the selected media")
    try:
        document = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, expat.ExpatError) as exc:
        raise PreflightError("diskutil returned an invalid property list") from exc
    if not isinstance(document, dict):
        raise PreflightError("diskutil result is not a dictionary")
    return document


def _containing_volume_info(target: Path) -> dict[str, object]:
    candidate = target.resolve(strict=True)
    while True:
        try:
            return _diskutil_info(candidate)
        except PreflightError:
            parent = candidate.parent
            if parent == candidate:
                raise
            candidate = parent


def _whole_identifiers(info: dict[str, object]) -> set[str]:
    candidates: set[str] = set()
    for key in ("DeviceIdentifier", "ParentWholeDisk", "APFSContainerReference"):
        value = info.get(key)
        if isinstance(value, str):
            match = re.match(r"(disk[0-9]+)", value)
            if match:
                candidates.add(match.group(1))
    stores = info.get("APFSPhysicalStores", [])
    if isinstance(stores, list):
        for store in stores:
            if not isinstance(store, dict):
                continue
            value = store.get("APFSPhysicalStore")
            if isinstance(value, str):
                match = re.match(r"(disk[0-9]+)", value)
                if match:
                    candidates.add(match.group(1))
    return candidates


def build_preflight_document(
    *,
    whole_device: str,
    mount_root: Path,
    whole: dict[str, object],
    volume: dict[str, object],
    protected_whole_disks: set[str],
) -> dict[str, object]:
    match = WHOLE_DISK.fullmatch(whole_device)
    if match is None:
        raise PreflightError("selected device is not an exact whole-disk node")
    whole_identifier = match.group(1)
    resolved_root = mount_root.resolve(strict=True)
    if mount_root.is_symlink() or resolved_root == Path("/") or not resolved_root.is_dir():
        raise PreflightError("selected mount root is not a safe real directory")
    if whole_identifier in protected_whole_disks:
        raise PreflightError("selected device contains a protected filesystem")
    if whole.get("DeviceNode") != whole_device or whole.get("WholeDisk") is not True:
        raise PreflightError("selected device is not the resolved whole disk")
    if (
        whole.get("Internal") is not False
        or whole.get("OSInternalMedia") is not False
        or whole.get("VirtualOrPhysical") != "Physical"
        or whole.get("RemovableMediaOrExternalDevice") is not True
        or whole.get("Writable") is not True
        or whole.get("WritableMedia") is not True
    ):
        raise PreflightError("selected whole disk is not writable external physical media")
    partition_device = volume.get("DeviceNode")
    if (
        volume.get("ParentWholeDisk") != whole_identifier
        or not isinstance(partition_device, str)
        or not partition_device.startswith(whole_device + "s")
        or volume.get("MountPoint") != str(resolved_root)
        or volume.get("FilesystemType") != "msdos"
        or volume.get("Writable") is not True
        or volume.get("WritableVolume") is not True
    ):
        raise PreflightError("mounted FAT32 volume does not belong to the selected disk")
    capacity = whole.get("TotalSize")
    model = whole.get("DeviceModel") or whole.get("MediaName")
    if not isinstance(capacity, int) or capacity <= 0 or not isinstance(model, str) or not model:
        raise PreflightError("selected media identity is incomplete")
    return {
        "ambiguous": False,
        "capacity_bytes": capacity,
        "external": True,
        "filesystem": "msdos",
        "host_platform": "darwin",
        "model": model,
        "mount_root": str(resolved_root),
        "partition_device": partition_device,
        "physical": True,
        "physical_device": whole_device,
        "schema_version": 1,
        "system_device": False,
        "writable": True,
    }


def create_preflight_document(*, whole_device: str, mount_root: Path) -> dict[str, object]:
    """Inspect one mounted macOS SD card without formatting or writing it."""

    whole = _diskutil_info(whole_device)
    volume = _diskutil_info(mount_root)
    protected: set[str] = set()
    for protected_root in (Path("/"), ROOT):
        protected.update(_whole_identifiers(_containing_volume_info(protected_root)))
    return build_preflight_document(
        whole_device=whole_device,
        mount_root=mount_root,
        whole=whole,
        volume=volume,
        protected_whole_disks=protected,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whole-device", required=True)
    parser.add_argument("--mount-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        document = create_preflight_document(
            whole_device=arguments.whole_device,
            mount_root=arguments.mount_root,
        )
        raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        atomic_write_new(arguments.output, raw)
    except (OSError, PreflightError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        return 2
    print(json.dumps({**document, "ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
