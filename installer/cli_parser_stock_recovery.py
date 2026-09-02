"""Register complete-backup and same-device stock-restore commands."""

from __future__ import annotations


def register_stock_recovery_commands(facade: object, commands: object) -> None:
    Path = getattr(facade, "Path")
    finalize_backup = getattr(facade, "_finalize_ram_backup")
    inspect_restore = getattr(facade, "_inspect_same_device_stock_restore")
    prepare_live = getattr(facade, "_prepare_live_ram")
    prepare_restore = getattr(facade, "_prepare_same_device_stock_restore")
    prepare_restore_live = getattr(facade, "_prepare_stock_restore_live")
    run_live = getattr(facade, "_run_live_ram")
    show_confirmation = getattr(facade, "_show_stock_restore_confirmation")
    passivate_restore_live = getattr(facade, "_passivate_stock_restore_live")
    stage_live = getattr(facade, "_stage_live_ram")
    stage_restore_live = getattr(facade, "_stage_stock_restore_live")
    unstage_live = getattr(facade, "_unstage_live_ram")
    authorize_restore_live = getattr(facade, "_authorize_stock_restore_live")
    validate_backup = getattr(facade, "_validate_complete_backup")
    validate_protected = getattr(facade, "_validate_read_only_protected")

    live_prepare = commands.add_parser("prepare-live-ram-set")
    live_prepare.add_argument(
        "--mode", choices=("collector", "stock-restore"), required=True
    )
    live_prepare.add_argument("--kernel", type=Path, required=True)
    live_prepare.add_argument("--linux-config", type=Path, required=True)
    live_prepare.add_argument("--wrapper", type=Path, required=True)
    live_prepare.add_argument("--output-dir", type=Path, required=True)
    live_prepare.set_defaults(handler=prepare_live)

    live_stage = commands.add_parser("stage-live-ram-set")
    live_stage.add_argument("--input-dir", type=Path, required=True)
    live_stage.add_argument("--linux-config", type=Path, required=True)
    live_stage.add_argument("--mount-root", type=Path, required=True)
    live_stage.add_argument("--media-preflight", type=Path, required=True)
    live_stage.add_argument("--confirm-physical-device", required=True)
    live_stage.set_defaults(handler=stage_live)

    live_unstage = commands.add_parser("unstage-live-ram-set")
    live_unstage.add_argument("--input-dir", type=Path, required=True)
    live_unstage.add_argument("--linux-config", type=Path, required=True)
    live_unstage.add_argument("--mount-root", type=Path, required=True)
    live_unstage.add_argument("--media-preflight", type=Path, required=True)
    live_unstage.add_argument("--confirm-physical-device", required=True)
    live_unstage.set_defaults(handler=unstage_live)

    live_run = commands.add_parser("run-live-ram-set")
    live_run.add_argument("--input-dir", type=Path, required=True)
    live_run.add_argument("--linux-config", type=Path, required=True)
    live_run.add_argument("--serial-device", required=True)
    live_run.add_argument("--confirm-serial-device", required=True)
    live_run.set_defaults(handler=run_live)

    backup_finalize = commands.add_parser("finalize-ram-complete-backup")
    backup_finalize.add_argument("--collector-dir", type=Path, required=True)
    backup_finalize.add_argument("--output-dir", type=Path, required=True)
    backup_finalize.add_argument("--confirm-output-dir", type=Path, required=True)
    backup_finalize.set_defaults(handler=finalize_backup)

    protected_output = commands.add_parser("validate-read-only-protected-output")
    protected_output.add_argument(
        "--collector-output-dir", type=Path, required=True
    )
    protected_output.add_argument("--recovery-dir", type=Path, required=True)
    protected_output.set_defaults(handler=validate_protected)

    complete_backup = commands.add_parser("validate-complete-private-backup")
    complete_backup.add_argument("--recovery-dir", type=Path, required=True)
    complete_backup.set_defaults(handler=validate_backup)

    stock_prepare = commands.add_parser("prepare-same-device-stock-restore")
    stock_prepare.add_argument("--recovery-dir", type=Path, required=True)
    stock_prepare.add_argument(
        "--preserved-readback-dir", type=Path, required=True
    )
    stock_prepare.add_argument("--output-dir", type=Path, required=True)
    stock_prepare.set_defaults(handler=prepare_restore)

    stock_inspect = commands.add_parser("inspect-same-device-stock-restore")
    stock_inspect.add_argument("--recovery-dir", type=Path, required=True)
    stock_inspect.add_argument(
        "--preserved-readback-dir", type=Path, required=True
    )
    stock_inspect.add_argument("--output-dir", type=Path, required=True)
    stock_inspect.set_defaults(handler=inspect_restore)

    stock_live_prepare = commands.add_parser("prepare-stock-restore-live-set")
    stock_live_prepare.add_argument("--recovery-dir", type=Path, required=True)
    stock_live_prepare.add_argument(
        "--preserved-readback-dir", type=Path, required=True
    )
    stock_live_prepare.add_argument(
        "--restore-output-dir", type=Path, required=True
    )
    stock_live_prepare.add_argument("--kernel", type=Path, required=True)
    stock_live_prepare.add_argument("--linux-config", type=Path, required=True)
    stock_live_prepare.add_argument("--mmc-module", type=Path, required=True)
    stock_live_prepare.add_argument("--output-dir", type=Path, required=True)
    stock_live_prepare.add_argument("--clang", type=Path)
    stock_live_prepare.add_argument("--lld", type=Path)
    stock_live_prepare.add_argument("--mksquashfs", type=Path)
    stock_live_prepare.add_argument("--unsquashfs", type=Path)
    stock_live_prepare.set_defaults(handler=prepare_restore_live)

    stock_live_stage = commands.add_parser("stage-stock-restore-live-set")
    stock_live_authorize = commands.add_parser("authorize-stock-restore-live-set")
    for live_command in (stock_live_stage, stock_live_authorize):
        live_command.add_argument("--recovery-dir", type=Path, required=True)
        live_command.add_argument(
            "--preserved-readback-dir", type=Path, required=True
        )
        live_command.add_argument(
            "--restore-output-dir", type=Path, required=True
        )
        live_command.add_argument("--linux-config", type=Path, required=True)
        live_command.add_argument("--input-dir", type=Path, required=True)
        live_command.add_argument("--mount-root", type=Path, required=True)
        live_command.add_argument("--media-preflight", type=Path, required=True)
        live_command.add_argument("--confirm-physical-device", required=True)
    stock_live_stage.set_defaults(handler=stage_restore_live)
    stock_live_authorize.add_argument("--confirm-live-restore", required=True)
    stock_live_authorize.set_defaults(handler=authorize_restore_live)

    stock_confirmation = commands.add_parser("show-stock-restore-confirmation")
    stock_confirmation.add_argument("--mount-root", type=Path, required=True)
    stock_confirmation.add_argument("--media-preflight", type=Path, required=True)
    stock_confirmation.set_defaults(handler=show_confirmation)

    stock_passivate = commands.add_parser(
        "passivate-completed-stock-restore"
    )
    stock_passivate.add_argument("--recovery-dir", type=Path, required=True)
    stock_passivate.add_argument(
        "--preserved-readback-dir", type=Path, required=True
    )
    stock_passivate.add_argument(
        "--restore-output-dir", type=Path, required=True
    )
    stock_passivate.add_argument("--linux-config", type=Path, required=True)
    stock_passivate.add_argument("--input-dir", type=Path, required=True)
    stock_passivate.add_argument("--mount-root", type=Path, required=True)
    stock_passivate.add_argument("--media-preflight", type=Path, required=True)
    stock_passivate.add_argument("--confirm-physical-device", required=True)
    stock_passivate.set_defaults(handler=passivate_restore_live)
