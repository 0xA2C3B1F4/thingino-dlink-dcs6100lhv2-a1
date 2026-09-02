from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from installer.final_bundle import (
    BundleError,
    DATA_FLASH_SPAN,
    SYSTEM_FLASH_SPAN,
    build_final_bundle,
    build_universal_final_bundle,
    derive_final_layout,
    final_kernel_command_line,
    final_bundle_images,
    render_final_kernel_fragment,
    validate_final_bundle,
    validate_universal_final_bundle,
)
from installer.install_policy import universal_physical_write_policy
from installer.layout import TARGET
from installer.mtd3_split import Mtd3SplitError
from installer.fake_mtd import FakeNor, FinalInstaller
from installer.layout import NOR_SIZE
from test_artifacts import test_squashfs, test_uimage


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class FinalBundleTests(unittest.TestCase):
    @staticmethod
    def _jffs2() -> bytes:
        layout = derive_final_layout(4096)
        return b"\x85\x19" + b"data" + b"\xff" * (layout.data_span - 6)

    def _images(self, system_size: int = 64) -> dict[str, bytes]:
        system = test_squashfs(system_size)
        command_line = final_kernel_command_line(derive_final_layout(len(system)))
        config = render_final_kernel_fragment(len(system))
        return {
            "kernel": test_uimage(command_line.encode("ascii")),
            "bootstrap_rootfs": test_squashfs(),
            "system_rootfs": system,
            "data_jffs2": self._jffs2(),
            "linux_config": config,
        }

    def _keys(self, directory: Path) -> tuple[Path, Path]:
        private_key = directory / "private.pem"
        public_key = directory / "public.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", private_key],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.chmod(private_key, 0o600)
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                private_key,
                "-pubout",
                "-out",
                public_key,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return private_key, public_key

    def test_signed_bundle_round_trip_and_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            private_key, public_key = self._keys(Path(directory_name))
            inputs = self._images(0x18000)
            system = inputs["system_rootfs"]
            raw = build_final_bundle(
                **inputs,
                signing_key=private_key,
            )
            bundle = validate_final_bundle(raw, public_key=public_key)
            layout = derive_final_layout(len(system))
            self.assertEqual(layout.system_offset, TARGET.partition(3).offset)
            self.assertEqual(layout.system_span, 6464 * 1024)
            self.assertEqual(layout.data_span, 1472 * 1024)
            self.assertEqual(layout.system_span, SYSTEM_FLASH_SPAN)
            self.assertEqual(layout.data_span, DATA_FLASH_SPAN)
            self.assertEqual(
                layout.data_offset,
                TARGET.partition(3).offset + SYSTEM_FLASH_SPAN,
            )
            self.assertEqual(bundle.manifest["preserved_mtd"], [0, 4, 5])
            self.assertEqual(bundle.manifest["schema_version"], 2)
            self.assertEqual(bundle.manifest["artifact_scope"], "device-personalized")
            self.assertNotIn("physical_write_policy", bundle.manifest)
            self.assertEqual(
                bundle.manifest["image_kind"], "dcs6100-mtd3-split-v1"
            )
            self.assertTrue(
                bundle.manifest["persistence_policy"]["preserve_data_on_update"]
            )
            self.assertEqual(
                bundle.manifest["write_order"]["firmware_update"][0],
                "images/system.squashfs",
            )
            self.assertNotIn(
                "images/data.jffs2",
                bundle.manifest["write_order"]["firmware_update"],
            )
            self.assertTrue(bundle.manifest["activation"]["written_last"])
            self.assertIn("root=/dev/mtdblock2", bundle.manifest["kernel_command_line"])
            self.assertNotIn("root=/dev/mtdblock3", bundle.manifest["kernel_command_line"])
            installer = FinalInstaller(FakeNor(b"\xa5" * NOR_SIZE))
            installer.run(final_bundle_images(bundle))
            self.assertEqual(installer.state.state, "final_readback_verified")

    def test_wrong_signing_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            private_key, _ = self._keys(directory)
            other = directory / "other"
            other.mkdir()
            _, wrong_public = self._keys(other)
            raw = build_final_bundle(
                **self._images(),
                signing_key=private_key,
            )
            with self.assertRaises(BundleError):
                validate_final_bundle(raw, public_key=wrong_public)

    def test_jffs2_magic_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            private_key, _ = self._keys(Path(directory_name))
            with self.assertRaisesRegex(BundleError, "JFFS2"):
                build_final_bundle(
                    **{
                        **self._images(),
                        "data_jffs2": b"not-jffs2".ljust(DATA_FLASH_SPAN, b"\xff"),
                    },
                    signing_key=private_key,
                )

    def test_short_jffs2_image_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            private_key, _ = self._keys(Path(directory_name))
            with self.assertRaisesRegex(BundleError, "exact-span"):
                build_final_bundle(
                    **{**self._images(), "data_jffs2": b"\x85\x19data"},
                    signing_key=private_key,
                )

    def test_empty_erased_data_region_is_signed_and_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            private_key, public_key = self._keys(Path(directory_name))
            inputs = {**self._images(), "data_jffs2": b""}
            raw = build_final_bundle(**inputs, signing_key=private_key)
            bundle = validate_final_bundle(raw, public_key=public_key)
            self.assertTrue(bundle.manifest["data_initialization"]["empty_erased_region"])
            installer = FinalInstaller(FakeNor(b"\xa5" * NOR_SIZE))
            installer.run(final_bundle_images(bundle))
            layout = derive_final_layout(len(inputs["system_rootfs"]))
            self.assertEqual(
                installer.nor.snapshot()[layout.data_offset : layout.data_offset + layout.data_span],
                b"\xff" * layout.data_span,
            )

    def test_universal_bundle_is_identical_across_camera_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            private_key, public_key = self._keys(Path(directory_name))
            inputs = self._images()
            common = {
                key: value
                for key, value in inputs.items()
                if key != "data_jffs2"
            }
            first = build_universal_final_bundle(
                **common,
                signing_key=private_key,
            )
            second = build_universal_final_bundle(
                **common,
                signing_key=private_key,
            )
            self.assertEqual(first, second)
            bundle = validate_universal_final_bundle(first, public_key=public_key)
            self.assertEqual(bundle.manifest["artifact_scope"], "model-universal")
            self.assertEqual(
                bundle.manifest["physical_write_policy"],
                universal_physical_write_policy(),
            )
            self.assertEqual(bundle.members["images/data.jffs2"], b"")
            with self.assertRaisesRegex(BundleError, "not model-universal"):
                validate_universal_final_bundle(
                    build_final_bundle(**inputs, signing_key=private_key),
                    public_key=public_key,
                )

    def test_layout_is_fixed_across_firmware_sizes(self) -> None:
        small = derive_final_layout(4096)
        large = derive_final_layout(SYSTEM_FLASH_SPAN)
        self.assertEqual(small, large)
        self.assertEqual(small.system_span + small.data_span, TARGET.partition(3).size)

    def test_system_region_overflow_fails_closed(self) -> None:
        with self.assertRaisesRegex(Mtd3SplitError, "fixed mtd3 system region"):
            derive_final_layout(SYSTEM_FLASH_SPAN + 1)


if __name__ == "__main__":
    unittest.main()
