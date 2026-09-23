"""Validate and persist bounded read-only runtime snapshots and hypotheses."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .sd_package import atomic_write


class RuntimeDiagnosticsError(ValueError):
    """A runtime snapshot or hypothesis violates its evidence contract."""


FAULT_CLASSES = {
    "uhttpd_event_loop",
    "control_workers",
    "raptor",
    "isp_rmem",
    "network",
    "browser",
    "unknown",
}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_error_count(section: dict[str, object], section_name: str) -> None:
    if "error_count" not in section:
        if "error_count_source" in section:
            raise RuntimeDiagnosticsError(
                f"runtime snapshot {section_name} error count is missing"
            )
        return
    count = section["error_count"]
    if count is not None and (
        not isinstance(count, int) or isinstance(count, bool) or count < 0
    ):
        raise RuntimeDiagnosticsError(
            f"runtime snapshot {section_name} error count is invalid"
        )
    if "error_count_source" not in section:
        return
    source = section["error_count_source"]
    if not isinstance(source, dict) or set(source) != {"path", "read_status"}:
        raise RuntimeDiagnosticsError(
            f"runtime snapshot {section_name} error source is invalid"
        )
    read_status = source.get("read_status")
    if (
        source.get("path") != "/var/log/messages"
        or not isinstance(read_status, str)
        or read_status not in {
            "missing",
            "read_error",
            "readable",
            "unreadable",
        }
    ):
        raise RuntimeDiagnosticsError(
            f"runtime snapshot {section_name} error source is invalid"
        )
    readable = read_status == "readable"
    if readable != (count is not None):
        raise RuntimeDiagnosticsError(
            f"runtime snapshot {section_name} error evidence is inconsistent"
        )


def validate_runtime_snapshot(document: dict[str, object]) -> dict[str, object]:
    required = {
        "binary_identity",
        "control",
        "kernel_media",
        "network",
        "observed_at",
        "processes",
        "raptor",
        "schema_version",
        "uhttpd",
    }
    optional = {"memory", "storage"}
    if (
        not required.issubset(document)
        or set(document) - required - optional
        or document.get("schema_version") != 1
    ):
        raise RuntimeDiagnosticsError("runtime snapshot has the wrong schema")
    if not isinstance(document.get("observed_at"), str) or not document["observed_at"]:
        raise RuntimeDiagnosticsError("runtime snapshot lacks observed_at")
    for name in ("binary_identity", "control", "kernel_media", "network", "processes", "raptor", "uhttpd"):
        if not isinstance(document.get(name), dict):
            raise RuntimeDiagnosticsError(f"runtime snapshot {name} section is invalid")
    if "memory" in document and not isinstance(document["memory"], dict):
        raise RuntimeDiagnosticsError("runtime snapshot memory section is invalid")
    if "storage" in document and not isinstance(document["storage"], dict):
        raise RuntimeDiagnosticsError("runtime snapshot storage section is invalid")
    identity = document["binary_identity"]
    assert isinstance(identity, dict)
    for name, value in identity.items():
        if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40,64}", value) is None):
            raise RuntimeDiagnosticsError(f"runtime binary identity is invalid: {name}")
    for name in ("kernel_media", "raptor"):
        section = document[name]
        assert isinstance(section, dict)
        _validate_error_count(section, name)
    memory = document.get("memory")
    if isinstance(memory, dict):
        expected_memory = {
            "available_proxy_kib",
            "buffers_kib",
            "cached_kib",
            "free_kib",
            "reclaimable_kib",
            "shmem_kib",
            "total_kib",
        }
        if set(memory) != expected_memory or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in memory.values()
        ):
            raise RuntimeDiagnosticsError("runtime snapshot memory metrics are invalid")
    storage = document.get("storage")
    if isinstance(storage, dict):
        expected_storage = {
            "data_jffs2_mounted",
            "data_sha256",
            "overlay_reset_pending",
            "root_overlay_mounted",
            "split_layout",
            "system_sha256",
        }
        if set(storage) != expected_storage:
            raise RuntimeDiagnosticsError("runtime snapshot storage section is invalid")
        for name in (
            "data_jffs2_mounted",
            "overlay_reset_pending",
            "root_overlay_mounted",
            "split_layout",
        ):
            if not isinstance(storage[name], bool):
                raise RuntimeDiagnosticsError("runtime snapshot storage flags are invalid")
        for name in ("data_sha256", "system_sha256"):
            value = storage[name]
            if value is not None and (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise RuntimeDiagnosticsError("runtime snapshot storage digest is invalid")
        if storage["split_layout"]:
            if (
                storage["system_sha256"] is None
                or storage["data_sha256"] is None
            ):
                raise RuntimeDiagnosticsError("split runtime lacks region identities")
        elif any(
            storage[name]
            for name in (
                "data_jffs2_mounted",
                "overlay_reset_pending",
                "root_overlay_mounted",
            )
        ) or storage["data_sha256"] is not None:
            raise RuntimeDiagnosticsError("legacy runtime claims split storage state")
    return document


def store_runtime_snapshot(
    *,
    store_root: Path,
    snapshot: dict[str, object],
    fault_class: str,
    candidate_id: str | None = None,
) -> dict[str, object]:
    if fault_class not in FAULT_CLASSES:
        raise RuntimeDiagnosticsError("runtime fault class is invalid")
    if candidate_id is not None and re.fullmatch(r"[0-9a-f]{64}", candidate_id) is None:
        raise RuntimeDiagnosticsError("runtime candidate ID is invalid")
    validated = validate_runtime_snapshot(snapshot)
    envelope = {
        "candidate_id": candidate_id,
        "fault_class": fault_class,
        "nor_writes": False,
        "snapshot": validated,
        "stored_at": _now(),
        "schema_version": 1,
    }
    digest = hashlib.sha256(_canonical(envelope)).hexdigest()
    store_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store_root, 0o700)
    destination = store_root / f"snapshot-{digest}.json"
    if not destination.exists():
        atomic_write(destination, _canonical(envelope) + b"\n")
        destination.chmod(0o600)
    return {
        "fault_class": fault_class,
        "path": str(destination),
        "schema_version": 1,
        "snapshot_sha256": digest,
    }


def record_hypothesis(
    *,
    ledger_path: Path,
    snapshot_sha256: str,
    hypothesis_id: str,
    change_identity: str,
    result: str,
    evidence: str,
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256) is None:
        raise RuntimeDiagnosticsError("hypothesis snapshot identity is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", hypothesis_id):
        raise RuntimeDiagnosticsError("hypothesis ID is invalid")
    if re.fullmatch(r"[0-9a-f]{40,64}", change_identity) is None:
        raise RuntimeDiagnosticsError("hypothesis change identity is invalid")
    if result not in {"pending", "supported", "rejected", "inconclusive"} or not evidence:
        raise RuntimeDiagnosticsError("hypothesis result or evidence is invalid")
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeDiagnosticsError("hypothesis ledger is invalid") from exc
    else:
        ledger = {"entries": [], "schema_version": 1}
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 1 or not isinstance(ledger.get("entries"), list):
        raise RuntimeDiagnosticsError("hypothesis ledger has the wrong schema")
    entries = ledger["entries"]
    assert isinstance(entries, list)
    duplicates = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("hypothesis_id") == hypothesis_id
        and entry.get("change_identity") == change_identity
    ]
    if duplicates:
        previous = duplicates[-1]
        previous_result = previous.get("result")
        if previous_result == "pending":
            if (
                result == "pending"
                or previous.get("snapshot_sha256") != snapshot_sha256
                or previous.get("evidence") == evidence
            ):
                raise RuntimeDiagnosticsError(
                    "the pending hypothesis outcome needs the same snapshot and new evidence"
                )
        elif previous_result in {"rejected", "inconclusive"}:
            if (
                previous.get("snapshot_sha256") == snapshot_sha256
                or previous.get("evidence") == evidence
            ):
                raise RuntimeDiagnosticsError(
                    "the same hypothesis and change were already rejected; "
                    "a different snapshot and new evidence are required"
                )
        else:
            raise RuntimeDiagnosticsError("the same hypothesis and change are already recorded")
    entry = {
        "change_identity": change_identity,
        "evidence": evidence,
        "hypothesis_id": hypothesis_id,
        "recorded_at": _now(),
        "result": result,
        "snapshot_sha256": snapshot_sha256,
    }
    entries.append(entry)
    ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(ledger_path, _canonical(ledger) + b"\n")
    ledger_path.chmod(0o600)
    return entry
