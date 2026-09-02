from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.collector import output
from installer.collector.output import CollectorOutputError
from installer.recovery_gate import RecoveryDecision
from installer.vendor_bundle import ElfMetadata, VendorArtifact, VendorBundle


def _bundle(*, audio: bool) -> VendorBundle:
    names = ["libimp.so", "libalog.so", "libsysutils.so"]
    if audio:
        names.append("libaudioProcess.so")
    artifacts = tuple(
        VendorArtifact(
            name=name,
            destination=None,
            source_path=f"lib/{name}",
            raw=b"",
            sha256="0" * 64,
            elf=ElfMetadata(flags=0, needed=(), soname=name),
            rootfs=name != "libaudioProcess.so",
        )
        for name in names
    )
    return VendorBundle(
        artifacts=artifacts,
        bundle_sha256="1" * 64,
        firmware_version="1.02.02",
        manifest_sha256="2" * 64,
    )


class CollectorOutputTests(unittest.TestCase):
    def _result(self, root: Path, *, audio: bool) -> Path:
        result = root / "DCS6100A1"
        (result / "vendor").mkdir(parents=True)
        (result / "preserved").mkdir()
        (result / "COLLECT.OK").write_text(
            json.dumps(
                {
                    "audio_process_archived": audio,
                    "mode": "existing-verified-same-device-pair",
                    "nor_writes": False,
                    "schema_version": 1,
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return result

    def test_closed_output_requires_both_existing_host_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._result(root, audio=True)
            with (
                mock.patch.object(output, "load_vendor_bundle", return_value=_bundle(audio=True)) as vendor,
                mock.patch.object(
                    output,
                    "validate_existing_recovery_boundary",
                    return_value=RecoveryDecision(
                        mode="existing-verified-same-device-pair",
                        preserved_mtd=(0, 4, 5),
                        recovery_images=2,
                    ),
                ) as recovery,
            ):
                decision = output.validate_collector_output(
                    collector_output_dir=result,
                    recovery_dir=root / "recovery",
                )
            self.assertTrue(decision.audio_process_archived)
            self.assertEqual(decision.recovery_images, 2)
            self.assertEqual(decision.vendor_files[-1], "libaudioProcess.so")
            vendor.assert_called_once_with(result / "vendor")
            recovery.assert_called_once_with(
                recovery_dir=root / "recovery",
                preserved_readback_dir=result / "preserved",
            )

    def test_completion_cannot_claim_an_absent_optional_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._result(root, audio=True)
            with mock.patch.object(
                output, "load_vendor_bundle", return_value=_bundle(audio=False)
            ):
                with self.assertRaisesRegex(CollectorOutputError, "audio disposition"):
                    output.validate_collector_output(
                        collector_output_dir=result,
                        recovery_dir=root / "recovery",
                    )

    def test_extra_entry_or_write_claim_fails_before_component_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._result(root, audio=False)
            (result / "extra").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(CollectorOutputError, "closure"):
                output.validate_collector_output(
                    collector_output_dir=result,
                    recovery_dir=root / "recovery",
                )
            (result / "extra").unlink()
            completion = json.loads((result / "COLLECT.OK").read_text(encoding="utf-8"))
            completion["nor_writes"] = True
            (result / "COLLECT.OK").write_text(json.dumps(completion), encoding="utf-8")
            with self.assertRaisesRegex(CollectorOutputError, "contract differs"):
                output.validate_collector_output(
                    collector_output_dir=result,
                    recovery_dir=root / "recovery",
                )

    def test_protected_output_is_vendor_independent_and_same_device_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "protected"
            (result / "preserved").mkdir(parents=True)
            (result / "PROTECT.OK").write_text(
                json.dumps(
                    {
                        "mode": "protected-readback",
                        "nor_writes": False,
                        "partition_storage_readback_verified": True,
                        "schema_version": 1,
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                output,
                "validate_existing_recovery_boundary",
                return_value=RecoveryDecision(
                    mode="existing-verified-same-device-pair",
                    preserved_mtd=(0, 4, 5),
                    recovery_images=2,
                ),
            ) as recovery:
                decision = output.validate_protected_collector_output(
                    collector_output_dir=result,
                    recovery_dir=root / "recovery",
                )
            self.assertEqual(decision.recovery_images, 2)
            self.assertEqual(decision.preserved_mtd, (0, 4, 5))
            recovery.assert_called_once_with(
                recovery_dir=root / "recovery",
                preserved_readback_dir=result / "preserved",
            )

    def test_protected_output_rejects_extra_file_or_changed_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "protected"
            (result / "preserved").mkdir(parents=True)
            completion = result / "PROTECT.OK"
            completion.write_text(
                '{"mode":"protected-readback","nor_writes":false,'
                '"partition_storage_readback_verified":true,"schema_version":1}\n',
                encoding="utf-8",
            )
            (result / "unexpected").write_text("no", encoding="utf-8")
            with self.assertRaisesRegex(CollectorOutputError, "closure"):
                output.validate_protected_collector_output(
                    collector_output_dir=result,
                    recovery_dir=root / "recovery",
                )
            (result / "unexpected").unlink()
            completion.write_text(
                '{"mode":"protected-readback","nor_writes":true,'
                '"partition_storage_readback_verified":true,"schema_version":1}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CollectorOutputError, "contract"):
                output.validate_protected_collector_output(
                    collector_output_dir=result,
                    recovery_dir=root / "recovery",
                )


if __name__ == "__main__":
    unittest.main()
