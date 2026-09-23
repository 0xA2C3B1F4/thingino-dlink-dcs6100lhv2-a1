"""Private preparation and exact SD staging for live stock restore."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import tempfile

from installer.fake_mtd import StockRestoreImages
from installer.functional_stock_restore import FunctionalStockPlan, FunctionalStockSelection
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

# Keep the set API importable here. Transaction implementations import their
# dependencies directly and do not dispatch through this facade.
from .contract import (
    PAYLOAD_NAMES,
    AUTH_PRIVATE_NAME,
    AUTH_SD_NAME,
    RUN_SD_NAME,
    COMPLETE_SD_NAME,
    MANIFEST_NAME,
    PASSIVE_DIR_NAME,
    PASSIVE_MARKER_NAME,
    RUN_STATUS,
    COMPLETE_STATUS,
    PASSIVE_STATUS,
    BOOTSTRAP_PRIVATE_NAME,
    BOOTSTRAP_ACTIVE_NAME,
    BOOTSTRAP_MANIFEST_NAME,
    StockRestoreSetError,
    StockRestoreRamSet,
    StockRestorePassivation,
    StockRestoreBootstrapSet,
)
from .file_io import (
    _write_exclusive,
    _read_regular,
    _read_bounded_regular,
    _status_sidecars,
    _fsync_directory,
)
from .media_policy import (
    _check_media,
    _reject_matching_update_names,
)
from .staging import (
    stage_stock_restore_ram_set,
    stage_stock_restore_bootstrap_set,
)
from .activation import (
    expected_live_confirmation,
    expected_bootstrap_confirmation,
    expected_retry_confirmation,
    expected_handoff_confirmation,
    authorize_stock_restore_bootstrap,
    deactivate_stock_restore_bootstrap,
    reauthorize_interrupted_stock_restore,
    authorize_stock_restore,
    _activate_exact_status,
    _activate_bootstrap_selector,
)
from .passivation import (
    passivate_completed_stock_restore,
)


def _require_authorization(raw: bytes) -> bytes:
    if len(raw) != 32:
        raise StockRestoreSetError("live restore authorization has the wrong size")
    return raw


def _images(plan: SameDeviceStockRestorePlan | FunctionalStockPlan) -> StockRestoreImages:
    result = StockRestoreImages(
        mtd1=plan.mtd1,
        mtd2=plan.mtd2,
        mtd3=plan.mtd3,
        protected=plan.protected,
        write_set=plan.write_set,
    )
    result.validate()
    return result


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
    functional_selection: FunctionalStockSelection | None = None,
) -> StockRestoreBootstrapSet:
    """Build one unarmed, no-UART stock-restorer SD transport."""

    if output_dir.exists() or output_dir.is_symlink():
        raise StockRestoreSetError("refusing to overwrite a stock restore bootstrap")
    plan = _bootstrap_restore_plan(
        recovery_dir, preserved_readback_dir, restore_output_dir, functional_selection,
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
        if isinstance(plan, FunctionalStockPlan):
            manifest["purpose"] = "functional-stock-uartless-bootstrap-private"
            manifest["origin"] = plan.private_origin()
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
    functional_selection: FunctionalStockSelection | None = None,
) -> StockRestoreBootstrapSet:
    plan = _bootstrap_restore_plan(
        recovery_dir, preserved_readback_dir, restore_output_dir, functional_selection,
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
    expected_origin = plan.private_origin() if isinstance(plan, FunctionalStockPlan) else None
    expected_purpose = (
        "functional-stock-uartless-bootstrap-private" if expected_origin is not None
        else "same-device-stock-1.02.02-uartless-bootstrap-private"
    )
    if not isinstance(manifest, dict) or (
        manifest.get("purpose") != expected_purpose
        or manifest.get("origin") != expected_origin
    ):
        raise StockRestoreSetError("stock restore input provenance differs")
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


def _bootstrap_restore_plan(
    recovery_dir: Path, preserved_readback_dir: Path, restore_output_dir: Path,
    selection: FunctionalStockSelection | None,
) -> SameDeviceStockRestorePlan | FunctionalStockPlan:
    if selection is not None:
        if not isinstance(selection, FunctionalStockSelection):
            raise StockRestoreSetError("functional stock selection is invalid")
        return selection.validate(
            recovery_dir=recovery_dir, preserved_readback_dir=preserved_readback_dir,
        )
    return inspect_same_device_stock_restore(
        recovery_dir=recovery_dir, preserved_readback_dir=preserved_readback_dir,
        output_dir=restore_output_dir,
    )
