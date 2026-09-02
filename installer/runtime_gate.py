"""Validate the manual live-runtime evidence required before installer lock."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .layout import TARGET
from .vendor_bundle import VendorBundle


class RuntimeGateError(ValueError):
    """The runtime evidence is incomplete or belongs to another build."""


@dataclass(frozen=True, slots=True)
class RuntimeDecision:
    audio_process_required: bool
    vendor_bundle_sha256: str


def _read_report(path: Path) -> dict[str, object]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RuntimeGateError("cannot read runtime report") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 64 * 1024:
            raise RuntimeGateError("runtime report is not a bounded regular file")
        raw = os.read(descriptor, 64 * 1024 + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
        raise RuntimeGateError("runtime report changed while being read")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeGateError("runtime report is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeGateError("runtime report must be a JSON object")
    return document


def _object(value: object, label: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RuntimeGateError(f"{label} has unexpected or missing fields")
    return value


def validate_runtime_report(path: Path, bundle: VendorBundle) -> RuntimeDecision:
    document = _read_report(path)
    if set(document) != {"schema_version", "target", "vendor_bundle_sha256", "tests"}:
        raise RuntimeGateError("runtime report has unexpected or missing fields")
    if document["schema_version"] != 1:
        raise RuntimeGateError("unsupported runtime report schema")
    if document["target"] != {"hardware_revision": TARGET.hardware_revision, "model": TARGET.model}:
        raise RuntimeGateError("runtime report targets the wrong camera")
    if document["vendor_bundle_sha256"] != bundle.bundle_sha256:
        raise RuntimeGateError("runtime report belongs to another vendor bundle")
    tests = _object(document["tests"], "runtime tests", {"aac", "day_night", "native_media", "video"})
    video = _object(tests["video"], "video test", {"colors_verified", "height", "passed", "width"})
    if video != {"colors_verified": True, "height": 1080, "passed": True, "width": 1920}:
        raise RuntimeGateError("1080p color test did not pass")
    aac = _object(tests["aac"], "AAC test", {"audio_process_loaded", "codec", "passed"})
    if aac.get("codec") != "AAC" or aac.get("passed") is not True or aac.get("audio_process_loaded") not in (True, False):
        raise RuntimeGateError("AAC runtime test did not pass")
    day_night = _object(tests["day_night"], "day/night test", {"day_to_night", "night_to_day", "passed"})
    if day_night != {"day_to_night": True, "night_to_day": True, "passed": True}:
        raise RuntimeGateError("day/night transition test did not pass")
    native = _object(
        tests["native_media"],
        "native media test",
        {"stock_iq_copied", "stock_modules_copied", "thingino_iq", "thingino_modules"},
    )
    if native != {
        "stock_iq_copied": False,
        "stock_modules_copied": False,
        "thingino_iq": True,
        "thingino_modules": True,
    }:
        raise RuntimeGateError("runtime report does not preserve Thingino native media")
    audio_required = aac["audio_process_loaded"] is True
    if audio_required and "libaudioProcess.so" not in {artifact.name for artifact in bundle.artifacts}:
        raise RuntimeGateError("AAC used libaudioProcess.so but the optional archive is missing")
    return RuntimeDecision(
        audio_process_required=audio_required,
        vendor_bundle_sha256=bundle.bundle_sha256,
    )
