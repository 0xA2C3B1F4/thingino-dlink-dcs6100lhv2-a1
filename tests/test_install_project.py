import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import install_project as project
from installer import user_cli, user_cli_project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "camera.json"

    def create(self, selections=None):
        return project.init_project(self.path, name="Camera A", selections=selections or {})

    def test_private_roundtrip_and_no_replacement(self):
        self.create()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(project.load_project(self.path).name, "Camera A")
        with self.assertRaises(project.ProjectError):
            self.create()

    def test_conflicting_explicit_environment_and_repeated_selection(self):
        with self.assertRaises(project.ProjectError):
            project.select_project([str(self.path)], str(self.root / "other"))
        with self.assertRaises(project.ProjectError):
            project.select_project([str(self.path), str(self.root / "other")], None)
        self.assertIsNone(project.select_project([], None))

    def test_reject_secret_device_confirmation_and_relative_paths(self):
        for key, value in (("confirm-target", "/yes"), ("whole-device", "/dev/card"),
                           ("passphrase", "/key"), ("expected-wpa-config", "/wifi"),
                           ("session-dir", "relative")):
            with self.subTest(key=key), self.assertRaises(project.ProjectError):
                self.create({"universal stage": {key: value}})

    def test_corrupt_missing_and_public_project_rejected(self):
        with self.assertRaises(project.ProjectError):
            project.load_project(self.path)
        self.path.write_text("invalid")
        self.path.chmod(0o600)
        with self.assertRaises(project.ProjectError):
            project.load_project(self.path)
        self.path.write_text("{}")
        self.path.chmod(0o644)
        with self.assertRaises(project.ProjectError):
            project.load_project(self.path)

    def test_symlink_rejected(self):
        self.create()
        link = self.root / "link"
        link.symlink_to(self.path)
        with self.assertRaises(project.ProjectError):
            project.load_project(link)

    def test_selected_path_conflict(self):
        p = self.create({"local-build status": {"build-root": str(self.root)}})
        with self.assertRaises(project.ProjectError):
            project.selected_paths(p, "local-build status", {"build-root": "/other"})

    def test_changed_missing_output_invalidates_completion(self):
        source, output = self.root / "source", self.root / "output"
        source.write_bytes(b"input")
        output.write_bytes(b"accepted")
        paths = {"package": str(source), "output": str(output)}
        p = self.create({"stock-recovery uartless-validate": paths})
        p.records["stock-recovery uartless-validate"] = {
            "state": "completed", "inputs": project.snapshot(paths),
            "outputs": project.snapshot(paths, outputs=True),
        }
        self.assertEqual(project.project_status(p)["records"]["stock-recovery uartless-validate"]["state"], "completed")
        output.write_bytes(b"changed")
        self.assertEqual(project.project_status(p)["records"]["stock-recovery uartless-validate"]["state"], "needs-review")
        output.unlink()
        self.assertFalse(project.project_status(p)["write_authorized"])
        self.assertFalse(project.project_status(p)["physical_boot_verified"])

    def test_missing_recorded_artifact_always_invalidates_completion(self):
        artifact = self.root / "accepted"
        artifact.write_bytes(b"accepted")
        p = self.create()
        record = {"state": "completed", "inputs": {}, "outputs": {},
                  "artifacts": {"artifact": {"path": str(artifact), "sha256": project.fingerprint(artifact)}}}
        p.records["universal authorize"] = record
        self.assertEqual(project.project_status(p)["records"]["universal authorize"]["state"], "completed")
        artifact.unlink()
        self.assertEqual(project.project_status(p)["records"]["universal authorize"]["state"], "needs-review")
        for malformed in ([], {"artifact": None}, {"artifact": {"path": "relative", "sha256": "bad"}}):
            record["artifacts"] = malformed
            self.assertEqual(project.project_status(p)["records"]["universal authorize"]["state"], "needs-review")
            project.save_project(p)
            with self.assertRaises(project.ProjectError):
                project.load_project(self.path)

    def test_interrupted_operation_is_not_completed(self):
        self.create({"local-build acquire": {"build-root": str(self.root)}})
        args = user_cli.build_parser().parse_args(["--project", str(self.path), "local-build", "acquire"])
        active = user_cli_project.begin(args)
        user_cli_project.finish(active, {"ok": False})
        self.assertEqual(project.load_project(self.path).records["local-build acquire"]["state"], "started")

    def test_plan_only_does_not_change_project(self):
        self.create()
        original = self.path.read_bytes()
        args = mock.Mock(project=[str(self.path)], command="universal", plan_only=True)
        self.assertIsNone(user_cli_project.begin(args))
        self.assertEqual(self.path.read_bytes(), original)

    def test_long_and_short_parse_the_same_paths(self):
        self.create({"local-build status": {"build-root": str(self.root)}})
        short = ["--project", str(self.path), "local-build", "status"]
        parser = user_cli.build_parser()
        a = parser.parse_args(user_cli_project.expand(short))
        b = parser.parse_args(short + ["--build-root", str(self.root),
                                       "--work-dir", str(self.root / "camera.work")])
        self.assertEqual(vars(a), vars(b))

    def test_project_init_label_cannot_select_another_command(self):
        raw = ["project", "init", "--project", str(self.path), "--name", "universal", "--paths", "/paths"]
        self.assertEqual(user_cli_project.expand(raw), raw)

    def test_noninteractive_missing_input_is_json_and_never_prompts(self):
        output = io.StringIO()
        with mock.patch("builtins.input", side_effect=AssertionError("prompt")), contextlib.redirect_stdout(output):
            self.assertEqual(user_cli.main(["--non-interactive", "local-build", "configure"]), 2)
        document = json.loads(output.getvalue())
        self.assertEqual(document["error_code"], "missing_input")
        self.assertIn("--secrets-fd", document["missing_inputs"])

    def test_events_contain_only_static_phase_and_command(self):
        output = io.StringIO()
        args = mock.Mock(events_jsonl=True, command="universal", password="never emit")
        with contextlib.redirect_stderr(output):
            user_cli_project.event(args, "validating")
        self.assertEqual(json.loads(output.getvalue()), {
            "schema_version": 1, "event": "validating", "command": "universal"})

    def test_success_links_verified_output_and_change_invalidates_record(self):
        p = self.create()
        output = self.root / "authorization"
        output.mkdir()
        artifact = output / "manifest"
        artifact.write_bytes(b"synthetic accepted authorization")
        user_cli_project.finish((p, "universal authorize", {}), {
            "ok": True, "result": {"authorization_dir": str(output)}})
        loaded = project.load_project(self.path)
        self.assertEqual(loaded.selections["universal stage"]["authorization-dir"], str(output))
        self.assertEqual(project.project_status(loaded)["records"]["universal authorize"]["state"], "completed")
        artifact.write_bytes(b"changed")
        self.assertEqual(project.project_status(loaded)["records"]["universal authorize"]["state"], "needs-review")

    def test_init_build_root_supports_short_command(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(user_cli.main(["project", "init", "--project", str(self.path),
                "--name", "camera-a", "--build-root", str(self.root / "build")]), 0)
        args = user_cli.build_parser().parse_args(user_cli_project.expand([
            "--project", str(self.path), "local-build", "status"]))
        self.assertEqual(args.build_root, self.root / "build")

    def test_structural_missing_options_and_help(self):
        parser = user_cli.build_parser()
        missing = user_cli_project.missing_options(parser, ["universal", "provision"])
        self.assertIn("--universal-bundle", missing)
        self.assertIn("--recovery-dir | --functional-recovery-dir", missing)
        for command, facts in ((["local-build", "status"], ("without building", "prerequisites")),
                               (["universal", "stage"], ("--plan-only", "SD files", "physical boot", "Interactive:")),
                               (["stock-recovery", "uartless-authorize"], ("WRITE-MTD1-MTD2", "physical boot", "uartless-handoff")),
                               (["project", "init"], ("--build-root", "--project", "Next:"))):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                parser.parse_args(command + ["--help"])
            self.assertEqual(exit.exception.code, 0)
            rendered = " ".join(output.getvalue().split())
            for fact in facts:
                self.assertIn(fact, rendered)
        self.assertIn("universal configure", " ".join(parser.format_help().split()))

    def test_init_conflicting_build_workspace_does_not_create_project(self):
        paths = self.root / "paths.json"
        paths.write_text(json.dumps({"local-build prepare": {"build-root": str(self.root / "other")}}))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.dict("os.environ", {}, clear=True):
            code = user_cli.main(["project", "init", "--project", str(self.path), "--name", "camera-a",
                                  "--build-root", str(self.root / "build"), "--paths", str(paths), "--json"])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "project_conflict")
        self.assertFalse(self.path.exists())

    def test_different_camera_recovery_selections_rejected(self):
        a, b, readback = self.root / "a", self.root / "b", self.root / "readback"
        for path in (a, b, readback):
            path.mkdir()
        p = self.create({"universal stage": {"recovery-dir": str(a), "preserved-readback-dir": str(readback)},
                         "universal authorize": {"recovery-dir": str(b), "preserved-readback-dir": str(readback)}})
        with mock.patch("installer.recovery_gate.validate_existing_recovery_boundary",
                        side_effect=[mock.Mock(camera_identity_sha256="a"), mock.Mock(camera_identity_sha256="b")]):
            with self.assertRaises(project.ProjectError):
                project.validate_camera_selection(p)


if __name__ == "__main__":
    unittest.main()
