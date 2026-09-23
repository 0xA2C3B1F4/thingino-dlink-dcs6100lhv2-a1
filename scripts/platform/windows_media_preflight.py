#!/usr/bin/env python3
"""Produce a fail-closed media preflight from Windows Storage cmdlets."""

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


PHYSICAL_DEVICE = re.compile(r"\\\\\.\\PHYSICALDRIVE(0|[1-9][0-9]*)", re.IGNORECASE)
MOUNT_ROOT = re.compile(r"([A-Za-z]):\\")
POWERSHELL_QUERY = r"""
& {
  $ErrorActionPreference = 'Stop'
  $DiskNumber = [int]$env:DCS6100_DISK_NUMBER
  $DriveLetter = [string]$env:DCS6100_DRIVE_LETTER
  $disk = Get-Disk -Number $DiskNumber
  $partition = Get-Partition -DriveLetter $DriveLetter
  $volume = Get-Volume -DriveLetter $DriveLetter
  $protected = @(Get-Disk | Where-Object { $_.IsBoot -or $_.IsSystem } | ForEach-Object { [int]$_.Number })
  [pscustomobject]@{
    disk = [pscustomobject]@{
      number = [int]$disk.Number
      friendly_name = [string]$disk.FriendlyName
      size = [long]$disk.Size
      is_boot = [bool]$disk.IsBoot
      is_system = [bool]$disk.IsSystem
      is_offline = [bool]$disk.IsOffline
      is_read_only = [bool]$disk.IsReadOnly
      bus_type = [string]$disk.BusType
      operational_status = @($disk.OperationalStatus | ForEach-Object { [string]$_ })
    }
    partition = [pscustomobject]@{
      disk_number = [int]$partition.DiskNumber
      partition_number = [int]$partition.PartitionNumber
      drive_letter = [string]$partition.DriveLetter
    }
    volume = [pscustomobject]@{
      drive_letter = [string]$volume.DriveLetter
      filesystem = [string]$volume.FileSystem
      drive_type = [string]$volume.DriveType
      health_status = [string]$volume.HealthStatus
    }
    protected_disk_numbers = $protected
  } | ConvertTo-Json -Depth 5 -Compress
}
"""


def _mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PreflightError(f"Windows {label} result is invalid")
    return value


def build_preflight_document(
    *,
    whole_device: str,
    mount_root: str,
    disk: dict[str, object],
    partition: dict[str, object],
    volume: dict[str, object],
    protected_disk_numbers: set[int],
) -> dict[str, object]:
    match = PHYSICAL_DEVICE.fullmatch(whole_device)
    root_match = MOUNT_ROOT.fullmatch(mount_root)
    if match is None:
        raise PreflightError("selected device is not an exact Windows physical-drive ID")
    if root_match is None:
        raise PreflightError("selected mount root is not an exact Windows drive root")
    disk_number = int(match.group(1))
    drive_letter = root_match.group(1).upper()
    canonical_device = rf"\\.\PHYSICALDRIVE{disk_number}"
    if disk_number in protected_disk_numbers or disk.get("is_boot") is True or disk.get("is_system") is True:
        raise PreflightError("selected device contains a protected filesystem")
    statuses = disk.get("operational_status")
    if isinstance(statuses, str):
        statuses = [statuses]
    if (
        disk.get("number") != disk_number
        or disk.get("is_offline") is not False
        or disk.get("is_read_only") is not False
        or str(disk.get("bus_type") or "").upper() not in {"USB", "SD", "MMC"}
        or not isinstance(statuses, list)
        or "Online" not in statuses
    ):
        raise PreflightError("selected disk is not writable external physical media")
    partition_number = partition.get("partition_number")
    if (
        partition.get("disk_number") != disk_number
        or not isinstance(partition_number, int)
        or partition_number <= 0
        or str(partition.get("drive_letter") or "").upper() != drive_letter
        or str(volume.get("drive_letter") or "").upper() != drive_letter
        or str(volume.get("filesystem") or "").upper() != "FAT32"
        or str(volume.get("drive_type") or "") not in {"Removable", "Fixed"}
        or str(volume.get("health_status") or "") != "Healthy"
    ):
        raise PreflightError("mounted FAT32 volume does not belong to the selected disk")
    capacity = disk.get("size")
    model = disk.get("friendly_name")
    if not isinstance(capacity, int) or capacity <= 0 or not isinstance(model, str) or not model.strip():
        raise PreflightError("selected media identity is incomplete")
    return {
        "ambiguous": False,
        "capacity_bytes": capacity,
        "external": True,
        "filesystem": "fat32",
        "host_platform": "windows",
        "model": model.strip(),
        "mount_root": f"{drive_letter}:\\",
        "partition_device": f"Disk {disk_number} Partition {partition_number}",
        "physical": True,
        "physical_device": canonical_device,
        "schema_version": 1,
        "system_device": False,
        "writable": True,
    }


def _powershell() -> str:
    for name in ("pwsh.exe", "powershell.exe", "pwsh", "powershell"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    raise PreflightError("PowerShell is unavailable")


def create_preflight_document(*, whole_device: str, mount_root: Path) -> dict[str, object]:
    if sys.platform not in {"win32", "cygwin"}:
        raise PreflightError("Windows media preflight can run only on Windows")
    match = PHYSICAL_DEVICE.fullmatch(whole_device)
    if match is None:
        raise PreflightError("selected device is not an exact Windows physical-drive ID")
    resolved = mount_root.resolve(strict=True)
    if mount_root.is_symlink() or not resolved.is_dir() or resolved != Path(resolved.anchor):
        raise PreflightError("selected mount root is not an exact real drive root")
    root_match = MOUNT_ROOT.fullmatch(str(resolved))
    if root_match is None:
        raise PreflightError("selected mount root is not an exact Windows drive root")
    result = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            POWERSHELL_QUERY,
        ],
        env={
            **os.environ,
            "DCS6100_DISK_NUMBER": str(int(match.group(1))),
            "DCS6100_DRIVE_LETTER": root_match.group(1).upper(),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise PreflightError("PowerShell Storage cmdlets could not resolve the selected media")
    try:
        document = json.loads(result.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreflightError("PowerShell returned invalid storage JSON") from exc
    if not isinstance(document, dict):
        raise PreflightError("PowerShell storage result is not an object")
    protected = document.get("protected_disk_numbers")
    if isinstance(protected, int):
        protected = [protected]
    if not isinstance(protected, list) or any(not isinstance(value, int) for value in protected):
        raise PreflightError("PowerShell protected-disk result is invalid")
    return build_preflight_document(
        whole_device=whole_device,
        mount_root=str(resolved),
        disk=_mapping(document.get("disk"), label="disk"),
        partition=_mapping(document.get("partition"), label="partition"),
        volume=_mapping(document.get("volume"), label="volume"),
        protected_disk_numbers=set(protected),
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
