"""Validate external-media identity documents without staging any files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


class MediaError(ValueError):
    """The media identity or root contents are not safe for staging."""


@dataclass(frozen=True, slots=True)
class MediaPreflight:
    physical_device: str
    model: str
    capacity_bytes: int
    filesystem: str
    mount_root: Path


DARWIN_DEVICE = re.compile(r"/dev/disk[1-9][0-9]*")
LINUX_DEVICE = re.compile(r"/dev/(?:sd[a-z]+|mmcblk(?:0|[1-9][0-9]*))")
WINDOWS_DEVICE = re.compile(r"\\\\\.\\PHYSICALDRIVE(?:0|[1-9][0-9]*)", re.IGNORECASE)


def load_media_preflight(path: Path, *, expected_root: Path) -> MediaPreflight:
    if path.is_symlink() or not path.is_file():
        raise MediaError("media preflight is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaError("cannot read media preflight") from exc
    return validate_media_preflight_document(document, expected_root=expected_root)


def _validated_physical_device(document: dict[str, object]) -> str:
    physical_device = document.get("physical_device")
    host_platform = document.get("host_platform")
    if not isinstance(physical_device, str):
        raise MediaError("media preflight physical-device identity is malformed")
    if host_platform is None:
        if not physical_device.startswith("/dev/"):
            raise MediaError("legacy media preflight physical-device identity is malformed")
        return physical_device
    patterns = {
        "darwin": DARWIN_DEVICE,
        "linux": LINUX_DEVICE,
        "windows": WINDOWS_DEVICE,
    }
    pattern = patterns.get(host_platform) if isinstance(host_platform, str) else None
    if pattern is None or pattern.fullmatch(physical_device) is None:
        raise MediaError(
            "media preflight host platform or physical-device identity is malformed"
        )
    return physical_device


def validate_media_preflight_document(
    document: object,
    *,
    expected_root: Path,
) -> MediaPreflight:
    if not isinstance(document, dict):
        raise MediaError("media preflight is not an object")
    required_true = ("external", "physical", "writable")
    if document.get("schema_version") != 1 or any(
        document.get(field) is not True for field in required_true
    ):
        raise MediaError("media preflight does not prove writable external physical media")
    if document.get("system_device") is not False or document.get("ambiguous") is not False:
        raise MediaError("media preflight identifies a system or ambiguous device")
    filesystem = document.get("filesystem")
    if filesystem not in ("fat32", "msdos"):
        raise MediaError("media filesystem is not FAT32")
    physical_device = _validated_physical_device(document)
    model = document.get("model")
    capacity = document.get("capacity_bytes")
    mount_root = document.get("mount_root")
    if (
        not isinstance(model, str)
        or not model.strip()
        or not isinstance(capacity, int)
        or capacity <= 0
        or not isinstance(mount_root, str)
    ):
        raise MediaError("media preflight identity fields are malformed")
    root = expected_root.resolve(strict=True)
    if root == Path("/") or root != Path(mount_root).resolve(strict=True):
        raise MediaError("media preflight mount root does not match the requested root")
    if expected_root.is_symlink() or not root.is_dir():
        raise MediaError("media root is not a real directory")
    return MediaPreflight(
        physical_device=physical_device,
        model=model,
        capacity_bytes=capacity,
        filesystem=filesystem,
        mount_root=root,
    )
