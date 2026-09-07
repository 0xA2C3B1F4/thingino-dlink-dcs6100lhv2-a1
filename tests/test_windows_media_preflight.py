from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.media import validate_media_preflight_document
from scripts.platform.windows_media_preflight import (
    PreflightError,
    build_preflight_document,
)


class WindowsMediaPreflightTests(unittest.TestCase):
    def _inputs(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        disk = {
            "number": 3,
            "friendly_name": "TEST SD READER",
            "size": 64_000_000_000,
            "is_boot": False,
            "is_system": False,
            "is_offline": False,
            "is_read_only": False,
            "bus_type": "USB",
            "operational_status": ["Online"],
        }
        partition = {"disk_number": 3, "partition_number": 1, "drive_letter": "E"}
        volume = {
            "drive_letter": "E",
            "filesystem": "FAT32",
            "drive_type": "Removable",
            "health_status": "Healthy",
        }
        return disk, partition, volume

    def test_accepts_exact_external_physical_fat32_volume(self) -> None:
        disk, partition, volume = self._inputs()
        document = build_preflight_document(
            whole_device=r"\\.\PHYSICALDRIVE3",
            mount_root="E:\\",
            disk=disk,
            partition=partition,
            volume=volume,
            protected_disk_numbers={0},
        )
        self.assertEqual(document["host_platform"], "windows")
        self.assertEqual(document["physical_device"], r"\\.\PHYSICALDRIVE3")
        self.assertEqual(document["partition_device"], "Disk 3 Partition 1")

    def test_rejects_system_disk_and_non_fat32_volume(self) -> None:
        disk, partition, volume = self._inputs()
        with self.assertRaisesRegex(PreflightError, "protected"):
            build_preflight_document(
                whole_device=r"\\.\PHYSICALDRIVE3",
                mount_root="E:\\",
                disk=disk,
                partition=partition,
                volume=volume,
                protected_disk_numbers={3},
            )
        volume["filesystem"] = "exFAT"
        with self.assertRaisesRegex(PreflightError, "FAT32"):
            build_preflight_document(
                whole_device=r"\\.\PHYSICALDRIVE3",
                mount_root="E:\\",
                disk=disk,
                partition=partition,
                volume=volume,
                protected_disk_numbers={0},
            )

    def test_shared_loader_accepts_windows_device_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            document = {
                "ambiguous": False,
                "capacity_bytes": 64_000_000_000,
                "external": True,
                "filesystem": "fat32",
                "host_platform": "windows",
                "model": "TEST SD READER",
                "mount_root": str(root.resolve()),
                "partition_device": "Disk 3 Partition 1",
                "physical": True,
                "physical_device": r"\\.\PHYSICALDRIVE3",
                "schema_version": 1,
                "system_device": False,
                "writable": True,
            }
            preflight = validate_media_preflight_document(document, expected_root=root)
        self.assertEqual(preflight.physical_device, r"\\.\PHYSICALDRIVE3")

    def test_rejects_read_only_disk_or_partition_from_another_disk(self) -> None:
        disk, partition, volume = self._inputs()
        disk["is_read_only"] = True
        with self.assertRaisesRegex(PreflightError, "writable external"):
            build_preflight_document(
                whole_device=r"\\.\PHYSICALDRIVE3",
                mount_root="E:\\",
                disk=disk,
                partition=partition,
                volume=volume,
                protected_disk_numbers={0},
            )
        disk["is_read_only"] = False
        partition["disk_number"] = 4
        with self.assertRaisesRegex(PreflightError, "does not belong"):
            build_preflight_document(
                whole_device=r"\\.\PHYSICALDRIVE3",
                mount_root="E:\\",
                disk=disk,
                partition=partition,
                volume=volume,
                protected_disk_numbers={0},
            )


if __name__ == "__main__":
    unittest.main()
