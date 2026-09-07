"""Machine-readable, host-only command line for installer artifacts."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import sys
from pathlib import Path

from .artifacts import ArtifactError, validate_squashfs, validate_stage1_images
from .collector.build import CollectorBuildError, build_collector_root
from .collector.kernel import (
    CollectorKernelError,
    render_collector_kernel_fragment,
    validate_collector_kernel,
)
from .collector.output import (
    CollectorOutputError,
    validate_collector_output,
    validate_protected_collector_output,
)
from .development_install import (
    DevelopmentInstallError,
    complete_personal_install,
)
from .final_bundle import (
    BundleError,
    build_final_bundle,
    render_final_kernel_fragment,
    validate_final_bundle,
)
from .final_root import FinalRootError, prepare_from_private_directory
from .full_backup import (
    FullBackupError,
    capture_complete_backup_from_ram_collector,
    validate_complete_backup,
)
from .live_ram import (
    LiveRamError,
    inspect_live_ram_set,
    prepare_live_ram_set,
    run_live_ram_plan,
    stage_live_ram_set,
    unstage_live_ram_set,
)
from .layout import TARGET
from .macos_wifi import MacosWifiError, join_recovery_ap
from .official_input import OfficialInputError, validate_official_input
from .media import (
    MediaError,
    activate_staged_install_set,
    archive_existing_stock_backup,
    build_legacy_migration_profile,
    deactivate_verified_package,
    deactivate_staged_install_set,
    evacuate_existing_stock_backups,
    load_media_preflight,
    replace_passive_bootstrap,
    stage_verified_install_set,
    stage_verified_package,
    validate_install_set,
)
from .private_config import (
    PrivateConfigError,
    generate_private_config,
    import_private_wpa_config,
    read_authorized_key,
    read_private_input,
    seal_private_config_for_session,
)
from .recovery import (
    RecoveryError,
    build_stage1_recovery,
    inspect_same_device_stock_restore,
    prepare_same_device_stock_restore,
    write_recovery_result,
)
from .recovery_ap.kernel import (
    RecoveryApKernelError,
    render_recovery_ap_kernel_fragment,
    validate_recovery_ap_kernel,
)
from .recovery_ap.build import RecoveryApBuildError, build_recovery_ap_root
from .recovery_ap.session import (
    RecoveryApSessionError,
    create_recovery_ap_session,
    materialize_recovery_ap_session,
)
from .recovery_ap.host import (
    RecoveryApHostError,
    activate_personal_mtd3,
    diagnose_thingino_failure,
    extract_camera_vendor_bundle,
    install_personal_mtd3,
    inspect_recovery_ap_nor,
    reconcile_recovery_ap_state,
    install_recovery_ap,
    probe_recovery_ap,
    prove_thingino_health,
    provision_recovery_ap,
    resolve_recovery_ap_station,
)
from .mtd3_image import (
    Mtd3ImageError,
    build_personal_mtd3_image,
)
from .recovery_gate import RecoveryGateError, validate_existing_recovery_boundary
from .ram_boot import RamBootError, build_ramdisk_uimage, parse_ramdisk_uimage
from .runtime_gate import RuntimeGateError, validate_runtime_report
from .uartless_gate import UartlessGateError, validate_uartless_report
from .sd_package import (
    PackageError,
    atomic_write,
    generate_bootstrap,
    package_manifest,
    parse_package,
    read_snapshot,
    validate_bootstrap,
    validate_stock_source,
)
from .stage1.build import Stage1BuildError, render_installer_kernel_fragment
from .stock_restore.build import StockRestoreBuildError
from .stock_restore.set import (
    StockRestoreSetError,
    authorize_stock_restore,
    expected_live_confirmation,
    inspect_stock_restore_ram_set,
    passivate_completed_stock_restore,
    prepare_stock_restore_ram_set,
    stage_stock_restore_ram_set,
)
from .vendor_bundle import (
    VendorBundleError,
    extract_vendor_bundle,
    load_vendor_bundle,
    prepare_vendor_build_site,
)


DEFAULT_SD_NAME = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"


def _emit(document: dict[str, object]) -> None:
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


def _build_read_only_collector(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _build_read_only_collector as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_read_only_collector(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _validate_read_only_collector as implementation

    return implementation(sys.modules[__name__], arguments)


def _prepare_live_ram(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import _prepare_live_ram as implementation

    return implementation(sys.modules[__name__], arguments)


def _stage_live_ram(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import _stage_live_ram as implementation

    return implementation(sys.modules[__name__], arguments)


def _run_live_ram(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import _run_live_ram as implementation

    return implementation(sys.modules[__name__], arguments)


def _unstage_live_ram(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import _unstage_live_ram as implementation

    return implementation(sys.modules[__name__], arguments)


def _finalize_ram_backup(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import _finalize_ram_backup as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_read_only_protected(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _validate_read_only_protected as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _render_collector_kernel_fragment(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _render_collector_kernel_fragment as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_collector_kernel(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _validate_collector_kernel as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_uartless_gate(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _validate_uartless_gate as implementation

    return implementation(sys.modules[__name__], arguments)


def _render_recovery_ap_kernel_fragment(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _render_recovery_ap_kernel_fragment as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_recovery_ap_kernel(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _validate_recovery_ap_kernel as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_recovery_ap_root(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _build_recovery_ap_root as implementation

    return implementation(sys.modules[__name__], arguments)


def _create_recovery_ap_session(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _create_recovery_ap_session as implementation

    return implementation(sys.modules[__name__], arguments)


def _materialize_recovery_ap_session(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _materialize_recovery_ap_session as implementation

    return implementation(sys.modules[__name__], arguments)


def _probe_recovery_ap(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _probe_recovery_ap as implementation

    return implementation(sys.modules[__name__], arguments)


def _diagnose_thingino_failure(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _diagnose_thingino_failure as implementation

    return implementation(sys.modules[__name__], arguments)


def _provision_recovery_ap(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _provision_recovery_ap as implementation

    return implementation(sys.modules[__name__], arguments)


def _install_recovery_ap(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _install_recovery_ap as implementation

    return implementation(sys.modules[__name__], arguments)


def _extract_camera_vendor_bundle(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _extract_camera_vendor_bundle as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_personal_mtd3(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _build_personal_mtd3 as implementation

    return implementation(sys.modules[__name__], arguments)


def _install_personal_mtd3(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _install_personal_mtd3 as implementation

    return implementation(sys.modules[__name__], arguments)


def _reconcile_camera_state(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _reconcile_camera_state as implementation

    return implementation(sys.modules[__name__], arguments)


def _inspect_recovery_state(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _inspect_recovery_state as implementation

    return implementation(sys.modules[__name__], arguments)


def _activate_personal_mtd3(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _activate_personal_mtd3 as implementation

    return implementation(sys.modules[__name__], arguments)


def _prove_thingino_health(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _prove_thingino_health as implementation

    return implementation(sys.modules[__name__], arguments)


def _complete_personal_install(arguments: argparse.Namespace) -> None:
    from .cli_commands_recovery import _complete_personal_install as implementation

    return implementation(sys.modules[__name__], arguments)


def _inspect_sd(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _inspect_sd as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_bootstrap(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _build_bootstrap as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_recovery_ap_bootstrap(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _build_recovery_ap_bootstrap as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_stage1_recovery(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _build_stage1_recovery as implementation

    return implementation(sys.modules[__name__], arguments)


def _inspect_official(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _inspect_official as implementation

    return implementation(sys.modules[__name__], arguments)


def _stage_media(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _stage_media as implementation

    return implementation(sys.modules[__name__], arguments)


def _deactivate_staged_package(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _deactivate_staged_package as implementation

    return implementation(sys.modules[__name__], arguments)


def _stage_install_set(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _stage_install_set as implementation

    return implementation(sys.modules[__name__], arguments)


def _inspect_install_set(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _inspect_install_set as implementation

    return implementation(sys.modules[__name__], arguments)


def _activate_staged_install_set(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _activate_staged_install_set as implementation

    return implementation(sys.modules[__name__], arguments)


def _deactivate_staged_install_set(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _deactivate_staged_install_set as implementation

    return implementation(sys.modules[__name__], arguments)


def _archive_existing_stock_backup(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _archive_existing_stock_backup as implementation

    return implementation(sys.modules[__name__], arguments)


def _evacuate_existing_stock_backups(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _evacuate_existing_stock_backups as implementation

    return implementation(sys.modules[__name__], arguments)


def _replace_passive_bootstrap(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _replace_passive_bootstrap as implementation

    return implementation(sys.modules[__name__], arguments)


def _create_legacy_migration_profile(arguments: argparse.Namespace) -> None:
    from .cli_commands_media import _create_legacy_migration_profile as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_final_bundle(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _build_final_bundle as implementation

    return implementation(sys.modules[__name__], arguments)


def _verify_final_bundle(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _verify_final_bundle as implementation

    return implementation(sys.modules[__name__], arguments)


def _render_final_kernel_fragment(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _render_final_kernel_fragment as implementation

    return implementation(sys.modules[__name__], arguments)


def _render_installer_kernel_fragment(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _render_installer_kernel_fragment as implementation

    return implementation(sys.modules[__name__], arguments)


def _create_private_bootstrap_config(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _create_private_bootstrap_config as implementation

    return implementation(sys.modules[__name__], arguments)


def _seal_private_install_config(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _seal_private_install_config as implementation

    return implementation(sys.modules[__name__], arguments)


def _prepare_final_root(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _prepare_final_root as implementation

    return implementation(sys.modules[__name__], arguments)


def _inspect_vendor_bundle(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _inspect_vendor_bundle as implementation

    return implementation(sys.modules[__name__], arguments)


def _extract_vendor_bundle(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _extract_vendor_bundle as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_runtime_gate(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _validate_runtime_gate as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_recovery_boundary(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _validate_recovery_boundary as implementation

    return implementation(sys.modules[__name__], arguments)


def _validate_complete_backup(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import _validate_complete_backup as implementation

    return implementation(sys.modules[__name__], arguments)


def _prepare_same_device_stock_restore(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _prepare_same_device_stock_restore as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _inspect_same_device_stock_restore(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _inspect_same_device_stock_restore as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _prepare_stock_restore_live(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _prepare_stock_restore_live as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _stage_stock_restore_live(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _stage_stock_restore_live as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _authorize_stock_restore_live(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _authorize_stock_restore_live as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _show_stock_restore_confirmation(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _show_stock_restore_confirmation as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _passivate_stock_restore_live(arguments: argparse.Namespace) -> None:
    from .cli_commands_stock_recovery import (
        _passivate_stock_restore_live as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _prepare_vendor_build_site(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _prepare_vendor_build_site as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_ramdisk_wrapper(arguments: argparse.Namespace) -> None:
    from .cli_commands_build import _build_ramdisk_wrapper as implementation

    return implementation(sys.modules[__name__], arguments)


def build_parser() -> argparse.ArgumentParser:
    from .cli_parser import build_parser as build_parser_impl

    return build_parser_impl(sys.modules[__name__])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        arguments.handler(arguments)
    except (
        ArtifactError,
        BundleError,
        CollectorBuildError,
        CollectorKernelError,
        CollectorOutputError,
        DevelopmentInstallError,
        FinalRootError,
        FullBackupError,
        LiveRamError,
        MediaError,
        MacosWifiError,
        Mtd3ImageError,
        OfficialInputError,
        PackageError,
        PrivateConfigError,
        RamBootError,
        RecoveryError,
        RecoveryApBuildError,
        RecoveryApHostError,
        RecoveryApKernelError,
        RecoveryApSessionError,
        RecoveryGateError,
        RuntimeGateError,
        Stage1BuildError,
        StockRestoreBuildError,
        StockRestoreSetError,
        UartlessGateError,
        VendorBundleError,
        OSError,
    ) as exc:
        _emit({"error": str(exc), "ok": False})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
