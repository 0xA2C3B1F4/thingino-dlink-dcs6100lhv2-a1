"""Content-addressed release candidate records and acceptance decisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .sd_package import atomic_write


class CandidateLogError(ValueError):
    """A candidate or acceptance record violates the release contract."""


STATUSES = {"passed", "failed", "not_run", "blocked", "manual_observation"}
REQUIRED_CHECKS = (
    "auth.login_logout_expiry",
    "preview.mjpeg_stream0",
    "preview.mjpeg_stream1",
    "snapshot.stream0",
    "snapshot.stream1",
    "controls.preview_all",
    "controls.audio_motion_privacy_led_ir",
    "config.get_post",
    "recorder.start_stop",
    "protocol.rtsp",
    "protocol.onvif",
    "runtime.prudynt_restart",
    "concurrency.slow_disconnect_api",
    "resources.cpu_rss_threads_fds_connections",
    "kernel.media_errors",
    "browser.real_safari_or_chromium",
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise CandidateLogError(f"{label} is missing or not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateLogError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise CandidateLogError(f"{label} is invalid")
    return value


def _identity(spec: dict[str, object]) -> dict[str, object]:
    required = {
        "acceptance_conditions",
        "artifacts",
        "baseline_commit",
        "changes",
        "expected_effect",
        "schema_version",
        "source_commits",
        "stop_conditions",
    }
    if set(spec) != required or spec.get("schema_version") != 1:
        raise CandidateLogError("candidate specification has the wrong schema")
    for name in ("acceptance_conditions", "changes", "source_commits", "stop_conditions"):
        value = spec[name]
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            raise CandidateLogError(f"candidate {name} must be a non-empty string list")
    if not isinstance(spec["baseline_commit"], str) or not spec["baseline_commit"]:
        raise CandidateLogError("candidate baseline commit is missing")
    if not isinstance(spec["expected_effect"], str) or not spec["expected_effect"]:
        raise CandidateLogError("candidate expected effect is missing")
    artifacts = spec["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise CandidateLogError("candidate artifacts must be a non-empty list")
    normalized = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) not in ({"name", "path"}, {"name", "path", "sha256"}):
            raise CandidateLogError("candidate artifact entry has the wrong schema")
        name = item.get("name")
        path_value = item.get("path")
        if not isinstance(name, str) or not name or not isinstance(path_value, str):
            raise CandidateLogError("candidate artifact name or path is invalid")
        path = Path(path_value)
        if path.is_symlink() or not path.is_file():
            raise CandidateLogError(f"candidate artifact is missing: {name}")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if item.get("sha256") not in (None, digest):
            raise CandidateLogError(f"candidate artifact digest changed: {name}")
        normalized.append({"name": name, "sha256": digest, "size": len(raw)})
    return {**spec, "artifacts": sorted(normalized, key=lambda entry: str(entry["name"]))}


def create_candidate(*, store_root: Path, specification: Path) -> dict[str, object]:
    identity = _identity(_load(specification, "candidate specification"))
    candidate_id = hashlib.sha256(b"dcs6100-candidate-v1\0" + _canonical(identity)).hexdigest()
    root = store_root / candidate_id
    candidate_path = root / "candidate.json"
    document = {
        "candidate_id": candidate_id,
        "created_at": _now(),
        "identity": identity,
        "required_checks": list(REQUIRED_CHECKS),
        "schema_version": 1,
    }
    if candidate_path.exists():
        existing = _load(candidate_path, "candidate record")
        if existing.get("identity") != identity or existing.get("candidate_id") != candidate_id:
            raise CandidateLogError("candidate ID collision or changed record")
        return {"candidate_id": candidate_id, "created": False, "path": str(root)}
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    (root / "runs").mkdir(mode=0o700)
    atomic_write(candidate_path, (_canonical(document) + b"\n"))
    candidate_path.chmod(0o600)
    return {"candidate_id": candidate_id, "created": True, "path": str(root)}


def _candidate_root(store_root: Path, candidate_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", candidate_id) is None:
        raise CandidateLogError("candidate ID is invalid")
    root = store_root / candidate_id
    document = _load(root / "candidate.json", "candidate record")
    if document.get("candidate_id") != candidate_id:
        raise CandidateLogError("candidate record ID does not match its directory")
    return root


def record_candidate_run(
    *, store_root: Path, candidate_id: str, run_path: Path
) -> dict[str, object]:
    root = _candidate_root(store_root, candidate_id)
    run = _load(run_path, "candidate run")
    if set(run) - {"checks", "metrics", "notes", "observed_at", "schema_version"}:
        raise CandidateLogError("candidate run contains unknown fields")
    if run.get("schema_version") != 1 or not isinstance(run.get("checks"), dict):
        raise CandidateLogError("candidate run has the wrong schema")
    checks = run["checks"]
    assert isinstance(checks, dict)
    if not checks:
        raise CandidateLogError("candidate run must record at least one check")
    for check_id, result in checks.items():
        if check_id not in REQUIRED_CHECKS or not isinstance(result, dict):
            raise CandidateLogError("candidate run contains an unknown check")
        if result.get("status") not in STATUSES:
            raise CandidateLogError("candidate check status is invalid")
        if set(result) - {"status", "evidence", "note", "device_effect_observed"}:
            raise CandidateLogError("candidate check result contains unknown fields")
        if check_id.startswith("controls.") and result.get("status") in {"passed", "manual_observation"}:
            if result.get("device_effect_observed") is not True:
                raise CandidateLogError("control acceptance requires an observed device effect")
    observed_at = run.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        raise CandidateLogError("candidate run observed_at is required")
    document = {**run, "candidate_id": candidate_id}
    digest = hashlib.sha256(_canonical(document)).hexdigest()
    safe_time = re.sub(r"[^0-9A-Za-z]+", "", observed_at)[:20] or "run"
    destination = root / "runs" / f"{safe_time}-{digest[:12]}.json"
    if destination.exists():
        if _load(destination, "candidate run") != document:
            raise CandidateLogError("candidate run identity collision")
        return {"candidate_id": candidate_id, "created": False, "run_sha256": digest}
    atomic_write(destination, _canonical(document) + b"\n")
    destination.chmod(0o600)
    return {"candidate_id": candidate_id, "created": True, "run_sha256": digest}


def candidate_status(*, store_root: Path, candidate_id: str) -> dict[str, object]:
    root = _candidate_root(store_root, candidate_id)
    latest: dict[str, dict[str, object]] = {}
    runs = sorted((root / "runs").glob("*.json"))
    for path in runs:
        run = _load(path, "candidate run")
        checks = run.get("checks")
        if isinstance(checks, dict):
            for check_id, result in checks.items():
                if check_id in REQUIRED_CHECKS and isinstance(result, dict):
                    latest[check_id] = result
    missing = [check for check in REQUIRED_CHECKS if check not in latest]
    failing = [
        check
        for check, result in latest.items()
        if result.get("status") in {"failed", "not_run", "blocked"}
    ]
    complete = not missing and not failing
    return {
        "candidate_id": candidate_id,
        "checks": latest,
        "complete": complete,
        "failing_or_incomplete": sorted(failing),
        "missing": missing,
        "run_count": len(runs),
        "schema_version": 1,
    }


def decide_candidate(
    *, store_root: Path, candidate_id: str, decision: str, reason: str
) -> dict[str, object]:
    if decision not in {"accepted", "rejected"} or not reason:
        raise CandidateLogError("candidate decision or reason is invalid")
    root = _candidate_root(store_root, candidate_id)
    status = candidate_status(store_root=store_root, candidate_id=candidate_id)
    if decision == "accepted" and status["complete"] is not True:
        raise CandidateLogError("candidate cannot be accepted before every required check passes")
    document = {
        "candidate_id": candidate_id,
        "decided_at": _now(),
        "decision": decision,
        "reason": reason,
        "schema_version": 1,
        "status": status,
    }
    destination = root / "decision.json"
    if destination.exists():
        existing = _load(destination, "candidate decision")
        if existing.get("decision") != decision or existing.get("reason") != reason:
            raise CandidateLogError("candidate already has a different decision")
        return existing
    atomic_write(destination, _canonical(document) + b"\n")
    destination.chmod(0o600)
    return document
