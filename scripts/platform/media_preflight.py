"""Select the host-specific removable-media preflight adapter."""

from __future__ import annotations

import sys
from pathlib import Path

from .media_preflight_common import PreflightError


def create_preflight_document(
    *,
    whole_device: str,
    mount_root: Path,
    platform_name: str | None = None,
) -> dict[str, object]:
    """Inspect one mounted SD card without formatting or writing it."""

    selected = platform_name or sys.platform
    if selected == "darwin":
        from .macos_media_preflight import create_preflight_document as create
    elif selected.startswith("linux"):
        from .linux_media_preflight import create_preflight_document as create
    elif selected in {"win32", "cygwin"}:
        from .windows_media_preflight import create_preflight_document as create
    else:
        raise PreflightError("this operating system has no reviewed media adapter")
    return create(whole_device=whole_device, mount_root=mount_root)


__all__ = ["PreflightError", "create_preflight_document"]
