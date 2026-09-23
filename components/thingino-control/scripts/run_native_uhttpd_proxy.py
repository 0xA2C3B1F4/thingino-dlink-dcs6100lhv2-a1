#!/usr/bin/env python3
"""Build and exercise the canonical uhttpd proxy on a native Linux arm64 host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "docs/development/raptor-full/ingress/source-inputs.json"
REPOSITORY_PATCHES = ROOT / "patches/uhttpd"
HARNESS = ROOT / "components/thingino-control/scripts/host_uhttpd_proxy.py"
PATCH_TIMEOUT_SECONDS = 30
CONFIGURE_TIMEOUT_SECONDS = 120
BUILD_TIMEOUT_SECONDS = 300
HARNESS_TIMEOUT_SECONDS = 90
STRICT_PATCH_DIAGNOSTIC = re.compile(
    r"(?:offset|fuzz|failed|reversed|previously applied)", re.IGNORECASE
)


class RunnerError(RuntimeError):
    """A bounded native-runner precondition or command failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(
    source: Path, *arguments: str, index_file: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file)
    return subprocess.run(
        ["git", "-c", f"safe.directory={source}", "-C", str(source), *arguments],
        check=False,
        capture_output=True,
        env=environment,
        timeout=30,
    )


def verify_source_tree(source: Path, expected_tree: str) -> None:
    inside = _git(source, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        raise RunnerError("source must be a Git worktree with a prepared index")
    index_result = _git(source, "rev-parse", "--git-path", "index")
    if index_result.returncode != 0:
        raise RunnerError("could not locate the prepared source index")
    index = Path(index_result.stdout.decode("utf-8").strip())
    if not index.is_absolute():
        index = source / index
    tmpdir_value = os.environ.get("TMPDIR")
    if not tmpdir_value:
        raise RunnerError("TMPDIR must name an explicit writable scratch directory")
    tmpdir = Path(tmpdir_value).resolve(strict=True)
    with tempfile.NamedTemporaryFile(prefix="uhttpd-index-", dir=tmpdir) as copied_index:
        shutil.copyfile(index, copied_index.name)
        private_index = Path(copied_index.name)
        status = _git(
            source,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            index_file=private_index,
        )
        if status.returncode != 0:
            raise RunnerError("could not inspect source worktree status")
        records = [record for record in status.stdout.split(b"\0") if record]
        if records and not all(record.startswith(b"A  ") for record in records):
            raise RunnerError("source worktree differs from its prepared index")
        unstaged = _git(
            source, "diff", "--quiet", "--no-ext-diff", index_file=private_index
        )
        if unstaged.returncode != 0:
            raise RunnerError("source worktree differs from its prepared index")
        tree = _git(source, "write-tree", index_file=private_index)
        if tree.returncode != 0 or tree.stdout.decode("ascii", "replace").strip() != expected_tree:
            raise RunnerError("source Git tree does not match the canonical ingress manifest")


def load_manifest() -> dict[str, object]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise RunnerError("unsupported ingress manifest schema")
    source = data.get("sources", {}).get("uhttpd")
    patches = data.get("patches")
    if not isinstance(source, dict) or not isinstance(patches, list):
        raise RunnerError("invalid uhttpd ingress manifest")
    return data


def resolve_patch_chain(
    manifest: dict[str, object], upstream_patch_dir: Path
) -> list[tuple[Path, str]]:
    repository = {path.name: path for path in REPOSITORY_PATCHES.glob("*.patch")}
    chain: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for entry in manifest["patches"]:
        if not isinstance(entry, dict):
            raise RunnerError("invalid patch entry in ingress manifest")
        name = entry.get("name")
        expected = entry.get("sha256")
        if not isinstance(name, str) or not isinstance(expected, str) or name in seen:
            raise RunnerError("invalid patch identity in ingress manifest")
        seen.add(name)
        path = repository.get(name, upstream_patch_dir / name)
        if not path.is_file() or path.is_symlink():
            raise RunnerError(f"canonical patch is missing: {name}")
        if sha256_file(path) != expected:
            raise RunnerError(f"canonical patch hash changed: {name}")
        chain.append((path, expected))
    extras = sorted(set(repository) - seen)
    if extras:
        raise RunnerError("repository patches are absent from the ingress manifest: " + ", ".join(extras))
    return chain


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def validate_paths(
    source: Path, dependency_prefix: Path, upstream_patch_dir: Path, output: Path
) -> tuple[Path, Path, Path, Path]:
    source = source.resolve(strict=True)
    dependency_prefix = dependency_prefix.resolve(strict=True)
    upstream_patch_dir = upstream_patch_dir.resolve(strict=True)
    if not all(path.is_dir() for path in (source, dependency_prefix, upstream_patch_dir)):
        raise RunnerError("source, dependency prefix, and upstream patch inputs must be directories")
    for required in ("include/libubox", "lib/libubox.so", "lib/libustream-ssl.so"):
        if not (dependency_prefix / required).exists():
            raise RunnerError(f"dependency prefix is incomplete: {required}")
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    if output.exists() or output.is_symlink():
        raise RunnerError("output must not already exist")
    for protected in (ROOT.resolve(), source, dependency_prefix, upstream_patch_dir):
        if _overlaps(output, protected):
            raise RunnerError("output must not overlap the repository or any input")
    return source, dependency_prefix, upstream_patch_dir, output


def cmake_configure_command(source: Path, build: Path, install: Path, prefix: Path) -> list[str]:
    return [
        "cmake",
        "-S",
        str(source),
        "-B",
        str(build),
        f"-DCMAKE_INSTALL_PREFIX={install}",
        f"-DCMAKE_PREFIX_PATH={prefix}",
        "-DCMAKE_C_FLAGS=-Wno-error=stringop-overread",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DTLS_SUPPORT=ON",
        "-DLUA_SUPPORT=OFF",
        "-DUBUS_SUPPORT=OFF",
        "-DUCODE_SUPPORT=OFF",
    ]


def _run_logged(
    command: list[str], *, cwd: Path, env: dict[str, str], log: Path, timeout: int
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        captured = exc.stdout or b""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", "replace")
        log.write_text(captured + "\ncommand timed out\n", encoding="utf-8")
        raise RunnerError(f"command timed out after {timeout} seconds") from exc
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RunnerError(f"command failed with exit status {completed.returncode}")
    return completed.stdout


def apply_patch_chain(source: Path, chain: list[tuple[Path, str]], log_dir: Path) -> None:
    combined = log_dir / "patches.log"
    combined.write_text("", encoding="utf-8")
    for number, (patch, _) in enumerate(chain, start=1):
        per_patch = log_dir / f"patch-{number:02d}-{patch.name}.log"
        output = _run_logged(
            ["patch", "--batch", "--forward", "-p1", "-F0", "-i", str(patch)],
            cwd=source,
            env=os.environ.copy(),
            log=per_patch,
            timeout=PATCH_TIMEOUT_SECONDS,
        )
        with combined.open("a", encoding="utf-8") as destination:
            destination.write(f"== {patch.name} ==\n{output}")
        if STRICT_PATCH_DIAGNOSTIC.search(output):
            raise RunnerError(f"patch was not applied strictly: {patch.name}")
    if any(source.rglob("*.rej")) or any(source.rglob("*.orig")):
        raise RunnerError("patch application left reject or original files")


def write_receipt(path: Path, receipt: dict[str, object]) -> None:
    temporary = path.with_suffix(".json.new")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute(args: argparse.Namespace) -> Path:
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        raise RunnerError("native proxy runner requires Linux arm64")
    source, prefix, upstream, output = validate_paths(
        args.source, args.dependency_prefix, args.upstream_patch_dir, args.output
    )
    manifest = load_manifest()
    uhttpd = manifest["sources"]["uhttpd"]
    verify_source_tree(source, uhttpd["tree"])
    chain = resolve_patch_chain(manifest, upstream)

    output.mkdir()
    logs = output / "logs"
    patched = output / "source"
    build = output / "build"
    install = output / "install"
    artifacts = output / "artifacts"
    for directory in (logs, build, install, artifacts):
        directory.mkdir()
    receipt_path = output / "receipt.json"
    receipt: dict[str, object] = {
        "schema_version": 1,
        "status": "failed",
        "source": {"commit": uhttpd["commit"], "tree": uhttpd["tree"]},
        "patches": [{"name": path.name, "sha256": digest} for path, digest in chain],
        "logs": {
            "patches": "logs/patches.log",
            "configure": "logs/configure.log",
            "build": "logs/build.log",
            "harness": "logs/harness.log",
        },
    }
    phase = "copy_source"
    try:
        shutil.copytree(source, patched, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        phase = "apply_patches"
        apply_patch_chain(patched, chain, logs)
        environment = os.environ.copy()
        environment["PKG_CONFIG_PATH"] = str(prefix / "lib/pkgconfig")
        environment["LD_LIBRARY_PATH"] = str(prefix / "lib")
        phase = "configure"
        configure = cmake_configure_command(patched, build, install, prefix)
        _run_logged(
            configure,
            cwd=output,
            env=environment,
            log=logs / "configure.log",
            timeout=CONFIGURE_TIMEOUT_SECONDS,
        )
        phase = "build"
        _run_logged(
            ["cmake", "--build", str(build), "--parallel", str(args.jobs)],
            cwd=output,
            env=environment,
            log=logs / "build.log",
            timeout=BUILD_TIMEOUT_SECONDS,
        )
        binary = build / "uhttpd"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RunnerError("native build did not produce an executable uhttpd")
        artifact = artifacts / "uhttpd"
        shutil.copy2(binary, artifact)
        phase = "harness"
        _run_logged(
            [sys.executable, str(HARNESS), str(artifact)],
            cwd=ROOT,
            env=environment,
            log=logs / "harness.log",
            timeout=HARNESS_TIMEOUT_SECONDS,
        )
        phase = "verify_source_unchanged"
        verify_source_tree(source, uhttpd["tree"])
        receipt.update(
            {
                "status": "passed",
                "binary": {
                    "path": "artifacts/uhttpd",
                    "sha256": sha256_file(artifact),
                    "size_bytes": artifact.stat().st_size,
                },
                "cmake": {"configure": configure, "jobs": args.jobs},
                "harness": "passed",
            }
        )
        write_receipt(receipt_path, receipt)
        return receipt_path
    except Exception as exc:
        receipt["failure"] = {"phase": phase, "type": type(exc).__name__}
        write_receipt(receipt_path, receipt)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dependency-prefix", required=True, type=Path)
    parser.add_argument("--upstream-patch-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--jobs", type=int, choices=range(1, 17), default=4)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        receipt = execute(parse_args(argv))
    except (OSError, ValueError, json.JSONDecodeError, RunnerError) as exc:
        print(f"native uhttpd proxy check failed: {exc}", file=sys.stderr)
        return 1
    print(f"native uhttpd proxy check passed: {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
