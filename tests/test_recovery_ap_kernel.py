from __future__ import annotations

import unittest

from installer.recovery_ap.kernel import (
    RecoveryApKernelError,
    recovery_ap_kernel_command_line,
    render_recovery_ap_kernel_fragment,
    validate_recovery_ap_kernel,
    validate_recovery_ap_kernel_config,
)
from tests.test_artifacts import test_uimage


class RecoveryApKernelTests(unittest.TestCase):
    def test_only_physical_mtd3_is_writable_and_ipv6_is_enabled(self) -> None:
        fragment = render_recovery_ap_kernel_fragment()
        validate_recovery_ap_kernel_config(fragment)
        self.assertIn(b"CONFIG_INET=y", fragment)
        self.assertIn(b"CONFIG_WIRELESS=y", fragment)
        self.assertIn(b"CONFIG_USB_JZ_DWC2=y", fragment)
        self.assertIn(b"CONFIG_IPV6=y", fragment)
        self.assertIn(b"CONFIG_JFFS2_FS=y", fragment)
        command_line = recovery_ap_kernel_command_line()
        self.assertNotIn("ipv6.disable=1", command_line)
        self.assertIn("root=/dev/mtdblock2 rootfstype=squashfs ro", command_line)
        self.assertIn("4608k(rootfs)ro,7936k(userdata),1536k(userdata2)ro", command_line)
        self.assertEqual(command_line.count(")ro"), 5)

    def test_writable_mtd_or_missing_wireless_fails(self) -> None:
        fragment = render_recovery_ap_kernel_fragment()
        writable = fragment.replace(b"1536k(userdata2)ro", b"1536k(userdata2)")
        with self.assertRaisesRegex(RecoveryApKernelError, "CONFIG_CMDLINE"):
            validate_recovery_ap_kernel_config(writable)
        no_wireless = fragment.replace(b"CONFIG_WIRELESS=y", b"# CONFIG_WIRELESS is not set")
        with self.assertRaisesRegex(RecoveryApKernelError, "CONFIG_WIRELESS"):
            validate_recovery_ap_kernel_config(no_wireless)

    def test_built_kernel_is_bound_to_the_persistent_bootstrap_command_line(self) -> None:
        command_line = recovery_ap_kernel_command_line()
        decision = validate_recovery_ap_kernel(
            kernel=test_uimage(
                command_line.encode("ascii"),
                entry_point=0x80010100,
            ),
            linux_config=render_recovery_ap_kernel_fragment(),
        )
        self.assertEqual(decision.command_line, command_line)


if __name__ == "__main__":
    unittest.main()
