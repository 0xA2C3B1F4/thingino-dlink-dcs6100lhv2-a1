from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import (
    local_build_inputs,
    local_build_package,
    local_build_run,
    local_build_support,
    raptor_full_build,
    raptor_full_root,
)


class LocalBuildRunTests(unittest.TestCase):
    def test_download_cache_key_tracks_prepared_source_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            build_root = root / "build-root"
            (build_root / "cache/downloads").mkdir(parents=True)
            run_dir = root / "run"
            run_dir.mkdir()
            prepared_source = root / "prepared-source"
            prepared_source.mkdir()
            manifest = prepared_source / "dcs6100-source-preparation.json"
            manifest.write_bytes(b'{"profile":"first"}\n')
            (root / "scripts").mkdir()
            fetch_contract = root / "scripts/container_fetch_thingino_downloads.sh"
            fetch_contract.write_text("first fetch contract\n")
            policy = root / "profiles/dlink-dcs6100lhv2-a1/download-cache.json"
            policy.parent.mkdir(parents=True)
            policy.write_text("first policy\n")

            def fetch(_arguments: list[str], **_: object) -> None:
                result = run_dir / "download-fetch-result"
                (result / "download-cache.tar").write_bytes(manifest.read_bytes())

            def validate(path: Path) -> dict[str, object]:
                return {"archive_sha256": local_build_run._sha256(path)}

            with (
                mock.patch.object(local_build_run, "_run", side_effect=fetch),
                mock.patch.object(
                    local_build_run,
                    "validate_download_cache_archive",
                    side_effect=validate,
                ),
            ):
                first, first_identity = local_build_run._prepare_download_cache(
                    root=root,
                    run_dir=run_dir,
                    build_root=build_root,
                    prepared_source=prepared_source,
                    builder_image="sha256:" + "a" * 64,
                    lock_sha256="b" * 64,
                    thingino_toolchain=root / "toolchain.tar.gz",
                )
                (run_dir / "download-fetch-result").rmdir()
                manifest.write_bytes(b'{"profile":"second"}\n')
                second, second_identity = local_build_run._prepare_download_cache(
                    root=root,
                    run_dir=run_dir,
                    build_root=build_root,
                    prepared_source=prepared_source,
                    builder_image="sha256:" + "a" * 64,
                    lock_sha256="b" * 64,
                    thingino_toolchain=root / "toolchain.tar.gz",
                )
                (run_dir / "download-fetch-result").rmdir()
                fetch_contract.write_text("second fetch contract\n")
                third, third_identity = local_build_run._prepare_download_cache(
                    root=root,
                    run_dir=run_dir,
                    build_root=build_root,
                    prepared_source=prepared_source,
                    builder_image="sha256:" + "a" * 64,
                    lock_sha256="b" * 64,
                    thingino_toolchain=root / "toolchain.tar.gz",
                )
                (run_dir / "download-fetch-result").rmdir()
                policy.write_text("second policy\n")
                fourth, fourth_identity = local_build_run._prepare_download_cache(
                    root=root,
                    run_dir=run_dir,
                    build_root=build_root,
                    prepared_source=prepared_source,
                    builder_image="sha256:" + "a" * 64,
                    lock_sha256="b" * 64,
                    thingino_toolchain=root / "toolchain.tar.gz",
                )

            self.assertNotEqual(first, second)
            self.assertNotEqual(second, third)
            self.assertNotEqual(third, fourth)
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            self.assertTrue(third.is_file())
            self.assertTrue(fourth.is_file())
            self.assertNotEqual(
                first_identity["prepared_source_manifest_sha256"],
                second_identity["prepared_source_manifest_sha256"],
            )
            self.assertNotEqual(
                second_identity["fetch_contract_sha256"],
                third_identity["fetch_contract_sha256"],
            )
            self.assertNotEqual(
                third_identity["download_policy_sha256"],
                fourth_identity["download_policy_sha256"],
            )

    def test_stage1_uses_homebrew_llvm_instead_of_apple_clang(self) -> None:
        clang = Path("/opt/homebrew/opt/llvm/bin/clang")
        lld = Path("/opt/homebrew/bin/ld.lld")
        with (
            mock.patch.object(Path, "is_file", autospec=True, return_value=True),
            mock.patch.object(local_build_support.os, "access", return_value=True),
            mock.patch.object(local_build_support, "_tool", return_value=lld.resolve()),
        ):
            selected = local_build_support._llvm_tools()
        self.assertEqual(selected, (clang.resolve(), lld.resolve()))

    def test_full_raptor_build_runs_two_clean_builds_split_and_inspection(self) -> None:
        self._exercise_install_build(build_count=2)

    def test_full_raptor_single_build_does_not_claim_reproducibility(self) -> None:
        self._exercise_install_build(build_count=1)

    def test_universal_build_always_composes_full_raptor(self) -> None:
        self._exercise_install_build(build_count=1, universal=True)

    def test_universal_two_builds_bind_base_reproducibility(self) -> None:
        self._exercise_install_build(build_count=2, universal=True)

    def test_full_raptor_failed_second_build_retains_owner_and_log_without_success_manifest(self) -> None:
        self._exercise_install_build(build_count=2, failure="build-b")

    def test_full_raptor_failed_inspection_retains_reproducibility_without_success_manifest(self) -> None:
        self._exercise_install_build(build_count=1, failure="inspection")

    def test_full_raptor_acquisition_os_error_keeps_owner_and_exception_cause(self) -> None:
        self._exercise_install_build(build_count=1, failure="acquisition")

    def test_universal_preserve_build_reaches_camera_authorized_packaging(self) -> None:
        self._exercise_install_build(
            build_count=1, universal=True, data_mode="preserve"
        )

    def _exercise_install_build(
        self, *, build_count: int, universal: bool = True,
        failure: str | None = None,
        data_mode: str = "initialize",
    ) -> None:
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
            signing_key = private / "release.pem"
            signing_key.write_bytes(b"synthetic-signing-input")
            (private / "vendor/files").mkdir()
            (private / "vendor/files/libaudioProcess.so").write_bytes(b"audio")
            scope = "model-universal"
            full_raptor = True
            suffix = "universal"
            events: list[str] = []
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
                events.append(label)
                self.assertEqual(_["builder_image"], "sha256:" + "a" * 64)
                self.assertEqual(
                    _["audio_link"], private / "vendor/files/libaudioProcess.so"
                )
                build = run_dir / label
                result = build / "result"
                result.mkdir(parents=True)
                for filename in local_build_run.BUILD_RESULT_FILES:
                    (result / filename).write_bytes(filename.encode())
                workspace = build / "workspace.ext4"
                workspace.write_bytes(b"workspace")
                if failure == label:
                    log = run_dir / f"logs/{label}.log"
                    log.parent.mkdir(exist_ok=True)
                    log.write_text("synthetic build failure")
                    raise local_build_run.LocalBuildRunError(
                        f"clean Thingino {label} failed; see {log}"
                    )
                return result, workspace

            def prepare_final(*, output_dir: Path, **_: object) -> None:
                events.append("final-root")
                self.assertEqual(_["base_rootfs"], b"thingino-base.squashfs")
                self.assertIsNone(_["media_closure"])
                output_dir.mkdir()
                (output_dir / f"system.{suffix}.squashfs").write_bytes(b"base")
                (output_dir / f"final-root.{suffix}.json").write_text('{"prepared": true}')

            def full_component(**_: object):
                events.append("full-component")
                self.assertEqual(_["base_rootfs"].read_bytes(), b"base")
                self.assertEqual(_["base_workspace"].name, "workspace.ext4")
                return {
                    "artifact": str(root / "raptor-full.tar.gz"),
                    "sha256": "e" * 64,
                    "identity": {"build_inputs": {"base": "fresh"}},
                }

            def compose_full(*, output_dir: Path, **_: object) -> None:
                events.append("full-compose")
                self.assertEqual(_["component_sha256"], "e" * 64)
                self.assertEqual(_["build_inputs"], {"base": "fresh"})
                output_dir.mkdir()
                (output_dir / "system.universal.squashfs").write_bytes(b"hsqs-final")
                (output_dir / "final-root.universal.json").write_text(
                    '{"raptor_full": true}'
                )

            def split_run(_arguments: list[str], *, label: str, **_: object) -> None:
                events.append("split")
                self.assertEqual(label, "fixed-layout split-kernel build")
                result = next(build_root.glob("runs/*/split-kernels"))
                self.assertEqual(_["log_path"], result.parent / "logs/split-kernels.log")
                self.assertEqual(
                    _arguments[_arguments.index("--workspace-image") + 1],
                    str(result.parent / "build-a/workspace.ext4"),
                )
                for filename in (
                    "installer-kernel.uimage",
                    "final-kernel.uimage",
                    "final-linux.config",
                    "installer-jzmmc_v12.ko",
                ):
                    (result / filename).write_bytes(filename.encode())

            def install_set(*, output_dir: Path, **_: object) -> None:
                events.append("package")
                self.assertEqual(_["system"], b"hsqs-final")
                self.assertEqual(_["final_kernel"], b"final-kernel.uimage")
                self.assertEqual(_["signing_key"], signing_key)
                self.assertEqual(
                    _["universal_root_manifest"],
                    {"raptor_full": True},
                )
                self.assertEqual(_["data_mode"], data_mode)
                output_dir.mkdir()

            def inspect(*_: object):
                events.append("inspection")
                if failure == "inspection":
                    raise local_build_run.LocalBuildRunError("synthetic inspection failure")
                return inspection

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
            inspection = {
                "data_mode": data_mode,
                "layout": "dcs6100lhv2-a1-mtd3-split-v1",
                "ok": True,
                "schema_version": 2,
            }
            acquisition_error = OSError("synthetic acquisition failure")
            patches = (
                mock.patch.object(
                    local_build_inputs,
                    "local_build_workspace_status",
                    return_value={
                        "build_count": 2,
                        "build_root": str(build_root),
                        "current_head": "c" * 40,
                        "ready_to_build": True,
                    },
                ),
                mock.patch.object(local_build_inputs, "_project_root", return_value=project),
                mock.patch.object(local_build_run, "_run_id", return_value="build-characterized"),
                mock.patch.object(local_build_inputs, "load_vendor_bundle"),
                mock.patch.object(
                    local_build_run,
                    "acquire_locked_public_inputs",
                    return_value=acquired,
                    side_effect=acquisition_error if failure == "acquisition" else None,
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
                    local_build_package,
                    "_workspace_tool",
                    side_effect=lambda name, **_: root / name,
                ),
                mock.patch.object(
                    local_build_package, "prepare_universal_final_root",
                    side_effect=prepare_final,
                ),
                mock.patch.object(
                    raptor_full_build,
                    "build_full_component",
                    side_effect=full_component,
                ),
                mock.patch.object(
                    raptor_full_root,
                    "compose_universal_root",
                    side_effect=compose_full,
                ),
                mock.patch.object(local_build_package, "_run", side_effect=split_run),
                mock.patch.object(
                    local_build_package, "build_universal_install_set",
                    side_effect=install_set,
                ),
                mock.patch.object(local_build_support, "_tool", return_value=root / "tool"),
                mock.patch.object(
                    local_build_package,
                    "_inspect_install_set",
                    side_effect=inspect,
                ),
            )
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                arguments = dict(
                    build_root=build_root,
                    vendor_bundle_dir=private / "vendor",
                    build_count=build_count,
                )
                build = local_build_run.build_local_universal_install_set
                arguments["signing_key"] = signing_key
                arguments["data_mode"] = data_mode
                if failure:
                    with self.assertRaisesRegex(
                        local_build_run.LocalBuildRunError, "failure|failed|provenance"
                    ) as raised:
                        build(**arguments)
                    run_dir = build_root / "runs/build-characterized"
                    self.assertTrue((run_dir / "OWNER.md").is_file())
                    self.assertFalse(list(run_dir.glob("local-build-run.*.json")))
                    if failure == "acquisition":
                        self.assertIs(raised.exception.__cause__, acquisition_error)
                        self.assertEqual(events, [])
                        self.assertEqual({path.name for path in run_dir.iterdir()}, {"OWNER.md"})
                    elif failure == "build-b":
                        self.assertTrue((run_dir / "logs/build-b.log").is_file())
                        self.assertFalse((run_dir / "reproducibility.json").exists())
                        self.assertEqual(events, ["build-a", "build-b"])
                    else:
                        self.assertTrue((run_dir / "reproducibility.json").is_file())
                        if failure in {"overlay", "provenance"}:
                            self.assertNotIn("package", events)
                            self.assertNotIn("inspection", events)
                    return
                result = build(**arguments)

            self.assertEqual(
                clean_calls, ["build-a", "build-b"] if build_count == 2 else ["build-a"]
            )
            self.assertEqual(
                events,
                clean_calls + ["final-root"]
                + ["full-component", "full-compose"]
                + ["split", "package", "inspection"],
            )
            self.assertFalse(result["reproducible"])
            self.assertEqual(result["artifact_scope"], scope)
            self.assertEqual(result["schema_version"], 2)
            self.assertFalse(result["public_firmware_release_gate_consulted"])
            self.assertTrue(Path(result["install_set_dir"]).is_dir())
            run_manifest = json.loads(
                next(build_root.glob(f"runs/*/local-build-run.{suffix}.json")).read_text()
            )
            self.assertEqual(
                run_manifest["thingino_toolchain"]["archive_sha256"],
                "d" * 64,
            )
            self.assertEqual(run_manifest["build_count"], build_count)
            self.assertEqual(run_manifest["reproducibility"]["builds"], build_count)
            self.assertFalse(run_manifest["private"])
            self.assertEqual(run_manifest["project_head"], "c" * 40)
            self.assertEqual(run_manifest["sources_lock_sha256"], "b" * 64)
            run_dir = Path(result["run_dir"])
            self.assertTrue(result.pop("raptor_full_source_build"))
            self.assertEqual(result, {
                "artifact_scope": scope,
                "media_backend": "raptor",
                "build_count": build_count,
                "build_root": str(build_root),
                "data_mode": data_mode,
                "install_set_dir": str(run_dir / "install-set"),
                "inspection": inspection,
                "nor_writes": False,
                "public_firmware_release_gate_consulted": False,
                "reproducible": False,
                "run_dir": str(run_dir),
                "schema_version": 2,
                "status": "host-built and inspected; live installation not authorized",
                "universal_firmware": str(run_dir / "install-set/thingino-universal.tgb"),
            })
            self.assertEqual(run_dir.name, "build-characterized")
            self.assertEqual(run_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual((run_dir / "OWNER.md").stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (run_dir / f"local-build-run.{suffix}.json").stat().st_mode & 0o777,
                0o600,
            )
            self.assertNotIn(str(wpa), json.dumps(run_manifest))
            self.assertNotIn(str(signing_key), json.dumps(run_manifest))
            if build_count == 1:
                self.assertEqual(
                    run_manifest["reproducibility"], {
                        "builds": 1,
                        "byte_identical": None,
                        "scope": "base-image-only",
                    }
                )

    def test_invalid_inputs_fail_before_acquisition_or_run_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            arguments = {
                "build_root": root,
                "vendor_bundle_dir": root,
                "data_mode": "initialize",
                "artifact_scope": "model-universal",
                "signing_key": root / "signing.pem",
                "build_count": 1,
            }
            cases = (
                (
                    {"artifact_scope": "unknown"},
                    "personalized local builds are retired; use model-universal full Raptor",
                ),
                ({"data_mode": "factory-reset"}, "requires initialize or preserve"),
                ({"data_mode": "invalid"}, "requires initialize or preserve"),
                ({"build_count": 3}, "must be one or two"),
                ({"signing_key": None}, "requires signing"),
                (
                    {"artifact_scope": "device-personalized"},
                    "personalized local builds are retired; use model-universal full Raptor",
                ),
                ({"signing_key": root / "missing"}, "signing key is missing"),
            )
            with (
                mock.patch.object(local_build_inputs, "local_build_workspace_status", return_value={
                    "ready_to_build": True, "build_root": str(root),
                    "build_count": 2, "current_head": "c" * 40,
                }),
                mock.patch.object(local_build_inputs, "_project_root", return_value=root),
                mock.patch.object(local_build_run, "acquire_locked_public_inputs") as acquire,
                mock.patch.object(local_build_run, "_write_owner") as owner,
            ):
                for changes, error in cases:
                    with self.subTest(changes=changes):
                        with self.assertRaisesRegex(local_build_run.LocalBuildRunError, error):
                            local_build_run._build_local_install_set(**(arguments | changes))
                acquire.assert_not_called()
                owner.assert_not_called()

    def test_recovery_assets_build_before_private_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            project.mkdir()
            build_root = root / "build-root"
            (build_root / "runs").mkdir(parents=True)
            source = root / "source"
            source.mkdir()

            def prepare_source(_checkout: Path, destination: Path, **_: object) -> None:
                destination.mkdir()
                (destination / "dcs6100-source-preparation.json").write_bytes(b"source")

            def collector_run(arguments: list[str], *, label: str, **_: object) -> None:
                self.assertIn(
                    label,
                    {
                        "read-only collector kernel build",
                        "read-only collector reproducibility build",
                    },
                )
                result = Path(arguments[arguments.index("--result") + 1])
                for filename in (
                    "collector-kernel.uimage",
                    "collector-linux.config",
                    "jzmmc_v12.ko",
                    "source-preparation.json",
                    "uartless-collector-kernel.uimage",
                    "uartless-collector-linux.config",
                ):
                    payload = b"source" if filename == "source-preparation.json" else filename.encode()
                    (result / filename).write_bytes(payload)

            acquired = {
                "builder_image": {"id": "sha256:" + "a" * 64},
                "source_checkout": {"path": str(source)},
                "sources_lock_sha256": "b" * 64,
                "thingino_toolchain": {"acquisition": "source-build"},
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
                    "_prepare_thingino_toolchain",
                    return_value=(root / "toolchain.tar.gz", {"archive_sha256": "d" * 64}),
                ),
                mock.patch.object(
                    local_build_run,
                    "_prepare_download_cache",
                    return_value=(root / "downloads.tar", {"inventory_entries": 1}),
                ),
                mock.patch.object(local_build_run, "_run", side_effect=collector_run),
                mock.patch.object(local_build_run, "validate_collector_kernel"),
                mock.patch.object(local_build_run, "validate_uartless_collector_kernel"),
                mock.patch.object(local_build_run, "validate_mmc_module"),
                mock.patch.object(
                    local_build_run,
                    "build_collector_root",
                    side_effect=lambda **values: values["output"].write_bytes(b"rootfs"),
                ) as build_rootfs,
            ):
                result = local_build_run.build_local_recovery_assets(
                    build_root=build_root
                )

            self.assertEqual(build_rootfs.call_count, 2)
            self.assertEqual(result["write_set"], [])
            self.assertEqual(
                result["next_action"],
                "choose-uart-read-only-or-uartless-functional-capture",
            )
            self.assertTrue(Path(result["files"]["kernel"]).is_file())
            self.assertTrue(Path(result["files"]["uartless_package"]).is_file())
            self.assertEqual(
                result["transports"]["uartless"]["future_physical_boot_writes_mtd"],
                [1, 2],
            )
            manifest = json.loads(Path(result["manifest"]).read_text())
            self.assertEqual(manifest["project_head"], "c" * 40)
            self.assertEqual(manifest["sources_lock_sha256"], "b" * 64)
            self.assertEqual(
                manifest["reproducibility"],
                {"builds": 2, "byte_identical": True},
            )

    def test_universal_build_wrapper_cannot_receive_camera_private_inputs(self) -> None:
        with mock.patch.object(
            local_build_run,
            "_build_local_install_set",
            return_value={"artifact_scope": "model-universal"},
        ) as build:
            result = local_build_run.build_local_universal_install_set(
                build_root=Path("/build"),
                vendor_bundle_dir=Path("/model/vendor"),
                signing_key=Path("/model/release.pem"),
            )
        self.assertEqual(result["artifact_scope"], "model-universal")
        values = build.call_args.kwargs
        self.assertEqual(values["data_mode"], "initialize")
        self.assertEqual(values["artifact_scope"], "model-universal")
        self.assertNotIn("media_closure_dir", values)
        self.assertNotIn("full_raptor", values)

    def test_universal_build_wrapper_forwards_preserve_mode(self) -> None:
        with mock.patch.object(
            local_build_run,
            "_build_local_install_set",
            return_value={"artifact_scope": "model-universal"},
        ) as build:
            local_build_run.build_local_universal_install_set(
                build_root=Path("/build"),
                vendor_bundle_dir=Path("/model/vendor"),
                signing_key=Path("/model/release.pem"),
                data_mode="preserve",
            )
        self.assertEqual(build.call_args.kwargs["data_mode"], "preserve")

    def test_two_build_request_requires_matching_workspace_capacity(self) -> None:
        with mock.patch.object(
            local_build_inputs,
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
                "does not reserve capacity",
            ):
                local_build_run.build_local_universal_install_set(
                    build_root=Path("/external/build"),
                    vendor_bundle_dir=Path("/private/vendor"),
                    signing_key=Path("/private/release.pem"),
                    build_count=2,
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
