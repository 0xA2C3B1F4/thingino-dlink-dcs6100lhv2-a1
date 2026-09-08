#!/usr/bin/env python3
"""Produce a fail-closed media preflight from Linux block-device metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.platform.media_preflight_common import PreflightError, atomic_write_new


ROOT = Path(__file__).resolve().parents[2]
WHOLE_DEVICE = re.compile(r"/dev/(?:sd[a-z]+|mmcblk(?:0|[1-9][0-9]*))")


def _flag(value: object) -> bool | None:
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False"):
        return False
    return None


def _flatten(nodes: object) -> list[dict[str, object]]:
    if not isinstance(nodes, list):
        raise PreflightError("lsblk result does not contain a device list")
    result: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise PreflightError("lsblk result contains an invalid device")
        result.append(node)
        children = node.get("children", [])
        if children:
            result.extend(_flatten(children))
    return result


def _single_findmnt(document: dict[str, object]) -> dict[str, object]:
    filesystems = document.get("filesystems")
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise PreflightError("findmnt did not resolve one mounted filesystem")
    filesystem = filesystems[0]
    if not isinstance(filesystem, dict):
        raise PreflightError("findmnt result is invalid")
    return filesystem


def _mountpoints(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _device_path(node: dict[str, object]) -> str | None:
    value = node.get("path")
    return value if isinstance(value, str) else None


def _whole_for_source(nodes: list[dict[str, object]], source: str) -> str:
    source = source.split("[", 1)[0]
    by_name = {
        str(node.get("kname")): node
        for node in nodes
        if isinstance(node.get("kname"), str)
    }
    current = next((node for node in nodes if _device_path(node) == source), None)
    if current is None:
        raise PreflightError("cannot resolve a protected filesystem backing device")
    seen: set[str] = set()
    while isinstance(current.get("pkname"), str) and current["pkname"]:
        parent = str(current["pkname"])
        if parent in seen or parent not in by_name:
            raise PreflightError("block-device parent chain is invalid")
        seen.add(parent)
        current = by_name[parent]
    path = _device_path(current)
    if path is None or current.get("type") != "disk":
        raise PreflightError("protected filesystem does not resolve to a whole disk")
    return path


def build_preflight_document(
    *,
    whole_device: str,
    mount_root: Path,
    lsblk_document: dict[str, object],
    findmnt_document: dict[str, object],
    udev_properties: dict[str, str],
    protected_whole_devices: set[str],
) -> dict[str, object]:
    if WHOLE_DEVICE.fullmatch(whole_device) is None:
        raise PreflightError("selected device is not a reviewed Linux whole-disk node")
    resolved_root = mount_root.resolve(strict=True)
    if mount_root.is_symlink() or resolved_root == Path("/") or not resolved_root.is_dir():
        raise PreflightError("selected mount root is not a safe real directory")
    nodes = _flatten(lsblk_document.get("blockdevices"))
    whole = next((node for node in nodes if _device_path(node) == whole_device), None)
    if whole is None or whole.get("type") != "disk" or whole.get("pkname") not in (None, ""):
        raise PreflightError("selected device is not the resolved whole disk")
    if whole_device in protected_whole_devices:
        raise PreflightError("selected device contains a protected filesystem")
    removable = _flag(whole.get("rm"))
    readonly = _flag(whole.get("ro"))
    transport = str(whole.get("tran") or "").lower()
    udev_bus = udev_properties.get("ID_BUS", "").lower()
    flash_sd = udev_properties.get("ID_DRIVE_FLASH_SD") == "1"
    if (
        removable is not True
        or readonly is not False
        or not (transport in {"usb", "mmc"} or udev_bus in {"usb", "mmc"} or flash_sd)
    ):
        raise PreflightError("selected whole disk is not writable removable physical media")

    mounted = _single_findmnt(findmnt_document)
    source = mounted.get("source")
    target = mounted.get("target")
    options = mounted.get("options")
    if not isinstance(source, str) or not isinstance(target, str) or not isinstance(options, str):
        raise PreflightError("mounted filesystem identity is incomplete")
    partition = next((node for node in nodes if _device_path(node) == source), None)
    whole_name = whole.get("kname")
    expected_partition = (
        re.compile(re.escape(whole_device) + r"p[1-9][0-9]*")
        if whole_device.startswith("/dev/mmcblk")
        else re.compile(re.escape(whole_device) + r"[1-9][0-9]*")
    )
    if (
        partition is None
        or partition.get("type") != "part"
        or partition.get("pkname") != whole_name
        or expected_partition.fullmatch(source) is None
        or target != str(resolved_root)
        or mounted.get("fstype") != "vfat"
        or "rw" not in options.split(",")
        or partition.get("fstype") != "vfat"
        or str(partition.get("fsver") or "").upper() != "FAT32"
        or _flag(partition.get("ro")) is not False
        or str(resolved_root) not in _mountpoints(partition.get("mountpoints"))
    ):
        raise PreflightError("mounted FAT32 volume does not belong to the selected disk")

    capacity = whole.get("size")
    model = whole.get("model") or udev_properties.get("ID_MODEL_FROM_DATABASE") or udev_properties.get("ID_MODEL")
    if not isinstance(capacity, int) or capacity <= 0 or not isinstance(model, str) or not model.strip():
        raise PreflightError("selected media identity is incomplete")
    return {
        "ambiguous": False,
        "capacity_bytes": capacity,
        "external": True,
        "filesystem": "fat32",
        "host_platform": "linux",
        "media_uuid": partition.get("uuid") or "",
        "model": model.strip(),
        "mount_root": str(resolved_root),
        "partition_device": source,
        "physical": True,
        "physical_device": whole_device,
        "schema_version": 1,
        "system_device": False,
        "writable": True,
    }


def _command(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise PreflightError(f"required Linux command is unavailable: {name}")
    return executable


def _json_command(arguments: list[str], *, label: str) -> dict[str, object]:
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise PreflightError(f"{label} could not resolve the selected media")
    try:
        document = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError(f"{label} returned invalid JSON") from exc
    if not isinstance(document, dict):
        raise PreflightError(f"{label} result is not an object")
    return document


def _findmnt(target: Path) -> dict[str, object]:
    return _json_command(
        [_command("findmnt"), "--json", "--target", str(target), "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"],
        label="findmnt",
    )


def _udev(device: str) -> dict[str, str]:
    result = subprocess.run(
        [_command("udevadm"), "info", "--query=property", "--name", device],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise PreflightError("udevadm could not resolve the selected media")
    try:
        lines = result.stdout.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise PreflightError("udevadm returned non-UTF-8 properties") from exc
    properties: dict[str, str] = {}
    for raw_line in lines:
        key, separator, value = raw_line.partition("=")
        if separator and key:
            properties[key] = value
    return properties


def create_preflight_document(*, whole_device: str, mount_root: Path) -> dict[str, object]:
    if not sys.platform.startswith("linux"):
        raise PreflightError("Linux media preflight can run only on Linux")
    if not os.path.exists(whole_device) or os.path.islink(whole_device):
        raise PreflightError("selected whole-disk node is missing or linked")
    lsblk = _json_command(
        [
            _command("lsblk"),
            "--json",
            "--bytes",
            "--paths",
            "--output",
            "NAME,KNAME,PATH,PKNAME,TYPE,SIZE,MODEL,RM,RO,TRAN,FSTYPE,FSVER,UUID,MOUNTPOINTS",
        ],
        label="lsblk",
    )
    nodes = _flatten(lsblk.get("blockdevices"))
    protected = {
        _whole_for_source(nodes, str(_single_findmnt(_findmnt(root))["source"]))
        for root in (Path("/"), ROOT)
    }
    return build_preflight_document(
        whole_device=whole_device,
        mount_root=mount_root,
        lsblk_document=lsblk,
        findmnt_document=_findmnt(mount_root),
        udev_properties=_udev(whole_device),
        protected_whole_devices=protected,
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
        atomic_write_new(
            arguments.output,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
        )
    except (OSError, PreflightError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        return 2
    print(json.dumps({**document, "ok": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
