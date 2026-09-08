from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
import shutil

from installer import user_cli, install_project, recovery_actions, install_actions
from installer.sd_package import generate_bootstrap, parse_package, package_manifest
from installer.media_contracts import UARTLESS_CAPTURE_ACTIVE_FILENAME, UARTLESS_CAPTURE_PASSIVE_FILENAME
from installer.media_preflight import MediaPreflight
from installer import user_cli_media


class InstallationFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.package = self.root / "capture.bin"
        self.raw = generate_bootstrap(b"synthetic kernel", b"synthetic rootfs")
        self.package.write_bytes(self.raw)
        self.manifest = self.root / "capture.json"
        parsed = parse_package(self.raw, require_project_header=True)
        manifest = json.loads(package_manifest(parsed, purpose="uartless-functional-capture"))
        manifest.update(future_physical_boot_writes_mtd=[1, 2], original_complete_backup=False,
                        original_preserved_mtd=[0, 3, 4, 5], restoration_class="recovery-functional")
        self.manifest.write_text(json.dumps(manifest))
        self.model = "synthetic removable card"
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict("os.environ", {"DCS6100_PROJECT": ""}).start()
        mock.patch("installer.recovery_actions.create_preflight_document", side_effect=self.preflight).start()

    def preflight(self, *, whole_device, mount_root):
        return {"schema_version": 1, "external": True, "physical": True, "writable": True,
                "system_device": False, "ambiguous": False, "filesystem": "fat32",
                "physical_device": whole_device, "model": self.model,
                "capacity_bytes": 1024 * 1024 * 1024, "mount_root": str(mount_root)}

    def cli(self, args, expected=0):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            code = user_cli.main(args)
        result = json.loads(output.getvalue())
        self.assertEqual(code, expected, result)
        return result

    def confirmations(self, plan, operation):
        values = plan["result"]["required_confirmations"]
        return ["--confirm-plan", values["plan_sha256"], "--confirm-physical-device", values["physical_device"],
                "--confirm-target", values["target"],
                "--confirm-stock-uboot-result" if operation.endswith("handoff") else "--confirm-write-set",
                values["write_set"]]

    def test_short_and_long_capture_chain_use_real_same_validators_and_writers(self):
        outcomes = []
        for short in (False, True):
            card = self.root / ("short-card" if short else "long-card")
            card.mkdir()
            recording = card / "recording.mp4"
            recording.write_bytes(b"keep this unrelated recording")
            project = self.root / "camera.json"
            if short:
                install_project.init_project(project, name="fixture", selections={})
                install_project.attach_inputs(install_project.load_project(project),
                    {"package": self.package, "package-manifest": self.manifest})
            options = (["--project", str(project)] if short else
                       ["--package", str(self.package), "--package-manifest", str(self.manifest)])
            phases = []
            for operation in ("uartless-prepare", "uartless-authorize", "uartless-handoff"):
                args = ["stock-recovery", operation, "--non-interactive", "--whole-device", "/dev/fixture",
                        "--mount-root", str(card), *options]
                plan = self.cli([*args, "--plan-only"])
                result = self.cli([*args, *self.confirmations(plan, operation)])
                inspected = self.cli([*args, "--plan-only"])["result"]
                self.assertNotIn("armed", inspected)
                self.assertFalse(inspected["writes_performed"])
                phases.append(result["phase"])
                self.assertEqual((card / (UARTLESS_CAPTURE_ACTIVE_FILENAME if operation == "uartless-authorize"
                                        else UARTLESS_CAPTURE_PASSIVE_FILENAME)).read_bytes(), self.raw)
            self.assertEqual(recording.read_bytes(), b"keep this unrelated recording")
            outcomes.append((phases, sorted(p.name for p in card.iterdir())))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_real_planner_rejects_changed_media_and_corrupt_package_without_writing(self):
        card = self.root / "card"
        card.mkdir()
        inputs = recovery_actions.CaptureMediaInputs(self.package, self.manifest, card, "/dev/fixture")
        plan = recovery_actions.plan_capture(inputs, "uartless-prepare")
        confirmation = install_actions.WriteConfirmation(**plan.required_confirmations())
        self.model = "replacement card"
        with self.assertRaises(install_project.ProjectError) as error:
            recovery_actions.execute_capture(inputs, "uartless-prepare", confirmation)
        self.assertEqual(error.exception.code, "stale_plan")
        self.assertEqual(list(card.iterdir()), [])
        self.model = "synthetic removable card"
        self.package.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            recovery_actions.execute_capture(inputs, "uartless-prepare", confirmation)
        self.assertEqual(list(card.iterdir()), [])

    def test_one_card_still_requires_explicit_interactive_selection(self):
        choice = MediaPreflight("/dev/fixture", "fixture", 1024, "fat32", self.root)
        args = user_cli.build_parser().parse_args(["universal", "evacuate-recovery", "--output-dir", str(self.root / "out")])
        with mock.patch.object(user_cli_media, "discover_media", return_value=(choice,)), \
             mock.patch("builtins.input", return_value="1") as prompt, contextlib.redirect_stdout(io.StringIO()):
            user_cli_media.select_media(args)
        prompt.assert_called_once()
        self.assertEqual(args.whole_device, "/dev/fixture")

    def test_interruption_records_uncertain_outcome_and_never_replays(self):
        path = self.root / "project.json"
        install_project.init_project(path, name="fixture", selections={"local-build acquire": {"build-root": str(self.root)}})
        with mock.patch.object(user_cli, "acquire_locked_public_inputs", side_effect=KeyboardInterrupt):
            result = self.cli(["--project", str(path), "local-build", "acquire", "--non-interactive"], expected=130)
        self.assertEqual(result["operation_outcome"], "unknown-requires-inspection")
        self.assertFalse(result["automatic_retry"])
        with mock.patch.object(user_cli, "acquire_locked_public_inputs", side_effect=AssertionError("replayed")):
            status = self.cli(["project", "status", "--project", str(path), "--json"])
        self.assertEqual(status["result"]["next_action"]["safe_next_action"], "inspect-interrupted-operation")


