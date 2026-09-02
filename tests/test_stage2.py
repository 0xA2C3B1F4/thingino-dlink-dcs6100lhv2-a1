from __future__ import annotations

import hashlib
import struct
import unittest

from installer.artifacts import EXPECTED_KERNEL_LOAD
from installer.final_bundle import derive_final_layout, final_kernel_command_line
from installer.layout import TARGET, align_up
from installer.stage2 import (
    FILENAME,
    HEADER_SIZE,
    MAGIC,
    Stage2Error,
    build_stage2,
    validate_legacy_stage2_v1,
    validate_stage2,
)
from test_artifacts import test_squashfs, test_uimage


class Stage2Tests(unittest.TestCase):
    def _legacy_v1_payload(self) -> bytes:
        system = test_squashfs(0x18000)
        system_span = align_up(len(system))
        data_span = TARGET.partition(3).size - system_span
        command_line = " ".join(
            (
                "console=ttyS1,115200n8",
                "mem=39M@0x0",
                "rmem=25M@0x2700000",
                "init=/sbin/init",
                "root=/dev/mtdblock2",
                "rootfstype=squashfs",
                "ro",
                "panic=10",
                "mtdparts=jz_sfc:256k(boot)ro,1792k(kernel)ro,"
                "4608k(bootstrap)ro,"
                f"{system_span // 1024}k(system)ro,"
                f"{data_span // 1024}k(data),"
                "1536k(vendor)ro,256k(factory)ro",
            )
        )
        kernel = test_uimage(command_line.encode("ascii"))
        fmt = ">16sII32s8sQQQ32sQQ32sIIII32s"
        struct_size = struct.calcsize(fmt)
        system_offset = HEADER_SIZE + len(kernel)
        header = struct.pack(
            fmt,
            MAGIC,
            1,
            HEADER_SIZE,
            TARGET.model.encode("ascii").ljust(32, b"\0"),
            TARGET.hardware_revision.encode("ascii").ljust(8, b"\0"),
            system_offset + len(system),
            HEADER_SIZE,
            len(kernel),
            hashlib.sha256(kernel).digest(),
            system_offset,
            len(system),
            hashlib.sha256(system).digest(),
            TARGET.partition(3).offset,
            system_span,
            TARGET.partition(3).offset + system_span,
            data_span,
            b"\0" * 32,
        ).ljust(HEADER_SIZE, b"\0")
        digest_offset = struct_size - 32
        digest = hashlib.sha256(header).digest()
        header = header[:digest_offset] + digest + header[digest_offset + 32 :]
        return header + kernel + system

    def test_valid_in_range_nonlegacy_kernel_entry_is_accepted(self) -> None:
        system = test_squashfs(64)
        command_line = final_kernel_command_line(derive_final_layout(len(system)))
        raw = build_stage2(
            final_kernel=test_uimage(
                command_line.encode("ascii"),
                entry_point=EXPECTED_KERNEL_LOAD + 0x1000,
            ),
            system_rootfs=system,
        )
        self.assertEqual(validate_stage2(raw).system, system)

    def _payload(self) -> bytes:
        system = test_squashfs(0x18000)
        command_line = final_kernel_command_line(derive_final_layout(len(system)))
        return build_stage2(
            final_kernel=test_uimage(command_line.encode("ascii")),
            system_rootfs=system,
        )

    def test_round_trip_binds_kernel_system_and_layout(self) -> None:
        payload = validate_stage2(self._payload())
        self.assertEqual(FILENAME, "THINGINO2.BIN")
        self.assertEqual(payload.system_flash_offset, 0x680000)
        self.assertGreater(payload.data_flash_span, 0)
        self.assertEqual(payload.data_mode, "initialize")

    def test_data_mode_is_bound_into_stage2(self) -> None:
        system = test_squashfs(0x18000)
        command_line = final_kernel_command_line(derive_final_layout(len(system)))
        payload = build_stage2(
            final_kernel=test_uimage(command_line.encode("ascii")),
            system_rootfs=system,
            data_mode="preserve",
        )
        self.assertEqual(validate_stage2(payload).data_mode, "preserve")

    def test_unknown_data_mode_fails(self) -> None:
        system = test_squashfs(0x18000)
        command_line = final_kernel_command_line(derive_final_layout(len(system)))
        with self.assertRaisesRegex(Stage2Error, "data mode"):
            build_stage2(
                final_kernel=test_uimage(command_line.encode("ascii")),
                system_rootfs=system,
                data_mode="unknown",
            )

    def test_changed_payload_fails(self) -> None:
        raw = bytearray(self._payload())
        raw[-1] ^= 1
        with self.assertRaises(Stage2Error):
            validate_stage2(bytes(raw))

    def test_trailing_bytes_fail(self) -> None:
        with self.assertRaises(Stage2Error):
            validate_stage2(self._payload() + b"x")

    def test_legacy_v1_is_migration_valid_but_not_current(self) -> None:
        raw = self._legacy_v1_payload()
        self.assertEqual(validate_legacy_stage2_v1(raw).raw, raw)
        with self.assertRaisesRegex(Stage2Error, "header identity"):
            validate_stage2(raw)

    def test_legacy_v1_changed_payload_fails(self) -> None:
        raw = bytearray(self._legacy_v1_payload())
        raw[-1] ^= 1
        with self.assertRaises(Stage2Error):
            validate_legacy_stage2_v1(bytes(raw))


if __name__ == "__main__":
    unittest.main()
