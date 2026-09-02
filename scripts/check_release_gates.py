#!/usr/bin/env python3
"""Validate the machine-readable source and firmware release gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "policy/release-gates.json"
IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATUSES = {"blocked", "closed"}


class ReleaseGateError(ValueError):
    """The release-gate ledger is malformed or overclaims readiness."""


def _exact_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ReleaseGateError(f"{label} fields are not exact")


def _evidence_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
        raise ReleaseGateError(f"unsafe evidence path: {relative}")
    candidates = [root.joinpath(*pure.parts)]
    if (
        pure.parts[0] == "docs"
        and (root / "policy/production-export.json").is_file()
    ):
        candidates.append(root / "production" / Path(*pure.parts))
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            return path
    raise ReleaseGateError(f"release evidence is missing or not regular: {relative}")


def validate_document(
    document: dict[str, object], *, root: Path = ROOT
) -> dict[str, dict[str, object]]:
    _exact_keys(document, {"schema_version", "status_date", "scopes"}, "policy")
    if document["schema_version"] != 1:
        raise ReleaseGateError("unsupported release-gate schema")
    status_date = document["status_date"]
    if not isinstance(status_date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", status_date) is None:
        raise ReleaseGateError("release-gate status_date is invalid")
    scopes = document["scopes"]
    if not isinstance(scopes, list) or not scopes:
        raise ReleaseGateError("release-gate scopes must be a non-empty list")

    summaries: dict[str, dict[str, object]] = {}
    for scope_index, raw_scope in enumerate(scopes):
        if not isinstance(raw_scope, dict):
            raise ReleaseGateError(f"scope {scope_index} is not an object")
        _exact_keys(raw_scope, {"id", "ready", "gates"}, f"scope {scope_index}")
        scope_id = raw_scope["id"]
        ready = raw_scope["ready"]
        gates = raw_scope["gates"]
        if not isinstance(scope_id, str) or IDENTIFIER.fullmatch(scope_id) is None:
            raise ReleaseGateError(f"scope {scope_index} id is invalid")
        if scope_id in summaries:
            raise ReleaseGateError(f"duplicate release scope: {scope_id}")
        if not isinstance(ready, bool):
            raise ReleaseGateError(f"scope {scope_id} ready flag is invalid")
        if not isinstance(gates, list) or not gates:
            raise ReleaseGateError(f"scope {scope_id} gates must be a non-empty list")

        gate_ids: set[str] = set()
        blockers: list[str] = []
        for gate_index, raw_gate in enumerate(gates):
            if not isinstance(raw_gate, dict):
                raise ReleaseGateError(f"scope {scope_id} gate {gate_index} is not an object")
            _exact_keys(
                raw_gate,
                {"id", "status", "requirement", "evidence"},
                f"scope {scope_id} gate {gate_index}",
            )
            gate_id = raw_gate["id"]
            status = raw_gate["status"]
            requirement = raw_gate["requirement"]
            evidence = raw_gate["evidence"]
            if not isinstance(gate_id, str) or IDENTIFIER.fullmatch(gate_id) is None:
                raise ReleaseGateError(f"scope {scope_id} gate id is invalid")
            if gate_id in gate_ids:
                raise ReleaseGateError(f"duplicate gate in {scope_id}: {gate_id}")
            gate_ids.add(gate_id)
            if status not in STATUSES:
                raise ReleaseGateError(f"gate {scope_id}/{gate_id} status is invalid")
            if not isinstance(requirement, str) or not requirement.strip():
                raise ReleaseGateError(f"gate {scope_id}/{gate_id} requirement is empty")
            if not isinstance(evidence, list) or not evidence or not all(
                isinstance(item, str) for item in evidence
            ):
                raise ReleaseGateError(f"gate {scope_id}/{gate_id} evidence is invalid")
            if evidence != sorted(set(evidence)):
                raise ReleaseGateError(f"gate {scope_id}/{gate_id} evidence is not sorted and unique")
            for relative in evidence:
                _evidence_path(root, relative)
            if status == "blocked":
                blockers.append(gate_id)

        calculated_ready = not blockers
        if ready != calculated_ready:
            raise ReleaseGateError(
                f"scope {scope_id} ready={str(ready).lower()} disagrees with its gates"
            )
        summaries[scope_id] = {
            "blocked": blockers,
            "closed": len(gates) - len(blockers),
            "ready": ready,
            "total": len(gates),
        }
    return summaries


def validate(*, root: Path = ROOT, policy_path: Path = POLICY_PATH) -> dict[str, dict[str, object]]:
    document = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ReleaseGateError("release-gate policy must be an object")
    return validate_document(document, root=root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", metavar="SCOPE")
    arguments = parser.parse_args()
    try:
        summaries = validate()
        if arguments.require is not None:
            summary = summaries.get(arguments.require)
            if summary is None:
                raise ReleaseGateError(f"unknown release scope: {arguments.require}")
            if not summary["ready"]:
                print(
                    json.dumps(
                        {"blocked": summary["blocked"], "ok": False, "scope": arguments.require},
                        sort_keys=True,
                    )
                )
                return 1
        print(json.dumps({"ok": True, "scopes": summaries}, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ReleaseGateError) as exc:
        print(f"release-gate check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
