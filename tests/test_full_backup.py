from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer.full_backup import (
    FullBackupError,
    _collector_preserved_layout_document,
    capture_complete_backup,
    capture_complete_backup_from_ram_collector,
    capture_functional_backup_from_uartless_collector,
    functional_target_layout_document,
    target_layout_document,
    validate_complete_backup,
)
from installer.layout import TARGET
from installer.sd_package import generate_bootstrap, parse_package


def partition_images() -> dict[int, bytes]:
    return {
        partition.mtd: bytes([0x20 + partition.mtd]) * partition.size
        for partition in TARGET.partitions
    }


def capture(root: Path, *, parts: dict[int, bytes] | None = None) -> Path:
    images = parts or partition_images()
    output = root / "private-backup"
    capture_complete_backup(
        output_dir=output,
        confirmed_output_dir=output,
        layout=target_layout_document(),
        partition_reader=lambda _copy, mtd: images[mtd],
    )
    return output


class FullBackupTests(unittest.TestCase):
    def test_ram_collector_closed_output_becomes_canonical_backup(self) -> None:
        images = partition_images()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "DCS6100B"
            collector.mkdir()
            for copy in ("a", "b"):
                directory = collector / f"copy-{copy}"
                directory.mkdir()
                for mtd, raw in images.items():
                    (directory / f"mtd{mtd}.bin").write_bytes(raw)
            (collector / "device-layout.private.json").write_text(
                json.dumps(target_layout_document()),
                encoding="utf-8",
            )
            (collector / "CAPTURE.OK").write_bytes(
                b"duplicate_reads=complete;host_validation=required;"
                b"nor_writes=false\n"
            )
            output = root / "canonical-private-backup"
            decision = capture_complete_backup_from_ram_collector(
                collector_dir=collector,
                output_dir=output,
                confirmed_output_dir=output,
            )
            validated = validate_complete_backup(output)
        self.assertTrue(decision.duplicate_partitions_accepted)
        self.assertTrue(validated.full_flash_reconstruction_accepted)

    def test_uartless_functional_capture_accepts_only_authorized_replacements(self) -> None:
        package_raw = generate_bootstrap(b"replacement-kernel", b"replacement-rootfs")
        package = parse_package(package_raw, require_project_header=True)
        images = partition_images()
        for record, mtd in zip(package.records, (1, 2), strict=True):
            images[mtd] = record.payload + b"\xff" * (
                TARGET.partition(mtd).size - len(record.payload)
            )
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            collector = root / "DCS6100F"
            collector.mkdir()
            for copy in ("a", "b"):
                directory = collector / f"copy-{copy}"
                directory.mkdir()
                for mtd, raw in images.items():
                    (directory / f"mtd{mtd}.bin").write_bytes(raw)
            (collector / "device-layout.private.json").write_text(
                json.dumps(functional_target_layout_document()),
                encoding="utf-8",
            )
            (collector / "CAPTURE.OK").write_bytes(
                b"functional_duplicate_reads=complete;host_validation=required;"
                b"pre_capture_writes=mtd1,mtd2\n"
            )
            preserved = collector / "preserved"
            preserved.mkdir()
            (preserved / "device-layout.private.json").write_text(
                json.dumps(
                    _collector_preserved_layout_document(),
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            for mtd in (0, 4, 5):
                (preserved / f"mtd{mtd}.bin").write_bytes(images[mtd])
            vendor = collector / "vendor"
            (vendor / "files").mkdir(parents=True)
            (vendor / "vendor-bundle.private.json").write_text("{}")
            (vendor / "files/libimp.so").write_bytes(b"vendor")
            output = root / "functional-recovery"
            bundle = SimpleNamespace(
                artifacts=(SimpleNamespace(name="libimp.so", raw=b"vendor"),)
            )
            with mock.patch(
                "installer.full_backup.load_vendor_bundle", return_value=bundle
            ):
                decision = capture_functional_backup_from_uartless_collector(
                    collector_dir=collector,
                    bootstrap_package=package_raw,
                    output_dir=output,
                    confirmed_output_dir=output,
                )
            manifest = json.loads((output / "manifest.private.json").read_text())
        self.assertTrue(decision.functional_recovery_accepted)
        self.assertFalse(decision.original_complete_backup_accepted)
        self.assertEqual(decision.original_preserved_mtd, (0, 3, 4, 5))
        self.assertEqual(manifest["schema"], 3)
        self.assertEqual(manifest["replacement_mtd"], [1, 2])

    def test_two_independent_mtd0_to_mtd5_reads_and_full_images_are_accepted(self) -> None:
        calls: list[tuple[str, int]] = []
        images = partition_images()
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "private-backup"

            def reader(copy_name: str, mtd: int) -> bytes:
                calls.append((copy_name, mtd))
                return images[mtd]

            capture_decision = capture_complete_backup(
                output_dir=output,
                confirmed_output_dir=output,
                layout=target_layout_document(),
                partition_reader=reader,
            )
            validation = validate_complete_backup(output)

        self.assertEqual(
            calls,
            [(copy, mtd) for copy in ("a", "b") for mtd in range(6)],
        )
        self.assertTrue(capture_decision.duplicate_partitions_accepted)
        self.assertTrue(validation.full_flash_reconstruction_accepted)
        self.assertEqual(validation.partition_count, 6)

    def test_copy_difference_or_changed_source_is_rejected(self) -> None:
        images = partition_images()
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "private-backup"

            def changed(copy_name: str, mtd: int) -> bytes:
                raw = images[mtd]
                if copy_name == "b" and mtd == 2:
                    return bytes([raw[0] ^ 1]) + raw[1:]
                return raw

            with self.assertRaisesRegex(FullBackupError, "duplicate reads differ"):
                capture_complete_backup(
                    output_dir=output,
                    confirmed_output_dir=output,
                    layout=target_layout_document(),
                    partition_reader=changed,
                )
            with self.assertRaises(FullBackupError):
                validate_complete_backup(output)

    def test_wrong_nor_geometry_partition_order_or_read_only_state_is_rejected(self) -> None:
        images = partition_images()
        for field, value in (
            ("nor_size", TARGET.nor_size - 1),
            ("all_partitions_read_only", False),
            ("partition_count", 5),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as name:
                layout = target_layout_document()
                layout[field] = value
                with self.assertRaisesRegex(FullBackupError, "layout differs"):
                    capture_complete_backup(
                        output_dir=Path(name) / "private-backup",
                        confirmed_output_dir=Path(name) / "private-backup",
                        layout=layout,
                        partition_reader=lambda _copy, mtd: images[mtd],
                    )
        with tempfile.TemporaryDirectory() as name:
            layout = target_layout_document()
            partitions = list(layout["partitions"])
            partitions[1], partitions[2] = partitions[2], partitions[1]
            layout["partitions"] = partitions
            with self.assertRaisesRegex(FullBackupError, "layout differs"):
                capture_complete_backup(
                    output_dir=Path(name) / "private-backup",
                    confirmed_output_dir=Path(name) / "private-backup",
                    layout=layout,
                    partition_reader=lambda _copy, mtd: images[mtd],
                )

    def test_truncated_partition_and_storage_readback_error_are_rejected(self) -> None:
        images = partition_images()
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(FullBackupError, "wrong partition size"):
                capture_complete_backup(
                    output_dir=Path(name) / "short",
                    confirmed_output_dir=Path(name) / "short",
                    layout=target_layout_document(),
                    partition_reader=lambda _copy, mtd: (
                        images[mtd][:-1] if mtd == 3 else images[mtd]
                    ),
                )
        with tempfile.TemporaryDirectory() as name:
            def bad_readback(path: Path) -> bytes:
                raw = path.read_bytes()
                return raw[:-1] + bytes([raw[-1] ^ 1]) if path.name == "mtd4.bin" else raw

            with self.assertRaisesRegex(FullBackupError, "storage readback mismatch"):
                capture_complete_backup(
                    output_dir=Path(name) / "readback",
                    confirmed_output_dir=Path(name) / "readback",
                    layout=target_layout_document(),
                    partition_reader=lambda _copy, mtd: images[mtd],
                    storage_reader=bad_readback,
                )

    def test_existing_target_symlink_hardlink_and_extra_file_are_rejected(self) -> None:
        images = partition_images()
        with tempfile.TemporaryDirectory() as name:
            existing = Path(name) / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(FullBackupError, "overwrite"):
                capture_complete_backup(
                    output_dir=existing,
                    confirmed_output_dir=existing,
                    layout=target_layout_document(),
                    partition_reader=lambda _copy, mtd: images[mtd],
                )

        mutations = ("symlink", "hardlink", "extra")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as name:
                backup = capture(Path(name), parts=images)
                target = backup / "copy-b/mtd0.bin"
                if mutation == "symlink":
                    target.unlink()
                    target.symlink_to(backup / "copy-a/mtd0.bin")
                elif mutation == "hardlink":
                    target.unlink()
                    os.link(backup / "copy-a/mtd0.bin", target)
                else:
                    (backup / "unexpected.private").write_bytes(b"no")
                with self.assertRaises(FullBackupError):
                    validate_complete_backup(backup)


if __name__ == "__main__":
    unittest.main()
