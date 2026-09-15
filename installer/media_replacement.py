"""Verified passive bootstrap migration with paired rollback."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .media_contracts import (
    DirectorySync,
    PASSIVE_BOOTSTRAP_FILENAME,
    Stage2Validators,
    ValidateInstallSet,
    ValidateLegacyMigrationProfile,
    ValidateSdRoot,
    WriteTemporary,
)
from .media_preflight import MediaError, MediaPreflight
from .sd_package import (
    is_matching_update_filename,
    matching_update_filenames,
    parse_package,
    validate_bootstrap,
)
from .stage2 import FILENAME as STAGE2_FILENAME, Stage2Error


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
    passive_name: str = "STAGE1.PKG",
    _sync_directory: DirectorySync,
    _validate_legacy_migration_profile: ValidateLegacyMigrationProfile,
    _write_verified_temporary: WriteTemporary,
    validate_install_set: ValidateInstallSet,
    validate_sd_root: ValidateSdRoot,
    stage2_validators: Stage2Validators,
) -> dict[str, str]:
    """Replace one verified passive bootstrap with rollback until readback."""
    validate_stage2 = stage2_validators.current
    validate_retired_stage2_v2_39_25 = stage2_validators.retired_memory
    validate_retired_stage2_v2_ipv6_disabled = stage2_validators.retired_ipv6
    validate_legacy_stage2_v1 = stage2_validators.legacy

    old_package = parse_package(old_bootstrap_bytes, require_project_header=True)
    new_package = parse_package(new_bootstrap_bytes, require_project_header=True)
    validate_bootstrap(old_package)
    validate_bootstrap(new_package)
    if old_stage2_bytes is None:
        old_stage2_bytes = stage2_bytes
    try:
        validate_stage2(old_stage2_bytes)
    except (Stage2Error, ValueError):
        try:
            try:
                validate_retired_stage2_v2_39_25(old_stage2_bytes)
            except (Stage2Error, ValueError):
                validate_retired_stage2_v2_ipv6_disabled(old_stage2_bytes)
        except (Stage2Error, ValueError):
            _validate_legacy_migration_profile(
                legacy_migration_profile_bytes,
                bootstrap_name=bootstrap_name,
                old_bootstrap_bytes=old_bootstrap_bytes,
                old_stage2_bytes=old_stage2_bytes,
            )
            try:
                validate_legacy_stage2_v1(old_stage2_bytes)
            except (Stage2Error, ValueError) as exc:
                raise MediaError(
                    "old passive stage-2 is neither current schema 2, "
                    "retired 42/22 schema 2, nor reviewed schema 1"
                ) from exc
    validate_install_set(
        bootstrap_bytes=new_bootstrap_bytes,
        stage2_bytes=stage2_bytes,
        manifest_bytes=manifest_bytes,
        bootstrap_name=bootstrap_name,
    )
    if old_bootstrap_bytes == new_bootstrap_bytes:
        raise MediaError("passive bootstrap replacement is byte-identical")
    if (
        passive_name != PASSIVE_BOOTSTRAP_FILENAME
        or Path(passive_name).name != passive_name
        or is_matching_update_filename(passive_name)
    ):
        raise MediaError("passive bootstrap filename is not the reviewed fixed name")
    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("replacement root changed after preflight")
    validate_sd_root(root)

    passive = root / passive_name
    stage2 = root / STAGE2_FILENAME
    temporary = root / ".thingino-stage1-replacement.part"
    stage2_temporary = root / ".thingino-stage2-replacement.part"
    rollback = root / ".thingino-stage1-rollback.part"
    stage2_rollback = root / ".thingino-stage2-rollback.part"
    owned_sidecars = tuple(
        root / ("._" + path.name)
        for path in (temporary, stage2_temporary, rollback, stage2_rollback)
    )
    output_sidecars = (
        root / ("._" + passive.name),
        root / ("._" + stage2.name),
    )
    if (
        passive.is_symlink()
        or stage2.is_symlink()
        or not passive.is_file()
        or not stage2.is_file()
        or temporary.exists()
        or stage2_temporary.exists()
        or rollback.exists()
        or stage2_rollback.exists()
        or any(sidecar.exists() for sidecar in owned_sidecars)
    ):
        raise MediaError("passive replacement paths are missing, linked, or ambiguous")
    if passive.read_bytes() != old_bootstrap_bytes:
        raise MediaError("old passive bootstrap differs from the reviewed artifact")
    if stage2.read_bytes() != old_stage2_bytes:
        raise MediaError("passive stage-2 readback differs from the reviewed artifact")

    bootstrap_rollback_created = False
    stage2_rollback_created = False
    bootstrap_activated = False
    stage2_activated = False
    replacement_committed = False
    try:
        _write_verified_temporary(temporary, new_bootstrap_bytes)
        _write_verified_temporary(stage2_temporary, stage2_bytes)
        os.replace(passive, rollback)
        bootstrap_rollback_created = True
        _sync_directory(root)
        os.replace(stage2, stage2_rollback)
        stage2_rollback_created = True
        _sync_directory(root)
        os.replace(stage2_temporary, stage2)
        stage2_activated = True
        _sync_directory(root)
        os.replace(temporary, passive)
        bootstrap_activated = True
        _sync_directory(root)
        if passive.read_bytes() != new_bootstrap_bytes:
            raise MediaError("replacement passive bootstrap readback mismatch")
        if stage2.read_bytes() != stage2_bytes:
            raise MediaError("stage-2 changed during passive bootstrap replacement")
        for sidecar in output_sidecars:
            sidecar.unlink(missing_ok=True)
        _sync_directory(root)
        if matching_update_filenames(entry.name for entry in root.iterdir()):
            raise MediaError("stock selector is not empty after passive replacement")
        replacement_committed = True
        cleanup_error: OSError | None = None
        for rollback_path in (rollback, stage2_rollback):
            try:
                rollback_path.unlink()
                _sync_directory(root)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            raise MediaError(
                "passive replacement is active and verified, but old-file "
                "cleanup failed"
            ) from cleanup_error
        bootstrap_rollback_created = False
        stage2_rollback_created = False
        return {
            passive_name: hashlib.sha256(new_bootstrap_bytes).hexdigest(),
            STAGE2_FILENAME: hashlib.sha256(stage2_bytes).hexdigest(),
        }
    except BaseException:
        if not replacement_committed:
            if bootstrap_rollback_created and rollback.exists():
                if bootstrap_activated:
                    passive.unlink(missing_ok=True)
                os.replace(rollback, passive)
                _sync_directory(root)
            if stage2_rollback_created and stage2_rollback.exists():
                if stage2_activated:
                    stage2.unlink(missing_ok=True)
                os.replace(stage2_rollback, stage2)
                _sync_directory(root)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        stage2_temporary.unlink(missing_ok=True)
        for sidecar in owned_sidecars:
            sidecar.unlink(missing_ok=True)
