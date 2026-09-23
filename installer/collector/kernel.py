"""Exact read-only kernel contract for the RAM collector."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from installer.artifacts import (
    ArtifactError,
    UImageInfo,
    validate_uimage,
    validate_uimage_command_line,
)
from installer.layout import TARGET


COLLECTOR_FRAGMENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "profiles/dlink-dcs6100lhv2-a1/collector-kernel.fragment"
)


class CollectorKernelError(ValueError):
    """The collector kernel or effective configuration violates its contract."""


@dataclass(frozen=True, slots=True)
class CollectorKernel:
    image: UImageInfo
    command_line: str | None = None


def _config_values(raw: bytes) -> dict[str, str]:
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CollectorKernelError("effective collector Linux config is not ASCII") from exc
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
            raise CollectorKernelError(f"duplicate effective collector config key: {key}")
        values[key] = value
    return values


def validate_collector_kernel_config(raw: bytes) -> None:
    values = _config_values(raw)
    required = {
        "CONFIG_BINFMT_ELF": "y",
        "CONFIG_BLK_DEV_INITRD": "y",
        "CONFIG_BLK_DEV_RAM": "y",
        "CONFIG_BLK_DEV_RAM_COUNT": "1",
        "CONFIG_BLK_DEV_RAM_SIZE": "8192",
        "CONFIG_CMDLINE_BOOL": "n",
        "CONFIG_DEVTMPFS": "y",
        "CONFIG_DEVTMPFS_MOUNT": "y",
        "CONFIG_FAT_FS": "y",
        "CONFIG_JFFS2_FS": "y",
        "CONFIG_JZMMC_V12": "m",
        "CONFIG_MMC": "y",
        "CONFIG_MMC_BLOCK": "y",
        "CONFIG_MODULES": "y",
        "CONFIG_MTD": "y",
        "CONFIG_MTD_BLOCK": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_MTD_JZ_SFC": "y",
        "CONFIG_MTD_JZ_SFC_NOR": "y",
        "CONFIG_INET": "n",
        "CONFIG_NET": "y",
        "CONFIG_NETDEVICES": "n",
        "CONFIG_NLS_UTF8": "y",
        "CONFIG_SQUASHFS": "y",
        "CONFIG_SQUASHFS_XZ": "y",
        "CONFIG_VFAT_FS": "y",
        "CONFIG_WIRELESS": "n",
    }
    for key, expected in required.items():
        if values.get(key) != expected:
            raise CollectorKernelError(
                f"effective collector Linux config does not enforce {key}={expected}"
            )
    if values.get("CONFIG_CMDLINE_OVERRIDE") not in (None, "n"):
        raise CollectorKernelError(
            "effective collector Linux config enables CONFIG_CMDLINE_OVERRIDE"
        )
    if values.get("CONFIG_CMDLINE") not in (None, '""'):
        raise CollectorKernelError(
            "effective collector Linux config embeds a command line"
        )


def uartless_collector_kernel_command_line() -> str:
    """Return the fixed mtd2-boot command line for functional UARTless capture."""

    partitions = ",".join(
        f"{partition.size // 1024}k({partition.name})ro"
        for partition in TARGET.partitions
    )
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=42M@0x0",
            "rmem=22M@0x2a00000",
            "init=/sbin/init",
            "root=/dev/mtdblock2",
            "rootfstype=squashfs",
            "ro",
            "panic=10",
            f"mtdparts=jz_sfc:{partitions}",
        )
    )


def validate_uartless_collector_kernel_config(raw: bytes) -> None:
    values = _config_values(raw)
    command_line = uartless_collector_kernel_command_line()
    required = {
        "CONFIG_BINFMT_ELF": "y",
        "CONFIG_CMDLINE": f'"{command_line}"',
        "CONFIG_CMDLINE_BOOL": "y",
        "CONFIG_CMDLINE_OVERRIDE": "y",
        "CONFIG_DEVTMPFS": "y",
        "CONFIG_DEVTMPFS_MOUNT": "y",
        "CONFIG_FAT_FS": "y",
        "CONFIG_JFFS2_FS": "y",
        "CONFIG_JZMMC_V12": "m",
        "CONFIG_MMC": "y",
        "CONFIG_MMC_BLOCK": "y",
        "CONFIG_MODULES": "y",
        "CONFIG_MTD": "y",
        "CONFIG_MTD_BLOCK": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_MTD_JZ_SFC": "y",
        "CONFIG_MTD_JZ_SFC_NOR": "y",
        "CONFIG_INET": "n",
        "CONFIG_NET": "y",
        "CONFIG_NETDEVICES": "n",
        "CONFIG_NLS_UTF8": "y",
        "CONFIG_SQUASHFS": "y",
        "CONFIG_SQUASHFS_XZ": "y",
        "CONFIG_VFAT_FS": "y",
        "CONFIG_WIRELESS": "n",
    }
    for key, expected in required.items():
        if values.get(key) != expected:
            raise CollectorKernelError(
                f"effective UARTless collector config does not enforce {key}={expected}"
            )


def render_collector_kernel_fragment() -> bytes:
    fragment = COLLECTOR_FRAGMENT_PATH.read_bytes()
    validate_collector_kernel_config(fragment)
    return fragment


def render_uartless_collector_kernel_fragment() -> bytes:
    fragment = render_collector_kernel_fragment().decode("ascii")
    marker = "# CONFIG_CMDLINE_BOOL is not set\n"
    if fragment.count(marker) != 1:
        raise CollectorKernelError("collector fragment command-line marker differs")
    command_line = uartless_collector_kernel_command_line()
    rendered = fragment.replace(
        marker,
        "CONFIG_CMDLINE_BOOL=y\n"
        f'CONFIG_CMDLINE="{command_line}"\n'
        "CONFIG_CMDLINE_OVERRIDE=y\n",
    ).encode("ascii")
    validate_uartless_collector_kernel_config(rendered)
    return rendered


def validate_collector_kernel(*, kernel: bytes, linux_config: bytes) -> CollectorKernel:
    validate_collector_kernel_config(linux_config)
    try:
        image = validate_uimage(
            kernel,
            partition_limit=TARGET.partition(1).size,
            expected_entry=None,
        )
    except ArtifactError as exc:
        raise CollectorKernelError(str(exc)) from exc
    return CollectorKernel(image=image)


def validate_uartless_collector_kernel(
    *, kernel: bytes, linux_config: bytes
) -> CollectorKernel:
    validate_uartless_collector_kernel_config(linux_config)
    command_line = uartless_collector_kernel_command_line()
    try:
        image = validate_uimage_command_line(
            kernel,
            command_line,
            partition_limit=TARGET.partition(1).size,
            expected_entry=None,
        )
    except ArtifactError as exc:
        raise CollectorKernelError(str(exc)) from exc
    return CollectorKernel(image=image, command_line=command_line)
