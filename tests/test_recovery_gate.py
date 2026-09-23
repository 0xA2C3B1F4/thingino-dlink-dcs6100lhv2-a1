from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import full_backup
from installer.layout import Partition, Target
from installer import recovery_gate
from installer.recovery_gate import (
    RecoveryGateError,
    validate_existing_recovery_boundary,
    validate_functional_recovery_boundary,
)


TARGET = Target(
    model="DCS-6100LHV2",
    hardware_revision="A1",
    nor_size=42,
    erase_block_size=1,
    partitions=tuple(
        Partition(f"part{mtd}", mtd, mtd * 7, 7, always_preserve=mtd in (0, 4, 5))
        for mtd in range(6)
    ),
)


def identity(raw: bytes) -> dict[str, object]:
    return {
        "md5": hashlib.md5(raw, usedforsecurity=False).hexdigest(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }


def fixture(root: Path, *, stock_config: bytes | None = None) -> tuple[Path, Path]:
    recovery = root / "recovery"
    recovery.mkdir()
    chunks = [bytes([mtd + 1]) * 7 for mtd in range(6)]
    if stock_config is not None:
        assert len(stock_config) == TARGET.partition(5).size
        chunks[5] = stock_config
    full = b"".join(chunks)
    files: dict[str, object] = {}
    for copy in ("a", "b"):
        copy_dir = recovery / f"copy-{copy}"
        copy_dir.mkdir()
        for mtd, raw in enumerate(chunks):
            relative = f"copy-{copy}/mtd{mtd}.bin"
            (recovery / relative).write_bytes(raw)
            files[relative] = identity(raw)
        name = f"full-flash-{copy}.bin"
        (recovery / name).write_bytes(full)
        files[name] = identity(full)
    manifest = {
        "schema": 1,
        "description": "private test fixture",
        "checks": {
            "copy_a_matches_copy_b": True,
            "device_md5_matches_host": True,
            "full_flash_a_matches_full_flash_b": True,
        },
        "files": files,
        "firmware_runtime": "1.02.02",
        "partition_order": [f"mtd{mtd}" for mtd in range(6)],
        "total_flash_size": len(full),
    }
    (recovery / "manifest.private.json").write_text(json.dumps(manifest), encoding="utf-8")

    readback = root / "readback"
    readback.mkdir()
    layout = {
        "read_only": True,
        "schema_version": 1,
        "target": {
            "erase_block_size": 1,
            "flash_size": 42,
            "hardware_revision": "A1",
            "model": "DCS-6100LHV2",
            "partitions": [
                {"mtd": p.mtd, "name": p.name, "offset": p.offset, "size": p.size}
                for p in TARGET.partitions
            ],
        },
    }
    (readback / "device-layout.private.json").write_text(json.dumps(layout), encoding="utf-8")
    for mtd in (0, 4, 5):
        (readback / f"mtd{mtd}.bin").write_bytes(chunks[mtd])
    return recovery, readback


class RecoveryGateTests(unittest.TestCase):
    def test_changed_stock_config_requires_new_capture_binding(self) -> None:
        # Synthetic snapshots model a stock configuration change, not a live
        # camera probe. Both snapshots retain the same model and boot bytes.
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            before_root, after_root = root / "before", root / "after"
            before_root.mkdir()
            after_root.mkdir()
            old_recovery, old_readback = fixture(before_root)
            new_recovery, new_readback = fixture(
                after_root, stock_config=b"changed"
            )
            old = validate_existing_recovery_boundary(
                recovery_dir=old_recovery,
                preserved_readback_dir=old_readback,
                target=TARGET,
            )
            with self.assertRaisesRegex(RecoveryGateError, "mtd5 differs"):
                validate_existing_recovery_boundary(
                    recovery_dir=old_recovery,
                    preserved_readback_dir=new_readback,
                    target=TARGET,
                )
            fresh = validate_existing_recovery_boundary(
                recovery_dir=new_recovery,
                preserved_readback_dir=new_readback,
                target=TARGET,
            )
            self.assertNotEqual(old.camera_identity_sha256, fresh.camera_identity_sha256)
            self.assertNotEqual(
                old.camera_authorization_key_sha256,
                fresh.camera_authorization_key_sha256,
            )
            # A consistent old pair still passes offline. Host validation must
            # not be advertised as evidence of the current camera's bytes.
            repeated = validate_existing_recovery_boundary(
                recovery_dir=old_recovery,
                preserved_readback_dir=old_readback,
                target=TARGET,
            )
            self.assertEqual(old.camera_identity_sha256, repeated.camera_identity_sha256)
            self.assertEqual(
                old.camera_authorization_key_sha256,
                repeated.camera_authorization_key_sha256,
            )

    def test_existing_verified_pair_replaces_redundant_new_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            recovery, readback = fixture(Path(directory_name))

            decision = validate_existing_recovery_boundary(
                recovery_dir=recovery,
                preserved_readback_dir=readback,
                target=TARGET,
            )

            self.assertEqual(decision.mode, "existing-verified-same-device-pair")
            self.assertEqual(decision.preserved_mtd, (0, 4, 5))
            self.assertEqual(decision.recovery_images, 2)
            self.assertRegex(decision.camera_identity_sha256, r"^[0-9a-f]{64}$")
            expected_identity = hashlib.sha256()
            expected_identity.update(b"thingino-dcs6100-camera-identity-v1\0")
            expected_key = hashlib.sha256()
            expected_key.update(
                b"thingino-dcs6100-camera-authorization-key-v1\0"
            )
            for digest in (expected_identity, expected_key):
                digest.update(TARGET.model.encode("ascii") + b"\0")
                digest.update(TARGET.hardware_revision.encode("ascii") + b"\0")
                digest.update(TARGET.nor_size.to_bytes(8, "big"))
            for mtd in (0, 4, 5):
                raw = (readback / f"mtd{mtd}.bin").read_bytes()
                for digest in (expected_identity, expected_key):
                    digest.update(bytes((mtd,)))
                    digest.update(len(raw).to_bytes(8, "big"))
                    digest.update(raw)
            self.assertEqual(
                decision.camera_identity_sha256,
                expected_identity.hexdigest(),
            )
            self.assertEqual(
                decision.camera_authorization_key_sha256,
                expected_key.hexdigest(),
            )
            self.assertNotIn(
                decision.camera_authorization_key_sha256,
                repr(decision),
            )
            repeated = validate_existing_recovery_boundary(
                recovery_dir=recovery,
                preserved_readback_dir=readback,
                target=TARGET,
            )
            self.assertEqual(
                repeated.camera_identity_sha256,
                decision.camera_identity_sha256,
            )

    def test_gate_uses_the_same_manifest_snapshot_for_backup_and_device_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            recovery, readback = fixture(Path(directory_name))
            with mock.patch.object(
                full_backup,
                "load_private_manifest",
                wraps=full_backup.load_private_manifest,
            ) as load:
                validate_existing_recovery_boundary(
                    recovery_dir=recovery,
                    preserved_readback_dir=readback,
                    target=TARGET,
                )
            load.assert_called_once_with(recovery)

    def test_functional_recovery_uses_a_separate_honest_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            recovery, readback = fixture(Path(directory_name))
            manifest = json.loads((recovery / "manifest.private.json").read_text())
            with mock.patch.object(
                recovery_gate,
                "validate_functional_backup_with_manifest",
                return_value=(SimpleNamespace(recovery_images=2), manifest),
            ):
                decision = validate_functional_recovery_boundary(
                    recovery_dir=recovery,
                    preserved_readback_dir=readback,
                    target=TARGET,
                )
            self.assertTrue(decision.functional_recovery_accepted)
            self.assertFalse(decision.original_complete_backup_accepted)
            self.assertEqual(decision.preserved_mtd, (0, 4, 5))
            self.assertEqual(decision.original_preserved_mtd, (0, 3, 4, 5))
            self.assertRegex(decision.camera_identity_sha256, r"^[0-9a-f]{64}$")

    def test_other_device_or_changed_secret_partition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            recovery, readback = fixture(Path(directory_name))
            (readback / "mtd5.bin").write_bytes(b"different")

            with self.assertRaisesRegex(RecoveryGateError, "mtd5"):
                validate_existing_recovery_boundary(
                    recovery_dir=recovery,
                    preserved_readback_dir=readback,
                    target=TARGET,
                )


if __name__ == "__main__":
    unittest.main()
