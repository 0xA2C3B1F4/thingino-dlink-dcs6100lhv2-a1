"""Verified exclusive temporary writes and directory synchronization."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .media_preflight import MediaError


def _write_verified_temporary(temporary: Path, raw: bytes) -> None:
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    readback = temporary.read_bytes()
    if len(readback) != len(raw) or hashlib.sha256(readback).digest() != hashlib.sha256(
        raw
    ).digest():
        raise MediaError("SD temporary-file readback mismatch")


def _sync_directory(root: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
