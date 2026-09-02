from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer import runtime_gate, vendor_bundle
from tests.test_vendor_bundle import make_bundle


def report(bundle_sha256: str, *, audio_process_loaded: bool = False) -> dict[str, object]:
    return {
        "schema_version": 1,
        "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
        "vendor_bundle_sha256": bundle_sha256,
        "tests": {
            "video": {"colors_verified": True, "height": 1080, "passed": True, "width": 1920},
            "aac": {
                "audio_process_loaded": audio_process_loaded,
                "codec": "AAC",
                "passed": True,
            },
            "day_night": {"day_to_night": True, "night_to_day": True, "passed": True},
            "native_media": {
                "stock_iq_copied": False,
                "stock_modules_copied": False,
                "thingino_iq": True,
                "thingino_modules": True,
            },
        },
    }


class RuntimeGateTests(unittest.TestCase):
    def test_report_is_bound_to_bundle_and_all_required_live_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, _raws = make_bundle(root)
            bundle = vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)
            path = root / "runtime.private.json"
            path.write_text(json.dumps(report(bundle.bundle_sha256)), encoding="utf-8")

            decision = runtime_gate.validate_runtime_report(path, bundle)

            self.assertFalse(decision.audio_process_required)

            document = report(bundle.bundle_sha256)
            document["tests"]["video"]["colors_verified"] = False  # type: ignore[index]
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(runtime_gate.RuntimeGateError, "1080p color"):
                runtime_gate.validate_runtime_report(path, bundle)

    def test_audio_process_is_installed_when_runtime_use_is_observed(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, _raws = make_bundle(root)
            bundle = vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)
            path = root / "runtime.private.json"
            path.write_text(
                json.dumps(report(bundle.bundle_sha256, audio_process_loaded=True)),
                encoding="utf-8",
            )

            decision = runtime_gate.validate_runtime_report(path, bundle)

            self.assertTrue(decision.audio_process_required)
            audio = next(artifact for artifact in bundle.artifacts if artifact.name == "libaudioProcess.so")
            self.assertTrue(audio.rootfs)
            self.assertEqual(audio.destination, "usr/lib/libaudioProcess.so")


if __name__ == "__main__":
    unittest.main()
