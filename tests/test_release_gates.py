from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/check_release_gates.py"
SPEC = importlib.util.spec_from_file_location("check_release_gates", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
GATES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATES)


class ReleaseGateTests(unittest.TestCase):
    def test_public_firmware_gate_is_named_and_local_build_is_independent(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        local_build = (ROOT / "installer/local_build_run.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("release-ready-public-firmware:", makefile)
        self.assertNotIn("release-ready-firmware:", makefile)
        self.assertNotIn("check_release_gates", local_build)
        self.assertNotIn("release-ready-public-firmware", local_build)

    def test_current_ledger_allows_disclosed_source_but_blocks_firmware(self) -> None:
        summaries = GATES.validate()
        self.assertTrue(summaries["source-publication"]["ready"])
        self.assertEqual(summaries["source-publication"]["blocked"], [])
        self.assertEqual(summaries["source-publication"]["closed"], 4)
        self.assertFalse(summaries["firmware-release"]["ready"])
        self.assertIn(
            "prudynt-license-grant",
            summaries["firmware-release"]["blocked"],
        )

    def test_rtl_license_copies_match_the_reviewed_primary_sources(self) -> None:
        expected = {
            "RTL8188FU-GPL-2.0-only.txt": "4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e",
            "RTL8188FU-hostap-BSD-3-Clause.txt": "2c2b3640a8256edb409356a7e3f3dfe762a489bd2cd9586aa1647b0f1128caf8",
        }
        for name, digest in expected.items():
            payload = (ROOT / "third_party/licenses" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_rejects_ready_scope_with_a_blocked_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.txt"
            evidence.write_text("checked\n", encoding="utf-8")
            document = {
                "schema_version": 1,
                "status_date": "2026-08-29",
                "scopes": [
                    {
                        "id": "source-publication",
                        "ready": True,
                        "gates": [
                            {
                                "id": "license",
                                "status": "blocked",
                                "requirement": "Record the license grant.",
                                "evidence": ["evidence.txt"],
                            }
                        ],
                    }
                ],
            }
            with self.assertRaisesRegex(GATES.ReleaseGateError, "disagrees"):
                GATES.validate_document(document, root=root)

    def test_rejects_unsafe_evidence(self) -> None:
        document = json.loads(
            (ROOT / "policy/release-gates.json").read_text(encoding="utf-8")
        )
        document["scopes"][0]["gates"][0]["evidence"] = ["../private.txt"]
        with self.assertRaisesRegex(GATES.ReleaseGateError, "unsafe evidence"):
            GATES.validate_document(document)


if __name__ == "__main__":
    unittest.main()
