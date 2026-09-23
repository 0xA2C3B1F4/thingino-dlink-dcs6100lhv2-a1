from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.platform import macos_media_preflight
from scripts.platform.macos_media_preflight import (
    PreflightError,
    build_preflight_document,
)


class MacOSMediaPreflightTests(unittest.TestCase):
    def test_malformed_diskutil_xml_is_a_fail_closed_preflight_error(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=b'<?xml version="1.0"?><plist><dict><key>broken</dict>',
            stderr=b"",
        )
        with mock.patch.object(
            macos_media_preflight.subprocess, "run", return_value=completed
        ):
            with self.assertRaisesRegex(PreflightError, "invalid property list"):
                macos_media_preflight._diskutil_info("/dev/disk9")

    def _inputs(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        whole = {
            "DeviceNode": "/dev/disk9",
            "WholeDisk": True,
            "Internal": False,
            "OSInternalMedia": False,
            "VirtualOrPhysical": "Physical",
            "RemovableMediaOrExternalDevice": True,
            "Writable": True,
            "WritableMedia": True,
            "TotalSize": 128_000_000_000,
            "MediaName": "TEST CARD",
        }
        volume = {
            "DeviceNode": "/dev/disk9s1",
            "ParentWholeDisk": "disk9",
            "MountPoint": str(root.resolve()),
            "FilesystemType": "msdos",
            "Writable": True,
            "WritableVolume": True,
        }
        return whole, volume

    def test_accepts_exact_external_physical_fat32_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            whole, volume = self._inputs(root)
            document = build_preflight_document(
                whole_device="/dev/disk9",
                mount_root=root,
                whole=whole,
                volume=volume,
                protected_whole_disks={"disk0", "disk6"},
            )
            self.assertEqual(document["physical_device"], "/dev/disk9")
            self.assertEqual(document["partition_device"], "/dev/disk9s1")
            self.assertFalse(document["system_device"])

    def test_rejects_workspace_backing_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            whole, volume = self._inputs(root)
            with self.assertRaisesRegex(PreflightError, "protected"):
                build_preflight_document(
                    whole_device="/dev/disk9",
                    mount_root=root,
                    whole=whole,
                    volume=volume,
                    protected_whole_disks={"disk9"},
                )


if __name__ == "__main__":
    unittest.main()
