"""Shared fail-closed helpers for removable-media preflight adapters."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class PreflightError(ValueError):
    """The selected media cannot be proven safe for file staging."""


def atomic_write_new(destination: Path, raw: bytes) -> None:
    """Create one mode-0600 result without replacing an existing path."""

    if destination.exists() or destination.is_symlink():
        raise PreflightError("refusing to overwrite an existing preflight result")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
