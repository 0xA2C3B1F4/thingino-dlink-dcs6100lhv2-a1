from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import workflow_preflight


ROOT = Path(__file__).resolve().parents[1]


class WorkflowPreflightTests(unittest.TestCase):
    def test_explicit_station_ipv4_uses_pinned_session_without_mdns(self) -> None:
        session = SimpleNamespace(station_mdns_name="dcs6100-1234abcd.local")

        def probe(*, session_dir: Path, host: str, expected_state: str) -> dict[str, object]:
            self.assertEqual(session_dir, Path("/private/session"))
            if expected_state == "ap":
                raise workflow_preflight.RecoveryApHostError("AP is not active")
            self.assertEqual(host, "192.0.2.123")
            return {"mtd3_sha256": "4" * 64, "state": "station"}

        with (
            mock.patch.object(workflow_preflight, "load_host_session", return_value=session),
            mock.patch.object(workflow_preflight, "probe_recovery_ap", side_effect=probe),
            mock.patch.object(
                workflow_preflight, "resolve_recovery_ap_station_candidates"
            ) as resolve,
        ):
            result = workflow_preflight.observe_camera_state(
                session_dir=Path("/private/session"),
                station_ipv4="192.0.2.123",
            )

        resolve.assert_not_called()
        self.assertEqual(result["classification"], "station")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(
            result["observations"][0]["discovery"], "explicit-private-ipv4"
        )
        self.assertEqual(result["attempts"][0]["result"], "rejected")
        self.assertEqual(result["attempts"][1]["result"], "authenticated")

    def test_personal_build_requires_independent_current_station_wifi(self) -> None:
        def git_result(_root: Path, *arguments: str) -> str:
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return "a" * 40
            if arguments == ("status", "--short"):
                return ""
            raise AssertionError(arguments)

        sealed = b'network={\nssid="current"\npsk=' + b"a" * 64 + b"\n}\n"
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            expected = root / "current-wpa.conf"
            expected.write_bytes(sealed)
            expected.chmod(0o600)
            private_dir = root / "private"
            private_dir.mkdir(mode=0o700)
            (private_dir / "wpa_supplicant.conf").write_bytes(sealed)
            (private_dir / "wpa_supplicant.conf").chmod(0o600)
            common = {
                "project_root": ROOT,
                "mode": "build-personal-mtd3",
                "data_volume": Path("/"),
                "session_dir": root / "session",
                "vendor_bundle_dir": root / "vendor",
                "private_config_dir": private_dir,
                "output_dir": root / "candidate",
                "work_dir": root / "work",
            }
            with (
                mock.patch.object(workflow_preflight, "_git", side_effect=git_result),
                mock.patch.object(workflow_preflight, "load_host_session"),
                mock.patch.object(
                    workflow_preflight,
                    "load_vendor_bundle",
                    return_value=SimpleNamespace(bundle_sha256="b" * 64),
                ),
                mock.patch.object(
                    workflow_preflight,
                    "load_private_config_for_session",
                    return_value=SimpleNamespace(
                        credential_set_id="c" * 64,
                        wpa_config=sealed,
                    ),
                ),
            ):
                missing = workflow_preflight.run_workflow_preflight(**common)
                ready = workflow_preflight.run_workflow_preflight(
                    **common,
                    expected_wpa_config_path=expected,
                )
                with mock.patch.object(
                    workflow_preflight,
                    "load_private_wpa_config",
                    return_value=sealed.replace(b"current", b"stale__"),
                ):
                    mismatch = workflow_preflight.run_workflow_preflight(
                        **common,
                        expected_wpa_config_path=expected,
                    )

        self.assertIn("station-wifi-confirmation-required", missing["failures"])
        self.assertTrue(ready["ok"])
        self.assertIn("--expected-wpa-config", ready["next_command"])
        self.assertIn("station-wifi-confirmation-mismatch", mismatch["failures"])

    def test_clean_production_build_returns_one_canonical_command(self) -> None:
        def git_result(_root: Path, *arguments: str) -> str:
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return "a" * 40
            if arguments == ("status", "--short"):
                return ""
            raise AssertionError(arguments)

        with mock.patch.object(workflow_preflight, "_git", side_effect=git_result):
            result = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="production-build",
                data_volume=Path("/"),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["next_command"],
            "python3 scripts/source_checkout.py validate-lock",
        )
        self.assertEqual(result["safe_next_action"], "run-next-command")

    def test_dirty_collector_build_keeps_production_clean_gate_intact(self) -> None:
        def git_result(_root: Path, *arguments: str) -> str:
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return "a" * 40
            if arguments == ("status", "--short"):
                return " M installer/collector/kernel.py"
            raise AssertionError(arguments)

        with mock.patch.object(workflow_preflight, "_git", side_effect=git_result):
            collector = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="collector-build",
                data_volume=Path("/"),
            )
            production = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="production-build",
                data_volume=Path("/"),
            )

        self.assertTrue(collector["ok"])
        self.assertEqual(
            collector["next_command"],
            "python3 scripts/source_checkout.py validate-lock",
        )
        self.assertFalse(collector["authorization_required_before_next_command"])
        self.assertIn(
            "production-build-requires-clean-worktree", production["failures"]
        )

    def test_dirty_production_build_and_ram_mode_stop(self) -> None:
        def git_result(_root: Path, *arguments: str) -> str:
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return "a" * 40
            if arguments == ("status", "--short"):
                return " M installer/user_cli.py"
            raise AssertionError(arguments)

        with mock.patch.object(workflow_preflight, "_git", side_effect=git_result):
            production = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="production-build",
                data_volume=Path("/"),
            )
            ram = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="ram-candidate",
                data_volume=Path("/"),
            )
        self.assertIn("production-build-requires-clean-worktree", production["failures"])
        self.assertIn("live-ram-command-not-implemented", ram["failures"])
        self.assertIsNone(ram["next_command"])

    def test_recovery_observation_command_keeps_project_and_data_volume(self) -> None:
        def git_result(_root: Path, *arguments: str) -> str:
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return "a" * 40
            if arguments == ("status", "--short"):
                return ""
            raise AssertionError(arguments)

        with (
            mock.patch.object(workflow_preflight, "_git", side_effect=git_result),
            mock.patch.object(workflow_preflight, "load_host_session"),
        ):
            result = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="recovery-preflight",
                data_volume=Path("/"),
                session_dir=Path("/private/session"),
            )

        self.assertTrue(result["ok"])
        self.assertIn(f"--project-root {ROOT}", result["next_command"])
        self.assertIn("--data-volume /", result["next_command"])
        self.assertIn("--observe-camera", result["next_command"])

    def test_runtime_candidate_requires_bound_artifacts_then_station_observation(self) -> None:
        def git_result(_root: Path, *arguments: str) -> str:
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(ROOT)
            if arguments == ("rev-parse", "HEAD"):
                return "a" * 40
            if arguments == ("status", "--short"):
                return ""
            raise AssertionError(arguments)

        candidate = {
            "rootfs_sha256": "1" * 64,
            "rootfs_size": 100,
            "prudynt_sha256": "2" * 64,
            "prudynt_size": 80,
            "control_sha256": "3" * 64,
            "control_size": 90,
            "nor_writes": False,
        }
        session = Path("/private/session")
        rootfs = Path("/private/candidate.squashfs")
        provenance = Path("/private/final-root.private.json")
        station = {
            "classification": "station",
            "observations": [
                {
                    "host": "192.0.2.20",
                    "state": "station",
                    "proof": {"mtd3_sha256": "4" * 64},
                }
            ],
        }
        with (
            mock.patch.object(workflow_preflight, "_git", side_effect=git_result),
            mock.patch.object(workflow_preflight, "load_host_session"),
            mock.patch.object(
                workflow_preflight,
                "build_runtime_candidate_package",
                return_value=(b"archive", candidate),
            ),
        ):
            host_only = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="runtime-candidate",
                data_volume=Path("/"),
                session_dir=session,
                rootfs_path=rootfs,
                provenance_path=provenance,
                station_ipv4="192.0.2.123",
            )
            live_ready = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="runtime-candidate",
                data_volume=Path("/"),
                session_dir=session,
                rootfs_path=rootfs,
                provenance_path=provenance,
                camera_observation=station,
            )
            recovery = workflow_preflight.run_workflow_preflight(
                project_root=ROOT,
                mode="runtime-candidate",
                data_volume=Path("/"),
                session_dir=session,
                rootfs_path=rootfs,
                provenance_path=provenance,
                camera_observation={
                    "classification": "recovery-ap",
                    "observations": [{"host": "192.168.88.1", "state": "recovery-ap"}],
                },
            )
        self.assertTrue(host_only["ok"])
        self.assertIn("--observe-camera", host_only["next_command"])
        self.assertIn(f"--project-root {ROOT}", host_only["next_command"])
        self.assertIn("--data-volume /", host_only["next_command"])
        self.assertIn("--station-ipv4 192.0.2.123", host_only["next_command"])
        self.assertTrue(host_only["authorization_required_before_next_command"])
        self.assertTrue(live_ready["ok"])
        self.assertIn("runtime-candidate stage", live_ready["next_command"])
        self.assertIn("--expected-mtd3-sha256", live_ready["next_command"])
        self.assertIn("--approve-volatile-runtime-restart", live_ready["next_command"])
        self.assertTrue(live_ready["authorization_required_before_next_command"])
        self.assertIn("runtime-candidate-requires-station", recovery["failures"])
        self.assertIsNone(recovery["next_command"])


if __name__ == "__main__":
    unittest.main()
