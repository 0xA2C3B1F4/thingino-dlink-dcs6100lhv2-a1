from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.platform import linux_media_preflight
from scripts.platform.linux_media_preflight import (
    PreflightError,
    build_preflight_document,
)


class LinuxMediaPreflightTests(unittest.TestCase):
    def test_udev_non_utf8_is_a_fail_closed_preflight_error(self) -> None:
        completed = mock.Mock(returncode=0, stdout=b"ID_BUS=usb\n\xff", stderr=b"")
        with (
            mock.patch.object(linux_media_preflight, "_command", return_value="udevadm"),
            mock.patch.object(linux_media_preflight.subprocess, "run", return_value=completed),
        ):
            with self.assertRaisesRegex(PreflightError, "non-UTF-8"):
                linux_media_preflight._udev("/dev/sdb")

    def _inputs(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        lsblk = {
            "blockdevices": [
                {
                    "kname": "sdb",
                    "path": "/dev/sdb",
                    "pkname": None,
                    "type": "disk",
                    "size": 64_000_000_000,
                    "model": "TEST USB CARD",
                    "rm": True,
                    "ro": False,
                    "tran": "usb",
                    "children": [
                        {
                            "kname": "sdb1",
                            "path": "/dev/sdb1",
                            "pkname": "sdb",
                            "type": "part",
                            "size": 63_000_000_000,
                            "rm": True,
                            "ro": False,
                            "fstype": "vfat",
                            "fsver": "FAT32",
                            "uuid": "1234-ABCD",
                            "mountpoints": [str(root.resolve())],
                        }
                    ],
                }
            ]
        }
        findmnt = {
            "filesystems": [
                {
                    "source": "/dev/sdb1",
                    "target": str(root.resolve()),
                    "fstype": "vfat",
                    "options": "rw,nosuid,nodev",
                }
            ]
        }
        return lsblk, findmnt

    def test_accepts_exact_removable_usb_fat32_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            lsblk, findmnt = self._inputs(root)
            document = build_preflight_document(
                whole_device="/dev/sdb",
                mount_root=root,
                lsblk_document=lsblk,
                findmnt_document=findmnt,
                udev_properties={"ID_BUS": "usb"},
                protected_whole_devices={"/dev/sda"},
            )
        self.assertEqual(document["host_platform"], "linux")
        self.assertEqual(document["physical_device"], "/dev/sdb")
        self.assertEqual(document["partition_device"], "/dev/sdb1")
        self.assertEqual(document["media_uuid"], "1234-ABCD")

    def test_rejects_protected_or_non_fat32_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            lsblk, findmnt = self._inputs(root)
            with self.assertRaisesRegex(PreflightError, "protected"):
                build_preflight_document(
                    whole_device="/dev/sdb",
                    mount_root=root,
                    lsblk_document=lsblk,
                    findmnt_document=findmnt,
                    udev_properties={"ID_BUS": "usb"},
                    protected_whole_devices={"/dev/sdb"},
                )
            lsblk["blockdevices"][0]["children"][0]["fsver"] = "FAT16"
            with self.assertRaisesRegex(PreflightError, "FAT32"):
                build_preflight_document(
                    whole_device="/dev/sdb",
                    mount_root=root,
                    lsblk_document=lsblk,
                    findmnt_document=findmnt,
                    udev_properties={"ID_BUS": "usb"},
                    protected_whole_devices={"/dev/sda"},
                )

    def test_rejects_internal_disk_or_partition_from_another_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            lsblk, findmnt = self._inputs(root)
            lsblk["blockdevices"][0]["rm"] = False
            with self.assertRaisesRegex(PreflightError, "removable"):
                build_preflight_document(
                    whole_device="/dev/sdb",
                    mount_root=root,
                    lsblk_document=lsblk,
                    findmnt_document=findmnt,
                    udev_properties={"ID_BUS": "usb"},
                    protected_whole_devices={"/dev/sda"},
                )
            lsblk["blockdevices"][0]["rm"] = True
            lsblk["blockdevices"][0]["children"][0]["pkname"] = "sdc"
            with self.assertRaisesRegex(PreflightError, "does not belong"):
                build_preflight_document(
                    whole_device="/dev/sdb",
                    mount_root=root,
                    lsblk_document=lsblk,
                    findmnt_document=findmnt,
                    udev_properties={"ID_BUS": "usb"},
                    protected_whole_devices={"/dev/sda"},
                )


if __name__ == "__main__":
    unittest.main()
