from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "components/thingino-control/scripts/camera_acceptance.sh"


class ThinginoControlDeviceScriptTests(unittest.TestCase):
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
            '"$app_js_sha" = "$installed_app_js_sha"',
            "WebUI still contains a CGI request path",
            "WebUI API key leaked into the process list",
            '\"same_origin_media\":\"passed\"',
            '\"schema_version\":1',
            "Thingino Control PID file is missing",
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
