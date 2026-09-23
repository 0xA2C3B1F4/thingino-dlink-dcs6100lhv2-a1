from __future__ import annotations

import unittest

from installer.stock_restore.kernel import (
    StockRestoreKernelError,
    render_stock_restore_kernel_fragment,
    stock_restore_kernel_command_line,
    validate_stock_restore_kernel,
    validate_stock_restore_kernel_config,
)
from installer.stock_restore.set import _uimage_record_payload
from installer.sd_package import generate_bootstrap, parse_package
from tests.test_artifacts import test_uimage


class StockRestoreKernelTests(unittest.TestCase):
    def test_exact_six_partition_write_policy_is_embedded(self) -> None:
        command_line = stock_restore_kernel_command_line()
        self.assertIn("root=/dev/mtdblock2 rootfstype=squashfs ro", command_line)
        self.assertIn(
            "mtdparts=jz_sfc:256k(boot)ro,1792k(kernel),"
            "4608k(rootfs),7936k(userdata),1536k(userdata2)ro,"
            "256k(userdata3)ro",
            command_line,
        )
        self.assertEqual(command_line.count(")ro"), 3)
        self.assertNotIn("mtd6", command_line)

    def test_fragment_has_no_ip_or_network_device_surface(self) -> None:
        fragment = render_stock_restore_kernel_fragment()
        validate_stock_restore_kernel_config(fragment)
        self.assertIn(b"# CONFIG_INET is not set", fragment)
        self.assertIn(b"# CONFIG_NETDEVICES is not set", fragment)
        self.assertIn(b"# CONFIG_WIRELESS is not set", fragment)

    def test_protected_or_write_partition_drift_is_rejected(self) -> None:
        fragment = render_stock_restore_kernel_fragment()
        writable_mtd5 = fragment.replace(b"256k(userdata3)ro", b"256k(userdata3)")
        with self.assertRaisesRegex(StockRestoreKernelError, "CONFIG_CMDLINE"):
            validate_stock_restore_kernel_config(writable_mtd5)
        read_only_mtd3 = fragment.replace(b"7936k(userdata)", b"7936k(userdata)ro")
        with self.assertRaisesRegex(StockRestoreKernelError, "CONFIG_CMDLINE"):
            validate_stock_restore_kernel_config(read_only_mtd3)

    def test_kernel_and_effective_config_share_the_exact_command_line(self) -> None:
        command_line = stock_restore_kernel_command_line()
        decision = validate_stock_restore_kernel(
            kernel=test_uimage(
                command_line.encode("ascii"),
                entry_point=0x80010100,
            ),
            linux_config=render_stock_restore_kernel_fragment(),
        )
        self.assertEqual(decision.command_line, command_line)
        with self.assertRaisesRegex(StockRestoreKernelError, "command line"):
            validate_stock_restore_kernel(
                kernel=test_uimage(b"stock restore wrong command line"),
                linux_config=render_stock_restore_kernel_fragment(),
            )

    def test_stock_package_alignment_is_removed_before_kernel_validation(self) -> None:
        kernel = test_uimage(
            stock_restore_kernel_command_line().encode("ascii") + b"x",
            entry_point=0x80010100,
        )
        packaged = parse_package(generate_bootstrap(kernel, b"rootfs"))
        self.assertEqual(_uimage_record_payload(packaged.records[0].payload), kernel)


if __name__ == "__main__":
    unittest.main()
