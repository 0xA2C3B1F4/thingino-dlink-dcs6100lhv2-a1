from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from installer import functional_stock_restore as subject
from installer.recovery import RecoveryError
from test_stock_recovery import stock_parts
from test_artifacts import test_squashfs, test_uimage
from installer.stock_restore import set as restore_set
from installer.stock_restore.kernel import (
    render_stock_restore_kernel_fragment, stock_restore_kernel_command_line,
)


class FunctionalStockRestoreTests(unittest.TestCase):
    def setUp(self):
        self.current = stock_parts()
        self.source = dict(self.current)
        self.source[5] = b"different source factory"
        self.source[3] = b"different source application data"
        self.capture = self.manifest(self.current)
        self.stock = self.manifest(self.source)

    @staticmethod
    def manifest(parts):
        return {"files": {f"copy-a/mtd{i}.bin": {
            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        } for i, raw in parts.items()}}

    def plan(self, **overrides):
        args = dict(recovery_dir=Path("current"), preserved_readback_dir=Path("readback"),
                    stock_source_dir=Path("stock"), expected_camera_identity="a" * 64,
                    expected_capture_identity=subject.manifest_identity(self.capture),
                    expected_stock_source_identity=subject.manifest_identity(self.stock))
        args.update(overrides)
        with patch.object(subject, "validate_functional_backup_with_manifest",
                          return_value=(None, self.capture)), \
             patch.object(subject, "validate_complete_backup_with_manifest",
                          return_value=(None, self.stock)), \
             patch.object(subject, "validate_functional_recovery_boundary",
                          return_value=SimpleNamespace(camera_identity_sha256="a" * 64)), \
             patch.object(subject, "_partition_snapshot", side_effect=lambda root, i:
                          (self.current if root == Path("current") else self.source)[i]):
            return subject.plan_functional_stock_restore(**args)

    def test_uses_only_current_application_and_protected_bytes(self):
        result = self.plan()
        self.assertEqual(result.mtd3, self.current[3])
        self.assertEqual(result.protected, tuple(self.current[i] for i in (0, 4, 5)))
        self.assertEqual(result.mtd1, self.source[1])
        self.assertEqual(result.mtd2, self.source[2])
        self.assertFalse(result.summary()["original_complete_backup_accepted"])
        self.assertFalse(result.summary()["media_prepared"])
        self.assertEqual(result.summary()["write_set"], [])
        self.assertNotIn("aaaaaaaa", repr(result))

    def test_wrong_camera(self):
        with self.assertRaisesRegex(RecoveryError, "camera selection"):
            self.plan(expected_camera_identity="b" * 64)

    def test_wrong_source_selection(self):
        with self.assertRaisesRegex(RecoveryError, "source selection"):
            self.plan(expected_stock_source_identity="b" * 64)

    def test_wrong_capture_selection(self):
        with self.assertRaisesRegex(RecoveryError, "source selection"):
            self.plan(expected_capture_identity="b" * 64)

    def test_changed_input_after_validation(self):
        self.current[5] = b"changed"
        with self.assertRaisesRegex(RecoveryError, "changed after validation"):
            self.plan()

    def test_incompatible_vendor(self):
        self.source[4] = b"other vendor"
        self.stock = self.manifest(self.source)
        with self.assertRaisesRegex(RecoveryError, "compatibility"):
            self.plan()

    def test_modified_application_not_stock(self):
        self.current[3] = b"not stock"
        self.capture = self.manifest(self.current)
        with self.assertRaises(RecoveryError):
            self.plan()

    def test_incompatible_bootloader(self):
        self.source[0] = b"other bootloader"
        self.stock = self.manifest(self.source)
        with self.assertRaisesRegex(RecoveryError, "compatibility"):
            self.plan()

    def test_invalid_selection_identity(self):
        with self.assertRaisesRegex(RecoveryError, "identity is invalid"):
            self.plan(expected_camera_identity="")

    def test_changed_kernel_after_validation(self):
        self.source[1] = b"changed kernel"
        with self.assertRaisesRegex(RecoveryError, "changed after validation"):
            self.plan()

    def test_bootstrap_roundtrip_binds_functional_origin(self):
        plan = self.plan()
        selection = subject.FunctionalStockSelection(
            Path("stock"), plan.camera_identity, plan.capture_identity,
            plan.stock_source_identity,
        )
        config = render_stock_restore_kernel_fragment()
        kernel = test_uimage(stock_restore_kernel_command_line().encode("ascii"),
                             entry_point=0x80010100)
        def build_root(**kwargs):
            kwargs["output"].write_bytes(test_squashfs())
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(subject.FunctionalStockSelection, "validate", return_value=plan), \
             patch.object(restore_set, "build_stock_restore_root", side_effect=build_root):
            args = dict(recovery_dir=Path("current"), preserved_readback_dir=Path("readback"),
                        restore_output_dir=Path("unused-legacy-plan"),
                        output_dir=Path(tmp) / "prepared", linux_config=config,
                        functional_selection=selection)
            prepared = restore_set.prepare_stock_restore_bootstrap_set(
                **args, kernel=kernel, mmc_module=b"fixture module",
                authorization=b"T" * 32,
            )
            checked = restore_set.inspect_stock_restore_bootstrap_set(**args)
            self.assertEqual(checked.files, prepared.files)
            self.assertEqual(checked.images.protected, plan.protected)
            self.assertNotIn(restore_set.BOOTSTRAP_ACTIVE_NAME, prepared.files)
            manifest_path = args["output_dir"] / restore_set.BOOTSTRAP_MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text())
            self.assertFalse(manifest["origin"]["original_complete_backup_accepted"])
            self.assertEqual(manifest["origin"], plan.private_origin())
            manifest["origin"]["stock_source_identity"] = "b" * 64
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(restore_set.StockRestoreSetError, "provenance"):
                restore_set.inspect_stock_restore_bootstrap_set(**args)

    def test_exact_original_default_does_not_dispatch_to_functional(self):
        with patch.object(restore_set, "inspect_same_device_stock_restore",
                          side_effect=RecoveryError("legacy rejection")) as legacy:
            with self.assertRaisesRegex(RecoveryError, "legacy rejection"):
                restore_set._bootstrap_restore_plan(Path("a"), Path("b"), Path("c"), None)
            legacy.assert_called_once()

    def test_private_selection_file_and_permissions(self):
        doc = dict(schema_version=1, stock_source_dir="/private-stock-source",
                   expected_camera_identity="a" * 64, expected_capture_identity="b" * 64,
                   expected_stock_source_identity="c" * 64)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "selection.json"
            path.write_text(json.dumps(doc))
            path.chmod(0o600)
            result = subject.load_functional_stock_selection(path)
            self.assertEqual(result.expected_capture_identity, "b" * 64)
            path.chmod(0o644)
            with self.assertRaises(RecoveryError):
                subject.load_functional_stock_selection(path)
            path.chmod(0o600)
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaises(RecoveryError):
                subject.load_functional_stock_selection(path)
            path.write_text(json.dumps(doc | {"stock_source_dir": "relative"}))
            with self.assertRaises(RecoveryError):
                subject.load_functional_stock_selection(path)

    def test_cli_loader_keeps_legacy_defaults_and_loads_explicit_selection(self):
        from installer.user_cli_stock_recovery import _functional_options
        self.assertEqual(_functional_options(SimpleNamespace()), {})
        with patch.object(subject, "load_functional_stock_selection", return_value="selected") as load:
            self.assertEqual(_functional_options(SimpleNamespace(functional_stock_selection=Path("private"))),
                             {"functional_selection": "selected"})
            load.assert_called_once_with(Path("private"))

    def test_cli_result_labels_functional_without_private_identities(self):
        from installer.user_cli_stock_recovery import _functional_result
        self.assertEqual(_functional_result(SimpleNamespace()), {})
        result = _functional_result(SimpleNamespace(functional_stock_selection=Path("private")))
        self.assertEqual(result["restoration_class"], "functional-stock")
        self.assertFalse(result["original_complete_backup_accepted"])
        self.assertNotIn("private", json.dumps(result))
