from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "components/thingino-control/integration/S95thingino-control"


class ThinginoControlInitTests(unittest.TestCase):
    def test_script_has_valid_shell_syntax_and_bounded_owned_pid_lifecycle(self) -> None:
        subprocess.run(["sh", "-n", str(INIT)], check=True)
        source = INIT.read_text(encoding="utf-8")
        for required in (
            "DAEMON=/usr/sbin/thingino-controld",
            "PIDFILE=/run/thingino-controld.pid",
            "WORKER_PIDFILE=/run/thingino-storage-worker.pid",
            "WORKER_SOCKET=/run/thingino-control/storage-v1.sock",
            '[ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$DAEMON" ]',
            '--camera --listen "127.0.0.1:$port" --token-file "$CONFIG"',
            'endpoint="0100007F:$hex_port"',
            'running_pid && port_listening && return 0',
            "port=1998",
            'while [ "$i" -lt 50 ]',
            'kill -9 "$pid"',
            '-- --storage-worker',
            'worker_running_pid && [ -S "$WORKER_SOCKET" ] && return 0',
        ):
            self.assertIn(required, source)
        for forbidden in ("pkill", "killall", "curl", "thingino-agentctl"):
            self.assertNotIn(forbidden, source)

    def test_script_rejects_unknown_actions(self) -> None:
        result = subprocess.run(
            ["sh", str(INIT), "unknown"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
