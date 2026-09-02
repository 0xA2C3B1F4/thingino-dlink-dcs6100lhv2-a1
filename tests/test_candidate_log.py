from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer.candidate_log import (
    REQUIRED_CHECKS,
    CandidateLogError,
    candidate_status,
    create_candidate,
    decide_candidate,
    record_candidate_run,
)


class CandidateLogTests(unittest.TestCase):
    def _candidate(self, root: Path) -> str:
        artifact = root / "candidate-artifact.txt"
        artifact.write_text("exact candidate bytes\n", encoding="utf-8")
        spec = root / "candidate-spec.json"
        spec.write_text(
            json.dumps(
                {
                    "acceptance_conditions": ["all required checks pass"],
                    "artifacts": [{"name": "host-fixture", "path": str(artifact)}],
                    "baseline_commit": "0" * 40,
                    "changes": ["one bounded change"],
                    "expected_effect": "the bounded symptom is removed",
                    "schema_version": 1,
                    "source_commits": ["1" * 40],
                    "stop_conditions": ["resource limit exceeded"],
                }
            ),
            encoding="utf-8",
        )
        first = create_candidate(store_root=root / "candidates", specification=spec)
        second = create_candidate(store_root=root / "candidates", specification=spec)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["candidate_id"], second["candidate_id"])
        return str(first["candidate_id"])

    def test_same_bytes_reuse_candidate_and_runs_complete_the_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            candidate_id = self._candidate(root)
            run = root / "run.json"
            checks = {}
            for check_id in REQUIRED_CHECKS:
                result: dict[str, object] = {"status": "passed", "evidence": "fixture"}
                if check_id.startswith("controls."):
                    result["device_effect_observed"] = True
                checks[check_id] = result
            run.write_text(
                json.dumps(
                    {
                        "checks": checks,
                        "observed_at": "2026-08-22T20:00:00Z",
                        "schema_version": 1,
                    }
                ),
                encoding="utf-8",
            )
            recorded = record_candidate_run(
                store_root=root / "candidates",
                candidate_id=candidate_id,
                run_path=run,
            )
            self.assertTrue(recorded["created"])
            self.assertTrue(
                candidate_status(
                    store_root=root / "candidates", candidate_id=candidate_id
                )["complete"]
            )
            decision = decide_candidate(
                store_root=root / "candidates",
                candidate_id=candidate_id,
                decision="accepted",
                reason="all required checks passed",
            )
            self.assertEqual(decision["decision"], "accepted")

    def test_control_pass_requires_observed_device_effect(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            candidate_id = self._candidate(root)
            run = root / "run.json"
            run.write_text(
                json.dumps(
                    {
                        "checks": {"controls.preview_all": {"status": "passed"}},
                        "observed_at": "2026-08-22T20:00:00Z",
                        "schema_version": 1,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CandidateLogError, "observed device effect"):
                record_candidate_run(
                    store_root=root / "candidates",
                    candidate_id=candidate_id,
                    run_path=run,
                )

    def test_incomplete_candidate_cannot_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            candidate_id = self._candidate(root)
            with self.assertRaisesRegex(CandidateLogError, "every required check"):
                decide_candidate(
                    store_root=root / "candidates",
                    candidate_id=candidate_id,
                    decision="accepted",
                    reason="too early",
                )


if __name__ == "__main__":
    unittest.main()
