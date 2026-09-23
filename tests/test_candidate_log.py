from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer.candidate_log import (
    RAPTOR_EFFECT_CHECKS,
    RAPTOR_REQUIRED_CHECKS,
    REQUIRED_CHECKS,
    CandidateLogError,
    candidate_status,
    create_candidate,
    decide_candidate,
    record_candidate_run,
)


class CandidateLogTests(unittest.TestCase):
    def _candidate(self, root: Path, *, raptor: bool = False) -> str:
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
                    "schema_version": 2 if raptor else 1,
                    **({"media_backend": "raptor"} if raptor else {}),
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

    def _record(
        self, root: Path, candidate_id: str, checks: dict,
        *, observed_at: str = "2026-09-13T16:00:00Z",
    ) -> dict:
        run = root / "run.json"
        run.write_text(json.dumps({
            "checks": checks,
            "observed_at": observed_at,
            "schema_version": 1,
        }), encoding="utf-8")
        return record_candidate_run(
            store_root=root / "candidates", candidate_id=candidate_id, run_path=run,
        )

    def test_latest_result_uses_observation_time_not_import_or_filename_order(self) -> None:
        for newest_status in ("passed", "failed"):
            with self.subTest(status=newest_status), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                candidate_id = self._candidate(root, raptor=True)
                check = "config.get_post"
                newest = {check: {"status": newest_status}}
                older = {check: {"status": "failed" if newest_status == "passed" else "passed"}}
                self._record(root, candidate_id, newest, observed_at="2026-09-01T10:00:00.1Z")
                self._record(root, candidate_id, older, observed_at="2026-09-01T10:00:00Z")
                status = candidate_status(store_root=root / "candidates", candidate_id=candidate_id)
                self.assertEqual(status["checks"][check], newest[check])
                self.assertEqual(check in status["failing_or_incomplete"], newest_status == "failed")

    def test_invalid_or_future_observations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            candidate_id = self._candidate(root, raptor=True)
            for observed in ("nonsense", "2026-09-01T10:00:00", "2026-02-30T10:00:00Z",
                             "2999-01-01T00:00:00Z", "2026-09-01T10:00:00+03:00"):
                with self.subTest(observed=observed), self.assertRaises(CandidateLogError):
                    self._record(root, candidate_id, {"config.get_post": {"status": "passed"}},
                                 observed_at=observed)
            self.assertEqual(candidate_status(
                store_root=root / "candidates", candidate_id=candidate_id,
            )["run_count"], 0)

    def test_equal_time_conflicts_are_rejected_and_identical_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            candidate_id = self._candidate(root, raptor=True)
            checks = {"config.get_post": {"status": "failed"}}
            self.assertTrue(self._record(root, candidate_id, checks)["created"])
            self.assertFalse(self._record(root, candidate_id, checks)["created"])
            with self.assertRaisesRegex(CandidateLogError, "conflicting results"):
                self._record(root, candidate_id, {"config.get_post": {"status": "passed"}},
                             observed_at="2026-09-13T16:00:00+00:00")
            self.assertEqual(candidate_status(
                store_root=root / "candidates", candidate_id=candidate_id,
            )["checks"]["config.get_post"]["status"], "failed")
            path = next((root / "candidates" / candidate_id / "runs").glob("*.json"))
            altered = json.loads(path.read_text())
            altered["checks"]["config.get_post"]["status"] = "passed"
            (path.parent / "conflict.json").write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(CandidateLogError, "conflicting results"):
                candidate_status(store_root=root / "candidates", candidate_id=candidate_id)

    def test_raptor_matrix_requires_webrtc_restart_and_field_effects(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            candidate_id = self._candidate(root, raptor=True)
            document = json.loads((root / "candidates" / candidate_id / "candidate.json").read_text())
            self.assertEqual(document["schema_version"], 2)
            self.assertEqual(document["required_checks"], list(RAPTOR_REQUIRED_CHECKS))
            self.assertNotIn("runtime.prudynt_restart", document["required_checks"])
            checks = {
                check: {"status": "passed", "device_effect_observed": True, "evidence": "fixture"}
                for check in RAPTOR_REQUIRED_CHECKS if check not in RAPTOR_EFFECT_CHECKS
            }
            self._record(root, candidate_id, checks)
            status = candidate_status(store_root=root / "candidates", candidate_id=candidate_id)
            self.assertFalse(status["complete"])
            self.assertEqual(set(status["missing"]), RAPTOR_EFFECT_CHECKS)
            with self.assertRaisesRegex(CandidateLogError, "every required check"):
                decide_candidate(store_root=root / "candidates", candidate_id=candidate_id,
                                 decision="accepted", reason="only common checks passed")
            with self.assertRaisesRegex(CandidateLogError, "unknown check"):
                self._record(root, candidate_id, {"runtime.prudynt_restart": {"status": "passed"}})
            for check in RAPTOR_EFFECT_CHECKS:
                for state in ("passed", "manual_observation"):
                    with self.assertRaisesRegex(CandidateLogError, "observed device effect"):
                        self._record(root, candidate_id, {check: {"status": state, "evidence": "HTTP 200"}})
            self._record(root, candidate_id, {
                check: {"status": "passed", "device_effect_observed": True, "evidence": "fixture"}
                for check in RAPTOR_EFFECT_CHECKS
            })
            self.assertTrue(candidate_status(store_root=root / "candidates", candidate_id=candidate_id)["complete"])
            self.assertEqual(decide_candidate(
                store_root=root / "candidates", candidate_id=candidate_id,
                decision="accepted", reason="complete fixture matrix",
            )["decision"], "accepted")

    def test_raptor_profile_has_a_distinct_identity_without_changing_legacy_checks(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            legacy = self._candidate(root)
            raptor = self._candidate(root, raptor=True)
            self.assertEqual(
                legacy,
                "db7de39371f146404ee1e14deded991df7d70bfae487103b43033e101478c486",
            )
            self.assertNotEqual(legacy, raptor)
            legacy_status = candidate_status(store_root=root / "candidates", candidate_id=legacy)
            self.assertEqual(legacy_status["missing"], list(REQUIRED_CHECKS))
            self.assertNotIn("config.field_matrix", legacy_status["missing"])

    def test_record_cannot_downgrade_its_bound_profile_or_required_checks(self) -> None:
        for changed in ("identity", "required_checks", "schema_version"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                candidate_id = self._candidate(root, raptor=True)
                path = root / "candidates" / candidate_id / "candidate.json"
                document = json.loads(path.read_text())
                if changed == "identity":
                    document["identity"].pop("media_backend")
                    document["identity"]["schema_version"] = 1
                elif changed == "required_checks":
                    document["required_checks"].remove("config.field_matrix")
                else:
                    document["schema_version"] = 1
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(CandidateLogError):
                    candidate_status(store_root=root / "candidates", candidate_id=candidate_id)
                with self.assertRaises(CandidateLogError):
                    create_candidate(store_root=root / "candidates", specification=root / "candidate-spec.json")

    def test_unsupported_backend_and_inexact_schema_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self._candidate(root, raptor=True)
            path = root / "candidate-spec.json"
            original = json.loads(path.read_text())
            for fields in ({"media_backend": "prudynt"}, {"media_backend": None},
                           {"schema_version": 1}, {"schema_version": True}, {"extra": 1}):
                with self.subTest(fields=fields):
                    path.write_text(json.dumps({**original, **fields}), encoding="utf-8")
                    with self.assertRaises(CandidateLogError):
                        create_candidate(store_root=root / "candidates", specification=path)

    def test_status_revalidates_persisted_run_checks_and_candidate_identity(self) -> None:
        for changed in ("candidate", "status", "effect"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                candidate_id = self._candidate(root, raptor=True)
                self._record(root, candidate_id, {
                    "runtime.raptor_restart": {"status": "passed", "device_effect_observed": True},
                })
                path = next((root / "candidates" / candidate_id / "runs").glob("*.json"))
                document = json.loads(path.read_text())
                if changed == "candidate":
                    document["candidate_id"] = "0" * 64
                elif changed == "status":
                    document["checks"]["runtime.raptor_restart"]["status"] = "unrecognized"
                else:
                    document["checks"]["runtime.raptor_restart"]["device_effect_observed"] = False
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(CandidateLogError):
                    candidate_status(store_root=root / "candidates", candidate_id=candidate_id)

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
