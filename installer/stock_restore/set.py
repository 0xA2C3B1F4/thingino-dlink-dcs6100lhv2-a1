"""Private preparation and exact SD staging for live stock restore."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import struct
import tempfile

from installer.fake_mtd import StockRestoreImages
from installer.live_ram import (
    KERNEL_SD_NAME,
    LiveRamPlan,
    MODE_WRAPPERS,
    build_live_ram_plan,
)
from installer.media import MediaPreflight, validate_sd_root
from installer.ram_boot import build_ramdisk_uimage
from installer.recovery import (
    SameDeviceStockRestorePlan,
    inspect_same_device_stock_restore,
)
from installer.sd_package import (
    generate_bootstrap,
    is_matching_update_filename,
    parse_package,
    selected_update_filename,
    validate_bootstrap,
)
from installer.stock_restore.build import build_stock_restore_root
from installer.stock_restore.kernel import validate_stock_restore_kernel


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


def _require_authorization(raw: bytes) -> bytes:
    if len(raw) != 32:
        raise StockRestoreSetError("live restore authorization has the wrong size")
    return raw


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


def _images(plan: SameDeviceStockRestorePlan) -> StockRestoreImages:
    result = StockRestoreImages(
        mtd1=plan.mtd1,
        mtd2=plan.mtd2,
        mtd3=plan.mtd3,
        protected=plan.protected,
        write_set=plan.write_set,
    )
    result.validate()
    return result


def _write_exclusive(path: Path, raw: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            amount = os.write(descriptor, view)
            if amount <= 0:
                raise StockRestoreSetError("short private restore-set write")
            view = view[amount:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular(path: Path, size: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != size
        ):
            raise StockRestoreSetError("private restore artifact is not exact")
        chunks: list[bytes] = []
        remaining = size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != size
    ):
        raise StockRestoreSetError("private restore artifact changed while read")
    return raw


def _read_bounded_regular(path: Path, maximum_size: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > maximum_size
    ):
        raise StockRestoreSetError("private restore manifest is not exact")
    return _read_regular(path, metadata.st_size)


def _payloads(images: StockRestoreImages) -> dict[str, bytes]:
    return {
        PAYLOAD_NAMES[0]: images.protected[0],
        PAYLOAD_NAMES[1]: images.mtd1,
        PAYLOAD_NAMES[2]: images.mtd2,
        PAYLOAD_NAMES[3]: images.mtd3,
        PAYLOAD_NAMES[4]: images.protected[1],
        PAYLOAD_NAMES[5]: images.protected[2],
    }


def _uimage_record_payload(raw: bytes) -> bytes:
    if len(raw) < 64:
        raise StockRestoreSetError("stock restore kernel record is too short")
    image_size = 64 + struct.unpack_from(">I", raw, 12)[0]
    padding = raw[image_size:]
    if image_size > len(raw) or len(padding) > 3 or padding != b"\xff" * len(padding):
        raise StockRestoreSetError("stock restore kernel record padding differs")
    return raw[:image_size]


def prepare_stock_restore_ram_set(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    restore_output_dir: Path,
    kernel: bytes,
    linux_config: bytes,
    mmc_module: bytes,
    output_dir: Path,
    clang: Path | None = None,
    lld: Path | None = None,
    mksquashfs: Path | None = None,
    unsquashfs: Path | None = None,
    authorization: bytes | None = None,
) -> StockRestoreRamSet:
    """Build a private restorer, unarmed and off-device."""

    if output_dir.exists() or output_dir.is_symlink():
        raise StockRestoreSetError("refusing to overwrite a live restore set")
    plan = inspect_same_device_stock_restore(
        recovery_dir=recovery_dir,
        preserved_readback_dir=preserved_readback_dir,
        output_dir=restore_output_dir,
    )
    images = _images(plan)
    token = _require_authorization(
        authorization if authorization is not None else secrets.token_bytes(32)
    )
    output_dir.mkdir(mode=0o700, parents=False)
    try:
        with tempfile.TemporaryDirectory(
            prefix="dcs6100-live-restore-", dir=os.environ.get("TMPDIR")
        ) as name:
            rootfs_path = Path(name) / "restorer.squashfs"
            build_stock_restore_root(
                images=images,
                mmc_module=mmc_module,
                authorization=token,
                output=rootfs_path,
                clang=clang,
                lld=lld,
                mksquashfs=mksquashfs,
                unsquashfs=unsquashfs,
            )
            wrapper = build_ramdisk_uimage(
                rootfs_path.read_bytes(), name="DCS6100 stock restore RAM"
            )
        live_plan = build_live_ram_plan(
            mode="stock-restore",
            kernel=kernel,
            linux_config=linux_config,
            wrapper=wrapper,
        )
        files = {
            KERNEL_SD_NAME: kernel,
            MODE_WRAPPERS["stock-restore"]: wrapper,
            **_payloads(images),
            AUTH_PRIVATE_NAME: token,
        }
        identities = {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
            for name, raw in files.items()
        }
        manifest = {
            "schema_version": 1,
            "purpose": "same-device-stock-1.02.02-live-restore-private",
            "artifacts": identities,
            "checks": {
                "duplicate_backup_accepted": True,
                "full_flash_reconstruction_accepted": True,
                "same_device_binding_accepted": True,
            },
            "nor_writes_during_preparation": False,
            "physical_restore_proven": False,
            "protected_mtd": [0, 4, 5],
            "restore_order": [
                "mtd3",
                "mtd2",
                "mtd1-tail",
                "mtd1-activation-last",
                "full-readback",
            ],
            "write_set": [3, 2, 1],
        }
        manifest_raw = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode()
        for name, raw in files.items():
            _write_exclusive(output_dir / name, raw, 0o400)
            if _read_regular(output_dir / name, len(raw)) != raw:
                raise StockRestoreSetError("private restore-set readback differs")
        _write_exclusive(output_dir / MANIFEST_NAME, manifest_raw, 0o600)
        if _read_regular(output_dir / MANIFEST_NAME, len(manifest_raw)) != manifest_raw:
            raise StockRestoreSetError("private restore manifest readback differs")
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return StockRestoreRamSet(live_plan, images, token, files)


def inspect_stock_restore_ram_set(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    restore_output_dir: Path,
    linux_config: bytes,
    output_dir: Path,
) -> StockRestoreRamSet:
    plan = inspect_same_device_stock_restore(
        recovery_dir=recovery_dir,
        preserved_readback_dir=preserved_readback_dir,
        output_dir=restore_output_dir,
    )
    images = _images(plan)
    expected_names = {
        KERNEL_SD_NAME,
        MODE_WRAPPERS["stock-restore"],
        *PAYLOAD_NAMES.values(),
        AUTH_PRIVATE_NAME,
        MANIFEST_NAME,
    }
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise StockRestoreSetError("live restore set is not a private directory")
    if {entry.name for entry in output_dir.iterdir()} != expected_names:
        raise StockRestoreSetError("live restore set closure differs")
    manifest_raw = _read_bounded_regular(output_dir / MANIFEST_NAME, 128 * 1024)
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StockRestoreSetError("live restore manifest is invalid") from exc
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict) or set(artifacts) != expected_names - {
        MANIFEST_NAME
    }:
        raise StockRestoreSetError("live restore manifest closure differs")
    files: dict[str, bytes] = {}
    inodes: set[tuple[int, int]] = set()
    for name, record in artifacts.items():
        if not isinstance(record, dict) or not isinstance(record.get("size"), int):
            raise StockRestoreSetError("live restore artifact record is invalid")
        item_path = output_dir / name
        metadata = item_path.stat(follow_symlinks=False)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in inodes:
            raise StockRestoreSetError("live restore set contains a hardlink")
        inodes.add(identity)
        raw = _read_regular(item_path, record["size"])
        if hashlib.sha256(raw).hexdigest() != record.get("sha256"):
            raise StockRestoreSetError("live restore artifact identity differs")
        files[name] = raw
    if _payloads(images) != {name: files[name] for name in PAYLOAD_NAMES.values()}:
        raise StockRestoreSetError("live restore payloads are not same-device images")
    _require_authorization(files[AUTH_PRIVATE_NAME])
    live_plan = build_live_ram_plan(
        mode="stock-restore",
        kernel=files[KERNEL_SD_NAME],
        linux_config=linux_config,
        wrapper=files[MODE_WRAPPERS["stock-restore"]],
    )
    if (
        manifest.get("write_set") != [3, 2, 1]
        or manifest.get("protected_mtd") != [0, 4, 5]
        or manifest.get("nor_writes_during_preparation") is not False
        or manifest.get("physical_restore_proven") is not False
    ):
        raise StockRestoreSetError("live restore safety contract differs")
    return StockRestoreRamSet(
        live_plan, images, files[AUTH_PRIVATE_NAME], files
    )


def prepare_stock_restore_bootstrap_set(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    restore_output_dir: Path,
    kernel: bytes,
    linux_config: bytes,
    mmc_module: bytes,
    output_dir: Path,
    clang: Path | None = None,
    lld: Path | None = None,
    mksquashfs: Path | None = None,
    unsquashfs: Path | None = None,
    authorization: bytes | None = None,
) -> StockRestoreBootstrapSet:
    """Build one unarmed, no-UART stock-restorer SD transport."""

    if output_dir.exists() or output_dir.is_symlink():
        raise StockRestoreSetError("refusing to overwrite a stock restore bootstrap")
    plan = inspect_same_device_stock_restore(
        recovery_dir=recovery_dir,
        preserved_readback_dir=preserved_readback_dir,
        output_dir=restore_output_dir,
    )
    images = _images(plan)
    validate_stock_restore_kernel(kernel=kernel, linux_config=linux_config)
    token = _require_authorization(
        authorization if authorization is not None else secrets.token_bytes(32)
    )
    output_dir.mkdir(mode=0o700, parents=False)
    try:
        with tempfile.TemporaryDirectory(
            prefix="dcs6100-stock-restore-bootstrap-",
            dir=os.environ.get("TMPDIR"),
        ) as name:
            rootfs_path = Path(name) / "restorer.squashfs"
            build_stock_restore_root(
                images=images,
                mmc_module=mmc_module,
                authorization=token,
                output=rootfs_path,
                clang=clang,
                lld=lld,
                mksquashfs=mksquashfs,
                unsquashfs=unsquashfs,
            )
            rootfs = rootfs_path.read_bytes()
        package = generate_bootstrap(kernel, rootfs)
        parsed = parse_package(package, require_project_header=True)
        validate_bootstrap(parsed)
        files = {
            BOOTSTRAP_PRIVATE_NAME: package,
            **_payloads(images),
            AUTH_PRIVATE_NAME: token,
        }
        manifest = {
            "schema_version": 1,
            "purpose": "same-device-stock-1.02.02-uartless-bootstrap-private",
            "artifacts": {
                name: {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
                for name, raw in files.items()
            },
            "activation": {
                "active_filename": BOOTSTRAP_ACTIVE_NAME,
                "passive_filename": BOOTSTRAP_PRIVATE_NAME,
                "selector_activated_last": True,
            },
            "checks": {
                "duplicate_backup_accepted": True,
                "full_flash_reconstruction_accepted": True,
                "same_device_binding_accepted": True,
                "stock_restore_kernel_accepted": True,
                "strict_mtd1_mtd2_transport_accepted": True,
            },
            "physical_restore_proven": False,
            "protected_mtd": [0, 4, 5],
            "stock_uboot_success_is_readback": False,
            "transport_write_set": [1, 2],
            "write_set": [3, 2, 1],
        }
        manifest_raw = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode()
        for name, raw in files.items():
            _write_exclusive(output_dir / name, raw, 0o400)
            if _read_regular(output_dir / name, len(raw)) != raw:
                raise StockRestoreSetError("private bootstrap readback differs")
        _write_exclusive(output_dir / BOOTSTRAP_MANIFEST_NAME, manifest_raw)
        if (
            _read_regular(output_dir / BOOTSTRAP_MANIFEST_NAME, len(manifest_raw))
            != manifest_raw
        ):
            raise StockRestoreSetError("private bootstrap manifest readback differs")
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return StockRestoreBootstrapSet(images=images, authorization=token, files=files)


def inspect_stock_restore_bootstrap_set(
    *,
    recovery_dir: Path,
    preserved_readback_dir: Path,
    restore_output_dir: Path,
    linux_config: bytes,
    output_dir: Path,
) -> StockRestoreBootstrapSet:
    plan = inspect_same_device_stock_restore(
        recovery_dir=recovery_dir,
        preserved_readback_dir=preserved_readback_dir,
        output_dir=restore_output_dir,
    )
    images = _images(plan)
    expected_names = {
        BOOTSTRAP_PRIVATE_NAME,
        BOOTSTRAP_MANIFEST_NAME,
        AUTH_PRIVATE_NAME,
        *PAYLOAD_NAMES.values(),
    }
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise StockRestoreSetError("stock restore bootstrap is not a private directory")
    if {entry.name for entry in output_dir.iterdir()} != expected_names:
        raise StockRestoreSetError("stock restore bootstrap closure differs")
    manifest_raw = _read_bounded_regular(
        output_dir / BOOTSTRAP_MANIFEST_NAME, 128 * 1024
    )
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StockRestoreSetError("stock restore bootstrap manifest is invalid") from exc
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict) or set(artifacts) != expected_names - {
        BOOTSTRAP_MANIFEST_NAME
    }:
        raise StockRestoreSetError("stock restore bootstrap manifest closure differs")
    files: dict[str, bytes] = {}
    inodes: set[tuple[int, int]] = set()
    for name, record in artifacts.items():
        if not isinstance(record, dict) or not isinstance(record.get("size"), int):
            raise StockRestoreSetError("stock restore bootstrap record is invalid")
        path = output_dir / name
        metadata = path.stat(follow_symlinks=False)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in inodes:
            raise StockRestoreSetError("stock restore bootstrap contains a hardlink")
        inodes.add(identity)
        raw = _read_regular(path, record["size"])
        if hashlib.sha256(raw).hexdigest() != record.get("sha256"):
            raise StockRestoreSetError("stock restore bootstrap identity differs")
        files[name] = raw
    if _payloads(images) != {name: files[name] for name in PAYLOAD_NAMES.values()}:
        raise StockRestoreSetError("stock restore payloads are not same-device images")
    _require_authorization(files[AUTH_PRIVATE_NAME])
    package = parse_package(
        files[BOOTSTRAP_PRIVATE_NAME], require_project_header=True
    )
    validate_bootstrap(package)
    validate_stock_restore_kernel(
        kernel=_uimage_record_payload(package.records[0].payload),
        linux_config=linux_config,
    )
    activation = manifest.get("activation")
    if (
        manifest.get("write_set") != [3, 2, 1]
        or manifest.get("transport_write_set") != [1, 2]
        or manifest.get("protected_mtd") != [0, 4, 5]
        or manifest.get("physical_restore_proven") is not False
        or manifest.get("stock_uboot_success_is_readback") is not False
        or not isinstance(activation, dict)
        or activation.get("active_filename") != BOOTSTRAP_ACTIVE_NAME
        or activation.get("passive_filename") != BOOTSTRAP_PRIVATE_NAME
        or activation.get("selector_activated_last") is not True
    ):
        raise StockRestoreSetError("stock restore bootstrap safety contract differs")
    return StockRestoreBootstrapSet(
        images=images,
        authorization=files[AUTH_PRIVATE_NAME],
        files=files,
    )


def _check_media(
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    *,
    allowed_matching_filename: str | None = None,
) -> None:
    if confirmed_physical_device != preflight.physical_device:
        raise StockRestoreSetError("exact SD device confirmation differs")
    if root.resolve(strict=True) != preflight.mount_root:
        raise StockRestoreSetError("SD root changed after preflight")
    validate_sd_root(root, allowed_matching_filename=allowed_matching_filename)


def stage_stock_restore_ram_set(
    *,
    prepared: StockRestoreRamSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> tuple[str, ...]:
    """Stage an unarmed set. RESTORE.GO is intentionally excluded."""

    _check_media(root, preflight, confirmed_physical_device)
    staged = {
        name: raw
        for name, raw in prepared.files.items()
        if name != AUTH_PRIVATE_NAME
    }
    reserved = {
        *staged,
        AUTH_SD_NAME,
        RUN_SD_NAME,
        COMPLETE_SD_NAME,
        PASSIVE_DIR_NAME,
    }
    reused: set[str] = set()
    for name in reserved:
        for candidate in (
            root / name,
            root / f".{name}.part",
            root / f"._{name}",
            root / f"._.{name}.part",
        ):
            if candidate.exists() or candidate.is_symlink():
                if (
                    name == KERNEL_SD_NAME
                    and candidate == root / name
                    and candidate.is_file()
                    and not candidate.is_symlink()
                    and candidate.read_bytes() == staged[name]
                ):
                    reused.add(name)
                    continue
                raise StockRestoreSetError("SD restore reserved path already exists")
    activated: list[Path] = []
    try:
        for name, raw in staged.items():
            if name in reused:
                continue
            temporary = root / f".{name}.part"
            destination = root / name
            _write_exclusive(temporary, raw)
            if temporary.read_bytes() != raw:
                raise StockRestoreSetError("SD temporary readback differs")
            os.replace(temporary, destination)
            activated.append(destination)
        for name in staged:
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        for name, raw in staged.items():
            if (root / name).read_bytes() != raw:
                raise StockRestoreSetError("SD activated readback differs")
            if (root / f"._{name}").exists() or (
                root / f"._.{name}.part"
            ).exists():
                raise StockRestoreSetError("SD restore AppleDouble cleanup failed")
    except BaseException:
        for item in activated:
            item.unlink(missing_ok=True)
        for name in staged:
            (root / f".{name}.part").unlink(missing_ok=True)
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        raise
    return tuple(staged)


def expected_live_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"RESTORE {preflight.physical_device} DCS-6100LHV2-A1 "
        "WRITE=mtd3,mtd2,mtd1 PROTECT=mtd0,mtd4,mtd5 ACTIVATION=last"
    )


def expected_bootstrap_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"RESTORE {preflight.physical_device} DCS-6100LHV2-A1 "
        "TRANSPORT=stock-uboot WRITE=mtd3,mtd2,mtd1 "
        "PROTECT=mtd0,mtd4,mtd5 ACTIVATION=last"
    )


def expected_retry_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"RETRY RESTORE {preflight.physical_device} DCS-6100LHV2-A1 "
        "WRITE=mtd3,mtd2,mtd1 PROTECT=mtd0,mtd4,mtd5 FROM=start"
    )


def expected_handoff_confirmation(preflight: MediaPreflight) -> str:
    return (
        f"HANDOFF {preflight.physical_device} DCS-6100LHV2-A1 "
        "STOCK-UBOOT-WRITE=mtd1,mtd2 LED=complete READBACK=not-proven"
    )


def _reject_matching_update_names(root: Path, allowed: set[str]) -> None:
    matches = {
        entry.name for entry in root.iterdir() if is_matching_update_filename(entry.name)
    }
    if matches != allowed:
        raise StockRestoreSetError("SD stock updater selector closure differs")


def stage_stock_restore_bootstrap_set(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> tuple[str, ...]:
    """Stage an inert stock-U-Boot package and exact restore inputs."""

    _check_media(root, preflight, confirmed_physical_device)
    _reject_matching_update_names(root, set())
    staged = {
        name: raw for name, raw in prepared.files.items() if name != AUTH_PRIVATE_NAME
    }
    reserved = {
        *staged,
        BOOTSTRAP_ACTIVE_NAME,
        AUTH_SD_NAME,
        RUN_SD_NAME,
        COMPLETE_SD_NAME,
        PASSIVE_DIR_NAME,
    }
    for name in reserved:
        for candidate in (
            root / name,
            *_status_sidecars(root, name),
            root / f"._.{name}.part",
        ):
            if candidate.exists() or candidate.is_symlink():
                raise StockRestoreSetError("SD restore reserved path already exists")
    activated: list[Path] = []
    try:
        for name, raw in staged.items():
            temporary = root / f".{name}.part"
            destination = root / name
            _write_exclusive(temporary, raw)
            if _read_regular(temporary, len(raw)) != raw:
                raise StockRestoreSetError("SD temporary readback differs")
            os.replace(temporary, destination)
            activated.append(destination)
        for name in staged:
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        _fsync_directory(root)
        for name, raw in staged.items():
            if _read_regular(root / name, len(raw)) != raw:
                raise StockRestoreSetError("SD activated readback differs")
            if (root / f"._{name}").exists() or (
                root / f"._.{name}.part"
            ).exists():
                raise StockRestoreSetError("SD restore AppleDouble cleanup failed")
        _reject_matching_update_names(root, set())
    except BaseException:
        for path in activated:
            path.unlink(missing_ok=True)
        for name in staged:
            (root / f".{name}.part").unlink(missing_ok=True)
            (root / f"._{name}").unlink(missing_ok=True)
            (root / f"._.{name}.part").unlink(missing_ok=True)
        raise
    return tuple(staged)


def authorize_stock_restore_bootstrap(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Create the one-use token, then activate the stock selector last."""

    _check_media(
        root,
        preflight,
        confirmed_physical_device,
        allowed_matching_filename=BOOTSTRAP_ACTIVE_NAME,
    )
    if confirmation != expected_bootstrap_confirmation(preflight):
        raise StockRestoreSetError("live restore confirmation differs")
    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    package = prepared.files[BOOTSTRAP_PRIVATE_NAME]
    active_exists = active.exists() or active.is_symlink()
    passive_exists = passive.exists() or passive.is_symlink()
    if active_exists == passive_exists:
        raise StockRestoreSetError("stock restore bootstrap activation state differs")
    _reject_matching_update_names(
        root, {BOOTSTRAP_ACTIVE_NAME} if active_exists else set()
    )
    for name, raw in prepared.files.items():
        if name in {AUTH_PRIVATE_NAME, BOOTSTRAP_PRIVATE_NAME}:
            continue
        if _read_regular(root / name, len(raw)) != raw:
            raise StockRestoreSetError("staged stock restore changed before arming")
    selected_package = active if active_exists else passive
    if _read_regular(selected_package, len(package)) != package:
        raise StockRestoreSetError("staged stock restore package changed before arming")
    for name in (RUN_SD_NAME, COMPLETE_SD_NAME):
        for item in (root / name, *_status_sidecars(root, name)):
            if item.exists() or item.is_symlink():
                raise StockRestoreSetError("live restore status exists")
    for sidecar in _status_sidecars(root, AUTH_SD_NAME):
        if sidecar.exists() or sidecar.is_symlink():
            raise StockRestoreSetError("live restore authorization sidecar exists")
    for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore bootstrap sidecar exists")
    authorization = root / AUTH_SD_NAME
    if authorization.exists() or authorization.is_symlink():
        if _read_regular(authorization, len(prepared.authorization)) != prepared.authorization:
            raise StockRestoreSetError("live restore authorization differs")
        if active_exists:
            return
    elif active_exists:
        raise StockRestoreSetError("active bootstrap lacks exact authorization")
    else:
        _activate_exact_status(root, AUTH_SD_NAME, prepared.authorization)

    _activate_bootstrap_selector(root, package)


