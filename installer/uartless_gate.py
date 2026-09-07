"""Fail-closed evidence gates for the UART-independent user path."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "profiles/dlink-dcs6100lhv2-a1/uartless-release-gate.json"
)
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ALLOWED_EVIDENCE_KINDS = {
    "artifact-manifest",
    "button-observation",
    "host-transcript",
    "led-observation",
    "network-capture-summary",
    "power-cycle-observation",
    "readback-manifest",
    "runtime-report",
}


class UartlessGateError(ValueError):
    """The supplied physical-test report cannot close the requested gate."""


@dataclass(frozen=True, slots=True)
class UartlessDecision:
    scope: str
    checks: tuple[str, ...]
    evidence_digests: tuple[str, ...]


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UartlessGateError(f"{label} is not an object")
    return value


def _exact(value: object, expected: object, label: str) -> None:
    if value != expected:
        raise UartlessGateError(f"{label} does not match the UARTless contract")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise UartlessGateError(f"{label} is not a regular file")
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UartlessGateError(f"{label} is not valid UTF-8 JSON") from exc


def load_uartless_contract() -> dict[str, Any]:
    contract = _load_json(CONTRACT_PATH, "UARTless contract")
    _exact(contract.get("schema_version"), 1, "contract schema")
    _exact(contract.get("profile"), "dlink-dcs6100lhv2-a1", "contract profile")
    return contract


def _validate_environment(report: dict[str, Any], contract: dict[str, Any]) -> None:
    _exact(report.get("schema_version"), 1, "report schema")
    _exact(report.get("profile"), contract["profile"], "report profile")
    target = _object(report.get("target"), "report target")
    expected_target = _object(contract["target"], "contract target")
    for field in ("model", "hardware_revision", "flash_size_bytes"):
        _exact(target.get(field), expected_target[field], f"target {field}")

    boundary = _object(report.get("user_path"), "user path")
    _exact(boundary.get("uart_dependency"), False, "UART dependency")
    _exact(boundary.get("external_spi_dependency"), False, "SPI dependency")
    _exact(boundary.get("opened_enclosure_dependency"), False, "enclosure dependency")
    _exact(
        boundary.get("control_interfaces"),
        contract["user_path"]["allowed_interfaces"],
        "user control interfaces",
    )
    if boundary.get("uart_observation") not in ("unused", "observation-only"):
        raise UartlessGateError("UART observation has an invalid role")
    _exact(report.get("contains_secrets"), False, "secret disclosure marker")


def _validate_evidence(
    report: dict[str, Any], required: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    entries = report.get("checks")
    if not isinstance(entries, list):
        raise UartlessGateError("checks is not a list")
    by_id: dict[str, dict[str, Any]] = {}
    for raw in entries:
        entry = _object(raw, "check")
        check_id = entry.get("id")
        if not isinstance(check_id, str) or check_id in by_id:
            raise UartlessGateError("check id is missing or duplicated")
        by_id[check_id] = entry
    if set(by_id) != set(required):
        raise UartlessGateError("report does not contain the exact required check set")

    digests: list[str] = []
    for check_id in required:
        entry = by_id[check_id]
        _exact(entry.get("result"), "pass", f"check {check_id}")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise UartlessGateError(f"check {check_id} has no evidence")
        for raw_item in evidence:
            item = _object(raw_item, f"check {check_id} evidence")
            if set(item) != {"kind", "sha256", "sanitized"}:
                raise UartlessGateError(f"check {check_id} evidence has unknown fields")
            if item["kind"] not in _ALLOWED_EVIDENCE_KINDS:
                raise UartlessGateError(f"check {check_id} evidence kind is not allowed")
            _exact(item["sanitized"], True, f"check {check_id} evidence sanitization")
            digest = item["sha256"]
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise UartlessGateError(f"check {check_id} evidence digest is invalid")
            digests.append(digest)
    return tuple(required), tuple(digests)


def validate_uartless_report(*, report_path: Path, scope: str) -> UartlessDecision:
    contract = load_uartless_contract()
    report = _load_json(report_path, "UARTless evidence report")
    _validate_environment(report, contract)

    if scope == "ap-feasibility":
        ap = _object(report.get("ap_feasibility"), "AP feasibility boundary")
        expected = _object(contract["ap_feasibility"], "AP feasibility contract")
        for field in (
            "boot_storage",
            "nor_write_count",
            "mtd_access",
            "wifi_chip",
            "ap_security",
            "control_transport",
            "setup_secret_source",
            "station_credentials_storage",
        ):
            _exact(ap.get(field), expected[field], f"AP feasibility {field}")
        required = expected["required_checks"]
    elif scope == "release":
        release = _object(report.get("release"), "release boundary")
        _exact(release.get("physical_test"), True, "physical release test")
        _exact(release.get("published_installer_path"), True, "published installer path")
        required = contract["release"]["required_checks"]
    else:
        raise UartlessGateError("unknown UARTless gate scope")

    checks, digests = _validate_evidence(report, required)
    return UartlessDecision(scope=scope, checks=checks, evidence_digests=digests)
