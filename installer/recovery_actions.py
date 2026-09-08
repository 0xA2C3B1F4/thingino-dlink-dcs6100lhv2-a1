"""Headless UARTless capture preparation and recovery acceptance."""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from scripts.platform.media_preflight import create_preflight_document
from .install_actions import WritePlan, WriteConfirmation, require_write_confirmation, reserved_media_identity
from .install_project import ProjectError
from .install_results import InstallationResult, InstallationEvent, EventSink, document
from .media_preflight import validate_media_preflight_document
from .media import (stage_passive_verified_package, activate_passive_verified_package,
                    deactivate_verified_package, UARTLESS_CAPTURE_ACTIVE_FILENAME,
                    UARTLESS_CAPTURE_PASSIVE_FILENAME)
from .sd_package import read_snapshot, parse_package, validate_bootstrap, package_manifest
from .full_backup import capture_functional_backup_from_uartless_collector, validate_complete_backup, capture_complete_backup_from_ram_collector


@dataclass(frozen=True)
class CaptureMediaInputs:
    package: Path
    package_manifest: Path
    mount_root: Path
    whole_device: str


def load_capture_package(package_path: Path, manifest_path: Path) -> tuple[bytes, object]:
    raw = read_snapshot(package_path)
    package = parse_package(raw, require_project_header=True)
    validate_bootstrap(package)
    expected = json.loads(package_manifest(package, purpose="uartless-functional-capture"))
    expected.update(future_physical_boot_writes_mtd=[1, 2], original_complete_backup=False,
                    original_preserved_mtd=[0, 3, 4, 5], restoration_class="recovery-functional")
    try:
        actual = json.loads(read_snapshot(manifest_path))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProjectError("invalid_input", "UARTless capture manifest is invalid") from exc
    if actual != expected or hashlib.sha256(raw).hexdigest() != package.sha256:
        raise ProjectError("invalid_input", "UARTless package manifest differs from the package")
    return raw, package


def plan_capture(inputs: CaptureMediaInputs, operation: str) -> WritePlan:
    writes = {
        "uartless-prepare": (f"SD create: {UARTLESS_CAPTURE_PASSIVE_FILENAME}; remains inert",),
        "uartless-authorize": (f"SD rename: {UARTLESS_CAPTURE_PASSIVE_FILENAME} to {UARTLESS_CAPTURE_ACTIVE_FILENAME}",
                               "future stock boot: physical mtd1,mtd2"),
        "uartless-handoff": (f"SD rename: {UARTLESS_CAPTURE_ACTIVE_FILENAME} to {UARTLESS_CAPTURE_PASSIVE_FILENAME}",),
    }
    if operation not in writes:
        raise ProjectError("invalid_input", "unknown capture operation")
    raw, _ = load_capture_package(inputs.package, inputs.package_manifest)
    media = validate_media_preflight_document(create_preflight_document(
        whole_device=inputs.whole_device, mount_root=inputs.mount_root), expected_root=inputs.mount_root)
    return WritePlan("label confirmation required; capture has no camera identity yet", "functional-capture",
        media, (("package", hashlib.sha256(raw).hexdigest()),
                ("reserved_sd_contents", reserved_media_identity(inputs.mount_root))), writes[operation],
        operation="stock-recovery " + operation)


def execute_capture(inputs: CaptureMediaInputs, operation: str, confirmation: WriteConfirmation,
                    *, emit: EventSink | None = None) -> InstallationResult:
    if emit:
        emit(InstallationEvent("validating", "stock-recovery " + operation))
    plan = plan_capture(inputs, operation)
    require_write_confirmation(plan, confirmation)
    # Use the bytes whose content identity was confirmed; recheck after loading.
    raw, _ = load_capture_package(inputs.package, inputs.package_manifest)
    if hashlib.sha256(raw).hexdigest() != dict(plan.artifacts)["package"]:
        raise ProjectError("stale_plan", "capture package changed before staging")
    kwargs = dict(root=inputs.mount_root, preflight=plan.media,
                  confirmed_physical_device=confirmation.physical_device)
    if emit:
        emit(InstallationEvent("writing-sd-files", plan.operation))
    if operation == "uartless-prepare":
        stage_passive_verified_package(raw, **kwargs)
        phase, next_action = "uartless-functional-capture-staged-inert", "review-write-set-then-run-uartless-authorize"
        next_command = "thingino-dlink stock-recovery uartless-authorize"
    elif operation == "uartless-authorize":
        activate_passive_verified_package(raw, **kwargs)
        phase, next_action = "uartless-functional-capture-armed", "boot-stock-uboot-once-then-return-sd-to-host"
        next_command = "thingino-dlink stock-recovery uartless-handoff"
    else:
        deactivate_verified_package(raw, active_name=UARTLESS_CAPTURE_ACTIVE_FILENAME,
                                    passive_name=UARTLESS_CAPTURE_PASSIVE_FILENAME, **kwargs)
        phase, next_action = "uartless-stock-selector-passive", "boot-camera-with-passive-card-to-run-uartless-collector"
        next_command = "thingino-dlink stock-recovery uartless-validate"
    if emit:
        emit(InstallationEvent("readback-completed", plan.operation))
    return document("stock-recovery " + operation, ok=True, phase=phase,
        next_command=next_command, preserved_mtd=[0, 3, 4, 5], result={
            "armed": operation == "uartless-authorize", "functional_recovery_accepted": False,
            "future_physical_boot_writes_mtd": [] if operation == "uartless-handoff" else [1, 2],
            "original_complete_backup_accepted": False, "original_preserved_mtd": [0, 3, 4, 5],
            "replacement_mtd": [1, 2], "restoration_class": "recovery-functional",
            "safe_next_action": next_action, "write_set": [], "plan_sha256": plan.identity})


