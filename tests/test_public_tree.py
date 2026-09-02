from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_public_tree.py"
SPEC = importlib.util.spec_from_file_location("check_public_tree", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class PublicTreeTests(unittest.TestCase):
    def test_current_allowlist_passes(self) -> None:
        files = POLICY.validate()
        self.assertIn("policy/public-tree.json", files)
        self.assertNotIn("AGENTS.md", files)

    def test_partition_map_is_contiguous_and_exactly_16_mib(self) -> None:
        path = ROOT / "profiles/dlink-dcs6100lhv2-a1/artifact-limits.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        cursor = 0
        for partition in data["partitions"]:
            self.assertEqual(cursor, partition["offset_bytes"])
            self.assertFalse(partition["write_allowed"])
            cursor += partition["size_bytes"]
        self.assertEqual(data["target"]["nor_size_bytes"], cursor)
        self.assertFalse(data["full_flash_image_allowed"])

    def test_rejects_forbidden_artifact_suffix(self) -> None:
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_public_path("output/camera.uim")

    def test_rejects_private_path(self) -> None:
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_public_path("evidence/private/manifest.json")

    def test_rejects_embedded_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            marker = "-----BEGIN " + "PRIVATE KEY-----\n"
            path.write_text(marker, encoding="utf-8")
            with self.assertRaises(POLICY.PolicyError):
                POLICY.validate_text("bad.txt", path)

    def test_production_rejects_local_host_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            marker = "/" + "Volumes" + "/PersonalDisk/build"
            path.write_text(marker, encoding="utf-8")
            with self.assertRaisesRegex(POLICY.PolicyError, "filesystem path"):
                POLICY.validate_text("bad.txt", path, production=True)

    def test_production_rejects_common_home_lan_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            marker = ".".join(("192", "168", "1", "123"))
            path.write_text(marker, encoding="utf-8")
            with self.assertRaisesRegex(POLICY.PolicyError, "home-LAN"):
                POLICY.validate_text("bad.txt", path, production=True)

    def test_production_rejects_high_confidence_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.txt"
            marker = "gh" + "p_" + "a" * 32
            path.write_text(marker, encoding="utf-8")
            with self.assertRaisesRegex(POLICY.PolicyError, "GitHub token"):
                POLICY.validate_text("bad.txt", path, production=True)

    def test_discovers_unmanifested_public_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unexpected.txt").write_text("public", encoding="utf-8")
            with mock.patch.object(POLICY, "ROOT", root):
                self.assertEqual(POLICY.discover_public_files(), ["unexpected.txt"])

    def test_ignores_editable_install_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "thingino_dlink_dcs6100lhv2.egg-info"
            metadata.mkdir()
            (metadata / "PKG-INFO").write_text("generated", encoding="utf-8")
            with mock.patch.object(POLICY, "ROOT", root):
                self.assertEqual(POLICY.discover_public_files(), [])

    def test_ignores_nested_webui_build_and_dependency_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "webui" / "dist"
            dependency = root / "webui" / "node_modules" / "package"
            build.mkdir(parents=True)
            dependency.mkdir(parents=True)
            (build / "app.js").write_text("built", encoding="utf-8")
            (dependency / "index.js").write_text("dependency", encoding="utf-8")
            with mock.patch.object(POLICY, "ROOT", root):
                self.assertEqual(POLICY.discover_public_files(), [])


if __name__ == "__main__":
    unittest.main()
