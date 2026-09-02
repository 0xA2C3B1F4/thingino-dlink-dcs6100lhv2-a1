"""Build the read-only RAM collector root for an existing recovery pair."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from installer.artifacts import validate_squashfs
from installer.full_backup import target_layout_document
from installer.layout import TARGET
from installer.stage1.build import LINKER, validate_mmc_module
from installer.vendor_bundle import CATALOG_PATH


SOURCE = Path(__file__).with_name("init.c")
MAX_COLLECTOR_ROOT_SIZE = 2 * 1024 * 1024


class CollectorBuildError(ValueError):
    """The read-only collector or one of its inputs violates the contract."""


def _digest_array(name: str, digest: str) -> str:
    try:
        raw = bytes.fromhex(digest)
    except ValueError as exc:
        raise CollectorBuildError("vendor catalog contains an invalid digest") from exc
    if len(raw) != 32:
        raise CollectorBuildError("vendor catalog digest is not SHA-256")
    body = ", ".join(f"0x{byte:02x}" for byte in raw)
    return f"static const unsigned char {name}[32] = {{{body}}};"


def _c_string(value: str) -> str:
    if not value.isascii():
        raise CollectorBuildError("collector contract text must be ASCII")
    return json.dumps(value)


def _target_document() -> dict[str, object]:
    return {
        "erase_block_size": TARGET.erase_block_size,
        "flash_size": TARGET.nor_size,
        "hardware_revision": TARGET.hardware_revision,
        "model": TARGET.model,
        "partitions": [
            {
                "mtd": partition.mtd,
                "name": partition.name,
                "offset": partition.offset,
                "size": partition.size,
            }
            for partition in TARGET.partitions
        ],
    }


def _manifest(catalog: dict[str, object], *, include_optional: bool) -> str:
    source = catalog["source"]
    assert isinstance(source, dict)
    files = catalog["files"]
    assert isinstance(files, list)
    selected = []
    for entry in files:
        assert isinstance(entry, dict)
        if entry["required"] is True or include_optional:
            selected.append(
                {
                    "name": entry["name"],
                    "sha256": entry["sha256"],
                    "size": entry["size"],
                    "source_path": entry["source_path"],
                }
            )
    document = {
        "schema_version": 1,
        "target": catalog["target"],
        "source": {
            "firmware_version": source["firmware_version"],
            "mounted_read_only": True,
            "partition": source["partition"],
        },
        "files": selected,
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def render_contract(
    *, mmc_module: bytes, capture_mode: str = "existing-recovery"
) -> bytes:
    """Bind the executable to the public catalog and exact MMC module."""

    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorBuildError("cannot load the public vendor catalog") from exc
    if catalog.get("schema_version") != 1 or catalog.get("target") != _target_document():
        raise CollectorBuildError("vendor catalog target differs from collector target")
    files = catalog.get("files")
    if not isinstance(files, list) or len(files) != 4:
        raise CollectorBuildError("collector requires the exact four-file catalog")
    expected_names = (
        "libimp.so",
        "libalog.so",
        "libsysutils.so",
        "libaudioProcess.so",
    )
    if tuple(entry.get("name") for entry in files if isinstance(entry, dict)) != expected_names:
        raise CollectorBuildError("collector vendor catalog is reordered or incomplete")
    if tuple(entry.get("required") for entry in files) != (True, True, True, False):
        raise CollectorBuildError("collector vendor dispositions changed")
    if any(
        type(entry.get("size")) is not int
        or not 0 < entry["size"] <= TARGET.partition(3).size
        for entry in files
        if isinstance(entry, dict)
    ):
        raise CollectorBuildError("collector vendor catalog contains an invalid size")

    symbols = ("LIBIMP", "LIBALOG", "LIBSYSUTILS", "LIBAUDIOPROCESS")
    lines = [
        "#ifndef DCS6100_COLLECTOR_GENERATED_CONTRACT_H",
        "#define DCS6100_COLLECTOR_GENERATED_CONTRACT_H",
        '#define MMC_MODULE_PATH "/modules/jzmmc_v12.ko"',
        f"#define MMC_MODULE_SIZE {len(mmc_module)}U",
        _digest_array("MMC_MODULE_SHA256", hashlib.sha256(mmc_module).hexdigest()),
    ]
    for symbol, entry in zip(symbols, files, strict=True):
        assert isinstance(entry, dict)
        lines.extend(
            (
                f"#define {symbol}_SIZE {entry['size']}U",
                _digest_array(f"{symbol}_SHA256", str(entry["sha256"])),
            )
        )
    layout = {
        "read_only": True,
        "schema_version": 1,
        "target": _target_document(),
    }
    if capture_mode not in {
        "existing-recovery",
        "protected-readback",
        "complete-backup",
    }:
        raise CollectorBuildError("collector capture mode is unsupported")
    lines.extend(
        (
            f"#define FULL_BACKUP_CAPTURE {int(capture_mode == 'complete-backup')}",
            f"#define PROTECTED_CAPTURE {int(capture_mode == 'protected-readback')}",
            f"#define DEVICE_LAYOUT_JSON {_c_string(json.dumps(layout, indent=2, sort_keys=True) + chr(10))}",
            f"#define FULL_BACKUP_LAYOUT_JSON {_c_string(json.dumps(target_layout_document(), indent=2, sort_keys=True) + chr(10))}",
            f"#define VENDOR_MANIFEST_REQUIRED_JSON {_c_string(_manifest(catalog, include_optional=False))}",
            f"#define VENDOR_MANIFEST_OPTIONAL_JSON {_c_string(_manifest(catalog, include_optional=True))}",
            "#endif",
            "",
        )
    )
    return "\n".join(lines).encode("ascii")


def validate_collector_source(raw: bytes) -> None:
    """Reject source that exposes any NOR mutation or general-purpose interface."""

    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CollectorBuildError("collector source is not UTF-8") from exc
    forbidden = (
        "MEMERASE",
        "MEMUNLOCK",
        "O_RDWR",
        "SYSCALL_EXECVE",
        "SYSCALL_UNLINK",
        "SYSCALL_RENAME",
        "/bin/sh",
        "telnet",
        "dropbear",
        "fw_setenv",
        "saveenv",
    )
    for token in forbidden:
        if token in source:
            raise CollectorBuildError(f"collector source contains forbidden token: {token}")
    required = (
        'mount_checked("/dev/mtdblock3", "/stock", "jffs2",',
        "MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC",
        "O_WRONLY | O_CREAT | O_EXCL",
        'verify_mtd("/dev/mtd0", 0x00040000)',
        'verify_mtd("/dev/mtd5", 0x00040000)',
        "verify_target_model_marker();",
        'call2(SYSCALL_OPEN, (long)path, O_RDONLY)',
    )
    for token in required:
        if token not in source:
            raise CollectorBuildError(f"collector source lost safety invariant: {token}")


def _run(arguments: list[str], label: str, *, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode:
        tail = result.stderr.decode("utf-8", "replace")[-1200:]
        raise CollectorBuildError(f"{label} failed: {tail}")
    return result.stdout


def _resolve(executable: Path | None, fallback: str) -> Path:
    candidate = str(executable) if executable else shutil.which(fallback)
    if not candidate:
        raise CollectorBuildError(f"required build tool is missing: {fallback}")
    path = Path(candidate).resolve()
    if not path.is_file():
        raise CollectorBuildError(f"build tool is not a regular file: {path}")
    return path


def _resolve_llvm_pair(clang: Path | None, lld: Path | None) -> tuple[Path, Path]:
    if clang is None and lld is None:
        homebrew = Path("/opt/homebrew/opt/llvm/bin")
        homebrew_lld = shutil.which("ld.lld")
        if (homebrew / "clang").is_file() and homebrew_lld:
            return (homebrew / "clang").resolve(), Path(homebrew_lld).resolve()
    clang_path = _resolve(clang, "clang")
    if lld is None:
        sibling = clang_path.parent / "ld.lld"
        if sibling.is_file():
            return clang_path, sibling.resolve()
    return clang_path, _resolve(lld, "ld.lld")


def build_collector_root(
    *,
    mmc_module: bytes,
    output: Path,
    clang: Path | None = None,
    lld: Path | None = None,
    mksquashfs: Path | None = None,
    unsquashfs: Path | None = None,
    capture_mode: str = "existing-recovery",
) -> dict[str, object]:
    """Build one atomic read-only collector SquashFS; never contacts a device."""

    if output.exists() or output.is_symlink():
        raise CollectorBuildError("refusing to overwrite collector root")
    try:
        validate_mmc_module(mmc_module)
    except ValueError as exc:
        raise CollectorBuildError(str(exc)) from exc
    source_raw = SOURCE.read_bytes()
    validate_collector_source(source_raw)
    contract = render_contract(mmc_module=mmc_module, capture_mode=capture_mode)
    clang_path, lld_path = _resolve_llvm_pair(clang, lld)
    mksquashfs_path = _resolve(mksquashfs, "mksquashfs")
    unsquashfs_path = _resolve(unsquashfs, "unsquashfs")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = os.environ.get("TMPDIR")
    with tempfile.TemporaryDirectory(prefix="dcs6100-collector-", dir=temporary_root) as name:
        workspace = Path(name)
        root = workspace / "root"
        executable = workspace / "init"
        source = workspace / "init.c"
        linker = workspace / "linker.ld"
        header = workspace / "generated_contract.h"
        squashfs = workspace / "collector.squashfs"
        source.write_bytes(source_raw)
        linker.write_bytes(LINKER.read_bytes())
        header.write_bytes(contract)
        env = {**os.environ, "PATH": f"{lld_path.parent}:{os.environ.get('PATH', '')}"}
        _run(
            [
                str(clang_path),
                "--target=mipsel-linux-gnu",
                "-march=mips32",
                "-mabi=32",
                "-G0",
                "-mno-abicalls",
                "-fno-pic",
                "-ffreestanding",
                "-fno-builtin",
                "-fno-stack-protector",
                "-fno-unwind-tables",
                "-fno-asynchronous-unwind-tables",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Oz",
                "-nostdlib",
                "-static",
                "-fuse-ld=lld",
                "-Wl,-e,_start",
                f"-Wl,-T,{linker}",
                "-Wl,--build-id=none",
                "-Wl,-z,max-page-size=4096",
                "-Wl,-s",
                "-I",
                str(workspace),
                "-o",
                str(executable),
                str(source),
            ],
            "collector PID 1 compilation",
            env=env,
        )
        init = executable.read_bytes()
        identity = {
            "existing-recovery": b"/dev/mtdblock3",
            "protected-readback": b"/card/DCS6100P",
            "complete-backup": b"/card/DCS6100B",
        }[capture_mode]
        if init[:4] != b"\x7fELF" or identity not in init:
            raise CollectorBuildError("compiled collector lost its fixed identity")
        for token in (b"/bin/sh", b"telnet", b"dropbear", b"MEMERASE"):
            if token in init:
                raise CollectorBuildError("compiled collector gained a forbidden interface")
        for relative in ("card", "dev", "modules", "sbin", "stock"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "sbin/init").write_bytes(init)
        (root / "sbin/init").chmod(0o500)
        (root / "modules/jzmmc_v12.ko").write_bytes(mmc_module)
        (root / "modules/jzmmc_v12.ko").chmod(0o400)
        (root / "linuxrc").symlink_to("sbin/init")
        _run(
            [
                str(mksquashfs_path),
                str(root),
                str(squashfs),
                "-comp",
                "xz",
                "-noappend",
                "-all-root",
                "-no-xattrs",
                "-no-progress",
                "-repro-time",
                "0",
            ],
            "collector SquashFS build",
        )
        listing = _run(
            [str(unsquashfs_path), "-lln", str(squashfs)],
            "collector SquashFS inventory",
        ).decode("utf-8")
        expected = {
            "card",
            "dev",
            "linuxrc",
            "modules",
            "modules/jzmmc_v12.ko",
            "sbin",
            "sbin/init",
            "stock",
        }
        actual = set()
        for line in listing.splitlines():
            marker = "squashfs-root"
            if marker in line:
                relative = line.split(marker, 1)[1].lstrip("/").split(" -> ", 1)[0]
                if relative:
                    actual.add(relative)
        if actual != expected or "linuxrc -> sbin/init" not in listing:
            raise CollectorBuildError("collector SquashFS allowlist changed")
        raw = squashfs.read_bytes()
        validate_squashfs(raw, partition_limit=MAX_COLLECTOR_ROOT_SIZE)
        temporary_output = output.with_name(f".{output.name}.new")
        if temporary_output.exists() or temporary_output.is_symlink():
            raise CollectorBuildError("collector temporary output already exists")
        try:
            temporary_output.write_bytes(raw)
            os.replace(temporary_output, output)
        finally:
            if temporary_output.exists() and not temporary_output.is_symlink():
                temporary_output.unlink()
    return {
        "mode": capture_mode,
        "nor_writes": False,
        "output_sha256": hashlib.sha256(raw).hexdigest(),
        "output_size": len(raw),
        "preserved_mtd": [0, 4, 5],
        "vendor_files": (
            ["libimp.so", "libalog.so", "libsysutils.so"]
            if capture_mode == "existing-recovery"
            else []
        ),
        "optional_archive": (
            "libaudioProcess.so" if capture_mode == "existing-recovery" else None
        ),
    }
