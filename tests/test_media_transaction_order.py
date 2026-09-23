"""Characterize media commit boundaries using host-only synthetic files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from installer import media
from installer.media_preflight import MediaPreflight
from installer.sd_package import generate_bootstrap


class MediaTransactionOrderTests(unittest.TestCase):
    bootstrap_name = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        identity = self.root.stat()
        self.preflight = MediaPreflight(
            physical_device="/dev/test-external-media",
            model="TEST REMOVABLE MEDIA",
            capacity_bytes=8_000_000_000,
            filesystem="fat32",
            mount_root=self.root.resolve(),
            mount_device_id=identity.st_dev,
            mount_inode=identity.st_ino,
        )
        self.bootstrap = generate_bootstrap(b"kernel", b"rootfs")
        self.stage2 = b"synthetic-stage2"
        self.manifest = json.dumps({
            "schema_version": 1,
            "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
            "artifacts": {
                name: {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                for name, raw in (
                    (self.bootstrap_name, self.bootstrap),
                    (media.STAGE2_FILENAME, self.stage2),
                )
            },
        }).encode()
        self.unrelated = self.root / "keep.txt"
        self.unrelated.write_bytes(b"unrelated")
        # Manifest, selector and transaction validation remain real. This test
        # exercises filesystem order, not the stage-2 binary format.
        self.stack.enter_context(mock.patch("installer.media.validate_stage2"))

    def arguments(self) -> dict:
        return {
            "bootstrap_bytes": self.bootstrap,
            "stage2_bytes": self.stage2,
            "manifest_bytes": self.manifest,
            "root": self.root,
            "bootstrap_name": self.bootstrap_name,
            "preflight": self.preflight,
            "confirmed_physical_device": self.preflight.physical_device,
        }

    def test_install_set_verifies_both_temporary_files_before_selector_commit(self) -> None:
        events = []
        original_write = media._write_verified_temporary
        original_replace = os.replace

        def write(path: Path, raw: bytes) -> None:
            original_write(path, raw)
            self.assertEqual(path.read_bytes(), raw)
            events.append(("verified", path.name))

        def replace(source: Path, destination: Path) -> None:
            if destination.name == self.bootstrap_name:
                self.assertEqual(events[-1], ("sync", self.root.name))
                self.assertEqual((self.root / media.STAGE2_FILENAME).read_bytes(), self.stage2)
            original_replace(source, destination)
            events.append(("replace", destination.name))

        with mock.patch("installer.media._write_verified_temporary", side_effect=write), \
             mock.patch("installer.media._sync_directory", side_effect=lambda path: events.append(("sync", path.name))), \
             mock.patch("installer.media.os.replace", side_effect=replace):
            result = media.stage_verified_install_set(**self.arguments())
        self.assertEqual(events, [
            ("verified", ".thingino-stage2-upload.part"),
            ("verified", ".thingino-installer-upload.part"),
            ("replace", media.STAGE2_FILENAME),
            ("sync", self.root.name),
            ("replace", self.bootstrap_name),
            ("sync", self.root.name),
        ])
        self.assertEqual(result[self.bootstrap_name], hashlib.sha256(self.bootstrap).hexdigest())
        self.assertEqual(self.unrelated.read_bytes(), b"unrelated")

    def test_install_set_rolls_back_at_each_write_rename_and_sync_boundary(self) -> None:
        for exception_type in (OSError, KeyboardInterrupt):
            for failure_index in range(1, 7):
                with self.subTest(exception=exception_type.__name__, boundary=failure_index):
                    count = 0
                    original_write = media._write_verified_temporary
                    original_replace = os.replace

                    def step() -> None:
                        nonlocal count
                        count += 1
                        if count == failure_index:
                            raise exception_type("injected transaction boundary")

                    def write(path: Path, raw: bytes) -> None:
                        # A failed write can leave a partial file behind.
                        path.write_bytes(raw[:1])
                        step()
                        path.unlink()
                        original_write(path, raw)

                    def replace(source: Path, destination: Path) -> None:
                        step()
                        original_replace(source, destination)

                    with mock.patch("installer.media._write_verified_temporary", side_effect=write), \
                         mock.patch("installer.media._sync_directory", side_effect=lambda _path: step()), \
                         mock.patch("installer.media.os.replace", side_effect=replace):
                        with self.assertRaisesRegex(exception_type, "injected transaction boundary"):
                            media.stage_verified_install_set(**self.arguments())
                    self.assertEqual(count, failure_index)
                    self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["keep.txt"])
                    self.assertEqual(self.unrelated.read_bytes(), b"unrelated")

    def test_changed_media_identity_fails_before_any_write(self) -> None:
        changed = MediaPreflight(
            physical_device=self.preflight.physical_device,
            model=self.preflight.model,
            capacity_bytes=self.preflight.capacity_bytes,
            filesystem=self.preflight.filesystem,
            mount_root=self.preflight.mount_root,
            mount_device_id=self.preflight.mount_device_id,
            mount_inode=self.preflight.mount_inode + 1,
        )
        arguments = self.arguments()
        arguments["preflight"] = changed
        with mock.patch("installer.media._write_verified_temporary") as write:
            with self.assertRaisesRegex(media.MediaError, "media identity changed"):
                media.stage_verified_install_set(**arguments)
            write.assert_not_called()
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["keep.txt"])

    def test_activation_sync_failure_restores_passive_selector_and_syncs_rollback(self) -> None:
        passive = self.root / media.PASSIVE_BOOTSTRAP_FILENAME
        passive.write_bytes(self.bootstrap)
        (self.root / media.STAGE2_FILENAME).write_bytes(self.stage2)
        events = []
        original_replace = os.replace

        def replace(source: Path, destination: Path) -> None:
            original_replace(source, destination)
            events.append(("replace", destination.name))

        def sync(_path: Path) -> None:
            events.append(("sync", "directory"))
            if len(events) == 2:
                raise OSError("activation sync failed")

        with mock.patch("installer.media.os.replace", side_effect=replace), \
             mock.patch("installer.media._sync_directory", side_effect=sync):
            with self.assertRaisesRegex(OSError, "activation sync failed"):
                media.activate_staged_install_set(**self.arguments())
        self.assertEqual(events, [
            ("replace", self.bootstrap_name), ("sync", "directory"),
            ("replace", media.PASSIVE_BOOTSTRAP_FILENAME), ("sync", "directory"),
        ])
        self.assertEqual(passive.read_bytes(), self.bootstrap)
        self.assertFalse((self.root / self.bootstrap_name).exists())
        self.assertEqual((self.root / media.STAGE2_FILENAME).read_bytes(), self.stage2)
        self.assertEqual(self.unrelated.read_bytes(), b"unrelated")


if __name__ == "__main__":
    unittest.main()