@unittest.skipUnless(all(shutil.which(name) for name in ("ssh-keygen", "dropbearkey", "openssl")),
                     "session integration requires SSH, Dropbear and OpenSSL tools")
class SessionWorkflowTests(unittest.TestCase):
    def setUp(self):
        from installer import camera_setup
        temporary = tempfile.TemporaryDirectory(prefix="installation session ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "camera.json"
        project = install_project.init_project(self.path, name="fixture")
        self.session = Path(project.selections["universal init-session"]["output-dir"])
        self.config = Path(project.selections["universal configure"]["output-dir"])
        self.dropbearkey = Path(shutil.which("dropbearkey")).resolve()
        # Only the acquired recovery evidence boundary is synthetic. Session/key
        # generation, config validation and project bookkeeping run unchanged.
        with mock.patch("installer.recovery_gate.validate_functional_recovery_boundary",
                        return_value=SimpleNamespace(camera_identity_sha256="a" * 64)):
            active = install_project.begin_operation(project, "universal init-session",
                                                      dict(project.selections["universal init-session"]))
            result = camera_setup.initialize_session(camera_setup.SessionInputs(
                functional_recovery_dir=self.root / "acquired-recovery",
                preserved_readback_dir=self.root / "acquired-readbacks",
                output_dir=self.session, config_output_dir=self.config,
                ssh_keygen=Path(shutil.which("ssh-keygen")).resolve(), dropbearkey=self.dropbearkey))
            install_project.finish_operation(active, result)
        project = install_project.load_project(self.path)
        active = install_project.begin_operation(project, "universal configure",
                                                  dict(project.selections["universal configure"]))
        result = camera_setup.configure_camera(camera_setup.ConfigurationInputs(
            session_dir=self.session, output_dir=self.config,
            wifi=camera_setup.WifiInput("fixture wifi", "fixture-password", "fixture wifi", "fixture-password")))
        install_project.finish_operation(active, result)
        self.pin = self.session / "host/station_known_hosts"
        self.expected_pin = self.pin.read_bytes()
        self.pin.unlink()  # valid older session: key exists, derived pin absent

    def run_verify(self, mutation=None):
        from installer import camera_setup
        from installer.recovery_ap.host import load_host_session
        name = load_host_session(self.session).station_mdns_name
        project = install_project.load_project(self.path)
        active = install_project.begin_operation(project, "universal verify",
                                                  dict(project.selections["universal verify"]))
        def exchange(session, **kwargs):
            self.assertEqual(session.station_known_hosts, self.pin)
            self.assertEqual(self.pin.read_bytes(), self.expected_pin)
            self.assertTrue(kwargs["allow_uartless_station_health"])
            if mutation:
                mutation()
            return b"healthy " + name.encode() + b"\n" + kwargs["payload"] + b"b" * 64 + b"  /dev/mtd3\n"
        # These are the only mocked verify boundaries; real key inspection,
        # pin creation, health parsing and identity checks all execute.
        with mock.patch("installer.recovery_ap.host.resolve_recovery_ap_station", return_value=(name, "192.0.2.10")), \
             mock.patch("installer.recovery_ap.host._exchange", side_effect=exchange):
            result = camera_setup.verify_camera(camera_setup.VerifyInputs(session_dir=self.session, dropbearkey=self.dropbearkey))
        install_project.finish_operation(active, result)
        return result

    def test_initialize_configure_verify_allows_only_derived_pin_creation(self):
        from installer.private_config import load_private_config_for_session
        load_private_config_for_session(output_dir=self.config, session_dir=self.session)
        before = install_project.project_status(install_project.load_project(self.path))["records"]
        self.assertEqual({row["state"] for row in before.values()}, {"completed"})
        result = self.run_verify()
        self.assertTrue(result["result"]["station_host_pin_created"])
        self.assertEqual(result["result"]["health_gate"], "passed")
        after = install_project.project_status(install_project.load_project(self.path))["records"]
        self.assertEqual({row["state"] for row in after.values()}, {"completed"})
        self.assertEqual(self.pin.read_bytes(), self.expected_pin)
        self.pin.unlink()
        after = install_project.project_status(install_project.load_project(self.path))["records"]
        self.assertEqual(after["universal verify"]["state"], "needs-review")
        self.assertEqual(after["universal init-session"]["state"], "completed")
        self.assertEqual(after["universal configure"]["state"], "completed")

    def test_verify_rejects_changed_identity_and_invalidates_earlier_records(self):
        for relative in ("host/identity.pub", "host/dropbear_ed25519_host_key", "host/session.json", "unexpected-input"):
            path = self.session / relative
            original = path.read_bytes() if path.exists() else None
            changed = (original or b"") + b"changed\n"
            if relative.endswith("session.json"):
                manifest = json.loads(original)
                manifest["camera_identity_sha256"] = "b" * 64
                changed = json.dumps(manifest).encode()
            with self.subTest(input=relative), self.assertRaises(install_project.ProjectError) as error:
                self.run_verify(lambda: path.write_bytes(changed))
            self.assertEqual(error.exception.code, "changed_input")
            status = install_project.project_status(install_project.load_project(self.path))
            self.assertEqual({row["state"] for row in status["records"].values()}, {"needs-review"})
            self.assertEqual(status["next_action"]["safe_next_action"], "inspect-interrupted-operation")
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_verify_rejects_changed_derived_pin_after_health(self):
        from installer.recovery_ap.session import RecoveryApSessionError
        with self.assertRaises(RecoveryApSessionError):
            self.run_verify(lambda: self.pin.write_bytes(b" ".join(self.expected_pin.split()[:2]) + b" " + b"A" * 68 + b"\n"))

    def test_stale_session_and_config_require_inspection_before_progression(self):
        for producer, path in (("universal init-session", self.session / "host/identity.pub"),
                               ("universal configure", self.config / "wpa_supplicant.conf")):
            original = path.read_bytes()
            for deleted in (False, True):
                with self.subTest(producer=producer, deleted=deleted):
                    path.unlink() if deleted else path.write_bytes(original + b"changed")
                    status = install_project.project_status(install_project.load_project(self.path))
                    self.assertEqual(status["next_action"]["operation"], producer)
                    self.assertEqual(status["next_action"]["safe_next_action"], "inspect-stale-operation")
                    self.assertFalse(status["next_action"]["writes"])
                    self.assertEqual(status["next_action"]["command"], "project status")
                    path.write_bytes(original)
                    path.chmod(0o600)
                    restored = install_project.project_status(install_project.load_project(self.path))
                    self.assertEqual(restored["records"][producer]["state"], "completed")

    def test_explicit_reviewed_config_attachment_replaces_only_host_producer(self):
        replacement = self.root / "reviewed config"
        shutil.copytree(self.config, replacement)
        (self.config / "wpa_supplicant.conf").unlink()
        state = install_project.load_project(self.path)
        # A prior interrupted media operation must survive the host attachment.
        state.records["universal stage"] = {"state": "started", "inputs": {}}
        install_project.save_project(state)
        result = install_project.attach_inputs(state, {"private-config-dir": replacement})
        self.assertEqual(result["superseded_host_records"], ["universal configure"])
        updated = install_project.load_project(self.path)
        self.assertNotIn("universal configure", updated.records)
        self.assertIn("universal init-session", updated.records)
        self.assertEqual(updated.records["universal stage"]["state"], "started")
        self.assertEqual(result["next_action"]["safe_next_action"], "inspect-interrupted-operation")

    def test_config_attachment_does_not_dismiss_a_changed_signer(self):
        state = install_project.load_project(self.path)
        signer = Path(state.selections["universal provision"]["signing-key"])
        signer.write_bytes(signer.read_bytes() + b"changed")
        result = install_project.attach_inputs(state, {"private-config-dir": self.config})
        self.assertEqual(result["superseded_host_records"], [])
        self.assertEqual(result["next_action"]["safe_next_action"], "inspect-stale-operation")


@unittest.skipUnless(all(shutil.which(name) for name in ("openssl", "ssh-keygen", "dropbearkey")),
                     "signed workflow requires OpenSSL, SSH and Dropbear tools")
class SignedInstallationTests(unittest.TestCase):
    def setUp(self):
        import test_final_bundle
        import test_media
        import test_universal_artifacts
        from installer.final_bundle import ensure_ed25519_keypair, build_universal_final_bundle
        from installer.stage2 import build_stage2
        from installer.install_policy import universal_physical_write_policy
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.card = self.root / "card"
        self.card.mkdir()
        self.keys = ensure_ed25519_keypair(self.root / "signer" / "key.pem")
        images = test_final_bundle.FinalBundleTests()._images()
        images.pop("data_jffs2")
        bundle = build_universal_final_bundle(**images, signing_key=Path(self.keys["private_key"]))
        self.install = self.root / "install"
        self.install.mkdir()
        bootstrap = generate_bootstrap(images["kernel"], images["bootstrap_rootfs"])
        stage2 = build_stage2(final_kernel=images["kernel"], system_rootfs=images["system_rootfs"], data_mode="initialize")
        manifest = json.loads(test_media.MediaTests()._split_install_manifest(install_actions.BOOTSTRAP_FILENAME, bootstrap, stage2))
        manifest.update(artifact_scope="model-universal", provisioning="separate-per-camera-audit-and-jffs2",
                        universal_firmware_sha256=hashlib.sha256(bundle).hexdigest(), physical_write_policy=universal_physical_write_policy())
        manifest["region_policy"]["data"]["initialize"] = "camera-authorized-jffs2-erase-write-readback"
        manifest["artifacts"]["thingino-universal.tgb"] = {"sha256": hashlib.sha256(bundle).hexdigest(), "size": len(bundle)}
        files = {install_actions.BOOTSTRAP_FILENAME: bootstrap, install_actions.STAGE2_FILENAME: stage2,
                 "install-set.manifest.json": json.dumps(manifest).encode(),
                 "stage1-bootstrap.squashfs": images["bootstrap_rootfs"], "thingino-universal.tgb": bundle}
        for name, raw in files.items():
            (self.install / name).write_bytes(raw)
        self.recovery = test_universal_artifacts.UniversalArtifactTests._recovery("a" * 64)
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict("os.environ", {"DCS6100_PROJECT": ""}).start()
        mock.patch("installer.camera_setup.validate_recovery", side_effect=lambda _: self.recovery).start()
        mock.patch("installer.recovery_gate.validate_functional_recovery_boundary", side_effect=lambda **_: self.recovery).start()
        mock.patch("installer.install_actions.validate_functional_recovery_boundary", side_effect=lambda **_: self.recovery).start()
        mock.patch("installer.install_project.validate_camera_selection").start()
        mock.patch("installer.provisioning.build_provisioning_data_image", side_effect=self.data_image).start()
        mock.patch("installer.camera_setup.resolve_tool", return_value=self.root / "unused-tool").start()
        mock.patch("installer.install_actions.create_preflight_document", side_effect=self.preflight).start()

    def data_image(self, **kwargs):
        from installer.provisioning_data import ProvisioningDataImage
        from installer.mtd3_split import DATA_FLASH_SPAN
        raw = b"\x85\x19" + b"x" * (DATA_FLASH_SPAN - 2)
        kwargs["output_path"].write_bytes(raw)
        kwargs["output_path"].chmod(0o600)
        return ProvisioningDataImage(raw, {"state": "committed"})

    def preflight(self, *, whole_device, mount_root):
        return {"schema_version": 1, "external": True, "physical": True, "writable": True,
                "system_device": False, "ambiguous": False, "filesystem": "fat32",
                "physical_device": whole_device, "model": "fixture", "capacity_bytes": 1024 ** 3,
                "mount_root": str(mount_root)}

    def cli(self, arguments, expected=0, *, secrets_path=None):
        output = io.StringIO()
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(output), mock.patch("builtins.input", side_effect=AssertionError("prompt")):
            if secrets_path is not None:
                source = stack.enter_context(secrets_path.open("rb"))
                arguments = [*arguments, "--secrets-fd", str(source.fileno())]
            code = user_cli.main(arguments)
        result = json.loads(output.getvalue())
        self.assertEqual(code, expected, result)
        return result

    def prepared_inputs(self):
        from installer.camera_setup import (ProvisionInputs, AuthorizationInputs, provision_camera, authorize_camera,
            SessionInputs, ConfigurationInputs, WifiInput, initialize_session, configure_camera)
        initialize_session(SessionInputs(functional_recovery_dir=self.root / "recovery",
            preserved_readback_dir=self.root / "preserved", output_dir=self.root / "session", config_output_dir=self.root / "config",
            ssh_keygen=Path(shutil.which("ssh-keygen")).resolve(), dropbearkey=Path(shutil.which("dropbearkey")).resolve()))
        configure_camera(ConfigurationInputs(session_dir=self.root / "session", output_dir=self.root / "config",
            wifi=WifiInput("fixture wifi", "fixture-password", "fixture wifi", "fixture-password"),
            signing_key=Path(self.keys["private_key"]), signing_public_key=Path(self.keys["public_key"])))
        common = dict(functional_recovery_dir=self.root / "recovery", preserved_readback_dir=self.root / "preserved",
                      universal_bundle=self.install / "thingino-universal.tgb", universal_public_key=Path(self.keys["public_key"]),
                      session_dir=self.root / "session", signing_key=Path(self.keys["private_key"]))
        sidecar, data = self.root / "provision.zip", self.root / "data.jffs2"
        provision_camera(ProvisionInputs(**common, private_config_dir=self.root / "config", output=sidecar, data_output=data))
        authorize_camera(AuthorizationInputs(**common, provisioning=sidecar, provisioning_data=data,
            provisioning_public_key=Path(self.keys["public_key"]), output_dir=self.root / "authorization"))
        return install_actions.UniversalStageInputs(
            install_set_dir=self.install, universal_public_key=Path(self.keys["public_key"]),
            provisioning=sidecar, provisioning_data=data, authorization_dir=self.root / "authorization",
            authorization_public_key=Path(self.keys["public_key"]), session_dir=self.root / "session",
            preserved_readback_dir=self.root / "preserved", functional_recovery_dir=self.root / "recovery",
            mount_root=self.card, whole_device="/dev/fixture")

    def test_signed_tuple_rejects_other_camera_corrupt_artifact_and_replaced_mount(self):
        from dataclasses import replace
        inputs = self.prepared_inputs()
        plan, _ = install_actions.plan_universal_stage(inputs)
        confirmation = install_actions.WriteConfirmation(**plan.required_confirmations())
        with mock.patch.object(install_actions, "stage_camera_bound_universal_install") as writer:
            original = self.recovery
            self.recovery = replace(original, camera_identity_sha256="b" * 64)
            with self.assertRaises(ValueError):
                install_actions.execute_universal_stage(inputs, confirmation)
            self.recovery = original
            original_sidecar = inputs.provisioning.read_bytes()
            inputs.provisioning.write_bytes(b"corrupt signed sidecar")
            with self.assertRaises(ValueError):
                install_actions.execute_universal_stage(inputs, confirmation)
            inputs.provisioning.write_bytes(original_sidecar)
            self.card.rename(self.root / "old-card")
            self.card.mkdir()
            with self.assertRaises(install_project.ProjectError) as error:
                install_actions.execute_universal_stage(inputs, confirmation)
            self.assertEqual(error.exception.code, "stale_plan")
            writer.assert_not_called()

    def test_handoff_plan_does_not_claim_an_armed_card_is_disarmed(self):
        from dataclasses import asdict
        inputs = self.prepared_inputs()
        plan, _ = install_actions.plan_universal_stage(inputs)
        install_actions.stage_universal(inputs, install_actions.WriteConfirmation(**plan.required_confirmations()))
        self.assertTrue((self.card / install_actions.BOOTSTRAP_FILENAME).exists())
        before = install_actions.reserved_media_identity(self.card)
        args = SimpleNamespace(**asdict(inputs), json=True, non_interactive=True, plan_only=True)
        result = user_cli._universal_handoff(args)["result"]
        self.assertNotIn("armed", result)
        self.assertFalse(result["writes_performed"])
        self.assertEqual(result["write_set"], [])
        self.assertEqual(install_actions.reserved_media_identity(self.card), before)

    def test_stale_provisioning_and_media_records_require_explicit_review(self):
        from installer.install_results import document
        inputs = self.prepared_inputs()
        for path in (inputs.functional_recovery_dir, inputs.preserved_readback_dir):
            path.mkdir()
        path = self.root / "project.json"
        state = install_project.init_project(path, name="fixture", selections={})
        selected = {"functional-recovery-dir": str(inputs.functional_recovery_dir),
                    "preserved-readback-dir": str(inputs.preserved_readback_dir),
                    "session-dir": str(inputs.session_dir), "private-config-dir": str(self.root / "config"),
                    "universal-bundle": str(self.install / "thingino-universal.tgb"),
                    "universal-public-key": str(inputs.universal_public_key),
                    "signing-key": self.keys["private_key"],
                    "output": str(inputs.provisioning), "data-output": str(inputs.provisioning_data)}
        active = install_project.begin_operation(state, "universal provision", selected)
        install_project.finish_operation(active, document("universal provision", ok=True, phase="accepted",
            result={"output": str(inputs.provisioning), "provisioning_data_output": str(inputs.provisioning_data)}))
        install_project.attach_inputs(install_project.load_project(path), {
            "install-set-dir": self.install, "universal-public-key": inputs.universal_public_key,
            "authorization-dir": inputs.authorization_dir, "authorization-public-key": inputs.authorization_public_key,
            "session-dir": inputs.session_dir, "functional-recovery-dir": inputs.functional_recovery_dir,
            "preserved-readback-dir": inputs.preserved_readback_dir})
        for artifact in (inputs.provisioning, inputs.provisioning_data):
            original = artifact.read_bytes()
            for deleted in (False, True):
                with self.subTest(artifact=artifact.name, deleted=deleted):
                    artifact.unlink() if deleted else artifact.write_bytes(original + b"changed")
                    status = install_project.project_status(install_project.load_project(path))
                    self.assertEqual(status["next_action"]["operation"], "universal provision")
                    self.assertEqual(status["next_action"]["safe_next_action"], "inspect-stale-operation")
                    self.assertFalse(status["next_action"]["writes"])
                    artifact.write_bytes(original)
                    artifact.chmod(0o600)
        replacement = self.root / "reviewed provision.zip"
        replacement_data = self.root / "reviewed data.jffs2"
        shutil.copy2(inputs.provisioning, replacement)
        shutil.copy2(inputs.provisioning_data, replacement_data)
        inputs.provisioning.unlink()
        partial = install_project.attach_inputs(install_project.load_project(path), {"provisioning": replacement})
        self.assertEqual(partial["superseded_host_records"], [])
        accepted = install_project.attach_inputs(install_project.load_project(path),
            {"provisioning": replacement, "provisioning-data": replacement_data})
        self.assertEqual(accepted["superseded_host_records"], ["universal provision"])
        self.assertEqual(accepted["next_action"]["command"], "universal stage")
        self.assertEqual(accepted["next_action"]["arguments"], ["--plan-only"])
        from dataclasses import replace
        inputs = replace(inputs, provisioning=replacement, provisioning_data=replacement_data)
        state = install_project.load_project(path)
        active = install_project.begin_operation(state, "universal stage", dict(state.selections["universal stage"]))
        plan, _ = install_actions.plan_universal_stage(inputs)
        result = install_actions.stage_universal(inputs, install_actions.WriteConfirmation(**plan.required_confirmations()))
        install_project.finish_operation(active, result)
        manifest = self.install / "install-set.manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        media_before = install_actions.reserved_media_identity(self.card)
        with mock.patch.object(install_actions, "stage_camera_bound_universal_install", side_effect=AssertionError("automatic retry")):
            result = install_project.attach_inputs(install_project.load_project(path), {"private-config-dir": self.root / "config"})
            self.assertEqual(result["next_action"]["operation"], "universal stage")
            self.assertEqual(result["next_action"]["safe_next_action"], "inspect-stale-operation")
            self.assertFalse(result["next_action"]["writes"])
            self.assertIn("universal stage", install_project.load_project(path).records)
        self.assertEqual(media_before, install_actions.reserved_media_identity(self.card))

    def test_signed_initialize_through_verify_short_and_long(self):
        outcomes = []
        for short in (False, True):
            root = self.root / ("short" if short else "long")
            root.mkdir()
            card = root / "card"
            card.mkdir()
            for role in ("recovery", "preserved"):
                (root / role).mkdir()
                (root / role / "fixture").write_bytes(b"synthetic input boundary")
            path = root / "project.json"
            project = install_project.init_project(path, name="fixture")
            selections = {
                "functional-recovery-dir": root / "recovery", "preserved-readback-dir": root / "preserved",
                "install-set-dir": self.install, "universal-bundle": self.install / "thingino-universal.tgb",
                "universal-public-key": Path(self.keys["public_key"]),
            }
            install_project.attach_inputs(project, selections)
            wifi = root / "confirmed wifi.json"
            wifi.write_text(json.dumps({"ssid": "fixture wifi", "passphrase": "__SET_LOCALLY__",
                                       "confirmation_ssid": "fixture wifi", "confirmation_passphrase": "__SET_LOCALLY__"}))
            wifi.chmod(0o600)
            phases = []
            for operation in ("init-session", "configure", "provision", "authorize", "stage", "handoff", "verify"):
                project = install_project.load_project(path)
                paths = project.selections["universal " + operation]
                args = ["universal", operation, "--non-interactive"]
                if short:
                    args += ["--project", str(path)]
                else:
                    args += [part for key, value in paths.items() for part in ("--" + key, value)]
                if operation in {"stage", "handoff"}:
                    args += ["--whole-device", "/dev/fixture", "--mount-root", str(card)]
                    plan = self.cli([*args, "--plan-only"])
                    confirmations = plan["result"]["required_confirmations"]
                    args += ["--confirm-plan", confirmations["plan_sha256"], "--confirm-target", confirmations["target"],
                             "--confirm-physical-device", confirmations["physical_device"],
                             "--confirm-stock-uboot-result" if operation == "handoff" else "--confirm-write-set", confirmations["write_set"]]
                active = None if short else install_project.begin_operation(project, "universal " + operation, dict(paths))
                if operation == "verify":
                    from installer.recovery_ap.host import load_host_session
                    session_dir = Path(paths["session-dir"])
                    (session_dir / "host/station_known_hosts").unlink()
                    name = load_host_session(session_dir).station_mdns_name
                    with mock.patch("installer.recovery_ap.host.resolve_recovery_ap_station", return_value=(name, "192.0.2.10")), \
                         mock.patch("installer.recovery_ap.host._exchange", side_effect=lambda session, **kw:
                             b"healthy " + name.encode() + b"\n" + kw["payload"] + b"b" * 64 + b"  /dev/mtd3\n"):
                        result = self.cli(args)
                else:
                    result = self.cli(args, secrets_path=wifi if operation == "configure" else None)
                if active is not None:
                    install_project.finish_operation(active, result)
                phases.append(result["phase"])
            self.assertTrue((card / "STAGE1.PKG").exists())
            self.assertFalse((card / install_actions.BOOTSTRAP_FILENAME).exists())
            records = install_project.project_status(install_project.load_project(path))["records"]
            self.assertEqual({row["state"] for row in records.values()}, {"completed"})
            outcomes.append(phases)
        self.assertEqual(outcomes[0], outcomes[1])
