#!/usr/bin/env python3
"""Build a deterministic, private technical archive of the locked Raptor sources.

This command consumes an already reconstructed local source cache.  It never
fetches or patches sources and it never includes the cache's ``.git`` data,
untracked files, build output, or credentials.  The result is a technical
Raptor-source closure only; it is not a complete-firmware corresponding-source
claim and it does not grant redistribution permission.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import BinaryIO, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from installer.raptor_source import (  # noqa: E402
    FULL_MEDIA_SOURCES,
    git as source_git,
    source_lock,
    verify_source,
)


SCHEMA_VERSION = 1
ARCHIVE_KIND = "private-technical-raptor-source-archive"
ARCHIVE_SCOPE = "raptor-13-source-closure"
ARCHIVE_MTIME = 0
ARCHIVE_FORMAT = "ustar"
LEGAL_REVIEW_STATUS = "not-assessed"
REDISTRIBUTION_STATUS = "not-authorized-by-this-repository"
SOURCE_LOCK_PATH = "components/raptor/source-build-lock.json"
HEADERS_INPUT_PATH = "components/raptor/headers-input.json"
SOURCE_ROOT = "sources"
REPOSITORY_ROOT = "repository"
HEX40 = set("0123456789abcdef")


class PrivateSourceArchiveError(ValueError):
    """The locked source cache cannot be archived safely."""


@dataclass(frozen=True)
class MetadataFile:
    source_path: str
    archive_path: str
    data: bytes
    sha256: str


@dataclass(frozen=True)
class SourceEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str
    git_object: str
    link_target: str | None = None


@dataclass(frozen=True)
class Gitlink:
    path: str
    commit: str
    delivery: str
    source: str | None


@dataclass(frozen=True)
class SourceSnapshot:
    name: str
    root: Path
    spec: dict[str, object]
    tracked_content_sha256: str
    entries: tuple[SourceEntry, ...]
    gitlinks: tuple[Gitlink, ...]


def _safe_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PrivateSourceArchiveError(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
        or "\x00" in value
    ):
        raise PrivateSourceArchiveError(f"{label} is not a safe relative path")
    return value


def _real_directory(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise PrivateSourceArchiveError(f"{label} is not a real directory")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise PrivateSourceArchiveError(f"{label} cannot be resolved") from exc


def _safe_repository_file(repository: Path, relative: str) -> Path:
    relative = _safe_relative(relative, "repository metadata path")
    current = repository
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PrivateSourceArchiveError(
                f"repository metadata path is a symlink: {relative}"
            )
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(repository)
    except (OSError, ValueError) as exc:
        raise PrivateSourceArchiveError(
            f"repository metadata path escapes repository: {relative}"
        ) from exc
    if not resolved.is_file():
        raise PrivateSourceArchiveError(f"repository metadata file is missing: {relative}")
    return resolved


def _stable_file(path: Path, label: str) -> tuple[bytes, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PrivateSourceArchiveError(f"{label} cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PrivateSourceArchiveError(f"{label} is not a regular file")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or total != before.st_size:
            raise PrivateSourceArchiveError(f"{label} changed while being read")
        return b"".join(chunks), digest.hexdigest()
    except PrivateSourceArchiveError:
        raise
    except OSError as exc:
        raise PrivateSourceArchiveError(f"{label} cannot be read safely") from exc
    finally:
        os.close(descriptor)


def _metadata_files(repository: Path, lock: dict[str, object]) -> tuple[MetadataFile, ...]:
    source_entries = lock.get("sources")
    if not isinstance(source_entries, dict):
        raise PrivateSourceArchiveError("Raptor source lock has no source entries")
    relative_paths = {SOURCE_LOCK_PATH, HEADERS_INPUT_PATH}
    for name, spec in source_entries.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise PrivateSourceArchiveError("Raptor source lock entry is malformed")
        patches = spec.get("patches")
        if not isinstance(patches, list):
            raise PrivateSourceArchiveError(f"Raptor source patches are malformed: {name}")
        for patch in patches:
            if not isinstance(patch, dict):
                raise PrivateSourceArchiveError(f"Raptor source patch is malformed: {name}")
            relative_paths.add(_safe_relative(patch.get("path"), f"{name} patch path"))

    result: list[MetadataFile] = []
    for relative in sorted(relative_paths):
        path = _safe_repository_file(repository, relative)
        data, digest = _stable_file(path, relative)
        result.append(
            MetadataFile(
                source_path=relative,
                archive_path=f"{REPOSITORY_ROOT}/{relative}",
                data=data,
                sha256=digest,
            )
        )
    return tuple(result)


def _metadata_identity(metadata: Iterable[MetadataFile]) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (item.source_path, item.sha256, len(item.data))
        for item in sorted(metadata, key=lambda item: item.source_path)
    )


def _mode_from_index(mode: str, path: str) -> int:
    if mode == "100644":
        return 0o644
    if mode == "100755":
        return 0o755
    if mode == "120000":
        return 0o777
    if mode == "160000":
        return 0o160000
    raise PrivateSourceArchiveError(f"unsupported tracked mode: {path}")


def _index_entries(source: Path) -> list[tuple[str, str, str, str]]:
    try:
        raw = source_git(source, "ls-files", "--stage", "-z")
    except Exception as exc:
        raise PrivateSourceArchiveError("source cache index cannot be inspected") from exc
    entries: list[tuple[str, str, str, str]] = []
    for record in raw.split("\x00"):
        if not record:
            continue
        try:
            header, path = record.split("\t", 1)
            mode, commit, stage = header.split(" ", 2)
        except ValueError as exc:
            raise PrivateSourceArchiveError("source cache index entry is malformed") from exc
        path = _safe_relative(path, "tracked source path")
        if len(commit) != 40 or set(commit) - HEX40:
            raise PrivateSourceArchiveError(f"tracked object identity is malformed: {path}")
        if stage != "0":
            raise PrivateSourceArchiveError(f"unmerged source index entry: {path}")
        _mode_from_index(mode, path)
        entries.append((mode, commit, stage, path))
    if len({entry[3] for entry in entries}) != len(entries):
        raise PrivateSourceArchiveError("source cache index contains duplicate paths")
    return sorted(entries, key=lambda entry: entry[3])


def _validate_symlink(path: str, target: str) -> None:
    if not target or "\x00" in target or target.startswith("/") or target.startswith("\\"):
        raise PrivateSourceArchiveError(f"unsafe source symlink: {path}")
    if "\\" in target:
        raise PrivateSourceArchiveError(f"unsafe source symlink: {path}")
    parent = PurePosixPath(path).parent.as_posix()
    normalized = posixpath.normpath(posixpath.join(parent, target))
    if normalized == ".." or normalized.startswith("../"):
        raise PrivateSourceArchiveError(f"source symlink escapes its tree: {path}")


def _source_member_path(root: Path, relative: str) -> Path:
    """Resolve a tracked member without traversing a symlinked directory."""

    parts = PurePosixPath(relative).parts
    current = root
    for part in parts[:-1]:
        current /= part
        try:
            if current.is_symlink() or not current.is_dir():
                raise PrivateSourceArchiveError(
                    f"tracked source parent is not a real directory: {relative}"
                )
        except OSError as exc:
            raise PrivateSourceArchiveError(
                f"tracked source parent cannot be inspected: {relative}"
            ) from exc
    return current / parts[-1]


def _read_regular_identity(path: Path, label: str) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PrivateSourceArchiveError(f"{label} cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PrivateSourceArchiveError(f"{label} is not a regular file")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
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
        if before_identity != after_identity or total != before.st_size:
            raise PrivateSourceArchiveError(f"{label} changed while being read")
        return total, digest.hexdigest()
    except PrivateSourceArchiveError:
        raise
    except OSError as exc:
        raise PrivateSourceArchiveError(f"{label} cannot be read safely") from exc
    finally:
        os.close(descriptor)


def _git_object_type(source: Path, object_id: str, label: str) -> str:
    try:
        return source_git(source, "cat-file", "-t", object_id)
    except Exception as exc:
        raise PrivateSourceArchiveError(f"{label} Git object cannot be inspected") from exc


def _git_environment() -> dict[str, str]:
    allowed = {
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        {
            "GIT_ASKPASS": "",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _open_git_blob(source: Path, object_id: str, label: str) -> subprocess.Popen[bytes]:
    if _git_object_type(source, object_id, label) != "blob":
        raise PrivateSourceArchiveError(f"{label} is not a Git blob")
    try:
        return subprocess.Popen(
            [
                "git",
                "--no-replace-objects",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=",
                "-c",
                "credential.helper=",
                "cat-file",
                "blob",
                object_id,
            ],
            cwd=source,
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise PrivateSourceArchiveError(f"{label} Git blob cannot be opened") from exc


def _git_blob_identity(source: Path, object_id: str, label: str) -> tuple[int, str]:
    process = _open_git_blob(source, object_id, label)
    assert process.stdout is not None
    digest = hashlib.sha256()
    total = 0
    try:
        while chunk := process.stdout.read(1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
    except OSError as exc:
        process.kill()
        process.wait()
        raise PrivateSourceArchiveError(f"{label} Git blob cannot be read") from exc
    finally:
        process.stdout.close()
    if process.wait() != 0:
        raise PrivateSourceArchiveError(f"{label} Git blob cannot be read")
    return total, digest.hexdigest()


def _tracked_content_sha256(
    name: str, entries: Iterable[SourceEntry], gitlinks: Iterable[Gitlink]
) -> str:
    digest = hashlib.sha256()
    records: list[dict[str, object]] = []
    for entry in entries:
        record: dict[str, object] = {
            "git_object": entry.git_object,
            "kind": entry.kind,
            "mode": format(entry.mode, "04o"),
            "path": entry.path,
            "sha256": entry.sha256,
            "size": entry.size,
        }
        if entry.link_target is not None:
            record["target"] = entry.link_target
        records.append(record)
    records.extend(
        {
            "commit": link.commit,
            "delivery": link.delivery,
            "kind": "gitlink",
            "path": link.path,
            **({"source": link.source} if link.source is not None else {}),
        }
        for link in gitlinks
    )
    for record in sorted(records, key=lambda item: (str(item["path"]), str(item["kind"]))):
        digest.update(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
        )
    digest.update(name.encode("utf-8"))
    return digest.hexdigest()


def _gitlink_delivery(source_name: str, path: str) -> tuple[str, str | None]:
    known = {
        ("mbedtls", "framework"): "mbedtls-framework",
        ("raptor-hal", "ingenic-headers"): "ingenic-headers",
    }
    target = known.get((source_name, path))
    if target is not None:
        return "separate-locked-source-archive", target
    if path == "run-clang-format":
        return "not-included-development-formatter-scope-unreviewed", None
    raise PrivateSourceArchiveError(
        f"unclassified source gitlink requires an explicit delivery decision: "
        f"{source_name}/{path}"
    )


def _collect_source(
    name: str,
    root: Path,
    spec: dict[str, object],
    source_specs: dict[str, object],
) -> SourceSnapshot:
    expected_tree = spec.get("tree")
    if not isinstance(expected_tree, str):
        raise PrivateSourceArchiveError(f"source tree identity is malformed: {name}")
    try:
        before_digest = verify_source(root, spec, tree=expected_tree)
    except Exception as exc:
        raise PrivateSourceArchiveError(f"source cache integrity check failed: {name}") from exc

    entries: list[SourceEntry] = []
    gitlinks: list[Gitlink] = []
    for mode, commit, _stage, relative in _index_entries(root):
        if any(part == ".git" for part in PurePosixPath(relative).parts):
            raise PrivateSourceArchiveError(f"tracked .git path is not allowed: {name}")
        mode_value = _mode_from_index(mode, relative)
        path = _source_member_path(root, relative)
        if mode == "160000":
            delivery, source = _gitlink_delivery(name, relative)
            if delivery == "separate-locked-source-archive":
                target_spec = source_specs.get(source or "")
                if not isinstance(target_spec, dict) or target_spec.get("base") != commit:
                    raise PrivateSourceArchiveError(
                        f"source gitlink does not match its locked target base: "
                        f"{name}/{relative}"
                    )
            gitlinks.append(
                Gitlink(path=relative, commit=commit, delivery=delivery, source=source)
            )
            continue
        if mode == "120000":
            try:
                if not path.is_symlink():
                    raise OSError("tracked symlink is absent")
                target = os.readlink(path)
            except OSError as exc:
                raise PrivateSourceArchiveError(f"tracked source symlink cannot be read: {name}") from exc
            _validate_symlink(relative, target)
            try:
                target_bytes = target.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PrivateSourceArchiveError(
                    f"tracked source symlink target is not UTF-8: {name}"
                ) from exc
            target_size, target_digest = _git_blob_identity(
                root, commit, f"tracked source symlink: {name}/{relative}"
            )
            if target_size != len(target_bytes) or target_digest != hashlib.sha256(target_bytes).hexdigest():
                raise PrivateSourceArchiveError(
                    f"tracked source symlink differs from its Git blob: {name}/{relative}"
                )
            entries.append(
                SourceEntry(
                    path=relative,
                    kind="symlink",
                    mode=mode_value,
                    size=0,
                    sha256=target_digest,
                    git_object=commit,
                    link_target=target,
                )
            )
            continue
        size, digest = _git_blob_identity(
            root, commit, f"tracked source file: {name}/{relative}"
        )
        entries.append(
            SourceEntry(
                path=relative,
                kind="file",
                mode=mode_value,
                size=size,
                sha256=digest,
                git_object=commit,
            )
        )

    try:
        after_digest = verify_source(root, spec, tree=expected_tree)
    except Exception as exc:
        raise PrivateSourceArchiveError(f"source cache changed while reading: {name}") from exc
    if before_digest != after_digest:
        raise PrivateSourceArchiveError(f"source cache changed while reading: {name}")
    return SourceSnapshot(
        name=name,
        root=root,
        spec=spec,
        tracked_content_sha256=_tracked_content_sha256(name, entries, gitlinks),
        entries=tuple(entries),
        gitlinks=tuple(gitlinks),
    )


def _source_manifest(snapshot: SourceSnapshot) -> dict[str, object]:
    spec = snapshot.spec
    evidence_name = "license" if "license" in spec else "notice"
    patches = spec.get("patches")
    if not isinstance(patches, list):
        raise PrivateSourceArchiveError(f"source patches are malformed: {snapshot.name}")
    return {
        "origin": spec["url"],
        "base": spec["base"],
        "base_tree": spec["base_tree"],
        "final_tree": spec["tree"],
        "tracked_content_sha256": snapshot.tracked_content_sha256,
        "archive_root": f"{SOURCE_ROOT}/{snapshot.name}",
        "file_count": len(snapshot.entries),
        "bytes": sum(entry.size for entry in snapshot.entries),
        "files": [
            {
                "path": entry.path,
                "kind": entry.kind,
                "mode": format(entry.mode, "04o"),
                "size": entry.size,
                "sha256": entry.sha256,
                "git_object": entry.git_object,
                **(
                    {"target": entry.link_target}
                    if entry.link_target is not None
                    else {}
                ),
            }
            for entry in snapshot.entries
        ],
        "gitlinks": [
            {
                "path": link.path,
                "commit": link.commit,
                "delivery": link.delivery,
                **({"source": link.source} if link.source is not None else {}),
            }
            for link in snapshot.gitlinks
        ],
        "patches": [
            {"path": patch["path"], "sha256": patch["sha256"]}
            for patch in patches
            if isinstance(patch, dict)
        ],
        "license_or_notice": {
            "kind": "license" if evidence_name == "license" else "notice",
            "path": spec[evidence_name],
            "sha256": spec[f"{evidence_name}_sha256"],
        },
    }


def _canonical_json(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _tar_info(name: str, *, mode: int, kind: str, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = ARCHIVE_MTIME
    info.size = size
    if kind == "directory":
        info.type = tarfile.DIRTYPE
    elif kind == "symlink":
        info.type = tarfile.SYMTYPE
    elif kind != "file":
        raise PrivateSourceArchiveError("unsupported archive member kind")
    return info


def _directory_members(paths: Iterable[str]) -> list[str]:
    directories: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]) + "/")
    return sorted(directories, key=lambda value: (value.count("/"), value))


class _HashingReader:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.digest.update(data)
        self.total += len(data)
        return data


def _stream_git_blob(
    archive: tarfile.TarFile,
    snapshot: SourceSnapshot,
    entry: SourceEntry,
    archive_name: str,
) -> None:
    process = _open_git_blob(
        snapshot.root,
        entry.git_object,
        f"tracked source file: {snapshot.name}/{entry.path}",
    )
    assert process.stdout is not None
    reader = _HashingReader(process.stdout)
    try:
        archive.addfile(
            _tar_info(archive_name, mode=entry.mode, kind="file", size=entry.size),
            reader,
        )
        while reader.read(1024 * 1024):
            pass
        if process.wait() != 0:
            raise PrivateSourceArchiveError(
                f"source Git blob cannot be read: {snapshot.name}"
            )
        if (
            reader.total != entry.size
            or reader.digest.hexdigest() != entry.sha256
        ):
            raise PrivateSourceArchiveError(
                f"source Git blob changed while archiving: {snapshot.name}"
            )
    except PrivateSourceArchiveError:
        raise
    except OSError as exc:
        raise PrivateSourceArchiveError(
            f"source Git blob cannot be read: {snapshot.name}"
        ) from exc
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def _write_archive(
    temporary: Path,
    manifest: dict[str, object],
    metadata: tuple[MetadataFile, ...],
    snapshots: tuple[SourceSnapshot, ...],
) -> None:
    manifest_data = _canonical_json(manifest)
    with temporary.open("w+b") as handle:
        with tarfile.open(fileobj=handle, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            archive.addfile(
                _tar_info("manifest.json", mode=0o644, kind="file", size=len(manifest_data)),
                io.BytesIO(manifest_data),
            )

            metadata_paths = [item.archive_path for item in metadata]
            metadata_dirs = _directory_members(metadata_paths)
            source_paths: list[str] = []
            for snapshot in snapshots:
                root_name = f"{SOURCE_ROOT}/{snapshot.name}/"
                source_paths.append(root_name)
                source_paths.extend(
                    f"{SOURCE_ROOT}/{snapshot.name}/{entry.path}"
                    for entry in snapshot.entries
                )
            source_dirs = _directory_members(source_paths)
            all_dirs = sorted(
                {"repository/", "sources/", *metadata_dirs, *source_dirs},
                key=lambda value: (value.count("/"), value),
            )
            for directory in all_dirs:
                archive.addfile(_tar_info(directory, mode=0o755, kind="directory"))

            for item in sorted(metadata, key=lambda item: item.archive_path):
                archive.addfile(
                    _tar_info(
                        item.archive_path,
                        mode=0o644,
                        kind="file",
                        size=len(item.data),
                    ),
                    io.BytesIO(item.data),
                )

            for snapshot in snapshots:
                for entry in snapshot.entries:
                    archive_name = f"{SOURCE_ROOT}/{snapshot.name}/{entry.path}"
                    if entry.kind == "file":
                        _stream_git_blob(archive, snapshot, entry, archive_name)
                    else:
                        info = _tar_info(archive_name, mode=entry.mode, kind="symlink")
                        info.linkname = entry.link_target or ""
                        archive.addfile(info)
        handle.flush()
        os.fsync(handle.fileno())


def _temporary_path() -> Path:
    temporary_root_value = os.environ.get("TMPDIR")
    if not temporary_root_value:
        raise PrivateSourceArchiveError("TMPDIR is required for archive staging")
    temporary_root = Path(temporary_root_value)
    if temporary_root.is_symlink() or not temporary_root.is_dir():
        raise PrivateSourceArchiveError("TMPDIR is not a real directory")
    descriptor, name = tempfile.mkstemp(
        prefix=".raptor-private-source-",
        suffix=".tmp",
        dir=temporary_root,
    )
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    return Path(name)


def _validate_output(output: Path) -> Path:
    output = Path(output)
    parent = output.parent
    _real_directory(parent, "archive output parent")
    if output.exists() or output.is_symlink():
        raise PrivateSourceArchiveError("archive output already exists")
    return output


def _sha256_regular(path: Path) -> str:
    _size, digest = _read_regular_identity(path, "archive output")
    return digest


def _remove_new_output(output: Path) -> None:
    """Remove only the regular file this invocation just published."""

    try:
        if not output.is_symlink():
            output.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _publish_exclusive(temporary: Path, output: Path) -> None:
    try:
        if temporary.stat().st_dev == output.parent.stat().st_dev:
            try:
                os.link(temporary, output, follow_symlinks=False)
            except FileExistsError as exc:
                raise PrivateSourceArchiveError("archive output already exists") from exc
            temporary.unlink()
            return

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(output, flags, 0o600)
        try:
            with temporary.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
                descriptor = -1
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                output.unlink()
            except OSError:
                pass
            raise
        temporary.unlink()
    except PrivateSourceArchiveError:
        raise
    except OSError as exc:
        raise PrivateSourceArchiveError("archive output could not be published exclusively") from exc


def build_private_source_archive(
    repository: Path,
    verified_cache_root: Path,
    output: Path,
) -> dict[str, object]:
    """Build one private archive from an already verified reconstructed cache."""

    repository = _real_directory(Path(repository), "repository")
    verified_cache_root = _real_directory(Path(verified_cache_root), "verified source cache")
    output = _validate_output(Path(output))

    try:
        lock = source_lock(repository, full_media=True)
    except Exception as exc:
        raise PrivateSourceArchiveError("Raptor source-lock validation failed") from exc
    sources = lock.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(FULL_MEDIA_SOURCES):
        raise PrivateSourceArchiveError("Raptor source closure must contain exactly 13 sources")
    metadata = _metadata_files(repository, lock)
    metadata_identity = _metadata_identity(metadata)

    snapshots: list[SourceSnapshot] = []
    for name in sorted(FULL_MEDIA_SOURCES):
        source = verified_cache_root / name
        if source.is_symlink() or not source.is_dir():
            raise PrivateSourceArchiveError(f"verified source cache is missing: {name}")
        snapshots.append(_collect_source(name, source, sources[name], sources))
    snapshots_tuple = tuple(snapshots)

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": ARCHIVE_KIND,
        "archive_format": ARCHIVE_FORMAT,
        "archive_mtime": ARCHIVE_MTIME,
        "scope": ARCHIVE_SCOPE,
        "technical_status": "locked-source-cache-verified",
        "firmware_corresponding_source_complete": False,
        "legal_review_status": LEGAL_REVIEW_STATUS,
        "redistribution": REDISTRIBUTION_STATUS,
        "notice": (
            "Technical Raptor source provenance and integrity only; this archive "
            "is private evidence and does not authorize redistribution."
        ),
        "source_lock": {
            "path": SOURCE_LOCK_PATH,
            "sha256": next(item.sha256 for item in metadata if item.source_path == SOURCE_LOCK_PATH),
        },
        "headers_input": {
            "path": HEADERS_INPUT_PATH,
            "sha256": next(item.sha256 for item in metadata if item.source_path == HEADERS_INPUT_PATH),
        },
        "source_count": len(snapshots_tuple),
        "metadata": [
            {
                "archive_path": item.archive_path,
                "source_path": item.source_path,
                "size": len(item.data),
                "sha256": item.sha256,
            }
            for item in metadata
        ],
        "sources": {
            snapshot.name: _source_manifest(snapshot)
            for snapshot in snapshots_tuple
        },
    }

    temporary: Path | None = None
    try:
        temporary = _temporary_path()
        _write_archive(temporary, manifest, metadata, snapshots_tuple)

        metadata_after = _metadata_files(repository, lock)
        if _metadata_identity(metadata_after) != metadata_identity:
            raise PrivateSourceArchiveError("repository lock or patch inputs changed while archiving")
        for snapshot in snapshots_tuple:
            try:
                verify_source(snapshot.root, snapshot.spec, tree=snapshot.spec["tree"])
            except Exception as exc:
                raise PrivateSourceArchiveError(
                    f"source cache changed while archiving: {snapshot.name}"
                ) from exc

        archive_sha256 = _sha256_regular(temporary)
        archive_size = temporary.stat().st_size
        _publish_exclusive(temporary, output)
        temporary = None
        try:
            published_size, published_sha256 = _read_regular_identity(
                output, "published archive"
            )
        except PrivateSourceArchiveError:
            _remove_new_output(output)
            raise
        if published_size != archive_size or published_sha256 != archive_sha256:
            _remove_new_output(output)
            raise PrivateSourceArchiveError("published archive readback mismatch")
        return {
            "ok": True,
            "archive": str(output),
            "sha256": published_sha256,
            "size": published_size,
            "source_count": len(snapshots_tuple),
            "scope": ARCHIVE_SCOPE,
            "firmware_corresponding_source_complete": False,
            "redistribution": REDISTRIBUTION_STATUS,
        }
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--verified-cache-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = build_private_source_archive(
            arguments.repository,
            arguments.verified_cache_root,
            arguments.output,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
