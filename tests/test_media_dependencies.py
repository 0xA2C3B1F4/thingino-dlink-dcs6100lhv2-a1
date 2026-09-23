"""Explicit transaction dependencies must not resolve through the media facade."""

from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
import typing
import unittest
from unittest import mock

from installer import media_install_set, media_transactions
from installer.media_preflight import MediaPreflight


class MediaDependencyTests(unittest.TestCase):
    def test_transaction_package_import_does_not_load_public_facade(self) -> None:
        output = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import installer.media_transactions; import sys; "
                "print('installer.media' in sys.modules)",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )
        self.assertEqual(output.strip(), "False")

    def test_all_transaction_annotations_resolve_without_caller_globals(self) -> None:
        for name in media_transactions.__all__:
            with self.subTest(name=name):
                function = getattr(media_transactions, name)
                hints = typing.get_type_hints(function)
                self.assertIn("return", hints)
                parameters = inspect.signature(function).parameters
                self.assertNotIn("facade", parameters)
                self.assertEqual(set(hints) - {"return"}, set(parameters))

    def test_injected_install_validator_stops_before_root_validation_or_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preflight = MediaPreflight("fixture-device", "fixture", 1, "fat32", root)
            failure = ValueError("fixture validation rejected")
            validate = mock.Mock(side_effect=failure)
            validate_root = mock.Mock()
            write = mock.Mock()
            sync = mock.Mock()
            with self.assertRaises(ValueError) as caught:
                media_install_set.stage_verified_install_set(
                    bootstrap_bytes=b"fixture bootstrap",
                    stage2_bytes=b"fixture stage 2",
                    manifest_bytes=b"fixture manifest",
                    root=root,
                    bootstrap_name="fixture-name",
                    preflight=preflight,
                    confirmed_physical_device=preflight.physical_device,
                    validate_install_set=validate,
                    validate_sd_root=validate_root,
                    _write_verified_temporary=write,
                    _sync_directory=sync,
                )
            self.assertIs(caught.exception, failure)
            validate.assert_called_once_with(
                bootstrap_bytes=b"fixture bootstrap",
                stage2_bytes=b"fixture stage 2",
                manifest_bytes=b"fixture manifest",
                bootstrap_name="fixture-name",
            )
            validate_root.assert_not_called()
            write.assert_not_called()
            sync.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
