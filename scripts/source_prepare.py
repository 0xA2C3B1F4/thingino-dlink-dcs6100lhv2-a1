#!/usr/bin/env python3
"""Prepare a deterministic DCS-6100LHV2 A1 Thingino source tree offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import source_checkout as sources  # noqa: E402


DEFAULT_PROFILE_PATH = (
    ROOT / "profiles/dlink-dcs6100lhv2-a1/source-profile.json"
)
HEX64 = set("0123456789abcdef")
MAX_PROFILE_INPUT = 1024 * 1024
MAX_BUILDROOT_OVERRIDES = 64
MAX_ARCHIVE_MEMBER = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL = 512 * 1024 * 1024
DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


class PreparationError(ValueError):
    """The source profile or prepared tree violates the public contract."""


def validate_persistent_overlay_init(raw: bytes) -> None:
    """Fail closed unless the prepared PID 1 keeps the fixed data overlay."""

    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreparationError("prepared overlay init is not UTF-8") from error
    required = (
        "THINGINO_DLINK_VERIFIED_MTD_ROOT",
        "unset THINGINO_DLINK_VERIFIED_MTD_ROOT",
        "mount_jffs2 data /overlay",
        "\nreset_overlay_if_requested\nmount_overlay",
        "/overlay/.thingino-factory-reset",
        "mount -t overlayfs -o noatime,lowerdir=/,upperdir=/overlay",
        'mount_overlay || die "Failed to mount overlay!"',
        'pivot_root /mnt /mnt/rom || die "Failed to pivot_root!"',
        "mount -o noatime,move /rom/overlay /overlay",
    )
    for token in required:
        if token not in source:
            raise PreparationError(
                f"prepared overlay init is missing required operation: {token}"
            )
    if "flash_eraseall" in source:
        raise PreparationError("prepared overlay init can erase persistent data implicitly")
    if not (
        source.index("unset THINGINO_DLINK_VERIFIED_MTD_ROOT")
        < source.index("mount_jffs2 data /overlay")
        < source.index("\nreset_overlay_if_requested\nmount_overlay")
        < source.index('mount_overlay || die "Failed to mount overlay!"')
        < source.index('pivot_root /mnt /mnt/rom || die "Failed to pivot_root!"')
        < source.index("mount -o noatime,move /rom/overlay /overlay")
    ):
        raise PreparationError("prepared overlay init boot order changed")


def validate_static_webui_cleanup(raw: bytes) -> None:
    """Fail closed unless the final rootfs hook removes legacy request paths."""

    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreparationError("prepared rootfs hook is not UTF-8") from error
    required = (
        'rm -rf "${TARGET_DIR}/var/www"',
        'rm -rf "${TARGET_DIR}/usr/libexec/thingino-webui"',
        '"${TARGET_DIR}/usr/sbin/formatsd"',
        '"${TARGET_DIR}/usr/sbin/envfromcard"',
        '"${TARGET_DIR}/usr/sbin/telegram-cam-register"',
        '"${TARGET_DIR}/usr/sbin/telegram-cam-agent"',
    )
    for token in required:
        if token not in source:
            raise PreparationError(
                f"prepared rootfs hook is missing legacy cleanup: {token}"
            )


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PreparationError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise PreparationError(
            f"{label} keys differ: "
            f"missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PreparationError(f"{label} must be a path string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise PreparationError(f"{label} is not a safe relative path")
    return value


def _sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX64 for character in value)
    ):
        raise PreparationError(f"{label} must be lowercase SHA-256")
    return value


def _read_regular_nofollow(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise PreparationError(f"cannot open {label}") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_PROFILE_INPUT
        ):
            raise PreparationError(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise PreparationError(f"{label} read was incomplete")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise PreparationError(f"{label} changed while being read")
    return b"".join(chunks)


def _profile_entry(
    raw: object, label: str, *, destination_required: bool
) -> dict[str, str]:
    entry = _require_mapping(raw, label)
    expected = {"source", "sha256"}
    if destination_required:
        expected.add("destination")
    actual = set(entry)
    if actual not in (expected, expected | {"replaces_sha256"}):
        _require_exact_keys(entry, expected, label)
    checked = {
        "source": _safe_relative_path(entry["source"], f"{label} source"),
        "sha256": _sha256(entry["sha256"], f"{label} digest"),
    }
    if destination_required:
        checked["destination"] = _safe_relative_path(
            entry["destination"], f"{label} destination"
        )
    if "replaces_sha256" in entry:
        checked["replaces_sha256"] = _sha256(
            entry["replaces_sha256"], f"{label} replacement digest"
        )
    return checked


def _validate_patch_paths(payload: bytes, label: str) -> list[str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise PreparationError(f"{label} is not UTF-8") from error
    paths: list[str] = []
    for line in lines:
        if not line.startswith("diff --git "):
            continue
        match = DIFF_HEADER.fullmatch(line)
        if match is None or match.group(1) != match.group(2):
            raise PreparationError(f"{label} has an unsafe diff header")
        paths.append(
            _safe_relative_path(match.group(1), f"{label} diff path")
        )
    if not paths or len(paths) != len(set(paths)):
        raise PreparationError(f"{label} diff paths must be non-empty and unique")
    return paths


def load_profile(
    path: Path = DEFAULT_PROFILE_PATH, *, repository_root: Path = ROOT
) -> dict[str, Any]:
    raw = _read_regular_nofollow(path, "source profile")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreparationError("source profile is not valid UTF-8 JSON") from error
    profile = dict(_require_mapping(document, "source profile"))
    _require_exact_keys(
        profile,
        {
            "schema_version",
            "model",
            "hardware_revision",
            "profile_name",
            "thingino_patches",
            "installed_files",
        },
        "source profile",
    )
    if profile["schema_version"] != 1:
        raise PreparationError("unsupported source-profile schema")
    if profile["model"] != "DCS-6100LHV2":
        raise PreparationError("source profile targets the wrong model")
    if profile["hardware_revision"] != "A1":
        raise PreparationError("source profile targets the wrong hardware revision")
    if (
        profile["profile_name"]
        != "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu"
    ):
        raise PreparationError("source profile name changed")

    raw_patches = profile["thingino_patches"]
    raw_installed = profile["installed_files"]
    if not isinstance(raw_patches, list) or not raw_patches:
        raise PreparationError("source profile must contain Thingino patches")
    if not isinstance(raw_installed, list) or not raw_installed:
        raise PreparationError("source profile must contain installed files")
    patches = [
        _profile_entry(item, f"thingino_patches[{index}]", destination_required=False)
        for index, item in enumerate(raw_patches)
    ]
    installed = [
        _profile_entry(item, f"installed_files[{index}]", destination_required=True)
        for index, item in enumerate(raw_installed)
    ]
    patch_sources = [item["source"] for item in patches]
    destinations = [item["destination"] for item in installed]
    installed_sources = [item["source"] for item in installed]
    if patch_sources != sorted(set(patch_sources)):
        raise PreparationError("Thingino patches must be unique and source-sorted")
    if destinations != sorted(set(destinations)):
        raise PreparationError("installed files must be unique and destination-sorted")
    if len(installed_sources) != len(set(installed_sources)):
        raise PreparationError("installed-file sources must be unique")

    snapshots: dict[str, bytes] = {}
    for entry in [*patches, *installed]:
        relative = entry["source"]
        source = repository_root.joinpath(*PurePosixPath(relative).parts)
        try:
            source.relative_to(repository_root)
        except ValueError as error:
            raise PreparationError("profile input escapes repository") from error
        payload = _read_regular_nofollow(source, f"profile input {relative}")
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise PreparationError(f"profile input digest mismatch: {relative}")
        snapshots[relative] = payload
    for entry in patches:
        _validate_patch_paths(
            snapshots[entry["source"]], f"Thingino patch {entry['source']}"
        )

    return {
        "schema_version": 1,
        "model": profile["model"],
        "hardware_revision": profile["hardware_revision"],
        "profile_name": profile["profile_name"],
        "thingino_patches": patches,
        "installed_files": installed,
        "snapshots": snapshots,
    }


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_WORK_TREE",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _run_git(
    arguments: list[str],
    *,
    cwd: Path,
    label: str,
    payload: bytes | None = None,
    stdout: object = subprocess.PIPE,
) -> bytes:
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=",
            *arguments,
        ],
        cwd=cwd,
        env=_git_environment(),
        input=payload,
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise PreparationError(
            f"{label} failed with exit code {result.returncode}{suffix}"
        )
    return result.stdout if isinstance(result.stdout, bytes) else b""


def _archive_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    seen: set[str] = set()
    total = 0
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != member.name
        ):
            raise PreparationError("Git archive contains an unsafe path")
        if member.name in seen:
            raise PreparationError("Git archive contains a duplicate path")
        seen.add(member.name)
        if member.islnk() or not (
            member.isdir() or member.isfile() or member.issym()
        ):
            raise PreparationError("Git archive contains an unsupported path type")
        if member.isfile():
            if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER:
                raise PreparationError("Git archive member exceeds the size limit")
            total += member.size
            if total > MAX_ARCHIVE_TOTAL:
                raise PreparationError("Git archive exceeds the total size limit")
        if member.issym():
            target = member.linkname
            if not target:
                raise PreparationError("Git archive contains an unsafe symlink")
            if not PurePosixPath(target).is_absolute():
                normalized = posixpath.normpath(
                    str(PurePosixPath(member.name).parent / target)
                )
                if normalized == ".." or normalized.startswith("../"):
                    raise PreparationError("Git archive symlink escapes its tree")
    return members


def _extract_git_archive(
    checkout: Path, destination: Path, *, source_date_epoch: int
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryFile(dir=destination.parent) as archive_file:
        _run_git(
            ["archive", "--format=tar", "HEAD"],
            cwd=checkout,
            label="Git tree export",
            stdout=archive_file,
        )
        archive_file.seek(0)
        with tarfile.open(fileobj=archive_file, mode="r:") as archive:
            members = _archive_members(archive)
            for member in sorted(
                (item for item in members if item.isdir()),
                key=lambda item: len(PurePosixPath(item.name).parts),
            ):
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o777)
            for member in (item for item in members if item.isfile()):
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise PreparationError("Git archive member cannot be read")
                with target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=64 * 1024)
                target.chmod(member.mode & 0o777)
                os.utime(
                    target,
                    (source_date_epoch, source_date_epoch),
                    follow_symlinks=False,
                )
            for member in (item for item in members if item.issym()):
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            for member in sorted(
                (item for item in members if item.isdir()),
                key=lambda item: len(PurePosixPath(item.name).parts),
                reverse=True,
            ):
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                os.utime(
                    target,
                    (source_date_epoch, source_date_epoch),
                    follow_symlinks=False,
                )


def _destination(
    destination: Path, *, repository_root: Path, checkout: Path
) -> Path:
    if not destination.is_absolute():
        raise PreparationError("destination must be an absolute scratch path")
    resolved = destination.resolve(strict=False)
    repository_root = repository_root.resolve()
    checkout = checkout.resolve()
    if (
        resolved in {repository_root, checkout}
        or repository_root in resolved.parents
        or checkout in resolved.parents
    ):
        raise PreparationError(
            "prepared source destination must be outside repository and checkout"
        )
    if destination.exists() or destination.is_symlink():
        raise PreparationError("refusing to reuse an existing destination")
    if not destination.parent.is_dir():
        raise PreparationError("prepared source destination parent does not exist")
    return resolved


def _require_real_parent(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise PreparationError("profile destination escapes prepared tree") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise PreparationError(
                    "profile destination parent is not a real directory"
                )
        else:
            current.mkdir()


def _write_snapshot(
    root: Path,
    path: Path,
    payload: bytes,
    *,
    source_date_epoch: int,
    replaces_sha256: str | None = None,
) -> None:
    exists = path.exists() or path.is_symlink()
    if exists:
        if replaces_sha256 is None:
            raise PreparationError(f"profile destination already exists: {path.name}")
        old = _read_regular_nofollow(path, f"profile replacement {path.name}")
        if hashlib.sha256(old).hexdigest() != replaces_sha256:
            raise PreparationError(f"profile replacement changed: {path.name}")
        path.unlink()
    elif replaces_sha256 is not None:
        raise PreparationError(f"profile replacement is missing: {path.name}")
    _require_real_parent(root, path.parent)
    with path.open("xb") as output:
        output.write(payload)
    path.chmod(0o644)
    os.utime(
        path,
        (source_date_epoch, source_date_epoch),
        follow_symlinks=False,
    )


def _apply_patch(root: Path, payload: bytes) -> None:
    # The exported tree deliberately contains no Git metadata. The patch bytes
    # are manifest-hashed and their diff paths are reviewed public inputs.
    common = ["apply", "--unsafe-paths", "--whitespace=error-all"]
    _run_git(
        [*common, "--check", "-"],
        cwd=root,
        label="patch preflight",
        payload=payload,
    )
    _run_git([*common, "-"], cwd=root, label="patch application", payload=payload)


def _apply_buildroot_overrides(
    prepared: Path, *, source_date_epoch: int
) -> list[dict[str, str]]:
    """Apply Thingino's locked Buildroot overrides without running `make update`."""
    patch_root = prepared / "package/all-patches/buildroot"
    buildroot = prepared / "buildroot"
    if not patch_root.is_dir() or patch_root.is_symlink():
        raise PreparationError("Buildroot override patch directory is missing")
    if not buildroot.is_dir() or buildroot.is_symlink():
        raise PreparationError("prepared Buildroot tree is missing")
    candidates = sorted(patch_root.glob("*.patch"), key=lambda item: item.name)
    if not candidates or len(candidates) > MAX_BUILDROOT_OVERRIDES:
        raise PreparationError("Buildroot override patch count is invalid")

    applied: list[dict[str, str]] = []
    for patch_path in candidates:
        payload = _read_regular_nofollow(
            patch_path, f"Buildroot override {patch_path.name}"
        )
        _validate_patch_paths(payload, f"Buildroot override {patch_path.name}")
        _apply_patch(buildroot, payload)
        applied.append(
            {
                "source": f"package/all-patches/buildroot/{patch_path.name}",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    _normalize_mtimes(buildroot, source_date_epoch)
    return applied


def _normalize_mtimes(root: Path, source_date_epoch: int) -> None:
    paths: list[Path] = []
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        paths.extend(base / name for name in names)
        paths.extend(base / name for name in files)
    for path in paths:
        os.utime(
            path,
            (source_date_epoch, source_date_epoch),
            follow_symlinks=False,
        )
    for directory, _names, _files in os.walk(
        root, topdown=False, followlinks=False
    ):
        os.utime(
            directory,
            (source_date_epoch, source_date_epoch),
            follow_symlinks=False,
        )


def prepare_source(
    checkout: Path,
    destination: Path,
    *,
    lock: Mapping[str, Any] | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    repository_root: Path = ROOT,
) -> dict[str, Any]:
    checked_lock = sources.load_lock() if lock is None else dict(lock)
    checkout = checkout.resolve()
    destination = _destination(
        destination, repository_root=repository_root, checkout=checkout
    )
    verification = sources.verify_checkout(checkout, checked_lock)
    profile = load_profile(profile_path, repository_root=repository_root)
    snapshots = profile.pop("snapshots")
    epoch = checked_lock["source_date_epoch"]

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.prepare-", dir=destination.parent
        )
    )
    try:
        _extract_git_archive(checkout, temporary / "source", source_date_epoch=epoch)
        buildroot_destination = temporary / "source/buildroot"
        if buildroot_destination.exists():
            if not buildroot_destination.is_dir() or any(
                buildroot_destination.iterdir()
            ):
                raise PreparationError("root archive populated the Buildroot gitlink")
            buildroot_destination.rmdir()
        _extract_git_archive(
            checkout / checked_lock["sources"]["buildroot"]["path"],
            buildroot_destination,
            source_date_epoch=epoch,
        )

        prepared = temporary / "source"
        for entry in profile["thingino_patches"]:
            _apply_patch(prepared, snapshots[entry["source"]])
        buildroot_overrides = _apply_buildroot_overrides(
            prepared, source_date_epoch=epoch
        )
        for entry in profile["installed_files"]:
            path = prepared.joinpath(
                *PurePosixPath(entry["destination"]).parts
            )
            _write_snapshot(
                prepared,
                path,
                snapshots[entry["source"]],
                source_date_epoch=epoch,
                replaces_sha256=entry.get("replaces_sha256"),
            )

        validate_persistent_overlay_init(
            _read_regular_nofollow(
                prepared / "overlay/init", "prepared persistent overlay init"
            )
        )
        if any(
            entry["source"]
            == "patches/thingino/0011-install-static-dlink-webui.patch"
            for entry in profile["thingino_patches"]
        ):
            validate_static_webui_cleanup(
                _read_regular_nofollow(
                    prepared / "scripts/rootfs_script.sh", "prepared rootfs hook"
                )
            )

        manifest = {
            "schema_version": 1,
            "operation": "thingino-source-preparation",
            "host_only": True,
            "source_date_epoch": epoch,
            "sources": verification["sources"],
            "constraints": verification["constraints"],
            "profile": {
                key: profile[key]
                for key in (
                    "model",
                    "hardware_revision",
                    "profile_name",
                    "thingino_patches",
                    "installed_files",
                )
            },
            "buildroot_override_patches": buildroot_overrides,
            "prepared": True,
        }
        manifest_path = prepared / "dcs6100-source-preparation.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o644)
        os.utime(
            manifest_path,
            (epoch, epoch),
            follow_symlinks=False,
        )
        _normalize_mtimes(prepared, epoch)
        prepared.replace(destination)
        temporary.rmdir()
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=sources.DEFAULT_LOCK_PATH)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = prepare_source(
            arguments.checkout,
            arguments.destination,
            lock=sources.load_lock(arguments.lock),
            profile_path=arguments.profile,
        )
    except (OSError, PreparationError, sources.SourceError) as error:
        print(f"source preparation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
