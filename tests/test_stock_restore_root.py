from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.media import MediaPreflight
from installer.stock_restore import build
from installer.stock_restore import set as restore_set
from installer.stock_restore.set import (
    AUTH_SD_NAME,
    BOOTSTRAP_ACTIVE_NAME,
    BOOTSTRAP_PRIVATE_NAME,
    COMPLETE_SD_NAME,
    COMPLETE_STATUS,
    PASSIVE_DIR_NAME,
    PASSIVE_MARKER_NAME,
    PASSIVE_STATUS,
    RUN_SD_NAME,
    RUN_STATUS,
    StockRestoreRamSet,
    StockRestoreBootstrapSet,
    StockRestoreSetError,
    authorize_stock_restore,
    authorize_stock_restore_bootstrap,
    deactivate_stock_restore_bootstrap,
    expected_bootstrap_confirmation,
    expected_handoff_confirmation,
    expected_live_confirmation,
    expected_retry_confirmation,
    passivate_completed_stock_restore,
    reauthorize_interrupted_stock_restore,
    stage_stock_restore_bootstrap_set,
    stage_stock_restore_ram_set,
)
from installer.sd_package import generate_bootstrap, matching_update_filenames, parse_package
from tests.test_live_ram import live_plan
from tests.test_stock_recovery import restore_images


def synthetic_mmc_module() -> bytes:
    raw = bytearray(4096)
    raw[:6] = b"\x7fELF\x01\x01"
    raw[18:20] = b"\x08\x00"
    for offset, marker in (
        (128, b"jzmmc_v12"),
        (256, b"cd_gpio_pin"),
        (384, b"3.10.14__isvp_swan_1.0__"),
    ):
        raw[offset : offset + len(marker)] = marker
    return bytes(raw)


def prepared_restore_set() -> StockRestoreRamSet:
    files = {
        "T4RAMK.UIM": b"kernel",
        "T4RSTR.UIM": b"wrapper",
        "STOCK1.BIN": b"one",
        "STOCK2.BIN": b"two",
        "STOCK3.BIN": b"three",
        "KEEP0.BIN": b"zero",
        "KEEP4.BIN": b"four",
        "KEEP5.BIN": b"five",
        "authorization.private": b"Z" * 32,
    }
    return StockRestoreRamSet(
        live_plan("stock-restore"), restore_images(), b"Z" * 32, files
    )


def prepared_bootstrap_set() -> StockRestoreBootstrapSet:
    images = restore_images()
    package = generate_bootstrap(b"kernel", b"rootfs")
    files = {
        BOOTSTRAP_PRIVATE_NAME: package,
        "STOCK1.BIN": images.mtd1,
        "STOCK2.BIN": images.mtd2,
        "STOCK3.BIN": images.mtd3,
        "KEEP0.BIN": images.protected[0],
        "KEEP4.BIN": images.protected[1],
        "KEEP5.BIN": images.protected[2],
        "authorization.private": b"Y" * 32,
    }
    return StockRestoreBootstrapSet(images, b"Y" * 32, files)


def media_preflight(root: Path) -> MediaPreflight:
    return MediaPreflight(
        physical_device="/dev/disk4",
        model="synthetic external",
        capacity_bytes=128_000_000_000,
        filesystem="fat32",
        mount_root=root.resolve(),
    )


def complete_restore_on_card(root: Path, prepared: StockRestoreRamSet) -> None:
    preflight = media_preflight(root)
    stage_stock_restore_ram_set(
        prepared=prepared,
        root=root,
        preflight=preflight,
        confirmed_physical_device=preflight.physical_device,
    )
    authorize_stock_restore(
        prepared=prepared,
        root=root,
        preflight=preflight,
        confirmed_physical_device=preflight.physical_device,
        confirmation=expected_live_confirmation(preflight),
    )
    (root / AUTH_SD_NAME).unlink()
    (root / RUN_SD_NAME).write_bytes(RUN_STATUS)
    (root / COMPLETE_SD_NAME).write_bytes(COMPLETE_STATUS)


