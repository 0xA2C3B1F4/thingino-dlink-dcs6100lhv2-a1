"""Focused tests for the host-side technical candidate closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from installer import raptor_source
from installer.final_bundle import (
    build_universal_final_bundle,
    ensure_ed25519_keypair,
    final_kernel_command_line,
    render_final_kernel_fragment,
)
from installer.mtd3_split import derive_final_layout
from scripts import release_closure
from tests.test_artifacts import test_squashfs, test_uimage


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class ReleaseClosureTests(unittest.TestCase):


    def make_run(self, root: Path) -> Path:
        run = root / "run-20260916T000000Z-aaaaaaaaaaaa"
        (run / "build-a/result").mkdir(parents=True)
        (run / "raptor-full").mkdir()
        (run / "raptor-final-root").mkdir()
        (run / "split-kernels").mkdir()
        (run / "install-set").mkdir()
        for name in release_closure.BUILD_RESULT_FILES:
            (run / "build-a/result" / name).write_bytes(f"base:{name}\n".encode())
        for name in release_closure.SPLIT_KERNEL_FILES:
            (run / "split-kernels" / name).write_bytes(f"split:{name}\n".encode())
        (run / release_closure.RAPTOR_COMPONENT).write_bytes(b"component fixture\n")
        component_sha256 = hashlib.sha256(
            (run / release_closure.RAPTOR_COMPONENT).read_bytes()
        ).hexdigest()
        private_key = root / "release.pem"
        self._public_key = root / "release.pub"
        ensure_ed25519_keypair(private_key, self._public_key)
        system_raw = test_squashfs(0x18000)
        command_line = final_kernel_command_line(derive_final_layout(len(system_raw)))
        tgb_raw = build_universal_final_bundle(
            kernel=test_uimage(command_line.encode("ascii")),
            bootstrap_rootfs=test_squashfs(),
            system_rootfs=system_raw,
            linux_config=render_final_kernel_fragment(len(system_raw)),
            signing_key=private_key,
        )
        system = run / release_closure.FINAL_ROOT_SYSTEM
        system.write_bytes(system_raw)
        system_identity = {
            "filename": "system.universal.squashfs",
            "sha256": hashlib.sha256(system.read_bytes()).hexdigest(),
            "size": system.stat().st_size,
        }
        raptor_lock = raptor_source.source_lock(ROOT, full_media=True)
        source_build = {
            "build_inputs": {
                "base_rootfs_sha256": "1" * 64,
                "builder_image_id": "sha256:" + "2" * 64,
                "toolchain_sha256": "3" * 64,
            },
            "files": {"usr/bin/raptorctl": "4" * 64},
            "kind": "raptor-full-media-component",
            "recipe": raptor_source.recipe_identity(ROOT, full_media=True),
            "schema_version": 1,
            "source_trees": {
                name: spec["tree"] for name, spec in raptor_lock["sources"].items()
            },
        }
        final_root = {
            "artifact_scope": "model-universal",
            "composition_required": False,
            "contains_device_secrets": False,
            "media_closure_sha256": None,
            "media_profile": "full-raptor-v1",
            "provisioning_required": True,
            "raptor_full": {
                "component_sha256": component_sha256,
                "source_build": source_build,
            },
            "schema_version": 1,
            "source_sha256": "5" * 64,
            "system": system_identity,
            "target": release_closure.TARGET,
            "vendor_bundle_sha256": "6" * 64,
        }
        (run / release_closure.FINAL_ROOT_MANIFEST).write_text(
            json.dumps(final_root, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        prepared_system_raw = b"prepared universal root fixture\n"
        (run / release_closure.PREPARED_ROOT_SYSTEM).parent.mkdir()
        prepared_system = run / release_closure.PREPARED_ROOT_SYSTEM
        prepared_system.write_bytes(prepared_system_raw)
        prepared_manifest = {
            "artifact_scope": "model-universal",
            "composition_required": True,
            "contains_device_secrets": False,
            "media_closure_sha256": None,
            "media_profile": "source-built-camera-support-v1",
            "provisioning_required": True,
            "schema_version": 1,
            "source_sha256": hashlib.sha256(
                (run / "build-a/result/thingino-base.squashfs").read_bytes()
            ).hexdigest(),
            "system": {
                "filename": "system.universal.squashfs",
                "sha256": hashlib.sha256(prepared_system_raw).hexdigest(),
                "size": len(prepared_system_raw),
            },
            "target": release_closure.TARGET,
            "vendor_bundle_sha256": "7" * 64,
        }
        (run / release_closure.PREPARED_ROOT_MANIFEST).write_text(
            json.dumps(prepared_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        prepared_sha256 = hashlib.sha256(prepared_system_raw).hexdigest()
        source_build["build_inputs"]["base_rootfs_sha256"] = prepared_sha256
        final_root["source_sha256"] = prepared_sha256
        (run / release_closure.FINAL_ROOT_MANIFEST).write_text(
            json.dumps(final_root, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        install_set = run / release_closure.INSTALL_SET_NAME
        for name in release_closure.INSTALL_SET_MEMBERS:
            if name == "install-set.manifest.json":
                continue
            (install_set / name).write_bytes(f"install:{name}\n".encode())
        tgb = install_set / "thingino-universal.tgb"
        tgb.write_bytes(tgb_raw)
        tgb_sha256 = hashlib.sha256(tgb.read_bytes()).hexdigest()
        artifacts = {
            name: {
                "sha256": hashlib.sha256((install_set / name).read_bytes()).hexdigest(),
                "size": (install_set / name).stat().st_size,
            }
            for name in sorted(release_closure.INSTALL_SET_MEMBERS - {"install-set.manifest.json"})
        }
        install_manifest = {
            "artifact_scope": "model-universal",
            "artifacts": artifacts,
            "provisioning": "separate-per-camera-audit-and-jffs2",
            "schema_version": 2,
            "status": "host-built install set; generation does not authorize live use",
            "target": release_closure.TARGET,
            "universal_firmware_sha256": tgb_sha256,
        }
        (install_set / "install-set.manifest.json").write_text(
            json.dumps(install_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        inspection = {
            "artifact_sizes": {
                name: artifacts[name]["size"] for name in sorted(artifacts)
            },
            "data_mode": "preserve",
            "data_span": 1,
            "layout": "dcs6100lhv2-a1-mtd3-split-v1",
            "ok": True,
            "schema_version": 2,
            "system_span": 1,
        }
        (run / release_closure.INSPECTION_NAME).parent.mkdir(exist_ok=True)
        (run / release_closure.INSPECTION_NAME).write_text(
            json.dumps(inspection, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )

        repro_files: dict[str, dict[str, object]] = {}
        actual_roots = {
            "base": run / "build-a/result",
            "raptor": run / "raptor-full",
            "final-root": run / "raptor-final-root",
            "split-kernels": run / "split-kernels",
            "install-set": install_set,
        }
        for relative in sorted(release_closure.REPRODUCTION_FILES):
            prefix, name = relative.split("/", 1)
            path = actual_roots[prefix] / name
            repro_files[relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        reproducibility = {
            "builds": 2,
            "byte_identical": True,
            "component_cache_used": False,
            "differences": [],
            "files": repro_files,
            "inspections_accepted": True,
            "scope": "complete-firmware",
        }
        (run / release_closure.REPRODUCIBILITY_NAME).write_text(
            json.dumps(reproducibility, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_manifest = {
            "artifact_scope": "model-universal",
            "build_count": 2,
            "data_mode": "preserve",
            "install_set": "install-set",
            "inspection": inspection,
            "media_backend": "raptor",
            "private": False,
            "project_head": "a" * 40,
            "provisioning": "separate-per-camera-sidecar",
            "public_firmware_release_gate_consulted": False,
            "reproducibility": reproducibility,
            "schema_version": 1,
            "sources_lock_sha256": hashlib.sha256(
                (ROOT / release_closure.GLOBAL_SOURCE_LOCK).read_bytes()
            ).hexdigest(),
            "status": "host-built and inspected; live installation not authorized",
        }
        (run / release_closure.RUN_MANIFEST_NAME).write_text(
            json.dumps(run_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return run

    @staticmethod
    def _repro_path(run: Path, relative: str) -> Path:
        prefix, name = relative.split("/", 1)
        roots = {
            "base": run / "build-a/result",
            "raptor": run / "raptor-full",
            "final-root": run / "raptor-final-root",
            "split-kernels": run / "split-kernels",
            "install-set": run / "install-set",
        }
        return roots[prefix] / name

    def refresh_reproducibility(self, run: Path) -> None:
        repro_path = run / release_closure.REPRODUCIBILITY_NAME
        repro = json.loads(repro_path.read_text(encoding="utf-8"))
        for relative in repro["files"]:
            path = self._repro_path(run, relative)
            repro["files"][relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        repro_path.write_text(
            json.dumps(repro, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        run_manifest_path = run / release_closure.RUN_MANIFEST_NAME
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        run_manifest["reproducibility"] = repro
        run_manifest_path.write_text(
            json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def validator_patches(self):
        return mock.patch.multiple(
            release_closure,
            validate_install_set=mock.Mock(return_value=object()),
            validate_component=mock.Mock(
                side_effect=lambda *args, **kwargs: (
                    {},
                    json.loads(
                        (self._fixture_run / release_closure.FINAL_ROOT_MANIFEST).read_text(
                            encoding="utf-8"
                        )
                    )["raptor_full"]["source_build"],
                )
            ),
        )

    def test_generates_deterministic_candidate_and_notice(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            with self.validator_patches():
                candidate_a, notice_a = release_closure.build_closure(
                    self._fixture_run,
                    Path(name) / "out-a",
                    universal_public_key=self._public_key,
                )
                candidate_b, notice_b = release_closure.build_closure(
                    self._fixture_run,
                    Path(name) / "out-b",
                    universal_public_key=self._public_key,
                )
            self.assertEqual(candidate_a, candidate_b)
            self.assertEqual(notice_a, notice_b)
            self.assertEqual(candidate_a["status"], "technical-candidate-only")
            self.assertEqual(candidate_a["technical_status"], release_closure.TECHNICAL_STATUS)
            self.assertEqual(candidate_a["legal_review_status"], "not-assessed")
            self.assertEqual(
                candidate_a["redistribution"],
                "not-authorized-by-this-repository",
            )
            self.assertEqual(len(notice_a["raptor_source_locks"]), 12)
            self.assertEqual(notice_a["headers"]["name"], "ingenic-headers")
            self.assertEqual(notice_a["legal_review_status"], "not-assessed")
            self.assertEqual(
                notice_a["redistribution"],
                "not-authorized-by-this-repository",
            )
            self.assertEqual(
                candidate_a["artifacts"]["tgb"]["sha256"],
                candidate_a["bindings"]["install_set_manifest"]["universal_firmware_sha256"],
            )

    def test_generate_writes_only_after_all_evidence_validates(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            output = Path(name) / "out"
            (self._fixture_run / release_closure.REPRODUCIBILITY_NAME).unlink()
            with self.validator_patches(), self.assertRaises(release_closure.ReleaseClosureError):
                release_closure.generate(
                    self._fixture_run,
                    output,
                    universal_public_key=self._public_key,
                )
            self.assertFalse(output.exists())

    def test_tgb_manifest_binding_is_not_replaced_by_a_hash(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            tgb = self._fixture_run / "install-set/thingino-universal.tgb"
            tgb.write_bytes(b"changed tgb\n")
            with self.validator_patches(), self.assertRaises(release_closure.ReleaseClosureError):
                release_closure.build_closure(
                    self._fixture_run,
                    Path(name) / "out",
                    universal_public_key=self._public_key,
                )

    def test_rejects_mixed_tgb_and_final_root_systems(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            final_system = self._fixture_run / release_closure.FINAL_ROOT_SYSTEM
            final_system.write_bytes(test_squashfs(0x10000))
            final_root_path = self._fixture_run / release_closure.FINAL_ROOT_MANIFEST
            final_root = json.loads(final_root_path.read_text(encoding="utf-8"))
            final_root["system"] = {
                "filename": "system.universal.squashfs",
                "sha256": hashlib.sha256(final_system.read_bytes()).hexdigest(),
                "size": final_system.stat().st_size,
            }
            final_root_path.write_text(
                json.dumps(final_root, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.refresh_reproducibility(self._fixture_run)
            with self.validator_patches(), self.assertRaisesRegex(
                release_closure.ReleaseClosureError, "TGB system"
            ):
                release_closure.build_closure(
                    self._fixture_run,
                    Path(name) / "out",
                    universal_public_key=self._public_key,
                )

    def test_prepared_root_must_bind_clean_base_and_full_root_input(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            prepared_path = self._fixture_run / release_closure.PREPARED_ROOT_MANIFEST
            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
            prepared["source_sha256"] = "f" * 64
            prepared_path.write_text(
                json.dumps(prepared, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.validator_patches(), self.assertRaisesRegex(
                release_closure.ReleaseClosureError, "prepared-root manifest"
            ):
                release_closure.build_closure(
                    self._fixture_run,
                    Path(name) / "out",
                    universal_public_key=self._public_key,
                )

    def test_source_recipe_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            final_root_path = self._fixture_run / release_closure.FINAL_ROOT_MANIFEST
            final_root = json.loads(final_root_path.read_text(encoding="utf-8"))
            final_root["raptor_full"]["source_build"]["recipe"][
                release_closure.RAPTOR_SOURCE_LOCK
            ] = "e" * 64
            final_root_path.write_text(
                json.dumps(final_root, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.refresh_reproducibility(self._fixture_run)
            with self.validator_patches(), self.assertRaisesRegex(
                release_closure.ReleaseClosureError, "recipe"
            ):
                release_closure.build_closure(
                    self._fixture_run,
                    Path(name) / "out",
                    universal_public_key=self._public_key,
                )

    def test_late_install_and_final_root_mutations_cannot_split_evidence(self):
        cases = (
            (
                "install-set/THINGINO2.BIN",
                "install-set member THINGINO2.BIN",
            ),
            (
                release_closure.FINAL_ROOT_MANIFEST,
                "Raptor final-root manifest",
            ),
        )
        for relative, label in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as name:
                self._fixture_run = self.make_run(Path(name))
                path = self._fixture_run / relative
                original_validate = release_closure._validate_prepared_root

                def mutate_after_repro(run_dir, repro_files, *, path=path):
                    path.write_bytes(path.read_bytes() + b" \n")
                    return original_validate(run_dir, repro_files)

                with (
                    self.validator_patches(),
                    mock.patch.object(
                        release_closure,
                        "_validate_prepared_root",
                        side_effect=mutate_after_repro,
                    ),
                    self.assertRaisesRegex(
                        release_closure.ReleaseClosureError,
                        f"{label} does not match reproducibility evidence",
                    ),
                ):
                    release_closure.build_closure(
                        self._fixture_run,
                        Path(name) / "out",
                        universal_public_key=self._public_key,
                    )

    def test_output_publication_is_atomic_and_destination_must_be_new(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            output = Path(name) / "candidate-output"
            with (
                self.validator_patches(),
                mock.patch.object(
                    release_closure,
                    "_rename_directory_noreplace",
                    side_effect=OSError("interrupted publication"),
                ),
                self.assertRaisesRegex(release_closure.ReleaseClosureError, "write closure"),
            ):
                release_closure.generate(
                    self._fixture_run,
                    output,
                    universal_public_key=self._public_key,
                )
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(name).glob(".candidate-output.*")), [])

            existing = Path(name) / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(release_closure.ReleaseClosureError, "already exists"):
                release_closure.generate(
                    self._fixture_run,
                    existing,
                    universal_public_key=self._public_key,
                )

    def test_output_publication_rejects_destination_created_after_check(self):
        with tempfile.TemporaryDirectory() as name:
            self._fixture_run = self.make_run(Path(name))
            output = Path(name) / "raced-output"
            real_publish = release_closure._rename_directory_noreplace

            def create_racing_destination(staged, destination):
                destination.mkdir()
                return real_publish(staged, destination)

            with (
                self.validator_patches(),
                mock.patch.object(
                    release_closure,
                    "_rename_directory_noreplace",
                    side_effect=create_racing_destination,
                ),
                self.assertRaisesRegex(
                    release_closure.ReleaseClosureError,
                    "appeared during publication",
                ),
            ):
                release_closure.generate(
                    self._fixture_run,
                    output,
                    universal_public_key=self._public_key,
                )
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])
            self.assertEqual(list(Path(name).glob(".raced-output.*")), [])


if __name__ == "__main__":
    unittest.main()
