from __future__ import annotations

import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import media
from installer.media import (
    LEGACY_MIGRATION_PROFILE_KIND,
    MediaError,
    activate_passive_verified_package,
    activate_staged_install_set,
    archive_existing_stock_backup,
    build_legacy_migration_profile,
    deactivate_verified_package,
    deactivate_staged_install_set,
    evacuate_existing_stock_backups,
    load_media_preflight,
    replace_passive_bootstrap,
    stage_passive_verified_package,
    stage_verified_install_set,
    stage_verified_package,
)
from installer.layout import TARGET
from installer.mtd3_split import derive_final_layout, final_kernel_command_line
from installer.sd_package import (
    generate_bootstrap,
    matching_update_filenames,
    parse_package,
)
from installer.stage2 import FILENAME as STAGE2_FILENAME, build_stage2, validate_stage2
from test_artifacts import test_squashfs, test_uimage


class MediaTests(unittest.TestCase):
    def test_private_preflight_validator_remains_available_via_media_facade(self) -> None:
        self.assertEqual(
            media._validated_physical_device(
                {"host_platform": "linux", "physical_device": "/dev/sdz"}
            ),
            "/dev/sdz",
        )
        with self.assertRaises(MediaError):
            media._validated_physical_device(
                {"host_platform": "linux", "physical_device": "/dev/disk0"}
            )

    def _split_install_manifest(
        self, bootstrap_name: str, bootstrap: bytes, stage2: bytes
    ) -> bytes:
        payload = validate_stage2(stage2)
        embedded_rootfs = parse_package(
            bootstrap, require_project_header=True
        ).records[1].payload
        document = {
            "schema_version": 2,
            "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
            "layout": {
                "abi": "dcs6100lhv2-a1-mtd3-split-v1",
                "parent_physical_mtd": 3,
                "parent_offset": TARGET.partition(3).offset,
                "parent_span": TARGET.partition(3).size,
                "system_offset": payload.system_flash_offset,
                "system_span": payload.system_flash_span,
                "data_offset": payload.data_flash_offset,
                "data_span": payload.data_flash_span,
                "preserved_physical_mtd": [0, 4, 5],
                "data_mode": payload.data_mode,
            },
            "region_policy": {
                "system": {
                    "filesystem": "squashfs",
                    "write": "erase-write-readback",
                    "sha256": hashlib.sha256(payload.system).hexdigest(),
                    "payload_size": len(payload.system),
                },
                "data": {
                    "filesystem": "jffs2",
                    "initialize": "explicit-erased-region",
                    "preserve": "before-and-after-complete-region-sha256",
                    "factory_reset": "explicit-data-only-erase",
                    "corrupt": "preserve-and-require-explicit-recovery",
                },
                "activation": {
                    "region": "kernel-first-64KiB",
                    "written_last": True,
                },
            },
            "artifacts": {
                bootstrap_name: {
                    "size": len(bootstrap),
                    "sha256": hashlib.sha256(bootstrap).hexdigest(),
                },
                STAGE2_FILENAME: {
                    "size": len(stage2),
                    "sha256": hashlib.sha256(stage2).hexdigest(),
                },
                "stage1-bootstrap.squashfs": {
                    "size": len(embedded_rootfs),
                    "sha256": hashlib.sha256(embedded_rootfs).hexdigest(),
                },
            },
        }
        return json.dumps(document).encode()

    def _recovery_checkpoint(
        self,
        backup: bytes,
        stage2: bytes,
        *,
        authorization: bytes | None = None,
        provisioning: bytes | None = None,
    ) -> bytes:
        universal = authorization is not None or provisioning is not None
        self.assertEqual(authorization is None, provisioning is None)
        checkpoint = b"".join(
            (
                b"DCS6RC02" if universal else b"DCS6RC01",
                len(backup).to_bytes(4, "big"),
                len(stage2).to_bytes(4, "big"),
                hashlib.sha256(backup).digest(),
                hashlib.sha256(stage2).digest(),
            )
        )
        if universal:
            checkpoint += hashlib.sha256(authorization).digest()
            checkpoint += hashlib.sha256(provisioning).digest()
        return checkpoint

    def _preflight(self, directory: Path) -> Path:
        path = directory / "preflight.json"
        path.write_text(
            json.dumps(
                {
                    "ambiguous": False,
                    "capacity_bytes": 8_000_000_000,
                    "external": True,
                    "filesystem": "fat32",
                    "model": "TEST REMOVABLE MEDIA",
                    "mount_root": str(directory.resolve()),
                    "physical": True,
                    "physical_device": "/dev/test-external-media",
                    "schema_version": 1,
                    "system_device": False,
                    "writable": True,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_stage_preserves_unrelated_file_and_leaves_exactly_one_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            unrelated = root / "photo.txt"
            unrelated.write_text("keep", encoding="utf-8")
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )
            output_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            stage_verified_package(
                generate_bootstrap(b"kernel", b"rootfs"),
                root=root,
                output_name=output_name,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()),
                [output_name],
            )

    def test_uartless_capture_stages_inert_then_activates_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )
            package = generate_bootstrap(b"kernel", b"rootfs")
            stage_passive_verified_package(
                package,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertTrue((root / media.UARTLESS_CAPTURE_PASSIVE_FILENAME).is_file())
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()), []
            )
            activate_passive_verified_package(
                package,
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertFalse((root / media.UARTLESS_CAPTURE_PASSIVE_FILENAME).exists())
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()),
                [media.UARTLESS_CAPTURE_ACTIVE_FILENAME],
            )

    def test_uartless_handoff_failure_after_rename_stays_inert(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )
            package = generate_bootstrap(b"kernel", b"rootfs")
            active = root / media.UARTLESS_CAPTURE_ACTIVE_FILENAME
            active.write_bytes(package)
            with (
                mock.patch.object(
                    media, "_sync_directory", side_effect=OSError("injected sync failure")
                ),
                self.assertRaises(OSError),
            ):
                deactivate_verified_package(
                    package,
                    root=root,
                    active_name=media.UARTLESS_CAPTURE_ACTIVE_FILENAME,
                    passive_name=media.UARTLESS_CAPTURE_PASSIVE_FILENAME,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertFalse(active.exists())
            self.assertEqual(
                (root / media.UARTLESS_CAPTURE_PASSIVE_FILENAME).read_bytes(),
                package,
            )
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()), []
            )

    def test_verified_package_deactivation_reuses_identical_passive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            active_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            package = generate_bootstrap(b"kernel", b"rootfs")
            (root / active_name).write_bytes(package)
            (root / "RECOVERY.OFF").write_bytes(package)
            unrelated = root / "recording.mp4"
            unrelated.write_bytes(b"keep")
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            digest = deactivate_verified_package(
                package,
                root=root,
                active_name=active_name,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertFalse((root / active_name).exists())
            self.assertEqual((root / "RECOVERY.OFF").read_bytes(), package)
            self.assertEqual(unrelated.read_bytes(), b"keep")
            self.assertEqual(digest, hashlib.sha256(package).hexdigest())

    def test_verified_package_deactivation_rejects_changed_passive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            active_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            package = generate_bootstrap(b"kernel", b"rootfs")
            (root / active_name).write_bytes(package)
            (root / "RECOVERY.OFF").write_bytes(package + b"changed")
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with self.assertRaisesRegex(MediaError, "passive recovery package differs"):
                deactivate_verified_package(
                    package,
                    root=root,
                    active_name=active_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual((root / active_name).read_bytes(), package)

    def test_verified_package_deactivation_replaces_reviewed_passive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            active_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            active = generate_bootstrap(b"new-kernel", b"new-rootfs")
            passive = generate_bootstrap(b"old-kernel", b"old-rootfs")
            (root / active_name).write_bytes(active)
            (root / "RECOVERY.OFF").write_bytes(passive)
            unrelated = root / "recording.mp4"
            unrelated.write_bytes(b"keep")
            preflight = load_media_preflight(self._preflight(root), expected_root=root)

            digest = deactivate_verified_package(
                active,
                root=root,
                active_name=active_name,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
                existing_passive_bytes=passive,
            )

            self.assertFalse((root / active_name).exists())
            self.assertEqual((root / "RECOVERY.OFF").read_bytes(), active)
            self.assertEqual(unrelated.read_bytes(), b"keep")
            self.assertFalse(
                (root / ".thingino-recovery-deactivate-rollback.part").exists()
            )
            self.assertEqual(digest, hashlib.sha256(active).hexdigest())

    def test_verified_package_deactivation_rejects_unreviewed_existing_passive(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            active_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            active = generate_bootstrap(b"new-kernel", b"new-rootfs")
            passive = generate_bootstrap(b"old-kernel", b"old-rootfs")
            (root / active_name).write_bytes(active)
            (root / "RECOVERY.OFF").write_bytes(passive)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)

            with self.assertRaisesRegex(
                MediaError, "existing passive recovery differs"
            ):
                deactivate_verified_package(
                    active,
                    root=root,
                    active_name=active_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                    existing_passive_bytes=generate_bootstrap(
                        b"other-kernel", b"other-rootfs"
                    ),
                )

            self.assertEqual((root / active_name).read_bytes(), active)
            self.assertEqual((root / "RECOVERY.OFF").read_bytes(), passive)

    def test_verified_package_deactivation_restores_both_files_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            active_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            active = generate_bootstrap(b"new-kernel", b"new-rootfs")
            passive = generate_bootstrap(b"old-kernel", b"old-rootfs")
            (root / active_name).write_bytes(active)
            (root / "RECOVERY.OFF").write_bytes(passive)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            real_sync = __import__("installer.media", fromlist=["_sync_directory"])._sync_directory
            calls = 0

            def fail_second_sync(path: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected sync failure")
                real_sync(path)

            with (
                mock.patch(
                    "installer.media._sync_directory", side_effect=fail_second_sync
                ),
                self.assertRaisesRegex(OSError, "injected sync failure"),
            ):
                deactivate_verified_package(
                    active,
                    root=root,
                    active_name=active_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                    existing_passive_bytes=passive,
                )

            self.assertEqual((root / active_name).read_bytes(), active)
            self.assertEqual((root / "RECOVERY.OFF").read_bytes(), passive)
            self.assertFalse(
                (root / ".thingino-recovery-deactivate-rollback.part").exists()
            )

    def test_stage_removes_only_its_macos_appledouble_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            output_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            real_replace = os.replace

            def replace_with_sidecar(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                (root / ("._" + output_name)).write_bytes(b"AppleDouble")

            with mock.patch(
                "installer.media.os.replace", side_effect=replace_with_sidecar
            ):
                stage_verified_package(
                    generate_bootstrap(b"kernel", b"rootfs"),
                    root=root,
                    output_name=output_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertFalse((root / ("._" + output_name)).exists())
            self.assertTrue((root / output_name).is_file())

    def test_existing_matching_file_fails_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            existing = root / "DCS6100LHV2Ax_FW999_SD.bin"
            existing.write_bytes(b"existing")
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )
            with self.assertRaisesRegex(MediaError, "already contains"):
                stage_verified_package(
                    generate_bootstrap(b"kernel", b"rootfs"),
                    root=root,
                    output_name="DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin",
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual(existing.read_bytes(), b"existing")

    def test_stage_atomically_replaces_only_the_exact_output_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            output_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_package = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_package = generate_bootstrap(b"new-kernel", b"new-rootfs")
            destination = root / output_name
            destination.write_bytes(old_package)
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )

            digest = stage_verified_package(
                new_package,
                root=root,
                output_name=output_name,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )

            self.assertEqual(destination.read_bytes(), new_package)
            self.assertEqual(digest, hashlib.sha256(new_package).hexdigest())
            self.assertEqual(
                matching_update_filenames(entry.name for entry in root.iterdir()),
                [output_name],
            )
            self.assertFalse(
                (root / ".thingino-installer-rollback.part").exists()
            )

    def test_stage_restores_old_active_package_after_final_readback_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            output_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_package = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_package = generate_bootstrap(b"new-kernel", b"new-rootfs")
            destination = root / output_name
            destination.write_bytes(old_package)
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )
            real_read_bytes = Path.read_bytes

            def corrupt_new_destination(path: Path) -> bytes:
                raw = real_read_bytes(path)
                if path == destination and raw == new_package:
                    return raw[:-1] + bytes([raw[-1] ^ 0x01])
                return raw

            with mock.patch.object(Path, "read_bytes", new=corrupt_new_destination):
                with self.assertRaisesRegex(MediaError, "activated SD package"):
                    stage_verified_package(
                        new_package,
                        root=root,
                        output_name=output_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )

            self.assertEqual(destination.read_bytes(), old_package)
            self.assertFalse(
                (root / ".thingino-installer-rollback.part").exists()
            )
            self.assertFalse((root / ".thingino-installer-upload.part").exists())

    def test_wrong_exact_device_confirmation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(
                self._preflight(root), expected_root=root
            )
            with self.assertRaisesRegex(MediaError, "confirmation"):
                stage_verified_package(
                    generate_bootstrap(b"kernel", b"rootfs"),
                    root=root,
                    output_name="DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin",
                    preflight=preflight,
                    confirmed_physical_device="/dev/wrong",
                )

    def test_install_set_activates_stage2_before_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            unrelated = root / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            with mock.patch("installer.media.validate_stage2"):
                stage_verified_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), stage2)
            self.assertEqual((root / bootstrap_name).read_bytes(), bootstrap)

    def test_split_install_set_binds_layout_policy_and_stage2(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            system = test_squashfs(64)
            command_line = final_kernel_command_line(derive_final_layout(len(system)))
            stage2 = build_stage2(
                final_kernel=test_uimage(command_line.encode("ascii")),
                system_rootfs=system,
            )
            manifest = self._split_install_manifest(
                bootstrap_name, bootstrap, stage2
            )
            stage_verified_install_set(
                bootstrap_bytes=bootstrap,
                stage2_bytes=stage2,
                manifest_bytes=manifest,
                root=root,
                bootstrap_name=bootstrap_name,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), stage2)
            self.assertEqual((root / bootstrap_name).read_bytes(), bootstrap)

    def test_universal_install_set_accepts_camera_authorized_data_policy(self) -> None:
        for data_mode in ("initialize", "preserve"):
            with self.subTest(data_mode=data_mode):
                bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
                bootstrap = generate_bootstrap(b"kernel", b"rootfs")
                system = test_squashfs(64)
                command_line = final_kernel_command_line(derive_final_layout(len(system)))
                stage2 = build_stage2(
                    final_kernel=test_uimage(command_line.encode("ascii")),
                    system_rootfs=system,
                    data_mode=data_mode,
                )
                document = json.loads(
                    self._split_install_manifest(bootstrap_name, bootstrap, stage2)
                )
                document.update(
                    {
                        "artifact_scope": "model-universal",
                        "physical_write_policy": media.universal_physical_write_policy(
                            data_mode
                        ),
                        "provisioning": "separate-per-camera-audit-and-jffs2",
                        "universal_firmware_sha256": "a" * 64,
                    }
                )
                document["artifacts"]["thingino-universal.tgb"] = {
                    "sha256": "a" * 64,
                    "size": 1,
                }
                document["region_policy"]["data"]["initialize"] = (
                    "camera-authorized-jffs2-erase-write-readback"
                )

                validated = media.validate_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=json.dumps(document).encode(),
                    bootstrap_name=bootstrap_name,
                )

                self.assertEqual(validated.data_mode, data_mode)

    def test_split_install_set_rejects_manifest_layout_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            system = test_squashfs(64)
            command_line = final_kernel_command_line(derive_final_layout(len(system)))
            stage2 = build_stage2(
                final_kernel=test_uimage(command_line.encode("ascii")),
                system_rootfs=system,
            )
            document = json.loads(
                self._split_install_manifest(bootstrap_name, bootstrap, stage2)
            )
            document["layout"]["data_mode"] = "preserve"
            with self.assertRaisesRegex(MediaError, "split layout"):
                stage_verified_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=json.dumps(document).encode(),
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertFalse((root / STAGE2_FILENAME).exists())
            self.assertFalse((root / bootstrap_name).exists())

    def test_passive_install_set_activation_preserves_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            unrelated = root / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                activate_staged_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / "STAGE1.PKG").exists())
            self.assertEqual((root / bootstrap_name).read_bytes(), bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), stage2)

    def test_passive_activation_mismatch_leaves_selector_inert(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(bootstrap + b"changed")
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "readback differs"):
                    activate_staged_install_set(
                        bootstrap_bytes=bootstrap,
                        stage2_bytes=stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
            self.assertTrue((root / "STAGE1.PKG").exists())
            self.assertFalse((root / bootstrap_name).exists())

    def test_passive_activation_removes_generated_appledouble_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            real_replace = os.replace

            def replace_with_sidecar(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                if Path(destination).name == bootstrap_name:
                    Path(destination).with_name("._" + bootstrap_name).write_bytes(
                        b"generated metadata"
                    )

            with (
                mock.patch("installer.media.validate_stage2"),
                mock.patch("installer.media.os.replace", side_effect=replace_with_sidecar),
            ):
                activate_staged_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertTrue((root / bootstrap_name).is_file())
            self.assertFalse((root / ("._" + bootstrap_name)).exists())
            self.assertEqual(
                matching_update_filenames(path.name for path in root.iterdir()),
                [bootstrap_name],
            )

    def test_passive_activation_removes_reserved_stage2_appledouble_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            (root / ("._" + STAGE2_FILENAME)).write_bytes(b"generated metadata")
            unrelated_sidecar = root / "._photo.jpg"
            unrelated_sidecar.write_bytes(b"keep")
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(bootstrap),
                            "sha256": hashlib.sha256(bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(stage2),
                            "sha256": hashlib.sha256(stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                activate_staged_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertFalse((root / ("._" + STAGE2_FILENAME)).exists())
            self.assertEqual(unrelated_sidecar.read_bytes(), b"keep")

    def test_active_install_set_deactivation_retains_verified_stage2(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            unrelated = root / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            (root / bootstrap_name).write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                deactivate_staged_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / bootstrap_name).exists())
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), stage2)

    def test_active_deactivation_mismatch_leaves_selector_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            (root / bootstrap_name).write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2 + b"changed")
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "readback differs"):
                    deactivate_staged_install_set(
                        bootstrap_bytes=bootstrap,
                        stage2_bytes=stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
            self.assertTrue((root / bootstrap_name).exists())
            self.assertFalse((root / "STAGE1.PKG").exists())

    def test_active_deactivation_replaces_reviewed_existing_passive(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            stage2 = b"stage2"
            (root / bootstrap_name).write_bytes(new_bootstrap)
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(new_bootstrap), "sha256": hashlib.sha256(new_bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                deactivate_staged_install_set(
                    bootstrap_bytes=new_bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                    existing_passive_bytes=old_bootstrap,
                )
            self.assertFalse((root / bootstrap_name).exists())
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), new_bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), stage2)
            self.assertFalse(
                (root / ".thingino-stage1-deactivate-rollback.part").exists()
            )

    def test_active_deactivation_rejects_unreviewed_existing_passive(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            other_bootstrap = generate_bootstrap(b"other-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            stage2 = b"stage2"
            (root / bootstrap_name).write_bytes(new_bootstrap)
            (root / "STAGE1.PKG").write_bytes(other_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(new_bootstrap), "sha256": hashlib.sha256(new_bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "existing passive"):
                    deactivate_staged_install_set(
                        bootstrap_bytes=new_bootstrap,
                        stage2_bytes=stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                        existing_passive_bytes=old_bootstrap,
                    )
            self.assertEqual((root / bootstrap_name).read_bytes(), new_bootstrap)
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), other_bootstrap)

    def test_passive_bootstrap_replacement_retains_stage2_and_unrelated_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            unrelated = root / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(new_bootstrap), "sha256": hashlib.sha256(new_bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                replace_passive_bootstrap(
                    old_bootstrap_bytes=old_bootstrap,
                    new_bootstrap_bytes=new_bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), new_bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), stage2)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / ".thingino-stage1-rollback.part").exists())
            self.assertEqual(matching_update_filenames(p.name for p in root.iterdir()), [])

    def test_passive_bootstrap_replacement_removes_reserved_output_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            (root / "._STAGE1.PKG").write_bytes(b"old metadata")
            (root / ("._" + STAGE2_FILENAME)).write_bytes(b"old metadata")
            unrelated_sidecar = root / "._photo.jpg"
            unrelated_sidecar.write_bytes(b"keep")
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(new_bootstrap),
                            "sha256": hashlib.sha256(new_bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(stage2),
                            "sha256": hashlib.sha256(stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                replace_passive_bootstrap(
                    old_bootstrap_bytes=old_bootstrap,
                    new_bootstrap_bytes=new_bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertFalse((root / "._STAGE1.PKG").exists())
            self.assertFalse((root / ("._" + STAGE2_FILENAME)).exists())
            self.assertEqual(unrelated_sidecar.read_bytes(), b"keep")

    def test_passive_bootstrap_replacement_rejects_wrong_old_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            reviewed_old = generate_bootstrap(b"old-kernel", b"old-rootfs")
            actual_old = generate_bootstrap(b"other-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            stage2 = b"stage2"
            (root / "STAGE1.PKG").write_bytes(actual_old)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(new_bootstrap), "sha256": hashlib.sha256(new_bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "old passive"):
                    replace_passive_bootstrap(
                        old_bootstrap_bytes=reviewed_old,
                        new_bootstrap_bytes=new_bootstrap,
                        stage2_bytes=stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), actual_old)
            self.assertFalse((root / ".thingino-stage1-rollback.part").exists())

    def test_retired_42_22_schema2_pair_is_replaced_without_legacy_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            old_stage2 = b"retired-42-22-schema2"
            new_stage2 = b"current-39-25-schema2"
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(old_stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(new_bootstrap),
                            "sha256": hashlib.sha256(new_bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(new_stage2),
                            "sha256": hashlib.sha256(new_stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)

            def validate_current(raw: bytes) -> object:
                if raw == old_stage2:
                    from installer.artifacts import ArtifactError

                    raise ArtifactError("retired memory split")
                return mock.sentinel.current_stage2

            with (
                mock.patch("installer.media.validate_stage2", side_effect=validate_current),
                mock.patch(
                    "installer.media.validate_retired_stage2_v2_39_25"
                ) as retired,
                mock.patch("installer.media.validate_legacy_stage2_v1") as legacy,
            ):
                replace_passive_bootstrap(
                    old_bootstrap_bytes=old_bootstrap,
                    old_stage2_bytes=old_stage2,
                    new_bootstrap_bytes=new_bootstrap,
                    stage2_bytes=new_stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
                retired.assert_called_once_with(old_stage2)
                legacy.assert_not_called()
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), new_bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), new_stage2)
            self.assertEqual(matching_update_filenames(p.name for p in root.iterdir()), [])

    def test_passive_replacement_requires_profile_for_legacy_stage2(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            old_stage2 = b"legacy-stage2-v1"
            new_stage2 = b"current-stage2-v2"
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(old_stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(new_bootstrap),
                            "sha256": hashlib.sha256(new_bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(new_stage2),
                            "sha256": hashlib.sha256(new_stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)

            def validate_current(raw: bytes) -> object:
                if raw == old_stage2:
                    from installer.stage2 import Stage2Error

                    raise Stage2Error("schema 1")
                return mock.sentinel.current_stage2

            with (
                mock.patch("installer.media.validate_stage2", side_effect=validate_current),
                mock.patch("installer.media.validate_legacy_stage2_v1") as legacy,
            ):
                with self.assertRaisesRegex(MediaError, "explicit profile"):
                    replace_passive_bootstrap(
                        old_bootstrap_bytes=old_bootstrap,
                        old_stage2_bytes=old_stage2,
                        new_bootstrap_bytes=new_bootstrap,
                        stage2_bytes=new_stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
                legacy.assert_not_called()
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), old_bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), old_stage2)

    def test_profiled_legacy_pair_is_replaced_transactionally(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            old_stage2 = b"legacy-stage2-v1"
            new_stage2 = b"current-stage2-v2"
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(old_stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(new_bootstrap),
                            "sha256": hashlib.sha256(new_bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(new_stage2),
                            "sha256": hashlib.sha256(new_stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            profile = json.dumps(
                {
                    "artifacts": {
                        bootstrap_name: {
                            "sha256": hashlib.sha256(old_bootstrap).hexdigest(),
                            "size": len(old_bootstrap),
                        },
                        STAGE2_FILENAME: {
                            "sha256": hashlib.sha256(old_stage2).hexdigest(),
                            "size": len(old_stage2),
                        },
                    },
                    "migration": LEGACY_MIGRATION_PROFILE_KIND,
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)

            def validate_current(raw: bytes) -> object:
                if raw == old_stage2:
                    from installer.stage2 import Stage2Error

                    raise Stage2Error("schema 1")
                return mock.sentinel.current_stage2

            with (
                mock.patch("installer.media.validate_stage2", side_effect=validate_current),
                mock.patch("installer.media.validate_legacy_stage2_v1") as legacy,
            ):
                replace_passive_bootstrap(
                    old_bootstrap_bytes=old_bootstrap,
                    old_stage2_bytes=old_stage2,
                    legacy_migration_profile_bytes=profile,
                    new_bootstrap_bytes=new_bootstrap,
                    stage2_bytes=new_stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
                legacy.assert_called_once_with(old_stage2)
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), new_bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), new_stage2)
            self.assertEqual(matching_update_filenames(p.name for p in root.iterdir()), [])

    def test_passive_replacement_cleanup_failure_keeps_verified_new_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            old_bootstrap = generate_bootstrap(b"old-kernel", b"old-rootfs")
            new_bootstrap = generate_bootstrap(b"new-kernel", b"new-rootfs")
            old_stage2 = b"old-stage2"
            new_stage2 = b"new-stage2"
            (root / "STAGE1.PKG").write_bytes(old_bootstrap)
            (root / STAGE2_FILENAME).write_bytes(old_stage2)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(new_bootstrap),
                            "sha256": hashlib.sha256(new_bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(new_stage2),
                            "sha256": hashlib.sha256(new_stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            original_unlink = Path.unlink
            failed = False

            def fail_one_cleanup(path: Path, *args: object, **kwargs: object) -> None:
                nonlocal failed
                if path.name == ".thingino-stage2-rollback.part" and not failed:
                    failed = True
                    raise OSError("injected cleanup failure")
                original_unlink(path, *args, **kwargs)

            with mock.patch("installer.media.validate_stage2"):
                with mock.patch.object(Path, "unlink", autospec=True, side_effect=fail_one_cleanup):
                    with self.assertRaisesRegex(
                        MediaError, "active and verified, but old-file cleanup failed"
                    ):
                        replace_passive_bootstrap(
                            old_bootstrap_bytes=old_bootstrap,
                            old_stage2_bytes=old_stage2,
                            new_bootstrap_bytes=new_bootstrap,
                            stage2_bytes=new_stage2,
                            manifest_bytes=manifest,
                            root=root,
                            bootstrap_name=bootstrap_name,
                            preflight=preflight,
                            confirmed_physical_device="/dev/test-external-media",
                        )

            self.assertEqual((root / "STAGE1.PKG").read_bytes(), new_bootstrap)
            self.assertEqual((root / STAGE2_FILENAME).read_bytes(), new_stage2)
            self.assertFalse((root / ".thingino-stage1-rollback.part").exists())
            self.assertEqual(
                (root / ".thingino-stage2-rollback.part").read_bytes(),
                old_stage2,
            )
            self.assertEqual(matching_update_filenames(p.name for p in root.iterdir()), [])

    def test_install_set_manifest_mismatch_leaves_media_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {},
                }
            ).encode()
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "does not bind"):
                    stage_verified_install_set(
                        bootstrap_bytes=bootstrap,
                        stage2_bytes=b"stage2",
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
            self.assertEqual(
                [entry.name for entry in root.iterdir()],
                ["preflight.json"],
            )

    def test_archive_existing_stock_backup_preserves_exact_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            backup = b"S" * 0x007C0000
            (root / "STOCKM3.BIN").write_bytes(backup)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            result = archive_existing_stock_backup(
                root=root,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertFalse((root / "STOCKM3.BIN").exists())
            self.assertEqual((root / "STOCKM3.OLD").read_bytes(), backup)
            self.assertEqual(
                result["STOCKM3.OLD"], hashlib.sha256(backup).hexdigest()
            )

    def test_archive_refuses_to_separate_recovery_checkpoint_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            backup = b"S" * 0x007C0000
            stage2 = b"stage2"
            checkpoint = self._recovery_checkpoint(backup, stage2)
            (root / "STOCKM3.BIN").write_bytes(backup)
            (root / "STOCKM3.OK").write_bytes(checkpoint)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with self.assertRaisesRegex(MediaError, "checkpoint is present"):
                archive_existing_stock_backup(
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertEqual((root / "STOCKM3.BIN").read_bytes(), backup)
            self.assertEqual((root / "STOCKM3.OK").read_bytes(), checkpoint)
            self.assertFalse((root / "STOCKM3.OLD").exists())

    def test_evacuation_copies_and_clears_all_reserved_stock_backup_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            root = parent / "card"
            root.mkdir()
            backup = b"S" * 0x007C0000
            archived = b"A" * 0x007C0000
            stage2 = b"stage2"
            checkpoint = self._recovery_checkpoint(backup, stage2)
            (root / "STOCKM3.BIN").write_bytes(backup)
            (root / "STOCKM3.OLD").write_bytes(archived)
            (root / "STOCKM3.OK").write_bytes(checkpoint)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            destination = parent / "private-evacuation"
            result = evacuate_existing_stock_backups(
                root=root,
                destination_dir=destination,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertEqual(destination.joinpath("STOCKM3.BIN").read_bytes(), backup)
            self.assertEqual(destination.joinpath("STOCKM3.OLD").read_bytes(), archived)
            self.assertEqual(destination.joinpath("STOCKM3.OK").read_bytes(), checkpoint)
            self.assertTrue(destination.joinpath("evacuation.manifest.private.json").is_file())
            self.assertFalse(root.joinpath("STOCKM3.BIN").exists())
            self.assertFalse(root.joinpath("STOCKM3.OLD").exists())
            self.assertFalse(root.joinpath("STOCKM3.OK").exists())
            self.assertEqual(result["STOCKM3.BIN"], hashlib.sha256(backup).hexdigest())

    def test_evacuation_preserves_structurally_bound_universal_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            root = parent / "card"
            root.mkdir()
            backup = b"S" * 0x007C0000
            stage2 = b"stage2"
            checkpoint = self._recovery_checkpoint(
                backup,
                stage2,
                authorization=b"old-authorization",
                provisioning=b"old-provisioning",
            )
            (root / "STOCKM3.BIN").write_bytes(backup)
            (root / "STOCKM3.OK").write_bytes(checkpoint)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            destination = parent / "private-universal-evacuation"
            evacuate_existing_stock_backups(
                root=root,
                destination_dir=destination,
                preflight=preflight,
                confirmed_physical_device="/dev/test-external-media",
            )
            self.assertEqual(destination.joinpath("STOCKM3.BIN").read_bytes(), backup)
            self.assertEqual(destination.joinpath("STOCKM3.OK").read_bytes(), checkpoint)
            self.assertFalse(root.joinpath("STOCKM3.BIN").exists())
            self.assertFalse(root.joinpath("STOCKM3.OK").exists())

    def test_evacuation_refuses_destination_on_sd_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "STOCKM3.BIN").write_bytes(b"S" * 0x007C0000)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with self.assertRaisesRegex(MediaError, "outside the SD root"):
                evacuate_existing_stock_backups(
                    root=root,
                    destination_dir=root / "private-evacuation",
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )

    def test_recovery_activation_validates_checkpoint_before_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            backup = b"S" * 0x007C0000
            (root / "STAGE1.PKG").write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            (root / "STOCKM3.BIN").write_bytes(backup)
            (root / "STOCKM3.OK").write_bytes(
                self._recovery_checkpoint(backup, stage2)
            )
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {
                        "hardware_revision": "A1",
                        "model": "DCS-6100LHV2",
                    },
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(bootstrap),
                            "sha256": hashlib.sha256(bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(stage2),
                            "sha256": hashlib.sha256(stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                activate_staged_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                )
            self.assertFalse((root / "STAGE1.PKG").exists())
            self.assertEqual((root / bootstrap_name).read_bytes(), bootstrap)

    def test_recovery_activation_rejects_changed_checkpoint_before_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            backup = b"S" * 0x007C0000
            checkpoint = bytearray(self._recovery_checkpoint(backup, stage2))
            checkpoint[-1] ^= 0x01
            (root / "STAGE1.PKG").write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            (root / "STOCKM3.BIN").write_bytes(backup)
            (root / "STOCKM3.OK").write_bytes(checkpoint)
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {
                        "hardware_revision": "A1",
                        "model": "DCS-6100LHV2",
                    },
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(bootstrap),
                            "sha256": hashlib.sha256(bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(stage2),
                            "sha256": hashlib.sha256(stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "does not bind"):
                    activate_staged_install_set(
                        bootstrap_bytes=bootstrap,
                        stage2_bytes=stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
            self.assertEqual((root / "STAGE1.PKG").read_bytes(), bootstrap)
            self.assertFalse((root / bootstrap_name).exists())

    def test_recovery_activation_accepts_bound_universal_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            backup = b"S" * 0x007C0000
            authorization = b"camera-authorization"
            provisioning = b"provisioning-jffs2"
            (root / "STAGE1.PKG").write_bytes(bootstrap)
            (root / STAGE2_FILENAME).write_bytes(stage2)
            (root / "STOCKM3.BIN").write_bytes(backup)
            (root / "STOCKM3.OK").write_bytes(
                self._recovery_checkpoint(
                    backup,
                    stage2,
                    authorization=authorization,
                    provisioning=provisioning,
                )
            )
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {
                        "hardware_revision": "A1",
                        "model": "DCS-6100LHV2",
                    },
                    "artifacts": {
                        bootstrap_name: {
                            "size": len(bootstrap),
                            "sha256": hashlib.sha256(bootstrap).hexdigest(),
                        },
                        STAGE2_FILENAME: {
                            "size": len(stage2),
                            "sha256": hashlib.sha256(stage2).hexdigest(),
                        },
                    },
                }
            ).encode()
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            with mock.patch("installer.media.validate_stage2"):
                activate_staged_install_set(
                    bootstrap_bytes=bootstrap,
                    stage2_bytes=stage2,
                    manifest_bytes=manifest,
                    root=root,
                    bootstrap_name=bootstrap_name,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-external-media",
                    recovery_authorization_bytes=authorization,
                    recovery_provisioning_bytes=provisioning,
                )
            self.assertFalse((root / "STAGE1.PKG").exists())
            self.assertEqual((root / bootstrap_name).read_bytes(), bootstrap)

    def test_install_set_rejects_preexisting_reserved_appledouble_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            preflight = load_media_preflight(self._preflight(root), expected_root=root)
            sidecar = root / "._THINGINO2.BIN"
            sidecar.write_bytes(b"preexisting")
            bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"
            bootstrap = generate_bootstrap(b"kernel", b"rootfs")
            stage2 = b"stage2"
            manifest = json.dumps(
                {
                    "schema_version": 1,
                    "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
                    "artifacts": {
                        bootstrap_name: {"size": len(bootstrap), "sha256": hashlib.sha256(bootstrap).hexdigest()},
                        STAGE2_FILENAME: {"size": len(stage2), "sha256": hashlib.sha256(stage2).hexdigest()},
                    },
                }
            ).encode()
            with mock.patch("installer.media.validate_stage2"):
                with self.assertRaisesRegex(MediaError, "reserved"):
                    stage_verified_install_set(
                        bootstrap_bytes=bootstrap,
                        stage2_bytes=stage2,
                        manifest_bytes=manifest,
                        root=root,
                        bootstrap_name=bootstrap_name,
                        preflight=preflight,
                        confirmed_physical_device="/dev/test-external-media",
                    )
            self.assertEqual(sidecar.read_bytes(), b"preexisting")


if __name__ == "__main__":
    unittest.main()
