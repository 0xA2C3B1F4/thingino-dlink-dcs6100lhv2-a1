"""Camera-specific RTSP configuration for an unprovisioned full Raptor image."""

from __future__ import annotations

import configparser
import hashlib
import io
import json
from pathlib import Path
import re

from .final_root import FinalRootError, _write_private


RUNTIME = "full-raptor-v1"
SERVICE = "etc/init.d/S96raptor"
CONFIGS = (
    "etc/raptor-media.conf",
    "etc/raptor-audio.conf",
    "etc/raptor-webrtc.conf",
)
CONTROL_INIT = b"#!/bin/sh\n# S96raptor starts the media-aware Control service.\nexit 0\n"


def _regular_file(root: Path, relative: str) -> Path:
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise FinalRootError(f"symlink in Raptor runtime path: {relative}")
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise FinalRootError(f"invalid Raptor runtime input: {relative}")
    return path


def _config(root: Path, relative: str) -> configparser.ConfigParser:
    path = _regular_file(root, relative)
    document = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        document.read_string(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise FinalRootError(f"invalid Raptor configuration: {relative}") from exc
    if document.defaults():
        raise FinalRootError("Raptor configuration must not inherit default values")
    return document


def validate_universal_runtime(root: Path) -> None:
    """Reject mixed media owners and secrets before creating a camera overlay."""
    service = _regular_file(root, SERVICE)
    if service.stat().st_mode & 0o111:
        raise FinalRootError("universal full Raptor service must be disabled")
    for relative in (
        "etc/init.d/S95thingino-control",
        "usr/share/thingino-provisioning/init/S95thingino-control",
    ):
        if _regular_file(root, relative).read_bytes() != CONTROL_INIT:
            raise FinalRootError("full Raptor must be the only Control startup owner")
    for relative in (
        "usr/bin/prudynt", "usr/bin/prudyntctl", "usr/bin/daynightd",
        "etc/prudynt.json", "etc/init.d/S96rwd",
        "etc/init.d/S10daynightd", "etc/init.d/S31prudynt",
        "etc/init.d/S13prudynt-rwd", "etc/init.d/S98recorder",
    ):
        path = root / relative
        if path.exists() or path.is_symlink():
            raise FinalRootError(f"full Raptor image retained a legacy media owner: {relative}")
    for relative in CONFIGS:
        document = _config(root, relative)
        for section in document.values():
            if any(section.get(key, "") for key in ("username", "password")):
                raise FinalRootError("universal Raptor image contains media credentials")
    media = _config(root, CONFIGS[0])
    if not media.has_section("rtsp") or media["rtsp"].get("bind_address") != "127.0.0.1":
        raise FinalRootError("universal Raptor RTSP must remain on loopback")
    try:
        closure = json.loads(_regular_file(root, "etc/dlink-media-closure.private.json").read_bytes())
    except (OSError, ValueError) as exc:
        raise FinalRootError("full Raptor media provenance is invalid") from exc
    if not isinstance(closure, dict) or closure.get("runtime") != RUNTIME:
        raise FinalRootError("full Raptor media provenance is missing")
    entries = closure.get("files")
    if not isinstance(entries, list):
        raise FinalRootError("full Raptor media provenance lacks files")
    for relative in CONFIGS:
        matches = [
            entry for entry in entries
            if isinstance(entry, dict) and entry.get("path") == relative
        ]
        raw = (root / relative).read_bytes()
        if (
            len(matches) != 1
            or matches[0].get("sha256") != hashlib.sha256(raw).hexdigest()
            or matches[0].get("size") != len(raw)
        ):
            raise FinalRootError(f"full Raptor configuration provenance changed: {relative}")


def provision_runtime(root: Path, rtsp_password: str) -> None:
    """Personalize the extracted copy, never the reusable model image."""
    if not re.fullmatch(r"[a-f0-9]{64}", rtsp_password):
        raise FinalRootError("invalid derived Raptor viewer credential")
    media = _config(root, CONFIGS[0])
    if not media.has_section("rtsp"):
        raise FinalRootError("Raptor media configuration lacks RTSP")
    media["rtsp"].update({
        "bind_address": "0.0.0.0",
        "port": "554",
        "username": "root",
        "password": rtsp_password,
    })
    output = io.StringIO()
    media.write(output)
    raw = output.getvalue().encode("utf-8")
    _write_private(root / CONFIGS[0], raw)

    path = _regular_file(root, "etc/dlink-media-closure.private.json")
    closure = json.loads(path.read_bytes())
    entries = [entry for entry in closure["files"] if entry.get("path") == CONFIGS[0]]
    if len(entries) != 1:
        raise FinalRootError("Raptor media provenance lacks one media configuration")
    entries[0]["sha256"] = hashlib.sha256(raw).hexdigest()
    entries[0]["size"] = len(raw)
    closure["runtime_config_sha256"] = entries[0]["sha256"]
    _write_private(path, (json.dumps(closure, indent=2, sort_keys=True) + "\n").encode())
