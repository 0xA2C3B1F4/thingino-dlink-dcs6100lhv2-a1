"""Non-formatting SD-root staging after an external-device preflight."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .install_policy import universal_physical_write_policy
from . import media_transactions
from .media_contracts import (
    PASSIVE_BOOTSTRAP_FILENAME,
    PASSIVE_RECOVERY_FILENAME,
    UARTLESS_CAPTURE_ACTIVE_FILENAME,
    UARTLESS_CAPTURE_PASSIVE_FILENAME,
    STOCK_BACKUP_FILENAME,
    ARCHIVED_STOCK_BACKUP_FILENAME,
    STOCK_BACKUP_SIZE,
    RECOVERY_CHECKPOINT_FILENAME,
    RECOVERY_CHECKPOINT_SIZE,
    RECOVERY_CHECKPOINT_MAGIC,
    UNIVERSAL_RECOVERY_CHECKPOINT_SIZE,
    UNIVERSAL_RECOVERY_CHECKPOINT_MAGIC,
    EVACUATION_MANIFEST_FILENAME,
    LEGACY_MIGRATION_PROFILE_KIND,
    Stage2Validators,
)
from .layout import TARGET
from .media_preflight import (
    MediaError,
    MediaPreflight,
    _validated_physical_device,
    load_media_preflight,
    validate_media_preflight_document,
)
from .sd_package import (
    is_matching_update_filename,
    matching_update_filenames,
    parse_package,
    selected_update_filename,
    validate_bootstrap,
)
from .stage2 import FILENAME as STAGE2_FILENAME
from .stage2 import (
    Stage2Error,
    Stage2Payload,
    validate_legacy_stage2_v1,
    validate_retired_stage2_v2_39_25,
    validate_retired_stage2_v2_ipv6_disabled,
    validate_stage2,
)




def validate_sd_root(
    root: Path, *, allowed_matching_filename: str | None = None
) -> list[str]:
    if root.is_symlink() or not root.is_dir() or root.resolve() == Path("/"):
        raise MediaError("SD root is not a safe real directory")
    names = [entry.name for entry in root.iterdir() if entry.is_file() and not entry.is_symlink()]
    matches = matching_update_filenames(names)
    if matches and matches != [allowed_matching_filename]:
        raise MediaError(
            "SD root already contains a stock-matching update filename: "
            + ", ".join(matches)
        )
    return names


def archive_existing_stock_backup(
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> dict[str, str]:
    """Preserve one complete prior mtd3 backup before a stage-1 retry."""

    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("archive root changed after preflight")
    validate_sd_root(root)
    source = root / STOCK_BACKUP_FILENAME
    destination = root / ARCHIVED_STOCK_BACKUP_FILENAME
    source_sidecar = root / ("._" + source.name)
    destination_sidecar = root / ("._" + destination.name)
    checkpoint = root / RECOVERY_CHECKPOINT_FILENAME
    checkpoint_sidecar = root / ("._" + checkpoint.name)
    if checkpoint.exists() or checkpoint.is_symlink() or checkpoint_sidecar.exists():
        raise MediaError(
            "recovery checkpoint is present; keep its stock-backup pair intact"
        )
    if (
        source.is_symlink()
        or not source.is_file()
        or destination.exists()
        or source_sidecar.exists()
        or destination_sidecar.exists()
    ):
        raise MediaError("stock-backup archive paths are missing, linked, or ambiguous")
    snapshot = source.read_bytes()
    if len(snapshot) != STOCK_BACKUP_SIZE:
        raise MediaError("existing stock backup has the wrong exact size")
    archived = False
    try:
        os.replace(source, destination)
        archived = True
        _sync_directory(root)
        if source.exists() or destination.read_bytes() != snapshot:
            raise MediaError("archived stock-backup readback mismatch")
        return {
            ARCHIVED_STOCK_BACKUP_FILENAME: hashlib.sha256(snapshot).hexdigest()
        }
    except BaseException:
        if archived and destination.exists() and not source.exists():
            os.replace(destination, source)
            _sync_directory(root)
        raise


def inspect_stock_backup_evacuation(*, root: Path, destination_dir: Path) -> dict[str, bytes]:
    root_resolved = root.resolve(strict=True)
    validate_sd_root(root)
    if destination_dir.exists() or destination_dir.is_symlink():
        raise MediaError("private evacuation destination already exists")
    destination_parent = destination_dir.parent.resolve(strict=True)
    if (
        destination_parent == root_resolved
        or root_resolved in destination_parent.parents
    ):
        raise MediaError("private evacuation destination must be outside the SD root")

    backup = root / STOCK_BACKUP_FILENAME
    archived = root / ARCHIVED_STOCK_BACKUP_FILENAME
    checkpoint = root / RECOVERY_CHECKPOINT_FILENAME
    stage2 = root / STAGE2_FILENAME
    if backup.is_symlink() or not backup.is_file():
        raise MediaError("active stock backup is missing, linked, or ambiguous")

    selected = [backup]
    for optional in (archived, checkpoint):
        if optional.is_symlink():
            raise MediaError("stock-backup evacuation path is linked or ambiguous")
        if optional.exists():
            if not optional.is_file():
                raise MediaError("stock-backup evacuation path is not a regular file")
            selected.append(optional)
    for path in selected:
        if (root / ("._" + path.name)).exists():
            raise MediaError("stock-backup evacuation has an ambiguous sidecar")

    snapshots = {path.name: path.read_bytes() for path in selected}
    for name in (STOCK_BACKUP_FILENAME, ARCHIVED_STOCK_BACKUP_FILENAME):
        if name in snapshots and len(snapshots[name]) != STOCK_BACKUP_SIZE:
            raise MediaError(f"{name} has the wrong exact size")
    if RECOVERY_CHECKPOINT_FILENAME in snapshots:
        if stage2.is_symlink() or not stage2.is_file():
            raise MediaError("recovery checkpoint lacks its current stage-2 file")
        _validate_recovery_checkpoint(
            root,
            stage2.read_bytes(),
            allow_unverified_universal_bindings=True,
        )

    return snapshots


def evacuate_existing_stock_backups(
    *,
    root: Path,
    destination_dir: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> dict[str, str]:
    """Copy private stock backups off-card, verify them, then clear reserved paths."""

    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("evacuation root changed after preflight")
    snapshots = inspect_stock_backup_evacuation(root=root, destination_dir=destination_dir)
    destination_parent = destination_dir.parent.resolve(strict=True)

    hashes = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in snapshots.items()
    }
    work = Path(
        tempfile.mkdtemp(
            prefix=f".{destination_dir.name}.", dir=destination_parent
        )
    )
    try:
        for name, raw in snapshots.items():
            _write_verified_temporary(work / name, raw)
        manifest = {
            "schema_version": 1,
            "status": "private same-device stock-backup evacuation; never publish",
            "source": {
                "capacity_bytes": preflight.capacity_bytes,
                "filesystem": preflight.filesystem,
                "model": preflight.model,
                "physical_device": preflight.physical_device,
            },
            "files": {
                name: {"sha256": hashes[name], "size": len(snapshots[name])}
                for name in sorted(snapshots)
            },
        }
        _write_verified_temporary(
            work / EVACUATION_MANIFEST_FILENAME,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        _sync_directory(work)
        os.chmod(work, 0o700)
        for path in work.iterdir():
            path.chmod(0o600)
        os.replace(work, destination_dir)
        _sync_directory(destination_parent)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise

    removed: list[str] = []
    try:
        for name in (
            RECOVERY_CHECKPOINT_FILENAME,
            STOCK_BACKUP_FILENAME,
            ARCHIVED_STOCK_BACKUP_FILENAME,
        ):
            if name not in snapshots:
                continue
            (root / name).unlink()
            removed.append(name)
            _sync_directory(root)
        if any((root / name).exists() for name in snapshots):
            raise MediaError("reserved stock-backup path remained after evacuation")
    except BaseException as exc:
        try:
            for name in removed:
                path = root / name
                if path.exists():
                    if path.read_bytes() != snapshots[name]:
                        raise MediaError("cannot restore changed stock-backup path")
                    continue
                temporary = root / f".thingino-{name.lower()}-restore.part"
                _write_verified_temporary(temporary, snapshots[name])
                os.replace(temporary, path)
                _sync_directory(root)
        except BaseException as restore_exc:
            raise MediaError(
                "stock-backup evacuation failed and card rollback also failed"
            ) from restore_exc
        raise MediaError("stock-backup evacuation failed; card state was restored") from exc
    return hashes


def _validate_recovery_checkpoint(
    root: Path,
    stage2_bytes: bytes,
    *,
    authorization_bytes: bytes | None = None,
    provisioning_bytes: bytes | None = None,
    allow_unverified_universal_bindings: bool = False,
) -> None:
    backup_path = root / STOCK_BACKUP_FILENAME
    checkpoint_path = root / RECOVERY_CHECKPOINT_FILENAME
    for path in (backup_path, checkpoint_path):
        if (
            path.is_symlink()
            or not path.is_file()
            or (root / ("._" + path.name)).exists()
        ):
            raise MediaError("recovery checkpoint paths are missing, linked, or ambiguous")
    backup = backup_path.read_bytes()
    checkpoint = checkpoint_path.read_bytes()
    if len(backup) != STOCK_BACKUP_SIZE:
        raise MediaError("recovery stock backup has the wrong exact size")
    common = b"".join(
        (
            len(backup).to_bytes(4, "big"),
            len(stage2_bytes).to_bytes(4, "big"),
            hashlib.sha256(backup).digest(),
            hashlib.sha256(stage2_bytes).digest(),
        )
    )
    if checkpoint[:8] == RECOVERY_CHECKPOINT_MAGIC:
        expected = RECOVERY_CHECKPOINT_MAGIC + common
    elif checkpoint[:8] == UNIVERSAL_RECOVERY_CHECKPOINT_MAGIC:
        if authorization_bytes is None or provisioning_bytes is None:
            expected_prefix = UNIVERSAL_RECOVERY_CHECKPOINT_MAGIC + common
            if (
                allow_unverified_universal_bindings
                and len(checkpoint) == UNIVERSAL_RECOVERY_CHECKPOINT_SIZE
                and checkpoint.startswith(expected_prefix)
            ):
                return
            raise MediaError(
                "universal recovery checkpoint requires authorization and provisioning bindings"
            )
        expected = b"".join(
            (
                UNIVERSAL_RECOVERY_CHECKPOINT_MAGIC,
                common,
                hashlib.sha256(authorization_bytes).digest(),
                hashlib.sha256(provisioning_bytes).digest(),
            )
        )
    else:
        expected = b""
    if checkpoint != expected:
        raise MediaError("recovery checkpoint does not bind backup and stage 2")


def _validate_install_manifest(
    manifest_bytes: bytes,
    *,
    bootstrap_name: str,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    stage2_payload: Stage2Payload,
) -> None:
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaError("install-set manifest is invalid JSON") from exc
    schema = manifest.get("schema_version")
    if schema not in (1, 2):
        raise MediaError("install-set manifest schema is not supported")
    target = manifest.get("target")
    if target != {"hardware_revision": "A1", "model": "DCS-6100LHV2"}:
        raise MediaError("install-set manifest targets the wrong camera")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MediaError("install-set manifest lacks artifact bindings")
    for name, raw in (
        (bootstrap_name, bootstrap_bytes),
        (STAGE2_FILENAME, stage2_bytes),
    ):
        expected = {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if artifacts.get(name) != expected:
            raise MediaError(f"install-set manifest does not bind {name}")
    if schema == 2:
        artifact_scope = manifest.get("artifact_scope")
        if artifact_scope is not None and artifact_scope not in {
            "device-personalized",
            "model-universal",
        }:
            raise MediaError("install-set artifact scope is invalid")
        if artifact_scope == "model-universal":
            firmware_identity = manifest.get("universal_firmware_sha256")
            if (
                manifest.get("provisioning") != "separate-per-camera-audit-and-jffs2"
                or not isinstance(firmware_identity, str)
                or re.fullmatch(r"[0-9a-f]{64}", firmware_identity) is None
                or stage2_payload.data_mode not in {"initialize", "preserve"}
                or manifest.get("physical_write_policy")
                != universal_physical_write_policy(stage2_payload.data_mode)
            ):
                raise MediaError("model-universal install-set binding is invalid")
        elif artifact_scope == "device-personalized" and (
            manifest.get("provisioning") != "embedded-device-personalization"
            or manifest.get("universal_firmware_sha256") is not None
        ):
            raise MediaError("personalized install-set binding is invalid")
        expected_layout = {
            "abi": "dcs6100lhv2-a1-mtd3-split-v1",
            "parent_physical_mtd": 3,
            "parent_offset": TARGET.partition(3).offset,
            "parent_span": TARGET.partition(3).size,
            "system_offset": stage2_payload.system_flash_offset,
            "system_span": stage2_payload.system_flash_span,
            "data_offset": stage2_payload.data_flash_offset,
            "data_span": stage2_payload.data_flash_span,
            "preserved_physical_mtd": [0, 4, 5],
            "data_mode": stage2_payload.data_mode,
        }
        if manifest.get("layout") != expected_layout:
            raise MediaError("install-set manifest split layout does not match stage 2")
        expected_region_policy = {
            "system": {
                "filesystem": "squashfs",
                "write": "erase-write-readback",
                "sha256": hashlib.sha256(stage2_payload.system).hexdigest(),
                "payload_size": len(stage2_payload.system),
            },
            "data": {
                "filesystem": "jffs2",
                "initialize": (
                    "camera-authorized-jffs2-erase-write-readback"
                    if artifact_scope == "model-universal"
                    else "explicit-erased-region"
                ),
                "preserve": "before-and-after-complete-region-sha256",
                "factory_reset": "explicit-data-only-erase",
                "corrupt": "preserve-and-require-explicit-recovery",
            },
            "activation": {
                "region": "kernel-first-64KiB",
                "written_last": True,
            },
        }
        if manifest.get("region_policy") != expected_region_policy:
            raise MediaError("install-set manifest region policy does not match stage 2")


def _validate_legacy_migration_profile(
    profile_bytes: bytes | None,
    *,
    bootstrap_name: str,
    old_bootstrap_bytes: bytes,
    old_stage2_bytes: bytes,
) -> None:
    """Bind an explicitly reviewed private schema-1 predecessor pair."""

    if profile_bytes is None:
        raise MediaError("legacy schema-1 replacement requires an explicit profile")
    try:
        profile = json.loads(profile_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaError("legacy migration profile is invalid JSON") from exc
    expected = {
        "artifacts": {
            bootstrap_name: {
                "sha256": hashlib.sha256(old_bootstrap_bytes).hexdigest(),
                "size": len(old_bootstrap_bytes),
            },
            STAGE2_FILENAME: {
                "sha256": hashlib.sha256(old_stage2_bytes).hexdigest(),
                "size": len(old_stage2_bytes),
            },
        },
        "migration": LEGACY_MIGRATION_PROFILE_KIND,
        "schema_version": 1,
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
        },
    }
    if profile != expected:
        raise MediaError("legacy migration profile does not bind the reviewed old pair")


def build_legacy_migration_profile(
    *,
    bootstrap_name: str,
    old_bootstrap_bytes: bytes,
    old_stage2_bytes: bytes,
) -> bytes:
    """Create a private exact-identity profile for a valid schema-1 pair."""

    package = parse_package(old_bootstrap_bytes, require_project_header=True)
    validate_bootstrap(package)
    validate_legacy_stage2_v1(old_stage2_bytes)
    profile = {
        "artifacts": {
            bootstrap_name: {
                "sha256": hashlib.sha256(old_bootstrap_bytes).hexdigest(),
                "size": len(old_bootstrap_bytes),
            },
            STAGE2_FILENAME: {
                "sha256": hashlib.sha256(old_stage2_bytes).hexdigest(),
                "size": len(old_stage2_bytes),
            },
        },
        "migration": LEGACY_MIGRATION_PROFILE_KIND,
        "schema_version": 1,
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
        },
    }
    raw = (json.dumps(profile, indent=2, sort_keys=True) + "\n").encode()
    _validate_legacy_migration_profile(
        raw,
        bootstrap_name=bootstrap_name,
        old_bootstrap_bytes=old_bootstrap_bytes,
        old_stage2_bytes=old_stage2_bytes,
    )
    return raw


def validate_install_set(
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    bootstrap_name: str,
) -> Stage2Payload:
    """Validate one immutable install-set snapshot without touching media."""

    package = parse_package(bootstrap_bytes, require_project_header=True)
    validate_bootstrap(package)
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaError("install-set manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise MediaError("install-set manifest must be an object")
    development_profile = manifest.get("development_profile")
    if development_profile is None:
        stage2_payload = validate_stage2(stage2_bytes)
    else:
        from .stage2 import validate_stage2_for_profile

        if manifest.get("schema_version") != 2 or manifest.get("artifact_scope") != "device-personalized":
            raise MediaError("development profile requires a personalized schema-2 install set")
        stage2_payload = validate_stage2_for_profile(stage2_bytes, development_profile)
    _validate_install_manifest(
        manifest_bytes,
        bootstrap_name=bootstrap_name,
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        stage2_payload=stage2_payload,
    )
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaError("install-set manifest is invalid JSON") from exc
    if manifest.get("schema_version") == 2:
        embedded_rootfs = package.records[1].payload
        artifacts = manifest.get("artifacts")
        expected_names = {
            bootstrap_name,
            STAGE2_FILENAME,
            "stage1-bootstrap.squashfs",
        }
        if manifest.get("artifact_scope") == "model-universal":
            expected_names.add("thingino-universal.tgb")
        if not isinstance(artifacts, dict) or set(artifacts) != expected_names:
            raise MediaError("install-set manifest artifact allowlist changed")
        if artifacts.get("stage1-bootstrap.squashfs") != {
            "size": len(embedded_rootfs),
            "sha256": hashlib.sha256(embedded_rootfs).hexdigest(),
        }:
            raise MediaError("install-set manifest does not bind embedded stage 1")
    if (
        not is_matching_update_filename(bootstrap_name)
        or Path(bootstrap_name).name != bootstrap_name
    ):
        raise MediaError("bootstrap filename does not match the stock root selector")
    return stage2_payload


def _write_verified_temporary(temporary: Path, raw: bytes) -> None:
    return media_transactions._write_verified_temporary(
        temporary,
        raw,
    )


def _sync_directory(root: Path) -> None:
    return media_transactions._sync_directory(
        root,
    )


def stage_verified_install_set(
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> dict[str, str]:
    return media_transactions.stage_verified_install_set(
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        root=root,
        bootstrap_name=bootstrap_name,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        _sync_directory=_sync_directory,
        _write_verified_temporary=_write_verified_temporary,
        validate_install_set=validate_install_set,
        validate_sd_root=validate_sd_root,
    )


def activate_staged_install_set(
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = PASSIVE_BOOTSTRAP_FILENAME,
    recovery_authorization_bytes: bytes | None = None,
    recovery_provisioning_bytes: bytes | None = None,
) -> dict[str, str]:
    return media_transactions.activate_staged_install_set(
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        root=root,
        bootstrap_name=bootstrap_name,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        passive_name=passive_name,
        recovery_authorization_bytes=recovery_authorization_bytes,
        recovery_provisioning_bytes=recovery_provisioning_bytes,
        _sync_directory=_sync_directory,
        _validate_recovery_checkpoint=_validate_recovery_checkpoint,
        validate_install_set=validate_install_set,
        validate_sd_root=validate_sd_root,
    )


def deactivate_staged_install_set(
    *,
    bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = PASSIVE_BOOTSTRAP_FILENAME,
    existing_passive_bytes: bytes | None = None,
) -> dict[str, str]:
    return media_transactions.deactivate_staged_install_set(
        bootstrap_bytes=bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        root=root,
        bootstrap_name=bootstrap_name,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        passive_name=passive_name,
        existing_passive_bytes=existing_passive_bytes,
        _sync_directory=_sync_directory,
        validate_install_set=validate_install_set,
    )


def replace_passive_bootstrap(
    *,
    old_bootstrap_bytes: bytes,
    old_stage2_bytes: bytes | None = None,
    legacy_migration_profile_bytes: bytes | None = None,
    new_bootstrap_bytes: bytes,
    stage2_bytes: bytes,
    manifest_bytes: bytes,
    root: Path,
    bootstrap_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = PASSIVE_BOOTSTRAP_FILENAME,
) -> dict[str, str]:
    return media_transactions.replace_passive_bootstrap(
        old_bootstrap_bytes=old_bootstrap_bytes,
        old_stage2_bytes=old_stage2_bytes,
        legacy_migration_profile_bytes=legacy_migration_profile_bytes,
        new_bootstrap_bytes=new_bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        root=root,
        bootstrap_name=bootstrap_name,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        passive_name=passive_name,
        _sync_directory=_sync_directory,
        _validate_legacy_migration_profile=_validate_legacy_migration_profile,
        _write_verified_temporary=_write_verified_temporary,
        validate_install_set=validate_install_set,
        validate_sd_root=validate_sd_root,
        stage2_validators=Stage2Validators(
            current=validate_stage2,
            retired_memory=validate_retired_stage2_v2_39_25,
            retired_ipv6=validate_retired_stage2_v2_ipv6_disabled,
            legacy=validate_legacy_stage2_v1,
        ),
    )


def stage_passive_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = UARTLESS_CAPTURE_PASSIVE_FILENAME,
) -> str:
    return media_transactions.stage_passive_verified_package(
        package_bytes,
        root=root,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        passive_name=passive_name,
        _sync_directory=_sync_directory,
        _write_verified_temporary=_write_verified_temporary,
        validate_sd_root=validate_sd_root,
    )


def activate_passive_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    active_name: str = UARTLESS_CAPTURE_ACTIVE_FILENAME,
    passive_name: str = UARTLESS_CAPTURE_PASSIVE_FILENAME,
) -> str:
    return media_transactions.activate_passive_verified_package(
        package_bytes,
        root=root,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        active_name=active_name,
        passive_name=passive_name,
        _sync_directory=_sync_directory,
        validate_sd_root=validate_sd_root,
    )


def stage_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    output_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> str:
    return media_transactions.stage_verified_package(
        package_bytes,
        root=root,
        output_name=output_name,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        _sync_directory=_sync_directory,
        validate_sd_root=validate_sd_root,
    )


def deactivate_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    active_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    passive_name: str = PASSIVE_RECOVERY_FILENAME,
    existing_passive_bytes: bytes | None = None,
) -> str:
    return media_transactions.deactivate_verified_package(
        package_bytes,
        root=root,
        active_name=active_name,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
        passive_name=passive_name,
        existing_passive_bytes=existing_passive_bytes,
        _sync_directory=_sync_directory,
    )