@dataclass(frozen=True)
class FunctionalRecoveryInputs:
    collector_dir: Path
    package: Path
    output_dir: Path
    confirmed_output_dir: Path


def accept_functional_recovery(inputs: FunctionalRecoveryInputs) -> InstallationResult:
    decision = capture_functional_backup_from_uartless_collector(
        collector_dir=inputs.collector_dir, bootstrap_package=read_snapshot(inputs.package),
        output_dir=inputs.output_dir, confirmed_output_dir=inputs.confirmed_output_dir)
    return document("stock-recovery uartless-validate", ok=True,
        phase="uartless-functional-recovery-validated", preserved_mtd=[0, 3, 4, 5],
        next_command="thingino-dlink local-build build-universal --vendor-bundle-dir " + shlex.quote(str(inputs.output_dir / "vendor")), result={
            "armed": False, "duplicate_backup_accepted": decision.duplicate_partitions_accepted,
            "functional_recovery_accepted": decision.functional_recovery_accepted,
            "original_complete_backup_accepted": decision.original_complete_backup_accepted,
            "original_preserved_mtd": list(decision.original_preserved_mtd),
            "replacement_mtd": list(decision.replacement_mtd), "restoration_class": "recovery-functional",
            "safe_next_action": "build-one-model-universal-install-set", "write_set": [],
            "functional_recovery_dir": str(inputs.output_dir),
            "preserved_readback_dir": str(inputs.output_dir / "preserved"),
            "vendor_bundle_dir": str(inputs.output_dir / "vendor")})


def inspect_exact_recovery(recovery_dir: Path) -> InstallationResult:
    decision = validate_complete_backup(recovery_dir)
    return document("stock-recovery backup-validate", ok=True, phase="private-backup-validated",
        result={"duplicate_backup_accepted": decision.duplicate_partitions_accepted,
                "full_flash_reconstruction_accepted": decision.full_flash_reconstruction_accepted,
                "recovery_dir": str(recovery_dir), "write_set": []})


@dataclass(frozen=True, kw_only=True)
class ExactRecoveryInputs:
    recovery_dir: Path | None = None
    collector_dir: Path | None = None
    output_dir: Path | None = None
    confirmed_output_dir: Path | None = None


def accept_exact_recovery(inputs: ExactRecoveryInputs) -> InstallationResult:
    if (inputs.recovery_dir is None) == (inputs.collector_dir is None):
        raise ProjectError("missing_input", "select existing exact recovery or completed collector output")
    if inputs.collector_dir is not None:
        if inputs.output_dir is None or inputs.confirmed_output_dir is None:
            raise ProjectError("missing_input", "confirm the private exact-recovery destination", ("--output-dir", "--confirm-output-dir"))
        decision = capture_complete_backup_from_ram_collector(collector_dir=inputs.collector_dir,
            output_dir=inputs.output_dir, confirmed_output_dir=inputs.confirmed_output_dir)
        output = inputs.output_dir
    else:
        decision = validate_complete_backup(inputs.recovery_dir)
        output = inputs.recovery_dir
    return document("stock-recovery backup-validate", ok=True, phase="private-backup-validated",
        next_command="thingino-dlink stock-recovery restore-prepare", result={
            "duplicate_backup_accepted": decision.duplicate_partitions_accepted,
            "full_flash_reconstruction_accepted": decision.full_flash_reconstruction_accepted,
            "same_device_binding_accepted": False, "physical_restore_proven": False,
            "restore_status": "backup-only", "safe_next_action": "collect-current-read-only-mtd0-mtd4-mtd5",
            "write_set": [3, 2, 1], "recovery_dir": str(output)})
