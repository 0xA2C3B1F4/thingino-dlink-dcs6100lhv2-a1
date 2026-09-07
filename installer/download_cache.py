"""Validate the pinned Buildroot download-cache archive without extracting it."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath


POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "profiles/dlink-dcs6100lhv2-a1/download-cache.json"
)
HEX64 = re.compile(r"[0-9a-f]{64}")
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_MEMBER_BYTES = 2 * 1024**3


class DownloadCacheError(ValueError):
    """The Buildroot cache does not match its public pinned inventory."""


def _policy(path: Path = POLICY_PATH) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise DownloadCacheError("download-cache policy is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadCacheError("download-cache policy is invalid") from exc
    expected = {
        "archive_root",
        "inventory_entries",
        "inventory_sha256",
        "schema_version",
        "source_date_epoch",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise DownloadCacheError("download-cache policy fields are invalid")
    if document.get("schema_version") != 1 or document.get("archive_root") != "dl":
        raise DownloadCacheError("download-cache policy schema is unsupported")
    entries = document.get("inventory_entries")
    epoch = document.get("source_date_epoch")
    digest = document.get("inventory_sha256")
    if (
        not isinstance(entries, int)
        or isinstance(entries, bool)
        or entries <= 0
        or not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch <= 0
        or not isinstance(digest, str)
        or HEX64.fullmatch(digest) is None
    ):
        raise DownloadCacheError("download-cache policy identity is invalid")
    return document


def _regular_archive(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise DownloadCacheError("download cache is not a regular file")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode) or details.st_size < 1:
        raise DownloadCacheError("download cache is empty or not regular")
    if details.st_size > MAX_ARCHIVE_BYTES:
        raise DownloadCacheError("download cache exceeds its size limit")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_download_cache_archive(
    archive_path: Path,
    *,
    policy_path: Path = POLICY_PATH,
    forbid_git_metadata: bool = False,
) -> dict[str, object]:
    """Hash the Linux tar inventory and require the pinned pristine closure."""

    _regular_archive(archive_path)
    policy = _policy(policy_path)
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    try:
        archive = tarfile.open(archive_path, mode="r:")
    except (OSError, tarfile.TarError) as exc:
        raise DownloadCacheError("download cache is not an uncompressed tar archive") from exc
    with archive:
        members = sorted(archive.getmembers(), key=lambda item: item.name)
        for member in members:
            if (
                member.mtime != policy["source_date_epoch"]
                or member.uid != 0
                or member.gid != 0
            ):
                raise DownloadCacheError(
                    f"download cache member metadata changed: {member.name!r}"
                )
            name = member.name.removeprefix("./")
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or not path.parts
                or path.parts[0] != policy["archive_root"]
                or any(part in ("", ".", "..") for part in path.parts)
            ):
                raise DownloadCacheError(f"unsafe download cache member: {member.name!r}")
            normalized = path.as_posix()
            if normalized in seen:
                raise DownloadCacheError(f"duplicate download cache member: {normalized}")
            seen.add(normalized)
            if member.islnk():
                raise DownloadCacheError(
                    f"download cache hard links are forbidden: {member.name!r}"
                )
            if not (member.isdir() or member.isfile() or member.issym()):
                raise DownloadCacheError(
                    f"unexpected download cache member type: {member.name!r}"
                )
            if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                raise DownloadCacheError(
                    f"download cache member violates its size limit: {member.name!r}"
                )
            if member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise DownloadCacheError(
                        f"unsafe download cache link: {member.name!r}"
                    )
            relative = PurePosixPath(*path.parts[1:])
            if ".git" in relative.parts:
                if forbid_git_metadata:
                    raise DownloadCacheError(
                        f"download cache Git metadata is forbidden: {member.name!r}"
                    )
                continue
            if not relative.parts:
                continue
            mode = member.mode & 0o7777
            if member.issym():
                entry: dict[str, object] = {
                    "mode": mode,
                    "path": relative.as_posix(),
                    "target": member.linkname,
                    "type": "symlink",
                }
            elif member.isdir():
                entry = {
                    "mode": mode,
                    "path": relative.as_posix(),
                    "type": "directory",
                }
            else:
                source = archive.extractfile(member)
                if source is None:
                    raise DownloadCacheError(
                        f"unreadable download cache member: {member.name!r}"
                    )
                digest = hashlib.sha256()
                size = 0
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                if size != member.size:
                    raise DownloadCacheError(
                        f"download cache member changed while read: {member.name!r}"
                    )
                entry = {
                    "mode": mode,
                    "path": relative.as_posix(),
                    "sha256": digest.hexdigest(),
                    "size": size,
                    "type": "file",
                }
            entries.append(entry)
    aggregate = hashlib.sha256()
    for entry in entries:
        aggregate.update(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
            + b"\n"
        )
    digest = aggregate.hexdigest()
    if (
        len(entries) != policy["inventory_entries"]
        or digest != policy["inventory_sha256"]
    ):
        raise DownloadCacheError(
            "download cache inventory mismatch: "
            f"got {len(entries)} entries / {digest}"
        )
    return {
        "archive_sha256": _sha256_file(archive_path),
        "archive_size": archive_path.stat().st_size,
        "inventory_entries": len(entries),
        "inventory_sha256": digest,
        "source_date_epoch": policy["source_date_epoch"],
    }
