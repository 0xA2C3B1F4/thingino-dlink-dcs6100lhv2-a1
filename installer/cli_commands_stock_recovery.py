"""Complete-backup and same-device stock-restore CLI implementations."""

from __future__ import annotations


def _prepare_live_ram(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, "_emit")
    prepare_live_ram_set = getattr(facade, "prepare_live_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    plan = prepare_live_ram_set(
        mode=arguments.mode,
        kernel=read_snapshot(arguments.kernel),
        linux_config=read_snapshot(arguments.linux_config),
        wrapper=read_snapshot(arguments.wrapper),
        output_dir=arguments.output_dir,
    )
    _emit(
        {
            "mode": plan.mode,
            "nor_writes": False,
            "ok": True,
            "protected_mtd": [0, 4, 5],
            "write_set": list(plan.write_set),
        }
    )


def _stage_live_ram(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, "_emit")
    inspect_live_ram_set = getattr(facade, "inspect_live_ram_set")
    load_media_preflight = getattr(facade, "load_media_preflight")
    read_snapshot = getattr(facade, "read_snapshot")
    stage_live_ram_set = getattr(facade, "stage_live_ram_set")
    plan = inspect_live_ram_set(
        output_dir=arguments.input_dir,
        linux_config=read_snapshot(arguments.linux_config),
    )
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    names = stage_live_ram_set(
        plan=plan,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "armed": False,
            "mode": plan.mode,
            "nor_writes": False,
            "ok": True,
            "staged_files": list(names),
            "write_set": list(plan.write_set),
        }
    )


def _run_live_ram(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, "_emit")
    inspect_live_ram_set = getattr(facade, "inspect_live_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    run_live_ram_plan = getattr(facade, "run_live_ram_plan")
    plan = inspect_live_ram_set(
        output_dir=arguments.input_dir,
        linux_config=read_snapshot(arguments.linux_config),
    )
    run_live_ram_plan(
        plan=plan,
        serial_device=arguments.serial_device,
        confirmed_serial_device=arguments.confirm_serial_device,
    )
    _emit(
        {
            "mode": plan.mode,
            "ok": True,
            "physical_completion_marker_seen": True,
            "write_set": list(plan.write_set),
        }
    )


def _unstage_live_ram(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, "_emit")
    inspect_live_ram_set = getattr(facade, "inspect_live_ram_set")
    load_media_preflight = getattr(facade, "load_media_preflight")
    read_snapshot = getattr(facade, "read_snapshot")
    unstage_live_ram_set = getattr(facade, "unstage_live_ram_set")
    plan = inspect_live_ram_set(
        output_dir=arguments.input_dir,
        linux_config=read_snapshot(arguments.linux_config),
    )
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    names = unstage_live_ram_set(
        plan=plan,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "mode": plan.mode,
            "nor_writes": False,
            "ok": True,
            "removed_files": list(names),
        }
    )


def _finalize_ram_backup(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, "_emit")
    capture = getattr(facade, "capture_complete_backup_from_ram_collector")
    decision = capture(
        collector_dir=arguments.collector_dir,
        output_dir=arguments.output_dir,
        confirmed_output_dir=arguments.confirm_output_dir,
    )
    _emit(
        {
            "duplicate_backup_accepted": decision.duplicate_partitions_accepted,
            "full_flash_reconstruction_accepted": (
                decision.full_flash_reconstruction_accepted
            ),
            "nor_writes": False,
            "ok": True,
            "partition_count": decision.partition_count,
            "private_values_printed": False,
        }
    )


