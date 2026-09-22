"""Focused tests for the public-safe Raptor source inventory."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import source_delivery_inventory as inventory


ROOT = Path(__file__).resolve().parents[1]


class SourceDeliveryInventoryTests(unittest.TestCase):
    def test_published_verified_inventory_matches_current_inputs_and_retains_legal_blocks(self) -> None:
        published = json.loads((ROOT / "third_party/raptor-source-delivery.inventory.json").read_text())
        expected = inventory.build_source_delivery_inventory(ROOT)
        self.assertEqual(published["technical_status"], "source-lock-and-reconstructed-cache-verified")
        self.assertTrue(published["verified_cache"]["all_verified"])
        self.assertEqual(published["verified_cache"]["source_count"], 13)
        self.assertEqual(set(published["verified_cache"]["sources"]), set(expected["sources"]))
        for key in expected:
            if key not in {"technical_status", "verified_cache"}:
                self.assertEqual(published[key], expected[key], key)
        self.assertEqual(published["legal_review_status"], "not-assessed")
        self.assertEqual(published["redistribution"], "not-authorized-by-this-repository")

    def test_embedded_notice_payloads_retain_exact_upstream_text_identities(self) -> None:
        expected = {
            "raptor-common-cJSON-header.txt": "3384d75264549cd04a5c00538a15871785f3f6ba60a779781df747d591655892",
            "raptor-common-Monocypher-LICENCE.txt": "5f8360e4c06ddcc584bdb4b210c6af824c4bb301e6a9a521869b6d90795ca4b3",
        }
        for name, digest in expected.items():
            payload = (ROOT / "third_party/licenses" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest, name)
        cjson = (ROOT / "third_party/licenses/raptor-common-cJSON-header.txt").read_text()
        self.assertIn("Copyright (c) 2009-2017 Dave Gamble and cJSON contributors", cjson)
        self.assertIn("permission notice shall be included", cjson)
        mono = (ROOT / "third_party/licenses/raptor-common-Monocypher-LICENCE.txt").read_text()
        self.assertIn("Licence 1 (2-clause BSD)", mono)
        self.assertIn("Licence 2 (CC-0)", mono)

    def test_sound_text_and_recorded_inventory_preserve_scope(self) -> None:
        # This checks the checked-in record, not the private firmware image.
        document = json.loads((ROOT / "third_party/thingino-sounds.inventory.json").read_text())
        license_info = document["supplemental_license"]
        payload = (ROOT / license_info["path"]).read_bytes()
        digest = "a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499"
        self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        self.assertEqual(license_info["sha256"], digest)
        self.assertEqual(len(payload), 7048)
        self.assertEqual(document["package_license_declaration"], "CC0")
        files = document["files"]
        self.assertEqual(document["file_count"], len(files))
        self.assertEqual(len(files), 12)
        self.assertEqual(len({item["installed_path"] for item in files}), 12)
        self.assertEqual(sum(item["bytes"] for item in files), document["total_bytes"])
        self.assertEqual(document["total_bytes"], 99287)
        for item in files:
            name = Path(item["installed_path"]).name
            self.assertTrue(name.endswith(".opus"))
            self.assertEqual(item["installed_path"], f"usr/share/sounds/{name}")
            self.assertEqual(item["source_path"], f"package/thingino-sounds/files/{name}")
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(item["bytes"], 0)
        for field in ("upstream_license_file_present", "package_source_archive_delivered",
                      "legal_review_approved", "publication_authorized"):
            self.assertIs(document[field], False)
        self.assertIn("Raptor motion.pcm and other packages audio files", document["excluded"])

    def test_current_lock_renders_all_sources_without_local_paths(self) -> None:
        document = inventory.build_source_delivery_inventory(ROOT)

        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["technical_status"], "source-lock-inventory-only")
        self.assertEqual(document["legal_review_status"], "not-assessed")
        self.assertEqual(document["redistribution"], "not-authorized-by-this-repository")
        self.assertEqual(document["source_count"], 13)
        self.assertEqual(len(document["sources"]), 13)
        self.assertEqual(document["verified_cache"]["provided"], False)
        self.assertEqual(document["verified_cache"]["sources"], {})

        encoded = json.dumps(document, sort_keys=True)
        self.assertNotIn(str(ROOT), encoded)
        self.assertNotIn(str(Path(tempfile.gettempdir())), encoded)
        self.assertEqual(
            document["sources"]["raptor"]["license_or_notice"]["kind"],
            "license",
        )
        self.assertEqual(
            document["sources"]["ingenic-headers"]["license_or_notice"]["kind"],
            "notice",
        )
        self.assertEqual(
            document["sources"]["raptor"]["final_tree"],
            inventory.source_lock(ROOT, full_media=True)["sources"]["raptor"]["tree"],
        )

    def test_existing_output_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"])) as temporary:
            output = Path(temporary) / "inventory.json"
            output.write_bytes(b"preserve-existing-evidence")
            with self.assertRaisesRegex(inventory.SourceDeliveryInventoryError, "output already exists"):
                inventory.write_inventory(output, ROOT)
            self.assertEqual(output.read_bytes(), b"preserve-existing-evidence")

    def test_local_cache_uses_verify_source_and_emits_relative_names_only(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=Path(os.environ["TMPDIR"]).resolve(strict=True)
        ) as temporary:
            cache = Path(temporary) / "private-cache"
            cache.mkdir()
            source_names = sorted(inventory.source_lock(ROOT, full_media=True)["sources"])
            for name in source_names:
                (cache / name).mkdir()

            calls: list[tuple[Path, str, str]] = []

            def fake_verify(path, spec, *, tree):
                calls.append((path, spec["url"], tree))
                return "a" * 64

            with mock.patch.object(inventory, "verify_source", side_effect=fake_verify):
                document = inventory.build_source_delivery_inventory(
                    ROOT, verified_cache_root=cache
                )

            self.assertEqual(len(calls), 13)
            self.assertEqual(document["technical_status"], "source-lock-and-reconstructed-cache-verified")
            self.assertTrue(document["verified_cache"]["all_verified"])
            self.assertEqual(
                document["verified_cache"]["sources"]["raptor"],
                {"path": "raptor", "tree_digest": "a" * 64},
            )
            encoded = json.dumps(document, sort_keys=True)
            self.assertNotIn(str(cache), encoded)
            self.assertNotIn("private-cache", encoded)
            self.assertEqual({path.name for path, _, _ in calls}, set(source_names))

    def test_cache_integrity_failure_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=Path(os.environ["TMPDIR"]).resolve(strict=True)
        ) as temporary:
            cache = Path(temporary) / "private-cache"
            cache.mkdir()
            for name in inventory.source_lock(ROOT, full_media=True)["sources"]:
                (cache / name).mkdir()

            def fail_verify(path, spec, *, tree):
                raise ValueError(f"private path: {path}")

            with mock.patch.object(inventory, "verify_source", side_effect=fail_verify):
                with self.assertRaisesRegex(
                    inventory.SourceDeliveryInventoryError,
                    r"^verified cache integrity check failed: compy$",
                ):
                    inventory.build_source_delivery_inventory(
                        ROOT, verified_cache_root=cache
                    )


if __name__ == "__main__":
    unittest.main()
