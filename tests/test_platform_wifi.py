from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.platform_wifi import join_recovery_ap, join_station_wifi


def private_session(root: Path) -> Path:
    session = root / "session"
    (session / "host").mkdir(parents=True)
    (session / "media/RECOVERY").mkdir(parents=True)
    manifest = session / "host/session.json"
    manifest.write_text(
        json.dumps(
            {
                "ap_address": "192.168.88.1",
                "schema_version": 1,
                "setup_ssid": "DCS6100-1234abcd",
            }
        ),
        encoding="ascii",
    )
    manifest.chmod(0o600)
    psk = session / "media/RECOVERY/AP.PSK"
    psk.write_text("a" * 64 + "\n", encoding="ascii")
    psk.chmod(0o600)
    return session


class PlatformWifiTests(unittest.TestCase):
    def test_windows_uses_bounded_manual_join_without_exposing_key(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session = private_session(Path(name))
            notices: list[str] = []
            with mock.patch("installer.platform_wifi.wait_for_control") as wait:
                result = join_recovery_ap(
                    session_dir=session,
                    macos_helper=Path("unused"),
                    platform="win32",
                    manual_timeout=120,
                    notify=notices.append,
                )
            wait.assert_called_once_with("192.168.88.1", timeout=120)
            self.assertEqual(result["transport"], "manual-system-settings")
            self.assertIn("DCS6100-1234abcd", notices[0])
            self.assertNotIn("a" * 64, notices[0])

    def test_linux_uses_private_keyfile_and_no_secret_argv(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session = private_session(Path(name))
            calls: list[list[str]] = []
            modes: list[int] = []

            def run(arguments: list[str], **_: object) -> mock.Mock:
                calls.append(arguments)
                if "load" in arguments:
                    keyfile = Path(arguments[-1])
                    modes.append(keyfile.stat().st_mode & 0o777)
                    self.assertIn("psk=" + "a" * 64, keyfile.read_text(encoding="ascii"))
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with (
                mock.patch("installer.platform_wifi.shutil.which", return_value="/usr/bin/nmcli"),
                mock.patch("installer.platform_wifi.subprocess.run", side_effect=run),
                mock.patch("installer.platform_wifi.wait_for_control"),
                mock.patch.dict(os.environ, {"TMPDIR": name}),
            ):
                result = join_recovery_ap(
                    session_dir=session,
                    macos_helper=Path("unused"),
                    platform="linux",
                )
            argv = "\n".join(" ".join(call) for call in calls)
            self.assertNotIn("a" * 64, argv)
            self.assertNotIn("DCS6100-1234abcd", argv)
            self.assertEqual(modes, [0o600])
            self.assertEqual(result["transport"], "networkmanager-keyfile")
            self.assertEqual(list(Path(name).glob("*.nmconnection")), [])

    def test_manual_station_return_never_reads_or_prints_credentials(self) -> None:
        notices: list[str] = []
        result = join_station_wifi(
            wpa_config=Path("not-read-in-manual-mode"),
            macos_helper=Path("unused"),
            mode="manual",
            platform="linux",
            notify=notices.append,
        )
        self.assertFalse(result["associated"])
        self.assertEqual(result["transport"], "manual-system-settings-pending")
        self.assertEqual(len(notices), 1)


if __name__ == "__main__":
    unittest.main()
