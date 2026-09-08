"""Synthetic-card transactions; no physical media, cameras or private backups."""

import ast
import contextlib
import hashlib
import io
import json
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from installer import capture_reuse as reuse, capture_state as state
from installer import (
    recovery_actions as recovery,
    media,
    user_cli,
    install_project as projects,
)
from installer.install_actions import WriteConfirmation
from installer.sd_package import generate_bootstrap, parse_package, package_manifest


class CaptureReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.card = self.root / "card"
        self.card.mkdir()
        self.output = self.root / "private-archive"
        self.request = reuse.CaptureReuseInputs(self.card, "/dev/sdz", self.output)
        self.preflight = {
            "schema_version": 1,
            "host_platform": "linux",
            "physical_device": "/dev/sdz",
            "model": "synthetic-card",
            "capacity_bytes": 8 * 1024**3,
            "filesystem": "fat32",
            "mount_root": str(self.card),
            "media_uuid": "synthetic-card-a",
            "external": True,
            "physical": True,
            "writable": True,
            "system_device": False,
            "ambiguous": False,
        }
        for module in (reuse, recovery):
            patch = mock.patch.object(
                module,
                "create_preflight_document",
                side_effect=lambda **_: dict(self.preflight),
            )
            patch.start()
            self.addCleanup(patch.stop)
        # Temporary directories emulate separate SD and host filesystems. No OS media calls.
        patch = mock.patch.object(reuse, "_same_filesystem", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)
        self.package = generate_bootstrap(b"synthetic-kernel", b"synthetic-root")
        self.package_path = self.root / "capture.bin"
        self.package_path.write_bytes(self.package)
        manifest = json.loads(
            package_manifest(
                parse_package(self.package, require_project_header=True),
                purpose="uartless-functional-capture",
            )
        )
        manifest.update(
            future_physical_boot_writes_mtd=[1, 2],
            original_complete_backup=False,
            original_preserved_mtd=[0, 3, 4, 5],
            restoration_class="recovery-functional",
        )
        self.manifest_path = self.root / "capture.json"
        self.manifest_path.write_text(json.dumps(manifest))
        self.capture = recovery.CaptureMediaInputs(
            self.package_path, self.manifest_path, self.card, "/dev/sdz"
        )

    def seed(self):
        (self.card / state.PASSIVE).write_bytes(self.package)
        for name in sorted(state.DIRECTORIES):
            (self.card / name).mkdir()
        for name in state.FILE_LIMITS:
            if name != state.PASSIVE:
                (self.card / name).write_bytes(b"synthetic-partial-capture")
        (self.card / "recording.mp4").write_bytes(b"unrelated-recording")
        (self.card / "STOCKM3.BIN").write_bytes(b"separate-checkpoint")

    def confirmation(self, request=None):
        plan = reuse.plan_capture_reuse(request or self.request)
        return WriteConfirmation(**plan.required_confirmations())

    def execute(self, request=None, confirmation=None, emit=None):
        request = request or self.request
        return reuse.reuse_capture(
            request,
            confirmation or self.confirmation(request),
            confirmed_output_dir=request.output_dir,
            emit=emit,
        )

    def cli(self, args, code=0):
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(io.StringIO()),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            actual = user_cli.main([*args, "--non-interactive", "--json"])
        self.assertEqual(actual, code, output.getvalue())
        return json.loads(output.getvalue())

    def test_real_cli_plan_archive_same_card_next_camera_separate_projects_same_firmware(
        self,
    ):
        self.seed()
        original = state.capture_inventory(self.card)
        firmware = self.root / "one-install-set"
        firmware.mkdir()
        (firmware / "thingino-universal.tgb").write_bytes(
            b"one-synthetic-model-universal-build"
        )
        public_key = firmware / "model-public-key.pem"
        public_key.write_bytes(b"synthetic-model-public-key")
        firmware_before = projects.fingerprint(firmware)
        paths = [self.root / "camera-a.json", self.root / "camera-b.json"]
        for label, path in zip(("Camera A", "Camera B"), paths):
            self.cli(["project", "init", "--project", str(path), "--name", label])
            self.cli(
                [
                    "project",
                    "attach",
                    "--project",
                    str(path),
                    "--install-set-dir",
                    str(firmware),
                    "--universal-public-key",
                    str(public_key),
                    "--package",
                    str(self.package_path),
                    "--package-manifest",
                    str(self.manifest_path),
                ]
            )
        a_before = projects.load_project(paths[0])
        # Camera A was created by the pre-feature installer.
        del a_before.selections[reuse.OPERATION]
        projects.save_project(a_before)
        legacy_bytes = paths[0].read_bytes()
        b_before = projects.load_project(paths[1])
        for command, field in (
            ("universal init-session", "output-dir"),
            ("universal configure", "output-dir"),
            ("universal authorize", "output-dir"),
            ("stock-recovery uartless-validate", "output-dir"),
        ):
            self.assertNotEqual(
                a_before.selections[command][field], b_before.selections[command][field]
            )
        common = ["--whole-device", "/dev/sdz", "--mount-root", str(self.card)]
        blocked = self.cli(
            [
                "stock-recovery",
                "uartless-prepare",
                "--project",
                str(paths[1]),
                *common,
                "--plan-only",
            ],
            2,
        )
        self.assertFalse(blocked["ok"])
        plan = self.cli(
            [
                "stock-recovery",
                "uartless-reuse",
                "--project",
                str(paths[0]),
                *common,
                "--plan-only",
            ]
        )
        self.assertEqual(paths[0].read_bytes(), legacy_bytes)
        self.assertTrue((self.card / state.PASSIVE).exists())
        archive = Path(
            projects.default_selections(paths[0])[reuse.OPERATION]["output-dir"]
        )
        flags = plan["result"]["required_confirmations"]
        result = self.cli(
            [
                "stock-recovery",
                "uartless-reuse",
                "--project",
                str(paths[0]),
                *common,
                "--confirm-plan",
                flags["plan_sha256"],
                "--confirm-physical-device",
                flags["physical_device"],
                "--confirm-target",
                flags["target"],
                "--confirm-write-set",
                flags["write_set"],
                "--confirm-output-dir",
                str(archive),
            ]
        )
        self.assertFalse(result["result"]["capture_archive_validated_as_recovery"])
        self.assertEqual(state.capture_inventory(archive / "files"), original)
        self.assertEqual(
            set(p.name for p in self.card.iterdir()), {"recording.mp4", "STOCKM3.BIN"}
        )
        prepare = self.cli(
            [
                "stock-recovery",
                "uartless-prepare",
                "--project",
                str(paths[1]),
                *common,
                "--plan-only",
            ]
        )
        flags = prepare["result"]["required_confirmations"]
        self.cli(
            [
                "stock-recovery",
                "uartless-prepare",
                "--project",
                str(paths[1]),
                *common,
                "--confirm-plan",
                flags["plan_sha256"],
                "--confirm-physical-device",
                flags["physical_device"],
                "--confirm-target",
                flags["target"],
                "--confirm-write-set",
                flags["write_set"],
            ]
        )
        self.assertEqual((self.card / state.PASSIVE).read_bytes(), self.package)
        self.cli(
            [
                "stock-recovery",
                "uartless-reuse",
                "--project",
                str(paths[0]),
                *common,
                "--resume",
                "--plan-only",
            ],
            2,
        )
        self.assertEqual((self.card / state.PASSIVE).read_bytes(), self.package)
        self.assertEqual(projects.fingerprint(firmware), firmware_before)
        a_after, b_after = (projects.load_project(path) for path in paths)
        self.assertEqual(a_after.records[reuse.OPERATION]["state"], "completed")
        self.assertNotIn(reuse.OPERATION, b_after.records)
        self.assertEqual(
            a_after.selections["universal stage"]["install-set-dir"],
            b_after.selections["universal stage"]["install-set-dir"],
        )
        self.assertEqual(
            a_after.selections["universal provision"]["universal-public-key"],
            str(public_key),
        )
        self.assertEqual(
            b_after.selections["universal provision"]["universal-public-key"],
            str(public_key),
        )

    def test_plan_and_low_level_stage_block_every_capture_remnant(self):
        for name in state.ROOT_NAMES:
            path = self.card / name
            if name == state.CAPTURE_ROOT:
                path.mkdir()
            else:
                path.write_bytes(b"old")
            with self.subTest(name=name), self.assertRaises(ValueError):
                recovery.plan_capture(self.capture, "uartless-prepare")
            with self.assertRaises(ValueError):
                media.stage_passive_verified_package(
                    self.package,
                    root=self.card,
                    preflight=reuse._media(self.request),
                    confirmed_physical_device="/dev/sdz",
                )
            path.rmdir() if path.is_dir() else path.unlink()

    def test_authorize_plan_checks_package_and_new_collector_but_handoff_allows_collector(
        self,
    ):
        for raw in (None, b"wrong"):
            if raw is not None:
                (self.card / state.PASSIVE).write_bytes(raw)
            with self.assertRaises((ValueError, OSError)):
                recovery.plan_capture(self.capture, "uartless-authorize")
        (self.card / state.PASSIVE).write_bytes(self.package)
        recovery.plan_capture(self.capture, "uartless-authorize")
        (self.card / state.CAPTURE_ROOT).mkdir()
        with self.assertRaises(ValueError):
            recovery.plan_capture(self.capture, "uartless-authorize")
        with self.assertRaises(ValueError):
            media.activate_passive_verified_package(
                self.package,
                root=self.card,
                preflight=reuse._media(self.request),
                confirmed_physical_device="/dev/sdz",
            )
        with self.assertRaises(ValueError):
            recovery.plan_capture(self.capture, "uartless-handoff")
        (self.card / state.PASSIVE).rename(self.card / state.ACTIVE)
        recovery.plan_capture(self.capture, "uartless-handoff")
        (self.card / state.ACTIVE).write_bytes(b"wrong")
        with self.assertRaises(ValueError):
            recovery.plan_capture(self.capture, "uartless-handoff")

    def test_changed_plan_inventory_media_and_extra_file_reject_before_copy(self):
        self.seed()
        confirmed = self.confirmation()
        for change, undo in (
            (
                lambda: self.preflight.update(media_uuid="other"),
                lambda: self.preflight.update(media_uuid="synthetic-card-a"),
            ),
            (
                lambda: (self.card / "new-unknown.txt").touch(),
                lambda: (self.card / "new-unknown.txt").unlink(),
            ),
            (
                lambda: (self.card / state.PASSIVE).write_bytes(b"changed"),
                lambda: (self.card / state.PASSIVE).write_bytes(self.package),
            ),
        ):
            change()
            with self.assertRaises(ValueError):
                self.execute(confirmation=confirmed)
            self.assertFalse(self.output.exists())
            undo()

    def test_unknown_collector_file_active_updater_and_rollback_are_not_removed(self):
        self.seed()
        for name in (
            state.CAPTURE_ROOT + "/unexpected.bin",
            state.ACTIVE,
            *state.ROLLBACK_NAMES,
        ):
            path = self.card / name
            path.write_bytes(b"retain")
            with self.assertRaises(ValueError):
                self.confirmation()
            self.assertEqual(path.read_bytes(), b"retain")
            self.assertFalse(self.output.exists())
            path.unlink()

    def test_symlink_special_file_and_destination_alias_reject(self):
        outside = self.root / "outside"
        outside.write_bytes(b"do-not-touch")
        path = self.card / state.PASSIVE
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.confirmation()
        path.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(path)
            with mock.patch.object(
                state.os,
                "open",
                side_effect=AssertionError("special file must not open"),
            ):
                with self.assertRaises(ValueError):
                    self.confirmation()
            path.unlink()
        self.seed()
        self.output.symlink_to(self.card, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.confirmation()
        self.output.unlink()
        with self.assertRaises(ValueError):
            self.confirmation(replace(self.request, output_dir=self.card / "archive"))
        with (
            mock.patch.object(reuse, "_same_filesystem", return_value=True),
            self.assertRaises(ValueError),
        ):
            self.confirmation()
        self.assertEqual(outside.read_bytes(), b"do-not-touch")

    def test_copy_failure_keeps_all_sources_and_requires_new_destination_before_receipt(
        self,
    ):
        self.seed()
        original = state.capture_inventory(self.card)
        real = reuse._copy_file
        count = 0

        def fail(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic write failure")
            real(*args)

        with (
            mock.patch.object(reuse, "_copy_file", side_effect=fail),
            self.assertRaises(OSError),
        ):
            self.execute()
        self.assertEqual(state.capture_inventory(self.card), original)
        self.assertFalse((self.output / reuse.RECEIPT).exists())
        with self.assertRaises(ValueError):
            self.confirmation(replace(self.request, resume=True))
        with self.assertRaises(ValueError):
            self.confirmation()
        self.execute(replace(self.request, output_dir=self.root / "second-archive"))

    def test_all_copies_compared_before_any_source_removal(self):
        self.seed()
        original = state.capture_inventory(self.card)

        def corrupt(event):
            if event.phase == "verifying-all-copies":
                (self.output / "files" / state.PASSIVE).write_bytes(b"bad-copy")

        with self.assertRaises(ValueError):
            self.execute(emit=corrupt)
        self.assertEqual(state.capture_inventory(self.card), original)

    def test_copy_or_media_change_after_copy_retains_every_source(self):
        self.seed()
        original = state.capture_inventory(self.card)

        def changed(event):
            if event.phase == "verifying-all-copies":
                self.preflight["media_uuid"] = "other-card"

        with self.assertRaises(ValueError):
            self.execute(emit=changed)
        self.assertEqual(state.capture_inventory(self.card), original)

    def test_interrupted_removal_resumes_only_exact_verified_backup(self):
        self.seed()
        original = state.capture_inventory(self.card)
        real = Path.unlink
        count = 0

        def interrupt(path, *args, **kwargs):
            nonlocal count
            if path.is_relative_to(self.card):
                self.assertEqual(
                    state.capture_inventory(self.output / "files"), original
                )
                count += 1
                if count == 2:
                    raise KeyboardInterrupt()
            return real(path, *args, **kwargs)

        with (
            mock.patch.object(Path, "unlink", interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.execute()
        self.assertLess(len(state.capture_inventory(self.card)), len(original))
        resume = replace(self.request, resume=True)
        plan = self.confirmation(resume)
        result = self.execute(resume, plan)
        self.assertTrue(result["ok"])
        self.assertEqual(state.capture_inventory(self.output / "files"), original)
        self.assertTrue(self.execute(resume)["ok"])

    def test_resume_receipt_cannot_name_arbitrary_paths_or_changed_archive(self):
        self.seed()

        def stop(event):
            if event.phase == "removing-verified-sd-copies":
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.execute(emit=stop)
        receipt_path = self.output / reuse.RECEIPT
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw)
        receipt["inventory"]["../../outside"] = {
            "kind": "file",
            "size": 0,
            "sha256": "a" * 64,
        }
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):
            self.confirmation(replace(self.request, resume=True))
        receipt_path.write_bytes(raw)
        archived = self.output / "files" / state.PASSIVE
        archived.unlink()
        archived.symlink_to(self.card / state.PASSIVE)
        with self.assertRaises(ValueError):
            self.confirmation(replace(self.request, resume=True))
        self.assertTrue((self.card / state.PASSIVE).exists())

    def test_source_or_backup_symlink_swap_before_cleanup_is_rejected(self):
        self.seed()

        def swap(event):
            if event.phase == "removing-verified-sd-copies":
                archived = self.output / "files" / state.PASSIVE
                archived.unlink()
                archived.symlink_to(self.card / state.PASSIVE)

        original = state.capture_inventory(self.card)
        with self.assertRaises(ValueError):
            self.execute(emit=swap)
        self.assertEqual(state.capture_inventory(self.card), original)

    def test_source_symlink_swap_and_new_collector_file_after_copy_stop_removal(self):
        self.seed()

        def swap(event):
            if event.phase == "removing-verified-sd-copies":
                source = self.card / state.PASSIVE
                source.unlink()
                source.symlink_to(self.package_path)

        with self.assertRaises(ValueError):
            self.execute(emit=swap)
        self.assertTrue((self.card / state.PASSIVE).is_symlink())
        self.assertEqual(self.package_path.read_bytes(), self.package)
        self.assertEqual(
            (self.output / "files" / state.PASSIVE).read_bytes(), self.package
        )

    def test_new_capture_file_after_copy_is_retained_and_blocks_cleanup(self):
        self.seed()
        original = state.capture_inventory(self.card)

        def add(event):
            if event.phase == "verifying-all-copies":
                (self.card / state.CAPTURE_ROOT / "unknown.bin").write_bytes(b"new")

        with self.assertRaises(ValueError):
            self.execute(emit=add)
        for name, entry in original.items():
            if entry["kind"] == "file":
                self.assertEqual(state.file_identity(self.card, name), entry)

    def test_missing_uuid_unsupported_host_and_parent_symlink_fail_before_copy(self):
        self.seed()
        self.preflight["media_uuid"] = ""
        with self.assertRaisesRegex(ValueError, "UUID"):
            self.confirmation()
        self.preflight["media_uuid"] = "synthetic-card-a"
        with (
            mock.patch.object(reuse.sys, "platform", "win32"),
            self.assertRaisesRegex(ValueError, "macOS or Linux"),
        ):
            self.confirmation()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.confirmation(replace(self.request, output_dir=alias / "other-archive"))
        self.assertFalse(self.output.exists())

    def test_collector_contract_paths_and_partial_empty_directories_are_supported(self):
        source = (
            Path(__file__).resolve().parents[1] / "installer/collector/init.c"
        ).read_text()
        for name in (
            "libimp.so",
            "libalog.so",
            "libsysutils.so",
            "libaudioProcess.so",
            "tx-isp-t31.ko",
            "sensor_os02g10_t31.ko",
            "os02g10-t31.bin",
        ):
            self.assertIn('VENDOR_FILES "/' + name + '"', source)
            self.assertIn("DCS6100F/vendor/files/" + name, state.FILE_LIMITS)
        for directory in sorted(state.DIRECTORIES):
            (self.card / directory).mkdir()
        self.execute()
        self.assertFalse((self.card / state.CAPTURE_ROOT).exists())
        for directory in state.DIRECTORIES:
            self.assertTrue((self.output / "files" / directory).is_dir())

    def test_headless_modules_and_noninteractive_confirmation_gate(self):
        for module in (reuse, state):
            for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, {"argparse", "input", "print"})
        self.seed()
        self.cli(
            [
                "stock-recovery",
                "uartless-reuse",
                "--mount-root",
                str(self.card),
                "--whole-device",
                "/dev/sdz",
                "--output-dir",
                str(self.output),
            ],
            2,
        )
        self.assertFalse(self.output.exists())

    def test_interrupted_pending_marker_blocks_new_capture_and_can_resume(self):
        self.seed()
        original = state.capture_inventory(self.card)
        real = reuse._write_marker

        def interrupt(path, token):
            if path == self.card / state.PENDING:
                path.write_bytes(token[:8])
                raise KeyboardInterrupt()
            real(path, token)

        with (
            mock.patch.object(reuse, "_write_marker", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.execute()
        self.assertEqual(state.capture_inventory(self.card), original)
        with self.assertRaises(ValueError):
            recovery.plan_capture(self.capture, "uartless-prepare")
        self.execute(replace(self.request, resume=True))
        self.assertFalse((self.card / state.PENDING).exists())

    def test_host_completion_is_synced_again_before_marker_only_resume_removal(self):
        self.seed()
        real_unlink = Path.unlink

        def stop(path, *args, **kwargs):
            if path == self.card / state.PENDING:
                self.assertTrue((self.output / reuse.COMPLETE).is_file())
                raise KeyboardInterrupt()
            return real_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(Path, "unlink", stop),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.execute()
        self.assertFalse(state.capture_inventory(self.card))
        with self.assertRaises(ValueError):
            recovery.plan_capture(self.capture, "uartless-prepare")
        synced = []
        real_sync = reuse.os.fsync
        real_write = reuse._write_marker

        def write(path, token):
            before = len(synced)
            real_write(path, token)
            if path == self.output / reuse.COMPLETE:
                self.assertGreater(len(synced), before)

        def sync(fd):
            synced.append(fd)
            real_sync(fd)

        with (
            mock.patch.object(reuse.os, "fsync", sync),
            mock.patch.object(reuse, "_write_marker", write),
        ):
            result = self.execute(replace(self.request, resume=True))
        self.assertTrue(result["result"]["sd_modified"])
        self.assertFalse((self.card / state.PENDING).exists())
        self.assertFalse(
            self.execute(replace(self.request, resume=True))["result"]["sd_modified"]
        )

    def test_existing_project_failed_copy_can_choose_new_confirmed_destination(self):
        self.seed()
        path = self.root / "old-camera-a.json"
        selected = projects.default_selections(path)
        del selected[reuse.OPERATION]
        projects.init_project(path, name="old camera A", selections=selected)
        before = path.read_bytes()
        base = [
            "stock-recovery",
            "uartless-reuse",
            "--project",
            str(path),
            "--whole-device",
            "/dev/sdz",
            "--mount-root",
            str(self.card),
        ]
        plan = self.cli([*base, "--plan-only"])
        self.assertEqual(path.read_bytes(), before)
        old_output = Path(plan["result"]["required_output_dir_confirmation"])

        def confirmations(document, output):
            values = document["result"]["required_confirmations"]
            return [
                "--confirm-output-dir",
                str(output),
                "--confirm-plan",
                values["plan_sha256"],
                "--confirm-physical-device",
                values["physical_device"],
                "--confirm-target",
                values["target"],
                "--confirm-write-set",
                values["write_set"],
            ]

        with mock.patch.object(
            reuse, "_copy_file", side_effect=OSError("fixture copy failure")
        ):
            self.cli([*base, *confirmations(plan, old_output)], 2)
        self.assertTrue(old_output.is_dir())
        new_output = self.root / "explicit-new-archive"
        plan = self.cli([*base, "--output-dir", str(new_output), "--plan-only"])
        self.cli(
            [*base, "--output-dir", str(new_output), *confirmations(plan, new_output)]
        )
        self.assertTrue(old_output.is_dir())
        self.assertTrue((new_output / reuse.COMPLETE).is_file())
        self.assertEqual(
            projects.load_project(path).selections[reuse.OPERATION]["output-dir"],
            str(new_output),
        )

    def test_private_permissions_exist_before_first_capture_byte_is_copied(self):
        self.seed()
        real = reuse._copy_file

        def inspect(source, target, relative, expected):
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            real(source, target, relative, expected)
            self.assertEqual((target / relative).stat().st_mode & 0o777, 0o600)

        with mock.patch.object(reuse, "_copy_file", side_effect=inspect):
            self.execute()


if __name__ == "__main__":
    unittest.main()
