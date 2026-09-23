"""Execute the production shell wait with deterministic DHCP timing."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "components/raptor/S96raptor").read_text()
WAIT = SOURCE.split("wait_for_ipv4() {", 1)[1].split("\nstart_owner()", 1)[0]
WAIT = "wait_for_ipv4() {" + WAIT


class BootNetworkTests(unittest.TestCase):
    def run_wait(self, ready_after):
        # sleep advances a logical clock without changing production limits.
        # ip runs in command substitution, so it only reads that clock.
        script = """
set -eu
ticks=0
ip() {
    if [ "$ticks" -ge READY_AFTER ]; then
        printf '2: wlan0 inet 192.0.2.5/24 scope global wlan0\n'
    fi
}
sleep() { ticks=$((ticks + 1)); }
""".replace("READY_AFTER", str(ready_after))
        return subprocess.run(
            ["/bin/sh", "-c", script + WAIT + '\nwait_for_ipv4\nprintf "%s %s\\n" "$local_ip" "$ticks"'],
            text=True, capture_output=True, timeout=15,
        )

    def test_immediate_address_does_not_wait(self):
        result = self.run_wait(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "192.0.2.5 0\n")
        self.assertEqual(result.stderr, "")

    def test_delayed_dhcp_continues_without_restart(self):
        result = self.run_wait(12)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "192.0.2.5 12\n")
        self.assertEqual(result.stderr.count("waiting for local IPv4"), 1)

    def test_address_at_deadline_is_accepted(self):
        result = self.run_wait(300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "192.0.2.5 300\n")

    def test_timeout_stops_startup_before_success(self):
        result = self.run_wait(301)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("timed out after 300 seconds", result.stderr)

    def test_wait_precedes_webrtc_config_and_owners(self):
        start = SOURCE.split("\nstart() {", 1)[1].split("\nstop_owner()", 1)[0]
        self.assertLess(start.index("    wait_for_ipv4\n"), start.index('>"$state/webrtc.conf"'))
        self.assertLess(start.index('>"$state/webrtc.conf"'), start.index("start_owner rwd"))
        self.assertLess(start.index("start_owner rwd"), start.index('>"$state/ready"'))
