from __future__ import annotations

import json
import unittest

from installer.fake_mtd import (
    FaultInjector,
    FakeNor,
    FinalBundleImages,
    FinalInstaller,
    InjectedPowerLoss,
    MtdError,
    OfflineStage2Images,
    OfflineStage2Installer,
)
from installer.layout import (
    ERASE_BLOCK_SIZE,
    MTD_PHYSICAL_ERASE_SIZE,
    MTD_WRITE_SIZE,
    NOR_SIZE,
    TARGET,
)
from installer.mtd3_split import DATA_FLASH_SPAN, SYSTEM_FLASH_SPAN


INITIAL = b"\xa5" * NOR_SIZE
OVERLAY_TEST_MAGIC = b"DCS6OVL1"


def overlay_region(files: dict[str, str], span: int) -> bytes:
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    header = OVERLAY_TEST_MAGIC + len(payload).to_bytes(4, "big")
    return (header + payload).ljust(span, b"\xff")


def overlay_files(region: bytes) -> dict[str, str]:
    if not region.startswith(OVERLAY_TEST_MAGIC):
        return {}
    size = int.from_bytes(region[len(OVERLAY_TEST_MAGIC) : len(OVERLAY_TEST_MAGIC) + 4], "big")
    start = len(OVERLAY_TEST_MAGIC) + 4
    return json.loads(region[start : start + size].decode())


def images() -> FinalBundleImages:
    return FinalBundleImages(
        system_offset=TARGET.partition(3).offset,
        system_erase_span=2 * ERASE_BLOCK_SIZE,
        system=b"system" * 1024 + b"!",
        data_offset=TARGET.partition(3).offset + 2 * ERASE_BLOCK_SIZE,
        data_erase_span=2 * ERASE_BLOCK_SIZE,
        data=b"data" * 1024,
        rootfs=b"rootfs" * 1024,
        kernel=b"activation".ljust(ERASE_BLOCK_SIZE, b"A") + b"kernel-tail",
    )


def offline_images(data_mode: str = "initialize") -> OfflineStage2Images:
    return OfflineStage2Images(
        system_offset=TARGET.partition(3).offset,
        system_erase_span=SYSTEM_FLASH_SPAN,
        system=b"system" * 1024,
        data_offset=TARGET.partition(3).offset + SYSTEM_FLASH_SPAN,
        data_erase_span=DATA_FLASH_SPAN,
        kernel=b"activation".ljust(ERASE_BLOCK_SIZE, b"A") + b"kernel-tail",
        data_mode=data_mode,
    )


