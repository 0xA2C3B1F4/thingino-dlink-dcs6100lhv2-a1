"""Acquire a public base plus patches that reconstruct the accepted Raptor trees."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable, Any

Progress = Callable[[dict[str, object]], None]

from .local_build_acquire import (
    _git,
    _directory,
    _private_child_directory,
    _regular_snapshot,
    _tree_digest,
    _load_json_object,
)
from .sd_package import atomic_write

SOURCES = frozenset(
    {
        "raptor",
        "raptor-ipc",
        "raptor-common",
        "compy",
        "mbedtls",
        "mbedtls-framework",
        "slice99",
        "datatype99",
        "interface99",
        "metalang99",
    }
)
FULL_MEDIA_SOURCES = SOURCES | {"raptor-hal", "ingenic-headers", "libschrift"}


def digest(path: Path) -> str:
    # A flattened full-media patch includes the daemon fixes previously kept
    # in separate development patches. Keep a bounded read for that closure.
    with _regular_snapshot(path, "Raptor source input", limit=8 * 1024 * 1024) as (
        handle,
        _,
    ):
        return hashlib.sha256(handle.read()).hexdigest()


def source_lock(root: Path, *, full_media: bool = True) -> dict[str, Any]:
    if not full_media:
        raise ValueError("legacy Raptor RWD source path is retired")
    lock = _load_json_object(
        root / "components/raptor/source-build-lock.json", "Raptor source lock"
    )
    expected_sources = FULL_MEDIA_SOURCES - {"ingenic-headers"}
    if lock.get("schema_version") != 1 or set(lock.get("sources", {})) != expected_sources:
        raise ValueError("Raptor source closure changed")
    if "runtime_tree" in lock:
        raise ValueError("full-media lock must describe the final source trees")
    headers = _load_json_object(
        root / "components/raptor/headers-input.json", "Raptor SDK headers input"
    )
    if (
        headers.get("license_status") != "unspecified"
        or headers.get("redistribution") != "not-authorized-by-this-repository"
        or headers.get("patches") != []
        or headers.get("tree") != headers.get("base_tree")
        or "license" in headers
    ):
        raise ValueError("SDK headers must retain their unlicensed build-input status")
    lock["sources"]["ingenic-headers"] = headers
    patch_directory = "raptor-full-source"
    for name, spec in lock["sources"].items():
        if not re.fullmatch(
            r"https://github.com/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.git", spec["url"]
        ):
            raise ValueError("Raptor source URL is not public HTTPS")
        for field in ("base", "base_tree", "tree"):
            if not re.fullmatch(r"[a-f0-9]{40}", spec[field]):
                raise ValueError("Raptor source identity is invalid")
        evidence = "notice" if name == "ingenic-headers" else "license"
        relative = PurePosixPath(spec[evidence])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != spec[evidence]
            or not relative.parts
            or not re.fullmatch(r"[a-f0-9]{64}", spec[evidence + "_sha256"])
        ):
            raise ValueError("Raptor source notice or license identity is invalid")
        for patch in spec["patches"]:
            if (
                patch["path"] != f"patches/{patch_directory}/{name}.patch"
                or digest(root / patch["path"]) != patch["sha256"]
            ):
                raise ValueError("Raptor source patch identity changed")
    return lock


def recipe_identity(root: Path, *, full_media: bool = True) -> dict[str, Any]:
    if not full_media:
        raise ValueError("legacy Raptor RWD source path is retired")
    lock = source_lock(root, full_media=full_media)
    paths = [
        "components/raptor/source-build-lock.json",
        "components/raptor/headers-input.json",
        "components/raptor/Ubuntu-Font-Licence-1.0.txt",
        "scripts/container_build_raptor_full.sh",
        "installer/raptor_source.py",
        "installer/raptor_full_build.py",
        "installer/raptor_full_component.py",
        "installer/raptor_component.py",
        "sources.lock.json",
        "containers/thingino-builder-arm64.Containerfile",
    ]
    paths += [p["path"] for spec in lock["sources"].values() for p in spec["patches"]]
    return {path: digest(root / path) for path in sorted(paths)}


def git(path: Path, *args: str, network=False) -> str:
    return _git(
        list(args),
        checkout=path,
        label=f"Raptor {path.name} {args[0]}",
        network=network,
        timeout=300 if network else 30,
    )


def verify_source(path: Path, spec: dict[str, Any], *, tree: str) -> str:
    _directory(path, "Raptor source")
    _directory(path / ".git", "Raptor source Git directory")
    if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise ValueError("Raptor source Git root changed")
    if (
        git(path, "rev-parse", "HEAD") != spec["base"]
        or git(path, "write-tree") != tree
    ):
        raise ValueError("Raptor source Git identity changed")
    if git(path, "diff", "--name-only") or git(
        path, "ls-files", "--others", "--exclude-standard"
    ):
        raise ValueError("Raptor source working tree changed")
    if any(
        not line.startswith("H ") for line in git(path, "ls-files", "-v").splitlines()
    ):
        raise ValueError("Raptor source index flags changed")
    evidence = "license" if "license" in spec else "notice"
    if digest(path / spec[evidence]) != spec[evidence + "_sha256"]:
        raise ValueError("Raptor source license or notice changed")
    return _tree_digest(path, excluded_root_names=frozenset({".git"}))


def acquire_sources(
    *, root: Path, cache_root: Path, progress: Progress | None = None,
    full_media: bool = True,
) -> tuple[Path, dict[str, Any]]:
    if not full_media:
        raise ValueError("legacy Raptor RWD source path is retired")
    lock = source_lock(root, full_media=full_media)
    recipe = recipe_identity(root, full_media=full_media)
    expected_sources = FULL_MEDIA_SOURCES
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    parent = _private_child_directory(cache_root, "sources", "raptor-sources")
    destination = parent / key
    if destination.exists() or destination.is_symlink():
        _directory(destination, "Raptor source cache")
        receipt = _load_json_object(
            destination / "sources.json", "Raptor source receipt"
        )
        if (
            receipt.get("recipe") != recipe
            or set(receipt.get("sources", {})) != expected_sources
        ):
            raise ValueError("Raptor source cache identity changed")
        for name, spec in lock["sources"].items():
            record = receipt["sources"][name]
            expected_tree = spec["tree"]
            if (
                record["tree"] != expected_tree
                or verify_source(destination / name, spec, tree=expected_tree)
                != record["digest"]
            ):
                raise ValueError("Raptor source cache content changed")
        return destination, receipt
    temporary = Path(tempfile.mkdtemp(prefix=".acquire-", dir=parent))
    receipt = {"recipe": recipe, "sources": {}}
    try:
        for name, spec in lock["sources"].items():
            if progress:
                progress({"phase": "raptor-source-acquire", "source": name})
            path = temporary / name
            path.mkdir(mode=0o700)
            git(path, "init", "--quiet")
            git(
                path,
                "fetch",
                "--no-tags",
                "--depth=1",
                spec["url"],
                spec["base"],
                network=True,
            )
            git(path, "checkout", "--detach", "FETCH_HEAD")
            if git(path, "rev-parse", "HEAD^{tree}") != spec["base_tree"]:
                raise ValueError("Raptor public base tree mismatch")
            for patch in spec["patches"]:
                git(path, "apply", "--index", str(root / patch["path"]))
            if git(path, "write-tree") != spec["tree"]:
                raise ValueError(
                    "Raptor reconstructed tree does not match accepted source"
                )
            tree = git(path, "write-tree")
            expected_tree = spec["tree"]
            if tree != expected_tree:
                raise ValueError("Raptor final patched tree mismatch")
            receipt["sources"][name] = {
                "tree": tree,
                "digest": verify_source(path, spec, tree=tree),
            }
        atomic_write(
            temporary / "sources.json",
            (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode(),
            mode=0o600,
        )
        os.rename(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return destination, receipt
