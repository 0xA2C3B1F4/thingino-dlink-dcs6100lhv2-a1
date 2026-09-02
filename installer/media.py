"""Non-formatting SD-root staging after an external-device preflight."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

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
    validate_stage2,
)


PASSIVE_BOOTSTRAP_FILENAME = "STAGE1.PKG"
PASSIVE_RECOVERY_FILENAME = "RECOVERY.OFF"
STOCK_BACKUP_FILENAME = "STOCKM3.BIN"
ARCHIVED_STOCK_BACKUP_FILENAME = "STOCKM3.OLD"
STOCK_BACKUP_SIZE = 0x007C0000
RECOVERY_CHECKPOINT_FILENAME = "STOCKM3.OK"
RECOVERY_CHECKPOINT_SIZE = 80
RECOVERY_CHECKPOINT_MAGIC = b"DCS6RC01"
EVACUATION_MANIFEST_FILENAME = "evacuation.manifest.private.json"
LEGACY_MIGRATION_PROFILE_KIND = "dcs6100lhv2-a1-stage2-v1-to-v2"


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
    root_resolved = root.resolve(strict=True)
    if root_resolved != preflight.mount_root:
        raise MediaError("evacuation root changed after preflight")
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
        _validate_recovery_checkpoint(root, stage2.read_bytes())

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


def _validate_recovery_checkpoint(root: Path, stage2_bytes: bytes) -> None:
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
    expected = b"".join(
        (
            RECOVERY_CHECKPOINT_MAGIC,
            len(backup).to_bytes(4, "big"),
            len(stage2_bytes).to_bytes(4, "big"),
            hashlib.sha256(backup).digest(),
            hashlib.sha256(stage2_bytes).digest(),
        )
    )
    if len(checkpoint) != RECOVERY_CHECKPOINT_SIZE or checkpoint != expected:
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
                "initialize": "explicit-erased-region",
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
    stage2_payload = validate_stage2(stage2_bytes)
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
    from .media_transactions import _write_verified_temporary as implementation

    return implementation(sys.modules[__name__], temporary, raw)


def _sync_directory(root: Path) -> None:
    from .media_transactions import _sync_directory as implementation

    return implementation(sys.modules[__name__], root)


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
    from .media_transactions import stage_verified_install_set as implementation

    return implementation(sys.modules[__name__], bootstrap_bytes=bootstrap_bytes, stage2_bytes=stage2_bytes, manifest_bytes=manifest_bytes, root=root, bootstrap_name=bootstrap_name, preflight=preflight, confirmed_physical_device=confirmed_physical_device)


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
) -> dict[str, str]:
    from .media_transactions import activate_staged_install_set as implementation

    return implementation(sys.modules[__name__], bootstrap_bytes=bootstrap_bytes, stage2_bytes=stage2_bytes, manifest_bytes=manifest_bytes, root=root, bootstrap_name=bootstrap_name, preflight=preflight, confirmed_physical_device=confirmed_physical_device, passive_name=passive_name)


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
    from .media_transactions import deactivate_staged_install_set as implementation

    return implementation(sys.modules[__name__], bootstrap_bytes=bootstrap_bytes, stage2_bytes=stage2_bytes, manifest_bytes=manifest_bytes, root=root, bootstrap_name=bootstrap_name, preflight=preflight, confirmed_physical_device=confirmed_physical_device, passive_name=passive_name, existing_passive_bytes=existing_passive_bytes)


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
    from .media_transactions import replace_passive_bootstrap as implementation

    return implementation(sys.modules[__name__], old_bootstrap_bytes=old_bootstrap_bytes, old_stage2_bytes=old_stage2_bytes, legacy_migration_profile_bytes=legacy_migration_profile_bytes, new_bootstrap_bytes=new_bootstrap_bytes, stage2_bytes=stage2_bytes, manifest_bytes=manifest_bytes, root=root, bootstrap_name=bootstrap_name, preflight=preflight, confirmed_physical_device=confirmed_physical_device, passive_name=passive_name)


def stage_verified_package(
    package_bytes: bytes,
    *,
    root: Path,
    output_name: str,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> str:
    from .media_transactions import stage_verified_package as implementation

    return implementation(sys.modules[__name__], package_bytes, root=root, output_name=output_name, preflight=preflight, confirmed_physical_device=confirmed_physical_device)


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
    from .media_transactions import deactivate_verified_package as implementation

    return implementation(sys.modules[__name__], package_bytes, root=root, active_name=active_name, preflight=preflight, confirmed_physical_device=confirmed_physical_device, passive_name=passive_name, existing_passive_bytes=existing_passive_bytes)
