from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "components/thingino-control/scripts/camera_acceptance.sh"


class ThinginoControlDeviceScriptTests(unittest.TestCase):
    def test_pidfile_selection_fails_closed_across_boot_owners(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        function = source.split("select_control_pidfile() {\n", 1)[1].split("\n}\n", 1)[0]
        shell = f"select_control_pidfile() {{\n{function}\n}}\nselect_control_pidfile \"$1\" \"$2\""
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            root = Path(directory)
            state = root / "raptor-boot"
            legacy = root / "thingino-controld.pid"

            def select() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["sh", "-c", shell, "sh", str(state), str(legacy)],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            legacy_selection = select()
            self.assertEqual(legacy_selection.returncode, 0)
            self.assertEqual(legacy_selection.stdout.strip(), str(legacy))
            state.mkdir()
            self.assertNotEqual(select().returncode, 0)
            (state / "ready").write_text("started\n", encoding="utf-8")
            raptor_selection = select()
            self.assertEqual(raptor_selection.returncode, 0)
            self.assertEqual(raptor_selection.stdout.strip(), str(state / "control.pid"))
            legacy.write_text("123\n", encoding="utf-8")
            self.assertNotEqual(select().returncode, 0)

    def test_script_is_bounded_and_keeps_token_out_of_argv(self) -> None:
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "requests=${1:-1000}",
            'requests" -le 10000',
            "--config \"$curl_config\"",
            "--config \"$web_curl_config\"",
            "web_origin=${2:-https://127.0.0.1}",
            'web_request "$web_origin/api/v1/health"',
            'web_request "$web_origin/api/v1/actions/snapshot?stream_id=1"',
            'web_request "$web_origin/onvif/image.cgi"',
            'web_request "$web_origin/onvif/image1.cgi"',
            '"$web_origin/assets/app.js"',
            "/var/www/assets/app.js",
            'expect_equal web_asset_sha "$app_js_sha" "$installed_app_js_sha"',
            "WebUI still contains a CGI request path",
            "WebUI API key leaked into the process list",
            '\"same_origin_media\":\"passed\"',
            '\"schema_version\":1',
            "Thingino Control PID file is missing",
            'readlink "/proc/$control_pid/exe"',
            "grep -Fqx -- '--camera'",
            'expect_unsupported_config direct_config "$curl_config"',
            'expect_unsupported_config web_config "$web_curl_config"',
            '"message":"backend is unavailable"',
            'expect_http onvif_main_api_key_rejection 401',
            'expect_http onvif_sub_api_key_rejection 401',
            '"config_get":"%s"',
            "Control token leaked into process arguments",
            '\"tmp_control_files_after\":%s',
        ):
            self.assertIn(required, source)
        for forbidden in (
            '-H "Authorization: Bearer $token"',
            "mtd",
            "flash_erase",
            "reboot",
            "pkill",
            "killall",
            "/a/main.js",
            '-X POST "$web_origin/api/v1/actions/snapshot',
            "request 'http://127.0.0.1:1998/api/v1/actions/snapshot",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