def deactivate_stock_restore_bootstrap(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Deactivate the updater after its LED loop so NOR can boot the restorer."""

    _check_media(
        root,
        preflight,
        confirmed_physical_device,
        allowed_matching_filename=BOOTSTRAP_ACTIVE_NAME,
    )
    if confirmation != expected_handoff_confirmation(preflight):
        raise StockRestoreSetError("stock restore handoff confirmation differs")
    package = prepared.files[BOOTSTRAP_PRIVATE_NAME]
    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    active_exists = active.exists() or active.is_symlink()
    passive_exists = passive.exists() or passive.is_symlink()
    if active_exists == passive_exists:
        raise StockRestoreSetError("stock restore handoff package state differs")
    _reject_matching_update_names(
        root, {BOOTSTRAP_ACTIVE_NAME} if active_exists else set()
    )
    selected = active if active_exists else passive
    if _read_regular(selected, len(package)) != package:
        raise StockRestoreSetError("stock restore handoff package differs")
    if _read_regular(root / AUTH_SD_NAME, len(prepared.authorization)) != prepared.authorization:
        raise StockRestoreSetError("stock restore handoff authorization differs")
    for name, raw in prepared.files.items():
        if name in {AUTH_PRIVATE_NAME, BOOTSTRAP_PRIVATE_NAME}:
            continue
        if _read_regular(root / name, len(raw)) != raw:
            raise StockRestoreSetError("stock restore handoff artifact differs")
    complete = root / COMPLETE_SD_NAME
    if complete.exists() or complete.is_symlink():
        raise StockRestoreSetError("completed restore cannot enter bootstrap handoff")
    run = root / RUN_SD_NAME
    if run.exists() or run.is_symlink():
        if _read_regular(run, len(RUN_STATUS)) != RUN_STATUS:
            raise StockRestoreSetError("stock restore handoff run marker differs")
    for name in (AUTH_SD_NAME, RUN_SD_NAME, COMPLETE_SD_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore handoff sidecar exists")
    for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore handoff package sidecar exists")
    if not active_exists:
        return
    moved = False
    try:
        os.replace(active, passive)
        moved = True
        for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
            for sidecar in _status_sidecars(root, name):
                sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if _read_regular(passive, len(package)) != package:
            raise StockRestoreSetError("stock restore handoff readback differs")
        _reject_matching_update_names(root, set())
        if any(
            sidecar.exists()
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME)
            for sidecar in _status_sidecars(root, name)
        ):
            raise StockRestoreSetError("stock restore handoff sidecar cleanup failed")
    except BaseException:
        if moved and passive.exists() and not active.exists():
            os.replace(passive, active)
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
                for sidecar in _status_sidecars(root, name):
                    sidecar.unlink(missing_ok=True)
            _fsync_directory(root)
        raise


def reauthorize_interrupted_stock_restore(
    *,
    prepared: StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Authorize a full-from-start retry after RUN exists without OK."""

    _check_media(
        root,
        preflight,
        confirmed_physical_device,
        allowed_matching_filename=BOOTSTRAP_ACTIVE_NAME,
    )
    if confirmation != expected_retry_confirmation(preflight):
        raise StockRestoreSetError("live restore retry confirmation differs")
    package = prepared.files[BOOTSTRAP_PRIVATE_NAME]
    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    active_exists = active.exists() or active.is_symlink()
    passive_exists = passive.exists() or passive.is_symlink()
    if active_exists == passive_exists:
        raise StockRestoreSetError("interrupted restore package state differs")
    _reject_matching_update_names(
        root, {BOOTSTRAP_ACTIVE_NAME} if active_exists else set()
    )
    selected = active if active_exists else passive
    if _read_regular(selected, len(package)) != package:
        raise StockRestoreSetError("interrupted restore bootstrap differs")
    expected = {
        name: raw
        for name, raw in prepared.files.items()
        if name not in {AUTH_PRIVATE_NAME, BOOTSTRAP_PRIVATE_NAME}
    }
    for name, raw in expected.items():
        if _read_regular(root / name, len(raw)) != raw:
            raise StockRestoreSetError("interrupted restore artifact differs")
    if _read_regular(root / RUN_SD_NAME, len(RUN_STATUS)) != RUN_STATUS:
        raise StockRestoreSetError("interrupted restore run marker differs")
    for name in (COMPLETE_SD_NAME,):
        for item in (root / name, *_status_sidecars(root, name)):
            if item.exists() or item.is_symlink():
                raise StockRestoreSetError("interrupted restore is not retryable")
    for sidecar in _status_sidecars(root, AUTH_SD_NAME):
        if sidecar.exists() or sidecar.is_symlink():
            raise StockRestoreSetError("interrupted restore authorization sidecar exists")
    authorization = root / AUTH_SD_NAME
    if authorization.exists() or authorization.is_symlink():
        if _read_regular(authorization, len(prepared.authorization)) != prepared.authorization:
            raise StockRestoreSetError("restore retry authorization differs")
    elif active_exists:
        raise StockRestoreSetError("active retry bootstrap lacks authorization")
    else:
        _activate_exact_status(root, AUTH_SD_NAME, prepared.authorization)
    _activate_bootstrap_selector(root, package)


def authorize_stock_restore(
    *,
    prepared: StockRestoreRamSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
    confirmation: str,
) -> None:
    """Arm exactly one boot after a target- and write-set-bound confirmation."""

    _check_media(root, preflight, confirmed_physical_device)
    if confirmation != expected_live_confirmation(preflight):
        raise StockRestoreSetError("live restore confirmation differs")
    for name, raw in prepared.files.items():
        if name == AUTH_PRIVATE_NAME:
            continue
        item = root / name
        if item.is_symlink() or not item.is_file() or item.read_bytes() != raw:
            raise StockRestoreSetError("staged live restore set changed before arming")
    for name in (RUN_SD_NAME, COMPLETE_SD_NAME):
        for item in (root / name, *_status_sidecars(root, name)):
            if item.exists() or item.is_symlink():
                raise StockRestoreSetError("live restore status or authorization exists")
    _activate_exact_status(root, AUTH_SD_NAME, prepared.authorization)


def _status_sidecars(root: Path, name: str) -> tuple[Path, ...]:
    return (
        root / f".{name}.part",
        root / f"._{name}",
        root / f"._.{name}.part",
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _activate_exact_status(root: Path, name: str, raw: bytes) -> bool:
    """Atomically activate one exact marker or roll back this transaction."""

    destination = root / name
    temporary = root / f".{name}.part"
    if destination.exists() or destination.is_symlink():
        if _read_regular(destination, len(raw)) != raw:
            raise StockRestoreSetError("live restore status differs")
        return False
    for item in _status_sidecars(root, name):
        if item.exists() or item.is_symlink():
            raise StockRestoreSetError("live restore status sidecar exists")
    temporary_created = False
    activated = False
    try:
        temporary_created = True
        _write_exclusive(temporary, raw)
        if _read_regular(temporary, len(raw)) != raw:
            raise StockRestoreSetError("live restore status temporary readback differs")
        os.replace(temporary, destination)
        activated = True
        for sidecar in _status_sidecars(root, name):
            sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if _read_regular(destination, len(raw)) != raw:
            raise StockRestoreSetError("live restore status activation differs")
        if any(sidecar.exists() for sidecar in _status_sidecars(root, name)):
            raise StockRestoreSetError("live restore status sidecar cleanup failed")
    except BaseException as exc:
        try:
            if activated:
                destination.unlink(missing_ok=True)
            if temporary_created:
                temporary.unlink(missing_ok=True)
            for sidecar in _status_sidecars(root, name):
                sidecar.unlink(missing_ok=True)
            _fsync_directory(root)
            if destination.exists() or destination.is_symlink() or any(
                item.exists() or item.is_symlink()
                for item in _status_sidecars(root, name)
            ):
                raise StockRestoreSetError(
                    "live restore status rollback is uncertain"
                )
        except BaseException as rollback_exc:
            raise StockRestoreSetError(
                "live restore status rollback is uncertain"
            ) from rollback_exc
        raise exc
    return True


def _activate_bootstrap_selector(root: Path, package: bytes) -> bool:
    """Activate only the strict selector and restore the inert name on failure."""

    active = root / BOOTSTRAP_ACTIVE_NAME
    passive = root / BOOTSTRAP_PRIVATE_NAME
    if active.exists() or active.is_symlink():
        if passive.exists() or passive.is_symlink():
            raise StockRestoreSetError("stock restore bootstrap activation is ambiguous")
        if _read_regular(active, len(package)) != package:
            raise StockRestoreSetError("stock restore bootstrap activation differs")
        _reject_matching_update_names(root, {BOOTSTRAP_ACTIVE_NAME})
        return False
    if _read_regular(passive, len(package)) != package:
        raise StockRestoreSetError("stock restore bootstrap changed before activation")
    for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
        for sidecar in _status_sidecars(root, name):
            if sidecar.exists() or sidecar.is_symlink():
                raise StockRestoreSetError("stock restore bootstrap sidecar exists")
    moved = False
    try:
        os.replace(passive, active)
        moved = True
        for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
            for sidecar in _status_sidecars(root, name):
                sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if _read_regular(active, len(package)) != package:
            raise StockRestoreSetError("stock restore bootstrap activation differs")
        _reject_matching_update_names(root, {BOOTSTRAP_ACTIVE_NAME})
        if (
            selected_update_filename(entry.name for entry in root.iterdir())
            != BOOTSTRAP_ACTIVE_NAME
        ):
            raise StockRestoreSetError("stock restore bootstrap is not selected")
        if any(
            sidecar.exists()
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME)
            for sidecar in _status_sidecars(root, name)
        ):
            raise StockRestoreSetError("stock restore bootstrap sidecar cleanup failed")
    except BaseException as exc:
        try:
            if moved and active.exists() and not passive.exists():
                os.replace(active, passive)
            for name in (BOOTSTRAP_ACTIVE_NAME, BOOTSTRAP_PRIVATE_NAME):
                for sidecar in _status_sidecars(root, name):
                    sidecar.unlink(missing_ok=True)
            _fsync_directory(root)
            if (
                active.exists()
                or active.is_symlink()
                or _read_regular(passive, len(package)) != package
            ):
                raise StockRestoreSetError(
                    "stock restore bootstrap rollback is uncertain"
                )
            _reject_matching_update_names(root, set())
        except BaseException as rollback_exc:
            raise StockRestoreSetError(
                "stock restore bootstrap rollback is uncertain"
            ) from rollback_exc
        raise exc
    return True


def passivate_completed_stock_restore(
    *,
    prepared: StockRestoreRamSet | StockRestoreBootstrapSet,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> StockRestorePassivation:
    """Move one completed restore set out of the SD root without deleting it.

    The operation accepts only the terminal success state produced by the
    bounded restorer. It can resume after interruption because every expected
    file must exist either in the SD root or in the passive directory, never
    both. The one-use authorization must already be absent.
    """

    _check_media(root, preflight, confirmed_physical_device)
    passive = root / PASSIVE_DIR_NAME
    if passive.is_symlink():
        raise StockRestoreSetError("passive restore directory is a symlink")
    if passive.exists() and not passive.is_dir():
        raise StockRestoreSetError("passive restore path is not a directory")

    expected = {
        name: raw
        for name, raw in prepared.files.items()
        if name != AUTH_PRIVATE_NAME
    }
    if isinstance(prepared, StockRestoreBootstrapSet):
        _reject_matching_update_names(root, set())
    expected[RUN_SD_NAME] = RUN_STATUS
    expected[COMPLETE_SD_NAME] = COMPLETE_STATUS

    for candidate in (root / AUTH_SD_NAME, *_status_sidecars(root, AUTH_SD_NAME)):
        if candidate.exists() or candidate.is_symlink():
            raise StockRestoreSetError(
                "completed restore still has a live authorization"
            )
    for name in (*expected, PASSIVE_DIR_NAME):
        for candidate in _status_sidecars(root, name):
            if candidate.exists() or candidate.is_symlink():
                raise StockRestoreSetError("restore passivation sidecar exists")

    marker_existed = False
    if passive.exists():
        for name in (*expected, PASSIVE_MARKER_NAME):
            for candidate in _status_sidecars(passive, name):
                if candidate.exists() or candidate.is_symlink():
                    raise StockRestoreSetError(
                        "passive restore directory has a reserved sidecar"
                    )
        allowed = {*expected, PASSIVE_MARKER_NAME}
        if {entry.name for entry in passive.iterdir()} - allowed:
            raise StockRestoreSetError("passive restore directory has extra files")
        marker = passive / PASSIVE_MARKER_NAME
        if marker.exists() or marker.is_symlink():
            if _read_regular(marker, len(PASSIVE_STATUS)) != PASSIVE_STATUS:
                raise StockRestoreSetError("passive restore marker differs")
            marker_existed = True

    root_expected: list[str] = []
    for name, raw in expected.items():
        active = root / name
        archived = passive / name
        active_exists = active.exists() or active.is_symlink()
        archived_exists = archived.exists() or archived.is_symlink()
        if active_exists == archived_exists:
            raise StockRestoreSetError(
                "restore artifact must exist in exactly one passivation location"
            )
        selected = active if active_exists else archived
        if _read_regular(selected, len(raw)) != raw:
            raise StockRestoreSetError("restore artifact differs before passivation")
        if active_exists:
            root_expected.append(name)

    if not passive.exists():
        passive.mkdir(mode=0o700)
        for sidecar in _status_sidecars(root, PASSIVE_DIR_NAME):
            sidecar.unlink(missing_ok=True)
        _fsync_directory(root)
        if any(
            sidecar.exists()
            for sidecar in _status_sidecars(root, PASSIVE_DIR_NAME)
        ):
            raise StockRestoreSetError("passive directory sidecar cleanup failed")

    move_order = (
        RUN_SD_NAME,
        COMPLETE_SD_NAME,
        *(name for name in sorted(expected) if name not in {RUN_SD_NAME, COMPLETE_SD_NAME}),
    )
    moved: list[str] = []
    for name in move_order:
        if name not in root_expected:
            continue
        source = root / name
        destination = passive / name
        raw = expected[name]
        if _read_regular(source, len(raw)) != raw or destination.exists():
            raise StockRestoreSetError("restore artifact changed during passivation")
        os.replace(source, destination)
        for directory in (root, passive):
            for sidecar in _status_sidecars(directory, name):
                sidecar.unlink(missing_ok=True)
        _fsync_directory(passive)
        _fsync_directory(root)
        if _read_regular(destination, len(raw)) != raw:
            raise StockRestoreSetError("passive restore readback differs")
        if any(
            sidecar.exists()
            for directory in (root, passive)
            for sidecar in _status_sidecars(directory, name)
        ):
            raise StockRestoreSetError("passive restore sidecar cleanup failed")
        moved.append(name)

    marker = passive / PASSIVE_MARKER_NAME
    if not marker_existed:
        _write_exclusive(marker, PASSIVE_STATUS)
        for sidecar in _status_sidecars(passive, PASSIVE_MARKER_NAME):
            sidecar.unlink(missing_ok=True)
        _fsync_directory(passive)
        _fsync_directory(root)
    if _read_regular(marker, len(PASSIVE_STATUS)) != PASSIVE_STATUS:
        raise StockRestoreSetError("passive restore marker readback differs")
    if any(
        sidecar.exists()
        for sidecar in _status_sidecars(passive, PASSIVE_MARKER_NAME)
    ):
        raise StockRestoreSetError("passive restore marker sidecar cleanup failed")
    for name, raw in expected.items():
        if (root / name).exists() or (root / name).is_symlink():
            raise StockRestoreSetError("restore artifact remains active in SD root")
        if _read_regular(passive / name, len(raw)) != raw:
            raise StockRestoreSetError("passive restore closure differs")
        if any(
            sidecar.exists()
            for directory in (root, passive)
            for sidecar in _status_sidecars(directory, name)
        ):
            raise StockRestoreSetError("passive restore closure has a sidecar")

    reserved_root_names = {
        *expected,
        AUTH_SD_NAME,
        PASSIVE_DIR_NAME,
    }
    unrelated = sum(
        1 for entry in root.iterdir() if entry.name not in reserved_root_names
    )
    return StockRestorePassivation(
        already_passivated=marker_existed and not moved,
        moved_files=tuple(moved),
        preserved_unrelated_entries=unrelated,
    )
