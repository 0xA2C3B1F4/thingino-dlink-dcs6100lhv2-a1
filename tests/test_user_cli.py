from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import (
    user_cli,
    user_cli_parser,
    user_cli_stock_recovery,
    user_cli_universal,
)


class UserCliTests(unittest.TestCase):
    def test_guided_cli_inspects_vendor_bundle_with_shared_loader(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            [
                "inspect-vendor-bundle",
                "--vendor-bundle-dir",
                "/private/vendor",
            ]
        )
        artifact = SimpleNamespace(
            destination="lib/libimp.so",
            name="libimp.so",
            raw=b"vendor",
            sha256="a" * 64,
        )
        bundle = SimpleNamespace(
            artifacts=(artifact,),
            bundle_sha256="b" * 64,
            firmware_version="1.02.02",
            manifest_sha256="c" * 64,
        )
        with mock.patch.object(
            user_cli, "load_vendor_bundle", return_value=bundle
        ) as load:
            result = arguments.handler(arguments)

        load.assert_called_once_with(Path("/private/vendor"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "vendor-bundle-inspected")
        self.assertEqual(result["result"]["file_count"], 1)
        self.assertEqual(result["result"]["files"][0]["name"], "libimp.so")

    def test_workflow_preflight_parser_accepts_collector_build(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            [
                "workflow-preflight",
                "--mode",
                "collector-build",
                "--data-volume",
                "/task-data",
            ]
        )
        self.assertEqual(arguments.mode, "collector-build")
        self.assertIs(arguments.handler, user_cli._workflow_preflight)

    def test_parser_facade_preserves_help_and_dispatch_identity(self) -> None:
        facade_parser = user_cli.build_parser()
        extracted_parser = user_cli_parser.build_parser(user_cli)
        self.assertEqual(facade_parser.format_help(), extracted_parser.format_help())
        arguments = facade_parser.parse_args(["status"])
        self.assertIs(arguments.handler, user_cli._status)

    def test_global_options_work_before_or_after_command(self) -> None:
        parser = user_cli.build_parser()
        before = parser.parse_args(["--json", "--work-dir", "one", "status"])
        after = parser.parse_args(["status", "--json", "--work-dir", "two"])
        self.assertTrue(before.json)
        self.assertEqual(before.work_dir, Path("one"))
        self.assertTrue(after.json)
        self.assertEqual(after.work_dir, Path("two"))

    def test_uartless_stock_restore_parser_and_output_keep_physical_proof_separate(self) -> None:
        parser = user_cli.build_parser()
        parsed = parser.parse_args(
            [
                "stock-recovery",
                "sd-authorize",
                "--recovery-dir",
                "recovery",
                "--preserved-readback-dir",
                "current",
                "--restore-output-dir",
                "restore",
                "--linux-config",
                "linux.config",
                "--input-dir",
                "set",
                "--mount-root",
                "card",
                "--media-preflight",
                "preflight.json",
                "--confirm-physical-device",
                "/dev/disk9",
                "--confirm-live-restore",
                "exact-confirmation",
            ]
        )
        self.assertIs(parsed.handler, user_cli._stock_sd_authorize)

        prepared = SimpleNamespace(authorization=b"private-token")
        preflight = SimpleNamespace(physical_device="/dev/disk9")
        arguments = SimpleNamespace(
            recovery_dir=Path("recovery"),
            preserved_readback_dir=Path("current"),
            restore_output_dir=Path("restore"),
            linux_config=Path("linux.config"),
            input_dir=Path("set"),
            mount_root=Path("card"),
            media_preflight=Path("preflight.json"),
            confirm_physical_device="/dev/disk9",
            confirm_live_restore="exact-confirmation",
        )
        with (
            mock.patch.object(user_cli, "read_snapshot", return_value=b"config"),
            mock.patch.object(
                user_cli,
                "inspect_stock_restore_bootstrap_set",
                return_value=prepared,
            ),
            mock.patch.object(user_cli, "load_media_preflight", return_value=preflight),
            mock.patch.object(
                user_cli,
                "expected_bootstrap_confirmation",
                return_value="public-confirmation-contract",
            ),
            mock.patch.object(user_cli, "authorize_stock_restore_bootstrap") as run,
        ):
            result = user_cli._stock_sd_authorize(arguments)
        run.assert_called_once()
        self.assertFalse(result["nor"]["full_physical_readback_verified"])
        self.assertEqual(result["nor"]["written_mtd"], [])
        self.assertEqual(result["result"]["write_set"], [3, 2, 1])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("private-token", rendered)

    def test_uartless_functional_capture_parser_and_authorization_are_explicit(self) -> None:
        parser = user_cli.build_parser()
        common = [
            "--package",
            "capture.bin",
            "--package-manifest",
            "capture.json",
            "--mount-root",
            "card",
            "--media-preflight",
            "preflight.json",
            "--confirm-physical-device",
            "/dev/disk9",
        ]
        prepare = parser.parse_args(
            ["stock-recovery", "uartless-prepare", *common]
        )
        authorize = parser.parse_args(
            [
                "stock-recovery",
                "uartless-authorize",
                *common,
                "--confirm-write-set",
                "WRITE-MTD1-MTD2",
            ]
        )
        handoff = parser.parse_args(
            [
                "stock-recovery",
                "uartless-handoff",
                *common,
                "--confirm-stock-uboot-result",
                "MTD1-MTD2-WRITTEN",
            ]
        )
        self.assertIs(prepare.handler, user_cli._stock_uartless_prepare)
        self.assertIs(authorize.handler, user_cli._stock_uartless_authorize)
        self.assertIs(handoff.handler, user_cli._stock_uartless_handoff)

        arguments = SimpleNamespace(
            package=Path("capture.bin"),
            package_manifest=Path("capture.json"),
            mount_root=Path("card"),
            media_preflight=Path("preflight.json"),
            confirm_physical_device="/dev/disk9",
            confirm_write_set="WRITE-MTD1-MTD2",
        )
        preflight = SimpleNamespace(physical_device="/dev/disk9")
        with (
            mock.patch.object(
                user_cli_stock_recovery,
                "_load_uartless_package",
                return_value=(b"package", object()),
            ),
            mock.patch.object(user_cli, "load_media_preflight", return_value=preflight),
            mock.patch.object(user_cli, "activate_passive_verified_package") as run,
        ):
            result = user_cli._stock_uartless_authorize(arguments)
        run.assert_called_once()
        self.assertTrue(result["result"]["armed"])
        self.assertEqual(
            result["result"]["future_physical_boot_writes_mtd"], [1, 2]
        )
        self.assertFalse(result["result"]["original_complete_backup_accepted"])
        self.assertEqual(result["result"]["write_set"], [])

    def test_uartless_validation_points_to_the_model_universal_build(self) -> None:
        output_dir = Path("/private/recovery with spaces")
        arguments = SimpleNamespace(
            collector_dir=Path("/card/DCS6100F"),
            package=Path("/card/UARTCAP.PSV"),
            output_dir=output_dir,
            confirm_output_dir=output_dir,
        )
        decision = SimpleNamespace(
            duplicate_partitions_accepted=True,
            functional_recovery_accepted=True,
            original_complete_backup_accepted=False,
            original_preserved_mtd=(0, 3, 4, 5),
            replacement_mtd=(1, 2),
        )
        with (
            mock.patch.object(
                user_cli,
                "capture_functional_backup_from_uartless_collector",
                return_value=decision,
            ),
            mock.patch.object(user_cli, "read_snapshot", return_value=b"package"),
        ):
            result = user_cli._stock_uartless_validate(arguments)
        self.assertEqual(
            result["next_command"],
            "thingino-dlink local-build build-universal "
            "--vendor-bundle-dir '/private/recovery with spaces/vendor'",
        )
        self.assertEqual(
            result["result"]["safe_next_action"],
            "build-one-model-universal-install-set",
        )

    def test_guided_install_accepts_only_one_explicit_recovery_class(self) -> None:
        parser = user_cli.build_parser()
        functional = parser.parse_args(
            [
                "prepare-card",
                "--whole-device",
                "/dev/disk9",
                "--mount-root",
                "card",
                "--functional-recovery-dir",
                "functional",
                "--preserved-readback-dir",
                "functional/preserved",
            ]
        )
        self.assertEqual(functional.functional_recovery_dir, Path("functional"))
        self.assertIsNone(functional.recovery_dir)
        with self.assertRaises(user_cli.ArgumentParsingError):
            parser.parse_args(
                [
                    "prepare-card",
                    "--whole-device",
                    "/dev/disk9",
                    "--mount-root",
                    "card",
                    "--recovery-dir",
                    "exact",
                    "--functional-recovery-dir",
                    "functional",
                    "--preserved-readback-dir",
                    "preserved",
                ]
            )

    def test_universal_cli_separates_model_build_from_camera_inputs(self) -> None:
        parser = user_cli.build_parser()
        model = parser.parse_args(
            [
                "local-build",
                "build-universal",
                "--vendor-bundle-dir",
                "vendor",
                "--media-closure-dir",
                "media",
                "--raptor-rwd-artifact",
                "raptor.tar",
                "--signing-key",
                "release.pem",
            ]
        )
        self.assertEqual(model.local_build_command, "build-universal")
        self.assertFalse(hasattr(model, "private_config_dir"))
        provision = parser.parse_args(
            [
                "universal",
                "provision",
                "--functional-recovery-dir",
                "functional",
                "--preserved-readback-dir",
                "functional/preserved",
                "--universal-bundle",
                "thingino-universal.tgb",
                "--universal-public-key",
                "release.pub",
                "--private-config-dir",
                "private",
                "--session-dir",
                "session",
                "--signing-key",
                "authorization.pem",
                "--unsquashfs",
                "/tools/unsquashfs",
                "--mkfs-jffs2",
                "/tools/mkfs.jffs2",
                "--output",
                "camera.tps",
                "--data-output",
                "camera.jffs2",
            ]
        )
        self.assertEqual(provision.universal_command, "provision")
        self.assertEqual(provision.functional_recovery_dir, Path("functional"))
        self.assertIsNone(provision.recovery_dir)

    def test_universal_build_minimum_needs_only_the_vendor_bundle(self) -> None:
        arguments = user_cli.build_parser().parse_args(
            [
                "local-build",
                "build-universal",
                "--vendor-bundle-dir",
                "vendor",
            ]
        )
        self.assertIsNone(arguments.media_closure_dir)
        self.assertIsNone(arguments.raptor_rwd_artifact)
        self.assertIsNone(arguments.signing_key)
        self.assertEqual(arguments.build_count, 1)
        self.assertIs(arguments.handler, user_cli._local_build_build_universal)

    def test_universal_build_generates_a_stable_default_model_signer(self) -> None:
        root = Path("/external/build")
        arguments = SimpleNamespace(
            build_root=root,
            work_dir=Path("/state"),
            vendor_bundle_dir=Path("/camera/vendor"),
            media_closure_dir=None,
            raptor_rwd_artifact=None,
            signing_key=None,
            signing_public_key=None,
        )
        keypair = {
            "created": True,
            "key_id": "a" * 64,
            "private_key": "/external/build-private/model-signing/release-ed25519.pem",
            "public_key": "/external/build-private/model-signing/release-ed25519.pub",
        }
        with (
            mock.patch.object(user_cli, "resolve_local_build_workspace", return_value=root),
            mock.patch.object(
                user_cli, "ensure_ed25519_keypair", return_value=keypair
            ) as ensure,
            mock.patch.object(
                user_cli,
                "build_local_universal_install_set",
                return_value={"artifact_scope": "model-universal"},
            ) as build,
        ):
            result = user_cli._local_build_build_universal(arguments)
        ensure.assert_called_once_with(
            Path("/external/build-private/model-signing/release-ed25519.pem"),
            None,
        )
        self.assertIsNone(build.call_args.kwargs["media_closure_dir"])
        self.assertIsNone(build.call_args.kwargs["raptor_rwd_artifact"])
        self.assertEqual(build.call_args.kwargs["build_count"], 1)
        self.assertEqual(result["result"]["model_signing"], keypair)

    def test_universal_configure_binds_private_inputs_to_the_session(self) -> None:
        parsed = user_cli.build_parser().parse_args(
            [
                "universal",
                "configure",
                "--session-dir",
                "/private/session",
                "--output-dir",
                "/private/camera/install-config",
            ]
        )
        self.assertIs(parsed.handler, user_cli._universal_configure)
        self.assertFalse(hasattr(parsed, "ssid"))
        self.assertFalse(hasattr(parsed, "passphrase"))

        session = SimpleNamespace(identity=Path("/private/session/host/identity"))
        generated = SimpleNamespace(output_dir=Path("/private/camera/install-config"))
        private = SimpleNamespace(credential_set_id="b" * 64)
        keypair = {
            "created": True,
            "key_id": "c" * 64,
            "private_key": "/private/camera/authorization-signing/ed25519.pem",
            "public_key": "/private/camera/authorization-signing/ed25519.pub",
        }
        arguments = SimpleNamespace(
            session_dir=Path("/private/session"),
            output_dir=Path("/private/camera/install-config"),
            signing_key=None,
            signing_public_key=None,
            secrets_fd=7,
        )
        with (
            mock.patch.object(user_cli, "load_host_session", return_value=session),
            mock.patch.object(user_cli, "ensure_ed25519_keypair", return_value=keypair),
            mock.patch.object(
                user_cli,
                "read_confirmed_private_input",
                return_value=("wifi", "password", "wifi", "password"),
            ),
            mock.patch.object(user_cli, "render_private_wpa_config", return_value=b"wpa"),
            mock.patch.object(
                user_cli, "read_authorized_key", return_value=b"ssh-ed25519 key\n"
            ),
            mock.patch.object(user_cli, "load_service_credential", return_value=b"d" * 64),
            mock.patch.object(user_cli, "generate_private_config", return_value=generated) as generate,
            mock.patch.object(
                user_cli, "load_private_config_for_session", return_value=private
            ) as load_private,
        ):
            result = user_cli._universal_configure(arguments)
        self.assertEqual(
            generate.call_args.kwargs["credential"], b"d" * 64 + b"\n"
        )
        load_private.assert_called_once_with(
            output_dir=Path("/private/camera/install-config"),
            session_dir=Path("/private/session"),
        )
        self.assertEqual(result["phase"], "camera-private-inputs-configured")
        self.assertEqual(result["result"]["write_set"], [])

    def test_universal_stage_requires_the_full_declared_write_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            card = root / "card"
            work = root / "work"
            card.mkdir()
            arguments = SimpleNamespace(
                recovery_dir=None,
                functional_recovery_dir=root / "functional",
                preserved_readback_dir=root / "preserved",
                install_set_dir=root / "install-set",
                universal_public_key=root / "model.pub",
                provisioning=root / "provisioning.zip",
                provisioning_data=root / "provisioning.jffs2",
                authorization_dir=root / "authorization",
                authorization_public_key=root / "authorization.pub",
                session_dir=root / "session",
                whole_device="/dev/test-card",
                mount_root=card,
                confirm_physical_device="/dev/test-card",
                confirm_target="DCS-6100LHV2-A1",
                confirm_write_set=(
                    user_cli_universal.UNIVERSAL_WRITE_CONFIRMATION
                ),
                work_dir=work,
                json=True,
            )
            recovery = SimpleNamespace(camera_identity_sha256="a" * 64)
            preflight = SimpleNamespace(
                physical_device="/dev/test-card",
                mount_root=card.resolve(),
            )
            validated = SimpleNamespace()
            with (
                mock.patch.object(
                    user_cli,
                    "validate_functional_recovery_boundary",
                    return_value=recovery,
                ),
                mock.patch.object(
                    user_cli,
                    "create_preflight_document",
                    return_value={"schema_version": 1},
                ),
                mock.patch.object(
                    user_cli, "load_media_preflight", return_value=preflight
                ),
                mock.patch.object(
                    user_cli, "recovery_session_identity", return_value="b" * 64
                ),
                mock.patch.object(
                    user_cli,
                    "validate_camera_bound_universal_install",
                    return_value=validated,
                ) as validate,
                mock.patch.object(
                    user_cli,
                    "stage_camera_bound_universal_install",
                    return_value={"bootstrap": "c" * 64},
                ) as stage,
            ):
                result = user_cli._universal_stage(arguments)
            self.assertIs(validate.call_args.kwargs["recovery"], recovery)
            stage.assert_called_once_with(
                validated,
                root=card,
                preflight=preflight,
                confirmed_physical_device="/dev/test-card",
            )
            self.assertTrue(result["result"]["armed"])
            self.assertFalse(result["result"]["nor_written_by_host"])
            self.assertEqual(
                result["result"]["safe_next_action"],
                "boot-stock-updater-once-then-return-sd-for-universal-handoff",
            )
            self.assertEqual(result["result"]["write_set"], [])

    def test_universal_handoff_revalidates_tuple_before_passivation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            card = root / "card"
            work = root / "work"
            card.mkdir()
            arguments = SimpleNamespace(
                recovery_dir=None,
                functional_recovery_dir=root / "functional",
                preserved_readback_dir=root / "preserved",
                install_set_dir=root / "install-set",
                universal_public_key=root / "model.pub",
                provisioning=root / "provisioning.zip",
                provisioning_data=root / "provisioning.jffs2",
                authorization_dir=root / "authorization",
                authorization_public_key=root / "authorization.pub",
                session_dir=root / "session",
                whole_device="/dev/test-card",
                mount_root=card,
                confirm_physical_device="/dev/test-card",
                confirm_target="DCS-6100LHV2-A1",
                confirm_stock_uboot_result=(
                    user_cli_universal.UNIVERSAL_HANDOFF_CONFIRMATION
                ),
                work_dir=work,
                json=True,
            )
            recovery = SimpleNamespace(camera_identity_sha256="a" * 64)
            preflight = SimpleNamespace(
                physical_device="/dev/test-card",
                mount_root=card.resolve(),
            )
            validated = SimpleNamespace()
            with (
                mock.patch.object(
                    user_cli,
                    "validate_functional_recovery_boundary",
                    return_value=recovery,
                ),
                mock.patch.object(
                    user_cli,
                    "create_preflight_document",
                    return_value={"schema_version": 1},
                ),
                mock.patch.object(
                    user_cli, "load_media_preflight", return_value=preflight
                ),
                mock.patch.object(
                    user_cli, "recovery_session_identity", return_value="b" * 64
                ),
                mock.patch.object(
                    user_cli,
                    "validate_camera_bound_universal_install",
                    return_value=validated,
                ) as validate,
                mock.patch.object(
                    user_cli,
                    "handoff_camera_bound_universal_install",
                    return_value={"STAGE1.PKG": "c" * 64},
                ) as handoff,
            ):
                result = user_cli._universal_handoff(arguments)
            self.assertIs(validate.call_args.kwargs["recovery"], recovery)
            handoff.assert_called_once_with(
                validated,
                root=card,
                preflight=preflight,
                confirmed_physical_device="/dev/test-card",
            )
            self.assertFalse(result["result"]["armed"])
            self.assertFalse(result["result"]["nor_written_by_host"])
            self.assertEqual(
                result["result"]["safe_next_action"],
                "boot-camera-with-passive-card-to-run-stage1",
            )

    def test_universal_handoff_parser_requires_stock_result_confirmation(self) -> None:
        parsed = user_cli.build_parser().parse_args(
            [
                "universal",
                "handoff",
                "--functional-recovery-dir",
                "functional",
                "--preserved-readback-dir",
                "preserved",
                "--install-set-dir",
                "install-set",
                "--universal-public-key",
                "model.pub",
                "--provisioning",
                "provisioning.zip",
                "--provisioning-data",
                "provisioning.jffs2",
                "--authorization-dir",
                "authorization",
                "--authorization-public-key",
                "authorization.pub",
                "--session-dir",
                "session",
                "--whole-device",
                "/dev/disk9",
                "--mount-root",
                "card",
                "--confirm-stock-uboot-result",
                "MTD1-MTD2-WRITTEN",
            ]
        )
        self.assertIs(parsed.handler, user_cli._universal_handoff)
        self.assertEqual(parsed.confirm_stock_uboot_result, "MTD1-MTD2-WRITTEN")

    def test_local_build_prepare_defaults_to_one_clean_build(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            ["local-build", "prepare", "--build-root", "/external/build"]
        )
        self.assertEqual(arguments.build_root, Path("/external/build"))
        self.assertEqual(arguments.build_count, 1)
        self.assertIs(arguments.handler, user_cli._local_build_prepare)

    def test_local_build_document_keeps_host_action_separate_from_creation(self) -> None:
        document = user_cli._local_build_document(
            "local-build prepare",
            {
                "build_root": "/external/build",
                "docker": {"ready": False},
                "host_platform": "macos",
                "ready_to_build": False,
                "safe_next_action": "install-or-start-docker",
            },
        )
        self.assertTrue(document["ok"])
        self.assertEqual(document["phase"], "local-build-host-action-required")
        self.assertFalse(document["result"]["docker_ready"])
        self.assertEqual(document["result"]["host_platform"], "macos")

    def test_local_build_bootstrap_parser_uses_generated_workspace(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            ["local-build", "bootstrap", "--build-root", "/external/build"]
        )
        self.assertEqual(arguments.build_root, Path("/external/build"))
        self.assertIs(arguments.handler, user_cli._local_build_bootstrap)

    def test_local_build_acquire_parser_uses_generated_workspace(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            ["local-build", "acquire", "--build-root", "/external/build"]
        )
        self.assertEqual(arguments.build_root, Path("/external/build"))
        self.assertIs(arguments.handler, user_cli._local_build_acquire)

    def test_local_build_recovery_assets_parser_uses_generated_workspace(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            ["local-build", "recovery-assets", "--build-root", "/external/build"]
        )
        self.assertEqual(arguments.build_root, Path("/external/build"))
        self.assertIs(arguments.handler, user_cli._local_build_recovery_assets)

    def test_local_build_recovery_assets_dispatches_before_configure(self) -> None:
        arguments = SimpleNamespace(
            build_root=Path("/external/build"),
            work_dir=Path("/state"),
        )
        with (
            mock.patch.object(
                user_cli,
                "resolve_local_build_workspace",
                return_value=Path("/external/build"),
            ),
            mock.patch.object(
                user_cli,
                "build_local_recovery_assets",
                return_value={"write_set": [], "files": {"kernel": "/result/kernel"}},
            ) as build,
        ):
            result = user_cli._local_build_recovery_assets(arguments)
        build.assert_called_once_with(build_root=Path("/external/build"))
        self.assertEqual(result["phase"], "local-build-recovery-assets-ready")
        self.assertEqual(result["result"]["write_set"], [])

    def test_local_build_configure_parser_has_no_secret_arguments(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            ["local-build", "configure", "--private-root", "/private/input"]
        )
        self.assertEqual(arguments.private_root, Path("/private/input"))
        self.assertFalse(hasattr(arguments, "ssid"))
        self.assertFalse(hasattr(arguments, "passphrase"))
        self.assertIs(arguments.handler, user_cli._local_build_configure)

    def test_local_build_configure_validates_paths_before_reading_wifi(self) -> None:
        arguments = SimpleNamespace(
            build_root=Path("/external/build"),
            work_dir=Path("/state"),
            private_root=Path("/private"),
            vendor_bundle_dir=Path("/private/vendor"),
            media_closure_dir=Path("/private/media"),
            session_dir=Path("/private/session"),
            raptor_rwd_artifact=Path("/private/raptor-rwd.tar.gz"),
            data_mode="initialize",
            secrets_fd=None,
        )
        with (
            mock.patch.object(
                user_cli,
                "resolve_local_build_workspace",
                return_value=Path("/external/build"),
            ),
            mock.patch.object(
                user_cli,
                "validate_local_build_setting_inputs",
                side_effect=user_cli.LocalBuildError("private vendor bundle is missing"),
            ),
            mock.patch.object(user_cli, "read_confirmed_private_input") as read_wifi,
        ):
            with self.assertRaisesRegex(user_cli.LocalBuildError, "vendor bundle"):
                user_cli._local_build_configure(arguments)
        read_wifi.assert_not_called()

    def test_local_build_status_can_use_prepare_recorded_workspace(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(["local-build", "status"])
        self.assertIsNone(arguments.build_root)
        self.assertIs(arguments.handler, user_cli._local_build_status)

    def test_local_build_build_parser_keeps_secrets_in_files(self) -> None:
        parser = user_cli.build_parser()
        arguments = parser.parse_args(
            [
                "local-build",
                "build",
                "--vendor-bundle-dir",
                "/private/vendor",
                "--media-closure-dir",
                "/private/media",
                "--private-config-dir",
                "/private/config",
                "--expected-wpa-config",
                "/private/current-wpa.conf",
                "--session-dir",
                "/private/session",
                "--raptor-rwd-artifact",
                "/private/raptor-rwd.tar.gz",
            ]
        )
        self.assertIsNone(arguments.build_root)
        self.assertEqual(arguments.build_count, 1)
        self.assertIsNone(arguments.data_mode)
        self.assertFalse(hasattr(arguments, "password"))
        self.assertFalse(hasattr(arguments, "token"))
        self.assertIs(arguments.handler, user_cli._local_build_build)

    def test_local_build_build_uses_configure_recorded_settings(self) -> None:
        root = Path("/external/build")
        settings = {
            "build_root": str(root),
            "data_mode": "preserve",
            "expected_wpa_config": "/private/expected-wpa.conf",
            "media_closure_dir": "/private/media",
            "private_config_dir": "/private/config",
            "raptor_rwd_artifact": "/private/raptor-rwd.tar.gz",
            "session_dir": "/private/session",
            "vendor_bundle_dir": "/private/vendor",
        }
        arguments = SimpleNamespace(
            build_root=root,
            work_dir=Path("/state"),
            settings=None,
            vendor_bundle_dir=None,
            media_closure_dir=None,
            private_config_dir=None,
            expected_wpa_config=None,
            session_dir=None,
            raptor_rwd_artifact=None,
            data_mode=None,
        )
        with (
            mock.patch.object(user_cli, "resolve_local_build_workspace", return_value=root),
            mock.patch.object(user_cli, "load_local_build_settings", return_value=settings),
            mock.patch.object(
                user_cli,
                "build_local_install_set",
                return_value={"install_set_dir": "/private/result"},
            ) as build,
        ):
            result = user_cli._local_build_build(arguments)
        self.assertEqual(result["phase"], "local-build-install-set-inspected")
        self.assertEqual(build.call_args.kwargs["build_count"], 1)
        self.assertEqual(build.call_args.kwargs["data_mode"], "preserve")
        self.assertEqual(
            build.call_args.kwargs["expected_wpa_config_path"],
            Path("/private/expected-wpa.conf"),
        )

    def test_local_install_set_stager_binds_data_mode_and_physical_device(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            install_set = root / "install-set"
            mount_root = root / "card"
            install_set.mkdir()
            mount_root.mkdir()
            for filename in (
                user_cli.BOOTSTRAP_NAME,
                "THINGINO2.BIN",
                "install-set.manifest.json",
            ):
                (install_set / filename).write_bytes(filename.encode())
            parser = user_cli.build_parser()
            arguments = parser.parse_args(
                [
                    "stage-install-set",
                    "--json",
                    "--work-dir",
                    str(root / "state"),
                    "--install-set-dir",
                    str(install_set),
                    "--recovery-dir",
                    str(root / "recovery"),
                    "--preserved-readback-dir",
                    str(root / "preserved"),
                    "--whole-device",
                    "/dev/disk9",
                    "--mount-root",
                    str(mount_root),
                    "--expected-data-mode",
                    "initialize",
                    "--confirm-physical-device",
                    "/dev/disk9",
                    "--confirm-target",
                    "DCS-6100LHV2-A1",
                ]
            )
            preflight = SimpleNamespace(physical_device="/dev/disk9")
            with (
                mock.patch.object(
                    user_cli,
                    "validate_install_set",
                    return_value=SimpleNamespace(data_mode="initialize"),
                ),
                mock.patch.object(
                    user_cli,
                    "validate_existing_recovery_boundary",
                    return_value=SimpleNamespace(recovery_images=2),
                ),
                mock.patch.object(
                    user_cli,
                    "create_preflight_document",
                    return_value={"schema_version": 1},
                ),
                mock.patch.object(
                    user_cli,
                    "load_media_preflight",
                    return_value=preflight,
                ),
                mock.patch.object(
                    user_cli,
                    "stage_verified_install_set",
                    return_value={user_cli.BOOTSTRAP_NAME: "a" * 64},
                ) as stage,
            ):
                document = user_cli._stage_install_set(arguments)
            self.assertTrue(document["ok"])
            self.assertEqual(document["result"]["data_mode"], "initialize")
            self.assertEqual(document["result"]["physical_device"], "/dev/disk9")
            stage.assert_called_once()

    def test_failed_complete_backup_gate_prevents_media_and_live_install_actions(self) -> None:
        blocked = user_cli.RecoveryGateError("complete backup gate failed")
        stage_arguments = SimpleNamespace(
            recovery_dir=Path("/private/backup"),
            preserved_readback_dir=Path("/private/current"),
            install_set_dir=Path("/private/install-set"),
        )
        with (
            mock.patch.object(
                user_cli,
                "validate_existing_recovery_boundary",
                side_effect=blocked,
            ),
            mock.patch.object(user_cli, "stage_verified_install_set") as stage,
        ):
            with self.assertRaises(user_cli.RecoveryGateError):
                user_cli._stage_install_set(stage_arguments)
            stage.assert_not_called()

        install_arguments = SimpleNamespace(
            recovery_dir=Path("/private/backup"),
            preserved_readback_dir=Path("/private/current"),
        )
        with (
            mock.patch.object(
                user_cli,
                "validate_existing_recovery_boundary",
                side_effect=blocked,
            ),
            mock.patch.object(user_cli, "complete_personal_install") as install,
        ):
            with self.assertRaises(user_cli.RecoveryGateError):
                user_cli._install(install_arguments)
            install.assert_not_called()

    def test_json_argument_error_uses_the_stable_document(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = user_cli.main(["status", "--json", "--not-an-option"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "argument-error")
        self.assertEqual(result["nor"]["written_mtd"], [])

    def test_runtime_stage_requires_explicit_restart_approval(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = user_cli.main(
                [
                    "runtime-candidate",
                    "stage",
                    "--json",
                    "--session-dir",
                    "/private/session",
                    "--host",
                    "192.0.2.20",
                    "--rootfs",
                    "/private/candidate.squashfs",
                    "--image-provenance",
                    "/private/final-root.private.json",
                    "--expected-mtd3-sha256",
                    "a" * 64,
                ]
            )
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "argument-error")
        self.assertIn("--approve-volatile-runtime-restart", result["error"])

    def test_runtime_rollback_requires_explicit_restart_approval(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = user_cli.main(
                [
                    "runtime-candidate",
                    "rollback",
                    "--json",
                    "--session-dir",
                    "/private/session",
                    "--host",
                    "192.0.2.20",
                ]
            )
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "argument-error")
        self.assertIn("--approve-volatile-runtime-restart", result["error"])

    def test_invalid_runtime_candidate_is_rejected_before_camera_observation(self) -> None:
        arguments = SimpleNamespace(
            project_root=Path("/project"),
            mode="runtime-candidate",
            data_volume=Path("/external-volume"),
            session_dir=Path("/private/session"),
            vendor_bundle_dir=None,
            private_config_dir=None,
            image=None,
            image_provenance=Path("/private/final-root.private.json"),
            rootfs=Path("/private/candidate.squashfs"),
            output_dir=None,
            work_dir=Path("/private/work"),
            observe_camera=True,
            recovery_host="192.168.88.1",
        )
        stopped = {
            "ok": False,
            "failures": ["runtime-candidate-invalid"],
            "next_command": None,
        }
        with (
            mock.patch.object(
                user_cli,
                "run_workflow_preflight",
                return_value=stopped,
            ) as preflight,
            mock.patch.object(user_cli, "observe_camera_state") as observe,
        ):
            result = user_cli._workflow_preflight(arguments)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "workflow-stopped")
        preflight.assert_called_once()
        observe.assert_not_called()

    def test_unconfigured_status_has_the_full_stable_status_shape(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            result = user_cli._status(SimpleNamespace(work_dir=Path(name)))
        self.assertEqual(
            set(result["result"]),
            {
                "current_run_written_mtd",
                "last_camera_proven_at",
                "last_camera_state",
                "local_phase",
                "mtd3_kind",
                "mtd3_sha256",
                "write_history",
            },
        )

    def test_recovery_package_must_embed_the_same_private_session(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            package_path = root / "bootstrap.bin"
            package_path.write_bytes(b"package")
            session = root / "session"
            media = session / "media/RECOVERY"
            media.mkdir(parents=True)
            expected = {
                "AP.PSK": b"a" * 65,
                "AUTHORIZED.KEY": b"ssh-ed25519 key\n",
                "HOST.KEY": b"host-key",
            }
            for filename, raw in expected.items():
                (media / filename).write_bytes(raw)
            record = SimpleNamespace(
                flash_offset=user_cli.TARGET.partition(2).offset,
                payload=b"squashfs",
            )
            package = SimpleNamespace(records=(record,))

            def extract(*arguments: object, **_: object) -> SimpleNamespace:
                relative = str(arguments[0][-1])
                return SimpleNamespace(
                    returncode=0,
                    stdout=expected[Path(relative).name],
                )

            with (
                mock.patch.object(user_cli, "parse_package", return_value=package),
                mock.patch.object(user_cli, "validate_bootstrap"),
                mock.patch.object(user_cli.subprocess, "run", side_effect=extract),
            ):
                result = user_cli._validate_recovery_package_binding(
                    package_path=package_path,
                    session=session,
                    unsquashfs=Path("/usr/bin/unsquashfs"),
                )
            self.assertEqual(result["package_sha256"], user_cli.hashlib.sha256(b"package").hexdigest())
            self.assertEqual(len(result["session_binding_sha256"]), 64)

    def test_status_is_read_only_and_reports_separate_write_history(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            config = {
                "schema_version": 1,
                "target": user_cli._target(),
            }
            (work / user_cli.CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
            install = work / "install"
            install.mkdir()
            state = {
                "schema_version": 1,
                "phase": "healthy",
                "current_run": {"read_back_verified": True, "written_mtd": []},
                "last_camera": {
                    "mtd3_kind": "personal",
                    "mtd3_sha256": "9" * 64,
                    "state": "station",
                },
                "write_history": [
                    {"mtd": 3, "read_back_verified": True, "image_sha256": "9" * 64}
                ],
            }
            state_path = install / user_cli.STATE_NAME
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = user_cli.main(["status", "--json", "--work-dir", str(work)])
            after = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(before, after)
            self.assertEqual(result["nor"]["written_mtd"], [])
            self.assertEqual(len(result["result"]["write_history"]), 1)
            self.assertEqual(result["result"]["mtd3_sha256"], "9" * 64)

    def test_install_wraps_existing_orchestration_and_reports_no_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            base = work / "base"
            base.write_bytes(b"root")
            session = work / "session"
            session.mkdir()
            helper = work / "helper.swift"
            helper.write_text("", encoding="utf-8")
            tool = work / "tool"
            tool.write_text("", encoding="utf-8")
            arguments = SimpleNamespace(
                work_dir=work,
                secrets_fd=None,
                station_timeout=180,
                recovery_dir=work / "recovery",
                preserved_readback_dir=work / "preserved",
            )
            validated = {
                "base_rootfs": base,
                "media_closure_dir": work / "media-closure",
                "macos_wifi_helper": helper,
                "macos_station_wifi_helper": helper,
                "mksquashfs": tool,
                "session_dir": session,
                "unsquashfs": tool,
            }
            completed = {
                "file_transfer_bytes": 4096,
                "image_sha256": "a" * 64,
                "mtd3_read_back_verified": True,
                "mtd3_sha256": "a" * 64,
                "read_back_verified": True,
                "write_history": [
                    {"mtd": 3, "read_back_verified": True, "image_sha256": "a" * 64}
                ],
                "written_mtd": [],
            }
            with (
                mock.patch.object(user_cli, "validate_existing_recovery_boundary"),
                mock.patch.object(user_cli, "_load_config", return_value={}),
                mock.patch.object(user_cli, "_validate_config", return_value=validated),
                mock.patch.object(user_cli, "read_private_input", return_value=("ssid", "passphrase")),
                mock.patch.object(user_cli, "complete_personal_install", return_value=completed) as run,
            ):
                result = user_cli._install(arguments)
            self.assertEqual(result["nor"]["written_mtd"], [])
            self.assertTrue(result["nor"]["full_physical_readback_verified"])
            run.assert_called_once()

    def test_failed_install_reports_completed_current_mtd3_write(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            install = work / "install"
            install.mkdir()
            state = {
                "schema_version": 1,
                "phase": "activation-requested",
                "current_run": {"read_back_verified": True, "written_mtd": [3]},
                "write_history": [
                    {"mtd": 3, "read_back_verified": True, "image_sha256": "a" * 64}
                ],
            }
            (install / user_cli.STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(user_cli, "_install", side_effect=user_cli.UserInstallerError("timeout")),
                contextlib.redirect_stdout(output),
            ):
                code = user_cli.main(
                    [
                        "install",
                        "--json",
                        "--work-dir",
                        str(work),
                        "--recovery-dir",
                        str(work / "recovery"),
                        "--preserved-readback-dir",
                        str(work / "preserved"),
                    ]
                )
            result = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(result["nor"]["written_mtd"], [3])
            self.assertTrue(result["nor"]["full_physical_readback_verified"])

    def test_prepare_card_exact_rerun_does_not_rewrite_files(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            work = Path(name) / "work"
            mount = Path(name) / "card"
            install_set = Path(name) / "set"
            work.mkdir()
            mount.mkdir()
            install_set.mkdir()
            files = {
                user_cli.BOOTSTRAP_NAME: b"bootstrap",
            }
            for filename, raw in files.items():
                (install_set / filename).write_bytes(raw)
                (mount / filename).write_bytes(raw)
            arguments = SimpleNamespace(
                work_dir=work,
                mount_root=mount,
                whole_device="/dev/disk9",
                confirm_physical_device="/dev/disk9",
                confirm_target="DCS-6100LHV2-A1",
                json=True,
                recovery_dir=Path(name) / "recovery",
                preserved_readback_dir=Path(name) / "preserved",
            )
            preflight = SimpleNamespace(physical_device="/dev/disk9")
            with (
                mock.patch.object(
                    user_cli,
                    "validate_existing_recovery_boundary",
                    return_value=SimpleNamespace(recovery_images=2),
                ),
                mock.patch.object(user_cli, "_load_config", return_value={}),
                mock.patch.object(
                    user_cli,
                    "_validate_config",
                    return_value={
                        "recovery_package": install_set / user_cli.BOOTSTRAP_NAME
                    },
                ),
                mock.patch.object(
                    user_cli,
                    "create_preflight_document",
                    return_value={"schema_version": 1},
                ),
                mock.patch.object(user_cli, "load_media_preflight", return_value=preflight),
                mock.patch.object(user_cli, "parse_package"),
                mock.patch.object(user_cli, "validate_bootstrap"),
                mock.patch.object(user_cli, "stage_verified_package") as stage,
            ):
                result = user_cli._prepare_card(arguments)
            self.assertTrue(result["result"]["already_prepared"])
            stage.assert_not_called()

    def test_media_verification_is_separate_and_requires_management_first(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            (work / "closure").mkdir()
            order: list[str] = []
            with (
                mock.patch.object(
                    user_cli,
                    "_load_config",
                    return_value={},
                ),
                mock.patch.object(
                    user_cli,
                    "_validate_config",
                    return_value={
                        "session_dir": work / "session",
                        "media_closure_dir": work / "closure",
                    },
                ),
                mock.patch.object(
                    user_cli,
                    "load_media_closure",
                    return_value=SimpleNamespace(),
                ),
                mock.patch.object(
                    user_cli,
                    "load_service_credential",
                    return_value=b"a" * 64,
                ) as service_credential,
                mock.patch.object(
                    user_cli,
                    "prove_thingino_health",
                    side_effect=lambda **_: (
                        order.append("management")
                        or {
                            "health_gate": "passed",
                            "station_ipv4": "198.51.100.23",
                        }
                    ),
                ) as health,
                mock.patch.object(
                    user_cli,
                    "prove_thingino_media",
                    side_effect=lambda **_: (
                        order.append("media")
                        or {
                            "media_gate": "passed",
                            "station_ipv4": "198.51.100.23",
                        }
                    ),
                ) as media,
                mock.patch.object(
                    user_cli,
                    "_expected_personal_image_sha256",
                    return_value="a" * 64,
                ),
                mock.patch.object(
                    user_cli,
                    "verify_rtsp_h264_1080p",
                    return_value={"width": 1920, "height": 1080},
                ) as rtsp,
                mock.patch.object(user_cli, "_record_proof"),
            ):
                result = user_cli._verify_media(
                    SimpleNamespace(
                        work_dir=work,
                        media_closure_dir=work / "closure",
                    )
                )
            self.assertEqual(result["phase"], "media-verified")
            self.assertEqual(order, ["management", "media"])
            health.assert_called_once()
            media.assert_called_once()
            service_credential.assert_called_once_with(work / "session")
            rtsp.assert_called_once()
            self.assertEqual(rtsp.call_args.kwargs["username"], "root")
            self.assertEqual(rtsp.call_args.kwargs["password"], b"a" * 64)


if __name__ == "__main__":
    unittest.main()
