from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer.uartless_gate import (
    UartlessGateError,
    load_uartless_contract,
    validate_uartless_report,
)


def report(scope: str) -> dict[str, object]:
    contract = load_uartless_contract()
    required = contract[
        "ap_feasibility" if scope == "ap-feasibility" else "release"
    ]["required_checks"]
    document: dict[str, object] = {
        "schema_version": 1,
        "profile": "dlink-dcs6100lhv2-a1",
        "target": {
            "model": "DCS-6100LHV2",
            "hardware_revision": "A1",
            "flash_size_bytes": 16777216,
        },
        "user_path": {
            "control_interfaces": [
                "power",
                "sd-card",
                "reset-button",
                "status-leds",
                "local-setup-ap",
            ],
            "uart_dependency": False,
            "uart_observation": "observation-only",
            "external_spi_dependency": False,
            "opened_enclosure_dependency": False,
        },
        "contains_secrets": False,
        "checks": [
            {
                "id": check,
                "result": "pass",
                "evidence": [
                    {
                        "kind": "host-transcript",
                        "sha256": f"{index + 1:064x}",
                        "sanitized": True,
                    }
                ],
            }
            for index, check in enumerate(required)
        ],
    }
    if scope == "ap-feasibility":
        document["ap_feasibility"] = {
            key: value
            for key, value in contract["ap_feasibility"].items()
            if key != "required_checks"
        }
    else:
        document["release"] = {
            "physical_test": True,
            "published_installer_path": True,
        }
    return document


def validate(document: dict[str, object], scope: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "report.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        validate_uartless_report(report_path=path, scope=scope)


class UartlessGateTests(unittest.TestCase):
    def test_ram_only_ap_report_closes_only_the_ap_gate(self) -> None:
        document = report("ap-feasibility")
        decision = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            decision = validate_uartless_report(
                report_path=path,
                scope="ap-feasibility",
            )
        self.assertEqual(decision.scope, "ap-feasibility")
        self.assertEqual(len(decision.checks), 9)

    def test_uart_dependency_is_always_rejected(self) -> None:
        document = report("ap-feasibility")
        document["user_path"]["uart_dependency"] = True  # type: ignore[index]
        with self.assertRaisesRegex(UartlessGateError, "UART dependency"):
            validate(document, "ap-feasibility")

    def test_ap_test_rejects_any_nor_write(self) -> None:
        document = report("ap-feasibility")
        document["ap_feasibility"]["nor_write_count"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(UartlessGateError, "nor_write_count"):
            validate(document, "ap-feasibility")

    def test_missing_wrong_wifi_fallback_evidence_is_rejected(self) -> None:
        document = report("ap-feasibility")
        document["checks"] = [  # type: ignore[assignment]
            entry
            for entry in document["checks"]  # type: ignore[union-attr]
            if entry["id"] != "station_failure_returned_to_ap"
        ]
        with self.assertRaisesRegex(UartlessGateError, "exact required check set"):
            validate(document, "ap-feasibility")

    def test_release_requires_real_published_installer_path(self) -> None:
        document = report("release")
        document["release"]["published_installer_path"] = False  # type: ignore[index]
        with self.assertRaisesRegex(UartlessGateError, "published installer path"):
            validate(document, "release")

    def test_release_requires_corrupt_and_interrupted_mtd3_recovery(self) -> None:
        document = report("release")
        document["checks"] = [  # type: ignore[assignment]
            entry
            for entry in document["checks"]  # type: ignore[union-attr]
            if entry["id"] not in {
                "corrupt_mtd3_returned_to_setup_ap",
                "interrupted_mtd3_write_returned_to_setup_ap",
            }
        ]
        with self.assertRaisesRegex(UartlessGateError, "exact required check set"):
            validate(document, "release")


if __name__ == "__main__":
    unittest.main()
