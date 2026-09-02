"""Fixed installer state machine shared by host tests and stage-1 design."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


STATES = (
    "stock",
    "bootstrap_package_ready",
    "bootstrap_written_to_sd",
    "bootstrap_flash_started",
    "bootstrap_flash_complete",
    "installer_booted",
    "device_verified",
    "stock_backup_complete",
    "stock_backup_verified",
    "recovery_checkpoint_verified",
    "final_bundle_verified",
    "ram_resident_install_mode",
    "final_data_written",
    "final_rootfs_written",
    "final_kernel_written",
    "activation_written",
    "final_readback_verified",
    "thingino_booted",
)

STOCK_RESTORE_STATES = (
    "backup_validated",
    "same_device_verified",
    "restore_images_validated",
    "restore_prepared",
    "mtd3_written",
    "mtd3_readback_verified",
    "mtd2_written",
    "mtd2_readback_verified",
    "mtd1_tail_written",
    "mtd1_tail_readback_verified",
    "stock_activation_written",
    "stock_activation_readback_verified",
    "stock_full_readback_verified",
    "stock_restore_complete",
)

_NEXT = {current: following for current, following in zip(STATES, STATES[1:])}
_STOCK_RESTORE_NEXT = {
    current: following
    for current, following in zip(STOCK_RESTORE_STATES, STOCK_RESTORE_STATES[1:])
}
_FAILURE_CODE = re.compile(r"^[a-z0-9_]+$")


class StateError(ValueError):
    """An invalid or unsafe installer transition was requested."""


@dataclass(slots=True)
class InstallerStateMachine:
    state: str = "stock"
    history: list[str] = field(default_factory=lambda: ["stock"])

    def transition(self, next_state: str) -> None:
        if self.state.startswith("failed_"):
            raise StateError("failed installer state is terminal")
        expected = _NEXT.get(self.state)
        if next_state != expected:
            raise StateError(
                f"invalid transition {self.state!r} -> {next_state!r}; "
                f"expected {expected!r}"
            )
        self.state = next_state
        self.history.append(next_state)

    def fail(self, fixed_code: str) -> None:
        if self.state.startswith("failed_"):
            raise StateError("failed installer state is terminal")
        if not _FAILURE_CODE.fullmatch(fixed_code):
            raise StateError("failure code must be a fixed lowercase identifier")
        self.state = f"failed_{fixed_code}"
        self.history.append(self.state)


@dataclass(slots=True)
class StockRestoreStateMachine:
    """Host/fake-NOR stock restore states; no state performs live I/O itself."""

    state: str = "backup_validated"
    history: list[str] = field(default_factory=lambda: ["backup_validated"])

    def transition(self, next_state: str) -> None:
        if self.state.startswith("failed_"):
            raise StateError("failed stock restore state is terminal")
        expected = _STOCK_RESTORE_NEXT.get(self.state)
        if next_state != expected:
            raise StateError(
                f"invalid stock restore transition {self.state!r} -> "
                f"{next_state!r}; expected {expected!r}"
            )
        self.state = next_state
        self.history.append(next_state)

    def fail(self, fixed_code: str) -> None:
        if self.state.startswith("failed_"):
            raise StateError("failed stock restore state is terminal")
        if not _FAILURE_CODE.fullmatch(fixed_code):
            raise StateError("failure code must be a fixed lowercase identifier")
        self.state = f"failed_{fixed_code}"
        self.history.append(self.state)