def _validate_read_only_protected(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    validate = getattr(facade, "validate_protected_collector_output")
    decision = validate(
        collector_output_dir=arguments.collector_output_dir,
        recovery_dir=arguments.recovery_dir,
    )
    _emit(
        {
            "duplicate_recovery_accepted": decision.recovery_images == 2,
            "full_flash_reconstruction_accepted": True,
            "mode": "protected-readback",
            "nor_writes": False,
            "ok": True,
            "protected_mtd": list(decision.preserved_mtd),
            "same_device_binding_accepted": True,
            "secret_data_exposed": False,
            "write_set": [],
        }
    )


def _validate_complete_backup(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, "_emit")
    validate_complete_backup = getattr(facade, "validate_complete_backup")
    decision = validate_complete_backup(arguments.recovery_dir)
    _emit(
        {
            "duplicate_backup_accepted": decision.duplicate_partitions_accepted,
            "full_flash_reconstruction_accepted": (
                decision.full_flash_reconstruction_accepted
            ),
            "nor_writes": False,
            "ok": True,
            "partition_count": decision.partition_count,
            "private_values_exposed": False,
            "recovery_images": decision.recovery_images,
        }
    )


def _prepare_same_device_stock_restore(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    prepare = getattr(facade, "prepare_same_device_stock_restore")
    plan = prepare(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        output_dir=arguments.output_dir,
    )
    _emit(
        {
            "duplicate_backup_accepted": True,
            "full_flash_reconstruction_accepted": True,
            "nor_writes": False,
            "ok": True,
            "physical_restore_proven": False,
            "restore_status": "prepared-only",
            "same_device_binding_accepted": True,
            "safe_next_action": "inspect-restore-package",
            "write_set": list(plan.write_set),
        }
    )


def _inspect_same_device_stock_restore(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    inspect = getattr(facade, "inspect_same_device_stock_restore")
    plan = inspect(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        output_dir=arguments.output_dir,
    )
    _emit(
        {
            "duplicate_backup_accepted": True,
            "full_flash_reconstruction_accepted": True,
            "nor_writes": False,
            "ok": True,
            "physical_restore_proven": False,
            "restore_status": "prepared-only-not-physically-proven",
            "same_device_binding_accepted": True,
            "safe_next_action": "stop",
            "write_set": list(plan.write_set),
        }
    )


def _prepare_stock_restore_live(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    prepare = getattr(facade, "prepare_stock_restore_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
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
    _emit(
        {
            "armed": False,
            "nor_writes": False,
            "ok": True,
            "physical_restore_proven": False,
            "protected_mtd": [0, 4, 5],
            "same_device_binding_accepted": True,
            "write_set": list(prepared.live_plan.write_set),
        }
    )


def _load_stock_live(facade: object, arguments: argparse.Namespace):
    inspect = getattr(facade, "inspect_stock_restore_ram_set")
    read_snapshot = getattr(facade, "read_snapshot")
    return inspect(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
        restore_output_dir=arguments.restore_output_dir,
        linux_config=read_snapshot(arguments.linux_config),
        output_dir=arguments.input_dir,
    )


def _stage_stock_restore_live(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    load_media_preflight = getattr(facade, "load_media_preflight")
    stage = getattr(facade, "stage_stock_restore_ram_set")
    prepared = _load_stock_live(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    names = stage(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "armed": False,
            "nor_writes": False,
            "ok": True,
            "protected_mtd": [0, 4, 5],
            "staged_file_count": len(names),
            "write_set": [3, 2, 1],
        }
    )


def _authorize_stock_restore_live(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    authorize = getattr(facade, "authorize_stock_restore")
    load_media_preflight = getattr(facade, "load_media_preflight")
    prepared = _load_stock_live(facade, arguments)
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
    _emit(
        {
            "armed": True,
            "nor_writes": False,
            "ok": True,
            "physical_restore_proven": False,
            "protected_mtd": [0, 4, 5],
            "safe_next_action": "move the confirmed SD card to the confirmed camera",
            "write_set": [3, 2, 1],
        }
    )


def _show_stock_restore_confirmation(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    expected = getattr(facade, "expected_live_confirmation")
    load_media_preflight = getattr(facade, "load_media_preflight")
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    _emit(
        {
            "confirmation": expected(preflight),
            "nor_writes": False,
            "ok": True,
            "protected_mtd": [0, 4, 5],
            "write_set": [3, 2, 1],
        }
    )


def _passivate_stock_restore_live(
    facade: object, arguments: argparse.Namespace
) -> None:
    _emit = getattr(facade, "_emit")
    load_media_preflight = getattr(facade, "load_media_preflight")
    passivate = getattr(facade, "passivate_completed_stock_restore")
    prepared = _load_stock_live(facade, arguments)
    preflight = load_media_preflight(
        arguments.media_preflight, expected_root=arguments.mount_root
    )
    result = passivate(
        prepared=prepared,
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "active_restore_files": 0,
            "already_passivated": result.already_passivated,
            "completion_marker_accepted": True,
            "moved_file_count": len(result.moved_files),
            "nor_writes": False,
            "ok": True,
            "preserved_unrelated_entries": result.preserved_unrelated_entries,
            "safe_next_action": "inspect the passive card before reusing it",
            "write_set": [],
        }
    )
