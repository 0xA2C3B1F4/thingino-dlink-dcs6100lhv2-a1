from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.collector.kernel import render_collector_kernel_fragment
from installer.live_ram import (
    KERNEL_ADDRESS,
    KERNEL_SD_NAME,
    UBOOT_PROMPT,
    WRAPPER_ADDRESS,
    LiveRamError,
    build_live_ram_plan,
    execute_live_ram_plan,
    prepare_live_ram_set,
    ram_bootargs,
)
from installer.ram_boot import build_ramdisk_uimage
from tests.test_artifacts import test_squashfs, test_uimage


def external_config() -> bytes:
    values = {
        "CONFIG_BLK_DEV_INITRD": "y",
        "CONFIG_BLK_DEV_RAM": "y",
        "CONFIG_BLK_DEV_RAM_SIZE": "8192",
        "CONFIG_DEVTMPFS": "y",
        "CONFIG_DEVTMPFS_MOUNT": "y",
        "CONFIG_FAT_FS": "y",
        "CONFIG_JZMMC_V12": "m",
        "CONFIG_MMC": "y",
        "CONFIG_MMC_BLOCK": "y",
        "CONFIG_MODULES": "y",
        "CONFIG_MTD": "y",
        "CONFIG_MTD_BLOCK": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_MTD_JZ_SFC": "y",
        "CONFIG_MTD_JZ_SFC_NOR": "y",
        "CONFIG_SQUASHFS": "y",
        "CONFIG_SQUASHFS_XZ": "y",
        "CONFIG_VFAT_FS": "y",
    }
    lines = [f"{key}={value}" for key, value in values.items()]
    lines.append("# CONFIG_CMDLINE_BOOL is not set")
    return ("\n".join(lines) + "\n").encode()


def live_plan(mode: str = "collector"):
    if mode == "collector":
        kernel = test_uimage(entry_point=0x80010100)
        linux_config = render_collector_kernel_fragment()
    else:
        kernel = test_uimage()
        linux_config = external_config()
    return build_live_ram_plan(
        mode=mode,
        kernel=kernel,
        linux_config=linux_config,
        wrapper=build_ramdisk_uimage(test_squashfs(4096)),
    )


class Transcript:
    def __init__(self, plan, *, corrupt_crc: bool = False):
        self.plan = plan
        self.corrupt_crc = corrupt_crc
        self.commands: list[str] = []

    def command(self, command: str, *, dangerous: bool = False) -> bytes:
        del dangerous
        self.commands.append(command)
        body = b""
        if command.startswith("fatload"):
            size = (
                len(self.plan.kernel)
                if KERNEL_SD_NAME in command
                else len(self.plan.wrapper)
            )
            body = f"{size} bytes read in 1 ms\n".encode()
        elif command.startswith("crc32"):
            items = command.split()
            address = int(items[1], 16)
            size = int(items[2], 16)
            expected = (
                self.plan.kernel_crc32
                if address == KERNEL_ADDRESS
                else self.plan.wrapper_crc32
            )
            if self.corrupt_crc:
                expected = "00000000"
            body = (
                f"CRC32 for {address:08x} ... {address + size - 1:08x} "
                f"==> {expected}\n"
            ).encode()
        elif command.startswith("bootm start"):
            body = b"Verifying Checksum ... OK\nVerifying Checksum ... OK\n"
        elif command == "printenv bootargs":
            body = ("bootargs=" + self.plan.bootargs + "\n").encode()
        return command.encode() + b"\r\n" + body + UBOOT_PROMPT

    def final_boot(self, command: str, *, dangerous: bool = False) -> bytes:
        self.commands.append(command)
        if self.plan.mode == "stock-restore" and not dangerous:
            raise AssertionError("stock restore was not marked dangerous")
        return command.encode() + b"\r\n" + self.plan.completion_marker + b"\r\n"


