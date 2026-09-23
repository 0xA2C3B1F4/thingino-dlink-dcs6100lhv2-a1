"""Kernel contract for the bounded stock-restore bootstrap."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from installer.artifacts import ArtifactError, UImageInfo, validate_uimage_command_line
from installer.collector.kernel import _config_values
from installer.layout import TARGET


FRAGMENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "profiles/dlink-dcs6100lhv2-a1/stock-restore-kernel.fragment"
)


class StockRestoreKernelError(ValueError):
    """The stock-restorer kernel violates its fixed flash contract."""


@dataclass(frozen=True, slots=True)
class StockRestoreKernel:
    command_line: str
    image: UImageInfo


def stock_restore_kernel_command_line() -> str:
    partitions = ",".join(
        f"{partition.size // 1024}k({partition.name})"
        f"{'ro' if partition.mtd in {0, 4, 5} else ''}"
        for partition in TARGET.partitions
    )
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=42M@0x0",
            "rmem=22M@0x2a00000",
            "ipv6.disable=1",
            "init=/sbin/init",
            "root=/dev/mtdblock2",
            "rootfstype=squashfs",
            "ro",
            "panic=10",
            f"mtdparts=jz_sfc:{partitions}",
        )
    )


def validate_stock_restore_kernel_config(raw: bytes) -> None:
    values = _config_values(raw)
    required = {
        "CONFIG_BINFMT_ELF": "y",
        "CONFIG_CMDLINE_BOOL": "y",
        "CONFIG_CMDLINE_OVERRIDE": "y",
        "CONFIG_CMDLINE": json.dumps(stock_restore_kernel_command_line()),
        "CONFIG_DEVTMPFS": "y",
        "CONFIG_DEVTMPFS_MOUNT": "y",
        "CONFIG_FAT_FS": "y",
        "CONFIG_INET": "n",
        "CONFIG_JZMMC_V12": "m",
        "CONFIG_MMC": "y",
        "CONFIG_MMC_BLOCK": "y",
        "CONFIG_MODULES": "y",
        "CONFIG_MTD": "y",
        "CONFIG_MTD_BLOCK": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_MTD_JZ_SFC": "y",
        "CONFIG_MTD_JZ_SFC_NOR": "y",
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
            raise StockRestoreKernelError(
                f"effective stock-restore Linux config does not enforce "
                f"{key}={expected}"
            )


def render_stock_restore_kernel_fragment() -> bytes:
    fragment = FRAGMENT_PATH.read_bytes()
    validate_stock_restore_kernel_config(fragment)
    return fragment


def validate_stock_restore_kernel(
    *, kernel: bytes, linux_config: bytes
) -> StockRestoreKernel:
    validate_stock_restore_kernel_config(linux_config)
    command_line = stock_restore_kernel_command_line()
    try:
        image = validate_uimage_command_line(
            kernel,
            command_line,
            partition_limit=TARGET.partition(1).size,
            expected_entry=None,
        )
    except ArtifactError as exc:
        raise StockRestoreKernelError(str(exc)) from exc
    return StockRestoreKernel(command_line=command_line, image=image)
