from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import cli, cli_parser
from installer.final_bundle import ValidatedFinalBundle
from installer.sd_package import generate_bootstrap


class CliTests(unittest.TestCase):
    def test_parser_facade_preserves_help_and_dispatch_identity(self) -> None:
        facade_parser = cli.build_parser()
        extracted_parser = cli_parser.build_parser(cli)
        self.assertEqual(facade_parser.format_help(), extracted_parser.format_help())
        arguments = facade_parser.parse_args(
            ["inspect-sd-package", "package.bin", "--policy", "bootstrap"]
        )
        self.assertIs(arguments.handler, cli._inspect_sd)

    def test_inspect_install_set_validates_exact_artifact_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            artifacts = {
                cli.DEFAULT_SD_NAME: b"bootstrap",
                "THINGINO2.BIN": b"stage2",
                "stage1-bootstrap.squashfs": b"rootfs",
            }
            for name, raw in artifacts.items():
                (root / name).write_bytes(raw)
            (root / "install-set.manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "artifacts": {
                            name: {
                                "size": len(raw),
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            }
                            for name, raw in artifacts.items()
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = mock.Mock(
                data_mode="initialize",
                data_flash_span=1_507_328,
                system_flash_span=6_619_136,
            )
            output = io.StringIO()
            with (
                mock.patch.object(cli, "validate_install_set", return_value=payload),
                contextlib.redirect_stdout(output),
            ):
                cli._inspect_install_set(argparse.Namespace(install_set_dir=root))
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["data_mode"], "initialize")
            self.assertEqual(set(result["artifact_sizes"]), set(artifacts))

    def test_stage_media_handler_activates_and_emits_one_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            package = root / "bootstrap.bin"
            package.write_bytes(generate_bootstrap(b"kernel", b"rootfs"))
            preflight = root / "preflight.json"
            preflight.write_text(
                json.dumps(
                    {
                        "ambiguous": False,
                        "capacity_bytes": 8_000_000_000,
                        "external": True,
                        "filesystem": "fat32",
                        "model": "TEST REMOVABLE MEDIA",
                        "mount_root": str(root.resolve()),
                        "physical": True,
                        "physical_device": "/dev/test-external-media",
                        "schema_version": 1,
                        "system_device": False,
                        "writable": True,
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli._stage_media(
                    argparse.Namespace(
                        package=package,
                        preflight=preflight,
                        mount_root=root,
                        confirm_physical_device="/dev/test-external-media",
                    )
                )
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"])
            self.assertTrue((root / cli.DEFAULT_SD_NAME).is_file())

    def test_verify_bundle_handler_emits_verified_contract(self) -> None:
        raw = b"signed bundle"
        validated = ValidatedFinalBundle(
            raw=raw,
            manifest={
                "preserved_mtd": [0, 4, 5],
                "write_order": ["data", "activation"],
            },
            members={},
        )
        output = io.StringIO()
        with (
            mock.patch.object(cli, "read_snapshot", return_value=raw),
            mock.patch.object(cli, "validate_final_bundle", return_value=validated),
            contextlib.redirect_stdout(output),
        ):
            cli._verify_final_bundle(
                argparse.Namespace(bundle=Path("bundle"), public_key=Path("key"))
            )
        result = json.loads(output.getvalue())
        self.assertEqual(result["preserved_mtd"], [0, 4, 5])
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())

    def test_private_config_handler_emits_no_wifi_or_credential_value(self) -> None:
        generated = mock.Mock(
            files=(
                "authorized_keys",
                "installer.credential",
                "webui-api.key",
                "wpa_supplicant.conf",
            ),
            output_dir=Path("private-run"),
        )
        output = io.StringIO()
        with (
            mock.patch.object(
                cli, "read_private_input", return_value=("private-ssid", "private-pass")
            ),
            mock.patch.object(cli, "read_authorized_key", return_value=b"normalized-key\n"),
            mock.patch.object(cli, "generate_private_config", return_value=generated),
            contextlib.redirect_stdout(output),
        ):
            cli._create_private_bootstrap_config(
                argparse.Namespace(
                    output_dir=Path("private-run"),
                    authorized_key=Path("id.pub"),
                    secrets_fd=7,
                    wpa_config=None,
                )
            )
        emitted = output.getvalue()
        self.assertNotIn("private-ssid", emitted)
        self.assertNotIn("private-pass", emitted)
        self.assertNotIn("credential", json.loads(emitted).keys())

    def test_legacy_migration_profile_handler_writes_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            old_set = root / "old"
            old_set.mkdir()
            (old_set / cli.DEFAULT_SD_NAME).write_bytes(b"old-bootstrap")
            (old_set / "THINGINO2.BIN").write_bytes(b"old-stage2")
            output_path = root / "migration.private.json"
            output = io.StringIO()
            with (
                mock.patch.object(
                    cli,
                    "build_legacy_migration_profile",
                    return_value=b'{"private":true}\n',
                ) as build_profile,
                contextlib.redirect_stdout(output),
            ):
                cli._create_legacy_migration_profile(
                    argparse.Namespace(old_install_set_dir=old_set, output=output_path)
                )
            build_profile.assert_called_once_with(
                bootstrap_name=cli.DEFAULT_SD_NAME,
                old_bootstrap_bytes=b"old-bootstrap",
                old_stage2_bytes=b"old-stage2",
            )
            self.assertEqual(output_path.read_bytes(), b'{"private":true}\n')
            self.assertEqual(output_path.stat().st_mode & 0o777, 0o600)
            emitted = json.loads(output.getvalue())
            self.assertTrue(emitted["ok"])
            self.assertTrue(emitted["private"])


if __name__ == "__main__":
    unittest.main()
