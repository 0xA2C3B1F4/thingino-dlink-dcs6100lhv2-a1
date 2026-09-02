from __future__ import annotations

import binascii
import contextlib
import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from installer.fake_mtd import (
    FaultInjector,
    FakeNor,
    InjectedPowerLoss,
    MtdError,
    StockRestoreImages,
    StockRestoreInstaller,
    classify_stock_restore_snapshot,
)
from installer.full_backup import capture_complete_backup, target_layout_document
from installer.layout import (
    ERASE_BLOCK_SIZE,
    MTD_PHYSICAL_ERASE_SIZE,
    MTD_WRITE_SIZE,
    NOR_SIZE,
    Partition,
    TARGET,
)
from installer.recovery import (
    STOCK_RESTORE_PACKAGE_NAME,
    RecoveryError,
    inspect_same_device_stock_restore,
    prepare_same_device_stock_restore,
    validate_stock_mtd1,
    validate_stock_mtd2,
    validate_stock_mtd3,
)
from installer.recovery_gate import RecoveryGateError, validate_existing_recovery_boundary
from installer.sd_package import (
    END_MARKER,
    PROJECT_HEADER,
    PackageError,
    encode_record,
    parse_package,
    validate_bootstrap,
)
from installer import user_cli


def stock_mtd1() -> bytes:
    payload = b"same-device-stock-kernel"
    name = b"D-Link stock 1.02.02".ljust(32, b"\0")
    header = struct.pack(
        ">7I4B32s",
        0x27051956,
        0,
        1,
        len(payload),
        0x80010000,
        0x80010040,
        binascii.crc32(payload) & 0xFFFFFFFF,
        5,
        5,
        2,
        0,
        name,
    )
    header_crc = binascii.crc32(header) & 0xFFFFFFFF
    image = header[:4] + struct.pack(">I", header_crc) + header[8:] + payload
    return image.ljust(TARGET.partition(1).size, b"\xff")


def stock_mtd2() -> bytes:
    raw = bytearray(b"\xff" * TARGET.partition(2).size)
    raw[:4] = b"hsqs"
    struct.pack_into("<H", raw, 28, 4)
    struct.pack_into("<Q", raw, 40, 64)
    raw[48:64] = b"stock-rootfs-v1!"
    return bytes(raw)


def stock_mtd2_with_page_padding() -> bytes:
    raw = bytearray(stock_mtd2())
    raw[64:4096] = b"\0" * (4096 - 64)
    return bytes(raw)


def stock_mtd3() -> bytes:
    raw = bytearray(b"\xff" * TARGET.partition(3).size)
    prefix = struct.pack("<HHI", 0x1985, 0x2003, 12)
    header_crc = (binascii.crc32(prefix, -1) ^ -1) & 0xFFFFFFFF
    raw[:12] = prefix + struct.pack("<I", header_crc)
    return bytes(raw)


def stock_parts() -> dict[int, bytes]:
    return {
        0: b"B" * TARGET.partition(0).size,
        1: stock_mtd1(),
        2: stock_mtd2(),
        3: stock_mtd3(),
        4: b"C" * TARGET.partition(4).size,
        5: b"S" * TARGET.partition(5).size,
    }


def preserved_layout() -> dict[str, object]:
    return {
        "read_only": True,
        "schema_version": 1,
        "target": {
            "erase_block_size": TARGET.erase_block_size,
            "flash_size": TARGET.nor_size,
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
            "partitions": [
                {
                    "mtd": partition.mtd,
                    "name": partition.name,
                    "offset": partition.offset,
                    "size": partition.size,
                }
                for partition in TARGET.partitions
            ],
        },
    }


def private_fixture(root: Path) -> tuple[Path, Path, dict[int, bytes]]:
    parts = stock_parts()
    backup = root / "backup"
    capture_complete_backup(
        output_dir=backup,
        confirmed_output_dir=backup,
        layout=target_layout_document(),
        partition_reader=lambda _copy, mtd: parts[mtd],
    )
    current = root / "current-preserved"
    current.mkdir()
    (current / "device-layout.private.json").write_text(
        json.dumps(preserved_layout()), encoding="utf-8"
    )
    for mtd in (0, 4, 5):
        (current / f"mtd{mtd}.bin").write_bytes(parts[mtd])
    return backup, current, parts


def restore_images(parts: dict[int, bytes] | None = None) -> StockRestoreImages:
    source = parts or stock_parts()
    return StockRestoreImages(
        mtd1=source[1],
        mtd2=source[2],
        mtd3=source[3],
        protected=(source[0], source[4], source[5]),
    )


