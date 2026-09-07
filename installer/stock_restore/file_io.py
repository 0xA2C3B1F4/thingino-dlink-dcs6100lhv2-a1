"""Exclusive writes, exact regular-file reads and directory synchronization."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from .contract import StockRestoreSetError


def _write_exclusive(path: Path, raw: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            amount = os.write(descriptor, view)
            if amount <= 0:
                raise StockRestoreSetError("short private restore-set write")
            view = view[amount:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular(path: Path, size: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != size
        ):
            raise StockRestoreSetError("private restore artifact is not exact")
        chunks: list[bytes] = []
        remaining = size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != size
    ):
        raise StockRestoreSetError("private restore artifact changed while read")
    return raw


def _read_bounded_regular(path: Path, maximum_size: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > maximum_size
    ):
        raise StockRestoreSetError("private restore manifest is not exact")
    return _read_regular(path, metadata.st_size)


def _status_sidecars(root: Path, name: str) -> tuple[Path, ...]:
    return (
        root / f".{name}.part",
        root / f"._{name}",
        root / f"._.{name}.part",
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
