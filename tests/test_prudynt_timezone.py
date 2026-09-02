from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRUDYNT_PATCH = (
    ROOT / "patches/prudynt/0015-reload-camera-timezone.patch"
).read_text(encoding="utf-8")
STARTUP_PATCH = (
    ROOT / "patches/thingino/0017-pass-camera-timezone-to-prudynt.patch"
).read_text(encoding="utf-8")


class PrudyntTimezoneTests(unittest.TestCase):
    def test_live_reload_passes_a_filtered_timezone_environment_to_execve(self) -> None:
        self.assertIn('std::fopen("/etc/TZ", "r")', PRUDYNT_PATCH)
        self.assertIn('std::strncmp(*entry, "TZ=", 3)', PRUDYNT_PATCH)
        self.assertIn('environment.emplace_back("TZ=" + timezone)', PRUDYNT_PATCH)
        self.assertIn('execve("/proc/self/exe"', PRUDYNT_PATCH)
        self.assertNotIn("setenv(", PRUDYNT_PATCH)
        self.assertNotIn("unsetenv(", PRUDYNT_PATCH)
        self.assertNotIn("tzset(", PRUDYNT_PATCH)
        self.assertNotIn("fork(", PRUDYNT_PATCH)
        self.assertNotIn("system(", PRUDYNT_PATCH)

    def test_startup_reads_the_camera_file_without_a_helper(self) -> None:
        added = "\n".join(
            line[1:]
            for line in STARTUP_PATCH.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("IFS= read -r TZ_VALUE < /etc/TZ", STARTUP_PATCH)
        self.assertIn("export TZ", STARTUP_PATCH)
        self.assertIn("unset TZ", STARTUP_PATCH)
        self.assertNotIn("curl", added)
        self.assertNotIn("cat /etc/TZ", added)

    @unittest.skipUnless(hasattr(time, "tzset"), "platform has no tzset")
    def test_posix_timezone_drives_strftime_percent_z(self) -> None:
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "EET-2EEST,M3.5.0/3,M10.5.0/4"
            time.tzset()
            epoch = datetime(2026, 8, 23, 10, 34, 56, tzinfo=timezone.utc).timestamp()
            rendered = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(epoch))
            self.assertEqual(rendered, "2026-08-23 13:34:56 EEST")
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()


if __name__ == "__main__":
    unittest.main()