def current_nor(parts: dict[int, bytes]) -> bytes:
    raw = bytearray(b"\xa5" * NOR_SIZE)
    for mtd in (0, 4, 5):
        partition = TARGET.partition(mtd)
        raw[partition.offset : partition.end] = parts[mtd]
    return bytes(raw)


class StockRecoveryTests(unittest.TestCase):
    def test_prepare_and_inspect_accept_only_same_device_complete_backup(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            backup, current, _parts = private_fixture(root)
            output = root / "restore"
            plan = prepare_same_device_stock_restore(
                recovery_dir=backup,
                preserved_readback_dir=current,
                output_dir=output,
            )
            inspected = inspect_same_device_stock_restore(
                recovery_dir=backup,
                preserved_readback_dir=current,
                output_dir=output,
            )
            package = parse_package(
                (output / STOCK_RESTORE_PACKAGE_NAME).read_bytes(),
                require_project_header=True,
            )
            validate_bootstrap(package)

        self.assertEqual(plan.write_set, (3, 2, 1))
        self.assertTrue(plan.prepare_only)
        self.assertEqual(inspected.write_set, (3, 2, 1))
        self.assertEqual(
            [record.flash_offset for record in package.records],
            [TARGET.partition(1).offset, TARGET.partition(2).offset],
        )

    def test_wrong_camera_or_changed_mtd5_is_rejected_before_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            backup, current, _parts = private_fixture(root)
            (current / "mtd5.bin").write_bytes(b"X" * TARGET.partition(5).size)
            with self.assertRaisesRegex(RecoveryGateError, "mtd5"):
                validate_existing_recovery_boundary(
                    recovery_dir=backup,
                    preserved_readback_dir=current,
                )
            with self.assertRaises(RecoveryGateError):
                prepare_same_device_stock_restore(
                    recovery_dir=backup,
                    preserved_readback_dir=current,
                    output_dir=root / "restore",
                )

    def test_stock_partition_format_gates_reject_each_invalid_image(self) -> None:
        bad_mtd1 = bytearray(stock_mtd1())
        bad_mtd1[0] ^= 1
        with self.assertRaisesRegex(RecoveryError, "uImage"):
            validate_stock_mtd1(bytes(bad_mtd1))
        bad_mtd2 = bytearray(stock_mtd2())
        bad_mtd2[:4] = b"nope"
        with self.assertRaisesRegex(RecoveryError, "SquashFS"):
            validate_stock_mtd2(bytes(bad_mtd2))
        bad_mtd3 = bytearray(stock_mtd3())
        bad_mtd3[8] ^= 1
        with self.assertRaisesRegex(RecoveryError, "JFFS2"):
            validate_stock_mtd3(bytes(bad_mtd3))
        with self.assertRaisesRegex(RecoveryError, "wrong partition size"):
            validate_stock_mtd3(stock_mtd3()[:-1])

    def test_stock_mtd2_accepts_page_padding_followed_by_erased_tail(self) -> None:
        self.assertEqual(validate_stock_mtd2(stock_mtd2_with_page_padding()), 64)

    def test_stock_mtd2_rejects_padding_outside_the_first_page(self) -> None:
        bad_mtd2 = bytearray(stock_mtd2_with_page_padding())
        bad_mtd2[4096] = 0
        with self.assertRaisesRegex(RecoveryError, "unexpected bytes"):
            validate_stock_mtd2(bytes(bad_mtd2))

    def test_stock_mtd2_rejects_mixed_bytes_inside_page_padding(self) -> None:
        bad_mtd2 = bytearray(stock_mtd2_with_page_padding())
        bad_mtd2[65] = 1
        with self.assertRaisesRegex(RecoveryError, "unexpected bytes"):
            validate_stock_mtd2(bytes(bad_mtd2))

    def test_stock_mtd3_rejects_non_jffs2_crc_convention(self) -> None:
        bad_mtd3 = bytearray(stock_mtd3())
        prefix = bytes(bad_mtd3[:8])
        struct.pack_into("<I", bad_mtd3, 8, binascii.crc32(prefix) & 0xFFFFFFFF)
        with self.assertRaisesRegex(RecoveryError, "JFFS2"):
            validate_stock_mtd3(bytes(bad_mtd3))

    def test_stock_mtd3_rejects_unknown_node_type(self) -> None:
        bad_mtd3 = bytearray(stock_mtd3())
        struct.pack_into("<H", bad_mtd3, 2, 0x2005)
        prefix = bytes(bad_mtd3[:8])
        header_crc = (binascii.crc32(prefix, -1) ^ -1) & 0xFFFFFFFF
        struct.pack_into("<I", bad_mtd3, 8, header_crc)
        with self.assertRaisesRegex(RecoveryError, "JFFS2"):
            validate_stock_mtd3(bytes(bad_mtd3))

    def test_stock_mtd3_rejects_a_forged_later_cleanmarker(self) -> None:
        forged = bytearray(b"\xff" * TARGET.partition(3).size)
        forged[4:16] = stock_mtd3()[:12]
        with self.assertRaisesRegex(RecoveryError, "cleanmarker"):
            validate_stock_mtd3(bytes(forged))

    def test_stock_package_rejects_mtd0_and_out_of_bounds_records(self) -> None:
        overlaps_mtd0 = b"".join(
            (
                PROJECT_HEADER,
                encode_record(TARGET.partition(0), b"boot"),
                encode_record(TARGET.partition(2), b"root"),
                END_MARKER,
            )
        )
        with self.assertRaisesRegex(PackageError, "mtd0"):
            validate_bootstrap(parse_package(overlaps_mtd0))

        outside = Partition("outside", 1, NOR_SIZE, TARGET.partition(1).size)
        out_of_bounds = b"".join(
            (
                PROJECT_HEADER,
                encode_record(outside, b"kernel"),
                encode_record(TARGET.partition(2), b"root"),
                END_MARKER,
            )
        )
        with self.assertRaises(PackageError):
            validate_bootstrap(parse_package(out_of_bounds))

    def test_protected_mtd_can_never_enter_fake_nor_write_set(self) -> None:
        parts = stock_parts()
        images = StockRestoreImages(
            mtd1=parts[1],
            mtd2=parts[2],
            mtd3=parts[3],
            protected=(parts[0], parts[4], parts[5]),
            write_set=(0, 1, 2, 3, 4, 5),
        )
        with self.assertRaisesRegex(MtdError, "exactly mtd3,mtd2,mtd1"):
            images.validate()

    def test_fake_nor_restore_preserves_protected_partitions_and_activates_last(self) -> None:
        parts = stock_parts()
        initial = current_nor(parts)
        fault = FaultInjector()
        installer = StockRestoreInstaller(FakeNor(initial, fault))
        installer.run(restore_images(parts))
        expected = b"".join(parts[mtd] for mtd in range(6))

        self.assertEqual(installer.nor.snapshot(), expected)
        self.assertEqual(installer.state.state, "stock_restore_complete")
        activation = fault.events.index("before_write_stock_activation")
        for event in (
            "after_readback_stock_mtd3",
            "after_readback_stock_mtd2",
            "after_readback_stock_mtd1_tail",
        ):
            self.assertLess(fault.events.index(event), activation)

    def test_fault_at_every_erase_write_readback_and_activation_phase_is_terminal(self) -> None:
        parts = stock_parts()
        images = restore_images(parts)
        initial = current_nor(parts)
        trace = FaultInjector()
        StockRestoreInstaller(FakeNor(initial, trace)).run(images)
        events = [
            event
            for event in trace.events
            if any(kind in event for kind in ("state_", "erase_", "write_", "readback_"))
        ]
        self.assertGreater(len(events), 30)
        for event in events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event)
                nor = FakeNor(initial, fault)
                installer = StockRestoreInstaller(nor)
                with self.assertRaises(InjectedPowerLoss):
                    installer.run(images)
                self.assertEqual(installer.state.state, "failed_power_loss")
                for mtd in (0, 4, 5):
                    partition = TARGET.partition(mtd)
                    self.assertEqual(
                        nor.snapshot()[partition.offset : partition.end],
                        initial[partition.offset : partition.end],
                    )
                self.assertIn(
                    classify_stock_restore_snapshot(
                        nor.snapshot(), before=initial, images=images
                    ),
                    {"survivor", "retry", "unviable"},
                )

    def test_torn_physical_erase_and_write_steps_remain_bounded_and_retryable(self) -> None:
        parts = stock_parts()
        images = restore_images(parts)
        initial = current_nor(parts)
        spans = {
            "stock_mtd3": TARGET.partition(3).size,
            "stock_mtd2": TARGET.partition(2).size,
            "stock_mtd1_tail": TARGET.partition(1).size - ERASE_BLOCK_SIZE,
            "stock_activation": ERASE_BLOCK_SIZE,
        }
        events: list[str] = []
        for label, span in spans.items():
            for operation, unit, suffix in (
                ("erase", MTD_PHYSICAL_ERASE_SIZE, "sector"),
                ("write", MTD_WRITE_SIZE, "page"),
            ):
                count = span // unit
                for index in sorted({0, count // 2, count - 1}):
                    events.append(
                        f"during_{operation}_{label}_{suffix}_{index}"
                    )
        for event in events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event, physical_steps=True)
                nor = FakeNor(initial, fault)
                with self.assertRaises(InjectedPowerLoss):
                    StockRestoreInstaller(nor).run(images)
                stopped = nor.snapshot()
                for mtd in (0, 4, 5):
                    partition = TARGET.partition(mtd)
                    self.assertEqual(
                        stopped[partition.offset : partition.end],
                        initial[partition.offset : partition.end],
                    )
                self.assertIn(
                    classify_stock_restore_snapshot(
                        stopped, before=initial, images=images
                    ),
                    {"survivor", "retry", "unviable"},
                )
                retry = StockRestoreInstaller(FakeNor(stopped))
                retry.run(images)
                self.assertEqual(retry.state.state, "stock_restore_complete")

    def test_retry_classification_covers_survivor_retry_unviable_and_stop(self) -> None:
        parts = stock_parts()
        images = restore_images(parts)
        initial = current_nor(parts)
        self.assertEqual(
            classify_stock_restore_snapshot(initial, before=initial, images=images),
            "survivor",
        )
        retry = bytearray(initial)
        mtd3 = TARGET.partition(3)
        retry[mtd3.offset : mtd3.end] = images.mtd3
        self.assertEqual(
            classify_stock_restore_snapshot(bytes(retry), before=initial, images=images),
            "survivor",
        )
        mtd2 = TARGET.partition(2)
        retry[mtd2.offset : mtd2.end] = images.mtd2
        self.assertEqual(
            classify_stock_restore_snapshot(bytes(retry), before=initial, images=images),
            "retry",
        )
        unviable = bytearray(retry)
        mtd1 = TARGET.partition(1)
        unviable[mtd1.offset : mtd1.offset + ERASE_BLOCK_SIZE] = b"X" * ERASE_BLOCK_SIZE
        self.assertEqual(
            classify_stock_restore_snapshot(bytes(unviable), before=initial, images=images),
            "unviable",
        )
        stopped = bytearray(retry)
        stopped[TARGET.partition(5).offset] ^= 1
        self.assertEqual(
            classify_stock_restore_snapshot(bytes(stopped), before=initial, images=images),
            "stop",
        )

    def test_guided_cli_phases_never_print_private_binding_hash_or_mtd5_data(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            backup, current, parts = private_fixture(root)
            restore = root / "restore"
            private_manifest = json.loads(
                (backup / "manifest.private.json").read_text(encoding="utf-8")
            )
            forbidden = {
                private_manifest["device_binding_sha256"],
                hashlib.sha256(parts[5]).hexdigest(),
                parts[5][:32].hex(),
            }
            commands = (
                [
                    "stock-recovery",
                    "backup-validate",
                    "--json",
                    "--recovery-dir",
                    str(backup),
                ],
                [
                    "stock-recovery",
                    "restore-prepare",
                    "--json",
                    "--recovery-dir",
                    str(backup),
                    "--preserved-readback-dir",
                    str(current),
                    "--output-dir",
                    str(restore),
                ],
                [
                    "stock-recovery",
                    "restore-inspect",
                    "--json",
                    "--recovery-dir",
                    str(backup),
                    "--preserved-readback-dir",
                    str(current),
                    "--output-dir",
                    str(restore),
                ],
            )
            documents = []
            for command in commands:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    user_cli.main(command)
                rendered = output.getvalue()
                documents.append(json.loads(rendered))
                for secret in forbidden:
                    self.assertNotIn(secret, rendered)

        self.assertEqual(
            [document["phase"] for document in documents],
            [
                "private-backup-validated",
                "stock-restore-prepared-only",
                "stock-restore-package-inspected",
            ],
        )
        self.assertTrue(all(document["nor"]["written_mtd"] == [] for document in documents))
        self.assertFalse(documents[-1]["result"]["physical_restore_proven"])
        self.assertEqual(
            documents[-1]["result"]["safe_next_action"],
            "prepare-unarmed-live-restore-set",
        )


if __name__ == "__main__":
    unittest.main()
