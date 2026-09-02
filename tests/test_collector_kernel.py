from __future__ import annotations

import re
import stat
import subprocess
import unittest
from pathlib import Path

from installer.collector.kernel import (
    CollectorKernelError,
    render_collector_kernel_fragment,
    render_uartless_collector_kernel_fragment,
    uartless_collector_kernel_command_line,
    validate_collector_kernel,
    validate_collector_kernel_config,
    validate_uartless_collector_kernel_config,
)
from tests.test_artifacts import test_uimage


ROOT = Path(__file__).resolve().parents[1]


class CollectorKernelTests(unittest.TestCase):
    def test_macos_runner_is_offline_and_uses_the_collector_entrypoint(self) -> None:
        runner_path = ROOT / "scripts/run_macos_collector_kernel_build.sh"
        runner = runner_path.read_text(encoding="utf-8")
        builder = (ROOT / "scripts/container_build_collector_kernel.sh").read_text(
            encoding="utf-8"
        )
        self.assertTrue(stat.S_IMODE(runner_path.stat().st_mode) & 0o111)
        subprocess.run(["sh", "-n", str(runner_path)], check=True)
        self.assertIn("--network none", runner)
        self.assertIn("container_build_collector_kernel.sh", runner)
        self.assertIn('"$prepared_source:/input/source:ro"', runner)
        self.assertIn('"$download_cache:/input/download-cache.tar:ro"', runner)
        self.assertIn('"$workspace_image:/input/workspace.ext4"', runner)
        self.assertIn('"$result_dir:/result"', runner)
        self.assertIn("workspace image escaped the task scratch root", runner)
        self.assertIn("-L dcs6100-coll", runner)
        self.assertNotIn("container_build_stock_restore_kernel.sh", runner)
        self.assertEqual(
            set(re.findall(r'\$result_dir/([A-Za-z0-9_.-]+)"', builder)),
            {
                "collector-kernel.uimage",
                "collector-linux.config",
                "jzmmc_v12.ko",
                "source-preparation.json",
                "uartless-collector-kernel.uimage",
                "uartless-collector-linux.config",
            },
        )

    def test_fragment_leaves_command_line_under_volatile_uboot_control(self) -> None:
        fragment = render_collector_kernel_fragment()
        self.assertIn(b"# CONFIG_CMDLINE_BOOL is not set", fragment)
        self.assertNotIn(b"CONFIG_CMDLINE=", fragment)
        self.assertNotIn(b"CONFIG_CMDLINE_OVERRIDE=y", fragment)

    def test_generated_fragment_is_its_own_effective_config_fixture(self) -> None:
        fragment = render_collector_kernel_fragment()
        validate_collector_kernel_config(fragment)
        self.assertIn(b"CONFIG_NET=y", fragment)
        self.assertIn(b"# CONFIG_INET is not set", fragment)
        self.assertIn(b"# CONFIG_NETDEVICES is not set", fragment)
        self.assertIn(b"# CONFIG_WIRELESS is not set", fragment)
        self.assertIn(b"CONFIG_JZMMC_V12=m", fragment)
        self.assertIn(b"CONFIG_JFFS2_FS=y", fragment)

    def test_uartless_fragment_boots_mtd2_with_every_partition_read_only(self) -> None:
        fragment = render_uartless_collector_kernel_fragment()
        validate_uartless_collector_kernel_config(fragment)
        command_line = uartless_collector_kernel_command_line()
        self.assertIn(b"CONFIG_CMDLINE_OVERRIDE=y", fragment)
        self.assertIn(f'CONFIG_CMDLINE="{command_line}"'.encode(), fragment)
        for partition in ("boot", "kernel", "rootfs", "userdata", "userdata2", "userdata3"):
            self.assertIn(f"({partition})ro".encode(), fragment)

    def test_effective_config_rejects_ip_or_embedded_command_line_drift(self) -> None:
        fragment = render_collector_kernel_fragment()
        networked = fragment.replace(b"# CONFIG_INET is not set", b"CONFIG_INET=y")
        with self.assertRaisesRegex(CollectorKernelError, "CONFIG_INET=n"):
            validate_collector_kernel_config(networked)
        embedded = fragment.replace(
            b"# CONFIG_CMDLINE_BOOL is not set",
            b"CONFIG_CMDLINE_BOOL=y\n"
            b"CONFIG_CMDLINE_OVERRIDE=y\n"
            b'CONFIG_CMDLINE="root=/dev/mtdblock2"',
        )
        with self.assertRaisesRegex(CollectorKernelError, "CONFIG_CMDLINE_BOOL=n"):
            validate_collector_kernel_config(embedded)

    def test_kernel_image_accepts_a_valid_kernel_specific_entry(self) -> None:
        decision = validate_collector_kernel(
            kernel=test_uimage(entry_point=0x80010100),
            linux_config=render_collector_kernel_fragment(),
        )
        self.assertEqual(decision.image.entry_point, 0x80010100)
        corrupt = bytearray(test_uimage(entry_point=0x80010100))
        corrupt[-1] ^= 1
        with self.assertRaises(CollectorKernelError):
            validate_collector_kernel(
                kernel=bytes(corrupt),
                linux_config=render_collector_kernel_fragment(),
            )


if __name__ == "__main__":
    unittest.main()
