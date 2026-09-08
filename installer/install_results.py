"""Stable installation result documents shared by CLI and GUI callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypedDict
from .layout import TARGET

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class InstallationEvent:
    phase: str
    operation: str


EventSink = Callable[[InstallationEvent], None]


class InstallationResult(TypedDict):
    command: str
    ok: bool
    phase: str
    result: dict[str, object]
    error: str | None
    next_command: str | None
    nor: dict[str, object]
    physical_actions: list[str]
    schema_version: int
    target: dict[str, object]


def failure_details(error: BaseException) -> dict[str, object]:
    interrupted = isinstance(error, KeyboardInterrupt)
    return {
        "error_code": "interrupted" if interrupted else getattr(error, "code", "operation_rejected"),
        "missing_inputs": list(getattr(error, "missing", ())),
        "operation_outcome": "unknown-requires-inspection" if interrupted else "stopped",
        "automatic_retry": False,
        "physical_state": "not-observed",
    }


def _target(preserved_mtd: list[int] | None = None) -> dict[str, object]:
    return {
        "hardware_revision": TARGET.hardware_revision,
        "model": TARGET.model,
        "preserved_mtd": preserved_mtd or [0, 4, 5],
    }


def document(
    command: str,
    *,
    ok: bool,
    phase: str,
    written_mtd: list[int] | None = None,
    read_back_verified: bool = False,
    physical_actions: list[str] | None = None,
    next_command: str | None = None,
    result: dict[str, object] | None = None,
    error: str | None = None,
    preserved_mtd: list[int] | None = None,
) -> InstallationResult:
    protected = preserved_mtd or [0, 4, 5]
    return {
        "command": command,
        "error": error,
        "next_command": next_command,
        "nor": {
            "full_physical_readback_verified": read_back_verified,
            "preserved_mtd": protected,
            "written_mtd": written_mtd or [],
            "writing_now": False,
        },
        "ok": ok,
        "phase": phase,
        "physical_actions": physical_actions or [],
        "result": result or {},
        "schema_version": SCHEMA_VERSION,
        "target": _target(protected),
    }
