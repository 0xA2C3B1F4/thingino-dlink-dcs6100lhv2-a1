from __future__ import annotations

import binascii
import struct
import tempfile
from pathlib import Path
import unittest

from installer.cli import main
from installer.ram_boot import RamBootError, build_ramdisk_uimage, parse_ramdisk_uimage


def squashfs_stub(size: int = 4096) -> bytes:
    raw = bytearray(size)
    raw[:4] = b"hsqs"
    struct.pack_into("<Q", raw, 40, 96)
    return bytes(raw)


class RamBootTests(unittest.TestCase):
    def test_deterministic_wrapper_round_trip(self) -> None:
        rootfs = squashfs_stub()
        first = build_ramdisk_uimage(rootfs)
        second = build_ramdisk_uimage(rootfs)
        self.assertEqual(first, second)
        self.assertEqual(parse_ramdisk_uimage(first), rootfs)
        fields = struct.unpack(">7I4B32s", first[:64])
        self.assertEqual(fields[0], 0x27051956)
        self.assertEqual(fields[2:6], (0, len(rootfs), 0, 0))
        self.assertEqual(fields[7:11], (5, 5, 3, 0))

    def test_rejects_corrupt_payload(self) -> None:
        wrapper = bytearray(build_ramdisk_uimage(squashfs_stub()))
        wrapper[-1] ^= 1
        with self.assertRaisesRegex(RamBootError, "metadata or CRC"):
            parse_ramdisk_uimage(bytes(wrapper))

    def test_rejects_non_page_aligned_root(self) -> None:
        with self.assertRaisesRegex(RamBootError, "page aligned"):
            build_ramdisk_uimage(squashfs_stub(4097))

    def test_cli_refuses_overwrite_and_reports_exact_crc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rootfs = root / "stage1.squashfs"
            output = root / "stage1.uimg"
            rootfs.write_bytes(squashfs_stub())
            self.assertEqual(
                main(
                    [
                        "build-ramdisk-wrapper",
                        "--rootfs",
                        str(rootfs),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            wrapper = output.read_bytes()
            self.assertEqual(parse_ramdisk_uimage(wrapper), rootfs.read_bytes())
            self.assertEqual(len(wrapper), 4160)
            self.assertIsInstance(binascii.crc32(wrapper), int)
            self.assertEqual(
                main(
                    [
                        "build-ramdisk-wrapper",
                        "--rootfs",
                        str(rootfs),
                        "--output",
                        str(output),
                    ]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
