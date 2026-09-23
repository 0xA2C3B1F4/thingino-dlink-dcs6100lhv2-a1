"""Assemble the bounded RAM-only setup-AP SquashFS from public build output."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from installer.artifacts import validate_squashfs
from installer.layout import TARGET
from installer.stage1.build import validate_mmc_module


INIT = Path(__file__).with_name("init.sh")
RECOVERYCTL = Path(__file__).with_name("recoveryctl.sh")
MDNS_SERVICE = Path(__file__).with_name("recovery.service")
DTRNG_SOURCE = Path(__file__).with_name("ingenic_t31_dtrng.c")
ENTROPY_SOURCE = Path(__file__).with_name("entropy_seed.c")
THINGINO_ENTER_SOURCE = Path(__file__).with_name("thingino_enter.c")
FREESTANDING_LINKER = Path(__file__).parents[1] / "stage1/linker.ld"
MAX_ROOT_SIZE = TARGET.partition(2).size
RUNTIME_LIBRARIES = {
    "ld.so.1",
    "libc.so.6",
    "libcrypt.so",
    "libcrypt.so.2",
    "libcrypt.so.2.0.0",
    "libmdnsd.so",
    "libmdnsd.so.2",
    "libmdnsd.so.2.1.0",
    "libnl-3.so",
    "libnl-3.so.200",
    "libnl-3.so.200.26.0",
    "libnl-genl-3.so",
    "libnl-genl-3.so.200",
    "libnl-genl-3.so.200.26.0",
    "libnss_dns.so.2",
    "libnss_files.so.2",
    "libresolv.so.2",
}


class RecoveryApBuildError(ValueError):
    """The recovery-AP root or one of its inputs violates the allowlist."""


def _run(
    arguments: list[str], label: str, *, env: dict[str, str] | None = None
) -> bytes:
    result = subprocess.run(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
    )
    if result.returncode:
        tail = result.stderr.decode("utf-8", "replace")[-1600:]
        raise RecoveryApBuildError(f"{label} failed: {tail}")
    return result.stdout


def _resolve(executable: Path | None, fallback: str) -> Path:
    candidate = str(executable) if executable else shutil.which(fallback)
    if not candidate:
        raise RecoveryApBuildError(f"required build tool is missing: {fallback}")
    path = Path(candidate).resolve()
    if not path.is_file():
        raise RecoveryApBuildError(f"build tool is not a regular file: {path}")
    return path


def _resolve_llvm_pair(
    clang: Path | None, lld: Path | None
) -> tuple[Path, Path]:
    clang_path = _resolve(clang, "clang")
    if lld is None:
        sibling = clang_path.parent / "ld.lld"
        if sibling.is_file():
            return clang_path, sibling.resolve()
    return clang_path, _resolve(lld, "ld.lld")


def _build_thingino_enter(
    *, workspace: Path, clang: Path, lld: Path
) -> bytes:
    executable = workspace / "thingino-enter"
    env = {**os.environ, "PATH": f"{lld.parent}:{os.environ.get('PATH', '')}"}
    _run(
        [
            str(clang),
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
            f"-Wl,-T,{FREESTANDING_LINKER}",
            "-Wl,--build-id=none",
            "-Wl,-z,max-page-size=4096",
            "-Wl,-s",
            "-o",
            str(executable),
            str(THINGINO_ENTER_SOURCE),
        ],
        "fixed Thingino chroot launcher compilation",
        env=env,
    )
    raw = executable.read_bytes()
    if (
        not raw.startswith(b"\x7fELF")
        or b"/mnt/thingino" not in raw
        or b"/etc/init.d/rcS" not in raw
        or b"RECOVERY_AP THINGINO_ENTER_FAIL" not in raw
    ):
        raise RecoveryApBuildError("compiled Thingino launcher lost its fixed identity")
    for forbidden in (b"/dev/mtd", b"flashcp", b"/bin/login"):
        if forbidden in raw:
            raise RecoveryApBuildError("compiled Thingino launcher gained a forbidden interface")
    return raw


def _extract_target(archive: Path, destination: Path) -> None:
    if archive.is_symlink() or not archive.is_file():
        raise RecoveryApBuildError("target archive is not a regular file")
    exact = {
        "usr/bin/busybox",
        "usr/sbin/dropbear",
        "usr/sbin/flashcp",
        "usr/sbin/hostapd",
        "usr/sbin/mdnsd",
        "usr/sbin/wpa_supplicant",
        "usr/lib/firmware/PHY_REG_PG.txt",
        *(f"usr/lib/{name}" for name in RUNTIME_LIBRARIES),
    }
    seen: set[str] = set()
    with tarfile.open(archive, mode="r:") as package:
        for member in package.getmembers():
            relative = member.name.removeprefix("./")
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise RecoveryApBuildError("target archive contains an unsafe path")
            selected = relative in exact
            if not selected:
                continue
            if relative in seen:
                raise RecoveryApBuildError("target archive contains a duplicate input")
            seen.add(relative)
            destination_path = destination / relative
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                source = package.extractfile(member)
                if source is None:
                    raise RecoveryApBuildError("target archive file cannot be read")
                destination_path.write_bytes(source.read())
            elif member.issym():
                link = member.linkname
                if "/" in link or link in {"", ".", ".."}:
                    raise RecoveryApBuildError("target archive library link is unsafe")
                destination_path.symlink_to(link)
            else:
                raise RecoveryApBuildError("selected target input has an invalid type")
    missing = exact - seen
    if missing:
        raise RecoveryApBuildError(
            f"target archive is missing required input: {sorted(missing)[0]}"
        )


def _copy(source: Path, destination: Path, mode: int | None = None) -> None:
    if source.is_symlink() or not source.is_file():
        raise RecoveryApBuildError(f"required target file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode if mode is not None else source.stat().st_mode & 0o777)


def build_recovery_ap_root(
    *,
    target_archive: Path,
    wifi_module: bytes,
    mmc_module: bytes,
    dtrng_module: bytes,
    entropy_seed: bytes,
    output: Path,
    session_media_dir: Path | None = None,
    clang: Path | None = None,
    lld: Path | None = None,
    mksquashfs: Path | None = None,
    unsquashfs: Path | None = None,
) -> dict[str, object]:
    if output.exists() or output.is_symlink():
        raise RecoveryApBuildError("refusing to overwrite recovery-AP root")
    try:
        validate_mmc_module(mmc_module)
    except ValueError as exc:
        raise RecoveryApBuildError(str(exc)) from exc
    if not wifi_module.startswith(b"\x7fELF") or b"8188fu" not in wifi_module:
        raise RecoveryApBuildError("RTL8188FU module identity is invalid")
    if not dtrng_module.startswith(b"\x7fELF") or b"dcs6100-dtrng" not in dtrng_module:
        raise RecoveryApBuildError("T31 DTRNG module identity is invalid")
    if not entropy_seed.startswith(b"\x7fELF") or b"/dev/dtrng" not in entropy_seed:
        raise RecoveryApBuildError("entropy loader identity is invalid")
    for source in (INIT, RECOVERYCTL, MDNS_SERVICE):
        raw = source.read_bytes()
        for forbidden in (
            b"flash_erase",
            b"MEMERASE",
        ):
            if forbidden in raw:
                raise RecoveryApBuildError(
                    "recovery-AP source gained a write path outside physical mtd3"
                )
    session_files: dict[str, bytes] = {}
    if session_media_dir is not None:
        if session_media_dir.is_symlink() or not session_media_dir.is_dir():
            raise RecoveryApBuildError("recovery session media directory is invalid")
        if {path.name for path in session_media_dir.iterdir()} != {
            "AP.PSK",
            "AUTHORIZED.KEY",
            "HOST.KEY",
        }:
            raise RecoveryApBuildError("recovery session media file set is invalid")
        limits = {"AP.PSK": 65, "AUTHORIZED.KEY": 2048, "HOST.KEY": 4096}
        for name, limit in limits.items():
            path = session_media_dir / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
                raise RecoveryApBuildError("recovery session media file is invalid")
            session_files[name] = path.read_bytes()

    squash_tool = _resolve(mksquashfs, "mksquashfs")
    unsquash_tool = _resolve(unsquashfs, "unsquashfs")
    clang_tool, lld_tool = _resolve_llvm_pair(clang, lld)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="dcs6100-recovery-ap-", dir=os.environ.get("TMPDIR")
    ) as name:
        workspace = Path(name)
        target = workspace / "target"
        root = workspace / "root"
        squashfs = workspace / "recovery-ap.squashfs"
        target.mkdir()
        _extract_target(target_archive, target)

        thingino_enter = _build_thingino_enter(
            workspace=workspace, clang=clang_tool, lld=lld_tool
        )

        for relative in (
            "dev",
            "media/setup",
            "mnt/thingino",
            "modules",
            "proc",
            "root/.ssh",
            "run",
            "sys",
            "tmp",
            "usr/bin",
            "usr/lib/firmware",
            "usr/sbin",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "tmp").chmod(0o1777)
        (root / "lib").symlink_to("usr/lib")

        selected = {
            "usr/bin/busybox": 0o755,
            "usr/sbin/dropbear": 0o755,
            "usr/sbin/flashcp": 0o755,
            "usr/sbin/hostapd": 0o755,
            "usr/sbin/mdnsd": 0o755,
            "usr/sbin/wpa_supplicant": 0o755,
            "usr/lib/firmware/PHY_REG_PG.txt": 0o644,
        }
        for relative, mode in selected.items():
            _copy(target / relative, root / relative, mode)
        for library in sorted((target / "usr/lib").glob("*.so*")):
            destination = root / "usr/lib" / library.name
            if library.is_symlink():
                link = os.readlink(library)
                if "/" in link or link in {"", ".", ".."}:
                    raise RecoveryApBuildError("runtime library symlink is unsafe")
                destination.symlink_to(link)
            elif library.is_file():
                _copy(library, destination, 0o755)

        _copy(INIT, root / "sbin/init", 0o755)
        _copy(RECOVERYCTL, root / "usr/sbin/recoveryctl", 0o755)
        _copy(MDNS_SERVICE, root / "etc/mdns.d/recovery.service", 0o644)
        if session_files:
            session_root = root / "etc/recovery-session"
            session_root.mkdir(mode=0o700)
            for name, raw in session_files.items():
                (session_root / name).write_bytes(raw)
                (session_root / name).chmod(0o600)
        (root / "modules/8188fu.ko").write_bytes(wifi_module)
        (root / "modules/8188fu.ko").chmod(0o400)
        (root / "modules/jzmmc_v12.ko").write_bytes(mmc_module)
        (root / "modules/jzmmc_v12.ko").chmod(0o400)
        (root / "modules/ingenic_t31_dtrng.ko").write_bytes(dtrng_module)
        (root / "modules/ingenic_t31_dtrng.ko").chmod(0o400)
        (root / "usr/sbin/entropy-seed").write_bytes(entropy_seed)
        (root / "usr/sbin/entropy-seed").chmod(0o500)
        (root / "usr/sbin/thingino-enter").write_bytes(thingino_enter)
        (root / "usr/sbin/thingino-enter").chmod(0o500)
        (root / "linuxrc").symlink_to("sbin/init")
        (root / "var").mkdir()
        (root / "var/run").symlink_to("../run")
        for command in (
            "cat",
            "chmod",
            "cp",
            "cut",
            "dd",
            "grep",
            "head",
            "hexdump",
            "hostname",
            "killall",
            "mkdir",
            "mount",
            "mv",
            "readlink",
            "rm",
            "reboot",
            "sed",
            "sh",
            "sleep",
            "sync",
            "sha256sum",
            "tail",
            "tar",
            "tr",
            "umount",
            "wc",
        ):
            (root / "bin").mkdir(exist_ok=True)
            (root / "bin" / command).symlink_to("../usr/bin/busybox")
        for command in ("ifconfig", "insmod", "udhcpc", "udhcpd"):
            (root / "sbin").mkdir(exist_ok=True)
            (root / "sbin" / command).symlink_to("../usr/bin/busybox")
        (root / "usr/bin/dropbearkey").symlink_to("../sbin/dropbear")

        (root / "etc").mkdir(exist_ok=True)
        (root / "etc/passwd").write_text(
            "root:x:0:0:root:/root:/bin/sh\n", encoding="ascii"
        )
        (root / "etc/group").write_text("root:x:0:\n", encoding="ascii")
        (root / "etc/shells").write_text("/bin/sh\n", encoding="ascii")

        _run(
            [
                str(squash_tool),
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
            "recovery-AP SquashFS build",
        )
        listing = _run(
            [str(unsquash_tool), "-lln", str(squashfs)],
            "recovery-AP SquashFS inventory",
        ).decode("utf-8")
        for required in (
            "lib -> usr/lib",
            "etc/mdns.d/recovery.service",
            "modules/8188fu.ko",
            "modules/ingenic_t31_dtrng.ko",
            "bin/readlink",
            "sbin/init",
            "usr/sbin/entropy-seed",
            "usr/sbin/hostapd",
            "usr/sbin/mdnsd",
            "usr/sbin/wpa_supplicant",
            "usr/sbin/dropbear",
            "usr/sbin/flashcp",
            "usr/sbin/recoveryctl",
            "usr/sbin/thingino-enter",
        ):
            if required not in listing:
                raise RecoveryApBuildError(f"recovery-AP root lost {required}")
        raw = squashfs.read_bytes()
        validate_squashfs(raw, partition_limit=MAX_ROOT_SIZE)
        temporary = output.with_name(f".{output.name}.new")
        if temporary.exists() or temporary.is_symlink():
            raise RecoveryApBuildError("recovery-AP temporary output exists")
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, output)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
    return {
        "nor_writes": False,
        "output_sha256": hashlib.sha256(raw).hexdigest(),
        "output_size": len(raw),
        "root_storage": "mtd2" if session_files else "ram-sd-fallback",
        "session_storage": "embedded-mtd2" if session_files else "sd-fallback",
        "user_transport": "wpa2-setup-ap-and-key-only-ssh",
    }
