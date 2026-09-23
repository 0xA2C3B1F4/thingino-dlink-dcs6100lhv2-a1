"""Filesystem and host-command primitives shared by local build phases."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

COMMAND_TIMEOUT_SECONDS = 8 * 60 * 60


class LocalBuildRunError(ValueError):
    """The complete guided local build could not be accepted."""


def _project_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    required = (
        root / "scripts/run_macos_thingino_toolchain_download_fetch.sh",
        root / "scripts/run_macos_thingino_toolchain_build.sh",
        root / "scripts/run_macos_thingino_download_fetch.sh",
        root / "scripts/run_macos_thingino_build.sh",
        root / "scripts/run_macos_split_kernel_build.sh",
        root / "scripts/run_macos_collector_kernel_build.sh",
        root / "components/raptor/S96raptor",
    )
    if not all(path.is_file() and not path.is_symlink() for path in required):
        raise LocalBuildRunError("project local-build implementation is incomplete")
    return root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise LocalBuildRunError(f"{label} is symlinked")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LocalBuildRunError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise LocalBuildRunError(f"{label} is not a directory")
    return resolved


def _regular(path: Path, label: str, *, limit: int | None = None) -> Path:
    if path.is_symlink():
        raise LocalBuildRunError(f"{label} is symlinked")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LocalBuildRunError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise LocalBuildRunError(f"{label} is not a regular file")
    if limit is not None and (resolved.stat().st_size < 1 or resolved.stat().st_size > limit):
        raise LocalBuildRunError(f"{label} violates its size limit")
    return resolved


def _tool(name: str) -> Path:
    found = shutil.which(name)
    if found is None:
        raise LocalBuildRunError(f"required host tool is missing: {name}")
    path = Path(found).resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise LocalBuildRunError(f"required host tool is not executable: {name}")
    return path


def _llvm_tools() -> tuple[Path, Path]:
    """Use Homebrew LLVM on supported Apple Silicon hosts, not Apple Clang."""

    homebrew = Path("/opt/homebrew/opt/llvm/bin")
    clang = homebrew / "clang"
    if clang.is_file() and os.access(clang, os.X_OK):
        return clang.resolve(), _tool("ld.lld")
    clang = _tool("clang")
    sibling_lld = clang.parent / "ld.lld"
    if sibling_lld.is_file() and os.access(sibling_lld, os.X_OK):
        return clang, sibling_lld.resolve(strict=True)
    return clang, _tool("ld.lld")


def _run(
    arguments: list[str], *, label: str, log_path: Path, timeout: int = COMMAND_TIMEOUT_SECONDS
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        try:
            completed = subprocess.run(
                arguments,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalBuildRunError(f"{label} could not complete; see {log_path}") from exc
    if completed.returncode != 0:
        raise LocalBuildRunError(f"{label} failed; see {log_path}")
