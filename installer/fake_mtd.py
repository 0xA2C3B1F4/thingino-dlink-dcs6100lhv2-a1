"""Fake-NOR installer and deterministic power-loss fault injection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from .layout import (
    ERASE_BLOCK_SIZE,
    MTD_PHYSICAL_ERASE_SIZE,
    MTD_WRITE_SIZE,
    NOR_SIZE,
    TARGET,
)
from .mtd3_split import DATA_FLASH_SPAN, SYSTEM_FLASH_SPAN
from .recovery import (
    STOCK_RESTORE_PROTECTED_MTD,
    STOCK_RESTORE_WRITE_SET,
    validate_same_device_stock_images,
)
from .state_machine import (
    InstallerStateMachine,
    StateError,
    StockRestoreStateMachine,
)


class MtdError(ValueError):
    """An MTD operation violates the modeled NOR contract."""


class InjectedPowerLoss(RuntimeError):
    """The harness interrupted execution at one named event."""


@dataclass(slots=True)
class FaultInjector:
    fail_at: str | None = None
    physical_steps: bool = False
    events: list[str] = field(default_factory=list)

    def checkpoint(self, name: str) -> None:
        self.events.append(name)
        if self.fail_at == name:
            raise InjectedPowerLoss(name)


@dataclass(slots=True)
class FakeNor:
    initial: bytes
    fault: FaultInjector = field(default_factory=FaultInjector)
    _contents: bytearray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.initial) != NOR_SIZE:
            raise MtdError("fake NOR must be exactly 16 MiB")
        self._contents = bytearray(self.initial)

    def _range(self, offset: int, length: int) -> slice:
        if offset < 0 or length < 0 or offset + length > NOR_SIZE:
            raise MtdError("MTD range is outside the 16 MiB NOR")
        return slice(offset, offset + length)

    def erase(self, offset: int, length: int, label: str) -> None:
        if (
            offset % MTD_PHYSICAL_ERASE_SIZE
            or length <= 0
            or length % MTD_PHYSICAL_ERASE_SIZE
        ):
            raise MtdError("erase is not aligned to complete physical sectors")
        self._range(offset, length)
        self.fault.checkpoint(f"before_erase_{label}")
        if self.fault.physical_steps:
            for index, sector_offset in enumerate(
                range(offset, offset + length, MTD_PHYSICAL_ERASE_SIZE)
            ):
                self.fault.checkpoint(f"before_erase_{label}_sector_{index}")
                target = self._range(sector_offset, MTD_PHYSICAL_ERASE_SIZE)
                during = f"during_erase_{label}_sector_{index}"
                if self.fault.fail_at == during:
                    torn = MTD_PHYSICAL_ERASE_SIZE // 2
                    self._contents[target.start : target.start + torn] = b"\xff" * torn
                    self.fault.checkpoint(during)
                self._contents[target] = b"\xff" * MTD_PHYSICAL_ERASE_SIZE
                self.fault.checkpoint(f"after_erase_{label}_sector_{index}")
        else:
            target = self._range(offset, length)
            self._contents[target] = b"\xff" * length
        self.fault.checkpoint(f"after_erase_{label}")

    def write(self, offset: int, payload: bytes, label: str) -> None:
        if (
            offset % MTD_WRITE_SIZE
            or not payload
            or len(payload) % MTD_WRITE_SIZE
        ):
            raise MtdError("write is not aligned to complete physical pages")
        self._range(offset, len(payload))
        self.fault.checkpoint(f"before_write_{label}")
        chunks = (
            (
                index,
                offset + page_offset,
                payload[page_offset : page_offset + MTD_WRITE_SIZE],
            )
            for index, page_offset in enumerate(
                range(0, len(payload), MTD_WRITE_SIZE)
            )
        )
        for index, page_offset, page in chunks:
            if self.fault.physical_steps:
                self.fault.checkpoint(f"before_write_{label}_page_{index}")
            target = self._range(page_offset, MTD_WRITE_SIZE)
            existing = self._contents[target]
            for old, new in zip(existing, page):
                if new | old != old:
                    raise MtdError("NOR write attempted a zero-to-one bit transition")
            during = f"during_write_{label}_page_{index}"
            if self.fault.physical_steps and self.fault.fail_at == during:
                torn = MTD_WRITE_SIZE // 2
                self._contents[target.start : target.start + torn] = bytes(
                    old & new
                    for old, new in zip(existing[:torn], page[:torn])
                )
                self.fault.checkpoint(during)
            self._contents[target] = page
            if self.fault.physical_steps:
                self.fault.checkpoint(f"after_write_{label}_page_{index}")
        self.fault.checkpoint(f"after_write_{label}")

    def read(self, offset: int, length: int, label: str) -> bytes:
        target = self._range(offset, length)
        self.fault.checkpoint(f"before_readback_{label}")
        result = bytes(self._contents[target])
        self.fault.checkpoint(f"after_readback_{label}")
        return result

    def snapshot(self) -> bytes:
        return bytes(self._contents)


@dataclass(frozen=True, slots=True)
class FinalBundleImages:
    """Already validated final images bound to exact physical regions."""

    system_offset: int
    system_erase_span: int
    system: bytes
    data_offset: int
    data_erase_span: int
    data: bytes
    rootfs: bytes
    kernel: bytes

    def validate(self) -> None:
        mtd1 = TARGET.partition(1)
        mtd2 = TARGET.partition(2)
        mtd3 = TARGET.partition(3)
        if len(self.kernel) <= ERASE_BLOCK_SIZE or len(self.kernel) > mtd1.size:
            raise MtdError("kernel must include an activation block and fit mtd1")
        if not self.rootfs or len(self.rootfs) > mtd2.size:
            raise MtdError("bootstrap rootfs does not fit mtd2")
        for label, offset, span, payload in (
            ("system", self.system_offset, self.system_erase_span, self.system),
            ("data", self.data_offset, self.data_erase_span, self.data),
        ):
            if (
                offset < mtd3.offset
                or offset % ERASE_BLOCK_SIZE
                or span <= 0
                or span % ERASE_BLOCK_SIZE
                or offset + span > mtd3.end
            ):
                raise MtdError(f"{label} image range is outside aligned mtd3 bounds")
            if label == "system" and not payload:
                raise MtdError("system image is empty")
            if len(payload) > span:
                raise MtdError(f"{label} image does not fit its mtd3 erase span")
        system_end = self.system_offset + self.system_erase_span
        data_end = self.data_offset + self.data_erase_span
        if self.system_offset < data_end and self.data_offset < system_end:
            raise MtdError("system and data image erase ranges overlap")


@dataclass(frozen=True, slots=True)
class OfflineStage2Images:
    """Images consumed after stock U-Boot has installed mtd1/mtd2 stage 1."""

    system_offset: int
    system_erase_span: int
    system: bytes
    data_offset: int
    data_erase_span: int
    kernel: bytes
    data_mode: str = "initialize"

    def validate(self) -> None:
        if self.data_mode not in {"initialize", "preserve", "factory-reset"}:
            raise MtdError("offline stage-2 data mode is invalid")
        mtd3 = TARGET.partition(3)
        if (
            self.system_offset != mtd3.offset
            or self.system_erase_span != SYSTEM_FLASH_SPAN
            or self.data_offset != mtd3.offset + SYSTEM_FLASH_SPAN
            or self.data_erase_span != DATA_FLASH_SPAN
        ):
            raise MtdError("offline stage-2 layout does not match the fixed mtd3 ABI")
        FinalBundleImages(
            system_offset=self.system_offset,
            system_erase_span=self.system_erase_span,
            system=self.system,
            data_offset=self.data_offset,
            data_erase_span=self.data_erase_span,
            data=b"",
            rootfs=b"stage-1-is-preserved",
            kernel=self.kernel,
        ).validate()


@dataclass(frozen=True, slots=True)
class StockRestoreImages:
    """Same-device stock partitions and immutable protected references."""

    mtd1: bytes
    mtd2: bytes
    mtd3: bytes
    protected: tuple[bytes, bytes, bytes]
    write_set: tuple[int, ...] = STOCK_RESTORE_WRITE_SET

    def validate(self) -> None:
        if self.write_set != STOCK_RESTORE_WRITE_SET:
            raise MtdError("stock restore write set must be exactly mtd3,mtd2,mtd1")
        if set(self.write_set) & set(STOCK_RESTORE_PROTECTED_MTD):
            raise MtdError("stock restore write set contains a protected partition")
        if len(self.protected) != len(STOCK_RESTORE_PROTECTED_MTD):
            raise MtdError("stock restore protected reference set is incomplete")
        for raw, mtd in zip(self.protected, STOCK_RESTORE_PROTECTED_MTD):
            if len(raw) != TARGET.partition(mtd).size:
                raise MtdError(f"protected mtd{mtd} reference has the wrong size")
        try:
            validate_same_device_stock_images(self.mtd1, self.mtd2, self.mtd3)
        except ValueError as exc:
            raise MtdError(str(exc)) from exc


def _padded(payload: bytes, span: int) -> bytes:
    if len(payload) > span:
        raise MtdError("payload exceeds erase span")
    return payload + b"\xff" * (span - len(payload))


def _page_padded(payload: bytes) -> bytes:
    if not payload:
        return b""
    size = (len(payload) + MTD_WRITE_SIZE - 1) & ~(MTD_WRITE_SIZE - 1)
    return payload + b"\xff" * (size - len(payload))


def _offline_recovery_checkpoint(
    images: OfflineStage2Images, backup: bytes
) -> bytes:
    stage2_size = len(images.kernel) + len(images.system)
    stage2_digest = hashlib.sha256(images.kernel + images.system).digest()
    return b"DCS6RC01" + b"".join(
        (
            len(backup).to_bytes(4, "big"),
            stage2_size.to_bytes(4, "big"),
            hashlib.sha256(backup).digest(),
            stage2_digest,
        )
    )


class FinalInstaller:
    """Reference write order used by the fake-MTD power-loss matrix.

    The first 64 KiB of the final uImage is the activation block.  It remains
    untouched while data, bootstrap rootfs, and the kernel tail are written.
    Writing it last activates the final kernel without changing stock U-Boot or
    its environment.  An interruption can still require the prepared stage-1
    SD recovery package; this is not represented as atomic installation.
    """

    def __init__(self, nor: FakeNor, state: InstallerStateMachine | None = None):
        self.nor = nor
        self.state = state or InstallerStateMachine(state="installer_booted", history=["installer_booted"])

    def _transition(self, next_state: str) -> None:
        self.nor.fault.checkpoint(f"before_state_{next_state}")
        self.state.transition(next_state)
        self.nor.fault.checkpoint(f"after_state_{next_state}")

    def _write_and_verify(
        self,
        *,
        offset: int,
        erase_span: int,
        payload: bytes,
        label: str,
    ) -> None:
        expected = _padded(payload, erase_span)
        self.nor.erase(offset, erase_span, label)
        write_payload = _page_padded(payload)
        if write_payload:
            self.nor.write(offset, write_payload, label)
        actual = self.nor.read(offset, erase_span, label)
        if not hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest():
            raise MtdError(f"readback mismatch for {label}")

    def run(self, images: FinalBundleImages) -> None:
        images.validate()
        mtd1 = TARGET.partition(1)
        mtd2 = TARGET.partition(2)
        try:
            self._transition("device_verified")
            self._transition("stock_backup_complete")
            self._transition("stock_backup_verified")
            self._transition("recovery_checkpoint_verified")
            self._transition("final_bundle_verified")
            self._transition("ram_resident_install_mode")

            self._write_and_verify(
                offset=images.data_offset,
                erase_span=images.data_erase_span,
                payload=images.data,
                label="final_data",
            )
            self._write_and_verify(
                offset=images.system_offset,
                erase_span=images.system_erase_span,
                payload=images.system,
                label="final_system",
            )
            self._transition("final_data_written")

            self._write_and_verify(
                offset=mtd2.offset,
                erase_span=mtd2.size,
                payload=images.rootfs,
                label="final_rootfs",
            )
            self._transition("final_rootfs_written")

            kernel_tail = images.kernel[ERASE_BLOCK_SIZE:]
            kernel_tail_span = mtd1.size - ERASE_BLOCK_SIZE
            self._write_and_verify(
                offset=mtd1.offset + ERASE_BLOCK_SIZE,
                erase_span=kernel_tail_span,
                payload=kernel_tail,
                label="final_kernel_tail",
            )
            self._transition("final_kernel_written")

            activation = _padded(images.kernel[:ERASE_BLOCK_SIZE], ERASE_BLOCK_SIZE)
            self._write_and_verify(
                offset=mtd1.offset,
                erase_span=ERASE_BLOCK_SIZE,
                payload=activation,
                label="activation",
            )
            self._transition("activation_written")

            expected_kernel = _padded(images.kernel, mtd1.size)
            final_kernel = self.nor.read(mtd1.offset, mtd1.size, "final_kernel")
            if hashlib.sha256(final_kernel).digest() != hashlib.sha256(
                expected_kernel
            ).digest():
                raise MtdError("final kernel readback mismatch")
            self._transition("final_readback_verified")
        except InjectedPowerLoss:
            if not self.state.state.startswith("failed_"):
                self.state.fail("power_loss")
            raise
        except (MtdError, StateError):
            if not self.state.state.startswith("failed_"):
                self.state.fail("write_or_state_gate")
            raise

    def mark_thingino_booted(self) -> None:
        self._transition("thingino_booted")


class OfflineStage2Installer(FinalInstaller):
    """Model the fixed SD stage 2 while preserving the permanent mtd2 root."""

    stock_userdata_backup: bytes | None = None
    stock_userdata_backup_status: str | None = None
    stock_userdata_checkpoint: bytes | None = None

    def __init__(
        self,
        nor: FakeNor,
        state: InstallerStateMachine | None = None,
        *,
        stock_userdata_backup: bytes | None = None,
        stock_userdata_checkpoint: bytes | None = None,
    ):
        super().__init__(nor, state)
        self.stock_userdata_backup = stock_userdata_backup
        self.stock_userdata_backup_status = None
        self.stock_userdata_checkpoint = stock_userdata_checkpoint

    def run(self, images: OfflineStage2Images) -> None:
        images.validate()
        mtd1 = TARGET.partition(1)
        mtd2 = TARGET.partition(2)
        mtd3 = TARGET.partition(3)
        preserved_mtd2 = self.nor.snapshot()[mtd2.offset : mtd2.end]
        try:
            self._transition("device_verified")
            if images.data_mode == "initialize":
                if self.stock_userdata_backup is None:
                    self.stock_userdata_backup = self.nor.read(
                        mtd3.offset, mtd3.size, "stock_userdata_backup"
                    )
                    backup_status = "exported"
                else:
                    backup_status = "reused"
                if len(self.stock_userdata_backup) != mtd3.size:
                    raise MtdError("stock userdata backup size mismatch")
                backup_matches_current_nor = self.nor.read(
                    mtd3.offset, mtd3.size, "stock_userdata_backup_verify"
                ) == self.stock_userdata_backup
                expected_checkpoint = _offline_recovery_checkpoint(
                    images, self.stock_userdata_backup
                )
                if backup_matches_current_nor:
                    self.nor.fault.checkpoint("before_write_recovery_checkpoint")
                    self.stock_userdata_checkpoint = expected_checkpoint
                    self.nor.fault.checkpoint("after_write_recovery_checkpoint")
                else:
                    backup_status = "interrupted_install_recovery"
                    self.nor.fault.checkpoint("before_readback_recovery_checkpoint")
                    if self.stock_userdata_checkpoint != expected_checkpoint:
                        raise MtdError("recovery checkpoint mismatch")
                    self.nor.fault.checkpoint("after_readback_recovery_checkpoint")
                self.stock_userdata_backup_status = backup_status
            else:
                self.stock_userdata_backup_status = f"{images.data_mode}_mode"
            self._transition("stock_backup_complete")
            self._transition("stock_backup_verified")
            self._transition("recovery_checkpoint_verified")
            self._transition("final_bundle_verified")
            self._transition("ram_resident_install_mode")

            preserved_data = self.nor.read(
                images.data_offset,
                images.data_erase_span,
                "data_before",
            )
            if images.data_mode != "preserve":
                self._write_and_verify(
                    offset=images.data_offset,
                    erase_span=images.data_erase_span,
                    payload=b"",
                    label="final_data",
                )
            elif self.nor.read(
                images.data_offset,
                images.data_erase_span,
                "data_preserved",
            ) != preserved_data:
                raise MtdError("preserved data region changed")
            self._transition("final_data_written")

            self._write_and_verify(
                offset=images.system_offset,
                erase_span=images.system_erase_span,
                payload=images.system,
                label="final_system",
            )
            self._transition("final_rootfs_written")

            kernel_tail = images.kernel[ERASE_BLOCK_SIZE:]
            self._write_and_verify(
                offset=mtd1.offset + ERASE_BLOCK_SIZE,
                erase_span=mtd1.size - ERASE_BLOCK_SIZE,
                payload=kernel_tail,
                label="final_kernel_tail",
            )
            self._transition("final_kernel_written")

            self._write_and_verify(
                offset=mtd1.offset,
                erase_span=ERASE_BLOCK_SIZE,
                payload=images.kernel[:ERASE_BLOCK_SIZE],
                label="activation",
            )
            self._transition("activation_written")

            expected_kernel = _padded(images.kernel, mtd1.size)
            final_kernel = self.nor.read(mtd1.offset, mtd1.size, "final_kernel")
            if hashlib.sha256(final_kernel).digest() != hashlib.sha256(
                expected_kernel
            ).digest():
                raise MtdError("final kernel readback mismatch")
            if self.nor.snapshot()[mtd2.offset : mtd2.end] != preserved_mtd2:
                raise MtdError("permanent stage-1 mtd2 changed")
            self._transition("final_readback_verified")
        except InjectedPowerLoss:
            if not self.state.state.startswith("failed_"):
                self.state.fail("power_loss")
            raise
        except (MtdError, StateError):
            if not self.state.state.startswith("failed_"):
                self.state.fail("write_or_state_gate")
            raise


class StockRestoreInstaller(FinalInstaller):
    """Fake-NOR proof of bounded stock restore and activation-last ordering."""

    def __init__(
        self,
        nor: FakeNor,
        state: StockRestoreStateMachine | None = None,
    ):
        self.nor = nor
        self.state = state or StockRestoreStateMachine()

    def _verify_protected(self, images: StockRestoreImages) -> None:
        snapshot = self.nor.snapshot()
        for expected, mtd in zip(images.protected, STOCK_RESTORE_PROTECTED_MTD):
            partition = TARGET.partition(mtd)
            if snapshot[partition.offset : partition.end] != expected:
                raise MtdError(f"same-device protected mtd{mtd} differs")

    def run(self, images: StockRestoreImages) -> None:
        try:
            images.validate()
            self._verify_protected(images)
            self._transition("same_device_verified")
            self._transition("restore_images_validated")
            self._transition("restore_prepared")

            mtd3 = TARGET.partition(3)
            self._write_and_verify(
                offset=mtd3.offset,
                erase_span=mtd3.size,
                payload=images.mtd3,
                label="stock_mtd3",
            )
            self._transition("mtd3_written")
            self._transition("mtd3_readback_verified")

            mtd2 = TARGET.partition(2)
            self._write_and_verify(
                offset=mtd2.offset,
                erase_span=mtd2.size,
                payload=images.mtd2,
                label="stock_mtd2",
            )
            self._transition("mtd2_written")
            self._transition("mtd2_readback_verified")

            mtd1 = TARGET.partition(1)
            self._write_and_verify(
                offset=mtd1.offset + ERASE_BLOCK_SIZE,
                erase_span=mtd1.size - ERASE_BLOCK_SIZE,
                payload=images.mtd1[ERASE_BLOCK_SIZE:],
                label="stock_mtd1_tail",
            )
            self._transition("mtd1_tail_written")
            self._transition("mtd1_tail_readback_verified")

            self._write_and_verify(
                offset=mtd1.offset,
                erase_span=ERASE_BLOCK_SIZE,
                payload=images.mtd1[:ERASE_BLOCK_SIZE],
                label="stock_activation",
            )
            self._transition("stock_activation_written")
            self._transition("stock_activation_readback_verified")

            snapshot = self.nor.snapshot()
            for expected, mtd in (
                (images.mtd1, 1),
                (images.mtd2, 2),
                (images.mtd3, 3),
            ):
                partition = TARGET.partition(mtd)
                actual = self.nor.read(
                    partition.offset,
                    partition.size,
                    f"stock_full_mtd{mtd}",
                )
                if actual != expected:
                    raise MtdError(f"complete stock mtd{mtd} readback differs")
            self._verify_protected(images)
            if snapshot != self.nor.snapshot():
                raise MtdError("fake NOR changed during final stock readback")
            self._transition("stock_full_readback_verified")
            self._transition("stock_restore_complete")
        except InjectedPowerLoss:
            if not self.state.state.startswith("failed_"):
                self.state.fail("power_loss")
            raise
        except (MtdError, StateError):
            if not self.state.state.startswith("failed_"):
                self.state.fail("write_or_state_gate")
            raise


def classify_stock_restore_snapshot(
    snapshot: bytes,
    *,
    before: bytes,
    images: StockRestoreImages,
) -> str:
    """Classify a stopped fake-NOR run without authorizing a retry."""

    if len(snapshot) != NOR_SIZE or len(before) != NOR_SIZE:
        return "stop"
    for mtd in STOCK_RESTORE_PROTECTED_MTD:
        partition = TARGET.partition(mtd)
        if snapshot[partition.offset : partition.end] != before[
            partition.offset : partition.end
        ]:
            return "stop"
    mtd1 = TARGET.partition(1)
    mtd2 = TARGET.partition(2)
    mtd3 = TARGET.partition(3)
    current_mtd1 = snapshot[mtd1.offset : mtd1.end]
    before_mtd1 = before[mtd1.offset : mtd1.end]
    if (
        current_mtd1 == before_mtd1
        and snapshot[mtd2.offset : mtd2.end] == before[mtd2.offset : mtd2.end]
    ):
        return "survivor"
    activation = current_mtd1[:ERASE_BLOCK_SIZE]
    if activation not in (
        before_mtd1[:ERASE_BLOCK_SIZE],
        images.mtd1[:ERASE_BLOCK_SIZE],
    ):
        return "unviable"
    if (
        current_mtd1 == images.mtd1
        and snapshot[mtd2.offset : mtd2.end] == images.mtd2
        and snapshot[mtd3.offset : mtd3.end] == images.mtd3
    ):
        return "survivor"
    return "retry"
