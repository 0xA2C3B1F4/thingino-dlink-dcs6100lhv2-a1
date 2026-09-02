"""Parse the fixed read-only recovery NOR inspection report."""

from __future__ import annotations

import re
from dataclasses import dataclass


class NorStateError(ValueError):
    """The recovery NOR inspection report violates its fixed schema."""


@dataclass(frozen=True, slots=True)
class RecoveryNorState:
    layout: str
    mtd1_kind: str | None
    mtd1_sha256: str | None
    mtd2_kind: str | None
    mtd2_sha256: str | None
    mtd3_mounted: bool | None
    mtd3_kind: str | None
    mtd3_sha256: str | None
    mtd3_payload_sha256: str | None


def _digest(value: str, label: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise NorStateError(f"camera {label} digest is invalid")
    return value


def parse_nor_state(raw: bytes) -> RecoveryNorState:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise NorStateError("camera NOR inspection is not ASCII") from exc
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.count("=") != 1:
            raise NorStateError("camera NOR inspection framing is invalid")
        key, value = line.split("=", 1)
        if key in fields:
            raise NorStateError("camera NOR inspection has duplicate fields")
        fields[key] = value
    if fields.get("schema") != "1":
        raise NorStateError("camera NOR inspection schema is invalid")
    if fields.get("layout") == "unknown":
        if set(fields) != {"schema", "layout"}:
            raise NorStateError("unknown NOR layout exposed extra state")
        return RecoveryNorState(
            "unknown", None, None, None, None, None, None, None, None
        )
    expected = {
        "schema",
        "layout",
        "mtd1_kind",
        "mtd1_sha256",
        "mtd2_kind",
        "mtd2_sha256",
        "mtd3_mounted",
        "mtd3_kind",
        "mtd3_sha256",
        "mtd3_payload_sha256",
    }
    if set(fields) != expected or fields["layout"] != "dcs6100lhv2-a1-six-partition":
        raise NorStateError("camera NOR inspection field set is invalid")
    if fields["mtd1_kind"] not in {"uimage", "unknown"}:
        raise NorStateError("camera mtd1 classification is invalid")
    if fields["mtd2_kind"] not in {"squashfs", "unknown"}:
        raise NorStateError("camera mtd2 classification is invalid")
    if fields["mtd3_kind"] not in {"stock", "personal", "unknown"}:
        raise NorStateError("camera mtd3 classification is invalid")
    if fields["mtd3_mounted"] not in {"yes", "no"}:
        raise NorStateError("camera mtd3 mount state is invalid")
    payload = fields["mtd3_payload_sha256"]
    if fields["mtd3_kind"] == "personal":
        payload = _digest(payload, "mtd3 payload")
    elif payload != "-":
        raise NorStateError("camera non-personal mtd3 exposed a payload digest")
    return RecoveryNorState(
        layout=fields["layout"],
        mtd1_kind=fields["mtd1_kind"],
        mtd1_sha256=_digest(fields["mtd1_sha256"], "mtd1"),
        mtd2_kind=fields["mtd2_kind"],
        mtd2_sha256=_digest(fields["mtd2_sha256"], "mtd2"),
        mtd3_mounted=fields["mtd3_mounted"] == "yes",
        mtd3_kind=fields["mtd3_kind"],
        mtd3_sha256=_digest(fields["mtd3_sha256"], "mtd3"),
        mtd3_payload_sha256=payload if payload != "-" else None,
    )
