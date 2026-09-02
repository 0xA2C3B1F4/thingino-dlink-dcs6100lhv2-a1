"""Build the bounded RAM-resident same-device stock restorer."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from installer.artifacts import validate_squashfs
from installer.fake_mtd import StockRestoreImages
from installer.stage1.build import LINKER, validate_mmc_module


SOURCE = Path(__file__).with_name("init.c")
SHA256_HEADER = SOURCE.parents[1] / "freestanding_sha256.h"
MAX_RESTORER_ROOT_SIZE = 2 * 1024 * 1024


class StockRestoreBuildError(ValueError):
    """A live stock-restorer input or build output is unsafe."""


def _digest_array(name: str, raw: bytes) -> str:
    digest = hashlib.sha256(raw).digest()
    body = ", ".join(f"0x{byte:02x}" for byte in digest)
    return f"static const unsigned char {name}[32] = {{{body}}};"


def render_contract(
    *, images: StockRestoreImages, mmc_module: bytes, authorization: bytes
) -> bytes:
    images.validate()
    try:
        validate_mmc_module(mmc_module)
    except ValueError as exc:
        raise StockRestoreBuildError(str(exc)) from exc
    if len(authorization) != 32:
        raise StockRestoreBuildError("live restore authorization must be 32 bytes")
    entries = {
        "MTD1": images.mtd1,
        "MTD2": images.mtd2,
        "MTD3": images.mtd3,
        "KEEP0": images.protected[0],
        "KEEP4": images.protected[1],
        "KEEP5": images.protected[2],
        "AUTH": authorization,
        "MMC_MODULE": mmc_module,
    }
    lines = [
        "#ifndef DCS6100_STOCK_RESTORE_CONTRACT_H",
        "#define DCS6100_STOCK_RESTORE_CONTRACT_H",
        '#define MMC_MODULE_PATH "/modules/jzmmc_v12.ko"',
    ]
    for name, raw in entries.items():
        lines.append(f"#define {name}_SIZE {len(raw)}U")
        lines.append(_digest_array(f"{name}_SHA256", raw))
    lines.extend(("#endif", ""))
    return "\n".join(lines).encode("ascii")


def validate_restorer_source(raw: bytes) -> None:
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StockRestoreBuildError("stock restorer source is not UTF-8") from exc
    forbidden = (
        "/bin/sh",
        "SYSCALL_EXECVE",
        "SYSCALL_REBOOT",
        "saveenv",
        "fw_setenv",
        "dropbear",
        "telnet",
        '"/dev/mtd0", O_RDWR',
        '"/dev/mtd4", O_RDWR',
        '"/dev/mtd5", O_RDWR',
        '"/dev/mtd6", O_RDWR',
    )
    for token in forbidden:
        if token in source:
            raise StockRestoreBuildError(
                f"stock restorer source contains forbidden token: {token}"
            )
    required = (
        '#define MTD1_PATH "/dev/mtd1"',
        '#define MTD2_PATH "/dev/mtd2"',
        '#define MTD3_PATH "/dev/mtd3"',
        'verify_mtd("/dev/mtd0", 0x00040000, 0)',
        'verify_mtd("/dev/mtd4", 0x00180000, 0)',
        'verify_mtd("/dev/mtd5", 0x00040000, 0)',
        "verify_target_model_marker();",
        "restore_complete_partition(MTD3_FILE, MTD3_PATH",
        "restore_complete_partition(MTD2_FILE, MTD2_PATH",
        "erase_range(descriptor, ACTIVATION_SIZE, MTD1_SIZE - ACTIVATION_SIZE)",
        "erase_range(descriptor, 0, ACTIVATION_SIZE)",
        "SYSCALL_MLOCKALL",
        "MCL_CURRENT | MCL_FUTURE",
        "RESTORE ram_residency_locked",
        '#define RUN_TEMP_FILE "/card/.RESTORE.RUN.part"',
        "SYSCALL_RENAME",
        "RESTORE COMPLETE physical_readback_verified",
    )
    for token in required:
        if token not in source:
            raise StockRestoreBuildError(
                f"stock restorer source lost safety invariant: {token}"
            )
    if source.index("RUN_TEMP_FILE, RUN_FILE") > source.index(
        "call1(SYSCALL_UNLINK, (long)AUTH_FILE)"
    ):
        raise StockRestoreBuildError(
            "stock restorer must activate RESTORE.RUN before consuming RESTORE.GO"
        )


def _resolve(executable: Path | None, fallback: str) -> Path:
    candidate = str(executable) if executable else shutil.which(fallback)
    if not candidate:
        raise StockRestoreBuildError(f"required build tool is missing: {fallback}")
    result = Path(candidate).resolve()
    if not result.is_file():
        raise StockRestoreBuildError(
            f"build tool is not a regular file: {result}"
        )
    return result


def _resolve_llvm_pair(
    clang: Path | None, lld: Path | None
) -> tuple[Path, Path]:
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
        raise StockRestoreBuildError(f"{label} failed: {tail}")
    return result.stdout


def build_stock_restore_root(
    *,
    images: StockRestoreImages,
    mmc_module: bytes,
    authorization: bytes,
    output: Path,
    clang: Path | None = None,
    lld: Path | None = None,
    mksquashfs: Path | None = None,
    unsquashfs: Path | None = None,
) -> dict[str, object]:
    """Build one private SquashFS.  This function never contacts a device."""

    if output.exists() or output.is_symlink():
        raise StockRestoreBuildError("refusing to overwrite stock restorer root")
    source_raw = SOURCE.read_bytes()
    sha256_raw = SHA256_HEADER.read_bytes()
    validate_restorer_source(source_raw)
    contract = render_contract(
        images=images, mmc_module=mmc_module, authorization=authorization
    )
    clang_path, lld_path = _resolve_llvm_pair(clang, lld)
    mksquashfs_path = _resolve(mksquashfs, "mksquashfs")
    unsquashfs_path = _resolve(unsquashfs, "unsquashfs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="dcs6100-stock-restorer-", dir=os.environ.get("TMPDIR")
    ) as name:
        workspace = Path(name)
        root = workspace / "root"
        executable = workspace / "init"
        squashfs = workspace / "restorer.squashfs"
        (workspace / "init.c").write_bytes(source_raw)
        (workspace / "generated_contract.h").write_bytes(contract)
        (workspace / "freestanding_sha256.h").write_bytes(sha256_raw)
        (workspace / "linker.ld").write_bytes(LINKER.read_bytes())
        environment = {
            **os.environ,
            "PATH": f"{lld_path.parent}:{os.environ.get('PATH', '')}",
        }
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
                f"-Wl,-T,{workspace / 'linker.ld'}",
                "-Wl,--build-id=none",
                "-Wl,-z,max-page-size=4096",
                "-Wl,-s",
                "-I",
                str(workspace),
                "-o",
                str(executable),
                str(workspace / "init.c"),
            ],
            "stock restorer PID 1 compilation",
            env=environment,
        )
        init = executable.read_bytes()
        if init[:4] != b"\x7fELF":
            raise StockRestoreBuildError("compiled stock restorer is not ELF")
        for token in (b"/bin/sh", b"telnet", b"dropbear", b"/dev/mtd6\x00O_RDWR"):
            if token in init:
                raise StockRestoreBuildError(
                    "compiled stock restorer gained a forbidden interface"
                )
        for relative in ("card", "dev", "modules", "sbin", "sys"):
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
            "stock restorer SquashFS build",
        )
        listing = _run(
            [str(unsquashfs_path), "-lln", str(squashfs)],
            "stock restorer SquashFS inventory",
        ).decode("utf-8")
        expected = {
            "card",
            "dev",
            "linuxrc",
            "modules",
            "modules/jzmmc_v12.ko",
            "sbin",
            "sbin/init",
            "sys",
        }
        actual: set[str] = set()
        for line in listing.splitlines():
            if "squashfs-root" in line:
                relative = line.split("squashfs-root", 1)[1].lstrip("/").split(
                    " -> ", 1
                )[0]
                if relative:
                    actual.add(relative)
        if actual != expected or "linuxrc -> sbin/init" not in listing:
            raise StockRestoreBuildError("stock restorer SquashFS allowlist changed")
        raw = squashfs.read_bytes()
        validate_squashfs(raw, partition_limit=MAX_RESTORER_ROOT_SIZE)
        temporary = output.with_name(f".{output.name}.new")
        if temporary.exists() or temporary.is_symlink():
            raise StockRestoreBuildError("stock restorer temporary output exists")
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, output)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
    return {
        "nor_writes": False,
        "output_size": len(raw),
        "protected_mtd": [0, 4, 5],
        "write_set": [3, 2, 1],
    }
