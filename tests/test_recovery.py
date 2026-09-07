from __future__ import annotations

import json
import unittest

from installer.recovery import build_stage1_recovery
from installer.sd_package import parse_package, validate_bootstrap
from test_artifacts import test_squashfs, test_uimage


class RecoveryTests(unittest.TestCase):
    def test_stage1_recovery_is_not_mislabelled_as_stock(self) -> None:
        result = build_stage1_recovery(test_uimage(), test_squashfs())
        validate_bootstrap(parse_package(result.package, require_project_header=True))
        manifest = json.loads(result.manifest)
        self.assertEqual(result.kind, "stage1-recovery")
        self.assertFalse(manifest["recovery"]["stock_restore"])
        self.assertEqual(manifest["recovery"]["restores"], ["mtd1", "mtd2"])
        self.assertFalse(manifest["writes_mtd0"])


if __name__ == "__main__":
    unittest.main()
