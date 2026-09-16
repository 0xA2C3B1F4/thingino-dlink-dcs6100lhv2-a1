"""Exercise build boundaries without starting Docker or touching a camera."""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import raptor_full_build as build, raptor_full_component as component
from installer.raptor_full_component import PAYLOAD
from installer.raptor_source import source_lock
from test_raptor_full_component import FONT_FIXTURE, MOTION_CLIP_FIXTURE, elf


ROOT = Path(__file__).resolve().parents[1]


class FullBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "cache").mkdir(mode=0o700)
        self.run = self.root / "run"
        self.run.mkdir()
        self.base = self.root / "base.squashfs"
        self.workspace = self.root / "base.ext4"
        self.sdk = self.root / "sdk.tar.gz"
        for path in (self.base, self.workspace, self.sdk):
            path.write_bytes(b"host-test fixture")
        self.sdk_hash = json.loads((ROOT / "sources.lock.json").read_bytes())[
            "sources"]["thingino_build_toolchain_aarch64"]["sha256"]
        self.receipt = {
            "sources": {name: {"tree": spec["tree"]} for name, spec in
                        source_lock(ROOT, full_media=True)["sources"].items()}
        }
        self.calls = []
        license_fixture = b"fixture font licence\n"
        self.payload = {name: elf() for name in PAYLOAD - component.DATA_FILES}
        self.payload[component.FONT] = FONT_FIXTURE
        self.payload[component.FONT_LICENSE] = license_fixture
        self.payload[component.LIBSCHRIFT_LICENSE] = b"fixture libschrift licence\n"
        self.payload[component.MOTION_CLIP] = MOTION_CLIP_FIXTURE
        for name, value in (
            ("FONT_SHA256", hashlib.sha256(FONT_FIXTURE).hexdigest()),
            ("FONT_LICENSE_SHA256", hashlib.sha256(license_fixture).hexdigest()),
            ("LIBSCHRIFT_LICENSE_SHA256", hashlib.sha256(
                self.payload[component.LIBSCHRIFT_LICENSE]
            ).hexdigest()),
        ):
            replacement = patch.object(component, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def build(self, run=None, **kwargs):
        return build.build_full_component(
            root=ROOT, build_root=self.root, run_dir=run or self.run,
            builder_image="sha256:" + "2" * 64, base_rootfs=self.base,
            base_workspace=self.workspace, toolchain=self.sdk,
            **kwargs,
        )

    def inputs(self):
        stack = ExitStack()
        stack.enter_context(patch.object(build, "acquire_sources", return_value=(self.root, self.receipt)))
        stack.enter_context(patch.object(build.subprocess, "run"))
        stack.enter_context(patch.object(build.shutil, "disk_usage", return_value=SimpleNamespace(free=8 * 1024**3)))
        stack.enter_context(patch.object(build, "_sha256", side_effect=lambda path:
            self.sdk_hash if path == self.sdk else hashlib.sha256(path.read_bytes()).hexdigest()))
        return stack

    def compile_fixture(self, command, **kwargs):
        self.calls.append(command)
        if command[-1] != build.COMPILE:
            return
        result_mount = next(
            item for item in command
            if item.startswith("type=bind,src=") and item.endswith(",dst=/result")
        )
        destination = Path(result_mount.removeprefix("type=bind,src=").removesuffix(",dst=/result")) / "root"
        for relative, raw in self.payload.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def test_recipe_builds_static_rod_and_installs_locked_font_data(self):
        recipe = (ROOT / "scripts/container_build_raptor_full.sh").read_text()
        self.assertIn("rvd rhd rsd ric rad rod rmr raptorctl", recipe)
        self.assertIn("libschrift.a", recipe)
        self.assertIn("-L/work/src/libschrift", recipe)
        self.assertNotIn("libschrift.so", recipe)
        self.assertIn("resources/Ubuntu-Regular.ttf", recipe)
        self.assertIn("install -m 0644 /font-license", recipe)
        self.assertIn("install -m 0644 /work/src/libschrift/LICENSE", recipe)

        schrift_compile = next(
            line for line in recipe.splitlines()
            if "/libschrift/schrift.c" in line and '"$cc"' in line
        )
        self.assertIn("$flags -std=c99 -I/work/src/libschrift", schrift_compile)
        self.assertEqual(recipe.count("-std=c99"), 1)

    def test_offline_compile_and_reuse_of_identical_model_component(self):
        with self.inputs(), patch.object(build, "run_container", side_effect=self.compile_fixture):
            first = self.build()
            self.assertFalse(first["cached"])
            other_run = self.root / "another-run"
            other_run.mkdir()
            second = self.build(other_run)
            self.assertTrue(second["cached"])
            self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(len(self.calls), 2)
        for command in self.calls:
            self.assertIn("--network=none", command)
        compile_command = self.calls[1]
        for flag in ("--cpus=2", "--memory=2g", "--memory-swap=2g", "--pids-limit=256"):
            self.assertIn(flag, compile_command)
        self.assertIn(f"type=bind,src={self.base},dst=/base.squashfs,readonly", compile_command)
        self.assertIn(f"type=bind,src={self.workspace},dst=/base-workspace.ext4,readonly", compile_command)
        self.assertIn(
            f"type=bind,src={ROOT / 'components/raptor/Ubuntu-Font-Licence-1.0.txt'},dst=/font-license,readonly",
            compile_command,
        )
        self.assertFalse((other_run / "raptor-full").exists())

    def test_independent_build_bypasses_shared_component_cache(self):
        with self.inputs(), patch.object(build, "run_container", side_effect=self.compile_fixture):
            first = self.build()
            other_run = self.root / "independent-run"
            other_run.mkdir()
            second = self.build(other_run, artifact_cache=False)
        self.assertFalse(first["cached"])
        self.assertFalse(second["cached"])
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(
            Path(second["artifact"]),
            other_run / "raptor-full/raptor-full-component.tar.gz",
        )
        self.assertEqual(len(self.calls), 4)

    def test_recipe_generates_the_exact_packaged_motion_clip(self):
        recipe = (ROOT / "scripts/container_build_raptor_full.sh").read_text()
        marker = "python3 - /result/root/usr/share/raptor/audio/motion.pcm <<'PYCODE'\n"
        self.assertEqual(recipe.count(marker), 1)
        program = recipe.split(marker, 1)[1].split("\nPYCODE", 1)[0]
        output = self.root / "motion.pcm"
        subprocess.run([sys.executable, "-c", program, str(output)], check=True)
        self.assertEqual(output.read_bytes(), MOTION_CLIP_FIXTURE)
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), component.MOTION_CLIP_SHA256)
        self.assertIn(component.MOTION_CLIP, PAYLOAD)

    def test_compile_failure_never_publishes_component(self):
        def fail(command, **kwargs):
            if command[-1] == build.COMPILE:
                raise build.LocalBuildRunError("compile probe failed")

        with self.inputs(), patch.object(build, "run_container", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "compile probe"):
                self.build()
        self.assertTrue((self.run / "raptor-full/FAILED").is_file())
        self.assertEqual(list((self.root / "cache/downloads/raptor-full-artifacts").iterdir()), [])

    def test_sdk_identity_mismatch_stops_before_any_container(self):
        with patch.object(build, "run_container") as run:
            with self.assertRaisesRegex(ValueError, "SDK identity"):
                self.build()
            run.assert_not_called()

    def test_mutable_builder_name_stops_before_acquiring_sources(self):
        with patch.object(build, "acquire_sources") as acquire:
            with self.assertRaisesRegex(ValueError, "immutable image ID"):
                build.build_full_component(
                    root=ROOT, build_root=self.root, run_dir=self.run,
                    builder_image="thingino-builder:latest", base_rootfs=self.base,
                    base_workspace=self.workspace, toolchain=self.sdk,
                )
            acquire.assert_not_called()

    def test_corrupt_cached_component_does_not_fall_back_to_rebuilding(self):
        with self.inputs(), patch.object(build, "run_container", side_effect=self.compile_fixture) as run:
            first = self.build()
            artifact = Path(first["artifact"])
            artifact.chmod(0o600)
            artifact.write_bytes(b"corrupt")
            run.reset_mock()
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.build()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
