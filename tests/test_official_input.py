from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from installer.official_input import (
    OfficialInputError,
    require_stock_kernel_rootfs,
    validate_official_input,
)


class OfficialInputTests(unittest.TestCase):
    def _catalog(self, directory: Path, payload: bytes) -> Path:
        catalog = directory / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "target": {
                        "model": "DCS-6100LHV2",
                        "hardware_revision": "A1",
                    },
                    "approved_inputs": [
                        {
                            "filename": "official.bin",
                            "format": "encrypted-application-update",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size": len(payload),
                            "version": "test",
                            "stock_recovery_components": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return catalog

    def test_exact_identity_passes_but_stock_recovery_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            payload = b"approved test payload"
            input_path = directory / "official.bin"
            input_path.write_bytes(payload)
            source = validate_official_input(
                input_path, catalog_path=self._catalog(directory, payload)
            )
            self.assertEqual(source.snapshot, payload)
            with self.assertRaisesRegex(OfficialInputError, "no restorable stock"):
                require_stock_kernel_rootfs(source)

    def test_changed_bytes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            input_path = directory / "official.bin"
            input_path.write_bytes(b"changed")
            with self.assertRaisesRegex(OfficialInputError, "digest changed"):
                validate_official_input(
                    input_path,
                    catalog_path=self._catalog(directory, b"approved"),
                )


if __name__ == "__main__":
    unittest.main()
