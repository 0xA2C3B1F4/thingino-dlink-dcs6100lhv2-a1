"""The installer compiles full Raptor only after preparing its own base."""

from pathlib import Path
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from installer import local_build_package, local_build_run, raptor_full_build, raptor_full_root


class FullPipelineTests(unittest.TestCase):
    def test_download_fetch_uses_the_same_raptor_support_fragment(self):
        repository = Path(__file__).resolve().parents[1]
        fetch = (repository / "scripts/container_fetch_thingino_downloads.sh").read_text()
        self.assertIn(
            'media_fragment=$source_dir/configs/cameras-exp/$profile/raptor-support.fragment',
            fetch,
        )
        self.assertIn('set -- "THINGINO_USER_FRAGMENT_FILES=$media_fragment"', fetch)
        self.assertIn('GROUP=exp "$@" defconfig', fetch)
        self.assertIn('GROUP=exp "$@" source', fetch)
        self.assertIn("grep -Fxq 'BR2_PACKAGE_THINGINO_STREAMER_NONE=y'", fetch)
        self.assertIn("grep -Eq '^BR2_PACKAGE_.*PRUDYNT.*=y$'", fetch)
        self.assertIn("grep -Fxq 'BR2_THINGINO_LIBSTDCPP=y'", fetch)
        self.assertGreaterEqual(
            fetch.count("grep -Fxq 'BR2_PACKAGE_THINGINO_STREAMER_NONE=y'"), 2
        )

    def test_support_build_gate_rejects_retired_media_and_cpp_runtime(self):
        repository = Path(__file__).resolve().parents[1]
        script = (repository / "scripts/container_build_thingino.sh").read_text()
        gate_start = "grep -Fxq 'BR2_PACKAGE_THINGINO_STREAMER_NONE=y'"
        gate_end = 'test -x "$output_dir/target/usr/sbin/wpa_supplicant"'
        gate = gate_start + script.rsplit(gate_start, 1)[1].split(gate_end, 1)[0]
        fragment = (repository / "profiles/dlink-dcs6100lhv2-a1/raptor-support.fragment").read_text()
        self.assertIn("# BR2_THINGINO_LIBSTDCPP is not set", fragment)
        self.assertNotIn("BR2_THINGINO_LIBSTDCPP=y", fragment)
        for option in (
            "THINGINO_STREAMER_NONE", "INGENIC_LIB", "INGENIC_LIB_LIBIMP",
            "INGENIC_SYSTEM_LIBS_NEO", "INGENIC_SYSTEM_LIBS_NEO_LIBALOG",
            "INGENIC_SYSTEM_LIBS_NEO_LIBSYSUTILS", "LIBAUDIOPROCESS_NEO",
            "THINGINO_DAYNIGHTD", "THINGINO_ONVIF",
        ):
            self.assertIn(f"BR2_PACKAGE_{option}=y", fragment)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            (output / "build").mkdir()
            config = output / ".config"
            config.write_text("BR2_PACKAGE_THINGINO_STREAMER_NONE=y\n")

            def check():
                return subprocess.run(
                    ["sh", "-c", "set -eu\n" + gate],
                    env={**os.environ, "output_dir": str(output)},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                ).returncode

            self.assertEqual(check(), 0)
            config.write_text("BR2_PACKAGE_THINGINO_STREAMER_NONE=y\nBR2_PACKAGE_PRUDYNT_T=y\n")
            self.assertNotEqual(check(), 0)
            config.write_text("BR2_PACKAGE_THINGINO_STREAMER_NONE=y\nBR2_THINGINO_LIBSTDCPP=y\n")
            self.assertNotEqual(check(), 0)
            config.write_text("BR2_PACKAGE_THINGINO_STREAMER_NONE=y\n")
            for relative in (
                "target/usr/bin/prudynt", "target/etc/init.d/S31prudynt",
                "per-package/prudynt-t", "build/prudynt-t-test",
                "target/usr/lib/libstdc++.so.6.0.35", "target/lib/libstdc++.so.6",
            ):
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"unexpected")
                self.assertNotEqual(check(), 0, relative)
                path.unlink()
            dangling = output / "target/usr/lib/libstdc++.so.6"
            dangling.symlink_to("libstdc++.so.6.0.35")
            self.assertNotEqual(check(), 0)
            dangling.unlink()
            self.assertEqual(check(), 0)

    def test_full_selection_reaches_both_clean_base_builds(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve()
            inputs = SimpleNamespace(root=run, build_count=2, audio_link=run / "audio")
            environment = SimpleNamespace(**{
                name: run / name for name in (
                    "prepared_source", "download_cache", "vendor_site", "rust_source",
                    "rust_toolchain", "ingenic_toolchain_archive", "builder_image",
                )
            })
            with (
                patch.object(local_build_run, "_run_clean_build", return_value=(run, run)) as build,
                patch.object(local_build_run, "_compare_clean_builds", return_value={"byte_identical": True}),
            ):
                local_build_run._build_clean_roots(inputs, environment, run)
            self.assertEqual(build.call_count, 2)
            self.assertTrue(all("full_raptor" not in call.kwargs for call in build.call_args_list))

    def test_full_build_wrapper_never_selects_a_legacy_media_closure(self):
        with patch.object(
            local_build_run,
            "_build_local_install_set",
            return_value={"artifact_scope": "model-universal"},
        ) as build:
            result = local_build_run.build_local_universal_install_set(
                build_root=Path("unused"),
                vendor_bundle_dir=Path("unused"),
                signing_key=Path("unused"),
            )
        self.assertTrue(result["raptor_full_source_build"])
        values = build.call_args.kwargs
        self.assertNotIn("media_closure_dir", values)
        self.assertNotIn("full_raptor", values)

    def exercise(self, *, failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve()
            events = []
            inputs = SimpleNamespace(
                root=run, build_root=run, artifact_scope="model-universal",
                vendor_bundle=object(),
            )
            environment = SimpleNamespace(
                builder_image="sha256:" + "a" * 64,
                thingino_toolchain_archive=run / "same-sdk.tar.gz",
            )
            clean = SimpleNamespace(result=run, workspace=run / "same-build.ext4")
            (run / "thingino-base.squashfs").write_bytes(b"clean-base")

            def prepare(*, output_dir, **kwargs):
                events.append("prepare")
                self.assertTrue(kwargs["support_only"])
                output_dir.mkdir()
                (output_dir / "system.universal.squashfs").write_bytes(b"prepared-base")
                (output_dir / "final-root.universal.json").write_bytes(b"prepared-manifest")

            def compile(**kwargs):
                events.append("compile")
                self.assertEqual(kwargs["base_rootfs"].read_bytes(), b"prepared-base")
                self.assertEqual(kwargs["base_workspace"], clean.workspace)
                self.assertEqual(kwargs["toolchain"], environment.thingino_toolchain_archive)
                if failure:
                    raise ValueError("full compile failed")
                return {
                    "artifact": str(run / "full.tar.gz"), "sha256": "b" * 64,
                    "identity": {"build_inputs": {"base": "fresh"}},
                }

            def compose(**kwargs):
                events.append("compose")
                self.assertEqual(kwargs["component_artifact"], run / "full.tar.gz")
                self.assertEqual(kwargs["component_sha256"], "b" * 64)
                self.assertEqual(kwargs["base_manifest"].read_bytes(), b"prepared-manifest")
                self.assertEqual(kwargs["build_inputs"], {"base": "fresh"})
                output = kwargs["output_dir"]
                output.mkdir()
                (output / "system.universal.squashfs").write_bytes(b"full-media")

            with (
                patch.object(local_build_package, "_workspace_tool", return_value=run / "tool"),
                patch.object(local_build_package, "prepare_universal_final_root", side_effect=prepare),
                patch.object(raptor_full_build, "build_full_component", side_effect=compile),
                patch.object(raptor_full_root, "compose_universal_root", side_effect=compose),
            ):
                if failure:
                    with self.assertRaisesRegex(ValueError, "full compile failed"):
                        local_build_package.prepare_install_root(
                            inputs, environment, clean, run,
                        )
                    self.assertFalse((run / "raptor-final-root").exists())
                    self.assertEqual(events, ["prepare", "compile"])
                else:
                    result = local_build_package.prepare_install_root(
                        inputs, environment, clean, run,
                    )
                    self.assertEqual(result.system, b"full-media")
                    self.assertEqual(events, ["prepare", "compile", "compose"])

    def test_same_base_and_sdk_reach_full_component_and_composition(self):
        self.exercise()

    def test_compile_failure_never_composes_a_firmware(self):
        self.exercise(failure=True)


if __name__ == "__main__":
    unittest.main()
