"""Non-formatting SD-root staging after an external-device preflight."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath

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


INCONSISTENT_MEDIA_MANIFEST_FILENAME = "inconsistent-media.manifest.private.json"
INCONSISTENT_MEDIA_STATE_FILENAME = "inconsistent-media.state.private.json"
INCONSISTENT_MEDIA_ROOT_INSTALLER_NAMES = {
    "STOCKM3.PART",
    "STOCKM3.OK.PART",
    "THINGINO.PROVISION",
    "INSTALL.AUTH",
    "INSTALL.AUTH.SIG",
    "INSTALL.AUTH.BIN",
    ".THINGINO.PROVISION.part",
    ".INSTALL.AUTH.part",
    ".INSTALL.AUTH.SIG.part",
    ".INSTALL.AUTH.BIN.part",
    ".thingino-stage2-upload.part",
    ".thingino-installer-upload.part",
    ".thingino-stage1-deactivate-rollback.part",
    ".thingino-stage1-replacement.part",
    ".thingino-stage2-replacement.part",
    ".thingino-stage1-rollback.part",
    ".thingino-stage2-rollback.part",
    ".thingino-installer-rollback.part",
    ".thingino-recovery-deactivate-rollback.part",
    ".uartless-capture-upload.part",
}


def _safe_media_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
        raise MediaError("inconsistent media contains an unsafe path")
    return relative


def _read_stable_regular(path: Path) -> tuple[bytes, tuple[int, int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MediaError("inconsistent media file cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MediaError("inconsistent media contains a non-regular path")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
    )
    after_identity = (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    )
    raw = b"".join(chunks)
    if before_identity != after_identity or len(raw) != before.st_size:
        raise MediaError("inconsistent media file changed while being read")
    return raw, after_identity


def _tree_content_snapshot(root: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    files: dict[str, dict[str, object]] = {}
    directories: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise MediaError("inconsistent media contains a linked path")
        relative = _safe_media_relative(path, root)
        if path.is_dir():
            directories.append(relative)
            continue
        if not path.is_file():
            raise MediaError("inconsistent media contains a non-regular path")
        raw, _ = _read_stable_regular(path)
        files[relative] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    return files, directories


def _snapshot_sha256(snapshot: dict[str, object]) -> str:
    bound = {
        "checkpoint": snapshot["checkpoint"],
        "directories": snapshot["directories"],
        "files": snapshot["files"],
        "removals": snapshot["removals"],
    }
    return hashlib.sha256(
        json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_snapshot_shape(snapshot: object) -> dict[str, object]:
    if not isinstance(snapshot, dict):
        raise MediaError("inconsistent-media snapshot is malformed")
    if not isinstance(snapshot.get("files"), dict) or not isinstance(snapshot.get("directories"), list):
        raise MediaError("inconsistent-media snapshot tree is malformed")
    if not isinstance(snapshot.get("removals"), list) or not isinstance(snapshot.get("checkpoint"), dict):
        raise MediaError("inconsistent-media snapshot contract is malformed")
    if snapshot.get("snapshot_sha256") != _snapshot_sha256(snapshot):
        raise MediaError("inconsistent-media snapshot identity differs")
    return snapshot


def _load_quarantine_document(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise MediaError("inconsistent-media quarantine document is missing or ambiguous")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaError("inconsistent-media quarantine document is invalid") from exc
    if not isinstance(value, dict):
        raise MediaError("inconsistent-media quarantine document is malformed")
    return value


def _validate_tree_against_snapshot(
    root: Path,
    snapshot: dict[str, object],
    *,
    allow_missing_removals: bool,
) -> list[str]:
    files, directories = _tree_content_snapshot(root)
    expected_files = snapshot["files"]
    removals = set(snapshot["removals"])
    missing = sorted(set(expected_files) - set(files))
    if not allow_missing_removals and missing:
        raise MediaError("inconsistent media changed after its reviewed snapshot")
    if any(name not in removals for name in missing):
        raise MediaError("non-installer media content is missing from the reviewed snapshot")
    if set(files) - set(expected_files) or directories != snapshot["directories"]:
        raise MediaError("inconsistent media tree changed after its reviewed snapshot")
    for name, identity in files.items():
        if identity != expected_files[name]:
            raise MediaError("inconsistent media content changed after its reviewed snapshot")
    return missing


def inspect_inconsistent_media_quarantine(
    *, root: Path, destination_dir: Path, resume: bool = False
) -> dict[str, object]:
    """Describe one mismatched universal checkpoint without accepting it as recovery."""

    root_resolved = root.resolve(strict=True)
    validate_sd_root(root)
    if not destination_dir.is_absolute():
        raise MediaError("private quarantine destination must be absolute")
    for component in (destination_dir, *destination_dir.parents):
        if component.is_symlink():
            raise MediaError("private quarantine destination has a linked component")
    destination_parent = destination_dir.parent.resolve(strict=True)
    if destination_parent == root_resolved or root_resolved in destination_parent.parents:
        raise MediaError("private quarantine destination must be outside the SD root")

    if resume:
        manifest = _load_quarantine_document(destination_dir / INCONSISTENT_MEDIA_MANIFEST_FILENAME)
        state = _load_quarantine_document(destination_dir / INCONSISTENT_MEDIA_STATE_FILENAME)
        if manifest.get("schema_version") != 2 or state.get("schema_version") != 1:
            raise MediaError("inconsistent-media quarantine schema is unsupported")
        parent_stat = destination_parent.stat(follow_symlinks=False)
        if (
            manifest.get("destination") != str(destination_dir)
            or manifest.get("destination_parent_device") != parent_stat.st_dev
            or manifest.get("destination_parent_inode") != parent_stat.st_ino
        ):
            raise MediaError("inconsistent-media quarantine destination identity differs")
        snapshot = _validate_snapshot_shape(manifest.get("snapshot"))
        if state.get("snapshot_sha256") != snapshot["snapshot_sha256"]:
            raise MediaError("inconsistent-media resume state binds another snapshot")
        if state.get("phase") not in ("archived-pending-removal", "completed"):
            raise MediaError("inconsistent-media quarantine is not resumable")
        archive = destination_dir / "archive"
        archive_files, archive_directories = _tree_content_snapshot(archive)
        if archive_files != snapshot["files"] or archive_directories != snapshot["directories"]:
            raise MediaError("inconsistent-media archive differs from its receipt")
        completed = state.get("completed_removals")
        if not isinstance(completed, list) or any(name not in snapshot["removals"] for name in completed):
            raise MediaError("inconsistent-media resume progress is malformed")
        missing: list[str] = []
        for name in snapshot["removals"]:
            path = root / name
            if not path.exists() and not path.is_symlink():
                missing.append(name)
                continue
            if path.is_symlink():
                raise MediaError("inconsistent-media removal path became ambiguous")
            raw, _ = _read_stable_regular(path)
            identity = snapshot["files"][name]
            if len(raw) != identity["size"] or hashlib.sha256(raw).hexdigest() != identity["sha256"]:
                raise MediaError("inconsistent-media removal path differs from its archive")
        if state["phase"] == "completed":
            if set(missing) != set(snapshot["removals"]):
                raise MediaError("completed quarantine has a restored installer path")
            resume_mode = "completed"
        else:
            try:
                _validate_tree_against_snapshot(root, snapshot, allow_missing_removals=True)
                stable = True
            except MediaError:
                stable = False
            progress_consistent = set(completed).issubset(missing)
            resume_mode = "finish" if stable and progress_consistent else "rollback"
        snapshot = dict(snapshot)
        snapshot["resume"] = True
        snapshot["missing_removals"] = missing
        snapshot["state_phase"] = state["phase"]
        snapshot["resume_mode"] = resume_mode
        return snapshot
    if destination_dir.exists() or destination_dir.is_symlink():
        raise MediaError("private quarantine destination already exists")

    files, directories = _tree_content_snapshot(root)

    required = {
        STOCK_BACKUP_FILENAME,
        RECOVERY_CHECKPOINT_FILENAME,
        STAGE2_FILENAME,
    }
    if not required.issubset(files):
        raise MediaError("inconsistent media lacks its recovery tuple")
    backup, _ = _read_stable_regular(root / STOCK_BACKUP_FILENAME)
    checkpoint, _ = _read_stable_regular(root / RECOVERY_CHECKPOINT_FILENAME)
    stage2, _ = _read_stable_regular(root / STAGE2_FILENAME)
    for name, raw in (
        (STOCK_BACKUP_FILENAME, backup),
        (RECOVERY_CHECKPOINT_FILENAME, checkpoint),
        (STAGE2_FILENAME, stage2),
    ):
        if files[name] != {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}:
            raise MediaError("inconsistent media recovery tuple changed while being inspected")
    if len(backup) != STOCK_BACKUP_SIZE:
        raise MediaError("inconsistent media stock backup has the wrong exact size")
    if (
        len(checkpoint) != UNIVERSAL_RECOVERY_CHECKPOINT_SIZE
        or checkpoint[:8] != UNIVERSAL_RECOVERY_CHECKPOINT_MAGIC
    ):
        raise MediaError("inconsistent media lacks a universal recovery checkpoint")
    expected_backup_size = int.from_bytes(checkpoint[8:12], "big")
    expected_stage2_size = int.from_bytes(checkpoint[12:16], "big")
    expected_backup_sha256 = checkpoint[16:48].hex()
    expected_stage2_sha256 = checkpoint[48:80].hex()
    backup_sha256 = hashlib.sha256(backup).hexdigest()
    stage2_sha256 = hashlib.sha256(stage2).hexdigest()
    if expected_backup_size != len(backup) or expected_backup_sha256 != backup_sha256:
        raise MediaError("inconsistent media checkpoint does not bind its stock backup")
    if expected_stage2_size != len(stage2):
        raise MediaError("inconsistent media checkpoint stage-2 size differs")
    if expected_stage2_sha256 == stage2_sha256:
        raise MediaError("recovery checkpoint is consistent; use evacuate-recovery")

    reserved = INCONSISTENT_MEDIA_ROOT_INSTALLER_NAMES | {
        PASSIVE_BOOTSTRAP_FILENAME,
        PASSIVE_RECOVERY_FILENAME,
        UARTLESS_CAPTURE_ACTIVE_FILENAME,
        UARTLESS_CAPTURE_PASSIVE_FILENAME,
        STOCK_BACKUP_FILENAME,
        ARCHIVED_STOCK_BACKUP_FILENAME,
        RECOVERY_CHECKPOINT_FILENAME,
        STAGE2_FILENAME,
    }
    reserved.update("._" + name for name in tuple(reserved))
    removals = sorted(
        name
        for name in files
        if "/" not in name
        and (name in reserved or is_matching_update_filename(name))
    )
    if not {STOCK_BACKUP_FILENAME, RECOVERY_CHECKPOINT_FILENAME, STAGE2_FILENAME}.issubset(removals):
        raise MediaError("inconsistent media quarantine removal set is incomplete")
    snapshot = {
        "checkpoint": {
            "backup_sha256": backup_sha256,
            "current_stage2_sha256": stage2_sha256,
            "expected_stage2_sha256": expected_stage2_sha256,
            "stage2_size": len(stage2),
        },
        "files": files,
        "directories": directories,
        "removals": removals,
    }
    snapshot["snapshot_sha256"] = _snapshot_sha256(snapshot)
    snapshot["resume"] = False
    snapshot["missing_removals"] = []
    snapshot["state_phase"] = "not-started"
    snapshot["resume_mode"] = "fresh"
    return snapshot


def quarantine_inconsistent_media(
    *,
    root: Path,
    destination_dir: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    expected_snapshot_sha256: str,
    expected_resume_mode: str,
    expected_missing_removals_sha256: str,
    resume: bool = False,
) -> dict[str, object]:
    """Archive an inconsistent card's file tree, then clear exact installer paths."""

    if confirmed_physical_device != preflight.physical_device:
        raise MediaError("exact physical-device confirmation does not match preflight")
    if root.resolve(strict=True) != preflight.mount_root:
        raise MediaError("quarantine root changed after preflight")
    expected_mount_identity = (preflight.mount_device_id, preflight.mount_inode)
    if (root.stat(follow_symlinks=False).st_dev, root.stat(follow_symlinks=False).st_ino) != expected_mount_identity:
        raise MediaError("quarantine media identity changed after preflight")
    snapshot = inspect_inconsistent_media_quarantine(
        root=root, destination_dir=destination_dir, resume=resume
    )
    if snapshot["snapshot_sha256"] != expected_snapshot_sha256:
        raise MediaError("quarantine snapshot differs from the confirmed plan")
    if snapshot["resume_mode"] != expected_resume_mode:
        raise MediaError("quarantine resume mode differs from the confirmed plan")
    missing_identity = hashlib.sha256(json.dumps(
        snapshot.get("missing_removals", []), separators=(",", ":")
    ).encode()).hexdigest()
    if missing_identity != expected_missing_removals_sha256:
        raise MediaError("quarantine missing-path set differs from the confirmed plan")
    destination_parent = destination_dir.parent.resolve(strict=True)
    parent_stat = destination_parent.stat(follow_symlinks=False)
    expected_parent_identity = (parent_stat.st_dev, parent_stat.st_ino)

    def validate_mount_and_parent() -> None:
        mount_stat = root.resolve(strict=True).stat(follow_symlinks=False)
        if (mount_stat.st_dev, mount_stat.st_ino) != expected_mount_identity:
            raise MediaError("quarantine media identity changed during operation")
        current_parent = destination_parent.stat(follow_symlinks=False)
        if (current_parent.st_dev, current_parent.st_ino) != expected_parent_identity:
            raise MediaError("private quarantine destination parent changed")

    def write_state(phase: str, completed: list[str]) -> None:
        state = {
            "schema_version": 1,
            "phase": phase,
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "completed_removals": sorted(completed),
        }
        raw = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + INCONSISTENT_MEDIA_STATE_FILENAME + ".",
            suffix=".part",
            dir=destination_dir,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            if temporary.read_bytes() != raw:
                raise MediaError("inconsistent-media state readback mismatch")
            os.replace(temporary, destination_dir / INCONSISTENT_MEDIA_STATE_FILENAME)
            _sync_directory(destination_dir)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def restore_missing_removals() -> list[str]:
        validate_mount_and_parent()
        restored: list[str] = []
        for relative in snapshot["removals"]:
            path = root / relative
            archived = destination_dir / "archive" / relative
            archived_raw, _ = _read_stable_regular(archived)
            identity = snapshot["files"][relative]
            if (
                len(archived_raw) != identity["size"]
                or hashlib.sha256(archived_raw).hexdigest() != identity["sha256"]
            ):
                raise MediaError("cannot restore invalid quarantined installer path")
            if path.exists() or path.is_symlink():
                if path.is_symlink():
                    raise MediaError("cannot restore an ambiguous installer path")
                current_raw, _ = _read_stable_regular(path)
                if current_raw != archived_raw:
                    raise MediaError("cannot overwrite a changed installer path during rollback")
                continue
            temporary = root / f".thingino-{Path(relative).name.lower()}-restore.part"
            _write_verified_temporary(temporary, archived_raw)
            os.replace(temporary, path)
            _sync_directory(root)
            restored.append(relative)
        write_state("rolled-back", [])
        return restored

    if not resume:
        work = Path(tempfile.mkdtemp(prefix=f".{destination_dir.name}.", dir=destination_parent))
        destination_created = False
        try:
            archive = work / "archive"
            archive.mkdir(mode=0o700)
            for relative in snapshot["directories"]:
                (archive / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
            for relative, identity in snapshot["files"].items():
                raw, _ = _read_stable_regular(root / relative)
                if len(raw) != identity["size"] or hashlib.sha256(raw).hexdigest() != identity["sha256"]:
                    raise MediaError("inconsistent media changed while being archived")
                target = archive / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write_verified_temporary(target, raw)
                target.chmod(0o600)
                with target.open("rb") as archived_stream:
                    os.fsync(archived_stream.fileno())
            for directory in sorted(
                (path for path in archive.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts), reverse=True,
            ):
                _sync_directory(directory)
            _sync_directory(archive)
            archived_files, archived_directories = _tree_content_snapshot(archive)
            if archived_files != snapshot["files"] or archived_directories != snapshot["directories"]:
                raise MediaError("inconsistent media archive final readback mismatch")
            manifest = {
                "schema_version": 2,
                "status": "inconsistent media file-tree archive, not validated recovery",
                "destination": str(destination_dir),
                "destination_parent_device": expected_parent_identity[0],
                "destination_parent_inode": expected_parent_identity[1],
                "source": {
                    "capacity_bytes": preflight.capacity_bytes,
                    "filesystem": preflight.filesystem,
                    "media_uuid": preflight.media_uuid,
                    "model": preflight.model,
                    "physical_device": preflight.physical_device,
                },
                "snapshot": {key: snapshot[key] for key in (
                    "checkpoint", "directories", "files", "removals", "snapshot_sha256"
                )},
                "planned_installer_path_removals": snapshot["removals"],
                "recovery_validated": False,
                "authorization_and_provisioning_bindings_validated": False,
            }
            manifest_path = work / INCONSISTENT_MEDIA_MANIFEST_FILENAME
            _write_verified_temporary(
                manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            )
            manifest_path.chmod(0o600)
            _sync_directory(work)
            validate_mount_and_parent()
            _validate_tree_against_snapshot(root, snapshot, allow_missing_removals=False)
            destination_dir.mkdir(mode=0o700)
            destination_created = True
            _sync_directory(destination_parent)
            os.replace(archive, destination_dir / "archive")
            os.replace(manifest_path, destination_dir / INCONSISTENT_MEDIA_MANIFEST_FILENAME)
            _sync_directory(destination_dir)
            write_state("archived-pending-removal", [])
        except BaseException:
            if not destination_created:
                shutil.rmtree(work, ignore_errors=True)
            raise
        finally:
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)
    elif snapshot["resume_mode"] == "completed":
        return {
            "archive_dir": str(destination_dir), "checkpoint": snapshot["checkpoint"],
            "files": snapshot["files"], "recovery_validated": False,
            "removed_installer_paths": snapshot["removals"],
            "snapshot_sha256": snapshot["snapshot_sha256"], "resumed": True,
        }
    elif snapshot["resume_mode"] == "rollback":
        restored = restore_missing_removals()
        return {
            "archive_dir": str(destination_dir), "checkpoint": snapshot["checkpoint"],
            "files": snapshot["files"], "recovery_validated": False,
            "removed_installer_paths": [], "restored_installer_paths": restored,
            "snapshot_sha256": snapshot["snapshot_sha256"], "resumed": True,
            "rolled_back": True,
        }

    state = _load_quarantine_document(destination_dir / INCONSISTENT_MEDIA_STATE_FILENAME)
    completed = list(state.get("completed_removals", []))
    missing = _validate_tree_against_snapshot(root, snapshot, allow_missing_removals=True)
    completed = sorted(set(completed) | set(missing))
    write_state("archived-pending-removal", completed)
    try:
        for relative in snapshot["removals"]:
            if relative in completed:
                continue
            validate_mount_and_parent()
            path = root / relative
            raw, stable_identity = _read_stable_regular(path)
            after = path.stat(follow_symlinks=False)
            identity = snapshot["files"][relative]
            if (
                stable_identity
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(raw) != identity["size"]
                or hashlib.sha256(raw).hexdigest() != identity["sha256"]
            ):
                raise MediaError("installer path changed immediately before removal")
            path.unlink()
            _sync_directory(root)
            completed.append(relative)
            completed.sort()
            write_state("archived-pending-removal", completed)
        validate_mount_and_parent()
        if any(
            (root / relative).exists() or (root / relative).is_symlink()
            for relative in snapshot["removals"]
        ):
            raise MediaError("installer path remained after inconsistent-media quarantine")
        write_state("completed", completed)
    except BaseException as exc:
        try:
            restore_missing_removals()
        except BaseException as restore_exc:
            raise MediaError(
                "inconsistent-media quarantine failed and card rollback also failed"
            ) from restore_exc
        raise MediaError(
            "inconsistent-media quarantine failed; card state was restored"
        ) from exc
    return {
        "archive_dir": str(destination_dir),
        "checkpoint": snapshot["checkpoint"],
        "files": snapshot["files"],
        "recovery_validated": False,
        "removed_installer_paths": snapshot["removals"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "resumed": resume,
    }


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
