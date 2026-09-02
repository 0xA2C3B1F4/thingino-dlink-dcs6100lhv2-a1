from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import local_build_run


class LocalBuildRunTests(unittest.TestCase):
    def test_build_runs_two_clean_builds_overlay_split_and_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            project.mkdir()
            build_root = root / "build-root"
            (build_root / "runs").mkdir(parents=True)
            cache = build_root / "cache"
            for relative in ("downloads", "sources"):
                (cache / relative).mkdir(parents=True)
            private = root / "private"
            for relative in ("vendor", "media/files/lib", "config", "session"):
                (private / relative).mkdir(parents=True)
            audio = private / "media/files/lib/libaudioProcess.so"
            audio.write_bytes(b"audio")
            wpa = private / "current-wpa.conf"
            wpa.write_bytes(b"wpa")
            artifact = private / "raptor.tar.gz"
            artifact.write_bytes(b"raptor")
            acquired_root = root / "acquired"
            for relative in ("source", "rust-source", "rust-toolchain"):
                (acquired_root / relative).mkdir(parents=True)
            ingenic_archive = acquired_root / "ingenic.tar"
            ingenic_archive.write_bytes(b"ingenic")

            def prepare_source(_checkout: Path, destination: Path, **_: object) -> None:
                destination.mkdir()
                (destination / "dcs6100-source-preparation.json").write_text("{}")

            def prepare_vendor(*, output_dir: Path, **_: object) -> None:
                output_dir.mkdir()

            clean_calls: list[str] = []

            def clean_build(*, run_dir: Path, label: str, **_: object):
                clean_calls.append(label)
                build = run_dir / label
                result = build / "result"
                result.mkdir(parents=True)
                for filename in local_build_run.BUILD_RESULT_FILES:
                    (result / filename).write_bytes(filename.encode())
                workspace = build / "workspace.ext4"
                workspace.write_bytes(b"workspace")
                return result, workspace

            def prepare_final(*, output_dir: Path, **_: object) -> None:
                output_dir.mkdir()
                (output_dir / "system.private.squashfs").write_bytes(b"base")
                (output_dir / "final-root.private.json").write_text("{}")

            def raptor_build(*, output_dir: Path, **_: object):
                output_dir.mkdir()
                (output_dir / "system.private.squashfs").write_bytes(b"hsqs-final")
                (output_dir / "final-root.private.json").write_text("{}")
                return {"raptor_rwd": {"source_provenance": {}}}

            def split_run(_arguments: list[str], *, label: str, **_: object) -> None:
                self.assertEqual(label, "fixed-layout split-kernel build")
                result = next(build_root.glob("runs/*/split-kernels"))
                for filename in (
                    "installer-kernel.uimage",
                    "final-kernel.uimage",
                    "final-linux.config",
                    "installer-jzmmc_v12.ko",
                ):
                    (result / filename).write_bytes(filename.encode())

            def install_set(*, output_dir: Path, **_: object) -> None:
                output_dir.mkdir()

            acquired = {
                "builder_image": {"id": "sha256:" + "a" * 64},
                "ingenic_toolchain": {"archive": str(ingenic_archive)},
                "rust_source": {"path": str(acquired_root / "rust-source")},
                "rust_toolchain": {"path": str(acquired_root / "rust-toolchain")},
                "source_checkout": {"path": str(acquired_root / "source")},
                "sources_lock_sha256": "b" * 64,
                "thingino_toolchain": {
                    "archive": "thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz",
                    "sha256": "d" * 64,
                },
            }
            media = SimpleNamespace(
                by_path=lambda: {"lib/libaudioProcess.so": SimpleNamespace(raw=b"audio")}
            )
            inspection = {
                "data_mode": "initialize",
                "layout": "dcs6100lhv2-a1-mtd3-split-v1",
                "ok": True,
                "schema_version": 2,
            }
            with (
                mock.patch.object(
                    local_build_run,
                    "local_build_workspace_status",
                    return_value={
                        "build_count": 2,
                        "build_root": str(build_root),
                        "current_head": "c" * 40,
                        "ready_to_build": True,
                    },
                ),
                mock.patch.object(local_build_run, "_project_root", return_value=project),
                mock.patch.object(local_build_run, "load_vendor_bundle"),
                mock.patch.object(local_build_run, "load_media_closure", return_value=media),
                mock.patch.object(
                    local_build_run,
                    "acquire_locked_public_inputs",
                    return_value=acquired,
                ),
                mock.patch.object(
                    local_build_run.source_prepare,
                    "prepare_source",
                    side_effect=prepare_source,
                ),
                mock.patch.object(
                    local_build_run,
                    "prepare_vendor_build_site",
                    side_effect=prepare_vendor,
                ),
                mock.patch.object(
                    local_build_run,
                    "_prepare_thingino_toolchain",
                    return_value=(
                        root / "thingino-toolchain.tar.gz",
                        {"archive_sha256": "d" * 64},
                    ),
                ),
                mock.patch.object(
                    local_build_run,
                    "_prepare_download_cache",
                    return_value=(root / "downloads.tar", {"inventory_entries": 6389}),
                ),
                mock.patch.object(
                    local_build_run,
                    "_run_clean_build",
                    side_effect=clean_build,
                ),
                mock.patch.object(
                    local_build_run,
                    "_workspace_tool",
                    side_effect=lambda name, **_: root / name,
                ),
                mock.patch.object(
                    local_build_run,
                    "prepare_from_private_directory",
                    side_effect=prepare_final,
                ),
                mock.patch.object(
                    local_build_run,
                    "_raptor_module",
                    return_value=SimpleNamespace(build_persistent_root=raptor_build),
                ),
                mock.patch.object(local_build_run, "_run", side_effect=split_run),
                mock.patch.object(local_build_run, "build_install_set", side_effect=install_set),
                mock.patch.object(local_build_run, "_tool", return_value=root / "tool"),
                mock.patch.object(
                    local_build_run,
                    "_inspect_install_set",
                    return_value=inspection,
                ),
            ):
                result = local_build_run.build_local_install_set(
                    build_root=build_root,
                    vendor_bundle_dir=private / "vendor",
                    media_closure_dir=private / "media",
                    private_config_dir=private / "config",
                    expected_wpa_config_path=wpa,
                    session_dir=private / "session",
                    raptor_rwd_artifact=artifact,
                )

            self.assertEqual(clean_calls, ["build-a", "build-b"])
            self.assertTrue(result["reproducible"])
            self.assertTrue(result["raptor_rwd_overlay"])
            self.assertEqual(result["schema_version"], 2)
            self.assertFalse(result["public_firmware_release_gate_consulted"])
            self.assertTrue(Path(result["install_set_dir"]).is_dir())
            run_manifest = json.loads(
                next(build_root.glob("runs/*/local-build-run.private.json")).read_text()
            )
            self.assertEqual(
                run_manifest["thingino_toolchain"]["archive_sha256"],
                "d" * 64,
            )

    def test_build_rejects_a_single_build_workspace(self) -> None:
        with mock.patch.object(
            local_build_run,
            "local_build_workspace_status",
            return_value={
                "build_count": 1,
                "build_root": "/external/build",
                "current_head": "a" * 40,
                "ready_to_build": True,
            },
        ):
            with self.assertRaisesRegex(
                local_build_run.LocalBuildRunError,
                "two-build workspace",
            ):
                local_build_run.build_local_install_set(
                    build_root=Path("/external/build"),
                    vendor_bundle_dir=Path("/private/vendor"),
                    media_closure_dir=Path("/private/media"),
                    private_config_dir=Path("/private/config"),
                    expected_wpa_config_path=Path("/private/wpa"),
                    session_dir=Path("/private/session"),
                    raptor_rwd_artifact=Path("/private/raptor.tar.gz"),
                )

    def test_comparison_rejects_nonidentical_clean_builds(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first = root / "a"
            second = root / "b"
            first.mkdir()
            second.mkdir()
            for filename in local_build_run.BUILD_RESULT_FILES:
                (first / filename).write_bytes(b"same")
                (second / filename).write_bytes(b"same")
            (second / "thingino-base.squashfs").write_bytes(b"different")
            with self.assertRaisesRegex(
                local_build_run.LocalBuildRunError,
                "not byte-identical|differ",
            ):
                local_build_run._compare_clean_builds(first, second)

    def test_source_built_toolchain_requires_locked_digest(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / "toolchain.tar.gz"
            archive.write_bytes(b"toolchain")
            with self.assertRaisesRegex(
                local_build_run.LocalBuildRunError,
                "toolchain digest mismatch",
            ):
                local_build_run._toolchain_archive_identity(
                    archive,
                    expected_sha256="0" * 64,
                )


if __name__ == "__main__":
    unittest.main()
