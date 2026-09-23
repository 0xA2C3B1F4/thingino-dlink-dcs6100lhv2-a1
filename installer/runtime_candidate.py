"""Compatibility boundary for the retired volatile runtime-candidate flow.

The supported Raptor path is the full composed image and the separately
authenticated ``raptor_runtime_protocol`` experiment.  This module remains
as a small import-compatible boundary for older guided-CLI callers and for
the shared MIPS ELF validator used by component composition.  It deliberately
does not package, upload, mount, start, or roll back a partial runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn


class RuntimeCandidateError(ValueError):
    """A retired volatile runtime-candidate operation was requested."""


TEMPLATE_PATH = Path(__file__).with_name("templates") / "runtime-candidate.sh"


def _validate_mips_elf(raw: bytes, label: str) -> None:
    """Validate the MIPS32r2 O32 ELF contract shared by Raptor components."""

    if len(raw) < 52 or raw[:6] != b"\x7fELF\x01\x01":
        raise RuntimeCandidateError(f"candidate {label} is not a 32-bit little-endian ELF")
    if int.from_bytes(raw[16:18], "little") != 3:
        raise RuntimeCandidateError(f"candidate {label} is not a dynamic executable")
    if int.from_bytes(raw[18:20], "little") != 8:
        raise RuntimeCandidateError(f"candidate {label} does not target MIPS")
    flags = int.from_bytes(raw[36:40], "little")
    if flags & 0xF0000000 != 0x70000000 or flags & 0x0000F000 != 0x00001000:
        raise RuntimeCandidateError(f"candidate {label} is not MIPS32r2 O32")
    if b"/lib/ld.so.1\0" not in raw:
        raise RuntimeCandidateError(f"candidate {label} lacks the pinned glibc loader")


def _retired() -> NoReturn:
    raise RuntimeCandidateError(
        "volatile runtime candidates are retired; use the composed full Raptor image"
    )


def build_runtime_candidate_package(*, rootfs_path: Path, provenance_path: Path,
                                    unsquashfs: Path | None = None) -> tuple[bytes, dict[str, object]]:
    """Fail closed instead of creating a partial runtime package."""

    del rootfs_path, provenance_path, unsquashfs
    _retired()


def stage_runtime_candidate(
    *,
    session_dir: Path,
    host: str,
    rootfs_path: Path,
    provenance_path: Path,
    expected_mtd3_sha256: str,
    unsquashfs: Path | None = None,
) -> dict[str, object]:
    """Fail closed instead of replacing live camera processes."""

    del session_dir, host, rootfs_path, provenance_path, expected_mtd3_sha256, unsquashfs
    _retired()


def runtime_candidate_status(*, session_dir: Path, host: str) -> dict[str, object]:
    """Fail closed instead of querying the retired candidate state machine."""

    del session_dir, host
    _retired()


def rollback_runtime_candidate(*, session_dir: Path, host: str) -> dict[str, object]:
    """Fail closed instead of issuing a partial-runtime rollback command."""

    del session_dir, host
    _retired()
