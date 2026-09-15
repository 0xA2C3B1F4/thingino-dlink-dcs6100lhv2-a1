from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from installer.runtime_diagnostics import (
    RuntimeDiagnosticsError,
    record_hypothesis,
    store_runtime_snapshot,
    validate_runtime_snapshot,
)


def snapshot() -> dict[str, object]:
    return {
        "binary_identity": {
            "mtd3_sha256": "a" * 64,
            "rvd_sha256": "b" * 64,
            "source_commit": "c" * 40,
            "thingino_control_sha256": "d" * 64,
            "uhttpd_sha256": "e" * 64,
        },
        "control": {"admission_metrics_available": False},
        "kernel_media": {"error_count": 0},
        "memory": {
            "available_proxy_kib": 18000,
            "buffers_kib": 100,
            "cached_kib": 12000,
            "free_kib": 5000,
            "reclaimable_kib": 1000,
            "shmem_kib": 100,
            "total_kib": 37580,
        },
        "network": {"tcp_entries": 4, "udp_entries": 2},
        "observed_at": "2026-08-22T20:00:00Z",
        "processes": {},
        "raptor": {"media_ready": True},
        "schema_version": 1,
        "storage": {
            "data_jffs2_mounted": True,
            "data_sha256": "f" * 64,
            "overlay_reset_pending": False,
            "root_overlay_mounted": True,
            "split_layout": True,
            "system_sha256": "a" * 64,
        },
        "uhttpd": {"event_loop_metrics_available": False},
    }


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_target_snapshot_collects_memory_cpu_and_current_media_marker(self) -> None:
        script = Path(__file__).resolve().parents[1] / "installer/templates/dlink-runtime-snapshot"
        subprocess.run(["sh", "-n", str(script)], check=True)
        source = script.read_text(encoding="utf-8")
        for required in (
            "/run/raptor-boot/ready",
            '"cpu_ticks":%s',
            '"private_kib":%s',
            '"pss_kib":%s',
            '"shared_kib":%s',
            '"available_proxy_kib":%s',
            '"rmem_device_present":%s',
            '"rmem_reserved_kib":%s',
            '"tcp_established":%s',
            '"data_jffs2_mounted":%s',
            '"root_overlay_mounted":%s',
            'mtd4: 00170000 00008000 "data"',
            "/overlay/.thingino-factory-reset",
        ):
            self.assertIn(required, source)
        self.assertNotIn("/run/dlink-media.ready", source)
        self.assertNotIn("prudynt", source.lower())

    def test_snapshot_is_read_only_and_hypothesis_is_bound_to_it(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            stored = store_runtime_snapshot(
                store_root=root / "diagnostics",
                snapshot=snapshot(),
                fault_class="unknown",
                candidate_id="f" * 64,
            )
            self.assertTrue(Path(str(stored["path"])).is_file())
            entry = record_hypothesis(
                ledger_path=root / "diagnostics/hypotheses.json",
                snapshot_sha256=str(stored["snapshot_sha256"]),
                hypothesis_id="jpeg-demand",
                change_identity="1" * 40,
                result="rejected",
                evidence="the exact candidate retained the symptom",
            )
            self.assertEqual(entry["result"], "rejected")
            with self.assertRaisesRegex(RuntimeDiagnosticsError, "already rejected"):
                record_hypothesis(
                    ledger_path=root / "diagnostics/hypotheses.json",
                    snapshot_sha256=str(stored["snapshot_sha256"]),
                    hypothesis_id="jpeg-demand",
                    change_identity="1" * 40,
                    result="pending",
                    evidence="no new evidence",
                )
            retried = record_hypothesis(
                ledger_path=root / "diagnostics/hypotheses.json",
                snapshot_sha256="9" * 64,
                hypothesis_id="jpeg-demand",
                change_identity="1" * 40,
                result="pending",
                evidence="new binary identity proves the change is absent",
            )
            self.assertEqual(retried["snapshot_sha256"], "9" * 64)
            outcome = record_hypothesis(
                ledger_path=root / "diagnostics/hypotheses.json",
                snapshot_sha256="9" * 64,
                hypothesis_id="jpeg-demand",
                change_identity="1" * 40,
                result="supported",
                evidence="bounded A/B run removed the symptom",
            )
            self.assertEqual(outcome["result"], "supported")

    def test_legacy_snapshot_without_memory_section_remains_readable(self) -> None:
        legacy = snapshot()
        del legacy["memory"]
        with tempfile.TemporaryDirectory() as name:
            stored = store_runtime_snapshot(
                store_root=Path(name),
                snapshot=legacy,
                fault_class="unknown",
            )
        self.assertEqual(stored["schema_version"], 1)

    def test_unknown_snapshot_fields_fail_closed(self) -> None:
        changed = snapshot()
        changed["secret"] = "must not be accepted"
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(RuntimeDiagnosticsError, "wrong schema"):
                store_runtime_snapshot(
                    store_root=Path(name),
                    snapshot=changed,
                    fault_class="unknown",
                )

    def test_split_storage_requires_exact_flags_and_region_digests(self) -> None:
        changed = snapshot()
        storage = dict(changed["storage"])
        storage["data_sha256"] = None
        changed["storage"] = storage
        with self.assertRaisesRegex(RuntimeDiagnosticsError, "region identities"):
            validate_runtime_snapshot(changed)

        legacy = snapshot()
        legacy["storage"] = {
            "data_jffs2_mounted": False,
            "data_sha256": None,
            "overlay_reset_pending": False,
            "root_overlay_mounted": False,
            "split_layout": False,
            "system_sha256": "a" * 64,
        }
        self.assertFalse(validate_runtime_snapshot(legacy)["storage"]["split_layout"])

    def test_invalid_memory_metrics_fail_closed(self) -> None:
        changed = snapshot()
        changed["memory"] = {"free_kib": -1}
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(RuntimeDiagnosticsError, "memory metrics"):
                store_runtime_snapshot(
                    store_root=Path(name),
                    snapshot=changed,
                    fault_class="isp_rmem",
                )


if __name__ == "__main__":
    unittest.main()
