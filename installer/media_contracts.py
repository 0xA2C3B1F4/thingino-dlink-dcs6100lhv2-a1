"""Fixed media names and explicit validation/IO dependency contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .stage2 import LegacyStage2V1Payload, Stage2Payload

PASSIVE_BOOTSTRAP_FILENAME = "STAGE1.PKG"
PASSIVE_RECOVERY_FILENAME = "RECOVERY.OFF"
UARTLESS_CAPTURE_ACTIVE_FILENAME = "DCS6100LHV2Ax_FW000C00_UARTCAP_SD.bin"
UARTLESS_CAPTURE_PASSIVE_FILENAME = "UARTCAP.PSV"
STOCK_BACKUP_FILENAME = "STOCKM3.BIN"
ARCHIVED_STOCK_BACKUP_FILENAME = "STOCKM3.OLD"
STOCK_BACKUP_SIZE = 0x007C0000
RECOVERY_CHECKPOINT_FILENAME = "STOCKM3.OK"
RECOVERY_CHECKPOINT_SIZE = 80
RECOVERY_CHECKPOINT_MAGIC = b"DCS6RC01"
UNIVERSAL_RECOVERY_CHECKPOINT_SIZE = 144
UNIVERSAL_RECOVERY_CHECKPOINT_MAGIC = b"DCS6RC02"
EVACUATION_MANIFEST_FILENAME = "evacuation.manifest.private.json"
LEGACY_MIGRATION_PROFILE_KIND = "dcs6100lhv2-a1-stage2-v1-to-v2"

DirectorySync = Callable[[Path], None]
WriteTemporary = Callable[[Path, bytes], None]


class ValidateSdRoot(Protocol):
    def __call__(
        self, root: Path, *, allowed_matching_filename: str | None = None
    ) -> None: ...


class ValidateInstallSet(Protocol):
    def __call__(
        self,
        *,
        bootstrap_bytes: bytes,
        stage2_bytes: bytes,
        manifest_bytes: bytes,
        bootstrap_name: str,
    ) -> Stage2Payload: ...


class ValidateRecoveryCheckpoint(Protocol):
    def __call__(
        self,
        root: Path,
        stage2_bytes: bytes,
        *,
        authorization_bytes: bytes | None = None,
        provisioning_bytes: bytes | None = None,
        allow_unverified_universal_bindings: bool = False,
    ) -> None: ...


class ValidateLegacyMigrationProfile(Protocol):
    def __call__(
        self,
        profile_bytes: bytes | None,
        *,
        bootstrap_name: str,
        old_bootstrap_bytes: bytes,
        old_stage2_bytes: bytes,
    ) -> None: ...


@dataclass(frozen=True)
class Stage2Validators:
    """Reviewed current and historical stage-2 formats used for migration."""

    current: Callable[[bytes], Stage2Payload]
    retired_memory: Callable[[bytes], Stage2Payload]
    retired_ipv6: Callable[[bytes], Stage2Payload]
    legacy: Callable[[bytes], LegacyStage2V1Payload]
