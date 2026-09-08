import ast
from dataclasses import replace
from pathlib import Path
import unittest
import tempfile
from unittest import mock

from installer import install_actions as actions
from installer.install_project import ProjectError
from installer.media_preflight import MediaPreflight


class ActionTests(unittest.TestCase):
    def test_unrelated_large_recordings_and_many_files_are_not_hashed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            before = actions.reserved_media_identity(root)
            recording = root / "recording.mp4"
            with recording.open("wb") as stream:
                stream.truncate(257 * 1024 * 1024)
            for index in range(4100):
                (root / f"recording-{index}").touch()
            with mock.patch.object(actions, "fingerprint", side_effect=AssertionError("unrelated content read")):
                self.assertEqual(actions.reserved_media_identity(root), before)
            self.assertEqual(recording.stat().st_size, 257 * 1024 * 1024)
            (root / actions.STAGE2_FILENAME).write_bytes(b"reserved")
            self.assertNotEqual(actions.reserved_media_identity(root), before)

    def setUp(self):
        self.inputs = actions.UniversalStageInputs(
            *[Path("/synthetic") for _ in range(9)], whole_device="/dev/card",
            functional_recovery_dir=Path("/synthetic/recovery"))
        self.plan = actions.WritePlan("camera-a", "functional", MediaPreflight(
            "/dev/card", "fixture", 1024, "fat32", Path("/synthetic"), 12, 34),
            (("firmware", "a" * 64),), ("SD staging",))
        self.confirmation = actions.WriteConfirmation(self.plan.identity, "/dev/card",
            "DCS-6100LHV2-A1", "STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3")

    def test_headless_core_has_no_parser_or_terminal_calls(self):
        from installer import camera_setup, install_project, install_results, recovery_actions, media_selection
        for module in (actions, camera_setup, install_project, install_results, recovery_actions, media_selection):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, {"argparse", "print", "input", "facade"}, module.__name__)

    def test_revalidates_then_uses_existing_writer_and_static_events(self):
        validated = object()
        events = []
        with (mock.patch.object(actions, "plan_universal_stage", return_value=(self.plan, validated)) as plan,
              mock.patch.object(actions, "stage_camera_bound_universal_install", return_value={"file": "digest"}) as write):
            result = actions.execute_universal_stage(self.inputs, self.confirmation, emit=events.append)
        plan.assert_called_once_with(self.inputs)
        write.assert_called_once_with(validated, root=self.inputs.mount_root, preflight=self.plan.media,
                                      confirmed_physical_device="/dev/card")
        self.assertEqual(result, {"file": "digest"})
        self.assertEqual(events, ["validating", "staging", "readback-completed"])

    def test_camera_artifact_and_media_changes_reject_old_confirmation(self):
        for plan in (replace(self.plan, camera="camera-b"),
                     replace(self.plan, artifacts=(("firmware", "b" * 64),)),
                     replace(self.plan, media=replace(self.plan.media, mount_inode=35)),
                     replace(self.plan, recovery_class="exact-original")):
            with (self.subTest(plan=plan), mock.patch.object(actions, "plan_universal_stage", return_value=(plan, object())),
                  mock.patch.object(actions, "stage_camera_bound_universal_install") as write):
                with self.assertRaises(ProjectError) as error:
                    actions.execute_universal_stage(self.inputs, self.confirmation)
                self.assertEqual(error.exception.code, "stale_plan")
                write.assert_not_called()

    def test_exact_confirmations_required(self):
        for confirmation in (replace(self.confirmation, physical_device="/other"),
                             replace(self.confirmation, target="yes"),
                             replace(self.confirmation, write_set="true")):
            with (mock.patch.object(actions, "plan_universal_stage", return_value=(self.plan, object())),
                  mock.patch.object(actions, "stage_camera_bound_universal_install") as write):
                with self.assertRaises(ProjectError):
                    actions.execute_universal_stage(self.inputs, confirmation)
                write.assert_not_called()

    def test_validation_failure_never_writes(self):
        with (mock.patch.object(actions, "plan_universal_stage", side_effect=ValueError("fixture rejection")),
              mock.patch.object(actions, "stage_camera_bound_universal_install") as write):
            with self.assertRaises(ValueError):
                actions.execute_universal_stage(self.inputs, self.confirmation)
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
