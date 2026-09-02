"""Validate that a recovery package belongs to one exact private session."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .layout import TARGET
from .sd_package import parse_package, read_snapshot, validate_bootstrap


class RecoveryPackageBindingError(ValueError):
    """The recovery package or embedded private session is invalid."""


Runner = Callable[..., object]
SnapshotReader = Callable[[Path], bytes]
PackageParser = Callable[..., object]
PackageValidator = Callable[[object], None]


def read_embedded_session_file(
    *,
    unsquashfs: Path,
    squashfs: Path,
    relative: str,
    limit: int,
    run: Runner = subprocess.run,
) -> bytes:
    try:
        completed = run(
            [str(unsquashfs), "-cat", str(squashfs), relative],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryPackageBindingError(
            "cannot inspect the recovery package session"
        ) from exc
    returncode = getattr(completed, "returncode", None)
    output = getattr(completed, "stdout", None)
    if returncode != 0 or not isinstance(output, bytes) or len(output) > limit:
        raise RecoveryPackageBindingError(
            "recovery package session file is invalid"
        )
    return output


def validate_recovery_package_binding(
    *,
    package_path: Path,
    session: Path,
    unsquashfs: Path,
    run: Runner = subprocess.run,
    read_package: SnapshotReader = read_snapshot,
    parse: PackageParser = parse_package,
    validate: PackageValidator = validate_bootstrap,
) -> dict[str, object]:
    """Validate mtd1/mtd2-only framing and its exact embedded session."""

    raw = read_package(package_path)
    try:
        package = parse(raw, require_project_header=True)
        validate(package)
    except ValueError as exc:
        raise RecoveryPackageBindingError(
            "recovery package is not the exact mtd1/mtd2 bootstrap"
        ) from exc
    mtd2 = next(
        (
            record
            for record in getattr(package, "records")
            if record.flash_offset == TARGET.partition(2).offset
        ),
        None,
    )
    if mtd2 is None:
        raise RecoveryPackageBindingError("recovery package lacks its exact mtd2 record")
    media = session / "media/RECOVERY"
    if media.is_symlink() or not media.is_dir():
        raise RecoveryPackageBindingError("recovery session media directory is invalid")
    expected_names = {"AP.PSK", "AUTHORIZED.KEY", "HOST.KEY"}
    if {path.name for path in media.iterdir()} != expected_names:
        raise RecoveryPackageBindingError("recovery session media file set is invalid")
    limits = {"AP.PSK": 65, "AUTHORIZED.KEY": 2048, "HOST.KEY": 4096}
    binding = hashlib.sha256()
    with tempfile.TemporaryDirectory(
        prefix="thingino-dlink-session-bind-", dir=os.environ.get("TMPDIR")
    ) as name:
        squashfs = Path(name) / "mtd2.squashfs"
        squashfs.write_bytes(mtd2.payload)
        squashfs.chmod(0o600)
        for filename in sorted(expected_names):
            source = media / filename
            if source.is_symlink() or not source.is_file():
                raise RecoveryPackageBindingError(
                    "recovery session media file is invalid"
                )
            local = source.read_bytes()
            if len(local) > limits[filename]:
                raise RecoveryPackageBindingError(
                    "recovery session media file is invalid"
                )
            embedded = read_embedded_session_file(
                unsquashfs=unsquashfs,
                squashfs=squashfs,
                relative=f"etc/recovery-session/{filename}",
                limit=limits[filename],
                run=run,
            )
            if embedded != local:
                raise RecoveryPackageBindingError(
                    "recovery package belongs to another private session"
                )
            binding.update(filename.encode("ascii") + b"\0")
            binding.update(len(local).to_bytes(8, "big"))
            binding.update(local)
    return {
        "package_sha256": hashlib.sha256(raw).hexdigest(),
        "session_binding_sha256": binding.hexdigest(),
    }
