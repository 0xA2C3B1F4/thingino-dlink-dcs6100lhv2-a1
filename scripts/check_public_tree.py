#!/usr/bin/env python3
"""Validate the explicit public-file allowlist without inspecting private data."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "policy" / "public-tree.json"
EXPORT_POLICY_PATH = ROOT / "policy" / "production-export.json"

FORBIDDEN_SUFFIXES = {
    ".bin",
    ".cer",
    ".crt",
    ".dmp",
    ".dump",
    ".ffu",
    ".img",
    ".jffs2",
    ".key",
    ".p12",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pfx",
    ".squashfs",
    ".ubi",
    ".ubifs",
    ".uim",
}

FORBIDDEN_PARTS = {
    ".tmp",
    "captures",
    "downloads",
    "firmware",
    "private",
    "vendor-src",
}

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    "non-placeholder password": re.compile(
        r'(?i)["\'](?:password|passphrase|api_key|token)["\']\s*:\s*["\']'
        r'(?!__(?:GENERATE|SET)_LOCALLY__)[^"\']+["\']'
    ),
}

PRODUCTION_PRIVACY_PATTERNS = {
    "local host filesystem path": re.compile(
        r"(?i)(?:/Users/[A-Za-z0-9._-]+|/Volumes/[A-Za-z0-9._-]+|"
        r"[A-Z]:\\Users\\[A-Za-z0-9._-]+)"
    ),
    "common home-LAN address": re.compile(
        r"(?<![0-9])192\.168\.1\."
        r"(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(?![0-9])"
    ),
    "GitHub token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    "AWS access key": re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
}

LOCAL_ONLY_TOP_LEVEL = {
    ".cache",
    ".git",
    ".tmp",
    "AGENTS.md",
    "build",
    "captures",
    "dist",
    "downloads",
    "firmware",
    "out",
    "private",
    "tmp",
    "vendor-src",
}


class PolicyError(ValueError):
    """A public-tree policy violation."""


def load_public_files() -> list[str]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise PolicyError("unsupported public-tree policy schema")
    files = data.get("files")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise PolicyError("public-tree policy files must be a string list")
    if files != sorted(set(files)):
        raise PolicyError("public-tree policy files must be unique and sorted")
    return files


def validate_public_path(relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
        raise PolicyError(f"unsafe public path: {relative}")
    if FORBIDDEN_PARTS.intersection(pure.parts):
        raise PolicyError(f"private path is public: {relative}")
    if pure.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise PolicyError(f"forbidden artifact is public: {relative}")
    path = ROOT.joinpath(*pure.parts)
    if not path.is_file() or path.is_symlink():
        raise PolicyError(f"public file missing or not regular: {relative}")
    return path


def validate_text(relative: str, path: Path, *, production: bool = False) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError(f"public file is not UTF-8 text: {relative}") from exc
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            raise PolicyError(f"{label} found in public file: {relative}")
    if production:
        for label, pattern in PRODUCTION_PRIVACY_PATTERNS.items():
            if pattern.search(text):
                raise PolicyError(f"{label} found in production file: {relative}")


def discover_public_files() -> list[str]:
    discovered: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in LOCAL_ONLY_TOP_LEVEL:
            continue
        if "node_modules" in relative.parts or relative.parts[:2] == ("webui", "dist"):
            continue
        if relative.parts[:2] == ("output", "private"):
            continue
        if "__pycache__" in relative.parts or path.name == ".DS_Store":
            continue
        if relative.parts[0].endswith(".egg-info"):
            continue
        if path.is_symlink():
            discovered.append(relative.as_posix())
        elif path.is_file() and path.suffix != ".pyc":
            discovered.append(relative.as_posix())
    return sorted(discovered)


def validate() -> list[str]:
    files = load_public_files()
    production = not EXPORT_POLICY_PATH.exists()
    if "policy/public-tree.json" not in files:
        raise PolicyError("public-tree policy must include itself")
    if "AGENTS.md" in files:
        raise PolicyError("the temporary local AGENTS.md is not public")
    for relative in files:
        path = validate_public_path(relative)
        validate_text(relative, path, production=production)
    discovered = discover_public_files()
    extras = sorted(set(discovered) - set(files))
    if extras:
        raise PolicyError("unmanifested public files: " + ", ".join(extras))
    return files


def main() -> int:
    try:
        files = validate()
    except (OSError, json.JSONDecodeError, PolicyError) as exc:
        print(f"public-tree check failed: {exc}", file=sys.stderr)
        return 1
    print(f"public-tree check passed: {len(files)} allowlisted text files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
