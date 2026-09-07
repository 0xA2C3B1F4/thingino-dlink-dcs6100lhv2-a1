"""Fixed physical-mtd3 split shared by every final-install path."""

from __future__ import annotations

from dataclasses import dataclass

from .layout import ERASE_BLOCK_SIZE, TARGET


# This is an on-flash ABI. Moving the boundary during an update would erase or
# reinterpret the persistent JFFS2 upperdir. The 101/23 project erase blocks
# cover the exact 124-block physical mtd3 region.
SYSTEM_ERASE_BLOCKS = 101
DATA_ERASE_BLOCKS = 23
SYSTEM_FLASH_SPAN = SYSTEM_ERASE_BLOCKS * ERASE_BLOCK_SIZE
DATA_FLASH_SPAN = DATA_ERASE_BLOCKS * ERASE_BLOCK_SIZE
MIN_OVERLAY_SIZE = 4 * ERASE_BLOCK_SIZE


class Mtd3SplitError(ValueError):
    """An input violates the fixed physical-mtd3 split contract."""


@dataclass(frozen=True, slots=True)
class FinalLayout:
    system_offset: int
    system_span: int
    data_offset: int
    data_span: int


def derive_final_layout(system_rootfs_size: int) -> FinalLayout:
    """Validate a system payload against the fixed layout and return that ABI."""

    mtd3 = TARGET.partition(3)
    data_offset = mtd3.offset + SYSTEM_FLASH_SPAN
    if system_rootfs_size <= 0 or system_rootfs_size > SYSTEM_FLASH_SPAN:
        raise Mtd3SplitError("system rootfs exceeds the fixed mtd3 system region")
    if SYSTEM_FLASH_SPAN + DATA_FLASH_SPAN != mtd3.size:
        raise AssertionError("fixed system/data split does not cover physical mtd3")
    if DATA_FLASH_SPAN < MIN_OVERLAY_SIZE or data_offset + DATA_FLASH_SPAN != mtd3.end:
        raise AssertionError("fixed JFFS2 data region violates the mtd3 layout")
    return FinalLayout(
        system_offset=mtd3.offset,
        system_span=SYSTEM_FLASH_SPAN,
        data_offset=data_offset,
        data_span=DATA_FLASH_SPAN,
    )


def final_kernel_command_line(layout: FinalLayout) -> str:
    if layout != derive_final_layout(1):
        raise Mtd3SplitError("final kernel command line requires the fixed mtd3 layout")
    return " ".join(
        (
            "console=ttyS1,115200n8",
            "mem=39M@0x0",
            "rmem=25M@0x2700000",
            "init=/sbin/init",
            "root=/dev/mtdblock2",
            "rootfstype=squashfs",
            "ro",
            "panic=10",
            "mtdparts=jz_sfc:256k(boot)ro,1792k(kernel)ro,"
            "4608k(bootstrap)ro,"
            f"{layout.system_span // 1024}k(system)ro,"
            f"{layout.data_span // 1024}k(data),"
            "1536k(vendor)ro,256k(factory)ro",
        )
    )
