#!/usr/bin/env python3
"""Build and validate the DCS-6100LHV2 Rust Control without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
TARGET = "mipsel-unknown-linux-gnu"
EXPECTED_RUST_RELEASE = "1.95.0"
EXPECTED_RUST_COMMIT = "59807616e1fa2540724bfbac14d7976d7e4a3860"
EXPECTED_PACKAGE = "thingino-control"
EXPECTED_BINARY = "thingino-controld"
SOURCE_DATE_EPOCH = "1787097600"
MAX_TEXT_INPUT = 4 * 1024 * 1024
MAX_BINARY_INPUT = 2 * 1024 * 1024
MAX_EXECUTABLE_INPUT = 512 * 1024 * 1024
ALLOWED_NEEDED = {
    "libc.so.6",
    "libdl.so.2",
    "libgcc_s.so.1",
    "libpthread.so.0",
    "librt.so.1",
}
NETWORK_ENVIRONMENT = {
    "ALL_PROXY",
    "CARGO_HTTP_PROXY",
    "CARGO_HTTP_USER_AGENT",
    "CARGO_NET_GIT_FETCH_WITH_CLI",
    "FTP_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "ftp_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}


class ControlBuildError(ValueError):
    """A build input, command result, or output violated the fixed contract."""


@dataclass(frozen=True)
class BuildInputs:
    rust_toolchain_root: Path
    rustc_source_root: Path
    ingenic_toolchain_root: Path
    manifest: Path
    readelf: Path
    output: Path
    scratch_root: Path
    data_volume_root: Path
    mips_toolchain_root: Path | None = None
    repository_root: Path | None = None


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ControlBuildError(f"{label} must be an absolute path")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ControlBuildError(f"cannot inspect {label}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ControlBuildError(f"{label} must be a non-symlink directory")
    return path


def _open_regular(path: Path, label: str, *, limit: int) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise ControlBuildError(f"cannot open {label}") from error
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > limit
    ):
        os.close(descriptor)
        raise ControlBuildError(f"{label} must be a bounded non-symlink regular file")
    return descriptor, metadata


def _read_regular(path: Path, label: str, *, limit: int = MAX_TEXT_INPUT) -> bytes:
    descriptor, before = _open_regular(path, label, limit=limit)
    try:
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise ControlBuildError(f"{label} read was incomplete")
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
        raise ControlBuildError(f"{label} changed while it was read")
    return b"".join(chunks)


def _require_executable(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ControlBuildError(f"{label} must be an absolute path")
    descriptor, metadata = _open_regular(path, label, limit=MAX_EXECUTABLE_INPUT)
    os.close(descriptor)
    if metadata.st_mode & 0o111 == 0:
        raise ControlBuildError(f"{label} is not executable")
    return path


def _validate_tree(root: Path, label: str, *, ignored: set[str] | None = None) -> None:
    ignored = ignored or set()
    count = 0
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        kept: list[str] = []
        for name in sorted(names):
            path = current / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ControlBuildError(f"{label} contains a symlink: {path.relative_to(root)}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise ControlBuildError(f"{label} contains an invalid directory entry")
            if name not in ignored:
                kept.append(name)
            count += 1
        names[:] = kept
        for name in sorted(files):
            path = current / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ControlBuildError(f"{label} contains a non-regular file")
            count += 1
        if count > 200_000:
            raise ControlBuildError(f"{label} contains too many entries")


def _run(
    arguments: Sequence[str | Path],
    *,
    label: str,
    environment: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> bytes:
    result = subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        suffix = ": " + " | ".join(detail[-12:]) if detail else ""
        raise ControlBuildError(f"{label} failed with exit code {result.returncode}{suffix}")
    return result.stdout


def _parse_version_fields(raw: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in raw.decode("utf-8", "strict").splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields


def _validate_rust_toolchain(root: Path) -> tuple[Path, Path]:
    rustc = _require_executable(root / "bin/rustc", "Rust compiler")
    cargo = _require_executable(root / "bin/cargo", "Cargo")
    fields = _parse_version_fields(
        _run([rustc, "--version", "--verbose"], label="Rust version check")
    )
    if fields.get("release") != EXPECTED_RUST_RELEASE:
        raise ControlBuildError("Rust compiler is not release 1.95.0")
    if fields.get("commit-hash") != EXPECTED_RUST_COMMIT:
        raise ControlBuildError("Rust compiler commit identity changed")
    cargo_version = _run([cargo, "--version"], label="Cargo version check").decode(
        "ascii", "strict"
    )
    if not cargo_version.startswith(f"cargo {EXPECTED_RUST_RELEASE} "):
        raise ControlBuildError("Cargo is not release 1.95.0")
    sysroot = _run([rustc, "--print", "sysroot"], label="Rust sysroot check").decode(
        "utf-8", "strict"
    ).strip()
    if Path(sysroot) != root:
        raise ControlBuildError("Rust compiler sysroot does not match toolchain root")
    return rustc, cargo


def _validate_rust_sources(toolchain_root: Path, source_root: Path) -> Path:
    library = _require_directory(source_root / "library", "Rust library source")
    vendor = _require_directory(source_root / "vendor", "vendored Rust dependencies")
    _validate_tree(library, "Rust library source")
    _validate_tree(vendor, "vendored Rust dependencies")
    anchors = ("Cargo.toml", "Cargo.lock", "std/Cargo.toml")
    embedded = toolchain_root / "lib/rustlib/src/rust/library"
    _require_directory(embedded, "toolchain Rust library source")
    for relative in anchors:
        supplied = _read_regular(library / relative, f"Rust source {relative}")
        toolchain = _read_regular(embedded / relative, f"toolchain Rust source {relative}")
        if supplied != toolchain:
            raise ControlBuildError(f"Rust source does not match toolchain: {relative}")
    checksums = list(vendor.glob("*/.cargo-checksum.json"))
    if not checksums:
        raise ControlBuildError("vendored Rust dependencies lack Cargo checksums")
    for path in checksums:
        _read_regular(path, "vendored Cargo checksum", limit=MAX_TEXT_INPUT)
    return library


def _validate_ingenic_toolchain(
    root: Path, mips_toolchain_root: Path | None
) -> tuple[Path, Path, Path, Path]:
    version = _read_regular(root / "VERSION", "Ingenic toolchain version").decode(
        "ascii", "strict"
    )
    if version.strip() != "mips32r2 linux release 2.3.3_gcc4.7.2":
        raise ControlBuildError("Ingenic toolchain version changed")
    source = _read_regular(root / ".SOURCE", "Ingenic toolchain source record").decode(
        "ascii", "strict"
    )
    if "glibc-2.16-2012.09/" not in source:
        raise ControlBuildError("Ingenic toolchain is not anchored to glibc 2.16")
    sysroot = _require_directory(root / "mips-linux-gnu/libc", "MIPS glibc sysroot")
    for relative in ("lib/ld-2.16.so", "lib/libc-2.16.so", "lib/libgcc_s.so.1"):
        _read_regular(sysroot / relative, f"MIPS sysroot {relative}", limit=64 * 1024 * 1024)
    if mips_toolchain_root is None:
        tools_root = root
        prefix = "mips-linux-gnu"
        gcc_name = f"{prefix}-gcc"
        expected_machine = "mips-linux-gnu"
        expected_version = "4.7.2"
    else:
        tools_root = _require_directory(
            mips_toolchain_root, "Buildroot MIPS compiler root"
        )
        prefix = "mipsel-thingino-linux-gnu"
        gcc_name = f"{prefix}-gcc.br_real"
        expected_machine = prefix
        expected_version = "16.1.0"
    gcc = _require_executable(tools_root / f"bin/{gcc_name}", "MIPS linker")
    strip = _require_executable(tools_root / f"bin/{prefix}-strip", "MIPS strip tool")
    ar = _require_executable(tools_root / f"bin/{prefix}-ar", "MIPS archive tool")
    machine = _run([gcc, "-dumpmachine"], label="MIPS linker target check").decode(
        "ascii", "strict"
    ).strip()
    if machine != expected_machine:
        raise ControlBuildError("MIPS compiler targets an unexpected architecture")
    compiler_version = _run([gcc, "-dumpversion"], label="MIPS linker version check").decode(
        "ascii", "strict"
    ).strip()
    if compiler_version != expected_version:
        raise ControlBuildError("MIPS compiler version changed")
    return gcc, strip, ar, sysroot


def _load_manifest(manifest: Path) -> str:
    if not manifest.is_absolute():
        raise ControlBuildError("Cargo manifest must be an absolute path")
    raw = _read_regular(manifest, "Cargo manifest")
    lock = _read_regular(manifest.with_name("Cargo.lock"), "Cargo lock file")
    try:
        document = tomllib.loads(raw.decode("utf-8"))
        lock_document = tomllib.loads(lock.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ControlBuildError("Cargo manifest or lock file is invalid") from error
    package = document.get("package")
    if not isinstance(package, dict) or package.get("name") != EXPECTED_PACKAGE:
        raise ControlBuildError("Cargo manifest has the wrong package identity")
    for section in ("dependencies", "build-dependencies"):
        value = document.get(section, {})
        if not isinstance(value, dict) or value:
            raise ControlBuildError("Thingino Control build must remain dependency-free")
    if package.get("build") not in (None, False):
        raise ControlBuildError("Thingino Control build scripts are not allowed")
    build_script = manifest.parent / "build.rs"
    if build_script.exists() or build_script.is_symlink():
        raise ControlBuildError("Thingino Control build scripts are not allowed")
    packages = lock_document.get("package")
    if (
        lock_document.get("version") != 4
        or not isinstance(packages, list)
        or len(packages) != 1
        or not isinstance(packages[0], dict)
        or packages[0].get("name") != EXPECTED_PACKAGE
    ):
        raise ControlBuildError("Cargo lock contains an unexpected dependency closure")
    project = _require_directory(manifest.parent, "Cargo source directory")
    _validate_tree(project, "Cargo source", ignored={".git", "target"})
    if document.get("bin") not in (None, []):
        binaries = document["bin"]
        if (
            not isinstance(binaries, list)
            or len(binaries) != 1
            or not isinstance(binaries[0], dict)
            or binaries[0].get("name") != EXPECTED_BINARY
        ):
            raise ControlBuildError("Cargo manifest must produce one Thingino Control binary")
    return EXPECTED_BINARY


def _validate_data_destination(inputs: BuildInputs) -> None:
    if not inputs.output.is_absolute() or not inputs.output.name:
        raise ControlBuildError("output must be an absolute file path")
    scratch = _require_directory(inputs.scratch_root, "scratch root")
    output_parent = _require_directory(inputs.output.parent, "output parent")
    data_root = _require_directory(
        inputs.data_volume_root, "approved data volume"
    ).resolve(strict=True)
    if not _path_within(scratch.resolve(strict=True), data_root):
        raise ControlBuildError("scratch root must be on the approved data volume")
    if not _path_within(output_parent.resolve(strict=True), data_root):
        raise ControlBuildError("output parent must be on the approved data volume")
    repository_root = _require_directory(
        inputs.repository_root or ROOT, "repository root"
    ).resolve(strict=True)
    if _path_within(output_parent.resolve(strict=True), repository_root):
        raise ControlBuildError("binary output must stay outside the repository")
    if inputs.output.exists() or inputs.output.is_symlink():
        raise ControlBuildError("refusing to overwrite an existing Control output")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _write_build_files(
    workspace: Path,
    *,
    gcc: Path,
    vendor: Path,
    sysroot: Path,
) -> tuple[Path, Path]:
    linker = workspace / "mips-linker"
    linker.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"exec {_shell_quote(str(gcc))} -EL -march=mips32r2 -mabi=32 -mhard-float "
        f"--sysroot={_shell_quote(str(sysroot))} "
        f"-L{_shell_quote(str(sysroot / 'lib'))} "
        f"-L{_shell_quote(str(sysroot / 'usr/lib'))} "
        f"-Wl,-rpath-link,{_shell_quote(str(sysroot / 'lib'))} "
        f"-Wl,-rpath-link,{_shell_quote(str(sysroot / 'usr/lib'))} "
        "-Wl,--dynamic-linker=/lib/ld.so.1 \"$@\"\n",
        encoding="ascii",
    )
    linker.chmod(0o700)
    cargo_home = workspace / "cargo-home"
    cargo_home.mkdir(mode=0o700)
    (cargo_home / "config.toml").write_text(
        "[net]\n"
        "offline = true\n\n"
        "[source.crates-io]\n"
        'replace-with = "vendored-sources"\n\n'
        "[source.vendored-sources]\n"
        f"directory = {_toml_string(str(vendor))}\n",
        encoding="utf-8",
    )
    return linker, cargo_home


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _build_environment(
    inputs: BuildInputs,
    *,
    workspace: Path,
    rustc: Path,
    cargo_home: Path,
    library: Path,
    linker: Path,
    gcc: Path,
    ar: Path,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    home = workspace / "home"
    temporary = workspace / "tmp"
    target = workspace / "target"
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    source = inputs.manifest.parent.resolve(strict=True)
    rust_flags = " ".join(
        (
            "-Ctarget-cpu=mips32r2",
            "-Cpanic=abort",
            f"--remap-path-prefix={source}=/control-source",
            f"--remap-path-prefix={workspace}=/build",
        )
    )
    environment.update(
        {
            "AR_mipsel_unknown_linux_gnu": str(ar),
            "CARGO_HOME": str(cargo_home),
            "CARGO_INCREMENTAL": "0",
            "CARGO_NET_OFFLINE": "true",
            "CARGO_TERM_COLOR": "never",
            "CARGO_TARGET_DIR": str(target),
            "CARGO_TARGET_MIPSEL_UNKNOWN_LINUX_GNU_LINKER": str(linker),
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER": "/usr/bin/cc",
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS": (
                "-Clink-arg=-fuse-ld=bfd"
            ),
            "CC_mipsel_unknown_linux_gnu": str(gcc),
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": f"{rustc.parent}:{gcc.parent}:/usr/bin:/bin",
            "RUSTC": str(rustc),
            "RUSTC_BOOTSTRAP": "1",
            "RUSTFLAGS": rust_flags,
            "RUST_SRC_PATH": str(library),
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            "TMPDIR": str(temporary),
            "TZ": "UTC",
        }
    )
    return environment


def _readelf(readelf: Path, candidate: Path, option: str, label: str) -> str:
    return _run([readelf, option, candidate], label=label).decode("utf-8", "strict")


def _validate_elf_and_snapshot(
    candidate: Path, *, readelf: Path
) -> tuple[dict[str, object], bytes]:
    header = _readelf(readelf, candidate, "-hW", "ELF header check")
    if not re.search(r"Class:\s+ELF32\b", header):
        raise ControlBuildError("Control is not ELF32")
    if not re.search(r"Data:\s+2's complement, little endian\b", header):
        raise ControlBuildError("Control is not little-endian")
    if not re.search(r"Machine:\s+MIPS", header):
        raise ControlBuildError("Control is not a MIPS executable")
    if not re.search(r"Flags:.*\bo32\b", header, re.IGNORECASE):
        raise ControlBuildError("Control does not declare the O32 ABI")
    attributes = _readelf(readelf, candidate, "-AW", "MIPS ABI check")
    if not (
        re.search(r"ISA:\s+MIPS32r2\b", attributes, re.IGNORECASE)
        or re.search(r"Flags:.*\bmips32r2\b", header, re.IGNORECASE)
    ):
        raise ControlBuildError("Control is not MIPS32r2")
    gpr_size = re.search(r"GPR size:\s+(\d+)\b", attributes, re.IGNORECASE)
    if gpr_size is not None and gpr_size.group(1) != "32":
        raise ControlBuildError("Control does not use 32-bit MIPS registers")
    if not re.search(
        r"(?:FP ABI|Tag_GNU_MIPS_ABI_FP):\s+Hard float", attributes, re.IGNORECASE
    ):
        raise ControlBuildError("Control is not hard-float")
    program = _readelf(readelf, candidate, "-lW", "ELF interpreter check")
    interpreters = re.findall(r"Requesting program interpreter:\s*([^\]]+)\]", program)
    if interpreters != ["/lib/ld.so.1"]:
        raise ControlBuildError("Control interpreter is not exactly /lib/ld.so.1")
    dynamic = _readelf(readelf, candidate, "-dW", "ELF dependency check")
    needed = set(re.findall(r"\(NEEDED\).*Shared library:\s*\[([^\]]+)\]", dynamic))
    if "libc.so.6" not in needed or not needed <= ALLOWED_NEEDED:
        unexpected = sorted(needed - ALLOWED_NEEDED)
        raise ControlBuildError(f"Control has an invalid DT_NEEDED closure: {unexpected}")
    versions = _readelf(readelf, candidate, "-VW", "glibc version check")
    if "GLIBC_PRIVATE" in versions:
        raise ControlBuildError("Control requires GLIBC_PRIVATE")
    glibc_versions = {
        tuple(int(part) for part in match.groups())
        for match in re.finditer(r"\bGLIBC_(\d+)\.(\d+)\b", versions)
    }
    if not glibc_versions:
        raise ControlBuildError("Control has no versioned glibc requirements")
    if any(version > (2, 16) for version in glibc_versions):
        raise ControlBuildError("Control requires a glibc version newer than 2.16")
    raw = _read_regular(candidate, "built Thingino Control", limit=MAX_BINARY_INPUT)
    evidence: dict[str, object] = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "dt_needed": sorted(needed),
        "glibc_versions": [f"{major}.{minor}" for major, minor in sorted(glibc_versions)],
    }
    return evidence, raw


def validate_elf(candidate: Path, *, readelf: Path) -> dict[str, object]:
    evidence, _ = _validate_elf_and_snapshot(candidate, readelf=readelf)
    return evidence


def _publish_atomic(snapshot: bytes, output: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(snapshot)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.chmod(0o755)
        try:
            os.link(temporary, output)
        except FileExistsError as error:
            raise ControlBuildError("Control output appeared during publication") from error
        parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def build(inputs: BuildInputs) -> dict[str, object]:
    _validate_data_destination(inputs)
    rust_root = _require_directory(inputs.rust_toolchain_root, "Rust toolchain root")
    rust_source = _require_directory(inputs.rustc_source_root, "Rust source root")
    ingenic_root = _require_directory(inputs.ingenic_toolchain_root, "Ingenic toolchain root")
    readelf = _require_executable(inputs.readelf, "readelf tool")
    rustc, cargo = _validate_rust_toolchain(rust_root)
    library = _validate_rust_sources(rust_root, rust_source)
    gcc, strip, ar, sysroot = _validate_ingenic_toolchain(
        ingenic_root, inputs.mips_toolchain_root
    )
    binary_name = _load_manifest(inputs.manifest)

    workspace = Path(
        tempfile.mkdtemp(prefix=".thingino-control-build-", dir=inputs.scratch_root)
    )
    try:
        linker, cargo_home = _write_build_files(
            workspace,
            gcc=gcc,
            vendor=rust_source / "vendor",
            sysroot=sysroot,
        )
        environment = _build_environment(
            inputs,
            workspace=workspace,
            rustc=rustc,
            cargo_home=cargo_home,
            library=library,
            linker=linker,
            gcc=gcc,
            ar=ar,
        )
        _run(
            [
                cargo,
                "build",
                "--manifest-path",
                inputs.manifest,
                "--target",
                TARGET,
                "--release",
                "--locked",
                "--offline",
                "-Z",
                "build-std=std,panic_abort",
            ],
            label="offline Rust Control build",
            environment=environment,
            cwd=inputs.manifest.parent,
        )
        candidate = workspace / "target" / TARGET / "release" / binary_name
        _require_executable(candidate, "Cargo Control output")
        _run([strip, "--strip-unneeded", candidate], label="Thingino Control strip")
        evidence, snapshot = _validate_elf_and_snapshot(candidate, readelf=readelf)
        _publish_atomic(snapshot, inputs.output)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return {
        "schema_version": 1,
        "target": "DCS-6100LHV2-A1",
        "rust_release": EXPECTED_RUST_RELEASE,
        "compile_network": "cargo-offline",
        "output": str(inputs.output),
        **evidence,
    }


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--rust-toolchain-root", type=Path, required=True)
    argument_parser.add_argument("--rustc-source-root", type=Path, required=True)
    argument_parser.add_argument("--ingenic-toolchain-root", type=Path, required=True)
    argument_parser.add_argument("--mips-toolchain-root", type=Path)
    argument_parser.add_argument("--manifest", type=Path, required=True)
    argument_parser.add_argument("--readelf", type=Path, required=True)
    argument_parser.add_argument("--output", type=Path, required=True)
    argument_parser.add_argument("--scratch-root", type=Path, required=True)
    argument_parser.add_argument("--data-volume-root", type=Path, required=True)
    argument_parser.add_argument("--repository-root", type=Path)
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        result = build(BuildInputs(**vars(options)))
    except ControlBuildError as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
