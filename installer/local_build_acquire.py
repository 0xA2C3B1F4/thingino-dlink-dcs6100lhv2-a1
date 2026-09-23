"""Acquire and validate locked public toolchains for the local firmware build."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from scripts import source_checkout

from .local_build_archive import normalize_parts, validate_tar_members
from .local_build_bootstrap import (
    LocalBuildBootstrapError,
    bootstrap_public_build_inputs,
    load_source_lock_snapshot,
)
from .sd_package import atomic_write


ARCHIVE_SCHEMA_VERSION = 1
EXTRACTION_SCHEMA_VERSION = 2
INPUTS_SCHEMA_VERSION = 2
RUST_TOOLCHAIN_SCHEMA_VERSION = 1
INGENIC_ARCHIVE_SCHEMA_VERSION = 1
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 12 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 350_000
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
ARCHIVE_NAMES = (
    "rust_source",
    "rust_std_source_component",
    "rust_toolchain_aarch64_linux",
)
EXTRACTION_RECEIPT = ".local-build-extraction.json"
RUST_RECEIPT = "local-build-rust.json"
EXPECTED_RUST_RELEASE = "1.95.0"
EXPECTED_RUST_COMMIT = "59807616e1fa2540724bfbac14d7976d7e4a3860"
RUST_SOURCE_TOP = "rustc-1.95.0-src"
RUST_SOURCE_INCLUDED_ROOTS = frozenset({"library", "vendor"})
RUST_SOURCE_SKIPPED_MEMBERS = frozenset(
    {
        f"{RUST_SOURCE_TOP}/vendor/scip-0.5.2/Readme.md",
        f"{RUST_SOURCE_TOP}/vendor/windows-link-0.2.0/readme.md",
        f"{RUST_SOURCE_TOP}/vendor/windows-sys-0.61.0/readme.md",
    }
)
RUST_SOURCE_ALLOWED_CASEFOLD_ALIASES = frozenset(
    {
        frozenset(
            {
                tuple(PurePosixPath(skipped).parts),
                tuple(PurePosixPath(skipped).with_name("README.md").parts),
            }
        )
        for skipped in RUST_SOURCE_SKIPPED_MEMBERS
    }
)


def _cache_generation_identity(label: str, *parts: str) -> str:
    """Return a domain-separated identity for one immutable cache generation."""

    document = json.dumps([label, *parts], separators=(",", ":"))
    return hashlib.sha256(document.encode("ascii")).hexdigest()


INGENIC_ARCHIVE_TOP = "ingenic-glibc216-toolchain"
INGENIC_ARCHIVE_REQUIRED = frozenset(
    {
        f"{INGENIC_ARCHIVE_TOP}/.SOURCE",
        f"{INGENIC_ARCHIVE_TOP}/VERSION",
        f"{INGENIC_ARCHIVE_TOP}/bin/mips-linux-gnu-ar",
        f"{INGENIC_ARCHIVE_TOP}/bin/mips-linux-gnu-gcc",
        f"{INGENIC_ARCHIVE_TOP}/bin/mips-linux-gnu-strip",
        f"{INGENIC_ARCHIVE_TOP}/mips-linux-gnu/libc/lib/ld-2.16.so",
        f"{INGENIC_ARCHIVE_TOP}/mips-linux-gnu/libc/lib/libc-2.16.so",
        f"{INGENIC_ARCHIVE_TOP}/mips-linux-gnu/libc/lib/libgcc_s.so.1",
    }
)


class LocalBuildAcquireError(ValueError):
    """A locked public local-build input failed acquisition or validation."""


class _RedirectPolicy(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request:
        parsed = urllib.parse.urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            raise LocalBuildAcquireError("archive redirect left the approved HTTPS hosts")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


@contextmanager
def _regular_snapshot(
    path: Path,
    label: str,
    *,
    limit: int = MAX_ARCHIVE_BYTES,
) -> Iterator[tuple[BinaryIO, int]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LocalBuildAcquireError(f"{label} cannot be opened safely") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise LocalBuildAcquireError(f"{label} is not a regular file")
        if details.st_size < 1 or details.st_size > limit:
            raise LocalBuildAcquireError(f"{label} violates its size policy")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            yield handle, details.st_size
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _regular(path: Path, label: str, *, limit: int = MAX_ARCHIVE_BYTES) -> Path:
    if path.is_symlink() or not path.is_file():
        raise LocalBuildAcquireError(f"{label} is not a regular file")
    size = path.stat().st_size
    if size < 1 or size > limit:
        raise LocalBuildAcquireError(f"{label} violates its size policy")
    return path


def _directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise LocalBuildAcquireError(f"{label} is not a real directory")
    return path.resolve(strict=True)


def _private_child_directory(parent: Path, *parts: str) -> Path:
    current = _directory(parent, "local-build cache root")
    if stat.S_IMODE(current.stat().st_mode) & 0o077:
        raise LocalBuildAcquireError(
            "local-build cache directory permissions are too broad"
        )
    for part in parts:
        if not part or part in {".", ".."} or "/" in part:
            raise LocalBuildAcquireError("local-build cache path is invalid")
        child = current / part
        if child.exists() or child.is_symlink():
            current = _directory(child, "local-build cache directory")
            if stat.S_IMODE(current.stat().st_mode) & 0o077:
                raise LocalBuildAcquireError(
                    "local-build cache directory permissions are too broad"
                )
        else:
            child.mkdir(mode=0o700)
            current = child.resolve(strict=True)
    return current


def _download(
    *, url: str, destination: Path, expected_sha256: str
) -> tuple[int, str]:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"static.rust-lang.org", "github.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LocalBuildAcquireError("archive URL is outside the approved HTTPS policy")
    allowed_hosts = {str(parsed.hostname)}
    if parsed.hostname == "github.com":
        allowed_hosts.update(
            {"objects.githubusercontent.com", "release-assets.githubusercontent.com"}
        )
    opener = urllib.request.build_opener(_RedirectPolicy(allowed_hosts))
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "thingino-dlink-local-builder/1"},
        method="GET",
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with opener.open(request, timeout=60) as response, destination.open("xb") as output:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in allowed_hosts:
                raise LocalBuildAcquireError("archive response left the approved HTTPS hosts")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise LocalBuildAcquireError("archive download exceeds its size policy")
                output.write(chunk)
                digest.update(chunk)
    except (OSError, urllib.error.URLError) as exc:
        raise LocalBuildAcquireError("archive download failed") from exc
    actual = digest.hexdigest()
    if size < 1 or actual != expected_sha256:
        raise LocalBuildAcquireError("archive download digest mismatch")
    return size, actual


def _archive_receipt(
    *, path: Path, name: str, url: str, sha256: str, size: int
) -> dict[str, object]:
    return {
        "archive": str(path),
        "name": name,
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "sha256": sha256,
        "size": size,
        "url": url,
    }


def _load_archive_receipt(path: Path) -> dict[str, object]:
    document = _load_json_object(path, "archive receipt")
    expected = {"archive", "name", "schema_version", "sha256", "size", "url"}
    if set(document) != expected or document.get("schema_version") != 1:
        raise LocalBuildAcquireError("archive receipt fields are invalid")
    return document


def acquire_archive(
    *, cache_root: Path, name: str, source: dict[str, object]
) -> dict[str, object]:
    if name not in ARCHIVE_NAMES:
        raise LocalBuildAcquireError("archive name is not allowlisted")
    url = source.get("url")
    expected_sha256 = source.get("sha256")
    if (
        not isinstance(url, str)
        or not isinstance(expected_sha256, str)
        or HEX64.fullmatch(expected_sha256) is None
    ):
        raise LocalBuildAcquireError("locked archive metadata is invalid")
    filename = Path(urllib.parse.urlsplit(url).path).name
    if not filename or filename in {".", ".."}:
        raise LocalBuildAcquireError("locked archive filename is invalid")
    parent = _private_child_directory(cache_root, "archives", name)
    directory = parent / expected_sha256
    archive = directory / filename
    receipt_path = directory / "archive.json"
    if directory.exists() or directory.is_symlink():
        directory = _directory(directory, f"cached {name} archive directory")
        receipt = _load_archive_receipt(receipt_path)
        with _regular_snapshot(archive, f"cached {name} archive") as (
            handle,
            size,
        ):
            actual = _archive_receipt(
                path=archive,
                name=name,
                url=url,
                sha256=_sha256_stream(handle),
                size=size,
            )
        if receipt != actual or actual["sha256"] != expected_sha256:
            raise LocalBuildAcquireError(f"cached {name} archive changed")
        return receipt
    temporary_directory = Path(
        tempfile.mkdtemp(prefix=f".{expected_sha256}.acquire-", dir=parent)
    )
    temporary = temporary_directory / filename
    try:
        size, actual_sha256 = _download(
            url=url,
            destination=temporary,
            expected_sha256=expected_sha256,
        )
        temporary.chmod(0o600)
        receipt = _archive_receipt(
            path=archive,
            name=name,
            url=url,
            sha256=actual_sha256,
            size=size,
        )
        atomic_write(
            temporary_directory / "archive.json",
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
        )
        (temporary_directory / "archive.json").chmod(0o600)
        with _regular_snapshot(temporary, f"downloaded {name} archive") as (
            handle,
            final_size,
        ):
            if final_size != size or _sha256_stream(handle) != expected_sha256:
                raise LocalBuildAcquireError(f"downloaded {name} archive changed")
        os.replace(temporary_directory, directory)
    except BaseException:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        raise
    return receipt


def _normalize_parts(path: PurePosixPath) -> tuple[str, ...] | None:
    return normalize_parts(path)


def _validate_tar_members(
    bundle: tarfile.TarFile,
    *,
    expected_top: str,
    included_roots: frozenset[str] | None = None,
    allowed_casefold_aliases: frozenset[
        frozenset[tuple[str, ...]]
    ] = frozenset(),
) -> None:
    validate_tar_members(
        bundle,
        expected_top=expected_top,
        max_members=MAX_ARCHIVE_MEMBERS,
        max_extracted_bytes=MAX_EXTRACTED_BYTES,
        reserved_root_name=EXTRACTION_RECEIPT,
        error=LocalBuildAcquireError,
        included_roots=included_roots,
        allowed_casefold_aliases=allowed_casefold_aliases,
    )


def _validate_tar(archive: Path, *, expected_top: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", expected_top) is None:
        raise LocalBuildAcquireError("archive expected root is invalid")
    try:
        with _regular_snapshot(archive, "locked archive") as (handle, _):
            with tarfile.open(fileobj=handle, mode="r:*") as bundle:
                _validate_tar_members(bundle, expected_top=expected_top)
    except (OSError, tarfile.TarError) as exc:
        raise LocalBuildAcquireError("archive cannot be read safely") from exc


def _tree_digest(
    root: Path,
    *,
    excluded_root_names: frozenset[str] = frozenset({EXTRACTION_RECEIPT}),
) -> str:
    root = _directory(root, "extracted archive")
    digest = hashlib.sha256()
    count = 0
    total = 0

    def visit(directory: Path, relative: PurePosixPath) -> None:
        nonlocal count, total
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise LocalBuildAcquireError("extracted archive cannot be inventoried") from exc
        for entry in entries:
            if not relative.parts and entry.name in excluded_root_names:
                continue
            count += 1
            if count > MAX_ARCHIVE_MEMBERS:
                raise LocalBuildAcquireError("extracted archive has too many members")
            path = Path(entry.path)
            member = relative / entry.name
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise LocalBuildAcquireError(
                    "extracted archive member cannot be inspected"
                ) from exc
            mode = stat.S_IMODE(details.st_mode)
            if entry.is_symlink():
                target = os.readlink(path)
                record = ["link", member.as_posix(), mode, target]
            elif entry.is_dir(follow_symlinks=False):
                record = ["directory", member.as_posix(), mode]
                digest.update(
                    (json.dumps(record, separators=(",", ":")) + "\n").encode()
                )
                visit(path, member)
                continue
            elif stat.S_ISREG(details.st_mode):
                total += details.st_size
                if total > MAX_EXTRACTED_BYTES:
                    raise LocalBuildAcquireError(
                        "extracted archive exceeds its size policy"
                    )
                record = [
                    "file",
                    member.as_posix(),
                    mode,
                    details.st_size,
                    _sha256(path),
                ]
            else:
                raise LocalBuildAcquireError(
                    "extracted archive contains an unsupported member type"
                )
            digest.update(
                (json.dumps(record, separators=(",", ":")) + "\n").encode()
            )

    visit(root, PurePosixPath())
    return digest.hexdigest()


def _extraction_receipt(
    *,
    archive_name: str,
    archive_sha256: str,
    archive_size: int,
    expected_top: str,
    included_roots: frozenset[str] | None,
    skipped_members: frozenset[str],
    tree_sha256: str,
) -> dict[str, object]:
    return {
        "archive_name": archive_name,
        "archive_sha256": archive_sha256,
        "archive_size": archive_size,
        "expected_top": expected_top,
        "included_roots": (
            None if included_roots is None else sorted(included_roots)
        ),
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "skipped_members": sorted(skipped_members),
        "tree_sha256": tree_sha256,
    }


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise LocalBuildAcquireError(f"{label} contains duplicate fields")
            document[key] = value
        return document

    try:
        with _regular_snapshot(path, label, limit=MAX_RECEIPT_BYTES) as (
            handle,
            _,
        ):
            document = json.loads(
                handle.read().decode("utf-8"),
                object_pairs_hook=unique_object,
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBuildAcquireError(f"{label} is invalid") from exc
    if not isinstance(document, dict):
        raise LocalBuildAcquireError(f"{label} is invalid")
    return document


def extract_archive(
    *,
    archive: Path,
    expected_sha256: str,
    output_parent: Path,
    expected_top: str,
    included_roots: frozenset[str] | None = None,
    allowed_casefold_aliases: frozenset[
        frozenset[tuple[str, ...]]
    ] = frozenset(),
    skipped_members: frozenset[str] = frozenset(),
) -> Path:
    if HEX64.fullmatch(expected_sha256) is None:
        raise LocalBuildAcquireError("locked archive digest is invalid")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", expected_top) is None:
        raise LocalBuildAcquireError("archive expected root is invalid")
    if included_roots is not None and (
        not included_roots
        or any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", root) is None
            for root in included_roots
        )
    ):
        raise LocalBuildAcquireError("archive included roots are invalid")
    for name in skipped_members:
        normalized = normalize_parts(PurePosixPath(name))
        if (
            included_roots is None
            or normalized is None
            or len(normalized) < 3
            or normalized[0] != expected_top
            or normalized[1] not in included_roots
            or PurePosixPath(name).is_absolute()
            or tuple(PurePosixPath(name).parts) != normalized
        ):
            raise LocalBuildAcquireError("archive skipped member policy is invalid")
    output_parent = _directory(output_parent, "archive extraction parent")
    destination = output_parent / expected_top
    with _regular_snapshot(archive, "locked archive") as (handle, archive_size):
        actual_sha256 = _sha256_stream(handle)
        if actual_sha256 != expected_sha256:
            raise LocalBuildAcquireError("locked archive digest changed")
        try:
            with tarfile.open(fileobj=handle, mode="r:*") as bundle:
                _validate_tar_members(
                    bundle,
                    expected_top=expected_top,
                    included_roots=included_roots,
                    allowed_casefold_aliases=allowed_casefold_aliases,
                )
        except (OSError, tarfile.TarError) as exc:
            raise LocalBuildAcquireError("archive cannot be read safely") from exc
        if destination.exists() or destination.is_symlink():
            destination = _directory(destination, "cached extracted archive")
            receipt = _load_json_object(
                destination / EXTRACTION_RECEIPT,
                "cached extraction receipt",
            )
            actual = _extraction_receipt(
                archive_name=archive.name,
                archive_sha256=actual_sha256,
                archive_size=archive_size,
                expected_top=expected_top,
                included_roots=included_roots,
                skipped_members=skipped_members,
                tree_sha256=_tree_digest(destination),
            )
            if receipt != actual:
                raise LocalBuildAcquireError("cached extracted archive changed")
            return destination
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{expected_top}.extract-", dir=output_parent)
        )
        try:
            handle.seek(0)
            try:
                with tarfile.open(fileobj=handle, mode="r:*") as bundle:
                    def selected_member(
                        member: tarfile.TarInfo,
                        path: str,
                    ) -> tarfile.TarInfo | None:
                        normalized = normalize_parts(PurePosixPath(member.name))
                        if normalized is None:
                            return None
                        if member.name in skipped_members:
                            return None
                        if (
                            included_roots is not None
                            and len(normalized) > 1
                            and normalized[1] not in included_roots
                        ):
                            return None
                        return tarfile.data_filter(member, path)

                    bundle.extractall(temporary, filter=selected_member)
            except (OSError, tarfile.TarError) as exc:
                raise LocalBuildAcquireError("archive extraction failed") from exc
            extracted = temporary / expected_top
            _directory(extracted, "extracted archive")
            if included_roots is not None:
                for root in included_roots:
                    _directory(extracted / root, f"extracted {root} root")
            receipt = _extraction_receipt(
                archive_name=archive.name,
                archive_sha256=actual_sha256,
                archive_size=archive_size,
                expected_top=expected_top,
                included_roots=included_roots,
                skipped_members=skipped_members,
                tree_sha256=_tree_digest(extracted),
            )
            atomic_write(
                extracted / EXTRACTION_RECEIPT,
                (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
            )
            (extracted / EXTRACTION_RECEIPT).chmod(0o600)
            os.replace(extracted, destination)
            temporary.rmdir()
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    return destination


def _run(arguments: list[str], *, label: str, timeout: int = 1800) -> str:
    from .local_build_toolchain import _run as _impl

    return _impl(sys.modules[__name__], arguments, label=label, timeout=timeout)


def _git_environment() -> dict[str, str]:
    from .local_build_toolchain import _git_environment as _impl

    return _impl(sys.modules[__name__])


def _git(
    arguments: list[str],
    *,
    checkout: Path,
    label: str,
    network: bool = False,
    timeout: int = 20,
) -> str:
    from .local_build_toolchain import _git as _impl

    return _impl(sys.modules[__name__], arguments, checkout=checkout, label=label, network=network, timeout=timeout)


def _verify_git_checkout(
    *,
    checkout: Path,
    revision: str,
    tree: str,
    canonical_url: str,
) -> dict[str, str]:
    from .local_build_toolchain import _verify_git_checkout as _impl

    return _impl(sys.modules[__name__], checkout=checkout, revision=revision, tree=tree, canonical_url=canonical_url)


def _git_checkout(
    *, destination: Path, source: dict[str, object]
) -> tuple[Path, dict[str, str]]:
    from .local_build_toolchain import _git_checkout as _impl

    return _impl(sys.modules[__name__], destination=destination, source=source)


def _verify_bare_git_repository(
    *,
    repository: Path,
    revision: str,
    tree: str,
    canonical_url: str,
) -> dict[str, str]:
    repository = _directory(repository, "cached Ingenic bare repository")
    alternates = repository / "objects/info/alternates"
    if alternates.exists() or alternates.is_symlink():
        raise LocalBuildAcquireError("Ingenic bare repository uses object alternates")
    actual_revision = _git(
        ["rev-parse", f"{revision}^{{commit}}"],
        checkout=repository,
        label="Ingenic revision check",
    )
    actual_tree = _git(
        ["rev-parse", f"{revision}^{{tree}}"],
        checkout=repository,
        label="Ingenic tree check",
    )
    origin = _git(
        ["remote", "get-url", "origin"],
        checkout=repository,
        label="Ingenic origin check",
    )
    bare = _git(
        ["rev-parse", "--is-bare-repository"],
        checkout=repository,
        label="Ingenic bare repository check",
    )
    replacements = _git(
        ["for-each-ref", "--format=%(refname)", "refs/replace"],
        checkout=repository,
        label="Ingenic replacement check",
    )
    if (
        actual_revision != revision
        or actual_tree != tree
        or bare != "true"
        or replacements
        or source_checkout.canonical_git_url(origin, "Ingenic toolchain origin")
        != canonical_url
    ):
        raise LocalBuildAcquireError("cached Ingenic bare repository identity changed")
    _git(
        ["fsck", "--strict", "--no-reflogs", revision],
        checkout=repository,
        label="Ingenic object verification",
        timeout=600,
    )
    return {"revision": actual_revision, "tree": actual_tree, "url": canonical_url}


def _acquire_ingenic_bare_repository(
    *, destination: Path, source: dict[str, object]
) -> tuple[Path, dict[str, str]]:
    url = source.get("url")
    revision = source.get("revision")
    tree = source.get("tree")
    if (
        not isinstance(url, str)
        or not isinstance(revision, str)
        or not isinstance(tree, str)
        or HEX40.fullmatch(revision) is None
        or HEX40.fullmatch(tree) is None
    ):
        raise LocalBuildAcquireError("locked Ingenic metadata is invalid")
    canonical = source_checkout.canonical_git_url(url, "Ingenic toolchain URL")
    _private_child_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        repository = _directory(destination, "cached Ingenic bare repository")
    else:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.fetch-", dir=destination.parent)
        )
        try:
            _git(
                ["init", "--bare", "--quiet"],
                checkout=temporary,
                label="Ingenic bare repository initialization",
            )
            _git(
                ["remote", "add", "origin", url],
                checkout=temporary,
                label="Ingenic remote configuration",
            )
            _git(
                ["fetch", "--depth=1", "--no-tags", "origin", revision],
                checkout=temporary,
                label="Ingenic toolchain fetch",
                network=True,
                timeout=600,
            )
            identity = _verify_bare_git_repository(
                repository=temporary,
                revision=revision,
                tree=tree,
                canonical_url=canonical,
            )
            os.replace(temporary, destination)
            repository = destination
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    identity = _verify_bare_git_repository(
        repository=repository,
        revision=revision,
        tree=tree,
        canonical_url=canonical,
    )
    return repository, identity


def _validate_ingenic_archive(archive: Path) -> None:
    try:
        with _regular_snapshot(archive, "Ingenic toolchain archive") as (handle, _):
            with tarfile.open(fileobj=handle, mode="r:") as bundle:
                validate_tar_members(
                    bundle,
                    expected_top=INGENIC_ARCHIVE_TOP,
                    max_members=MAX_ARCHIVE_MEMBERS,
                    max_extracted_bytes=MAX_EXTRACTED_BYTES,
                    reserved_root_name=EXTRACTION_RECEIPT,
                    error=LocalBuildAcquireError,
                    enforce_casefold_uniqueness=False,
                )
            handle.seek(0)
            with tarfile.open(fileobj=handle, mode="r:") as bundle:
                names = {member.name for member in bundle}
    except (OSError, tarfile.TarError) as exc:
        raise LocalBuildAcquireError("Ingenic toolchain archive is invalid") from exc
    if not INGENIC_ARCHIVE_REQUIRED.issubset(names):
        raise LocalBuildAcquireError("Ingenic toolchain archive lacks required files")


def _acquire_ingenic_toolchain(
    *, build_root: Path, cache_root: Path, source: dict[str, object]
) -> tuple[Path, dict[str, object]]:
    revision = source.get("revision")
    tree = source.get("tree")
    if (
        not isinstance(revision, str)
        or not isinstance(tree, str)
        or HEX40.fullmatch(revision) is None
        or HEX40.fullmatch(tree) is None
    ):
        raise LocalBuildAcquireError("locked Ingenic metadata is invalid")
    repository, identity = _acquire_ingenic_bare_repository(
        destination=build_root / "cache/sources" / f"ingenic-glibc216-{revision}.git",
        source=source,
    )
    parent = _private_child_directory(cache_root, "generated", "ingenic-glibc216")
    directory = parent / tree
    archive_name = f"ingenic-glibc216-{revision}.tar"
    archive = directory / archive_name
    receipt_path = directory / "archive.json"
    if directory.exists() or directory.is_symlink():
        directory = _directory(directory, "cached Ingenic archive directory")
        receipt = _load_json_object(receipt_path, "cached Ingenic archive receipt")
        with _regular_snapshot(archive, "cached Ingenic toolchain archive") as (
            handle,
            size,
        ):
            actual_sha256 = _sha256_stream(handle)
        actual = {
            **identity,
            "archive": str(archive),
            "archive_sha256": actual_sha256,
            "archive_size": size,
            "schema_version": INGENIC_ARCHIVE_SCHEMA_VERSION,
        }
        if receipt != actual:
            raise LocalBuildAcquireError("cached Ingenic toolchain archive changed")
        _validate_ingenic_archive(archive)
        return archive, actual
    temporary = Path(tempfile.mkdtemp(prefix=f".{tree}.archive-", dir=parent))
    temporary_archive = temporary / archive_name
    try:
        _git(
            [
                "archive",
                "--format=tar",
                f"--prefix={INGENIC_ARCHIVE_TOP}/",
                f"--output={temporary_archive}",
                revision,
            ],
            checkout=repository,
            label="Ingenic toolchain archive creation",
            timeout=600,
        )
        temporary_archive.chmod(0o600)
        _validate_ingenic_archive(temporary_archive)
        with _regular_snapshot(
            temporary_archive, "generated Ingenic toolchain archive"
        ) as (handle, size):
            archive_sha256 = _sha256_stream(handle)
        receipt = {
            **identity,
            "archive": str(archive),
            "archive_sha256": archive_sha256,
            "archive_size": size,
            "schema_version": INGENIC_ARCHIVE_SCHEMA_VERSION,
        }
        atomic_write(
            temporary / "archive.json",
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
        )
        (temporary / "archive.json").chmod(0o600)
        os.replace(temporary, directory)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return archive, receipt


def _install_rust_toolchain(
    *,
    cache_root: Path,
    builder_image_id: str,
    rust_distribution: Path,
    rust_distribution_sha256: str,
    rust_source_component: Path,
    rust_source_component_sha256: str,
    identity: str,
) -> tuple[Path, dict[str, object]]:
    from .local_build_toolchain import _install_rust_toolchain as _impl

    return _impl(sys.modules[__name__], cache_root=cache_root, builder_image_id=builder_image_id, rust_distribution=rust_distribution, rust_distribution_sha256=rust_distribution_sha256, rust_source_component=rust_source_component, rust_source_component_sha256=rust_source_component_sha256, identity=identity)


def acquire_locked_public_inputs(*, build_root: Path) -> dict[str, object]:
    """Acquire Rust and Ingenic inputs after the public bootstrap is ready."""

    try:
        bootstrap = bootstrap_public_build_inputs(build_root=build_root)
    except LocalBuildBootstrapError as exc:
        raise LocalBuildAcquireError(str(exc)) from exc
    root = _project_root()
    try:
        lock, lock_sha256 = load_source_lock_snapshot(root / "sources.lock.json")
    except LocalBuildBootstrapError as exc:
        raise LocalBuildAcquireError(str(exc)) from exc
    if lock_sha256 != bootstrap.get("sources_lock_sha256"):
        raise LocalBuildAcquireError("source lock changed after public bootstrap")
    sources = lock["sources"]
    if not isinstance(sources, dict):
        raise LocalBuildAcquireError("source lock sources are invalid")
    build_root = Path(str(bootstrap["build_root"]))
    cache_root = build_root / "cache/downloads"
    receipts: dict[str, dict[str, object]] = {}
    for name in ARCHIVE_NAMES:
        source = sources.get(name)
        if not isinstance(source, dict):
            raise LocalBuildAcquireError(f"source lock lacks {name}")
        receipts[name] = acquire_archive(
            cache_root=cache_root,
            name=name,
            source=source,
        )
    extracted = _private_child_directory(cache_root, "extracted")
    rust_source = extract_archive(
        archive=Path(str(receipts["rust_source"]["archive"])),
        expected_sha256=str(receipts["rust_source"]["sha256"]),
        output_parent=extracted,
        expected_top=RUST_SOURCE_TOP,
        included_roots=RUST_SOURCE_INCLUDED_ROOTS,
        allowed_casefold_aliases=RUST_SOURCE_ALLOWED_CASEFOLD_ALIASES,
        skipped_members=RUST_SOURCE_SKIPPED_MEMBERS,
    )
    rust_distribution = extract_archive(
        archive=Path(str(receipts["rust_toolchain_aarch64_linux"]["archive"])),
        expected_sha256=str(
            receipts["rust_toolchain_aarch64_linux"]["sha256"]
        ),
        output_parent=extracted,
        expected_top="rust-1.95.0-aarch64-unknown-linux-gnu",
    )
    rust_source_component = extract_archive(
        archive=Path(str(receipts["rust_std_source_component"]["archive"])),
        expected_sha256=str(receipts["rust_std_source_component"]["sha256"]),
        output_parent=extracted,
        expected_top="rust-src-1.95.0",
    )
    image = bootstrap.get("builder_image")
    if not isinstance(image, dict) or not isinstance(image.get("id"), str):
        raise LocalBuildAcquireError("public bootstrap lacks the builder image ID")
    rust_identity = hashlib.sha256(
        (
            str(receipts["rust_toolchain_aarch64_linux"]["sha256"])
            + str(receipts["rust_std_source_component"]["sha256"])
        ).encode("ascii")
    ).hexdigest()
    rust_toolchain, rust_toolchain_receipt = _install_rust_toolchain(
        cache_root=cache_root,
        builder_image_id=str(image["id"]),
        rust_distribution=rust_distribution,
        rust_distribution_sha256=str(
            receipts["rust_toolchain_aarch64_linux"]["sha256"]
        ),
        rust_source_component=rust_source_component,
        rust_source_component_sha256=str(
            receipts["rust_std_source_component"]["sha256"]
        ),
        identity=rust_identity,
    )
    ingenic_source = sources.get("ingenic_glibc216_toolchain")
    if not isinstance(ingenic_source, dict):
        raise LocalBuildAcquireError("source lock lacks the Ingenic toolchain")
    ingenic_toolchain_archive, ingenic_identity = _acquire_ingenic_toolchain(
        build_root=build_root,
        cache_root=cache_root,
        source=ingenic_source,
    )
    builder_receipt = image.get("receipt")
    builder_tag = image.get("tag")
    if not isinstance(builder_receipt, str) or not isinstance(builder_tag, str):
        raise LocalBuildAcquireError("public bootstrap builder receipt is invalid")
    with _regular_snapshot(
        Path(builder_receipt),
        "builder image receipt",
        limit=MAX_RECEIPT_BYTES,
    ) as (builder_handle, _):
        builder_receipt_sha256 = _sha256_stream(builder_handle)
    thingino_source = sources.get("thingino_firmware")
    if not isinstance(thingino_source, dict):
        raise LocalBuildAcquireError("source lock lacks the Thingino source")
    thingino_toolchain = sources.get("thingino_build_toolchain_aarch64")
    if not isinstance(thingino_toolchain, dict):
        raise LocalBuildAcquireError("source lock lacks the Thingino build toolchain")
    rust_source_receipt = _load_json_object(
        rust_source / EXTRACTION_RECEIPT,
        "Rust source extraction receipt",
    )
    rust_toolchain_receipt_path = rust_toolchain / RUST_RECEIPT
    with _regular_snapshot(
        rust_toolchain_receipt_path,
        "Rust toolchain receipt",
        limit=MAX_RECEIPT_BYTES,
    ) as (rust_receipt_handle, _):
        rust_toolchain_receipt_sha256 = _sha256_stream(rust_receipt_handle)
    result = {
        "archives": receipts,
        "build_root": str(build_root),
        "builder_image": {
            "id": image["id"],
            "receipt": builder_receipt,
            "receipt_sha256": builder_receipt_sha256,
            "tag": builder_tag,
        },
        "ingenic_toolchain": {
            **ingenic_identity,
            "archive": str(ingenic_toolchain_archive),
        },
        "next_action": "build-source-locked-thingino-toolchain",
        "rust_source": {
            "archive_sha256": receipts["rust_source"]["sha256"],
            "path": str(rust_source),
            "tree_sha256": rust_source_receipt["tree_sha256"],
        },
        "rust_toolchain": {
            "path": str(rust_toolchain),
            "receipt": str(rust_toolchain_receipt_path),
            "receipt_sha256": rust_toolchain_receipt_sha256,
            **rust_toolchain_receipt,
        },
        "schema_version": INPUTS_SCHEMA_VERSION,
        "source_checkout": {
            "path": bootstrap["source_checkout"],
            "revision": thingino_source.get("revision"),
            "tree": thingino_source.get("tree"),
            "url": thingino_source.get("url"),
        },
        "source_date_epoch": bootstrap["source_date_epoch"],
        "sources_lock_sha256": lock_sha256,
        "thingino_toolchain": dict(thingino_toolchain),
    }
    manifest_identity = _cache_generation_identity(
        "public-inputs-v1",
        lock_sha256,
        builder_receipt_sha256,
    )
    manifest_path = cache_root / f"public-inputs-{manifest_identity}.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        existing = _load_json_object(manifest_path, "public input manifest")
        if existing != result:
            raise LocalBuildAcquireError("public input manifest changed")
    else:
        atomic_write(
            manifest_path,
            (json.dumps(result, indent=2, sort_keys=True) + "\n").encode(),
        )
        manifest_path.chmod(0o600)
    return {**result, "manifest": str(manifest_path)}
