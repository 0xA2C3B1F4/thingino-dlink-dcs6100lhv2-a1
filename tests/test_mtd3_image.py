from __future__ import annotations

import struct
import unittest

from installer.layout import TARGET
from installer.mtd3_image import (
    FOOTER_SIZE,
    Mtd3ImageError,
    build_personal_mtd3_image,
    validate_personal_mtd3_image,
)


def squashfs_stub() -> bytes:
    raw = bytearray(4096)
    raw[:4] = b"hsqs"
    struct.pack_into("<Q", raw, 40, 96)
    return bytes(raw)


class PersonalMtd3ImageTests(unittest.TestCase):
    def test_build_is_exact_partition_sized_and_roundtrips(self) -> None:
        built = build_personal_mtd3_image(squashfs_stub())
        self.assertEqual(len(built.raw), TARGET.partition(3).size)
        self.assertEqual(validate_personal_mtd3_image(built.raw), built)
        self.assertEqual(
            set(built.raw[len(squashfs_stub()) : -FOOTER_SIZE]),
            {0xFF},
        )

    def test_payload_and_erased_gap_corruption_are_rejected(self) -> None:
        built = build_personal_mtd3_image(squashfs_stub())
        payload = bytearray(built.raw)
        payload[100] ^= 1
        with self.assertRaisesRegex(Mtd3ImageError, "hash mismatch"):
            validate_personal_mtd3_image(bytes(payload))

        gap = bytearray(built.raw)
        gap[5000] = 0
        with self.assertRaisesRegex(Mtd3ImageError, "gap is not erased"):
            validate_personal_mtd3_image(bytes(gap))

    def test_footer_and_size_corruption_are_rejected(self) -> None:
        built = build_personal_mtd3_image(squashfs_stub())
        with self.assertRaisesRegex(Mtd3ImageError, "wrong exact size"):
            validate_personal_mtd3_image(built.raw[:-1])
        footer = bytearray(built.raw)
        footer[-1] = 0
        with self.assertRaisesRegex(Mtd3ImageError, "footer framing"):
            validate_personal_mtd3_image(bytes(footer))


if __name__ == "__main__":
    unittest.main()
