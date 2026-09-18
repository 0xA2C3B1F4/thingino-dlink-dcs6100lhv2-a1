"""Keep full-media inputs separate from the retired RWD-only closure."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer import raptor_source


ROOT = Path(__file__).resolve().parents[1]


class FullMediaSourceTests(unittest.TestCase):
    def test_full_media_patch_contains_fixqp_native_fixture(self):
        lock = raptor_source.source_lock(ROOT, full_media=True)
        patches = lock["sources"]["raptor"]["patches"]
        self.assertEqual(len(patches), 1)
        patch_path = ROOT / patches[0]["path"]
        fixture = "tests/test_stream_fixqp.c"
        with TemporaryDirectory(prefix="raptor-full-fixqp-fixture-") as work:
            subprocess.run(
                ["git", "apply", f"--include={fixture}", str(patch_path)],
                cwd=work, check=True, capture_output=True,
            )
            source = (Path(work) / fixture).read_text()
            self.assertIn("rvd_stream_rc_config_command", source)
            self.assertIn("test_startup_readback", source)
            self.assertIn("RSS_RC_FIXQP", source)

    @unittest.skipUnless(
        (ROOT / "production").is_dir(),
        "Incremental patch provenance is development-only; the aggregate is tested separately",
    )
    def test_fixqp_increment_contains_its_locked_native_fixture(self):
        directory = ROOT / "docs/development/raptor-full/webui"
        lock = json.loads((directory / "stream-fixqp-source-lock.json").read_text())
        patch_path = directory / lock["patch"]
        self.assertEqual(hashlib.sha256(patch_path.read_bytes()).hexdigest(),
                         lock["patch_sha256"])
        fixture = "tests/test_stream_fixqp.c"
        with TemporaryDirectory(prefix="raptor-fixqp-fixture-") as work:
            subprocess.run(
                ["git", "apply", f"--include={fixture}", str(patch_path)],
                cwd=work, check=True, capture_output=True,
            )
            self.assertEqual(
                hashlib.sha256((Path(work) / fixture).read_bytes()).hexdigest(),
                lock["files"][fixture]["sha256"],
            )

    def test_full_media_pins_sensor_owner_and_tls_sources(self):
        lock = raptor_source.source_lock(ROOT, full_media=True)
        self.assertEqual(set(lock["sources"]), raptor_source.FULL_MEDIA_SOURCES)
        self.assertEqual(len(lock["sources"]), 13)
        self.assertEqual(lock["sources"]["libschrift"]["base"],
                         "8e533fd07acc2f8ae4cffe7f95d2c3392773e2b5")
        self.assertEqual(
            lock["sources"]["raptor"]["tree"],
            "0f05599901aeb93e6d1ab65f46abb04c9a65884a",
        )
        self.assertNotIn("runtime_tree", lock)
        self.assertEqual(
            lock["sources"]["ingenic-headers"]["tree"],
            "cdc342f404af4cec5d078b6a1d7bfc88d32e90c9",
        )

    def test_full_media_recipe_is_the_only_public_raptor_recipe(self):
        full = raptor_source.recipe_identity(ROOT, full_media=True)
        self.assertEqual(full, raptor_source.recipe_identity(ROOT))
        self.assertIn("components/raptor/headers-input.json", full)
        self.assertIn("scripts/container_build_raptor_full.sh", full)
        self.assertFalse(any("components/raptor-rwd/" in path for path in full))
        self.assertNotIn("installer/raptor_build.py", full)

    def test_full_media_patch_mutation_fails_before_acquisition(self):
        with patch.object(raptor_source, "digest", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "patch identity"):
                raptor_source.source_lock(ROOT, full_media=True)

    def test_headers_notice_is_not_a_license_or_redistribution_approval(self):
        original = raptor_source._load_json_object
        for mutation in (
            {"license_status": "approved"},
            {"redistribution": "allowed"},
            {"license": "README.md"},
            {"notice": "../LICENSE"},
        ):
            def changed(path, label):
                value = deepcopy(original(path, label))
                if path.name == "headers-input.json":
                    value.update(mutation)
                return value

            with self.subTest(mutation=mutation):
                with patch.object(raptor_source, "_load_json_object", side_effect=changed):
                    with self.assertRaises(ValueError):
                        raptor_source.source_lock(ROOT, full_media=True)

    def test_full_media_rejects_a_second_runtime_patch_layer(self):
        original = raptor_source._load_json_object

        def changed(path, label):
            value = deepcopy(original(path, label))
            if path.name == "source-build-lock.json":
                value["runtime_tree"] = "0" * 40
            return value

        with patch.object(raptor_source, "_load_json_object", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "final source trees"):
                raptor_source.source_lock(ROOT, full_media=True)


if __name__ == "__main__":
    unittest.main()
