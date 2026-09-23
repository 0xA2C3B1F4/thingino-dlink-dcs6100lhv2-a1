"""Build and validate the personal, whole-physical-mtd3 Thingino image."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .artifacts import ArtifactError, validate_squashfs
from .layout import TARGET


FOOTER_SIZE = 512
FOOTER_MAGIC = "DCS6100-MTD3-V1"
TARGET_ID = "DCS-6100LHV2-A1"
_DIGEST = re.compile(r"[0-9a-f]{64}")


class Mtd3ImageError(ValueError):
    """A personal mtd3 image violates the fixed installer contract."""


@dataclass(frozen=True, slots=True)
class PersonalMtd3Image:
    raw: bytes
    payload_size: int
    payload_sha256: str
    image_sha256: str


def _footer(*, payload_size: int, payload_sha256: str) -> bytes:
    document = (
        f"{FOOTER_MAGIC}\n"
        f"target={TARGET_ID}\n"
        f"payload_size={payload_size}\n"
        f"payload_sha256={payload_sha256}\n"
        f"image_size={TARGET.partition(3).size}\n"
        "END\n"
    ).encode("ascii")
    if len(document) > FOOTER_SIZE:
        raise AssertionError("personal mtd3 footer exceeds its fixed reservation")
    return document + b"\xff" * (FOOTER_SIZE - len(document))


def build_personal_mtd3_image(system_rootfs: bytes) -> PersonalMtd3Image:
    """Pad one validated Thingino SquashFS to the exact physical mtd3 size."""

    limit = TARGET.partition(3).size - FOOTER_SIZE
    try:
        validate_squashfs(system_rootfs, partition_limit=limit)
    except ArtifactError as exc:
        raise Mtd3ImageError(str(exc)) from exc
    payload_sha256 = hashlib.sha256(system_rootfs).hexdigest()
    raw = (
        system_rootfs
        + b"\xff" * (limit - len(system_rootfs))
        + _footer(
            payload_size=len(system_rootfs),
            payload_sha256=payload_sha256,
        )
    )
    return PersonalMtd3Image(
        raw=raw,
        payload_size=len(system_rootfs),
        payload_sha256=payload_sha256,
        image_sha256=hashlib.sha256(raw).hexdigest(),
    )


def validate_personal_mtd3_image(raw: bytes) -> PersonalMtd3Image:
    """Validate the exact image, payload hash, erased gap, and fixed footer."""

    expected_size = TARGET.partition(3).size
    if not isinstance(raw, bytes) or len(raw) != expected_size:
        raise Mtd3ImageError("personal mtd3 image has the wrong exact size")
    footer = raw[-FOOTER_SIZE:]
    end = footer.find(b"END\n")
    if end < 0 or any(byte != 0xFF for byte in footer[end + 4 :]):
        raise Mtd3ImageError("personal mtd3 footer framing is invalid")
    try:
        lines = footer[: end + 4].decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise Mtd3ImageError("personal mtd3 footer is not ASCII") from exc
    if not lines or lines[0] != FOOTER_MAGIC or lines[-1] != "END":
        raise Mtd3ImageError("personal mtd3 footer identity is invalid")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if line.count("=") != 1:
            raise Mtd3ImageError("personal mtd3 footer field is malformed")
        key, value = line.split("=", 1)
        if key in fields:
            raise Mtd3ImageError("personal mtd3 footer has a duplicate field")
        fields[key] = value
    if set(fields) != {"target", "payload_size", "payload_sha256", "image_size"}:
        raise Mtd3ImageError("personal mtd3 footer field set changed")
    if fields["target"] != TARGET_ID or fields["image_size"] != str(expected_size):
        raise Mtd3ImageError("personal mtd3 footer targets the wrong device")
    try:
        payload_size = int(fields["payload_size"], 10)
    except ValueError as exc:
        raise Mtd3ImageError("personal mtd3 payload size is invalid") from exc
    payload_sha256 = fields["payload_sha256"]
    limit = expected_size - FOOTER_SIZE
    if not 1 <= payload_size <= limit or _DIGEST.fullmatch(payload_sha256) is None:
        raise Mtd3ImageError("personal mtd3 payload identity is invalid")
    payload = raw[:payload_size]
    if hashlib.sha256(payload).hexdigest() != payload_sha256:
        raise Mtd3ImageError("personal mtd3 payload hash mismatch")
    if any(byte != 0xFF for byte in raw[payload_size:limit]):
        raise Mtd3ImageError("personal mtd3 payload gap is not erased")
    try:
        validate_squashfs(payload, partition_limit=limit)
    except ArtifactError as exc:
        raise Mtd3ImageError(str(exc)) from exc
    return PersonalMtd3Image(
        raw=raw,
        payload_size=payload_size,
        payload_sha256=payload_sha256,
        image_sha256=hashlib.sha256(raw).hexdigest(),
    )
