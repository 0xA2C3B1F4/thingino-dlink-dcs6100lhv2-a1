"""Application acceptance must reject a reachable but half-started camera."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from installer.recovery_ap.host import RecoveryApHostError, prove_thingino_application
from tests.test_recovery_ap_host import private_session

ROOT = Path(__file__).resolve().parents[1]
PASSED = {
    "schema_version": 1, "backend": "raptor", "application_gate": "passed",
    "webui_gate": "passed", "control_gate": "passed", "media_gate": "passed",
}


class ApplicationHostTests(unittest.TestCase):
    def test_response_is_validated_and_cannot_override_management_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            session = private_session(Path(directory))
            response = {**PASSED, "station_ipv4": "untrusted", "write_set": [3]}
            with patch("installer.recovery_ap.host._exchange", return_value=json.dumps(response).encode()) as exchange:
                result = prove_thingino_application(session_dir=session, station_ipv4="192.0.2.1")
            self.assertEqual(result["application_gate"], "passed")
            self.assertNotIn("station_ipv4", result)
            self.assertNotIn("write_set", result)
            self.assertEqual(exchange.call_args.kwargs["command"], "dlink-application-verify")

    def test_incomplete_or_missing_probe_cannot_pass(self):
        cases = [b"", b"not json", b"{}", b"[]", b"x" * 2049,
                 json.dumps({**PASSED, "control_gate": "failed"}).encode(),
                 json.dumps({**PASSED, "backend": "unknown"}).encode()]
        with tempfile.TemporaryDirectory() as directory:
            session = private_session(Path(directory))
            for raw in cases:
                with self.subTest(raw=raw[:40]), \
                     patch("installer.recovery_ap.host._exchange", return_value=raw), \
                     patch("installer.recovery_ap.host.time.monotonic", side_effect=[0, 61]):
                    with self.assertRaisesRegex(RecoveryApHostError, "application verification failed"):
                        prove_thingino_application(session_dir=session, station_ipv4="192.0.2.1")


class ApplicationScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "etc").mkdir()
        (self.root / "etc/raptor-media.conf").touch()
        self.state = self.root / "run/raptor-boot"
        self.state.mkdir(parents=True)
        (self.state / "ready").write_text("started\n")
        owners = "rvd rhd rod rsd ric rad rmr0 rmr1 rwd storage control".split()
        for pid, owner in enumerate(owners, start=100):
            (self.state / f"{owner}.pid").write_text(f"{pid}\n")
            proc = self.root / f"proc/{pid}"
            proc.mkdir(parents=True)
            binary = "rmr" if owner in {"rmr0", "rmr1"} else owner
            target = f"/usr/bin/{binary}"
            if owner in {"storage", "control"}:
                target = "/usr/sbin/thingino-controld"
            (proc / "exe").symlink_to(target)
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        curl = fake_bin / "curl"
        curl.write_text('''#!/bin/sh
for argument do url=$argument; done
case "$url" in *"${FAIL_PATH:-never-match}"*) exit 22 ;; esac
case "$url" in *"${FORBID_PATH:-never-match}"*) exit 99 ;; esac
case "$url" in
  */api/v1/auth/session) printf '%s' '{"control_api":{"name":"Thingino Control","version":1}}' ;;
  *snapshot*) printf '%s' "${SNAPSHOT_RESULT:-image/jpeg 1234}" ;;
esac
''')
        curl.chmod(0o755)
        raptorctl = fake_bin / "raptorctl"
        raptorctl.write_text('''#!/bin/sh
test "$#" -eq 2 && test "$1" = -j || exit 2
test "$2" = '{"daemon":"rvd","cmd":"get-stream-enabled","stream_id":1}' || exit 2
printf '%s' "${STREAM1_STATE:-{\\"status\\":\\"ok\\",\\"supported\\":true,\\"active_enabled\\":true,\\"configured_enabled\\":true}}"
''')
        raptorctl.chmod(0o755)
        source = (ROOT / "installer/templates/dlink-application-verify").read_text()
        source = source.replace("PATH=/bin:/sbin:/usr/bin:/usr/sbin", f"PATH={fake_bin}:/bin:/usr/bin")
        source = source.replace("/usr/bin/raptorctl", str(raptorctl))
        for prefix in ("/run/", "/proc/", "/etc/"):
            source = source.replace(prefix, str(self.root) + prefix)
        self.script = self.root / "verify.sh"
        self.script.write_text(source)

    def run_probe(self, **environment):
        return subprocess.run(["sh", str(self.script)], capture_output=True, text=True,
                              env={**os.environ, **environment}, timeout=5)

    def test_complete_startup_and_both_jpegs_pass(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), PASSED)

    def test_checked_disabled_substream_omits_only_its_owner_and_snapshot(self):
        rmr1_pid = (self.state / "rmr1.pid").read_text().strip()
        (self.state / "rmr1.pid").unlink()
        (self.root / f"proc/{rmr1_pid}/exe").unlink()
        state = json.dumps({
            "status": "ok", "supported": True,
            "configured_enabled": False, "active_enabled": False,
        }, separators=(",", ":"))
        result = self.run_probe(STREAM1_STATE=state, FORBID_PATH="snapshot?ch=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), PASSED)

    def test_unknown_or_pending_substream_state_fails_closed(self):
        for state in (
            '{"status":"error"}',
            '{"status":"ok","supported":true,"active_enabled":true,"configured_enabled":false}',
        ):
            with self.subTest(state=state):
                result = self.run_probe(STREAM1_STATE=state)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("stream1-state", result.stderr)

    def test_gateway_failure_is_not_management_success(self):
        result = self.run_probe(FAIL_PATH="https://127.0.0.1/")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("webui-ingress", result.stderr)

    def test_partial_startup_and_stale_pid_are_rejected(self):
        (self.state / "ready").unlink()
        self.assertIn("raptor-startup", self.run_probe().stderr)
        (self.state / "ready").write_text("started\n")
        rsd_pid = (self.state / "rsd.pid").read_text().strip()
        (self.root / f"proc/{rsd_pid}/exe").unlink()
        self.assertIn("rsd-process", self.run_probe().stderr)

    def test_missing_osd_owner_rejects_otherwise_ready_media(self):
        rod_pid = (self.state / "rod.pid").read_text().strip()
        (self.root / f"proc/{rod_pid}/exe").unlink()
        result = self.run_probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rod-process", result.stderr)

    def test_control_and_each_snapshot_must_respond(self):
        for path in ("auth/session", "snapshot?ch=0", "snapshot?ch=1"):
            with self.subTest(path=path):
                self.assertNotEqual(self.run_probe(FAIL_PATH=path).returncode, 0)
        for result in ("text/html 100", "image/jpeg 0", "image/jpeg nonsense"):
            with self.subTest(result=result):
                self.assertNotEqual(self.run_probe(SNAPSHOT_RESULT=result).returncode, 0)


if __name__ == "__main__":
    unittest.main()
