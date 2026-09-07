"""Single authoritative flash-layout contract for the supported camera."""

from __future__ import annotations

from dataclasses import dataclass


ERASE_BLOCK_SIZE = 0x10000
# The project image formats align records to 64 KiB, but the XM25QH128C MTD
# driver reports the actual physical erase size as 32 KiB.
MTD_PHYSICAL_ERASE_SIZE = 0x8000
MTD_WRITE_SIZE = 0x100
NOR_SIZE = 0x1000000


@dataclass(frozen=True, slots=True)
class Partition:
    name: str
    mtd: int
    offset: int
    size: int
    always_preserve: bool = False

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True, slots=True)
class Target:
    model: str
    hardware_revision: str
    nor_size: int
    erase_block_size: int
    partitions: tuple[Partition, ...]

    def partition(self, mtd: int) -> Partition:
        for partition in self.partitions:
            if partition.mtd == mtd:
                return partition
        raise KeyError(f"unknown MTD index: {mtd}")


TARGET = Target(
    model="DCS-6100LHV2",
    hardware_revision="A1",
    nor_size=NOR_SIZE,
    erase_block_size=ERASE_BLOCK_SIZE,
    partitions=(
        Partition("boot", 0, 0x000000, 0x040000, always_preserve=True),
        Partition("kernel", 1, 0x040000, 0x1C0000),
        Partition("rootfs", 2, 0x200000, 0x480000),
        Partition("userdata", 3, 0x680000, 0x7C0000),
        Partition("userdata2", 4, 0xE40000, 0x180000, always_preserve=True),
        Partition("userdata3", 5, 0xFC0000, 0x040000, always_preserve=True),
    ),
)


def align_up(value: int, alignment: int = ERASE_BLOCK_SIZE) -> int:
    if value < 0 or alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")
    return (value + alignment - 1) & ~(alignment - 1)


def validate_target() -> None:
    cursor = 0
    for expected_mtd, partition in enumerate(TARGET.partitions):
        if partition.mtd != expected_mtd:
            raise ValueError("MTD indexes are not contiguous")
        if partition.offset != cursor:
            raise ValueError("partition map has a gap or overlap")
        if partition.offset % ERASE_BLOCK_SIZE or partition.size % ERASE_BLOCK_SIZE:
            raise ValueError("partition is not erase-block aligned")
        cursor = partition.end
    if cursor != TARGET.nor_size:
        raise ValueError("partition map does not cover exactly 16 MiB")


validate_target()
