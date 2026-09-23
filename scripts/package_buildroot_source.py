#!/usr/bin/env python3
"""Package the pinned Buildroot source used by one local DCS-6100 build.

This host-only tool reads existing Git objects and receipts. It performs no
fetch, build, device operation, or publication.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import tarfile
import tempfile


HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PATCH_HEADER = re.compile(r"diff --git a/(\S+) b/(\S+)$")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_file(path: Path, label: str, limit: int = 1024 * 1024) -> bytes:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), f"{label} is not a regular file")
    require(0 < info.st_size <= limit, f"{label} has unexpected size")
    return path.read_bytes()


def git(repo: Path, *args: str, payload: bytes | None = None) -> bytes:
    environment = os.environ.copy()
    for key in (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_DIR",
        "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX", "GIT_QUARANTINE_PATH", "GIT_WORK_TREE",
    ):
        environment.pop(key, None)
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1", "LC_ALL": "C",
    })
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
         "-c", "core.hooksPath=", "-C", str(repo), *args],
        input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"git {args[0]} failed: {detail[-1] if detail else result.returncode}")
    return result.stdout


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    require(
        bool(path.parts) and not path.is_absolute()
        and path.as_posix() == name
        and all(part not in ("", ".", "..") for part in path.parts),
        f"unsafe archive path: {name}",
    )
    return path


def check_patch(payload: bytes, name: str) -> None:
    paths = []
    for line in payload.decode("utf-8").splitlines():
        if line.startswith("diff --git "):
            match = PATCH_HEADER.fullmatch(line)
            require(match is not None and match[1] == match[2], f"unsafe patch: {name}")
            safe_name(match[1])
            paths.append(match[1])
    require(paths and len(paths) == len(set(paths)), f"invalid patch paths: {name}")


def validate_archive(source_tar: Path) -> tuple[int, int]:
    count = size = 0
    seen: set[str] = set()
    with tarfile.open(source_tar, "r:") as archive:
        for member in archive:
            path = safe_name(member.name)
            require(member.name not in seen, f"duplicate archive path: {member.name}")
            seen.add(member.name)
            require(member.isfile() or member.isdir() or member.issym(),
                    f"unsupported archive entry: {member.name}")
            if member.isfile():
                require(0 <= member.size <= 128 * 1024 * 1024,
                        f"oversized archive entry: {member.name}")
                size += member.size
                require(size <= 512 * 1024 * 1024, "Buildroot archive exceeds 512 MiB")
            elif member.issym():
                require(bool(member.linkname), f"empty symlink: {member.name}")
                if not posixpath.isabs(member.linkname):
                    target = posixpath.normpath(posixpath.join(str(path.parent), member.linkname))
                    require(target != ".." and not target.startswith("../"),
                            f"escaping symlink: {member.name}")
            count += 1
    require("COPYING" in seen and "README" in seen, "Buildroot notices missing")
    return count, size


def extract_checked(source_tar: Path, destination: Path) -> None:
    destination.mkdir()
    with tarfile.open(source_tar, "r:") as archive:
        members = archive.getmembers()
        for member in sorted((m for m in members if m.isdir()),
                             key=lambda m: len(PurePosixPath(m.name).parts)):
            target = destination.joinpath(*safe_name(member.name).parts)
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(member.mode & 0o777)
        for member in (m for m in members if m.isfile()):
            target = destination.joinpath(*safe_name(member.name).parts)
            require(not any(parent.is_symlink() for parent in target.parents if parent != destination),
                    f"symlink parent: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            require(source is not None, f"unreadable archive entry: {member.name}")
            with target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            target.chmod(member.mode & 0o777)
        for member in (m for m in members if m.issym()):
            target = destination.joinpath(*safe_name(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(member.linkname)


def tree_inventory(root: Path) -> dict[str, dict[str, object]]:
    require(root.is_dir() and not root.is_symlink(), f"invalid tree: {root}")
    result: dict[str, dict[str, object]] = {}
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(names + files):
            path = base / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                item: dict[str, object] = {"type": "directory", "mode": stat.S_IMODE(mode)}
            elif stat.S_ISREG(mode):
                item = {"type": "file", "mode": stat.S_IMODE(mode),
                        "size": path.stat().st_size, "sha256": hash_file(path)}
            elif stat.S_ISLNK(mode):
                item = {"type": "symlink", "target": os.readlink(path)}
            else:
                raise ValueError(f"unsupported tree entry: {relative}")
            result[relative] = item
    return result


def add_bytes(archive: tarfile.TarFile, name: str, payload: bytes, epoch: int) -> None:
    safe_name(name)
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = epoch
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(payload))


def package(args: argparse.Namespace) -> dict[str, object]:
    source = args.source.resolve(strict=True)
    thingino = args.thingino.resolve(strict=True)
    buildroot = args.buildroot.resolve(strict=True)
    prepared = args.prepared.resolve(strict=True)
    output = args.output.resolve(strict=True)
    require(output.is_dir() and not output.is_symlink(), "output directory missing or unsafe")
    require(args.archive_name == Path(args.archive_name).name and
            args.archive_name.endswith(".tar.gz"), "invalid archive name")
    require(HEX40.fullmatch(args.project_revision) is not None, "invalid project revision")

    run_bytes = read_file(args.run_receipt, "build run receipt")
    run = json.loads(run_bytes)
    require(run.get("project_head") == args.project_revision, "candidate revision differs")
    prep_bytes = read_file(args.preparation_receipt, "preparation receipt")
    prep = json.loads(prep_bytes)
    require(prep.get("operation") == "thingino-source-preparation" and prep.get("prepared") is True,
            "preparation receipt is not complete")
    require(digest(prep_bytes) == run["download_cache"]["prepared_source_manifest_sha256"],
            "run and preparation receipts differ")
    epoch = prep["source_date_epoch"]
    require(isinstance(epoch, int) and epoch > 0, "invalid source date epoch")
    lock = git(source, "show", f"{args.project_revision}:sources.lock.json")
    require(digest(lock) == run["sources_lock_sha256"], "source lock differs from run")
    pinned = json.loads(lock)["sources"]["buildroot"]
    receipt_pin = prep["sources"]["buildroot"]
    require(pinned["revision"] == receipt_pin["revision"] and
            pinned["tree"] == receipt_pin["tree"] and
            pinned["url"].removesuffix(".git") == receipt_pin["url"].removesuffix(".git"),
            "Buildroot source pins differ")
    revision, tree = pinned["revision"], pinned["tree"]
    require(HEX40.fullmatch(revision) is not None and HEX40.fullmatch(tree) is not None,
            "invalid Buildroot Git pin")
    require(git(buildroot, "rev-parse", f"{revision}^{{commit}}").decode().strip() == revision,
            "Buildroot commit missing")
    require(git(buildroot, "rev-parse", f"{revision}^{{tree}}").decode().strip() == tree,
            "Buildroot tree pin differs")
    git_url = git(buildroot, "config", "--get", "remote.origin.url").decode().strip()
    require(git_url.removesuffix(".git") == pinned["url"].removesuffix(".git"),
            "Buildroot origin differs")
    thingino_pin = prep["sources"]["thingino_firmware"]
    require(git(thingino, "rev-parse", "HEAD").decode().strip() == thingino_pin["revision"] and
            git(thingino, "rev-parse", "HEAD^{tree}").decode().strip() == thingino_pin["tree"],
            "Thingino checkout differs from preparation receipt")
    thingino_url = git(thingino, "config", "--get", "remote.origin.url").decode().strip()
    require(thingino_url.removesuffix(".git") == thingino_pin["url"].removesuffix(".git"),
            "Thingino origin differs")
    temp_root = Path(os.environ["TMPDIR"])
    require(temp_root.is_dir() and temp_root.stat().st_dev == output.stat().st_dev,
            "TMPDIR must be on the writable output volume")

    patches: dict[str, bytes] = {}
    listed = prep["buildroot_override_patches"]
    require(len(listed) == 4, "expected four Buildroot overrides")
    for entry in listed:
        path = entry["source"]
        safe_name(path)
        require(path.startswith("package/all-patches/buildroot/") and path.endswith(".patch"),
                "unexpected Buildroot override path")
        if path.endswith("/0004-floor-package-stamp-mtimes.patch"):
            source_patch_path = "patches/thingino/0026-floor-buildroot-package-stamp-mtimes.patch"
            source_patch = git(source, "show", f"{args.project_revision}:{source_patch_path}")
            profile_entry = next((item for item in prep["profile"]["thingino_patches"]
                                  if item["source"] == source_patch_path), None)
            require(profile_entry is not None and digest(source_patch) == profile_entry["sha256"],
                    "the source patch generating override 0004 differs")
            check_patch(source_patch, source_patch_path)
            with tempfile.TemporaryDirectory(prefix="buildroot-patch-origin-", dir=temp_root) as patch_temp:
                patch_root = Path(patch_temp)
                git(patch_root, "apply", "--unsafe-paths", "--whitespace=error-all",
                    "--check", "-", payload=source_patch)
                git(patch_root, "apply", "--unsafe-paths", "--whitespace=error-all",
                    "-", payload=source_patch)
                payload = read_file(patch_root / path, path)
        else:
            payload = git(thingino, "show", f"{thingino_pin['revision']}:{path}")
        require(digest(payload) == entry["sha256"], f"override hash mismatch: {path}")
        prepared_patch = args.prepared.parent / path
        require(read_file(prepared_patch, path) == payload,
                f"prepared override differs: {path}")
        check_patch(payload, path)
        patches[path] = payload
    require(list(patches) == sorted(patches), "overrides not in application order")
    config = read_file(args.config, "final Buildroot config", limit=2 * 1024 * 1024)

    archive_path = output / args.archive_name
    receipt_path = output / "buildroot-source-receipt.json"
    require(not archive_path.exists() and not archive_path.is_symlink(), "archive already exists")
    require(not receipt_path.exists() and not receipt_path.is_symlink(), "receipt already exists")
    with tempfile.TemporaryDirectory(prefix="buildroot-source-", dir=temp_root) as temp_name:
        temp = Path(temp_name)
        base_tar = temp / "buildroot-upstream.tar"
        base_tar.write_bytes(git(buildroot, "archive", "--format=tar", revision))
        member_count, source_bytes = validate_archive(base_tar)
        reconstructed = temp / "reconstructed"
        extract_checked(base_tar, reconstructed)
        for path, payload in patches.items():
            git(reconstructed, "apply", "--unsafe-paths", "--whitespace=error-all",
                "--check", "-", payload=payload)
            git(reconstructed, "apply", "--unsafe-paths", "--whitespace=error-all",
                "-", payload=payload)
        actual_inventory = tree_inventory(reconstructed)
        expected_inventory = tree_inventory(prepared)
        require(actual_inventory == expected_inventory,
                "reconstructed Buildroot tree differs from exact prepared tree: "
                + str(next((name for name in sorted(set(actual_inventory) | set(expected_inventory))
                            if actual_inventory.get(name) != expected_inventory.get(name)), "unknown")))

        members = {
            "metadata/build-run.json": run_bytes,
            "metadata/preparation-receipt.json": prep_bytes,
            "metadata/sources.lock.json": lock,
            "sources/buildroot-upstream.tar": base_tar.read_bytes(),
            "config/buildroot.config": config,
        }
        for path, payload in patches.items():
            members[f"patches/{Path(path).name}"] = payload
        manifest = {
            "schema_version": 1,
            "scope": "pinned Buildroot Git tree and four exact Thingino overrides for one a091 candidate",
            "project_revision": args.project_revision,
            "buildroot_revision": revision,
            "buildroot_tree": tree,
            "buildroot_url": pinned["url"],
            "thingino_revision": thingino_pin["revision"],
            "source_date_epoch": epoch,
            "upstream_archive_members": member_count,
            "upstream_archive_file_bytes": source_bytes,
            "prepared_tree_entries_compared": len(actual_inventory),
            "prepared_tree_equal": True,
            "members": {name: {"bytes": len(data), "sha256": digest(data)}
                        for name, data in sorted(members.items())},
            "source_notices": ["sources/buildroot-upstream.tar:COPYING",
                               "sources/buildroot-upstream.tar:README"],
            "limits": ["Buildroot scope only; excludes other firmware component source",
                       "The supplied config is from the separate legal-info export",
                       "No package-license review or full legal approval is claimed",
                       "No binary, build output, or credential is included"],
            "legal_review_approved": False,
            "publication_authorized": False,
        }
        members["metadata/manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        descriptor = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output_stream:
                with gzip.GzipFile(fileobj=output_stream, mode="wb", filename="", mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                        for name, data in sorted(members.items()):
                            add_bytes(archive, name, data, epoch)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

    with tarfile.open(archive_path, "r:gz") as archive:
        require(archive.getnames() == sorted(members), "archive inventory readback differs")
        for item in archive:
            payload = archive.extractfile(item).read()
            require(item.isfile() and payload == members[item.name],
                    f"archive readback differs: {item.name}")
    result = {
        "schema_version": 1,
        "archive": str(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": hash_file(archive_path),
        "member_count": len(members),
        "buildroot_revision": revision,
        "prepared_tree_entries_compared": len(actual_inventory),
        "prepared_tree_equal": True,
        "config_sha256": digest(config),
        "preparation_receipt_sha256": digest(prep_bytes),
        "legal_review_approved": False,
        "publication_authorized": False,
    }
    receipt_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--thingino", type=Path, required=True,
                        help="Pinned Thingino checkout containing the four override patches")
    parser.add_argument("--project-revision", required=True)
    parser.add_argument("--buildroot", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True,
                        help="Exact run's prepared-source/buildroot directory")
    parser.add_argument("--preparation-receipt", type=Path, required=True)
    parser.add_argument("--run-receipt", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-name", default="buildroot-corresponding-source.tar.gz")
    args = parser.parse_args()
    try:
        print(json.dumps(package(args), sort_keys=True, indent=2))
    except (OSError, KeyError, UnicodeError, ValueError, tarfile.TarError) as error:
        parser.exit(2, f"Buildroot source packaging failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
