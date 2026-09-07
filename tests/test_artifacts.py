from __future__ import annotations

import binascii
import functools
import lzma
import random
import struct
import unittest

from installer.artifacts import (
    EXPECTED_KERNEL_ENTRY,
    EXPECTED_KERNEL_LOAD,
    SOURCE_DATE_EPOCH,
    ArtifactError,
    validate_squashfs,
    validate_uimage,
)


@functools.lru_cache(maxsize=None)
def test_uimage(
    marker: bytes = b"", *, entry_point: int = EXPECTED_KERNEL_ENTRY
) -> bytes:
    block = random.Random(0).randbytes(0xC0000)
    filler_size = 0x3A0000 - len(marker) - 1
    filler = (block * ((filler_size + len(block) - 1) // len(block)))[:filler_size]
    expanded = marker + b"\0" + filler
    payload = lzma.compress(expanded, format=lzma.FORMAT_ALONE)
    name = b"test-stage1".ljust(32, b"\0")
    header = struct.pack(
        ">7I4B32s",
        0x27051956,
        0,
        SOURCE_DATE_EPOCH,
        len(payload),
        EXPECTED_KERNEL_LOAD,
        entry_point,
        binascii.crc32(payload) & 0xFFFFFFFF,
        5,
        5,
        2,
        3,
        name,
    )
    header_crc = binascii.crc32(header) & 0xFFFFFFFF
    header = header[:4] + struct.pack(">I", header_crc) + header[8:]
    return header + payload


def test_squashfs(size: int = 64) -> bytes:
    raw = bytearray(max(size, 48))
    raw[:4] = b"hsqs"
    struct.pack_into("<Q", raw, 40, len(raw))
    return bytes(raw)


class ArtifactTests(unittest.TestCase):
    def test_uimage_metadata_crcs_lzma_and_entry_pass(self) -> None:
        info = validate_uimage(test_uimage())
        self.assertEqual(info.entry_point, EXPECTED_KERNEL_ENTRY)
        self.assertGreater(info.expanded_size, 0)

    def test_uimage_can_accept_a_valid_kernel_specific_entry(self) -> None:
        info = validate_uimage(
            test_uimage(entry_point=0x80010100), expected_entry=None
        )
        self.assertEqual(info.entry_point, 0x80010100)

    def test_changed_uimage_payload_fails(self) -> None:
        raw = bytearray(test_uimage())
        raw[-1] ^= 1
        with self.assertRaises(ArtifactError):
            validate_uimage(bytes(raw))

    def test_little_endian_squashfs_passes(self) -> None:
        self.assertEqual(validate_squashfs(test_squashfs()), 64)

    def test_nonzero_trailing_squashfs_data_fails(self) -> None:
        raw = bytearray(test_squashfs(128))
        struct.pack_into("<Q", raw, 40, 64)
        raw[-1] = 1
        with self.assertRaisesRegex(ArtifactError, "trailing"):
            validate_squashfs(bytes(raw))


if __name__ == "__main__":
    unittest.main()
