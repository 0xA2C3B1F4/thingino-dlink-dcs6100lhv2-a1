"""Validate one closed collector result before any later installation gate."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from installer.recovery_gate import validate_existing_recovery_boundary
from installer.vendor_bundle import load_vendor_bundle


class CollectorOutputError(ValueError):
    """The SD collector output is incomplete, inconsistent, or unsafe."""


@dataclass(frozen=True, slots=True)
class CollectorOutputDecision:
    audio_process_archived: bool
    recovery_images: int
    vendor_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProtectedOutputDecision:
    recovery_images: int
    preserved_mtd: tuple[int, ...]


def _read_completion(path: Path) -> dict[str, object]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CollectorOutputError("cannot read collector completion record") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 4096:
            raise CollectorOutputError("collector completion is not a bounded regular file")
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ) or len(raw) != before.st_size:
        raise CollectorOutputError("collector completion changed while being read")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorOutputError("collector completion is not valid UTF-8 JSON") from exc
    expected_keys = {
        "audio_process_archived",
        "mode",
        "nor_writes",
        "schema_version",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise CollectorOutputError("collector completion closure is not exact")
    if (
        document["schema_version"] != 1
        or document["mode"] != "existing-verified-same-device-pair"
        or document["nor_writes"] is not False
        or type(document["audio_process_archived"]) is not bool
    ):
        raise CollectorOutputError("collector completion contract differs")
    return document


def _read_protected_completion(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CollectorOutputError("cannot read protected completion record") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 4096:
            raise CollectorOutputError(
                "protected completion is not a bounded regular file"
            )
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or before.st_nlink != 1
        or len(raw) != before.st_size
    ):
        raise CollectorOutputError("protected completion changed while being read")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorOutputError(
            "protected completion is not valid UTF-8 JSON"
        ) from exc
    if document != {
        "mode": "protected-readback",
        "nor_writes": False,
        "partition_storage_readback_verified": True,
        "schema_version": 1,
    }:
        raise CollectorOutputError("protected completion contract differs")


def validate_collector_output(
    *, collector_output_dir: Path, recovery_dir: Path
) -> CollectorOutputDecision:
    """Require the completion, vendor, and recovery gates as one closed decision."""

    if collector_output_dir.is_symlink() or not collector_output_dir.is_dir():
        raise CollectorOutputError("collector output is not a regular directory")
    if {entry.name for entry in collector_output_dir.iterdir()} != {
        "COLLECT.OK",
        "preserved",
        "vendor",
    }:
        raise CollectorOutputError("collector output directory closure is not exact")
    completion = _read_completion(collector_output_dir / "COLLECT.OK")
    bundle = load_vendor_bundle(collector_output_dir / "vendor")
    artifact_names = tuple(artifact.name for artifact in bundle.artifacts)
    audio_present = "libaudioProcess.so" in artifact_names
    if completion["audio_process_archived"] is not audio_present:
        raise CollectorOutputError("collector completion audio disposition differs from bundle")
    recovery = validate_existing_recovery_boundary(
        recovery_dir=recovery_dir,
        preserved_readback_dir=collector_output_dir / "preserved",
    )
    return CollectorOutputDecision(
        audio_process_archived=audio_present,
        recovery_images=recovery.recovery_images,
        vendor_files=artifact_names,
    )


def validate_protected_collector_output(
    *, collector_output_dir: Path, recovery_dir: Path
) -> ProtectedOutputDecision:
    """Bind a vendor-independent protected readback to one private recovery."""

    if collector_output_dir.is_symlink() or not collector_output_dir.is_dir():
        raise CollectorOutputError("protected collector output is not a directory")
    if {entry.name for entry in collector_output_dir.iterdir()} != {
        "PROTECT.OK",
        "preserved",
    }:
        raise CollectorOutputError("protected output directory closure is not exact")
    _read_protected_completion(collector_output_dir / "PROTECT.OK")
    recovery = validate_existing_recovery_boundary(
        recovery_dir=recovery_dir,
        preserved_readback_dir=collector_output_dir / "preserved",
    )
    return ProtectedOutputDecision(
        recovery_images=recovery.recovery_images,
        preserved_mtd=recovery.preserved_mtd,
    )
