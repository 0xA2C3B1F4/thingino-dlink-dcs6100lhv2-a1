from __future__ import annotations

import unittest

from installer import cli


class InstallerContractTests(unittest.TestCase):
    def test_persistent_mtd3_commands_require_image_provenance(self) -> None:
        parser = cli.build_parser()
        for command in ("install-personal-mtd3", "reconcile-camera-state"):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        command,
                        "--session-dir",
                        "/private/session",
                        "--image",
                        "/private/image",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
