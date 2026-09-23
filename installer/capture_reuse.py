"""Confirmed, private copy/verify/remove transaction for old capture data."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from scripts.platform.media_preflight import create_preflight_document
from .capture_state import (
    capture_inventory,
    checked_path,
    file_identity,
    ensure_capture_ready,
    PENDING,
)
from .install_actions import WritePlan, WriteConfirmation, require_write_confirmation
from .install_project import ProjectError
from .install_results import EventSink, InstallationEvent, InstallationResult, document
from .media_preflight import MediaPreflight, validate_media_preflight_document

OPERATION = "stock-recovery uartless-reuse"
RECEIPT = "capture-reuse.private.json"
COMPLETE = "capture-reuse.complete"


@dataclass(frozen=True)
class CaptureReuseInputs:
    mount_root: Path
    whole_device: str
    output_dir: Path
    resume: bool = False


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _media(inputs: CaptureReuseInputs) -> MediaPreflight:
    if sys.platform not in {"darwin", "linux"}:
        raise ProjectError(
            "unsupported_host",
            "capture archival currently requires macOS or Linux private permissions and directory fsync",
        )
    media = validate_media_preflight_document(
        create_preflight_document(
            whole_device=inputs.whole_device, mount_root=inputs.mount_root
        ),
        expected_root=inputs.mount_root,
    )
    if not media.media_uuid:
        raise ProjectError(
            "media_identity_missing", "capture reuse requires a current media UUID"
        )
    return media


def _stable_media(media: MediaPreflight) -> dict[str, object]:
    return {
        key: value
        for key, value in asdict(media).items()
        if key not in {"mount_root", "mount_device_id", "mount_inode"}
    }


def _same_filesystem(parent: Path, card: Path) -> bool:
    return parent.stat().st_dev == card.stat().st_dev


def _destination(inputs: CaptureReuseInputs) -> Path:
    destination = inputs.output_dir.expanduser().absolute()
    for path in (destination, *destination.parents):
        if path.is_symlink():
            raise ProjectError(
                "invalid_destination",
                "capture backup destination has a symlink component",
            )
    parent = destination.parent.resolve(strict=True)
    card = inputs.mount_root.resolve(strict=True)
    if destination.is_relative_to(card) or card.is_relative_to(destination):
        raise ProjectError(
            "invalid_destination",
            "capture backup must be on the host outside the SD tree",
        )
    if _same_filesystem(parent, card):
        raise ProjectError(
            "invalid_destination",
            "capture backup must be on a different filesystem from the SD card",
        )
    if not parent.is_dir():
        raise ProjectError(
            "invalid_destination", "capture backup parent is not a directory"
        )
    if destination.exists() and not inputs.resume:
        raise ProjectError(
            "destination_exists",
            "capture backup destination exists; inspect it before explicit --resume",
        )
    if inputs.resume and not destination.is_dir():
        raise ProjectError(
            "invalid_resume", "resume requires the original verified private backup"
        )
    return destination


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_tree(destination: Path) -> None:
    for path in (destination, *destination.rglob("*")):
        mode = path.lstat().st_mode
        if (
            stat.S_ISLNK(mode)
            or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))
            or mode & 0o077
        ):
            raise ProjectError(
                "invalid_backup",
                "capture backup must contain only private regular files and directories",
            )


def _read_receipt(destination: Path, media: MediaPreflight) -> dict[str, object]:
    _private_tree(destination)
    if {path.name for path in destination.iterdir()} not in (
        {"files", RECEIPT},
        {"files", RECEIPT, COMPLETE},
    ):
        raise ProjectError(
            "invalid_resume",
            "backup is incomplete or contains unexpected paths; SD data retained",
        )
    path = destination / RECEIPT
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ProjectError(
            "invalid_resume", "verified capture receipt is missing or oversized"
        )
    receipt = json.loads(path.read_bytes())
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {"schema_version", "kind", "destination", "media", "inventory"}
        or receipt["schema_version"] != 1
        or receipt["kind"] != "verified-capture-copy"
        or receipt["destination"] != str(destination)
        or receipt["media"] != _stable_media(media)
    ):
        raise ProjectError(
            "invalid_resume", "capture receipt destination or media identity changed"
        )
    # Inventory is reconstructed with the fixed path allowlist, not trusted from JSON.
    actual = capture_inventory(destination / "files")
    if not actual or actual != receipt["inventory"]:
        raise ProjectError(
            "invalid_backup", "capture backup bytes differ from the verified receipt"
        )
    return receipt


def _marker(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65:
        raise ProjectError(
            "invalid_resume",
            "capture transaction marker is linked, special or oversized",
        )
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProjectError("invalid_resume", "capture marker changed type")
        raw = stream.read(66)
    if len(raw) > 65:
        raise ProjectError("invalid_resume", "capture marker grew")
    return raw


def _write_marker(path: Path, token: bytes) -> None:
    current = _marker(path)
    if current == token:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ProjectError(
                    "invalid_resume", "capture marker changed type before sync"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _sync(path.parent)
        return
    if current is not None and not token.startswith(current):
        raise ProjectError(
            "invalid_resume", "capture marker belongs to another transaction"
        )
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= os.O_CREAT | os.O_EXCL if current is None else os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProjectError(
                "invalid_resume", "capture marker changed type before writing"
            )
        stream.write(token)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)
    if _marker(path) != token:
        raise ProjectError("copy_mismatch", "capture marker readback failed")


def _inspect(
    inputs: CaptureReuseInputs,
) -> tuple[WritePlan, dict[str, dict[str, object]], Path, dict[str, object] | None]:
    media = _media(inputs)
    destination = _destination(inputs)
    inventory = capture_inventory(inputs.mount_root)
    receipt = _read_receipt(destination, media) if inputs.resume else None
    pending = _marker(inputs.mount_root / PENDING)
    sidecar = inputs.mount_root / ("._" + PENDING)
    if sidecar.exists() or sidecar.is_symlink():
        raise ProjectError(
            "invalid_resume",
            "capture transaction marker has an unexpected sidecar; inspect without deleting it",
        )
    completed = _marker(destination / COMPLETE) if inputs.resume else None
    if pending is not None and receipt is None:
        raise ProjectError(
            "invalid_resume",
            "unfinished capture archival requires its original archive and --resume",
        )
    if receipt is not None:
        token = (_digest(receipt) + "\n").encode()
        if completed is not None:
            if inventory or not token.startswith(completed):
                raise ProjectError(
                    "invalid_resume",
                    "completed archive cannot remove a new capture generation",
                )
        elif pending is None or pending != token:
            if inventory != receipt["inventory"] or (
                pending is not None and not token.startswith(pending)
            ):
                raise ProjectError(
                    "invalid_resume", "missing or changed capture transaction marker"
                )
        if pending is not None and not token.startswith(pending):
            raise ProjectError("invalid_resume", "capture transaction marker changed")
    if receipt is None and not inventory:
        raise ProjectError(
            "nothing_to_archive", "no allowlisted UARTless capture data found"
        )
    if receipt is not None and any(
        receipt["inventory"].get(name) != value for name, value in inventory.items()
    ):
        raise ProjectError(
            "stale_capture", "remaining SD capture differs from the verified backup"
        )
    parent = destination.parent.stat()
    artifacts = (
        ("pending_marker", _digest(pending.hex() if pending is not None else None)),
        (
            "completion_marker",
            _digest(completed.hex() if completed is not None else None),
        ),
        ("capture_inventory", _digest(inventory)),
        ("destination", _digest([str(destination), parent.st_dev, parent.st_ino])),
        ("verified_receipt", _digest(receipt)),
        (
            "retained_root_names",
            _digest(sorted(path.name for path in inputs.mount_root.iterdir())),
        ),
    )
    plan = WritePlan(
        "capture-content-bound; not independently camera-identified",
        "archived-capture-unvalidated",
        media,
        artifacts,
        tuple(
            f"copy and verify {name} to {destination}; then remove SD copy"
            for name in inventory
        )
        + (
            f"host create/verify private {destination / RECEIPT} before SD removal",
            f"SD create/verify {PENDING} while cleanup is unfinished; remove only after host completion is synced",
            f"host create/verify and sync {destination / COMPLETE} after capture cleanup",
        ),
        operation=OPERATION,
    )
    return plan, inventory, destination, receipt


def plan_capture_reuse(inputs: CaptureReuseInputs) -> WritePlan:
    return _inspect(inputs)[0]


def _copy_file(
    source: Path, target: Path, relative: str, expected: dict[str, object]
) -> None:
    path = checked_path(source, relative)
    if file_identity(source, relative) != expected:
        raise ProjectError("copy_changed", "capture source changed before copy")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as incoming:
        if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
            raise ProjectError("copy_changed", "capture source is no longer regular")
        output = os.open(target / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(output, "wb") as outgoing:
            while block := incoming.read(1024 * 1024):
                outgoing.write(block)
                if outgoing.tell() > expected["size"]:
                    raise ProjectError(
                        "copy_changed", "capture source grew during copy"
                    )
            outgoing.flush()
            os.fsync(outgoing.fileno())
    if (
        file_identity(source, relative) != expected
        or file_identity(target, relative) != expected
    ):
        raise ProjectError(
            "copy_mismatch", "capture copy comparison failed; SD sources retained"
        )


def reuse_capture(
    inputs: CaptureReuseInputs,
    confirmation: WriteConfirmation,
    *,
    confirmed_output_dir: Path,
    emit: EventSink | None = None,
) -> InstallationResult:
    def event(phase: str) -> None:
        if emit:
            emit(InstallationEvent(phase, OPERATION))

    event("validating")
    plan, inventory, destination, receipt = _inspect(inputs)
    require_write_confirmation(plan, confirmation)
    if confirmed_output_dir.expanduser().absolute() != destination:
        raise ProjectError(
            "confirmation_mismatch",
            "confirm the exact private capture backup destination",
        )
    if receipt is None:
        destination.mkdir(mode=0o700)
        payload = destination / "files"
        payload.mkdir(mode=0o700)
        _private_tree(destination)
        event("copying-capture")
        for name, entry in inventory.items():
            _destination(
                CaptureReuseInputs(
                    inputs.mount_root, inputs.whole_device, destination, True
                )
            )
            _private_tree(destination)
            if entry["kind"] == "directory":
                (payload / name).mkdir(mode=0o700)
            else:
                _copy_file(inputs.mount_root, payload, name, entry)
        # Verify every copy together, and sync all directory entries, before publishing a receipt.
        if capture_inventory(payload) != inventory:
            raise ProjectError(
                "copy_mismatch", "capture backup inventory differs; SD sources retained"
            )
        for path in sorted(
            (p for p in payload.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            _sync(path)
        _sync(payload)
        receipt = {
            "schema_version": 1,
            "kind": "verified-capture-copy",
            "destination": str(destination),
            "media": _stable_media(plan.media),
            "inventory": inventory,
        }
        fd = os.open(destination / RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(receipt, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        _sync(destination)
        _sync(destination.parent)
    event("verifying-all-copies")
    if (
        _media(inputs) != plan.media
        or capture_inventory(inputs.mount_root) != inventory
    ):
        raise ProjectError(
            "stale_plan",
            "media or capture changed before removal; verified backup retained",
        )
    _read_receipt(destination, plan.media)
    token = (_digest(receipt) + "\n").encode()
    completed = _marker(destination / COMPLETE)
    if _media(inputs) != plan.media:
        raise ProjectError(
            "stale_media", "media changed before capture transaction marker"
        )
    if completed != token:
        _write_marker(inputs.mount_root / PENDING, token)
    event("removing-verified-sd-copies")
    for name, expected in inventory.items():
        if expected["kind"] != "file":
            continue
        if _media(inputs) != plan.media:
            raise ProjectError(
                "stale_media",
                "media changed during cleanup; resume only after inspection",
            )
        if _marker(inputs.mount_root / PENDING) != token:
            raise ProjectError(
                "invalid_resume", "capture transaction marker changed during cleanup"
            )
        # Revalidate the archive path too: a replaced backup must never authorize removal.
        _destination(
            CaptureReuseInputs(
                inputs.mount_root, inputs.whole_device, destination, True
            )
        )
        _private_tree(destination)
        if (
            file_identity(destination / "files", name) != expected
            or file_identity(inputs.mount_root, name) != expected
        ):
            raise ProjectError(
                "copy_mismatch", "capture or backup changed before removal"
            )
        checked_path(inputs.mount_root, name).unlink()
        _sync((inputs.mount_root / name).parent)
    for name in sorted(
        (name for name, value in inventory.items() if value["kind"] == "directory"),
        key=lambda name: (name.count("/"), name),
        reverse=True,
    ):
        if _media(inputs) != plan.media:
            raise ProjectError("stale_media", "media changed during directory cleanup")
        if _marker(inputs.mount_root / PENDING) != token:
            raise ProjectError(
                "invalid_resume",
                "capture transaction marker changed during directory cleanup",
            )
        checked_path(inputs.mount_root, name).rmdir()
        _sync((inputs.mount_root / name).parent)
    if capture_inventory(inputs.mount_root):
        raise ProjectError("stale_capture", "new capture data appeared during cleanup")
    _write_marker(destination / COMPLETE, token)
    pending = _marker(inputs.mount_root / PENDING)
    if pending is not None:
        if pending != token or _media(inputs) != plan.media:
            raise ProjectError(
                "stale_media", "capture marker or media changed before completion"
            )
        (inputs.mount_root / PENDING).unlink()
        _sync(inputs.mount_root)
    ensure_capture_ready(inputs.mount_root)
    event("capture-card-ready")
    return document(
        OPERATION,
        ok=True,
        phase="capture-archived-card-ready",
        next_command="thingino-dlink stock-recovery uartless-prepare",
        result={
            "capture_archive_dir": str(destination),
            "capture_archive_validated_as_recovery": False,
            "files": receipt["inventory"],
            "retained_root_names": sorted(
                path.name for path in inputs.mount_root.iterdir()
            ),
            "plan_sha256": plan.identity,
            "sd_modified": bool(inventory)
            or dict(plan.artifacts)["pending_marker"] != _digest(None),
            "write_set": [],
            "nor_written_by_host": False,
        },
    )