class LiveRamTests(unittest.TestCase):
    def test_collector_plan_builds_a_dynamic_read_only_command_line(self) -> None:
        plan = live_plan("collector")
        self.assertEqual(plan.bootargs, ram_bootargs("collector", 4096))
        self.assertIn("rd_start=0x81000000 rd_size=0x1000", plan.bootargs)
        self.assertEqual(plan.write_set, ())
        self.assertEqual(plan.bootargs.count(")ro"), 6)

    def test_prepare_collector_live_set_accepts_validated_collector_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "live-set"
            plan = prepare_live_ram_set(
                mode="collector",
                kernel=test_uimage(entry_point=0x80010100),
                linux_config=render_collector_kernel_fragment(),
                wrapper=build_ramdisk_uimage(test_squashfs(4096)),
                output_dir=output,
            )
            self.assertEqual(plan.write_set, ())
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {"T4RAMK.UIM", "T4COLL.UIM", "ram-boot.private.json"},
            )

    def test_collector_plan_rejects_an_embedded_command_line_config(self) -> None:
        changed = render_collector_kernel_fragment().replace(
            b"# CONFIG_CMDLINE_BOOL is not set", b"CONFIG_CMDLINE_BOOL=y"
        )
        with self.assertRaisesRegex(LiveRamError, "CONFIG_CMDLINE_BOOL=n"):
            build_live_ram_plan(
                mode="collector",
                kernel=test_uimage(entry_point=0x80010100),
                linux_config=changed,
                wrapper=build_ramdisk_uimage(test_squashfs(4096)),
            )

    def test_collector_map_is_all_read_only_and_restore_map_is_bounded(self) -> None:
        collector = live_plan("collector")
        restorer = live_plan("stock-restore")
        self.assertEqual(collector.write_set, ())
        self.assertEqual(restorer.write_set, (3, 2, 1))
        self.assertEqual(collector.bootargs.count(")ro"), 6)
        self.assertIn("1792K(kernel),4608K(rootfs),7936K(userdata)", restorer.bootargs)
        self.assertIn("1536K(userdata2)ro,256K(userdata3)ro", restorer.bootargs)
        self.assertNotIn("mtd6", collector.bootargs + restorer.bootargs)

    def test_exact_uart_sequence_rechecks_crc_after_bootm_start(self) -> None:
        plan = live_plan()
        transcript = Transcript(plan)
        output = execute_live_ram_plan(transcript, plan)
        self.assertIn(plan.completion_marker, output)
        self.assertEqual(
            transcript.commands.count(
                f"crc32 0x{KERNEL_ADDRESS:x} 0x{len(plan.kernel):x}"
            ),
            2,
        )
        self.assertEqual(
            transcript.commands.count(
                f"crc32 0x{WRAPPER_ADDRESS:x} 0x{len(plan.wrapper):x}"
            ),
            2,
        )
        self.assertTrue(transcript.commands[-1].startswith("bootm "))

    def test_uart_crc_difference_stops_before_final_boot(self) -> None:
        plan = live_plan()
        transcript = Transcript(plan, corrupt_crc=True)
        with self.assertRaisesRegex(LiveRamError, "CRC"):
            execute_live_ram_plan(transcript, plan)
        self.assertFalse(any(command.startswith("bootm 0x") for command in transcript.commands))

    def test_config_must_leave_command_line_under_volatile_uboot_control(self) -> None:
        changed = external_config().replace(
            b"# CONFIG_CMDLINE_BOOL is not set", b"CONFIG_CMDLINE_BOOL=y"
        )
        with self.assertRaisesRegex(LiveRamError, "CONFIG_CMDLINE_BOOL=n"):
            build_live_ram_plan(
                mode="stock-restore",
                kernel=test_uimage(),
                linux_config=changed,
                wrapper=build_ramdisk_uimage(test_squashfs(4096)),
            )


if __name__ == "__main__":
    unittest.main()
