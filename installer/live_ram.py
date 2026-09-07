"""Bounded UART/SD RAM boot for the DCS-6100LHV2 A1.

The host stages two fixed, non-stock-matching filenames on an already
preflighted FAT32 card.  U-Boot commands are derived only from the validated
manifest; callers cannot supply an address, filename, or command.  All U-Boot
environment changes remain volatile.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Protocol

from .artifacts import ArtifactError, UImageInfo, validate_uimage
from .collector.kernel import CollectorKernelError, validate_collector_kernel
from .media import MediaPreflight, validate_sd_root
from .ram_boot import RamBootError, parse_ramdisk_uimage


KERNEL_ADDRESS = 0x80600000
WRAPPER_ADDRESS = 0x80FFFFC0
RAMDISK_PAYLOAD_ADDRESS = WRAPPER_ADDRESS + 64
STACK_FLOOR = 0x81F00000
STACK_GUARD = 0x100000
KERNEL_SD_NAME = "T4RAMK.UIM"
MODE_WRAPPERS = {
    "collector": "T4COLL.UIM",
    "stock-restore": "T4RSTR.UIM",
}
MODE_COMPLETION_MARKERS = {
    "collector": b"COLLECT COMPLETE host_validation_required",
    "stock-restore": b"RESTORE COMPLETE physical_readback_verified",
}
MODE_FAILURE_MARKERS = {
    "collector": b"COLLECT FAIL",
    "stock-restore": b"RESTORE FAIL",
}
MANIFEST_NAME = "ram-boot.private.json"
UBOOT_PROMPT = b"isvp_t31# "
DCS_UBOOT_BANNER = b"U-Boot 2013.07 (Jan 20 2022 - 20:05:09)"
DCS_AUTOBOOT_PROMPT = b"Hit Ctrl+c key to stop autoboot:"
FAILURE_MARKERS = (
    b"Bad Data CRC",
    b"Bad Header Checksum",
    b"Kernel panic",
    b"Oops:",
    b"Call Trace:",
)
MAX_TRANSCRIPT = 512 * 1024


class LiveRamError(ValueError):
    """A live RAM artifact, media target, or UART transcript is unsafe."""


@dataclass(frozen=True, slots=True)
class LiveRamPlan:
    mode: str
    kernel: bytes
    wrapper: bytes
    bootargs: str
    kernel_info: UImageInfo
    completion_marker: bytes
    write_set: tuple[int, ...]

    @property
    def wrapper_name(self) -> str:
        return MODE_WRAPPERS[self.mode]

    @property
    def kernel_crc32(self) -> str:
        return f"{binascii.crc32(self.kernel) & 0xFFFFFFFF:08x}"

    @property
    def wrapper_crc32(self) -> str:
        return f"{binascii.crc32(self.wrapper) & 0xFFFFFFFF:08x}"


class PromptIO(Protocol):
    def command(self, command: str, *, dangerous: bool = False) -> bytes: ...

    def final_boot(self, command: str, *, dangerous: bool = False) -> bytes: ...


def _config_values(raw: bytes) -> dict[str, str]:
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LiveRamError("RAM kernel config is not ASCII") from exc
    values: dict[str, str] = {}
    for line in source.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            key = line[2 : -len(" is not set")]
            value = "n"
        else:
            continue
        if key in values:
            raise LiveRamError(f"duplicate RAM kernel config key: {key}")
        values[key] = value
    return values


def validate_external_ram_kernel(kernel: bytes, linux_config: bytes) -> UImageInfo:
    """Accept a Linux/MIPS kernel whose command line comes only from U-Boot."""

    values = _config_values(linux_config)
    required = {
        "CONFIG_BLK_DEV_INITRD": "y",
        "CONFIG_BLK_DEV_RAM": "y",
        "CONFIG_BLK_DEV_RAM_SIZE": "8192",
        "CONFIG_CMDLINE_BOOL": "n",
        "CONFIG_DEVTMPFS": "y",
        "CONFIG_DEVTMPFS_MOUNT": "y",
        "CONFIG_FAT_FS": "y",
        "CONFIG_JZMMC_V12": "m",
        "CONFIG_MMC": "y",
        "CONFIG_MMC_BLOCK": "y",
        "CONFIG_MODULES": "y",
        "CONFIG_MTD": "y",
        "CONFIG_MTD_BLOCK": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_MTD_JZ_SFC": "y",
        "CONFIG_MTD_JZ_SFC_NOR": "y",
        "CONFIG_SQUASHFS": "y",
        "CONFIG_SQUASHFS_XZ": "y",
        "CONFIG_VFAT_FS": "y",
    }
    for key, expected in required.items():
        if values.get(key) != expected:
            raise LiveRamError(
                f"RAM kernel config does not enforce {key}={expected}"
            )
    try:
        return validate_uimage(kernel, partition_limit=0x001C0000)
    except ArtifactError as exc:
        raise LiveRamError(str(exc)) from exc


def _partition_argument(mode: str) -> str:
    suffixes = (
        "256K(boot)ro",
        "1792K(kernel)ro" if mode == "collector" else "1792K(kernel)",
        "4608K(rootfs)ro" if mode == "collector" else "4608K(rootfs)",
        "7936K(userdata)ro" if mode == "collector" else "7936K(userdata)",
        "1536K(userdata2)ro",
        "256K(userdata3)ro",
    )
    return "mtdparts=jz_sfc:" + ",".join(suffixes)


def ram_bootargs(mode: str, rootfs_size: int) -> str:
    if mode not in MODE_WRAPPERS:
        raise LiveRamError("unsupported RAM boot mode")
    if rootfs_size <= 0 or rootfs_size > 8 * 1024 * 1024 or rootfs_size % 4096:
        raise LiveRamError("RAM root size is outside the exact page-aligned limit")
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=39M@0x0",
            "rmem=25M@0x2700000",
            f"rd_start=0x{RAMDISK_PAYLOAD_ADDRESS:08x}",
            f"rd_size=0x{rootfs_size:x}",
            "root=/dev/ram0",
            "rootfstype=squashfs",
            "ro",
            "init=/sbin/init",
            "panic=3",
            _partition_argument(mode),
        )
    )


def build_live_ram_plan(
    *, mode: str, kernel: bytes, linux_config: bytes, wrapper: bytes
) -> LiveRamPlan:
    if mode not in MODE_WRAPPERS:
        raise LiveRamError("unsupported RAM boot mode")
    if mode == "collector":
        try:
            collector = validate_collector_kernel(
                kernel=kernel, linux_config=linux_config
            )
        except CollectorKernelError as exc:
            raise LiveRamError(str(exc)) from exc
        info = collector.image
    else:
        info = validate_external_ram_kernel(kernel, linux_config)
    try:
        rootfs = parse_ramdisk_uimage(wrapper)
    except RamBootError as exc:
        raise LiveRamError(str(exc)) from exc
    if KERNEL_ADDRESS + len(kernel) > WRAPPER_ADDRESS:
        raise LiveRamError("RAM kernel overlaps the wrapper staging address")
    if WRAPPER_ADDRESS + len(wrapper) + STACK_GUARD > STACK_FLOOR:
        raise LiveRamError("RAM wrapper violates the U-Boot stack guard")
    write_set = () if mode == "collector" else (3, 2, 1)
    return LiveRamPlan(
        mode=mode,
        kernel=kernel,
        wrapper=wrapper,
        bootargs=ram_bootargs(mode, len(rootfs)),
        kernel_info=info,
        completion_marker=MODE_COMPLETION_MARKERS[mode],
        write_set=write_set,
    )


def _identity(raw: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _write_exclusive(path: Path, raw: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            amount = os.write(descriptor, view)
            if amount <= 0:
                raise LiveRamError("short private RAM artifact write")
            view = view[amount:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_live_ram_set(
    *,
    mode: str,
    kernel: bytes,
    linux_config: bytes,
    wrapper: bytes,
    output_dir: Path,
) -> LiveRamPlan:
    plan = build_live_ram_plan(
        mode=mode, kernel=kernel, linux_config=linux_config, wrapper=wrapper
    )
    if output_dir.exists() or output_dir.is_symlink():
        raise LiveRamError("refusing to overwrite a RAM boot set")
    output_dir.mkdir(mode=0o700, parents=False)
    _write_exclusive(output_dir / KERNEL_SD_NAME, kernel, 0o400)
    _write_exclusive(output_dir / plan.wrapper_name, wrapper, 0o400)
    manifest = {
        "schema_version": 1,
        "purpose": "bounded-dcs6100lhv2-a1-live-ram-boot",
        "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
        "mode": mode,
        "addresses": {
            "kernel": KERNEL_ADDRESS,
            "ramdisk_payload": RAMDISK_PAYLOAD_ADDRESS,
            "wrapper": WRAPPER_ADDRESS,
        },
        "artifacts": {
            KERNEL_SD_NAME: _identity(kernel),
            plan.wrapper_name: _identity(wrapper),
        },
        "bootargs": plan.bootargs,
        "volatile_uboot_environment": True,
        "write_set": list(plan.write_set),
        "protected_mtd": [0, 4, 5],
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    _write_exclusive(output_dir / MANIFEST_NAME, raw, 0o600)
    for name, expected in (
        (KERNEL_SD_NAME, kernel),
        (plan.wrapper_name, wrapper),
        (MANIFEST_NAME, raw),
    ):
        if (output_dir / name).read_bytes() != expected:
            raise LiveRamError("RAM boot set storage readback differs")
    return plan


def _read_regular(path: Path, expected_size: int, label: str) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != expected_size
        ):
            raise LiveRamError(f"{label} is not one exact regular file")
        raw = os.read(descriptor, expected_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != expected_size
    ):
        raise LiveRamError(f"{label} changed while read")
    return raw


def inspect_live_ram_set(
    *, output_dir: Path, linux_config: bytes
) -> LiveRamPlan:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise LiveRamError("RAM boot set is not a private directory")
    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise LiveRamError("RAM boot manifest is missing")
    raw_manifest = manifest_path.read_bytes()
    if len(raw_manifest) > 128 * 1024:
        raise LiveRamError("RAM boot manifest is too large")
    try:
        manifest = json.loads(raw_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveRamError("RAM boot manifest is invalid") from exc
    mode = manifest.get("mode") if isinstance(manifest, dict) else None
    if mode not in MODE_WRAPPERS:
        raise LiveRamError("RAM boot manifest mode is invalid")
    expected_names = {KERNEL_SD_NAME, MODE_WRAPPERS[mode], MANIFEST_NAME}
    if {entry.name for entry in output_dir.iterdir()} != expected_names:
        raise LiveRamError("RAM boot set file closure differs")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != expected_names - {MANIFEST_NAME}:
        raise LiveRamError("RAM boot manifest artifact closure differs")
    snapshots: dict[str, bytes] = {}
    for name in expected_names - {MANIFEST_NAME}:
        record = artifacts.get(name)
        if not isinstance(record, dict) or not isinstance(record.get("size"), int):
            raise LiveRamError("RAM boot artifact record is invalid")
        item = _read_regular(output_dir / name, record["size"], name)
        if hashlib.sha256(item).hexdigest() != record.get("sha256"):
            raise LiveRamError("RAM boot artifact identity differs")
        snapshots[name] = item
    plan = build_live_ram_plan(
        mode=mode,
        kernel=snapshots[KERNEL_SD_NAME],
        linux_config=linux_config,
        wrapper=snapshots[MODE_WRAPPERS[mode]],
    )
    if (
        manifest.get("purpose") != "bounded-dcs6100lhv2-a1-live-ram-boot"
        or manifest.get("bootargs") != plan.bootargs
        or manifest.get("write_set") != list(plan.write_set)
        or manifest.get("protected_mtd") != [0, 4, 5]
        or manifest.get("volatile_uboot_environment") is not True
    ):
        raise LiveRamError("RAM boot manifest safety contract differs")
    return plan


def stage_live_ram_set(
    *,
    plan: LiveRamPlan,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> tuple[str, ...]:
    if confirmed_physical_device != preflight.physical_device:
        raise LiveRamError("exact SD device confirmation differs")
    if root.resolve(strict=True) != preflight.mount_root:
        raise LiveRamError("RAM staging root changed after preflight")
    validate_sd_root(root)
    artifacts = {
        KERNEL_SD_NAME: plan.kernel,
        plan.wrapper_name: plan.wrapper,
    }
    for name in artifacts:
        destination = root / name
        temporary = root / ("." + name + ".part")
        sidecar = root / ("._" + name)
        if any(
            candidate.exists() or candidate.is_symlink()
            for candidate in (destination, temporary, sidecar)
        ):
            raise LiveRamError("RAM staging refuses an existing reserved path")
    staged: list[Path] = []
    try:
        for name, raw in artifacts.items():
            temporary = root / ("." + name + ".part")
            destination = root / name
            _write_exclusive(temporary, raw)
            if temporary.read_bytes() != raw:
                raise LiveRamError("RAM staging temporary readback differs")
            os.replace(temporary, destination)
            staged.append(destination)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        for name, raw in artifacts.items():
            destination = root / name
            if (
                destination.is_symlink()
                or not destination.is_file()
                or destination.read_bytes() != raw
            ):
                raise LiveRamError("RAM staging activated readback differs")
    except BaseException:
        for destination in staged:
            destination.unlink(missing_ok=True)
        for name in artifacts:
            (root / ("." + name + ".part")).unlink(missing_ok=True)
        raise
    return tuple(artifacts)


def unstage_live_ram_set(
    *,
    plan: LiveRamPlan,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> tuple[str, ...]:
    if confirmed_physical_device != preflight.physical_device:
        raise LiveRamError("exact SD device confirmation differs")
    if root.resolve(strict=True) != preflight.mount_root:
        raise LiveRamError("RAM cleanup root changed after preflight")
    expected = {KERNEL_SD_NAME: plan.kernel, plan.wrapper_name: plan.wrapper}
    for name, raw in expected.items():
        item = root / name
        if item.is_symlink() or not item.is_file() or item.read_bytes() != raw:
            raise LiveRamError("RAM cleanup artifact differs from the staged set")
    for name in expected:
        (root / name).unlink()
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return tuple(expected)


def _normalize(output: bytes) -> bytes:
    return output.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _require_clean(output: bytes, mode: str) -> None:
    for marker in (*FAILURE_MARKERS, MODE_FAILURE_MARKERS[mode]):
        if marker in output:
            raise LiveRamError("RAM boot reported a failure marker")


def _prompt_body(output: bytes, command: str, mode: str) -> bytes:
    normalized = _normalize(output)
    prefix = command.encode("ascii") + b"\n"
    if not normalized.startswith(prefix):
        raise LiveRamError("U-Boot command echo differs")
    if not normalized.endswith(UBOOT_PROMPT) or normalized.count(UBOOT_PROMPT) != 1:
        raise LiveRamError("U-Boot prompt correlation differs")
    body = normalized[len(prefix) : -len(UBOOT_PROMPT)]
    _require_clean(body, mode)
    return body


def _require_transfer(body: bytes, expected_size: int, filename: str) -> None:
    lines = [line for line in body.split(b"\n") if line]
    prefixes = {
        f"reading {filename}".encode(),
        f"FAT:   loading {filename}".encode(),
    }
    if len(lines) == 2 and lines[0] in prefixes:
        line = lines[1]
    elif len(lines) == 1:
        line = lines[0]
    else:
        raise LiveRamError("SD transfer transcript differs")
    match = re.fullmatch(
        rb"([0-9]+) bytes read in ([0-9]+) ms(?: \([^\r\n()]+\))?", line
    )
    if match is None or int(match.group(1)) != expected_size:
        raise LiveRamError("SD transfer byte count differs")


def _require_crc(body: bytes, address: int, size: int, expected: str) -> None:
    lines = [line for line in body.split(b"\n") if line]
    wanted = (
        f"CRC32 for {address:08x} ... {address + size - 1:08x} ==> {expected}"
    ).encode()
    if len(lines) != 1 or lines[0].lower() != wanted.lower():
        raise LiveRamError("RAM transfer CRC differs")


def execute_live_ram_plan(io: PromptIO, plan: LiveRamPlan) -> bytes:
    def checked(command: str, *, dangerous: bool = False) -> bytes:
        return _prompt_body(
            io.command(command, dangerous=dangerous), command, plan.mode
        )

    checked("mmc dev 0")
    rescan = checked("mmc rescan")
    if b"no card present" in rescan.lower() or b"mmc init failed" in rescan.lower():
        raise LiveRamError("MMC0 rescan failed")
    command = f"fatload mmc 0:1 0x{KERNEL_ADDRESS:x} {KERNEL_SD_NAME}"
    _require_transfer(checked(command), len(plan.kernel), KERNEL_SD_NAME)
    command = f"crc32 0x{KERNEL_ADDRESS:x} 0x{len(plan.kernel):x}"
    _require_crc(checked(command), KERNEL_ADDRESS, len(plan.kernel), plan.kernel_crc32)
    command = f"fatload mmc 0:1 0x{WRAPPER_ADDRESS:x} {plan.wrapper_name}"
    _require_transfer(checked(command), len(plan.wrapper), plan.wrapper_name)
    command = f"crc32 0x{WRAPPER_ADDRESS:x} 0x{len(plan.wrapper):x}"
    _require_crc(checked(command), WRAPPER_ADDRESS, len(plan.wrapper), plan.wrapper_crc32)
    command = f"bootm start 0x{KERNEL_ADDRESS:x} 0x{WRAPPER_ADDRESS:x}"
    body = checked(command, dangerous=True)
    if body.count(b"Verifying Checksum ... OK") != 2:
        raise LiveRamError("legacy image checksum gate failed")
    command = f"crc32 0x{KERNEL_ADDRESS:x} 0x{len(plan.kernel):x}"
    _require_crc(checked(command), KERNEL_ADDRESS, len(plan.kernel), plan.kernel_crc32)
    command = f"crc32 0x{WRAPPER_ADDRESS:x} 0x{len(plan.wrapper):x}"
    _require_crc(checked(command), WRAPPER_ADDRESS, len(plan.wrapper), plan.wrapper_crc32)
    command = "setenv bootargs " + plan.bootargs
    if checked(command):
        raise LiveRamError("volatile bootargs setter returned unexpected output")
    if checked("printenv bootargs") != ("bootargs=" + plan.bootargs + "\n").encode():
        raise LiveRamError("volatile bootargs readback differs")
    command = f"bootm 0x{KERNEL_ADDRESS:x} 0x{WRAPPER_ADDRESS:x}"
    output = io.final_boot(command, dangerous=True)
    normalized = _normalize(output)
    if not normalized.startswith(command.encode() + b"\n"):
        raise LiveRamError("final U-Boot command echo differs")
    _require_clean(normalized, plan.mode)
    if plan.completion_marker not in normalized:
        raise LiveRamError("RAM boot completion marker was not reached")
    return normalized


class SerialPrompt:
    """Single-process serial owner; it never stores or prints raw UART data."""

    def __init__(self, device: str, mode: str, *, catch_timeout: float = 180.0):
        if not re.fullmatch(r"/dev/(?:cu|tty)\.usbserial-[A-Za-z0-9._-]+", device):
            raise LiveRamError("serial device is not an exact USB UART path")
        if mode not in MODE_WRAPPERS:
            raise LiveRamError("unsupported serial RAM mode")
        self.device = device
        self.mode = mode
        self.catch_timeout = catch_timeout
        self.serial: object | None = None

    def __enter__(self) -> "SerialPrompt":
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - installation boundary
            raise LiveRamError("pyserial is required for live RAM boot") from exc
        self.serial = serial.Serial(
            port=self.device,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=1.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
            exclusive=True,
        )
        self.serial.reset_input_buffer()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def _read_until(self, markers: tuple[bytes, ...], timeout: float) -> bytes:
        assert self.serial is not None
        deadline = time.monotonic() + timeout
        output = bytearray()
        while time.monotonic() < deadline:
            chunk = self.serial.read(4096)
            if chunk:
                output.extend(chunk)
                if len(output) > MAX_TRANSCRIPT:
                    raise LiveRamError("UART transcript exceeded its bound")
                _require_clean(bytes(output), self.mode)
                if any(marker in output for marker in markers):
                    return bytes(output)
        raise LiveRamError("UART phase timed out")

    def catch_fresh_uboot(self) -> None:
        assert self.serial is not None
        profiles = ((DCS_UBOOT_BANNER, DCS_AUTOBOOT_PROMPT),)
        deadline = time.monotonic() + self.catch_timeout
        output = bytearray()
        while time.monotonic() < deadline:
            chunk = self.serial.read(4096)
            if not chunk:
                continue
            output.extend(chunk)
            if len(output) > MAX_TRANSCRIPT:
                raise LiveRamError("fresh U-Boot transcript exceeded its bound")
            for banner, prompt in profiles:
                banner_at = output.find(banner)
                prompt_at = output.find(prompt, banner_at + len(banner)) if banner_at >= 0 else -1
                if prompt_at < 0:
                    continue
                tail = bytes(output[prompt_at + len(prompt) :])
                if len(tail) > 64 or re.fullmatch(rb"[ 0-9\x08\r\n]*", tail) is None:
                    raise LiveRamError("fresh U-Boot interrupt window closed")
                self.serial.write(b"\x03")
                self.serial.flush()
                remainder = self._read_until((UBOOT_PROMPT,), 15.0)
                if UBOOT_PROMPT not in remainder:
                    raise LiveRamError("U-Boot prompt was not reached")
                return
        raise LiveRamError("fresh U-Boot banner was not observed")

    def command(self, command: str, *, dangerous: bool = False) -> bytes:
        del dangerous
        assert self.serial is not None
        self.serial.reset_input_buffer()
        self.serial.write((command + "\n").encode("ascii"))
        self.serial.flush()
        return self._read_until((UBOOT_PROMPT,), 30.0)

    def final_boot(self, command: str, *, dangerous: bool = False) -> bytes:
        if self.mode == "stock-restore" and not dangerous:
            raise LiveRamError("stock restore final boot lacks write authorization")
        assert self.serial is not None
        self.serial.reset_input_buffer()
        self.serial.write((command + "\n").encode("ascii"))
        self.serial.flush()
        return self._read_until((MODE_COMPLETION_MARKERS[self.mode],), 900.0)


def run_live_ram_plan(
    *, plan: LiveRamPlan, serial_device: str, confirmed_serial_device: str
) -> None:
    if serial_device != confirmed_serial_device:
        raise LiveRamError("exact UART device confirmation differs")
    with SerialPrompt(serial_device, plan.mode) as prompt:
        prompt.catch_fresh_uboot()
        execute_live_ram_plan(prompt, plan)
