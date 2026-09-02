"""Guided complete-backup and same-device stock-restore command family."""

from __future__ import annotations

import tempfile


def _result(
    *,
    duplicate: bool,
    reconstructed: bool,
    same_device: bool,
    restore_status: str,
    safe_next_action: str,
    physical_restore_proven: bool = False,
) -> dict[str, object]:
    return {
        "duplicate_backup_accepted": duplicate,
        "full_flash_reconstruction_accepted": reconstructed,
        "same_device_binding_accepted": same_device,
        "physical_restore_proven": physical_restore_proven,
        "restore_status": restore_status,
        "safe_next_action": safe_next_action,
        "write_set": [3, 2, 1],
    }


def _stock_backup_prepare(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    Path = getattr(facade, "Path")
    _document = getattr(facade, "_document")
    build_collector_root = getattr(facade, "build_collector_root")
    build_ramdisk_uimage = getattr(facade, "build_ramdisk_uimage")
    load_media_preflight = getattr(facade, "load_media_preflight")
    os = getattr(facade, "os")
    prepare_live_ram_set = getattr(facade, "prepare_live_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    stage_live_ram_set = getattr(facade, "stage_live_ram_set")
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    with tempfile.TemporaryDirectory(
        prefix="dcs6100-complete-backup-", dir=os.environ.get("TMPDIR")
    ) as name:
        rootfs_path = Path(name) / "collector.squashfs"
        build_collector_root(
            mmc_module=read_snapshot(arguments.mmc_module),
            output=rootfs_path,
            clang=arguments.clang,
            lld=arguments.lld,
            mksquashfs=arguments.mksquashfs,
            unsquashfs=arguments.unsquashfs,
            capture_mode="complete-backup",
        )
        wrapper = build_ramdisk_uimage(
            rootfs_path.read_bytes(), name="DCS6100 complete backup RAM"
        )
    plan = prepare_live_ram_set(
        mode="collector",
        kernel=read_snapshot(arguments.kernel),
        linux_config=read_snapshot(arguments.linux_config),
        wrapper=wrapper,
        output_dir=arguments.output_dir,
    )
    staged = stage_live_ram_set(
        plan=plan,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    return _document(
        "stock-recovery backup-prepare",
        ok=True,
        phase="read-only-backup-prepared",
        next_command="thingino-dlink stock-recovery backup-capture",
        result=_result(
            duplicate=False,
            reconstructed=False,
            same_device=False,
            restore_status="read-only-collector-staged-unarmed",
            safe_next_action="move-confirmed-sd-to-camera-then-run-backup-capture",
        )
        | {"staged_file_count": len(staged), "write_set": []},
    )


def _stock_backup_capture(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    UserInstallerError = getattr(facade, "UserInstallerError")
    _document = getattr(facade, "_document")
    inspect_live_ram_set = getattr(facade, "inspect_live_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    run_live_ram_plan = getattr(facade, "run_live_ram_plan")
    plan = inspect_live_ram_set(
        output_dir=arguments.input_dir,
        linux_config=read_snapshot(arguments.linux_config),
    )
    if plan.mode != "collector" or plan.write_set:
        raise UserInstallerError("backup capture is not an all-read-only RAM set")
    run_live_ram_plan(
        plan=plan,
        serial_device=arguments.serial_device,
        confirmed_serial_device=arguments.confirm_serial_device,
    )
    return _document(
        "stock-recovery backup-capture",
        ok=True,
        phase="read-only-backup-captured",
        next_command="thingino-dlink stock-recovery backup-validate",
        result=_result(
            duplicate=False,
            reconstructed=False,
            same_device=False,
            restore_status="captured-awaiting-host-validation",
            safe_next_action="move-sd-to-host-and-finalize-private-backup",
        )
        | {"write_set": []},
    )


def _stock_backup_validate(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    UserInstallerError = getattr(facade, "UserInstallerError")
    _document = getattr(facade, "_document")
    capture = getattr(facade, "capture_complete_backup_from_ram_collector")
    validate_complete_backup = getattr(facade, "validate_complete_backup")
    if arguments.collector_dir is not None:
        if arguments.output_dir is None or arguments.confirm_output_dir is None:
            raise UserInstallerError(
                "collector validation requires --output-dir and --confirm-output-dir"
            )
        decision = capture(
            collector_dir=arguments.collector_dir,
            output_dir=arguments.output_dir,
            confirmed_output_dir=arguments.confirm_output_dir,
        )
    elif arguments.recovery_dir is not None:
        decision = validate_complete_backup(arguments.recovery_dir)
    else:
        raise UserInstallerError(
            "backup validation requires --recovery-dir or --collector-dir"
        )
    return _document(
        "stock-recovery backup-validate",
        ok=True,
        phase="private-backup-validated",
        next_command="thingino-dlink stock-recovery restore-prepare",
        result=_result(
            duplicate=decision.duplicate_partitions_accepted,
            reconstructed=decision.full_flash_reconstruction_accepted,
            same_device=False,
            restore_status="backup-only",
            safe_next_action="collect-current-read-only-mtd0-mtd4-mtd5",
        ),
    )


def _stock_restore_prepare(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    prepare = getattr(facade, "prepare_same_device_stock_restore")
    plan = prepare(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        output_dir=arguments.output_dir,
    )
    return _document(
        "stock-recovery restore-prepare",
        ok=True,
        phase="stock-restore-prepared-only",
        next_command="thingino-dlink stock-recovery restore-inspect",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="prepared-only",
            safe_next_action="inspect-restore-package",
        )
        | {"write_set": list(plan.write_set)},
    )


def _stock_restore_inspect(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    inspect = getattr(facade, "inspect_same_device_stock_restore")
    plan = inspect(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        output_dir=arguments.output_dir,
    )
    return _document(
        "stock-recovery restore-inspect",
        ok=True,
        phase="stock-restore-package-inspected",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="prepared-only-not-physically-proven",
            safe_next_action="prepare-unarmed-live-restore-set",
        )
        | {"write_set": list(plan.write_set)},
    )


def _stock_live_prepare(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    load_media_preflight = getattr(facade, "load_media_preflight")
    prepare = getattr(facade, "prepare_stock_restore_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    stage = getattr(facade, "stage_stock_restore_ram_set")
    prepared = prepare(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        restore_output_dir=arguments.restore_output_dir,
        kernel=read_snapshot(arguments.kernel),
        linux_config=read_snapshot(arguments.linux_config),
        mmc_module=read_snapshot(arguments.mmc_module),
        output_dir=arguments.output_dir,
        clang=arguments.clang,
        lld=arguments.lld,
        mksquashfs=arguments.mksquashfs,
        unsquashfs=arguments.unsquashfs,
    )
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    staged = stage(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    return _document(
        "stock-recovery live-prepare",
        ok=True,
        phase="live-stock-restore-prepared-unarmed",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="prepared-and-staged-unarmed",
            safe_next_action="inspect-confirmation-then-authorize-exact-sd",
        )
        | {
            "armed": False,
            "staged_file_count": len(staged),
            "write_set": list(prepared.live_plan.write_set),
        },
    )


def _load_live(facade: object, arguments: argparse.Namespace):
    inspect = getattr(facade, "inspect_stock_restore_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    return inspect(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        restore_output_dir=arguments.restore_output_dir,
        linux_config=read_snapshot(arguments.linux_config),
        output_dir=arguments.input_dir,
    )


def _stock_live_authorize(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    authorize = getattr(facade, "authorize_stock_restore")
    expected = getattr(facade, "expected_live_confirmation")
    load_media_preflight = getattr(facade, "load_media_preflight")
    prepared = _load_live(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    authorize(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
        confirmation=arguments.confirm_live_restore,
    )
    return _document(
        "stock-recovery live-authorize",
        ok=True,
        phase="live-stock-restore-armed",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="armed-not-executed",
            safe_next_action="move-confirmed-sd-to-camera-and-run-live-restore",
        )
        | {
            "armed": True,
            "confirmation_contract": expected(preflight),
        },
    )


def _stock_live_restore(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    run_live_ram_plan = getattr(facade, "run_live_ram_plan")
    prepared = _load_live(facade, arguments)
    run_live_ram_plan(
        plan=prepared.live_plan,
        serial_device=arguments.serial_device,
        confirmed_serial_device=arguments.confirm_serial_device,
    )
    return _document(
        "stock-recovery live-restore",
        ok=True,
        phase="live-stock-restore-physical-readback-proven",
        written_mtd=[3, 2, 1],
        read_back_verified=True,
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="physically-restored-and-read-back",
            safe_next_action="power-off-remove-sd-then-boot-stock",
            physical_restore_proven=True,
        ),
    )


def _stock_live_passivate(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    load_media_preflight = getattr(facade, "load_media_preflight")
    passivate = getattr(facade, "passivate_completed_stock_restore")
    prepared = _load_live(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    passivation = passivate(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    return _document(
        "stock-recovery live-passivate",
        ok=True,
        phase="completed-stock-restore-card-passivated",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="completion-marker-accepted-card-passivated",
            safe_next_action="inspect-or-prepare-the-passive-sd-on-the-host",
        )
        | {
            "active_restore_files": 0,
            "already_passivated": passivation.already_passivated,
            "completion_marker_accepted": True,
            "moved_file_count": len(passivation.moved_files),
            "preserved_unrelated_entries": passivation.preserved_unrelated_entries,
            "write_set": [],
        },
    )


def _load_sd(facade: object, arguments: argparse.Namespace):
    inspect = getattr(facade, "inspect_stock_restore_bootstrap_set")
    read_snapshot = getattr(facade, "read_snapshot")
    return inspect(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        restore_output_dir=arguments.restore_output_dir,
        linux_config=read_snapshot(arguments.linux_config),
        output_dir=arguments.input_dir,
    )


def _stock_sd_prepare(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    load_media_preflight = getattr(facade, "load_media_preflight")
    prepare = getattr(facade, "prepare_stock_restore_bootstrap_set")
    read_snapshot = getattr(facade, "read_snapshot")
    stage = getattr(facade, "stage_stock_restore_bootstrap_set")
    prepared = prepare(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        restore_output_dir=arguments.restore_output_dir,
        kernel=read_snapshot(arguments.kernel),
        linux_config=read_snapshot(arguments.linux_config),
        mmc_module=read_snapshot(arguments.mmc_module),
        output_dir=arguments.output_dir,
        clang=arguments.clang,
        lld=arguments.lld,
        mksquashfs=arguments.mksquashfs,
        unsquashfs=arguments.unsquashfs,
    )
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    staged = stage(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    return _document(
        "stock-recovery sd-prepare",
        ok=True,
        phase="uartless-stock-restore-staged-unarmed",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="prepared-and-staged-unarmed",
            safe_next_action="review-exact-confirmation-before-sd-authorize",
        )
        | {
            "armed": False,
            "nor_writes": False,
            "staged_file_count": len(staged),
            "transport": "stock-uboot-strict-mtd1-mtd2-bootstrap",
            "transport_write_set": [1, 2],
        },
    )


def _stock_sd_authorize(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    authorize = getattr(facade, "authorize_stock_restore_bootstrap")
    expected = getattr(facade, "expected_bootstrap_confirmation")
    load_media_preflight = getattr(facade, "load_media_preflight")
    prepared = _load_sd(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    authorize(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
        confirmation=arguments.confirm_live_restore,
    )
    return _document(
        "stock-recovery sd-authorize",
        ok=True,
        phase="uartless-stock-restore-card-armed",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="armed-not-executed",
            safe_next_action="run-stock-uboot-update-then-return-sd-for-handoff",
        )
        | {
            "armed": True,
            "confirmation_contract": expected(preflight),
            "nor_writes": False,
            "physical_restore_proven": False,
            "transport_write_set": [1, 2],
        },
    )


def _stock_sd_confirmation(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    expected_by_phase = {
        "authorize": getattr(facade, "expected_bootstrap_confirmation"),
        "handoff": getattr(facade, "expected_handoff_confirmation"),
        "retry": getattr(facade, "expected_retry_confirmation"),
    }
    load_media_preflight = getattr(facade, "load_media_preflight")
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    return _document(
        "stock-recovery sd-confirmation",
        ok=True,
        phase="uartless-stock-restore-confirmation-ready",
        result=_result(
            duplicate=False,
            reconstructed=False,
            same_device=False,
            restore_status="confirmation-template-only-unarmed",
            safe_next_action="run-sd-authorize-with-this-exact-confirmation",
        )
        | {
            "armed": False,
            "confirmation_contract": expected_by_phase[arguments.phase](preflight),
            "confirmation_phase": arguments.phase,
            "nor_writes": False,
            "physical_restore_proven": False,
            "transport_write_set": [1, 2],
            "validation_scope": "media-confirmation-template-only",
        },
    )


def _stock_sd_handoff(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    deactivate = getattr(facade, "deactivate_stock_restore_bootstrap")
    expected = getattr(facade, "expected_handoff_confirmation")
    load_media_preflight = getattr(facade, "load_media_preflight")
    prepared = _load_sd(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    deactivate(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
        confirmation=arguments.confirm_stock_uboot_handoff,
    )
    return _document(
        "stock-recovery sd-handoff",
        ok=True,
        phase="stock-uboot-bootstrap-passivated-for-restorer-boot",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="bootstrap-transport-written-not-read-back",
            safe_next_action="power-off-camera-insert-confirmed-sd-then-power-on-restorer",
        )
        | {
            "armed": True,
            "confirmation_contract": expected(preflight),
            "nor_writes": False,
            "physical_restore_proven": False,
            "stock_uboot_readback_proven": False,
            "transport_selector_active": False,
            "transport_write_set": [1, 2],
        },
    )


def _stock_sd_retry(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    expected = getattr(facade, "expected_retry_confirmation")
    load_media_preflight = getattr(facade, "load_media_preflight")
    reauthorize = getattr(facade, "reauthorize_interrupted_stock_restore")
    prepared = _load_sd(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    reauthorize(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
        confirmation=arguments.confirm_live_restore_retry,
    )
    return _document(
        "stock-recovery sd-retry",
        ok=True,
        phase="interrupted-stock-restore-retry-armed",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="retry-armed-from-start",
            safe_next_action="run-stock-uboot-update-then-return-sd-for-handoff",
        )
        | {
            "armed": True,
            "confirmation_contract": expected(preflight),
            "nor_writes": False,
            "physical_restore_proven": False,
            "transport_write_set": [1, 2],
        },
    )


def _stock_sd_passivate(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    load_media_preflight = getattr(facade, "load_media_preflight")
    passivate = getattr(facade, "passivate_completed_stock_restore")
    prepared = _load_sd(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    result = passivate(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    return _document(
        "stock-recovery sd-passivate",
        ok=True,
        phase="completed-uartless-stock-restore-card-passivated",
        result=_result(
            duplicate=True,
            reconstructed=True,
            same_device=True,
            restore_status="completion-marker-accepted-card-passivated",
            safe_next_action="boot-without-sd-then-verify-stock-runtime",
        )
        | {
            "active_restore_files": 0,
            "already_passivated": result.already_passivated,
            "completion_marker_accepted": True,
            "moved_file_count": len(result.moved_files),
            "nor_writes": False,
            "physical_restore_proven": False,
            "preserved_unrelated_entries": result.preserved_unrelated_entries,
            "write_set": [],
        },
    )
