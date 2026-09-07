from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.macos_wifi import MacosWifiError, join_recovery_ap, join_station_wifi


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


class MacosWifiTests(unittest.TestCase):
    def test_station_secret_and_ssid_are_stdin_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "wpa_supplicant.conf"
            config.write_text(
                'network={\n\tssid="Private WiFi"\n\tpsk=' + "b" * 64 + "\n}\n",
                encoding="ascii",
            )
            config.chmod(0o600)
            helper = root / "station-helper"
            helper.write_text("helper", encoding="ascii")
            helper.chmod(0o700)
            completed = mock.Mock(returncode=0, stdout=b"associated\n", stderr=b"")
            with mock.patch(
                "installer.macos_wifi.subprocess.run", return_value=completed
            ) as run:
                result = join_station_wifi(wpa_config=config, helper=helper)
            arguments = run.call_args.args[0]
            self.assertEqual(arguments, [str(helper)])
            self.assertNotIn("Private WiFi", " ".join(arguments))
            self.assertNotIn("b" * 64, " ".join(arguments))
            self.assertEqual(
                run.call_args.kwargs["input"],
                b"507269766174652057694669\n" + b"b" * 64 + b"\n",
            )
            self.assertTrue(result["associated"])
            self.assertFalse(result["network_identity_logged"])

    def test_secret_is_stdin_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = private_session(root)
            helper = root / "helper"
            helper.write_text("helper", encoding="ascii")
            helper.chmod(0o700)
            completed = mock.Mock(
                returncode=0,
                stdout=b"joined DCS6100-1234abcd\n",
                stderr=b"",
            )
            with mock.patch(
                "installer.macos_wifi.subprocess.run", return_value=completed
            ) as run, mock.patch("installer.macos_wifi.wait_for_control") as wait:
                result = join_recovery_ap(session_dir=session, helper=helper)
            arguments = run.call_args.args[0]
            self.assertEqual(arguments, [str(helper)])
            self.assertNotIn("a" * 64, " ".join(arguments))
            self.assertEqual(
                run.call_args.kwargs["input"],
                b"DCS6100-1234abcd\n" + b"a" * 64 + b"\n",
            )
            self.assertTrue(result["associated"])
            wait.assert_called_once_with("192.168.88.1", timeout=20.0)

    def test_swift_source_runs_through_system_interpreter_with_stdin_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = private_session(root)
            helper = root / "helper.swift"
            helper.write_text("import CoreWLAN\n", encoding="ascii")
            completed = mock.Mock(
                returncode=0,
                stdout=b"joined DCS6100-1234abcd\n",
                stderr=b"",
            )
            with mock.patch(
                "installer.macos_wifi.subprocess.run", return_value=completed
            ) as run, mock.patch(
                "installer.macos_wifi.wait_for_control"
            ), mock.patch(
                "installer.macos_wifi.Path.is_file", return_value=True
            ), mock.patch(
                "installer.macos_wifi.os.access", return_value=True
            ):
                result = join_recovery_ap(session_dir=session, helper=helper)
            arguments = run.call_args.args[0]
            self.assertEqual(arguments[0], "/usr/bin/swift")
            self.assertEqual(arguments[-1], str(helper))
            self.assertNotIn("a" * 64, " ".join(arguments))
            self.assertEqual(result["transport"], "swift-stdin")

    def test_rejects_group_readable_session_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = private_session(root)
            (session / "media/RECOVERY/AP.PSK").chmod(0o640)
            helper = root / "helper"
            helper.write_text("helper", encoding="ascii")
            helper.chmod(0o700)
            with self.assertRaisesRegex(MacosWifiError, "file policy"):
                join_recovery_ap(session_dir=session, helper=helper)


if __name__ == "__main__":
    unittest.main()
