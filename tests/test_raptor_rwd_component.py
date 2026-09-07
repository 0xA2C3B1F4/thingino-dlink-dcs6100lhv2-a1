import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "raptor-rwd"
PATCHES = ROOT / "patches" / "raptor"


class RaptorRwdComponentTests(unittest.TestCase):
    def test_component_uses_release_paths_and_provenance_names(self) -> None:
        self.assertFalse((ROOT / "experiments" / "prudynt-rwd").exists())
        policy = (ROOT / "policy" / "public-tree.json").read_text()
        self.assertIn('"components/raptor-rwd/README.md"', policy)
        self.assertNotIn("experiments/prudynt-rwd", policy)
        for path in (
            COMPONENT / "S13prudynt-rwd",
            COMPONENT / "build_persistent.py",
            ROOT / "installer" / "stage1" / "build.py",
            ROOT / "scripts" / "raptor_rwd_runtime.py",
        ):
            source = path.read_text()
            self.assertNotIn("prudynt-rwd-poc", source)
            self.assertNotIn("webrtc_poc", source)
            self.assertNotIn("WEBRTC_POC", source)

    def test_source_pins_are_immutable(self) -> None:
        lock = json.loads((COMPONENT / "raptor-lock.json").read_text())
        sources = lock["sources"]
        expected = {"compy", "mbedtls", "raptor", "raptor-common", "raptor-hal", "raptor-ipc"}
        build_closure = {"compy", "mbedtls", "raptor", "raptor-common", "raptor-ipc"}
        self.assertEqual(set(sources), expected)
        self.assertEqual(set(lock["source_urls"]), expected)
        self.assertEqual(set(lock["license_sha256"]), expected)
        self.assertEqual(set(lock["build_closure"]), build_closure)
        self.assertEqual(set(lock["excluded_sources"]), {"raptor-hal"})
        for name in sorted(expected):
            self.assertRegex(sources[name], r"^[0-9a-f]{40}$")
            self.assertRegex(lock["source_urls"][name], r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$")
            self.assertRegex(lock["license_sha256"][name], r"^[0-9a-f]{64}$")

    def test_config_is_one_client_video_only(self) -> None:
        config = (COMPONENT / "raptor.conf").read_text()
        self.assertIn("video_only = true", config)
        self.assertIn("signaling_loopback = true", config)
        self.assertIn("https = false", config)
        self.assertIn("max_clients = 1", config)
        self.assertNotIn("password =", config)

    def test_rwd_patch_has_no_media_owner(self) -> None:
        patch = (PATCHES / "0001-rwd-video-only-whip-cleanup.patch").read_text()
        self.assertTrue(patch.startswith("diff --git "))
        self.assertNotIn("From: ", patch)
        self.assertNotIn("Subject: ", patch)
        self.assertIn("video_only", patch)
        self.assertIn("signaling_loopback", patch)
        self.assertIn("INADDR_LOOPBACK", patch)
        added = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("Access-Control-Allow-Origin", added)
        self.assertNotIn("IMP_System_Init", patch)
        self.assertNotIn("rvd_main", patch)

    def test_rwd_runtime_patch_uses_bundled_dtls_and_balances_ring_reader(self) -> None:
        patch = (PATCHES / "0002-rwd-ingenic-runtime-and-ring-reader.patch").read_text()
        self.assertTrue(patch.startswith("diff --git "))
        self.assertNotIn("From: ", patch)
        self.assertNotIn("Subject: ", patch)
        self.assertIn("filter-out $(RUNTIME_RPATH)", patch)
        self.assertIn('mbedtls_platform_dev_random = "/dev/urandom"', patch)
        self.assertEqual(patch.count("rss_ring_acquire"), 2)
        self.assertEqual(patch.count("rss_ring_release"), 3)
        self.assertNotIn("IMP_System_Init", patch)

    def test_supervisor_orders_consumer_before_owner_teardown(self) -> None:
        script = (COMPONENT / "S13prudynt-rwd").read_text()
        stop_body = script.split("stop_runtime() {", 1)[1].split("case ", 1)[0]
        self.assertLess(stop_body.index("stop_rwd"), stop_body.index("PRUDYNT_PID"))
        self.assertIn("PRUDYNT_RAPTOR_RING=1", script)
        self.assertIn('nohup "$RWD" -f -c "$RUNTIME_CONF"', script)
        self.assertIn('ip -4 -o addr show scope global', script)
        self.assertIn('>"$ROOT/rwd.log" 2>&1 &', script)
        self.assertIn('$4 == "127.0.0.1:8554"', script)
        self.assertIn("loopback && !unsafe", script)
        self.assertIn("PATH=/bin:/sbin:/usr/bin:/usr/sbin", script)
        self.assertIn("umask 077", script)
        self.assertIn("/:8443$/", script)
        self.assertNotIn("flashcp", script)
        self.assertNotIn("/dev/mtd", script)


if __name__ == "__main__":
    unittest.main()