class StockRestoreRootTests(unittest.TestCase):
    def test_contract_binds_exact_payloads_module_and_one_shot_authorization(self) -> None:
        images = restore_images()
        contract = build.render_contract(
            images=images,
            mmc_module=synthetic_mmc_module(),
            authorization=b"A" * 32,
        ).decode("ascii")
        for symbol, size in (
            ("MTD1", len(images.mtd1)),
            ("MTD2", len(images.mtd2)),
            ("MTD3", len(images.mtd3)),
            ("KEEP0", len(images.protected[0])),
            ("KEEP4", len(images.protected[1])),
            ("KEEP5", len(images.protected[2])),
            ("AUTH", 32),
        ):
            self.assertIn(f"#define {symbol}_SIZE {size}U", contract)

    def test_source_write_set_is_structurally_bounded_and_activation_is_last(self) -> None:
        source = build.read_restorer_source().decode("utf-8")
        build.validate_restorer_source(source.encode())
        for mtd in (0, 4, 5):
            self.assertNotIn(f'"/dev/mtd{mtd}", O_RDWR', source)
            self.assertIn(f'verify_mtd("/dev/mtd{mtd}"', source)
        mtd3 = source.index("restore_complete_partition(MTD3_FILE")
        mtd2 = source.index("restore_complete_partition(MTD2_FILE")
        tail = source.index(
            "erase_range(descriptor, ACTIVATION_SIZE, MTD1_SIZE - ACTIVATION_SIZE)"
        )
        activation = source.index("erase_range(descriptor, 0, ACTIVATION_SIZE)")
        final = source.index("RESTORE COMPLETE physical_readback_verified")
        self.assertLess(mtd3, mtd2)
        self.assertLess(mtd2, tail)
        self.assertLess(tail, activation)
        self.assertLess(activation, final)
        restore = source[source.index("static void restore_stock(void)") :]
        self.assertLess(
            restore.index("read_exact(descriptor, activation_buffer, ACTIVATION_SIZE);"),
            restore.index("verify_kernel_before_activation();"),
        )
        self.assertLess(
            restore.index("verify_kernel_before_activation();"),
            restore.index("erase_range(descriptor, 0, ACTIVATION_SIZE);"),
        )
        self.assertIn(
            "dcs_sha256_update(&context, activation_buffer, ACTIVATION_SIZE);",
            source,
        )
        self.assertIn("MTD1_SHA256", source)
        self.assertIn("RESTORE retry_authorization_consumed", source)
        self.assertIn("SYSCALL_RENAME", source)
        self.assertLess(
            source.index("RUN_TEMP_FILE, RUN_FILE"),
            source.index("call1(SYSCALL_UNLINK, (long)AUTH_FILE)"),
        )
        self.assertLess(
            source.index("SYSCALL_MLOCKALL, MCL_CURRENT | MCL_FUTURE"),
            source.index("verify_exact_layout();"),
        )

    def test_sd_set_is_unarmed_until_exact_confirmation(self) -> None:
        prepared = prepared_restore_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_ram_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            self.assertFalse((root / AUTH_SD_NAME).exists())
            with self.assertRaisesRegex(StockRestoreSetError, "confirmation"):
                authorize_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation="wrong",
                )
            authorize_stock_restore(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_live_confirmation(preflight),
            )
            self.assertEqual((root / AUTH_SD_NAME).read_bytes(), b"Z" * 32)

    def test_private_set_authorization_gate_rejects_wrong_lengths(self) -> None:
        for size in (31, 33):
            with self.subTest(size=size), self.assertRaisesRegex(
                StockRestoreSetError, "wrong size"
            ):
                restore_set._require_authorization(b"A" * size)

    def test_authorization_partial_write_is_removed_before_failure(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )

            def partial_write(path: Path, raw: bytes, mode: int = 0o600) -> None:
                path.write_bytes(raw[:7])
                raise OSError("injected partial write")

            with (
                mock.patch(
                    "installer.stock_restore.activation._write_exclusive",
                    side_effect=partial_write,
                ),
                self.assertRaisesRegex(OSError, "injected partial write"),
            ):
                authorize_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_bootstrap_confirmation(preflight),
                )

            self.assertFalse((root / AUTH_SD_NAME).exists())
            self.assertFalse((root / f".{AUTH_SD_NAME}.part").exists())
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_ACTIVE_NAME).exists())

    def test_authorization_readback_failure_rolls_back_exact_marker(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            real_read = restore_set._read_regular

            def fail_activated_readback(path: Path, size: int) -> bytes:
                if path.name == AUTH_SD_NAME:
                    raise OSError("injected authorization readback")
                return real_read(path, size)

            with (
                mock.patch(
                    "installer.stock_restore.activation._read_regular",
                    side_effect=fail_activated_readback,
                ),
                self.assertRaisesRegex(OSError, "injected authorization readback"),
            ):
                authorize_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_bootstrap_confirmation(preflight),
                )

            self.assertFalse((root / AUTH_SD_NAME).exists())
            self.assertFalse((root / f".{AUTH_SD_NAME}.part").exists())
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_ACTIVE_NAME).exists())

    def test_selector_validation_failure_rolls_back_to_inert_name(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            with (
                mock.patch(
                    "installer.stock_restore.activation.selected_update_filename",
                    side_effect=ValueError("injected selector validation"),
                ),
                self.assertRaisesRegex(ValueError, "injected selector validation"),
            ):
                authorize_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_bootstrap_confirmation(preflight),
                )

            self.assertEqual((root / AUTH_SD_NAME).read_bytes(), b"Y" * 32)
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_ACTIVE_NAME).exists())
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()), []
            )

    def test_existing_reserved_sd_path_stops_without_overwrite(self) -> None:
        prepared = prepared_restore_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "STOCK2.BIN").write_bytes(b"keep")
            preflight = MediaPreflight(
                "/dev/disk4", "synthetic", 1, "fat32", root.resolve()
            )
            with self.assertRaisesRegex(StockRestoreSetError, "already exists"):
                stage_stock_restore_ram_set(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                )
            self.assertEqual((root / "STOCK2.BIN").read_bytes(), b"keep")

    def test_bootstrap_is_inert_until_selector_is_activated_last(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            names = stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            self.assertIn(BOOTSTRAP_PRIVATE_NAME, names)
            self.assertEqual(matching_update_filenames(entry.name for entry in root.iterdir()), [])
            self.assertFalse((root / AUTH_SD_NAME).exists())

            authorize_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_bootstrap_confirmation(preflight),
            )
            self.assertFalse((root / BOOTSTRAP_PRIVATE_NAME).exists())
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()),
                [BOOTSTRAP_ACTIVE_NAME],
            )
            self.assertEqual((root / AUTH_SD_NAME).read_bytes(), b"Y" * 32)
            package = parse_package((root / BOOTSTRAP_ACTIVE_NAME).read_bytes())
            self.assertEqual(
                tuple(record.flash_offset for record in package.records),
                (0x00040000, 0x00200000),
            )
            authorize_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_bootstrap_confirmation(preflight),
            )
            deactivate_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_handoff_confirmation(preflight),
            )
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_ACTIVE_NAME).exists())
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()),
                [],
            )

    def test_bootstrap_stage_removes_only_owned_appledouble_sidecars(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            unrelated_sidecar = root / "._photo.jpg"
            unrelated_sidecar.write_bytes(b"keep")
            real_replace = os.replace

            def replace_with_sidecar(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                destination.with_name("._" + destination.name).write_bytes(
                    b"generated metadata"
                )

            with mock.patch(
                "installer.stock_restore.staging.os.replace",
                side_effect=replace_with_sidecar,
            ):
                names = stage_stock_restore_bootstrap_set(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )

            self.assertEqual(unrelated_sidecar.read_bytes(), b"keep")
            for staged_name in names:
                self.assertFalse((root / ("._" + staged_name)).exists())

    def test_bootstrap_stage_rejects_preexisting_reserved_appledouble(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            sidecar = root / "._STOCK1.BIN"
            sidecar.write_bytes(b"preexisting")
            with self.assertRaisesRegex(StockRestoreSetError, "already exists"):
                stage_stock_restore_bootstrap_set(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )
            self.assertEqual(sidecar.read_bytes(), b"preexisting")

    def test_bootstrap_authorize_removes_only_owned_appledouble_sidecars(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            unrelated_sidecar = root / "._photo.jpg"
            unrelated_sidecar.write_bytes(b"keep")
            real_replace = os.replace

            def replace_with_sidecar(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                destination.with_name("._" + destination.name).write_bytes(
                    b"generated metadata"
                )

            with mock.patch(
                "installer.stock_restore.activation.os.replace",
                side_effect=replace_with_sidecar,
            ):
                authorize_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_bootstrap_confirmation(preflight),
                )

            self.assertEqual(unrelated_sidecar.read_bytes(), b"keep")
            self.assertFalse((root / "._RESTORE.GO").exists())
            self.assertFalse((root / ("._" + BOOTSTRAP_ACTIVE_NAME)).exists())

    def test_bootstrap_authorize_rejects_preexisting_authorization_sidecar(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            sidecar = root / "._RESTORE.GO"
            sidecar.write_bytes(b"preexisting")
            with self.assertRaisesRegex(StockRestoreSetError, "sidecar"):
                authorize_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_bootstrap_confirmation(preflight),
                )
            self.assertEqual(sidecar.read_bytes(), b"preexisting")
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_ACTIVE_NAME).exists())

    def test_bootstrap_handoff_removes_only_owned_appledouble_sidecars(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            authorize_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_bootstrap_confirmation(preflight),
            )
            unrelated_sidecar = root / "._photo.jpg"
            unrelated_sidecar.write_bytes(b"keep")
            real_replace = os.replace

            def replace_with_sidecar(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                destination.with_name("._" + destination.name).write_bytes(
                    b"generated metadata"
                )

            with mock.patch(
                "installer.stock_restore.activation.os.replace",
                side_effect=replace_with_sidecar,
            ):
                deactivate_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_handoff_confirmation(preflight),
                )

            self.assertEqual(unrelated_sidecar.read_bytes(), b"keep")
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_ACTIVE_NAME).exists())
            self.assertFalse((root / ("._" + BOOTSTRAP_PRIVATE_NAME)).exists())

    def test_bootstrap_handoff_rejects_preexisting_package_sidecar(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            authorize_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_bootstrap_confirmation(preflight),
            )
            sidecar = root / ("._" + BOOTSTRAP_ACTIVE_NAME)
            sidecar.write_bytes(b"preexisting")
            with self.assertRaisesRegex(ValueError, "stock-matching|sidecar"):
                deactivate_stock_restore_bootstrap(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_handoff_confirmation(preflight),
                )
            self.assertEqual(sidecar.read_bytes(), b"preexisting")
            self.assertTrue((root / BOOTSTRAP_ACTIVE_NAME).is_file())
            self.assertFalse((root / BOOTSTRAP_PRIVATE_NAME).exists())

    def test_bootstrap_stage_rejects_another_stock_update_selector(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "DCS6100LHV2Ax_FW001_BAD_SD.bin").write_bytes(b"unsafe")
            with self.assertRaisesRegex(ValueError, "stock-matching|selector"):
                stage_stock_restore_bootstrap_set(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )

    def test_interrupted_bootstrap_retry_requires_exact_run_without_ok(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            authorize_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_bootstrap_confirmation(preflight),
            )
            deactivate_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_handoff_confirmation(preflight),
            )
            (root / AUTH_SD_NAME).unlink()
            (root / RUN_SD_NAME).write_bytes(RUN_STATUS)
            reauthorize_interrupted_stock_restore(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_retry_confirmation(preflight),
            )
            self.assertEqual((root / AUTH_SD_NAME).read_bytes(), b"Y" * 32)
            self.assertTrue((root / BOOTSTRAP_ACTIVE_NAME).is_file())
            reauthorize_interrupted_stock_restore(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_retry_confirmation(preflight),
            )
            deactivate_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_handoff_confirmation(preflight),
            )
            self.assertTrue((root / BOOTSTRAP_PRIVATE_NAME).is_file())

            (root / AUTH_SD_NAME).unlink()
            (root / COMPLETE_SD_NAME).write_bytes(COMPLETE_STATUS)
            with self.assertRaisesRegex(StockRestoreSetError, "not retryable"):
                reauthorize_interrupted_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                    confirmation=expected_retry_confirmation(preflight),
                )

    def test_completed_bootstrap_passivation_removes_active_selector(self) -> None:
        prepared = prepared_bootstrap_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_bootstrap_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            authorize_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_bootstrap_confirmation(preflight),
            )
            deactivate_stock_restore_bootstrap(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_handoff_confirmation(preflight),
            )
            (root / AUTH_SD_NAME).unlink()
            (root / RUN_SD_NAME).write_bytes(RUN_STATUS)
            (root / COMPLETE_SD_NAME).write_bytes(COMPLETE_STATUS)
            first = passivate_completed_stock_restore(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            self.assertEqual(matching_update_filenames(entry.name for entry in root.iterdir()), [])
            self.assertTrue((root / PASSIVE_DIR_NAME / BOOTSTRAP_PRIVATE_NAME).is_file())
            self.assertFalse(first.already_passivated)
            second = passivate_completed_stock_restore(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            self.assertTrue(second.already_passivated)

    def test_completed_restore_passivation_is_resumable_and_idempotent(self) -> None:
        prepared = prepared_restore_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            complete_restore_on_card(root, prepared)
            (root / "THINGINO.BAK").write_bytes(b"preserve-me")
            passive = root / PASSIVE_DIR_NAME
            passive.mkdir()
            (root / RUN_SD_NAME).replace(passive / RUN_SD_NAME)

            first = passivate_completed_stock_restore(
                prepared=prepared,
                root=root,
                preflight=media_preflight(root),
                confirmed_physical_device="/dev/disk4",
            )
            self.assertFalse(first.already_passivated)
            self.assertGreater(len(first.moved_files), 0)
            self.assertEqual(first.preserved_unrelated_entries, 1)
            self.assertEqual((root / "THINGINO.BAK").read_bytes(), b"preserve-me")
            self.assertEqual(
                (passive / PASSIVE_MARKER_NAME).read_bytes(), PASSIVE_STATUS
            )
            for staged_name in prepared.files:
                if staged_name != "authorization.private":
                    self.assertFalse((root / staged_name).exists())
                    self.assertTrue((passive / staged_name).is_file())
            self.assertFalse((root / RUN_SD_NAME).exists())
            self.assertFalse((root / COMPLETE_SD_NAME).exists())

            second = passivate_completed_stock_restore(
                prepared=prepared,
                root=root,
                preflight=media_preflight(root),
                confirmed_physical_device="/dev/disk4",
            )
            self.assertTrue(second.already_passivated)
            self.assertEqual(second.moved_files, ())

    def test_passivation_removes_only_transaction_owned_appledouble(self) -> None:
        prepared = prepared_restore_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            complete_restore_on_card(root, prepared)
            unrelated_sidecar = root / "._photo.jpg"
            unrelated_sidecar.write_bytes(b"keep")
            real_replace = os.replace
            real_write_exclusive = restore_set._write_exclusive

            def replace_with_sidecar(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                destination.with_name("._" + destination.name).write_bytes(
                    b"generated metadata"
                )

            def write_with_sidecar(path: Path, raw: bytes, mode: int = 0o600) -> None:
                real_write_exclusive(path, raw, mode)
                path.with_name("._" + path.name).write_bytes(b"generated metadata")

            with (
                mock.patch(
                    "installer.stock_restore.passivation.os.replace",
                    side_effect=replace_with_sidecar,
                ),
                mock.patch(
                    "installer.stock_restore.passivation._write_exclusive",
                    side_effect=write_with_sidecar,
                ),
            ):
                result = passivate_completed_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )

            passive = root / PASSIVE_DIR_NAME
            self.assertFalse(result.already_passivated)
            self.assertEqual(unrelated_sidecar.read_bytes(), b"keep")
            self.assertEqual(
                (passive / PASSIVE_MARKER_NAME).read_bytes(), PASSIVE_STATUS
            )
            self.assertFalse(
                any(entry.name.startswith("._") for entry in passive.iterdir())
            )

    def test_passivation_rejects_preexisting_passive_sidecar(self) -> None:
        prepared = prepared_restore_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            complete_restore_on_card(root, prepared)
            passive = root / PASSIVE_DIR_NAME
            passive.mkdir()
            sidecar = passive / "._STOCK1.BIN"
            sidecar.write_bytes(b"preexisting")

            with self.assertRaisesRegex(StockRestoreSetError, "sidecar"):
                passivate_completed_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )
            self.assertEqual(sidecar.read_bytes(), b"preexisting")

    def test_passivation_rejects_nonterminal_or_unsafe_closure(self) -> None:
        prepared = prepared_restore_set()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            preflight = media_preflight(root)
            stage_stock_restore_ram_set(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
            )
            authorize_stock_restore(
                prepared=prepared,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/disk4",
                confirmation=expected_live_confirmation(preflight),
            )
            with self.assertRaisesRegex(StockRestoreSetError, "authorization"):
                passivate_completed_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/disk4",
                )

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            complete_restore_on_card(root, prepared)
            (root / COMPLETE_SD_NAME).unlink()
            with self.assertRaisesRegex(StockRestoreSetError, "exactly one"):
                passivate_completed_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            complete_restore_on_card(root, prepared)
            target = root / "STOCK2.BIN"
            target.unlink()
            target.symlink_to(root / "STOCK1.BIN")
            with self.assertRaises((OSError, StockRestoreSetError)):
                passivate_completed_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            complete_restore_on_card(root, prepared)
            target = root / "STOCK2.BIN"
            target.unlink()
            target.hardlink_to(root / "STOCK1.BIN")
            with self.assertRaisesRegex(StockRestoreSetError, "not exact"):
                passivate_completed_stock_restore(
                    prepared=prepared,
                    root=root,
                    preflight=media_preflight(root),
                    confirmed_physical_device="/dev/disk4",
                )


if __name__ == "__main__":
    unittest.main()
