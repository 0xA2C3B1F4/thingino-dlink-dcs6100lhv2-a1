"""Parse bounded path and mode facts from unsquashfs listings."""

from __future__ import annotations

import re


def listing_paths(listing: str) -> set[str]:
    paths: set[str] = set()
    for line in listing.splitlines():
        marker = "squashfs-root"
        position = line.find(marker)
        if position < 0:
            continue
        relative = line[position + len(marker) :].lstrip("/").split(" -> ", 1)[0]
        if relative:
            paths.add(relative)
    return paths


def listing_modes(listing: str) -> dict[str, str]:
    modes: dict[str, str] = {}
    for line in listing.splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"[bcdlps-][rwxStTs-]{9}", fields[0]):
            continue
        marker = "squashfs-root"
        position = line.find(marker)
        if position < 0:
            continue
        relative = line[position + len(marker) :].lstrip("/").split(" -> ", 1)[0]
        if relative:
            modes[relative] = fields[0]
    return modes