class FakeMtdTests(unittest.TestCase):
    def test_reboot_and_firmware_update_keep_etc_overlay_settings(self) -> None:
        candidate = offline_images("preserve")
        lower_v1 = {
            "/etc/TZ": "Etc/GMT",
            "/etc/timezone": "Etc/GMT",
            "/etc/thingino.json": '{"mqtt":{"enabled":false}}',
            "/etc/prudynt.json": '{"motion":{"enabled":false}}',
        }
        persisted = {
            "/etc/TZ": "Europe/Helsinki",
            "/etc/timezone": "Europe/Helsinki",
            "/etc/thingino.json": '{"mqtt":{"enabled":true,"host":"ha.local"}}',
            "/etc/prudynt.json": '{"motion":{"enabled":true}}',
        }
        initial = bytearray(INITIAL)
        encoded = overlay_region(persisted, candidate.data_erase_span)
        initial[candidate.data_offset : candidate.data_offset + len(encoded)] = encoded

        before_reboot = {**lower_v1, **overlay_files(encoded)}
        after_reboot = {**lower_v1, **overlay_files(encoded)}
        self.assertEqual(after_reboot, before_reboot)

        installer = OfflineStage2Installer(FakeNor(bytes(initial)))
        installer.run(candidate)
        updated_region = installer.nor.snapshot()[
            candidate.data_offset : candidate.data_offset + candidate.data_erase_span
        ]
        lower_v2 = {**lower_v1, "/etc/prudynt.json": '{"motion":{"enabled":false,"roi":[]}}'}
        after_update_reboot = {**lower_v2, **overlay_files(updated_region)}
        self.assertEqual(after_update_reboot, persisted)

    def test_factory_reset_reboot_reveals_system_defaults(self) -> None:
        candidate = offline_images("factory-reset")
        lower = {
            "/etc/TZ": "Etc/GMT",
            "/etc/thingino.json": '{"mqtt":{"enabled":false}}',
        }
        initial = bytearray(INITIAL)
        encoded = overlay_region(
            {
                "/etc/TZ": "Europe/Helsinki",
                "/etc/thingino.json": '{"mqtt":{"enabled":true}}',
            },
            candidate.data_erase_span,
        )
        initial[candidate.data_offset : candidate.data_offset + len(encoded)] = encoded
        installer = OfflineStage2Installer(FakeNor(bytes(initial)))
        installer.run(candidate)
        reset_region = installer.nor.snapshot()[
            candidate.data_offset : candidate.data_offset + candidate.data_erase_span
        ]
        self.assertEqual(overlay_files(reset_region), {})
        self.assertEqual({**lower, **overlay_files(reset_region)}, lower)

    def test_firmware_update_preserves_data_region_byte_for_byte(self) -> None:
        mtd3 = TARGET.partition(3)
        candidate = offline_images("preserve")
        initial = bytearray(INITIAL)
        data = bytes((index * 17) & 0xFF for index in range(candidate.data_erase_span))
        start = candidate.data_offset
        initial[start : start + len(data)] = data
        installer = OfflineStage2Installer(FakeNor(bytes(initial)))
        installer.run(candidate)
        self.assertEqual(
            installer.nor.snapshot()[start : start + len(data)],
            data,
        )
        self.assertEqual(installer.stock_userdata_backup_status, "preserve_mode")
        self.assertEqual(mtd3.size, candidate.system_erase_span + candidate.data_erase_span)

    def test_factory_reset_erases_only_data_region(self) -> None:
        candidate = offline_images("factory-reset")
        nor = FakeNor(INITIAL)
        installer = OfflineStage2Installer(nor)
        installer.run(candidate)
        snapshot = nor.snapshot()
        self.assertEqual(
            snapshot[candidate.data_offset : candidate.data_offset + candidate.data_erase_span],
            b"\xff" * candidate.data_erase_span,
        )
        self.assertEqual(installer.stock_userdata_backup_status, "factory-reset_mode")

    def test_full_and_corrupt_data_are_preserved_for_explicit_recovery(self) -> None:
        candidate = offline_images("preserve")
        start = candidate.data_offset
        cases = {
            "full-jffs2": b"\x85\x19" + b"\x00" * (candidate.data_erase_span - 2),
            "corrupt": b"corrupt".ljust(candidate.data_erase_span, b"\x00"),
        }
        for label, data in cases.items():
            with self.subTest(label=label):
                initial = bytearray(INITIAL)
                initial[start : start + len(data)] = data
                installer = OfflineStage2Installer(FakeNor(bytes(initial)))
                installer.run(candidate)
                self.assertEqual(
                    installer.nor.snapshot()[start : start + len(data)], data
                )

    def test_interrupted_update_retry_never_changes_data_region(self) -> None:
        candidate = offline_images("preserve")
        start = candidate.data_offset
        data = b"\x85\x19" + bytes(
            (index * 29) & 0xFF for index in range(candidate.data_erase_span - 2)
        )
        initial = bytearray(INITIAL)
        initial[start : start + len(data)] = data
        trace = FaultInjector()
        OfflineStage2Installer(FakeNor(bytes(initial), trace)).run(candidate)
        first_system_write = trace.events.index("before_erase_final_system")
        update_events = [
            event
            for event in trace.events[first_system_write:]
            if any(kind in event for kind in ("erase_", "write_", "readback_"))
        ]
        for event in update_events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event)
                interrupted_nor = FakeNor(bytes(initial), fault)
                interrupted = OfflineStage2Installer(interrupted_nor)
                with self.assertRaises(InjectedPowerLoss):
                    interrupted.run(candidate)
                self.assertEqual(
                    interrupted_nor.snapshot()[start : start + len(data)], data
                )
                retry = OfflineStage2Installer(FakeNor(interrupted_nor.snapshot()))
                retry.run(candidate)
                self.assertEqual(
                    retry.nor.snapshot()[start : start + len(data)], data
                )

    def test_preserve_update_survives_torn_physical_system_operations(self) -> None:
        candidate = offline_images("preserve")
        data_start = candidate.data_offset
        data = bytes(
            (index * 31) & 0xFF for index in range(candidate.data_erase_span)
        )
        initial = bytearray(INITIAL)
        initial[data_start : data_start + len(data)] = data
        system_pages = (
            len(candidate.system) + MTD_WRITE_SIZE - 1
        ) // MTD_WRITE_SIZE
        system_sectors = candidate.system_erase_span // MTD_PHYSICAL_ERASE_SIZE
        events = (
            "during_erase_final_system_sector_0",
            f"during_erase_final_system_sector_{system_sectors // 2}",
            f"during_erase_final_system_sector_{system_sectors - 1}",
            "during_write_final_system_page_0",
            f"during_write_final_system_page_{system_pages // 2}",
            f"during_write_final_system_page_{system_pages - 1}",
        )
        expected_system = candidate.system.ljust(candidate.system_erase_span, b"\xff")
        for event in events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event, physical_steps=True)
                interrupted_nor = FakeNor(bytes(initial), fault)
                with self.assertRaises(InjectedPowerLoss):
                    OfflineStage2Installer(interrupted_nor).run(candidate)
                self.assertEqual(
                    interrupted_nor.snapshot()[data_start : data_start + len(data)],
                    data,
                )
                self.assertNotEqual(
                    interrupted_nor.snapshot()[
                        candidate.system_offset :
                        candidate.system_offset + candidate.system_erase_span
                    ],
                    expected_system,
                )
                retry = OfflineStage2Installer(FakeNor(interrupted_nor.snapshot()))
                retry.run(candidate)
                self.assertEqual(
                    retry.nor.snapshot()[data_start : data_start + len(data)],
                    data,
                )

    def test_initialize_recovery_checkpoint_survives_torn_physical_writes(self) -> None:
        candidate = offline_images("initialize")
        data_sectors = candidate.data_erase_span // MTD_PHYSICAL_ERASE_SIZE
        system_pages = (
            len(candidate.system) + MTD_WRITE_SIZE - 1
        ) // MTD_WRITE_SIZE
        events = (
            "during_erase_final_data_sector_0",
            f"during_erase_final_data_sector_{data_sectors - 1}",
            "during_write_final_system_page_0",
            f"during_write_final_system_page_{system_pages - 1}",
            "during_write_final_kernel_tail_page_0",
            "during_write_activation_page_1",
        )
        for event in events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event, physical_steps=True)
                interrupted_nor = FakeNor(INITIAL, fault)
                interrupted = OfflineStage2Installer(interrupted_nor)
                with self.assertRaises(InjectedPowerLoss):
                    interrupted.run(candidate)
                self.assertIsNotNone(interrupted.stock_userdata_backup)
                self.assertIsNotNone(interrupted.stock_userdata_checkpoint)
                snapshot = interrupted_nor.snapshot()
                for mtd in (0, 2, 4, 5):
                    partition = TARGET.partition(mtd)
                    self.assertEqual(
                        snapshot[partition.offset : partition.end],
                        INITIAL[partition.offset : partition.end],
                    )

                retry = OfflineStage2Installer(
                    FakeNor(interrupted_nor.snapshot()),
                    stock_userdata_backup=interrupted.stock_userdata_backup,
                    stock_userdata_checkpoint=interrupted.stock_userdata_checkpoint,
                )
                retry.run(candidate)
                self.assertEqual(
                    retry.stock_userdata_backup_status,
                    "interrupted_install_recovery",
                )
                self.assertEqual(retry.state.state, "final_readback_verified")

    def test_write_order_preserves_mtd0_mtd4_mtd5_and_activates_last(self) -> None:
        fault = FaultInjector()
        nor = FakeNor(INITIAL, fault)
        installer = FinalInstaller(nor)
        installer.run(images())
        self.assertEqual(installer.state.state, "final_readback_verified")
        activation_write = fault.events.index("before_write_activation")
        self.assertLess(fault.events.index("after_readback_final_kernel_tail"), activation_write)
        self.assertLess(fault.events.index("after_readback_final_rootfs"), activation_write)
        self.assertLess(fault.events.index("after_readback_final_data"), activation_write)
        self.assertLess(fault.events.index("after_readback_final_system"), activation_write)
        snapshot = nor.snapshot()
        for mtd in (0, 4, 5):
            partition = TARGET.partition(mtd)
            self.assertEqual(
                snapshot[partition.offset : partition.end],
                INITIAL[partition.offset : partition.end],
            )

    def test_power_loss_at_every_erase_write_readback_and_transition_fails_terminally(self) -> None:
        trace = FaultInjector()
        FinalInstaller(FakeNor(INITIAL, trace)).run(images())
        required_kinds = ("state_", "erase_", "write_", "readback_")
        events = [event for event in trace.events if any(kind in event for kind in required_kinds)]
        self.assertGreater(len(events), 30)
        for event in events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event)
                installer = FinalInstaller(FakeNor(INITIAL, fault))
                with self.assertRaises(InjectedPowerLoss):
                    installer.run(images())
                self.assertEqual(installer.state.state, "failed_power_loss")
                self.assertNotIn("thingino_booted", installer.state.history)

    def test_offline_stage2_preserves_bootstrap_and_exports_stock_userdata(self) -> None:
        fault = FaultInjector()
        nor = FakeNor(INITIAL, fault)
        mtd2 = TARGET.partition(2)
        before_mtd2 = nor.snapshot()[mtd2.offset : mtd2.end]
        installer = OfflineStage2Installer(nor)
        installer.run(offline_images())
        self.assertEqual(installer.state.state, "final_readback_verified")
        self.assertEqual(installer.stock_userdata_backup, INITIAL[
            TARGET.partition(3).offset : TARGET.partition(3).end
        ])
        self.assertEqual(installer.stock_userdata_backup_status, "exported")
        self.assertEqual(len(installer.stock_userdata_checkpoint or b""), 80)
        self.assertEqual(nor.snapshot()[mtd2.offset : mtd2.end], before_mtd2)
        activation = fault.events.index("before_write_activation")
        self.assertLess(fault.events.index("after_readback_final_system"), activation)
        self.assertLess(fault.events.index("after_readback_final_kernel_tail"), activation)

    def test_offline_stage2_reuses_exact_matching_stock_backup(self) -> None:
        mtd3 = TARGET.partition(3)
        backup = INITIAL[mtd3.offset : mtd3.end]
        installer = OfflineStage2Installer(
            FakeNor(INITIAL), stock_userdata_backup=backup
        )
        installer.run(offline_images())
        self.assertEqual(installer.stock_userdata_backup_status, "reused")
        self.assertEqual(installer.stock_userdata_backup, backup)
        self.assertEqual(len(installer.stock_userdata_checkpoint or b""), 80)
        self.assertEqual(installer.state.state, "final_readback_verified")

    def test_offline_stage2_rejects_partial_backup_before_erasing(self) -> None:
        mtd3 = TARGET.partition(3)
        backup = INITIAL[mtd3.offset : mtd3.end - 1]
        fault = FaultInjector()
        nor = FakeNor(INITIAL, fault)
        installer = OfflineStage2Installer(
            nor, stock_userdata_backup=backup
        )
        with self.assertRaisesRegex(MtdError, "backup size mismatch"):
            installer.run(offline_images())
        self.assertEqual(installer.state.state, "failed_write_or_state_gate")
        self.assertFalse(any("erase_" in event for event in fault.events))
        self.assertEqual(nor.snapshot(), INITIAL)

    def test_offline_stage2_rejects_nonmatching_backup_before_erasing(self) -> None:
        mtd3 = TARGET.partition(3)
        backup = bytearray(INITIAL[mtd3.offset : mtd3.end])
        backup[0] ^= 0x01
        fault = FaultInjector()
        nor = FakeNor(INITIAL, fault)
        installer = OfflineStage2Installer(
            nor, stock_userdata_backup=bytes(backup)
        )
        with self.assertRaisesRegex(MtdError, "recovery checkpoint mismatch"):
            installer.run(offline_images())
        self.assertEqual(installer.state.state, "failed_write_or_state_gate")
        self.assertFalse(any("erase_" in event for event in fault.events))
        self.assertEqual(nor.snapshot(), INITIAL)

    def test_offline_stage2_recovers_after_interrupted_final_write(self) -> None:
        first_fault = FaultInjector(fail_at="after_erase_final_system")
        nor = FakeNor(INITIAL, first_fault)
        first = OfflineStage2Installer(nor)
        with self.assertRaises(InjectedPowerLoss):
            first.run(offline_images())
        self.assertIsNotNone(first.stock_userdata_backup)
        self.assertIsNotNone(first.stock_userdata_checkpoint)
        self.assertNotEqual(nor.snapshot(), INITIAL)

        retry = OfflineStage2Installer(
            FakeNor(nor.snapshot()),
            stock_userdata_backup=first.stock_userdata_backup,
            stock_userdata_checkpoint=first.stock_userdata_checkpoint,
        )
        retry.run(offline_images())
        self.assertEqual(
            retry.stock_userdata_backup_status,
            "interrupted_install_recovery",
        )
        self.assertEqual(retry.state.state, "final_readback_verified")

    def test_offline_stage2_rejects_changed_checkpoint_after_interruption(self) -> None:
        first_fault = FaultInjector(fail_at="after_erase_final_system")
        nor = FakeNor(INITIAL, first_fault)
        first = OfflineStage2Installer(nor)
        with self.assertRaises(InjectedPowerLoss):
            first.run(offline_images())
        snapshot = nor.snapshot()
        checkpoint = bytearray(first.stock_userdata_checkpoint or b"")
        checkpoint[-1] ^= 0x01

        retry_fault = FaultInjector()
        retry_nor = FakeNor(snapshot, retry_fault)
        retry = OfflineStage2Installer(
            retry_nor,
            stock_userdata_backup=first.stock_userdata_backup,
            stock_userdata_checkpoint=bytes(checkpoint),
        )
        with self.assertRaisesRegex(MtdError, "recovery checkpoint mismatch"):
            retry.run(offline_images())
        self.assertFalse(any("erase_" in event for event in retry_fault.events))
        self.assertEqual(retry_nor.snapshot(), snapshot)

    def test_offline_stage2_recovery_checkpoint_covers_final_write_matrix(self) -> None:
        trace = FaultInjector()
        OfflineStage2Installer(FakeNor(INITIAL, trace)).run(offline_images())
        first_write = trace.events.index("before_erase_final_data")
        recovery_events = [
            event
            for event in trace.events[first_write:]
            if any(
                kind in event
                for kind in ("state_", "erase_", "write_", "readback_")
            )
        ]
        self.assertGreater(len(recovery_events), 20)
        for event in recovery_events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event)
                interrupted_nor = FakeNor(INITIAL, fault)
                interrupted = OfflineStage2Installer(interrupted_nor)
                with self.assertRaises(InjectedPowerLoss):
                    interrupted.run(offline_images())
                self.assertIsNotNone(interrupted.stock_userdata_backup)
                self.assertIsNotNone(interrupted.stock_userdata_checkpoint)

                retry = OfflineStage2Installer(
                    FakeNor(interrupted_nor.snapshot()),
                    stock_userdata_backup=interrupted.stock_userdata_backup,
                    stock_userdata_checkpoint=interrupted.stock_userdata_checkpoint,
                )
                retry.run(offline_images())
                self.assertEqual(retry.state.state, "final_readback_verified")

    def test_offline_stage2_power_loss_matrix_is_terminal(self) -> None:
        trace = FaultInjector()
        OfflineStage2Installer(FakeNor(INITIAL, trace)).run(offline_images())
        required_kinds = ("state_", "erase_", "write_", "readback_")
        events = [
            event
            for event in trace.events
            if any(kind in event for kind in required_kinds)
        ]
        for event in events:
            with self.subTest(event=event):
                fault = FaultInjector(fail_at=event)
                installer = OfflineStage2Installer(FakeNor(INITIAL, fault))
                with self.assertRaises(InjectedPowerLoss):
                    installer.run(offline_images())
                self.assertEqual(installer.state.state, "failed_power_loss")


if __name__ == "__main__":
    unittest.main()
