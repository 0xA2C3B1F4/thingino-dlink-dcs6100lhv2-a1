from __future__ import annotations

import struct
import unittest

from installer.layout import TARGET
from installer.sd_package import (
    END_MARKER,
    HEADER_SIZE,
    MAX_BOOTSTRAP_PACKAGE_SIZE,
    PROJECT_HEADER,
    PackageError,
    encode_record,
    generate_bootstrap,
    matching_update_filenames,
    parse_package,
    selected_update_filename,
    validate_bootstrap,
)


class SdPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = generate_bootstrap(b"kernel", b"rootfs")

    def test_generated_package_round_trips_and_targets_only_mtd1_mtd2(self) -> None:
        package = parse_package(self.raw, require_project_header=True)
        validate_bootstrap(package)
        self.assertEqual(
            [record.flash_offset for record in package.records],
            [TARGET.partition(1).offset, TARGET.partition(2).offset],
        )
        self.assertLessEqual(len(self.raw), MAX_BOOTSTRAP_PACKAGE_SIZE)
        self.assertTrue(all(record.flash_offset >= TARGET.partition(0).end for record in package.records))

    def test_payloads_are_padded_with_ff_and_checksummed(self) -> None:
        package = parse_package(self.raw)
        self.assertEqual(package.records[0].payload, b"kernel\xff\xff")
        self.assertEqual(package.records[1].payload, b"rootfs\xff\xff")

    def test_rejects_short_missing_or_trailing_feof(self) -> None:
        with self.assertRaises(PackageError):
            parse_package(b"\x00" * (HEADER_SIZE + 3))
        with self.assertRaises(PackageError):
            parse_package(self.raw[:-4] + b"NOPE")
        with self.assertRaises(PackageError):
            parse_package(self.raw + b"trailing")

    def test_rejects_incomplete_header(self) -> None:
        malformed = PROJECT_HEADER + b"\xff" * 12 + END_MARKER
        with self.assertRaisesRegex(PackageError, "incomplete record header"):
            parse_package(malformed)

    def test_rejects_wrong_checksum(self) -> None:
        malformed = bytearray(self.raw)
        malformed[HEADER_SIZE + 0x20] ^= 1
        with self.assertRaisesRegex(PackageError, "checksum mismatch"):
            parse_package(bytes(malformed))

    def test_rejects_nonzero_ignored_high_bits(self) -> None:
        malformed = bytearray(self.raw)
        struct.pack_into(">I", malformed, HEADER_SIZE + 0x10, 1)
        with self.assertRaisesRegex(PackageError, "high bits"):
            parse_package(bytes(malformed))

    def test_rejects_wrong_header_for_public_bootstrap(self) -> None:
        malformed = b"X" + self.raw[1:]
        package = parse_package(malformed)
        with self.assertRaisesRegex(PackageError, "public header"):
            validate_bootstrap(package)

    def test_rejects_mtd0_overlap(self) -> None:
        malformed = b"".join(
            (
                PROJECT_HEADER,
                encode_record(TARGET.partition(0), b"boot"),
                encode_record(TARGET.partition(2), b"root"),
                END_MARKER,
            )
        )
        with self.assertRaisesRegex(PackageError, "mtd0"):
            validate_bootstrap(parse_package(malformed))

    def test_rejects_duplicate_or_overlapping_records(self) -> None:
        malformed = b"".join(
            (
                PROJECT_HEADER,
                encode_record(TARGET.partition(1), b"one"),
                encode_record(TARGET.partition(1), b"two"),
                END_MARKER,
            )
        )
        with self.assertRaisesRegex(PackageError, "overlapping"):
            validate_bootstrap(parse_package(malformed))

    def test_rejects_wrong_record_order(self) -> None:
        malformed = b"".join(
            (
                PROJECT_HEADER,
                encode_record(TARGET.partition(2), b"root"),
                encode_record(TARGET.partition(1), b"kernel"),
                END_MARKER,
            )
        )
        with self.assertRaisesRegex(PackageError, "wrong offset"):
            validate_bootstrap(parse_package(malformed))

    def test_rejects_payload_larger_than_erase_span(self) -> None:
        malformed = bytearray(
            generate_bootstrap(b"K" * (0x10000 + 4), b"rootfs")
        )
        struct.pack_into(">I", malformed, HEADER_SIZE + 0x0C, 0x10000)
        package = parse_package(bytes(malformed))
        with self.assertRaisesRegex(PackageError, "larger than"):
            validate_bootstrap(package)

    def test_rejects_unsafe_whole_file_load(self) -> None:
        with self.assertRaisesRegex(PackageError, "whole-file load cap"):
            parse_package(
                self.raw + b"X" * MAX_BOOTSTRAP_PACKAGE_SIZE,
                max_size=MAX_BOOTSTRAP_PACKAGE_SIZE,
            )

    def test_stock_filename_selection_is_exactly_reproduced(self) -> None:
        names = [
            "DCS6100LHV2Ax_FW001_SD.bin",
            "not-an-update.bin",
            "DCS6100LHV2Ax_FW999_SD.bin",
            "dcs6100lhv2ax_fw999_sd.bin",
        ]
        self.assertEqual(len(matching_update_filenames(names)), 2)
        self.assertEqual(selected_update_filename(names), names[2])


if __name__ == "__main__":
    unittest.main()
