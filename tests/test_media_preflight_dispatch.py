from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts.platform.media_preflight import PreflightError, create_preflight_document


class MediaPreflightDispatchTests(unittest.TestCase):
    def test_selects_each_reviewed_adapter(self) -> None:
        cases = (
            ("darwin", "scripts.platform.macos_media_preflight.create_preflight_document"),
            ("linux", "scripts.platform.linux_media_preflight.create_preflight_document"),
            ("win32", "scripts.platform.windows_media_preflight.create_preflight_document"),
        )
        for platform_name, target in cases:
            with self.subTest(platform=platform_name), mock.patch(
                target, return_value={"host_platform": platform_name}
            ) as create:
                result = create_preflight_document(
                    whole_device="device",
                    mount_root=Path("mount"),
                    platform_name=platform_name,
                )
                self.assertEqual(result["host_platform"], platform_name)
                create.assert_called_once()

    def test_rejects_unknown_platform(self) -> None:
        with self.assertRaisesRegex(PreflightError, "no reviewed"):
            create_preflight_document(
                whole_device="device",
                mount_root=Path("mount"),
                platform_name="plan9",
            )


if __name__ == "__main__":
    unittest.main()
