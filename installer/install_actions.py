"""Headless universal SD staging using the existing validators and writer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from scripts.platform.media_preflight import create_preflight_document
from .install_project import ProjectError, fingerprint
from .install_results import InstallationResult, InstallationEvent, EventSink, document
from .media_preflight import MediaPreflight, validate_media_preflight_document
from .provisioning import recovery_session_identity
from . import media_contracts
from .sd_package import is_matching_update_filename
from .stage1.build import BOOTSTRAP_FILENAME
from .stage2 import FILENAME as STAGE2_FILENAME
from .recovery_gate import validate_existing_recovery_boundary, validate_functional_recovery_boundary
from .universal_install import (
    ValidatedUniversalInstall, stage_camera_bound_universal_install,
    validate_camera_bound_universal_install,
)


@dataclass(frozen=True)
class UniversalStageInputs:
    install_set_dir: Path
    universal_public_key: Path
    provisioning: Path
    provisioning_data: Path
    authorization_dir: Path
    authorization_public_key: Path
    session_dir: Path
    preserved_readback_dir: Path
    mount_root: Path
    whole_device: str
    recovery_dir: Path | None = None
    functional_recovery_dir: Path | None = None


@dataclass(frozen=True)
class WritePlan:
    camera: str
    recovery_class: str
    media: MediaPreflight
    artifacts: tuple[tuple[str, str], ...]
    writes: tuple[str, ...]
    operation: str = "universal stage"

    def document(self) -> dict[str, object]:
        value = asdict(self)
        value["media"]["mount_root"] = str(self.media.mount_root)
        return value

    @property
    def identity(self) -> str:
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()

    def required_confirmations(self) -> dict[str, str]:
        phrases = {"universal stage": "STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3",
                   "universal handoff": "MTD1-MTD2-WRITTEN",
                   "universal evacuate-recovery": "COPY-VERIFY-THEN-REMOVE-BACKUPS",
                   "stock-recovery uartless-prepare": "STAGE-INERT-CAPTURE",
                   "stock-recovery uartless-authorize": "WRITE-MTD1-MTD2",
                   "stock-recovery uartless-handoff": "MTD1-MTD2-WRITTEN"}
        return {"plan_sha256": self.identity, "physical_device": self.media.physical_device,
                "target": "DCS-6100LHV2-A1", "write_set": phrases[self.operation]}


@dataclass(frozen=True)
class WriteConfirmation:
    plan_sha256: str
    physical_device: str
    target: str
    write_set: str


def reserved_media_identity(root: Path) -> str:
    """Bind installer-owned names only; recordings are not installer inputs."""
    names = {BOOTSTRAP_FILENAME, STAGE2_FILENAME,
             "THINGINO.PROVISION", "INSTALL.AUTH", "INSTALL.AUTH.SIG", "INSTALL.AUTH.BIN",
             ".thingino-stage2-upload.part", ".thingino-installer-upload.part"}
    names.update(value for key, value in vars(media_contracts).items()
                 if key.endswith("_FILENAME") and isinstance(value, str))
    names.update("." + name + ".part" for name in tuple(names))
    names.update("._" + name for name in tuple(names))
    # Include unexpected stock-selector matches without walking unrelated dirs.
    names.update(entry.name for entry in root.iterdir() if is_matching_update_filename(entry.name))
    identities = {}
    for name in sorted(names):
        path = root / name
        if path.exists() or path.is_symlink():
            identities[name] = fingerprint(path)
    return hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()


def plan_universal_stage(inputs: UniversalStageInputs) -> tuple[WritePlan, ValidatedUniversalInstall]:
    if (inputs.recovery_dir is None) == (inputs.functional_recovery_dir is None):
        raise ProjectError("invalid_input", "select exactly one recovery evidence class")
    functional = inputs.functional_recovery_dir is not None
    validator = validate_functional_recovery_boundary if functional else validate_existing_recovery_boundary
    recovery = validator(recovery_dir=inputs.functional_recovery_dir if functional else inputs.recovery_dir,
                         preserved_readback_dir=inputs.preserved_readback_dir)
    validated = validate_camera_bound_universal_install(
        install_set_dir=inputs.install_set_dir,
        universal_bundle_public_key=inputs.universal_public_key,
        recovery=recovery, provisioning_path=inputs.provisioning,
        provisioning_data_path=inputs.provisioning_data,
        authorization_dir=inputs.authorization_dir,
        authorization_public_key=inputs.authorization_public_key,
        recovery_session_sha256=recovery_session_identity(inputs.session_dir),
    )
    media = validate_media_preflight_document(create_preflight_document(
        whole_device=inputs.whole_device, mount_root=inputs.mount_root),
        expected_root=inputs.mount_root)
    artifacts = (
        ("universal_firmware", validated.bundle.sha256),
        ("authorization", validated.authorization.authorization_sha256),
        ("provisioning", validated.provisioning.sha256),
        ("bootstrap", hashlib.sha256(validated.bootstrap).hexdigest()),
        ("stage2", hashlib.sha256(validated.stage2.raw).hexdigest()),
        ("manifest", hashlib.sha256(validated.manifest).hexdigest()),
        ("reserved_sd_contents", reserved_media_identity(inputs.mount_root)),
    )
    return WritePlan(recovery.camera_identity_sha256,
                     "functional" if functional else "exact-original", media, artifacts,
                     ("SD: THINGINO.PROVISION, INSTALL.AUTH, INSTALL.AUTH.SIG, INSTALL.AUTH.BIN",
                      f"SD: {STAGE2_FILENAME}; {BOOTSTRAP_FILENAME} activated last",
                      "future stock boot: physical mtd1,mtd2",
                      "future stage1 boot: physical mtd1,mtd3")), validated


def execute_universal_stage(inputs: UniversalStageInputs, confirmation: WriteConfirmation,
                            *, emit: Callable[[str], None] | None = None) -> dict[str, str]:
    """Revalidate every input and rediscover the card after user confirmation."""
    if emit:
        emit("validating")
    plan, validated = plan_universal_stage(inputs)
    if confirmation.plan_sha256 != plan.identity:
        raise ProjectError("stale_plan", "write plan changed; inspect and confirm the new plan")
    if (confirmation.physical_device != plan.media.physical_device
            or confirmation.target != "DCS-6100LHV2-A1"
            or confirmation.write_set != "STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3"):
        raise ProjectError("confirmation_required", "exact target, media and write-set confirmations are required")
    if emit:
        emit("staging")
    result = stage_camera_bound_universal_install(
        validated, root=inputs.mount_root, preflight=plan.media,
        confirmed_physical_device=confirmation.physical_device)
    if emit:
        emit("readback-completed")
    return result


def require_write_confirmation(plan: WritePlan, confirmation: WriteConfirmation) -> None:
    if confirmation.plan_sha256 != plan.identity:
        raise ProjectError("stale_plan", "write plan changed; inspect and confirm the new plan")
    if asdict(confirmation) != plan.required_confirmations():
        raise ProjectError("confirmation_required", "exact current plan confirmations are required")


def plan_universal_handoff(inputs: UniversalStageInputs) -> tuple[WritePlan, ValidatedUniversalInstall]:
    plan, validated = plan_universal_stage(inputs)
    return replace(plan, operation="universal handoff", writes=(
        f"SD rename: {BOOTSTRAP_FILENAME} to {media_contracts.PASSIVE_BOOTSTRAP_FILENAME}",
        "future stage1 boot: physical mtd1,mtd3")), validated


def execute_universal_handoff(inputs: UniversalStageInputs, confirmation: WriteConfirmation,
                              *, emit: Callable[[str], None] | None = None) -> dict[str, str]:
    from .universal_install import handoff_camera_bound_universal_install
    if emit:
        emit("validating")
    plan, validated = plan_universal_handoff(inputs)
    require_write_confirmation(plan, confirmation)
    if emit:
        emit("passivating")
    result = handoff_camera_bound_universal_install(validated, root=inputs.mount_root,
        preflight=plan.media, confirmed_physical_device=confirmation.physical_device)
    if emit:
        emit("readback-completed")
    return result


@dataclass(frozen=True)
class EvacuationInputs:
    mount_root: Path
    whole_device: str
    output_dir: Path


def plan_evacuation(inputs: EvacuationInputs) -> WritePlan:
    from .media import inspect_stock_backup_evacuation
    media = validate_media_preflight_document(create_preflight_document(
        whole_device=inputs.whole_device, mount_root=inputs.mount_root), expected_root=inputs.mount_root)
    snapshots = inspect_stock_backup_evacuation(root=inputs.mount_root, destination_dir=inputs.output_dir)
    return WritePlan("backup-content-bound; camera identity not independently observed",
        "existing-stock-checkpoint", media,
        tuple((name, hashlib.sha256(raw).hexdigest()) for name, raw in sorted(snapshots.items()))
        + (("reserved_sd_contents", reserved_media_identity(inputs.mount_root)),),
        tuple(f"copy and verify {name} to {inputs.output_dir}; then remove SD copy" for name in sorted(snapshots)),
        operation="universal evacuate-recovery")


def execute_evacuation(inputs: EvacuationInputs, confirmation: WriteConfirmation,
                       *, emit: Callable[[str], None] | None = None) -> dict[str, str]:
    from .media import evacuate_existing_stock_backups
    if emit:
        emit("validating")
    plan = plan_evacuation(inputs)
    require_write_confirmation(plan, confirmation)
    if emit:
        emit("copying-verifying-removing")
    result = evacuate_existing_stock_backups(root=inputs.mount_root, destination_dir=inputs.output_dir,
        preflight=plan.media, confirmed_physical_device=confirmation.physical_device)
    if emit:
        emit("readback-completed")
    return result


def stage_universal(inputs: UniversalStageInputs, confirmation: WriteConfirmation,
                    *, emit: EventSink | None = None) -> InstallationResult:
    staged = execute_universal_stage(inputs, confirmation,
        emit=(lambda phase: emit(InstallationEvent(phase, "universal stage"))) if emit else None)
    return document("universal stage", ok=True, phase="camera-bound-universal-card-staged",
        next_command="thingino-dlink universal handoff", physical_actions=[
            "Power the camera off before inserting or removing the verified SD card.",
            "The first boot writes stock physical mtd1 and mtd2; the next phase writes final mtd1 and physical mtd3.",
            "Do not interrupt power during a write or readback."], result={
            "armed": True, "nor_written_by_host": False,
            "future_physical_write_phases": {"stock_bootstrap": [1, 2], "stage1_final": [1, 3]},
            "physical_device": confirmation.physical_device,
            "safe_next_action": "boot-stock-updater-once-then-return-sd-for-universal-handoff",
            "staged_files": staged, "write_set": [], "plan_sha256": confirmation.plan_sha256})


def handoff_universal(inputs: UniversalStageInputs, confirmation: WriteConfirmation,
                      *, emit: EventSink | None = None) -> InstallationResult:
    retained = execute_universal_handoff(inputs, confirmation,
        emit=(lambda phase: emit(InstallationEvent(phase, "universal handoff"))) if emit else None)
    return document("universal handoff", ok=True, phase="camera-bound-universal-stock-selector-passive",
        physical_actions=["Power the camera off before inserting or removing the verified SD card.",
                          "The next boot runs Stage 1 and writes final physical mtd1 and mtd3.",
                          "Do not interrupt power while Stage 1 reports a write or readback."],
        result={"armed": False, "future_physical_write_phases": {"stage1_final": [1, 3]},
                "nor_written_by_host": False, "physical_device": confirmation.physical_device,
                "retained_files": retained, "safe_next_action": "boot-camera-with-passive-card-to-run-stage1",
                "write_set": [], "plan_sha256": confirmation.plan_sha256})


def evacuate_recovery(inputs: EvacuationInputs, confirmation: WriteConfirmation,
                      *, emit: EventSink | None = None) -> InstallationResult:
    evacuated = execute_evacuation(inputs, confirmation,
        emit=(lambda phase: emit(InstallationEvent(phase, "universal evacuate-recovery"))) if emit else None)
    return document("universal evacuate-recovery", ok=True, phase="completed-recovery-checkpoint-evacuated",
        next_command="thingino-dlink universal stage", result={
            "evacuated_files": evacuated, "evacuation_dir": str(inputs.output_dir),
            "nor_written_by_host": False, "physical_device": confirmation.physical_device,
            "safe_next_action": "stage-new-camera-bound-universal-card", "sd_modified": True,
            "write_set": [], "plan_sha256": confirmation.plan_sha256})
