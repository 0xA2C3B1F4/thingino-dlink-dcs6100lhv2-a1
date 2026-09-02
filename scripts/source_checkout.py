#!/usr/bin/env python3
"""Fetch or verify the exact public Thingino source checkout.

This host-only tool never builds firmware and never contacts a camera. Fetching
requires network access; validation is completely offline.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = ROOT / "sources.lock.json"
REQUIRED_SOURCES = {
    "buildroot",
    "ingenic_glibc216_toolchain",
    "prudynt",
    "realtek_hostapd",
    "rtl8188fu",
    "rust_builder_container_amd64",
    "rust_builder_container_arm64",
    "rust_source",
    "rust_std_source_component",
    "rust_toolchain_aarch64_linux",
    "rust_toolchain_x86_64_linux",
    "thingino_build_toolchain_aarch64",
    "thingino_build_toolchain_x86_64",
    "thingino_firmware",
    "thingino_ingenic_sdk",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VARIABLE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(\S+)\s*$")


class SourceError(ValueError):
    """The source lock or checkout violates the pinned public contract."""


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SourceError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SourceError(f"{label} keys differ: missing={missing}, extra={extra}")


def _require_hex40(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX40.fullmatch(value) is None:
        raise SourceError(f"{label} must be a lowercase 40-hex object id")
    return value


def _require_hex64(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise SourceError(f"{label} must be a lowercase 64-hex digest")
    return value


def _safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SourceError(f"{label} must be a path string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SourceError(f"{label} is not a safe relative path")
    return value


def canonical_git_url(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SourceError(f"{label} must be a URL string")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SourceError(
            f"{label} must be an unauthenticated github.com HTTPS URL"
        )
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = PurePosixPath(path).parts
    if len(parts) != 3 or parts[0] != "/" or not all(parts[1:]):
        raise SourceError(f"{label} must identify one GitHub owner/repository")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts[1:]):
        raise SourceError(f"{label} contains an invalid GitHub path")
    return f"https://github.com/{parts[1]}/{parts[2]}"


def rust_archive_url(value: object, filename: str, label: str) -> str:
    if not isinstance(value, str):
        raise SourceError(f"{label} must be a URL string")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "static.rust-lang.org"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != f"/dist/{filename}"
    ):
        raise SourceError(f"{label} must be the exact Rust distribution URL")
    return value


def load_lock(path: Path = DEFAULT_LOCK_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceError(f"cannot read source lock: {error}") from error
    lock = dict(_require_mapping(value, "source lock"))
    _require_exact_keys(
        lock,
        {
            "schema_version",
            "status",
            "source_date_epoch",
            "constraints",
            "sources",
        },
        "source lock",
    )
    if lock["schema_version"] != 2:
        raise SourceError("unsupported source-lock schema")
    if not isinstance(lock["status"], str) or not lock["status"].strip():
        raise SourceError("source-lock status must be non-empty")
    if (
        not isinstance(lock["source_date_epoch"], int)
        or lock["source_date_epoch"] <= 0
    ):
        raise SourceError("source_date_epoch must be a positive integer")

    constraints = _require_mapping(lock["constraints"], "constraints")
    expected_constraints = {
        "camera_operation_allowed",
        "full_image_build_allowed",
        "network_during_compile_allowed",
        "thingino_packer_allowed",
        "thingino_uboot_allowed",
        "thingino_web_flasher_allowed",
    }
    _require_exact_keys(constraints, expected_constraints, "constraints")
    if any(value is not False for value in constraints.values()):
        raise SourceError("every source-stage safety constraint must be false")

    sources = _require_mapping(lock["sources"], "sources")
    _require_exact_keys(sources, REQUIRED_SOURCES, "sources")
    root = _require_mapping(sources["thingino_firmware"], "thingino_firmware")
    _require_exact_keys(
        root,
        {"acquisition", "revision", "tree", "url"},
        "thingino_firmware",
    )
    if root["acquisition"] != "root":
        raise SourceError("thingino_firmware must be the root source")
    _require_hex40(root["revision"], "thingino_firmware revision")
    _require_hex40(root["tree"], "thingino_firmware tree")
    canonical_git_url(root["url"], "thingino_firmware URL")

    buildroot = _require_mapping(sources["buildroot"], "buildroot")
    _require_exact_keys(
        buildroot,
        {"acquisition", "path", "revision", "tree", "url"},
        "buildroot",
    )
    if (
        buildroot["acquisition"] != "submodule"
        or buildroot["path"] != "buildroot"
    ):
        raise SourceError("Buildroot must be the exact buildroot submodule")
    _require_hex40(buildroot["revision"], "buildroot revision")
    _require_hex40(buildroot["tree"], "buildroot tree")
    canonical_git_url(buildroot["url"], "buildroot URL")

    ingenic = _require_mapping(
        sources["ingenic_glibc216_toolchain"], "ingenic_glibc216_toolchain"
    )
    _require_exact_keys(
        ingenic,
        {"acquisition", "redistribution", "revision", "tree", "url"},
        "ingenic_glibc216_toolchain",
    )
    if (
        ingenic["acquisition"] != "external-build-toolchain"
        or ingenic["redistribution"] != "not-authorized-by-this-repository"
    ):
        raise SourceError("Ingenic toolchain boundary changed")
    _require_hex40(ingenic["revision"], "Ingenic toolchain revision")
    _require_hex40(ingenic["tree"], "Ingenic toolchain tree")
    canonical_git_url(ingenic["url"], "Ingenic toolchain URL")

    rust_version = "1.95.0"
    rust_builders = {
        "rust_builder_container_amd64": "linux/amd64",
        "rust_builder_container_arm64": "linux/arm64",
    }
    for source_name, platform in rust_builders.items():
        rust_builder = _require_mapping(sources[source_name], source_name)
        _require_exact_keys(
            rust_builder,
            {"acquisition", "digest", "image", "platform", "version"},
            source_name,
        )
        if (
            rust_builder["acquisition"] != "oci-image"
            or rust_builder["image"] != "docker.io/library/debian"
            or rust_builder["platform"] != platform
            or rust_builder["version"] != "bookworm-slim"
            or not isinstance(rust_builder["digest"], str)
            or not rust_builder["digest"].startswith("sha256:")
        ):
            raise SourceError("Rust builder container identity changed")
        _require_hex64(
            rust_builder["digest"].removeprefix("sha256:"),
            "Rust builder container digest",
        )
    rust_source = _require_mapping(sources["rust_source"], "rust_source")
    _require_exact_keys(
        rust_source,
        {"acquisition", "sha256", "url", "version"},
        "rust_source",
    )
    if rust_source["acquisition"] != "archive" or rust_source["version"] != rust_version:
        raise SourceError("Rust source identity changed")
    _require_hex64(rust_source["sha256"], "Rust source SHA-256")
    rust_archive_url(
        rust_source["url"], f"rustc-{rust_version}-src.tar.xz", "Rust source URL"
    )

    rust_std_source = _require_mapping(
        sources["rust_std_source_component"], "rust_std_source_component"
    )
    _require_exact_keys(
        rust_std_source,
        {"acquisition", "sha256", "url", "version"},
        "rust_std_source_component",
    )
    if (
        rust_std_source["acquisition"] != "archive"
        or rust_std_source["version"] != rust_version
    ):
        raise SourceError("Rust standard-library source identity changed")
    _require_hex64(rust_std_source["sha256"], "Rust source component SHA-256")
    rust_archive_url(
        rust_std_source["url"],
        f"rust-src-{rust_version}.tar.xz",
        "Rust source component URL",
    )

    rust_toolchains = {
        "rust_toolchain_aarch64_linux": "aarch64-unknown-linux-gnu",
        "rust_toolchain_x86_64_linux": "x86_64-unknown-linux-gnu",
    }
    for source_name, rust_host in rust_toolchains.items():
        rust_toolchain = _require_mapping(sources[source_name], source_name)
        _require_exact_keys(
            rust_toolchain,
            {"acquisition", "host", "sha256", "url", "version"},
            source_name,
        )
        if (
            rust_toolchain["acquisition"] != "archive"
            or rust_toolchain["version"] != rust_version
            or rust_toolchain["host"] != rust_host
        ):
            raise SourceError("Rust host toolchain identity changed")
        _require_hex64(rust_toolchain["sha256"], "Rust toolchain SHA-256")
        rust_archive_url(
            rust_toolchain["url"],
            f"rust-{rust_version}-{rust_host}.tar.xz",
            "Rust toolchain URL",
        )

    build_toolchains = {
        "thingino_build_toolchain_aarch64": ("aarch64", "linux/arm64"),
    }
    for source_name, (build_host, builder_platform) in build_toolchains.items():
        build_toolchain = _require_mapping(sources[source_name], source_name)
        _require_exact_keys(
            build_toolchain,
            {
                "acquisition",
                "archive",
                "archive_format",
                "build_target",
                "builder_platform",
                "host",
                "kernel_headers",
                "kernel_tarball_sha256",
                "kernel_tarball_url",
                "linux_kernel",
                "recipe",
                "sha256",
                "source",
                "version",
            },
            source_name,
        )
        build_toolchain_filename = (
            f"thingino-toolchain-{build_host}_xburst1_glibc_gcc16-linux-mipsel.tar.gz"
        )
        if (
            build_toolchain["acquisition"] != "source-build"
            or build_toolchain["archive"] != build_toolchain_filename
            or build_toolchain["archive_format"]
            != "gnu-tar-sort-name-source-date-epoch-gzip-n"
            or build_toolchain["build_target"]
            != "buildroot-toolchain-relocatable-sdk"
            or build_toolchain["builder_platform"] != builder_platform
            or build_toolchain["host"] != f"{build_host}-linux"
            or build_toolchain["kernel_headers"] != "custom-tarball-3.10"
            or build_toolchain["kernel_tarball_url"]
            != "https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.10.14.tar.xz"
            or build_toolchain["linux_kernel"] is not False
            or build_toolchain["recipe"]
            != "configs/github/toolchain_xburst1_glibc_gcc16_defconfig"
            or build_toolchain["source"] != "thingino_firmware"
            or build_toolchain["version"] != "gcc16-glibc-xburst1"
        ):
            raise SourceError("Thingino build toolchain identity changed")
        _require_hex64(
            build_toolchain["sha256"],
            "Thingino build toolchain SHA-256",
        )
        _require_hex64(
            build_toolchain["kernel_tarball_sha256"],
            "Thingino toolchain kernel tarball SHA-256",
        )

    unsupported_toolchain = _require_mapping(
        sources["thingino_build_toolchain_x86_64"],
        "thingino_build_toolchain_x86_64",
    )
    _require_exact_keys(
        unsupported_toolchain,
        {"acquisition", "host", "reason", "version"},
        "thingino_build_toolchain_x86_64",
    )
    if unsupported_toolchain != {
        "acquisition": "unsupported",
        "host": "x86_64-linux",
        "reason": "source-built SDK reproducibility is not validated",
        "version": "gcc16-glibc-xburst1",
    }:
        raise SourceError("Thingino x86-64 toolchain support changed")

    package_contract = {
        "prudynt": (
            "package/prudynt-t/prudynt-t.mk",
            "PRUDYNT_T_SITE",
            "PRUDYNT_T_VERSION",
        ),
        "thingino_ingenic_sdk": (
            "package/ingenic-sdk/ingenic-sdk.mk",
            "INGENIC_SDK_SITE",
            "INGENIC_SDK_VERSION",
        ),
        "rtl8188fu": (
            "package/wifi-rtl8188fu/wifi-rtl8188fu.mk",
            "WIFI_RTL8188FU_SITE",
            "WIFI_RTL8188FU_VERSION",
        ),
        "realtek_hostapd": (
            "package/wifi-rtw-hostapd/wifi-rtw-hostapd.mk",
            "WIFI_RTW_HOSTAPD_SITE",
            "WIFI_RTW_HOSTAPD_VERSION",
        ),
    }
    package_keys = {
        "acquisition",
        "recipe",
        "revision",
        "site_variable",
        "tree",
        "url",
        "version_variable",
    }
    for name, expected in package_contract.items():
        source = _require_mapping(sources[name], name)
        _require_exact_keys(source, package_keys, name)
        if source["acquisition"] != "buildroot-package":
            raise SourceError(f"{name} must be acquired by a Buildroot package")
        recipe, site_variable, version_variable = expected
        if (
            source["recipe"] != recipe
            or source["site_variable"] != site_variable
            or source["version_variable"] != version_variable
        ):
            raise SourceError(f"{name} recipe contract changed")
        _safe_relative_path(source["recipe"], f"{name} recipe")
        _require_hex40(source["revision"], f"{name} revision")
        _require_hex40(source["tree"], f"{name} tree")
        canonical_git_url(source["url"], f"{name} URL")
    return lock


def _git(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    network: bool = False,
) -> str:
    command = [
        "git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=",
        "-c",
        "credential.helper=",
    ]
    if network:
        command += [
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
        ]
    command += arguments
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
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        operation = arguments[0] if arguments else "command"
        raise SourceError(
            f"git {operation} failed with exit code {result.returncode}"
        )
    return result.stdout.strip()


def _recipe_variables(path: Path, required: set[str]) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SourceError(
            f"cannot read pinned package recipe {path.name}: {error}"
        ) from error
    values: dict[str, str] = {}
    for line in lines:
        match = VARIABLE.fullmatch(line)
        if match:
            name, value = match.groups()
            if name not in required:
                continue
            if name in values:
                raise SourceError(f"duplicate package variable {name} in {path.name}")
            values[name] = value
    return values


def _realtek_hostapd_recipe_url(path: Path, site_variable: str) -> str | None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SourceError(
            f"cannot read pinned package recipe {path.name}: {error}"
        ) from error
    expression = re.compile(
        rf"^{re.escape(site_variable)}\s*=\s*"
        r"\$\(call github,([A-Za-z0-9_.-]+),([A-Za-z0-9_.-]+),"
        r"\$\(WIFI_RTW_HOSTAPD_VERSION\)\)$",
        re.MULTILINE,
    )
    match = expression.search(source)
    if match is None:
        return None
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


def _verify_git_identity(
    checkout: Path,
    source: Mapping[str, Any],
    *,
    label: str,
    expected_epoch: int | None = None,
) -> dict[str, Any]:
    if not checkout.is_dir() or checkout.is_symlink():
        raise SourceError(f"{label} checkout must be a real directory")
    top = Path(_git(["rev-parse", "--show-toplevel"], cwd=checkout)).resolve()
    if top != checkout.resolve():
        raise SourceError(f"{label} checkout is not its Git worktree root")
    revision = _git(["rev-parse", "HEAD"], cwd=checkout)
    tree = _git(["rev-parse", "HEAD^{tree}"], cwd=checkout)
    if revision != source["revision"]:
        raise SourceError(f"{label} revision mismatch")
    if tree != source["tree"]:
        raise SourceError(f"{label} tree mismatch")
    remote = _git(["remote", "get-url", "origin"], cwd=checkout)
    if canonical_git_url(remote, f"{label} origin") != canonical_git_url(
        source["url"], f"{label} URL"
    ):
        raise SourceError(f"{label} origin mismatch")
    if _git(
        ["status", "--porcelain=v1", "--untracked-files=all"], cwd=checkout
    ):
        raise SourceError(f"{label} checkout is dirty")
    if expected_epoch is not None:
        timestamp = _git(["show", "-s", "--format=%ct", "HEAD"], cwd=checkout)
        if timestamp != str(expected_epoch):
            raise SourceError(f"{label} source-date epoch mismatch")
    return {
        "revision": revision,
        "tree": tree,
        "url": canonical_git_url(remote, label),
    }


def _read_gitmodules(checkout: Path) -> tuple[str, str]:
    path = checkout / ".gitmodules"
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as error:
        raise SourceError(f"cannot parse .gitmodules: {error}") from error
    section = 'submodule "buildroot"'
    if parser.sections() != [section] or set(parser[section]) != {
        "path",
        "url",
        "branch",
    }:
        raise SourceError(
            ".gitmodules must define only the reviewed Buildroot submodule"
        )
    if parser[section]["branch"] != "master":
        raise SourceError("Buildroot submodule branch metadata changed")
    return parser[section]["path"], parser[section]["url"]


def verify_checkout(
    checkout: Path, lock: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    checked_lock = load_lock() if lock is None else dict(lock)
    sources = _require_mapping(checked_lock["sources"], "sources")
    checkout = checkout.resolve()
    root = _verify_git_identity(
        checkout,
        _require_mapping(sources["thingino_firmware"], "thingino_firmware"),
        label="Thingino",
        expected_epoch=checked_lock["source_date_epoch"],
    )

    buildroot_source = _require_mapping(sources["buildroot"], "buildroot")
    submodule_path, submodule_url = _read_gitmodules(checkout)
    if submodule_path != buildroot_source["path"]:
        raise SourceError("Buildroot .gitmodules path mismatch")
    if canonical_git_url(
        submodule_url, "Buildroot .gitmodules URL"
    ) != canonical_git_url(buildroot_source["url"], "Buildroot URL"):
        raise SourceError("Buildroot .gitmodules URL mismatch")
    gitlink = _git(["ls-files", "--stage", "--", submodule_path], cwd=checkout)
    expected_gitlink = (
        f"160000 {buildroot_source['revision']} 0\t{submodule_path}"
    )
    if gitlink != expected_gitlink:
        raise SourceError("Buildroot gitlink mismatch")
    buildroot = _verify_git_identity(
        checkout / submodule_path,
        buildroot_source,
        label="Buildroot",
    )

    packages: dict[str, dict[str, str]] = {}
    for name in (
        "prudynt",
        "realtek_hostapd",
        "thingino_ingenic_sdk",
        "rtl8188fu",
    ):
        source = _require_mapping(sources[name], name)
        recipe_path = checkout / _safe_relative_path(
            source["recipe"], f"{name} recipe"
        )
        site_variable = source["site_variable"]
        version_variable = source["version_variable"]
        variables = _recipe_variables(
            recipe_path, {site_variable, version_variable}
        )
        recipe_url = variables.get(site_variable)
        if recipe_url is None and name == "realtek_hostapd":
            recipe_url = _realtek_hostapd_recipe_url(recipe_path, site_variable)
        if recipe_url is None or canonical_git_url(
            recipe_url, f"{name} recipe URL"
        ) != canonical_git_url(source["url"], f"{name} URL"):
            raise SourceError(f"{name} package URL mismatch")
        if variables.get(version_variable) != source["revision"]:
            raise SourceError(f"{name} package revision mismatch")
        packages[name] = {
            "recipe": source["recipe"],
            "revision": source["revision"],
            "tree": source["tree"],
            "url": canonical_git_url(source["url"], name),
        }

    return {
        "schema_version": 1,
        "operation": "thingino-source-checkout-verification",
        "host_only": True,
        "source_date_epoch": checked_lock["source_date_epoch"],
        "sources": {
            "thingino_firmware": root,
            "buildroot": buildroot,
            **packages,
        },
        "constraints": dict(checked_lock["constraints"]),
        "verified": True,
    }


def _outside_repository(destination: Path) -> Path:
    if not destination.is_absolute():
        raise SourceError("destination must be an absolute scratch path")
    resolved = destination.resolve(strict=False)
    if resolved == ROOT or ROOT in resolved.parents:
        raise SourceError("source checkout destination must be outside the repository")
    if destination.exists() or destination.is_symlink():
        raise SourceError("refusing to reuse an existing source destination")
    if not destination.parent.is_dir():
        raise SourceError("source destination parent does not exist")
    return resolved


def fetch_checkout(
    destination: Path, lock: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    checked_lock = load_lock() if lock is None else dict(lock)
    destination = _outside_repository(destination)
    sources = _require_mapping(checked_lock["sources"], "sources")
    root_source = _require_mapping(
        sources["thingino_firmware"], "thingino_firmware"
    )
    buildroot_source = _require_mapping(sources["buildroot"], "buildroot")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.fetch-", dir=destination.parent)
    )
    try:
        _git(["init", "--quiet"], cwd=temporary)
        _git(["remote", "add", "origin", root_source["url"]], cwd=temporary)
        _git(
            [
                "fetch",
                "--depth=1",
                "--no-tags",
                "origin",
                root_source["revision"],
            ],
            cwd=temporary,
            network=True,
        )
        _git(
            ["checkout", "--detach", "--quiet", root_source["revision"]],
            cwd=temporary,
        )
        _git(
            [
                "submodule",
                "update",
                "--init",
                "--depth=1",
                "--checkout",
                "--",
                buildroot_source["path"],
            ],
            cwd=temporary,
            network=True,
        )
        result = verify_checkout(temporary, checked_lock)
        temporary.replace(destination)
        result["checkout"] = str(destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-lock")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--checkout", type=Path, required=True)
    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()

    try:
        lock = load_lock(arguments.lock)
        if arguments.command == "validate-lock":
            result: dict[str, Any] = {
                "schema_version": lock["schema_version"],
                "source_date_epoch": lock["source_date_epoch"],
                "sources": sorted(lock["sources"]),
                "valid": True,
            }
        elif arguments.command == "verify":
            result = verify_checkout(arguments.checkout, lock)
        else:
            result = fetch_checkout(arguments.destination, lock)
    except SourceError as error:
        print(f"source checkout failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
