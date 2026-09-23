"""Characterize build command dispatch without building or contacting a camera."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import user_cli, user_cli_build


class BuildCommandTests(unittest.TestCase):
    _LEGACY_TESTS = {
        "test_local_build_build_reports_migration_error_for_explicit_inputs",
        "test_explicit_path_conflicts_stop_before_loading_or_building",
        "test_saved_workspace_and_data_mode_must_match",
        "test_configure_eof_retains_error_and_does_not_read_secrets",
        "test_configure_forwards_confirmed_input_only_after_validation",
    }

    def setUp(self) -> None:
        if (
            not user_cli.LEGACY_INSTALLER_AVAILABLE
            and self._testMethodName in self._LEGACY_TESTS
        ):
            self.skipTest("legacy personal installer is not exported")
        self.root = Path("/external/build")
        self.resolve = self.enterContext(mock.patch.object(
            user_cli, "resolve_local_build_workspace", return_value=self.root
        ))

    def build_arguments(self, *extra: str) -> argparse.Namespace:
        return user_cli.build_parser().parse_args(["local-build", "build", *extra])

    def test_prepare_remembers_returned_root_and_preserves_host_action(self) -> None:
        arguments = user_cli.build_parser().parse_args(["local-build", "prepare"])
        result = {
            "build_root": "/external/resolved",
            "ready_to_build": False,
            "docker": {"ready": False},
        }
        with (
            mock.patch.object(
                user_cli, "prepare_local_build_workspace", return_value=result
            ) as prepare,
            mock.patch.object(
                user_cli, "remember_local_build_workspace",
                return_value=Path("/state/pointer"),
            ) as remember,
        ):
            document = arguments.handler(arguments)
        prepare.assert_called_once_with(build_root=self.root, build_count=1)
        remember.assert_called_once_with(
            build_root=Path("/external/resolved"), work_dir=arguments.work_dir
        )
        self.assertEqual(document, user_cli._document(
            "local-build prepare", ok=True, phase="local-build-host-action-required",
            result={**result, "workspace_pointer": "/state/pointer", "docker_ready": False},
        ))
        self.assertNotIn("workspace_pointer", result)

    def test_workspace_commands_keep_result_and_next_command(self) -> None:
        for command, operation, phase, next_command in (
            (
                "bootstrap", "bootstrap_public_build_inputs",
                "local-build-public-bootstrap-ready", None,
            ),
            (
                "acquire", "acquire_locked_public_inputs",
                "local-build-locked-public-inputs-ready", None,
            ),
            (
                "recovery-assets", "build_local_recovery_assets",
                "local-build-recovery-assets-ready",
                "thingino-dlink stock-recovery backup-prepare",
            ),
        ):
            with (
                self.subTest(command=command),
                mock.patch.object(
                    user_cli, operation, return_value={"evidence": "host-only"}
                ) as run,
            ):
                arguments = user_cli.build_parser().parse_args(["local-build", command])
                document = arguments.handler(arguments)
                run.assert_called_once_with(build_root=self.root)
                self.assertEqual(document, user_cli._document(
                    f"local-build {command}", ok=True, phase=phase,
                    next_command=next_command, result={"evidence": "host-only"},
                ))

    def test_local_build_build_reports_migration_error_for_explicit_inputs(self) -> None:
        arguments = self.build_arguments(
            "--vendor-bundle-dir", "/inputs/vendor-bundle",
            "--media-closure-dir", "/inputs/media-closure",
            "--private-config-dir", "/inputs/private-config",
            "--expected-wpa-config", "/inputs/expected-wpa",
            "--session-dir", "/inputs/session",
            "--build-count", "2",
        )
        with (
            mock.patch.object(user_cli, "load_local_build_settings") as load,
            mock.patch.object(
                user_cli, "build_local_install_set", return_value={"verified": True}
            ) as build,
        ):
            with self.assertRaisesRegex(
                user_cli.UserInstallerError,
                "local-build build is retired; use local-build build-universal",
            ):
                arguments.handler(arguments)
        load.assert_not_called()
        build.assert_not_called()

    def test_explicit_path_conflicts_stop_before_loading_or_building(self) -> None:
        for argv in (
            ["--settings", "/settings", "--vendor-bundle-dir", "/vendor"],
            ["--vendor-bundle-dir", "/vendor"],
        ):
            with (
                self.subTest(argv=argv),
                mock.patch.object(user_cli, "load_local_build_settings") as load,
                mock.patch.object(user_cli, "build_local_install_set") as build,
            ):
                arguments = self.build_arguments(*argv)
                with self.assertRaisesRegex(
                    user_cli.UserInstallerError,
                    "local-build build is retired; use local-build build-universal",
                ):
                    arguments.handler(arguments)
                load.assert_not_called()
                build.assert_not_called()

    def test_saved_workspace_and_data_mode_must_match(self) -> None:
        settings = {field: f"/inputs/{field}" for field in (
            "vendor_bundle_dir", "media_closure_dir", "private_config_dir",
            "expected_wpa_config", "session_dir",
        )}
        for root, argv in (
            ("/different", []),
            (str(self.root), ["--data-mode", "preserve"]),
        ):
            with (
                self.subTest(root=root),
                mock.patch.object(
                    user_cli, "load_local_build_settings",
                    return_value={**settings, "build_root": root, "data_mode": "preserve"},
                ),
                mock.patch.object(user_cli, "build_local_install_set") as build,
            ):
                arguments = self.build_arguments(*argv)
                with self.assertRaisesRegex(
                    user_cli.UserInstallerError,
                    "local-build build is retired; use local-build build-universal",
                ):
                    arguments.handler(arguments)
                build.assert_not_called()

    def test_configure_eof_retains_error_and_does_not_read_secrets(self) -> None:
        arguments = user_cli.build_parser().parse_args(["local-build", "configure"])
        with (
            mock.patch.object(user_cli.sys, "stdin", io.StringIO("")),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
            mock.patch.object(user_cli, "read_confirmed_private_input") as read,
        ):
            with self.assertRaises(user_cli.UserInstallerError) as raised:
                arguments.handler(arguments)
        self.assertEqual(
            str(raised.exception), "cannot read interactive setting: Private input root"
        )
        self.assertIn("Private input root [", stderr.getvalue())
        read.assert_not_called()

    def test_backend_error_json_retains_exit_code_and_safety_fields(self) -> None:
        with (
            mock.patch.object(
                user_cli, "bootstrap_public_build_inputs",
                side_effect=user_cli.LocalBuildBootstrapError("bootstrap refused"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            code = user_cli.main(["local-build", "bootstrap", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue()), {**user_cli._document(
            "local-build", ok=False, phase="stopped", next_command="thingino-dlink local-build",
            error="bootstrap refused",
        ), "error_code": "operation_rejected", "missing_inputs": [],
            "operation_outcome": "stopped", "automatic_retry": False, "physical_state": "not-observed"})

    def test_configure_forwards_confirmed_input_only_after_validation(self) -> None:
        fields = (
            "private-root", "vendor-bundle-dir", "media-closure-dir",
            "session-dir",
        )
        argv = [part for field in fields for part in (f"--{field}", f"/inputs/{field}")]
        arguments = user_cli.build_parser().parse_args([
            "local-build", "configure", *argv, "--data-mode", "preserve", "--secrets-fd", "9",
        ])
        calls = mock.Mock()
        calls.read.return_value = ("test-network", "test-secret", "test-network", "test-secret")
        calls.configure.return_value = {"configured": True}
        with (
            mock.patch.object(user_cli, "validate_local_build_setting_inputs", calls.validate),
            mock.patch.object(user_cli, "read_confirmed_private_input", calls.read),
            mock.patch.object(user_cli, "configure_local_build_settings", calls.configure),
        ):
            document = arguments.handler(arguments)
        selected = {field.replace("-", "_"): Path(f"/inputs/{field}") for field in fields}
        self.assertEqual(calls.mock_calls, [
            mock.call.validate(build_root=self.root, **selected),
            mock.call.read(secrets_fd=9),
            mock.call.configure(
                build_root=self.root, work_dir=arguments.work_dir, **selected,
                data_mode="preserve", ssid="test-network", passphrase="test-secret",
                confirmation_ssid="test-network", confirmation_passphrase="test-secret",
            ),
        ])
        self.assertEqual(document, user_cli._document(
            "local-build configure", ok=True, phase="local-build-private-inputs-configured",
            result={"configured": True},
        ))

    def test_universal_signer_failure_stops_before_build(self) -> None:
        arguments = user_cli.build_parser().parse_args([
            "local-build", "build-universal", "--vendor-bundle-dir", "/vendor",
            "--signing-key", "/model/key", "--signing-public-key", "/model/public",
        ])
        with (
            mock.patch.object(
                user_cli, "ensure_ed25519_keypair",
                side_effect=user_cli.BundleError("signer refused"),
            ) as signer,
            mock.patch.object(user_cli, "build_local_universal_install_set") as build,
        ):
            with self.assertRaisesRegex(user_cli.BundleError, "signer refused"):
                arguments.handler(arguments)
        signer.assert_called_once_with(Path("/model/key"), Path("/model/public"))
        build.assert_not_called()

    def test_universal_default_noninteractive_json_selects_full_raptor(self) -> None:
        keypair = {
            "created": False,
            "key_id": "a" * 64,
            "private_key": "/model/release.pem",
            "public_key": "/model/release.pub",
        }
        with (
            mock.patch.object(user_cli, "ensure_ed25519_keypair", return_value=keypair),
            mock.patch.object(
                user_cli,
                "build_local_universal_install_set",
                return_value={"raptor_full_source_build": True},
            ) as build,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = user_cli.main([
                "local-build", "build-universal",
                "--vendor-bundle-dir", "/vendor",
                "--non-interactive", "--json", "--events-jsonl",
            ])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["result"]["raptor_full_source_build"])
        self.assertNotIn("webrtc", build.call_args.kwargs)
        self.assertNotIn("raptor_rwd_artifact", build.call_args.kwargs)
        self.assertEqual(build.call_args.kwargs["data_mode"], "initialize")
        self.assertTrue(callable(build.call_args.kwargs["progress"]))
        events = [json.loads(line)["event"] for line in stderr.getvalue().splitlines()]
        self.assertEqual(events, ["started", "completed"])

    def test_runtime_stage_preserves_quoted_rollback_command(self) -> None:
        arguments = SimpleNamespace(
            session_dir=Path("/session with spaces"), host="camera.test",
            rootfs=Path("/candidate"), image_provenance=Path("/provenance"),
            expected_mtd3_sha256="a" * 64, unsquashfs=Path("/tools/unsquashfs"),
        )
        with mock.patch.object(
            user_cli_build, "stage_runtime_candidate", return_value={"active": True}
        ) as stage:
            document = user_cli._runtime_candidate_stage(arguments)
        stage.assert_called_once_with(
            session_dir=arguments.session_dir, host=arguments.host,
            rootfs_path=arguments.rootfs, provenance_path=arguments.image_provenance,
            expected_mtd3_sha256=arguments.expected_mtd3_sha256,
            unsquashfs=arguments.unsquashfs,
        )
        rollback = document["next_command"]
        self.assertEqual(shlex.split(rollback), [
            "python3", "-m", "installer.user_cli", "runtime-candidate", "rollback", "--json",
            "--session-dir", str(arguments.session_dir), "--host", "camera.test",
            "--approve-volatile-runtime-restart",
        ])
        self.assertEqual(document, user_cli._document(
            "runtime-candidate stage", ok=True, phase="runtime-candidate-active",
            next_command=rollback, result={"active": True, "rollback_command": rollback},
        ))

    def test_private_rotate_rejects_oversized_wpa_before_read_or_rotation(self) -> None:
        arguments = SimpleNamespace(wpa_config=Path("/inputs/wpa"))
        path = mock.Mock()
        path.stat.return_value.st_size = 4097
        with (
            mock.patch.object(user_cli, "_regular", return_value=path) as regular,
            mock.patch.object(user_cli_build, "rotate_private_config_role") as rotate,
        ):
            with self.assertRaisesRegex(
                user_cli.UserInstallerError,
                "private WPA configuration exceeds its size limit",
            ):
                user_cli._private_config_rotate(arguments)
        regular.assert_called_once_with(arguments.wpa_config, "private WPA configuration")
        path.read_bytes.assert_not_called()
        rotate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
