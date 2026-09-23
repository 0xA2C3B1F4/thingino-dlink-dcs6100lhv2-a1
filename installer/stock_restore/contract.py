"""Shared stock-restore set types, filenames and transaction status bytes."""

from __future__ import annotations

from dataclasses import dataclass

from installer.fake_mtd import StockRestoreImages
from installer.live_ram import LiveRamPlan


PAYLOAD_NAMES = {
    0: "KEEP0.BIN",
    1: "STOCK1.BIN",
    2: "STOCK2.BIN",
    3: "STOCK3.BIN",
    4: "KEEP4.BIN",
    5: "KEEP5.BIN",
}
AUTH_PRIVATE_NAME = "authorization.private"
AUTH_SD_NAME = "RESTORE.GO"
RUN_SD_NAME = "RESTORE.RUN"
COMPLETE_SD_NAME = "RESTORE.OK"
MANIFEST_NAME = "live-restore.private.json"
PASSIVE_DIR_NAME = "RESTORE.PSV"
PASSIVE_MARKER_NAME = "PASSIVE.OK"
RUN_STATUS = b"write_set=mtd3,mtd2,mtd1;activation=last\n"
COMPLETE_STATUS = b"physical_readback_verified=true;write_set=mtd3,mtd2,mtd1\n"
PASSIVE_STATUS = b"stock_restore_passivated=true;nor_writes=false\n"
BOOTSTRAP_PRIVATE_NAME = "STOCK-RESTORE-BOOTSTRAP.PKG"
BOOTSTRAP_ACTIVE_NAME = "DCS6100LHV2Ax_FW999R00_STOCK_RESTORE_SD.bin"
BOOTSTRAP_MANIFEST_NAME = "uartless-stock-restore.private.json"


class StockRestoreSetError(ValueError):
    """A private live-restore set or its SD activation is unsafe."""


@dataclass(frozen=True, slots=True)
class StockRestoreRamSet:
    live_plan: LiveRamPlan
    images: StockRestoreImages
    authorization: bytes
    files: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class StockRestorePassivation:
    already_passivated: bool
    moved_files: tuple[str, ...]
    preserved_unrelated_entries: int


@dataclass(frozen=True, slots=True)
class StockRestoreBootstrapSet:
    images: StockRestoreImages
    authorization: bytes
    files: dict[str, bytes]
