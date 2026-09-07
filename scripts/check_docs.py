#!/usr/bin/env python3
"""Validate local links in public Markdown without accessing the network."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "policy" / "public-tree.json"
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


class DocsError(ValueError):
    """A public Markdown link is unsafe or unresolved."""


def public_markdown_files() -> list[Path]:
    data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    files = data.get("files")
    if not isinstance(files, list):
        raise DocsError("public-tree policy files are not a list")
    return [
        ROOT / relative
        for relative in files
        if str(relative).endswith(".md") and not str(relative).startswith("production/")
    ]


def _local_target(raw: str) -> str | None:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    elif " " in target:
        target = target.split(" ", 1)[0]
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    return unquote(parsed.path) or None


def validate_paths(paths: list[Path], *, root: Path = ROOT) -> list[Path]:
    checked: list[Path] = []
    resolved_root = root.resolve()
    for path in paths:
        relative = path.relative_to(root)
        in_fence = False
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in LINK_PATTERN.finditer(line):
                local = _local_target(match.group(1))
                if local is None:
                    continue
                if local.startswith("/"):
                    raise DocsError(
                        f"absolute local link in {relative}:{line_number}: {local}"
                    )
                destination = (path.parent / local).resolve()
                if not destination.is_relative_to(resolved_root):
                    raise DocsError(
                        f"link escapes repository in {relative}:{line_number}: {local}"
                    )
                if not destination.exists():
                    raise DocsError(
                        f"missing local link in {relative}:{line_number}: {local}"
                    )
        checked.append(relative)
    return checked


def validate() -> list[Path]:
    return validate_paths(public_markdown_files())


def main() -> int:
    try:
        checked = validate()
    except (DocsError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"documentation check failed: {exc}", file=sys.stderr)
        return 1
    print(f"documentation check passed: {len(checked)} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
