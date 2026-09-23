#!/usr/bin/env python3
"""Render a public-safe, technical inventory for the Raptor source inputs.

The inventory binds the checked-in Raptor source lock and SDK-headers input to
their public origins, base and final Git identities, patch digests, and license
or notice digests.  An optional local reconstructed-source cache can be
verified with :func:`installer.raptor_source.verify_source`.  The cache is an
input to verification only.  This command never copies source trees, vendor
files, credentials, or other build inputs into its output.

The manifest is technical evidence.  It does not assess licensing or grant
redistribution rights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from installer.raptor_source import (  # noqa: E402
    FULL_MEDIA_SOURCES,
    source_lock,
    verify_source,
)


SCHEMA_VERSION = 1
SOURCE_LOCK_PATH = "components/raptor/source-build-lock.json"
HEADERS_INPUT_PATH = "components/raptor/headers-input.json"
LEGAL_REVIEW_STATUS = "not-assessed"
REDISTRIBUTION_STATUS = "not-authorized-by-this-repository"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
PUBLIC_GIT_URL = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git"
)


class SourceDeliveryInventoryError(ValueError):
    """The source-lock inventory could not be rendered safely."""


def _safe_relative(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SourceDeliveryInventoryError(f"{label} must be a relative path")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise SourceDeliveryInventoryError(f"{label} is not a safe relative path")
    return value


def _digest(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise SourceDeliveryInventoryError(f"{label} is not a lowercase hexadecimal digest")
    return value


def _stable_file(path: Path, relative: str) -> tuple[bytes, str]:
    """Read one checked-in input without exposing its local path."""

    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise SourceDeliveryInventoryError(f"{relative} is unreadable") from exc
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity or len(raw) != before.st_size:
        raise SourceDeliveryInventoryError(f"{relative} changed while being read")
    return raw, hashlib.sha256(raw).hexdigest()


def _json_object(raw: bytes, relative: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceDeliveryInventoryError(f"{relative} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SourceDeliveryInventoryError(f"{relative} must be a JSON object")
    return value


def _validate_source_entry(name: str, spec: object) -> dict[str, object]:
    if not isinstance(spec, dict):
        raise SourceDeliveryInventoryError(f"source-lock entry is malformed: {name}")
    origin = spec.get("url")
    if not isinstance(origin, str) or PUBLIC_GIT_URL.fullmatch(origin) is None:
        raise SourceDeliveryInventoryError(f"source origin is not public HTTPS: {name}")
    base = _digest(spec.get("base"), f"{name} base", HEX40)
    base_tree = _digest(spec.get("base_tree"), f"{name} base tree", HEX40)
    final_tree = _digest(spec.get("tree"), f"{name} final tree", HEX40)

    raw_patches = spec.get("patches")
    if not isinstance(raw_patches, list):
        raise SourceDeliveryInventoryError(f"source patches are malformed: {name}")
    patches: list[dict[str, str]] = []
    for index, raw_patch in enumerate(raw_patches):
        if not isinstance(raw_patch, dict):
            raise SourceDeliveryInventoryError(f"source patch is malformed: {name}[{index}]")
        patches.append(
            {
                "path": _safe_relative(raw_patch.get("path"), f"{name} patch path"),
                "sha256": _digest(raw_patch.get("sha256"), f"{name} patch", HEX64),
            }
        )

    evidence_kind = "notice" if name == "ingenic-headers" else "license"
    evidence_path = _safe_relative(spec.get(evidence_kind), f"{name} {evidence_kind}")
    evidence_digest = _digest(
        spec.get(f"{evidence_kind}_sha256"),
        f"{name} {evidence_kind}",
        HEX64,
    )
    entry: dict[str, object] = {
        "origin": origin,
        "base": base,
        "base_tree": base_tree,
        "final_tree": final_tree,
        "patches": patches,
        "patch_digests": [patch["sha256"] for patch in patches],
        "license_or_notice": {
            "kind": evidence_kind,
            "path": evidence_path,
            "sha256": evidence_digest,
        },
        "legal_review_status": LEGAL_REVIEW_STATUS,
        "redistribution": REDISTRIBUTION_STATUS,
    }
    if name == "ingenic-headers":
        entry["license_status"] = spec.get("license_status")
    return entry


def _verify_cache(
    cache_root: Path, sources: dict[str, Any]
) -> dict[str, dict[str, str]]:
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise SourceDeliveryInventoryError("verified cache root is not a directory")
    verified: dict[str, dict[str, str]] = {}
    for name in sorted(sources):
        source_path = cache_root / name
        if source_path.is_symlink() or not source_path.is_dir():
            raise SourceDeliveryInventoryError(f"verified cache source is missing: {name}")
        try:
            tree_digest = verify_source(source_path, sources[name], tree=sources[name]["tree"])
        except Exception as exc:
            # Do not surface a local cache path or a subprocess command in the
            # emitted error.  The caller only needs the source name and the
            # failed integrity check.
            raise SourceDeliveryInventoryError(
                f"verified cache integrity check failed: {name}"
            ) from exc
        verified_digest = _digest(tree_digest, f"{name} verified cache tree", HEX64)
        verified[name] = {
            "path": _safe_relative(name, f"{name} verified cache path"),
            "tree_digest": verified_digest,
        }
    return verified


def build_source_delivery_inventory(
    repository: Path = ROOT, *, verified_cache_root: Path | None = None
) -> dict[str, object]:
    """Build a deterministic source-delivery inventory.

    ``verified_cache_root`` must point to a local directory whose immediate
    children are the reconstructed source Git checkouts named by the source
    lock.  Only those child names and their tree digests enter the manifest.
    """

    repository = Path(repository)
    if repository.is_symlink() or not repository.is_dir():
        raise SourceDeliveryInventoryError("repository is not a directory")

    lock_path = repository / SOURCE_LOCK_PATH
    headers_path = repository / HEADERS_INPUT_PATH
    lock_raw_before, lock_sha256 = _stable_file(lock_path, SOURCE_LOCK_PATH)
    _, headers_sha256 = _stable_file(headers_path, HEADERS_INPUT_PATH)
    try:
        lock = source_lock(repository, full_media=True)
    except (OSError, TypeError, ValueError) as exc:
        raise SourceDeliveryInventoryError("Raptor source-lock validation failed") from exc
    _, lock_sha256_after = _stable_file(lock_path, SOURCE_LOCK_PATH)
    _, headers_sha256_after = _stable_file(headers_path, HEADERS_INPUT_PATH)
    if lock_sha256 != lock_sha256_after or headers_sha256 != headers_sha256_after:
        raise SourceDeliveryInventoryError("source-lock inputs changed while being validated")

    sources = lock.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(FULL_MEDIA_SOURCES):
        raise SourceDeliveryInventoryError("Raptor source closure must contain all locked sources")
    # Keep the current closure explicit.  This rejects an accidental omission
    # if the source set is changed without a corresponding schema decision.
    if len(sources) != 13:
        raise SourceDeliveryInventoryError("Raptor source closure must contain 13 sources including headers")

    entries = {
        name: _validate_source_entry(name, sources[name])
        for name in sorted(sources)
    }
    verified = (
        _verify_cache(Path(verified_cache_root), sources)
        if verified_cache_root is not None
        else {}
    )

    inventory: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "technical-source-delivery-inventory",
        "technical_status": (
            "source-lock-and-reconstructed-cache-verified"
            if verified_cache_root is not None
            else "source-lock-inventory-only"
        ),
        "legal_review_status": LEGAL_REVIEW_STATUS,
        "redistribution": REDISTRIBUTION_STATUS,
        "notice": (
            "Technical source provenance and integrity only; legal review and "
            "redistribution authorization remain open."
        ),
        "source_lock": {"path": SOURCE_LOCK_PATH, "sha256": lock_sha256},
        "headers_input": {"path": HEADERS_INPUT_PATH, "sha256": headers_sha256},
        "source_count": len(entries),
        "sources": entries,
        "verified_cache": {
            "provided": verified_cache_root is not None,
            "source_count": len(verified),
            "all_verified": verified_cache_root is not None and len(verified) == len(entries),
            "sources": verified,
        },
    }
    # The raw JSON is intentionally read only for stability and hashing.  A
    # quick sanity check prevents a future field from accidentally carrying it
    # into this public-safe manifest.
    if not lock_raw_before:
        raise SourceDeliveryInventoryError("Raptor source lock is empty")
    return inventory


def _canonical_json(document: dict[str, object]) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def write_inventory(
    output: Path,
    repository: Path = ROOT,
    *,
    verified_cache_root: Path | None = None,
) -> None:
    """Write one inventory without replacing an existing file."""

    output = Path(output)
    if output.exists() or output.is_symlink():
        raise SourceDeliveryInventoryError("output already exists")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise SourceDeliveryInventoryError("output parent is not a directory")
    payload = _canonical_json(
        build_source_delivery_inventory(
            repository, verified_cache_root=verified_cache_root
        )
    )
    try:
        with output.open("xb") as handle:
            handle.write(payload)
    except OSError as exc:
        raise SourceDeliveryInventoryError("cannot write inventory output") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--verified-cache-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        write_inventory(
            arguments.output,
            arguments.repository,
            verified_cache_root=arguments.verified_cache_root,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "source_count": 13}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
