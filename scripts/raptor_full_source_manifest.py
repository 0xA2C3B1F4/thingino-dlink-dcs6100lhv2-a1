#!/usr/bin/env python3
"""Flatten verified Raptor source trees onto locked public Git bases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


MEDIA_SOURCE_NAMES = (
    "raptor",
    "raptor-hal",
    "raptor-ipc",
    "raptor-common",
    "compy",
    "slice99",
    "datatype99",
    "interface99",
    "metalang99",
)
TLS_SOURCE_NAMES = ("mbedtls", "mbedtls-framework")
OSD_SOURCE_NAMES = ("libschrift",)
SOURCE_NAMES = MEDIA_SOURCE_NAMES + TLS_SOURCE_NAMES + OSD_SOURCE_NAMES
OSD_SOURCES = {
    "libschrift": {
        "url": "https://github.com/tomolt/libschrift.git",
        "base": "8e533fd07acc2f8ae4cffe7f95d2c3392773e2b5",
        "base_tree": "aa3848103f8c45abdd84de1874427140a455cae3",
        "tree": "aa3848103f8c45abdd84de1874427140a455cae3",
        "patches": [],
        "license": "LICENSE",
        "license_sha256": "13c322598cd5f3615a0e8b2b30cca28f8734435ff6214075f318f2bbe45ec4c3",
    }
}
PATCH_ROOT = Path("patches/raptor-full-source")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
PUBLIC_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git")
FORBIDDEN_PATCH_BYTES = (
    b"/Volumes/",
    b"/Users/",
    b"/private/",
    b"devicecred",
    b"accepted_commit",
    b"reviewed_source_commit",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def git(repo: Path, *arguments: str, capture: bool = True) -> bytes:
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("GIT_"):
            del environment[key]
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_SSH_COMMAND="false",
    )
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.autocrlf=false",
        "-c",
        "credential.helper=",
        "-C",
        str(repo),
        *arguments,
    ]
    if capture:
        return subprocess.check_output(command, env=environment, stderr=subprocess.PIPE)
    subprocess.check_call(
        command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    return b""


def tree_inventory(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            result[relative] = {"symlink": os.readlink(path)}
        elif stat.S_ISREG(mode):
            result[relative] = {
                "sha256": sha256_file(path),
                "executable": bool(mode & 0o111),
            }
        elif not stat.S_ISDIR(mode):
            raise ValueError(f"unsupported source entry: {relative}")
    return result


def inventory_sha256(inventory: dict[str, dict[str, object]]) -> str:
    return sha256_bytes(json.dumps(inventory, sort_keys=True).encode("utf-8"))


def _validate_locks(
    source_lock: dict[str, object], matched_lock: dict[str, object], tls_lock: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    if any(lock.get("schema_version") != 1 for lock in (source_lock, matched_lock, tls_lock)):
        raise ValueError("unsupported source lock schema")
    sources = source_lock.get("sources")
    matched = matched_lock.get("sources")
    tls_sources = tls_lock.get("sources")
    if not isinstance(sources, dict) or tuple(sources) != MEDIA_SOURCE_NAMES:
        raise ValueError("base source lock must contain the exact nine-source order")
    if not isinstance(matched, dict) or set(matched) != set(MEDIA_SOURCE_NAMES):
        raise ValueError("matched source lock must contain exactly nine sources")
    if not isinstance(tls_sources, dict) or any(
        name not in tls_sources for name in TLS_SOURCE_NAMES
    ):
        raise ValueError("TLS source lock must contain both locked TLS sources")
    combined = dict(sources)
    for name in TLS_SOURCE_NAMES:
        combined[name] = tls_sources[name]
    combined.update(OSD_SOURCES)
    for name, value in combined.items():
        if not isinstance(value, dict):
            raise ValueError(f"{name}: base source entry is invalid")
        if not PUBLIC_URL.fullmatch(str(value.get("url", ""))):
            raise ValueError(f"{name}: source URL is not public GitHub HTTPS")
        for field in ("base", "base_tree"):
            if not HEX40.fullmatch(str(value.get(field, ""))):
                raise ValueError(f"{name}: {field} is invalid")
        if not isinstance(value.get("license"), str) or not HEX64.fullmatch(
            str(value.get("license_sha256", ""))
        ):
            raise ValueError(f"{name}: license identity is invalid")
        if name in MEDIA_SOURCE_NAMES:
            target = matched[name]
            if not isinstance(target, dict) or type(target.get("file_count")) is not int:
                raise ValueError(f"{name}: matched inventory metadata is invalid")
            if not HEX64.fullmatch(str(target.get("inventory_sha256", ""))):
                raise ValueError(f"{name}: matched inventory digest is invalid")
        else:
            if value.get("tree") != value.get("base_tree") or value.get("patches") != []:
                raise ValueError(f"{name}: dependency source must be an unchanged locked base")
    expected_raptor_tree = matched_lock.get("raptor_git_tree")
    if not HEX40.fullmatch(str(expected_raptor_tree or "")):
        raise ValueError("matched Raptor Git tree is invalid")
    return combined, matched


def _copy_exact(source: Path, destination: Path) -> None:
    for child in destination.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target, symlinks=True, copy_function=shutil.copy2)
        elif child.is_symlink():
            target.symlink_to(os.readlink(child))
        else:
            shutil.copy2(child, target, follow_symlinks=False)


def _checkout_base(base_repo: Path, destination: Path, base: str) -> None:
    destination.mkdir()
    git(destination, "init", "--quiet", capture=False)
    git(
        destination,
        "-c",
        "protocol.file.allow=always",
        "fetch",
        "--quiet",
        "--update-shallow",
        "--no-tags",
        str(base_repo),
        base,
        capture=False,
    )
    git(destination, "checkout", "--quiet", "--detach", "FETCH_HEAD", capture=False)


def materialize_source(
    *, name: str, base_repo: Path, source: Path, spec: dict[str, object], destination: Path
) -> tuple[str, bytes]:
    if git(base_repo, "rev-parse", "HEAD").decode().strip() != spec["base"]:
        raise ValueError(f"{name}: local base repository is not at the locked commit")
    if git(base_repo, "rev-parse", "HEAD^{tree}").decode().strip() != spec["base_tree"]:
        raise ValueError(f"{name}: local base repository tree changed")
    _checkout_base(base_repo, destination, str(spec["base"]))
    _copy_exact(source, destination)
    git(destination, "add", "--all", capture=False)
    tree = git(destination, "write-tree").decode().strip()
    patch = git(destination, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff")
    if any(marker in patch for marker in FORBIDDEN_PATCH_BYTES):
        raise ValueError(f"{name}: flattened patch contains private provenance")
    if sha256_file(destination / str(spec["license"])) != spec["license_sha256"]:
        raise ValueError(f"{name}: matched source changed the locked license")
    return tree, patch


def _verify_patch(
    *, name: str, base_repo: Path, spec: dict[str, object], patch: bytes, tree: str, root: Path
) -> None:
    destination = root / f"verify-{name}"
    _checkout_base(base_repo, destination, str(spec["base"]))
    patch_path = root / f"verify-{name}.patch"
    patch_path.write_bytes(patch)
    if patch:
        git(destination, "apply", "--check", str(patch_path), capture=False)
        git(destination, "apply", "--index", str(patch_path), capture=False)
    if git(destination, "write-tree").decode().strip() != tree:
        raise ValueError(f"{name}: clean patch reconstruction changed the target tree")
    if git(destination, "diff", "--exit-code"):
        raise ValueError(f"{name}: patch reconstruction left worktree changes")


def generate(
    *, source_lock_path: Path, matched_lock_path: Path, tls_lock_path: Path, inputs: Path,
    base_repos: Path, destination: Path
) -> dict[str, object]:
    source_lock = load_object(source_lock_path, "base source lock")
    matched_lock = load_object(matched_lock_path, "matched source lock")
    tls_lock = load_object(tls_lock_path, "TLS source lock")
    sources, matched = _validate_locks(source_lock, matched_lock, tls_lock)
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination must not exist")
    destination.parent.resolve(strict=True)
    inputs = inputs.resolve(strict=True)
    base_repos = base_repos.resolve(strict=True)
    destination.mkdir()
    work = destination / "work"
    patches = destination / PATCH_ROOT
    work.mkdir()
    patches.mkdir(parents=True)
    public_sources: dict[str, object] = {}
    try:
        for name in SOURCE_NAMES:
            spec = sources[name]
            if name in MEDIA_SOURCE_NAMES:
                source = inputs / name
                inventory = tree_inventory(source)
                if len(inventory) != matched[name]["file_count"]:
                    raise ValueError(f"{name}: verified input file count changed")
                if inventory_sha256(inventory) != matched[name]["inventory_sha256"]:
                    raise ValueError(f"{name}: verified input bytes, modes, or symlinks changed")
                tree, patch = materialize_source(
                    name=name,
                    base_repo=base_repos / name,
                    source=source,
                    spec=spec,
                    destination=work / name,
                )
                if name == "raptor" and tree != matched_lock["raptor_git_tree"]:
                    raise ValueError("raptor: matched input Git tree changed")
            else:
                tree = str(spec["base_tree"])
                patch = b""
                checkout = work / name
                _checkout_base(base_repos / name, checkout, str(spec["base"]))
                if git(checkout, "write-tree").decode().strip() != tree:
                    raise ValueError(f"{name}: locked dependency base tree changed")
                if sha256_file(checkout / str(spec["license"])) != spec["license_sha256"]:
                    raise ValueError(f"{name}: locked dependency license changed")
            patch_entries: list[dict[str, str]] = []
            if patch:
                relative = PATCH_ROOT / f"{name}.patch"
                patch_path = destination / relative
                patch_path.write_bytes(patch)
                patch_entries.append({"path": relative.as_posix(), "sha256": sha256_bytes(patch)})
            _verify_patch(
                name=name,
                base_repo=base_repos / name,
                spec=spec,
                patch=patch,
                tree=tree,
                root=work,
            )
            public_sources[name] = {
                "url": spec["url"],
                "base": spec["base"],
                "base_tree": spec["base_tree"],
                "tree": tree,
                "patches": patch_entries,
                "license": spec["license"],
                "license_sha256": spec["license_sha256"],
            }
        lock = {"schema_version": 1, "sources": public_sources}
        (destination / "components/raptor").mkdir(parents=True)
        lock_path = destination / "components/raptor/source-build-lock.json"
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        return lock
    except BaseException:
        shutil.rmtree(destination)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-lock", type=Path, required=True)
    parser.add_argument("--matched-lock", type=Path, required=True)
    parser.add_argument("--tls-lock", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--base-repos", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        generate(
            source_lock_path=arguments.source_lock,
            matched_lock_path=arguments.matched_lock,
            tls_lock_path=arguments.tls_lock,
            inputs=arguments.inputs,
            base_repos=arguments.base_repos,
            destination=arguments.destination,
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Raptor source manifest generation failed: {exc}\n")
    print(arguments.destination)


if __name__ == "__main__":
    main()
