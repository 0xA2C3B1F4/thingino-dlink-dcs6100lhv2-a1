"""Register the unchanged command line against the public facade."""

from __future__ import annotations


def build_parser(facade: object) -> argparse.ArgumentParser:
    Path = getattr(facade, 'Path')
    _activate_personal_mtd3 = getattr(facade, '_activate_personal_mtd3')
    _activate_staged_install_set = getattr(facade, '_activate_staged_install_set')
    _archive_existing_stock_backup = getattr(facade, '_archive_existing_stock_backup')
    _build_bootstrap = getattr(facade, '_build_bootstrap')
    _build_final_bundle = getattr(facade, '_build_final_bundle')
    _build_personal_mtd3 = getattr(facade, '_build_personal_mtd3')
    _build_ramdisk_wrapper = getattr(facade, '_build_ramdisk_wrapper')
    _build_read_only_collector = getattr(facade, '_build_read_only_collector')
    _build_recovery_ap_bootstrap = getattr(facade, '_build_recovery_ap_bootstrap')
    _build_recovery_ap_root = getattr(facade, '_build_recovery_ap_root')
    _build_stage1_recovery = getattr(facade, '_build_stage1_recovery')
    _complete_personal_install = getattr(facade, '_complete_personal_install')
    _create_legacy_migration_profile = getattr(facade, '_create_legacy_migration_profile')
    _create_private_bootstrap_config = getattr(facade, '_create_private_bootstrap_config')
    _create_recovery_ap_session = getattr(facade, '_create_recovery_ap_session')
    _deactivate_staged_install_set = getattr(facade, '_deactivate_staged_install_set')
    _deactivate_staged_package = getattr(facade, '_deactivate_staged_package')
    _diagnose_thingino_failure = getattr(facade, '_diagnose_thingino_failure')
    _evacuate_existing_stock_backups = getattr(facade, '_evacuate_existing_stock_backups')
    _extract_camera_vendor_bundle = getattr(facade, '_extract_camera_vendor_bundle')
    _extract_vendor_bundle = getattr(facade, '_extract_vendor_bundle')
    _inspect_install_set = getattr(facade, '_inspect_install_set')
    _inspect_official = getattr(facade, '_inspect_official')
    _inspect_recovery_state = getattr(facade, '_inspect_recovery_state')
    _inspect_sd = getattr(facade, '_inspect_sd')
    _inspect_vendor_bundle = getattr(facade, '_inspect_vendor_bundle')
    _install_personal_mtd3 = getattr(facade, '_install_personal_mtd3')
    _install_recovery_ap = getattr(facade, '_install_recovery_ap')
    _materialize_recovery_ap_session = getattr(facade, '_materialize_recovery_ap_session')
    _prepare_final_root = getattr(facade, '_prepare_final_root')
    _prepare_vendor_build_site = getattr(facade, '_prepare_vendor_build_site')
    _probe_recovery_ap = getattr(facade, '_probe_recovery_ap')
    _prove_thingino_health = getattr(facade, '_prove_thingino_health')
    _provision_recovery_ap = getattr(facade, '_provision_recovery_ap')
    _reconcile_camera_state = getattr(facade, '_reconcile_camera_state')
    _render_collector_kernel_fragment = getattr(facade, '_render_collector_kernel_fragment')
    _render_final_kernel_fragment = getattr(facade, '_render_final_kernel_fragment')
    _render_installer_kernel_fragment = getattr(facade, '_render_installer_kernel_fragment')
    _render_recovery_ap_kernel_fragment = getattr(facade, '_render_recovery_ap_kernel_fragment')
    _replace_passive_bootstrap = getattr(facade, '_replace_passive_bootstrap')
    _seal_private_install_config = getattr(facade, '_seal_private_install_config')
    _stage_install_set = getattr(facade, '_stage_install_set')
    _stage_media = getattr(facade, '_stage_media')
    _validate_collector_kernel = getattr(facade, '_validate_collector_kernel')
    _validate_read_only_collector = getattr(facade, '_validate_read_only_collector')
    _validate_recovery_ap_kernel = getattr(facade, '_validate_recovery_ap_kernel')
    _validate_recovery_boundary = getattr(facade, '_validate_recovery_boundary')
    _validate_runtime_gate = getattr(facade, '_validate_runtime_gate')
    _validate_uartless_gate = getattr(facade, '_validate_uartless_gate')
    _verify_final_bundle = getattr(facade, '_verify_final_bundle')
    argparse = getattr(facade, 'argparse')
    parser = argparse.ArgumentParser(
        prog="python3 -m installer",
        description="Host-only DCS-6100LHV2 A1 installer artifact tools",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_sd = commands.add_parser("inspect-sd-package")
    inspect_sd.add_argument("package", type=Path)
    inspect_sd.add_argument(
        "--policy", choices=("bootstrap", "stock-source"), required=True
    )
    inspect_sd.set_defaults(handler=_inspect_sd)

    build_bootstrap = commands.add_parser("build-bootstrap")
    build_bootstrap.add_argument("--kernel", type=Path, required=True)
    build_bootstrap.add_argument("--rootfs", type=Path, required=True)
    build_bootstrap.add_argument("--output-dir", type=Path, required=True)
    build_bootstrap.set_defaults(handler=_build_bootstrap)

    recovery_ap_bootstrap = commands.add_parser("build-recovery-ap-bootstrap")
    recovery_ap_bootstrap.add_argument("--kernel", type=Path, required=True)
    recovery_ap_bootstrap.add_argument("--linux-config", type=Path, required=True)
    recovery_ap_bootstrap.add_argument("--rootfs", type=Path, required=True)
    recovery_ap_bootstrap.add_argument("--output-dir", type=Path, required=True)
    recovery_ap_bootstrap.set_defaults(handler=_build_recovery_ap_bootstrap)

    build_recovery = commands.add_parser("build-stage1-recovery")
    build_recovery.add_argument("--kernel", type=Path, required=True)
    build_recovery.add_argument("--rootfs", type=Path, required=True)
    build_recovery.add_argument("--output-dir", type=Path, required=True)
    build_recovery.set_defaults(handler=_build_stage1_recovery)

    ramdisk_wrapper = commands.add_parser("build-ramdisk-wrapper")
    ramdisk_wrapper.add_argument("--rootfs", type=Path, required=True)
    ramdisk_wrapper.add_argument("--output", type=Path, required=True)
    ramdisk_wrapper.set_defaults(handler=_build_ramdisk_wrapper)

    collector = commands.add_parser("build-read-only-collector-root")
    collector.add_argument("--mmc-module", type=Path, required=True)
    collector.add_argument("--output", type=Path, required=True)
    collector.add_argument("--clang", type=Path)
    collector.add_argument("--lld", type=Path)
    collector.add_argument("--mksquashfs", type=Path)
    collector.add_argument("--unsquashfs", type=Path)
    collector.add_argument(
        "--capture-mode",
        choices=("existing-recovery", "protected-readback", "complete-backup"),
        default="existing-recovery",
    )
    collector.set_defaults(handler=_build_read_only_collector)

    collector_output = commands.add_parser("validate-read-only-collector-output")
    collector_output.add_argument("--collector-output-dir", type=Path, required=True)
    collector_output.add_argument("--recovery-dir", type=Path, required=True)
    collector_output.set_defaults(handler=_validate_read_only_collector)

    collector_fragment = commands.add_parser(
        "render-read-only-collector-kernel-fragment"
    )
    collector_fragment.add_argument("--output", type=Path, required=True)
    collector_fragment.set_defaults(handler=_render_collector_kernel_fragment)

    collector_kernel = commands.add_parser("validate-read-only-collector-kernel")
    collector_kernel.add_argument("--kernel", type=Path, required=True)
    collector_kernel.add_argument("--linux-config", type=Path, required=True)
    collector_kernel.set_defaults(handler=_validate_collector_kernel)

    uartless_gate = commands.add_parser("validate-uartless-recovery-report")
    uartless_gate.add_argument(
        "--scope",
        choices=("ap-feasibility", "release"),
        required=True,
    )
    uartless_gate.add_argument("--report", type=Path, required=True)
    uartless_gate.set_defaults(handler=_validate_uartless_gate)

    ap_fragment = commands.add_parser("render-recovery-ap-kernel-fragment")
    ap_fragment.add_argument("--output", type=Path, required=True)
    ap_fragment.set_defaults(handler=_render_recovery_ap_kernel_fragment)

    ap_kernel = commands.add_parser("validate-recovery-ap-kernel")
    ap_kernel.add_argument("--kernel", type=Path, required=True)
    ap_kernel.add_argument("--linux-config", type=Path, required=True)
    ap_kernel.set_defaults(handler=_validate_recovery_ap_kernel)

    ap_root = commands.add_parser("build-recovery-ap-root")
    ap_root.add_argument("--target-archive", type=Path, required=True)
    ap_root.add_argument("--wifi-module", type=Path, required=True)
    ap_root.add_argument("--mmc-module", type=Path, required=True)
    ap_root.add_argument("--dtrng-module", type=Path, required=True)
    ap_root.add_argument("--entropy-seed", type=Path, required=True)
    ap_root.add_argument(
        "--session-media-dir",
        type=Path,
        help="embed one private session media/RECOVERY directory for persistent mtd2",
    )
    ap_root.add_argument("--output", type=Path, required=True)
    ap_root.add_argument("--clang", type=Path)
    ap_root.add_argument("--lld", type=Path)
    ap_root.add_argument("--mksquashfs", type=Path)
    ap_root.add_argument("--unsquashfs", type=Path)
    ap_root.set_defaults(handler=_build_recovery_ap_root)

    ap_session = commands.add_parser("create-recovery-ap-session")
    ap_session.add_argument("--output-dir", type=Path, required=True)
    ap_session.add_argument("--ssh-keygen", type=Path, required=True)
    ap_session.add_argument("--dropbearkey", type=Path, required=True)
    ap_session.set_defaults(handler=_create_recovery_ap_session)

    materialize_ap_session = commands.add_parser("materialize-recovery-ap-session")
    materialize_ap_session.add_argument("--output-dir", type=Path, required=True)
    materialize_ap_session.add_argument("--session-media-dir", type=Path, required=True)
    materialize_ap_session.add_argument("--identity", type=Path, required=True)
    materialize_ap_session.add_argument("--ssh-keygen", type=Path, required=True)
    materialize_ap_session.add_argument("--dropbearkey", type=Path, required=True)
    materialize_ap_session.add_argument("--service-credential", type=Path)
    materialize_ap_session.set_defaults(handler=_materialize_recovery_ap_session)

    ap_probe = commands.add_parser("probe-recovery-ap")
    ap_probe.add_argument("--session-dir", type=Path, required=True)
    ap_probe_target = ap_probe.add_mutually_exclusive_group()
    ap_probe_target.add_argument("--host")
    ap_probe_target.add_argument("--discover-station", action="store_true")
    ap_probe.add_argument("--expected-state", choices=("ap", "station"), required=True)
    ap_probe.set_defaults(handler=_probe_recovery_ap)

    ap_diagnostics = commands.add_parser("diagnose-thingino-failure")
    ap_diagnostics.add_argument("--session-dir", type=Path, required=True)
    ap_diagnostics.add_argument("--host", default="192.168.88.1")
    ap_diagnostics.set_defaults(handler=_diagnose_thingino_failure)

    ap_provision = commands.add_parser("provision-recovery-ap")
    ap_provision.add_argument("--session-dir", type=Path, required=True)
    ap_provision.add_argument("--host", default="192.168.88.1")
    ap_provision.add_argument("--secrets-fd", type=int)
    ap_provision.set_defaults(handler=_provision_recovery_ap)

    ap_install = commands.add_parser("install-recovery-ap")
    ap_install.add_argument("--session-dir", type=Path, required=True)
    ap_install.add_argument("--host", default="192.168.88.1")
    ap_install.add_argument("--image", type=Path, required=True)
    ap_install.set_defaults(handler=_install_recovery_ap)

    camera_vendor = commands.add_parser("extract-camera-vendor-bundle")
    camera_vendor.add_argument("--session-dir", type=Path, required=True)
    camera_vendor.add_argument("--host", default="192.168.88.1")
    camera_vendor.add_argument("--output-dir", type=Path, required=True)
    camera_vendor.set_defaults(handler=_extract_camera_vendor_bundle)

    personal_image = commands.add_parser("build-personal-mtd3")
    personal_image.add_argument("--system-rootfs", type=Path, required=True)
    personal_image.add_argument("--output", type=Path, required=True)
    personal_image.set_defaults(handler=_build_personal_mtd3)

    personal_install = commands.add_parser("install-personal-mtd3")
    personal_install.add_argument("--session-dir", type=Path, required=True)
    personal_install.add_argument("--host", default="192.168.88.1")
    personal_install.add_argument("--image", type=Path, required=True)
    personal_install.add_argument("--vendor-bundle-dir", type=Path)
    personal_install.add_argument("--image-provenance", type=Path, required=True)
    personal_install.set_defaults(handler=_install_personal_mtd3)

    reconcile = commands.add_parser("reconcile-camera-state")
    reconcile.add_argument("--session-dir", type=Path, required=True)
    reconcile.add_argument("--host", default="192.168.88.1")
    reconcile.add_argument("--image", type=Path, required=True)
    reconcile.add_argument("--vendor-bundle-dir", type=Path)
    reconcile.add_argument("--image-provenance", type=Path, required=True)
    reconcile.set_defaults(handler=_reconcile_camera_state)

    inspect_recovery = commands.add_parser("inspect-recovery-state")
    inspect_recovery.add_argument("--session-dir", type=Path, required=True)
    inspect_recovery.add_argument("--host", default="192.168.88.1")
    inspect_recovery.set_defaults(handler=_inspect_recovery_state)

    personal_activate = commands.add_parser("activate-personal-mtd3")
    personal_activate.add_argument("--session-dir", type=Path, required=True)
    personal_activate.add_argument("--host", default="192.168.88.1")
    personal_activate.add_argument("--image-sha256", required=True)
    personal_activate.set_defaults(handler=_activate_personal_mtd3)

    thingino_health = commands.add_parser("prove-thingino-health")
    thingino_health.add_argument("--session-dir", type=Path, required=True)
    thingino_health.add_argument("--expected-mtd3-sha256")
    thingino_health.set_defaults(handler=_prove_thingino_health)

    complete_install = commands.add_parser("complete-personal-install")
    complete_install.add_argument("--session-dir", type=Path, required=True)
    complete_install.add_argument("--base-rootfs", type=Path, required=True)
    complete_install.add_argument("--media-closure-dir", type=Path, required=True)
    complete_install.add_argument("--work-dir", type=Path, required=True)
    complete_install.add_argument("--mksquashfs", type=Path, required=True)
    complete_install.add_argument("--unsquashfs", type=Path, required=True)
    complete_install.add_argument("--host", default="192.168.88.1")
    complete_install.add_argument("--secrets-fd", type=int)
    complete_install.add_argument(
        "--expected-wpa-config",
        type=Path,
        help="required when resuming an existing private install configuration",
    )
    complete_install.add_argument("--station-timeout", type=int, default=180)
    complete_install.add_argument("--macos-wifi-helper", type=Path)
    complete_install.set_defaults(handler=_complete_personal_install)

    inspect_official = commands.add_parser("inspect-official-input")
    inspect_official.add_argument("input", type=Path)
    inspect_official.set_defaults(handler=_inspect_official)

    stage_media = commands.add_parser("stage-media")
    stage_media.add_argument("--package", type=Path, required=True)
    stage_media.add_argument("--mount-root", type=Path, required=True)
    stage_media.add_argument("--preflight", type=Path, required=True)
    stage_media.add_argument("--confirm-physical-device", required=True)
    stage_media.set_defaults(handler=_stage_media)

    deactivate_package = commands.add_parser("deactivate-staged-package")
    deactivate_package.add_argument("--package", type=Path, required=True)
    deactivate_package.add_argument("--existing-passive-package", type=Path)
    deactivate_package.add_argument("--mount-root", type=Path, required=True)
    deactivate_package.add_argument("--preflight", type=Path, required=True)
    deactivate_package.add_argument("--confirm-physical-device", required=True)
    deactivate_package.set_defaults(handler=_deactivate_staged_package)

    stage_install_set = commands.add_parser("stage-install-set")
    stage_install_set.add_argument("--install-set-dir", type=Path, required=True)
    stage_install_set.add_argument("--mount-root", type=Path, required=True)
    stage_install_set.add_argument("--preflight", type=Path, required=True)
    stage_install_set.add_argument("--confirm-physical-device", required=True)
    stage_install_set.set_defaults(handler=_stage_install_set)

    inspect_install_set = commands.add_parser("inspect-install-set")
    inspect_install_set.add_argument("--install-set-dir", type=Path, required=True)
    inspect_install_set.set_defaults(handler=_inspect_install_set)

    activate_install_set = commands.add_parser("activate-staged-install-set")
    activate_install_set.add_argument("--install-set-dir", type=Path, required=True)
    activate_install_set.add_argument("--mount-root", type=Path, required=True)
    activate_install_set.add_argument("--preflight", type=Path, required=True)
    activate_install_set.add_argument("--confirm-physical-device", required=True)
    activate_install_set.set_defaults(handler=_activate_staged_install_set)

    deactivate_install_set = commands.add_parser("deactivate-staged-install-set")
    deactivate_install_set.add_argument("--install-set-dir", type=Path, required=True)
    deactivate_install_set.add_argument("--mount-root", type=Path, required=True)
    deactivate_install_set.add_argument("--preflight", type=Path, required=True)
    deactivate_install_set.add_argument("--confirm-physical-device", required=True)
    deactivate_install_set.add_argument(
        "--existing-passive-install-set-dir",
        type=Path,
        help="reviewed install set matching an existing STAGE1.PKG",
    )
    deactivate_install_set.set_defaults(handler=_deactivate_staged_install_set)

    archive_stock_backup = commands.add_parser("archive-existing-stock-backup")
    archive_stock_backup.add_argument("--mount-root", type=Path, required=True)
    archive_stock_backup.add_argument("--preflight", type=Path, required=True)
    archive_stock_backup.add_argument("--confirm-physical-device", required=True)
    archive_stock_backup.set_defaults(handler=_archive_existing_stock_backup)

    evacuate_stock_backups = commands.add_parser("evacuate-existing-stock-backups")
    evacuate_stock_backups.add_argument("--mount-root", type=Path, required=True)
    evacuate_stock_backups.add_argument("--preflight", type=Path, required=True)
    evacuate_stock_backups.add_argument("--confirm-physical-device", required=True)
    evacuate_stock_backups.add_argument("--output-dir", type=Path, required=True)
    evacuate_stock_backups.set_defaults(handler=_evacuate_existing_stock_backups)

    replace_passive = commands.add_parser("replace-passive-bootstrap")
    replace_passive.add_argument("--old-install-set-dir", type=Path, required=True)
    replace_passive.add_argument("--legacy-migration-profile", type=Path)
    replace_passive.add_argument("--install-set-dir", type=Path, required=True)
    replace_passive.add_argument("--mount-root", type=Path, required=True)
    replace_passive.add_argument("--preflight", type=Path, required=True)
    replace_passive.add_argument("--confirm-physical-device", required=True)
    replace_passive.set_defaults(handler=_replace_passive_bootstrap)

    legacy_profile = commands.add_parser("create-legacy-migration-profile")
    legacy_profile.add_argument("--old-install-set-dir", type=Path, required=True)
    legacy_profile.add_argument("--output", type=Path, required=True)
    legacy_profile.set_defaults(handler=_create_legacy_migration_profile)

    final_bundle = commands.add_parser("build-final-bundle")
    final_bundle.add_argument("--kernel", type=Path, required=True)
    final_bundle.add_argument("--bootstrap-rootfs", type=Path, required=True)
    final_bundle.add_argument("--system-rootfs", type=Path, required=True)
    final_bundle.add_argument("--data-jffs2", type=Path, required=True)
    final_bundle.add_argument("--linux-config", type=Path, required=True)
    final_bundle.add_argument("--signing-key", type=Path, required=True)
    final_bundle.add_argument("--output-dir", type=Path, required=True)
    final_bundle.set_defaults(handler=_build_final_bundle)

    verify_bundle = commands.add_parser("verify-final-bundle")
    verify_bundle.add_argument("--bundle", type=Path, required=True)
    verify_bundle.add_argument("--public-key", type=Path, required=True)
    verify_bundle.set_defaults(handler=_verify_final_bundle)

    kernel_fragment = commands.add_parser("render-final-kernel-fragment")
    kernel_fragment.add_argument("--system-rootfs", type=Path, required=True)
    kernel_fragment.add_argument("--output", type=Path, required=True)
    kernel_fragment.set_defaults(handler=_render_final_kernel_fragment)

    installer_kernel_fragment = commands.add_parser(
        "render-installer-kernel-fragment"
    )
    installer_kernel_fragment.add_argument(
        "--system-rootfs", type=Path, required=True
    )
    installer_kernel_fragment.add_argument("--output", type=Path, required=True)
    installer_kernel_fragment.set_defaults(
        handler=_render_installer_kernel_fragment
    )

    private_config = commands.add_parser("create-private-install-config")
    private_config.add_argument("--output-dir", type=Path, required=True)
    private_config.add_argument("--authorized-key", type=Path, required=True)
    private_input = private_config.add_mutually_exclusive_group()
    private_input.add_argument("--secrets-fd", type=int)
    private_input.add_argument("--wpa-config", type=Path)
    private_config.set_defaults(handler=_create_private_bootstrap_config)

    seal_private_config_command = commands.add_parser(
        "seal-private-install-config"
    )
    seal_private_config_command.add_argument(
        "--private-config-dir", type=Path, required=True
    )
    seal_private_config_command.add_argument(
        "--session-dir", type=Path, required=True
    )
    seal_private_config_command.set_defaults(handler=_seal_private_install_config)

    final_root = commands.add_parser("prepare-final-root")
    final_root.add_argument("--base-rootfs", type=Path, required=True)
    final_root.add_argument("--private-config-dir", type=Path, required=True)
    final_root.add_argument("--expected-wpa-config", type=Path, required=True)
    final_root.add_argument("--vendor-bundle-dir", type=Path, required=True)
    final_root.add_argument("--media-closure-dir", type=Path, required=True)
    final_root.add_argument("--session-dir", type=Path, required=True)
    final_root.add_argument("--output-dir", type=Path, required=True)
    final_root.add_argument("--mksquashfs", type=Path, required=True)
    final_root.add_argument("--unsquashfs", type=Path, required=True)
    final_root.add_argument(
        "--output-size",
        type=int,
        help="optional exact zero-padded system image size required by a pinned kernel layout",
    )
    final_root.set_defaults(handler=_prepare_final_root)

    inspect_vendor = commands.add_parser("inspect-vendor-bundle")
    inspect_vendor.add_argument("--vendor-bundle-dir", type=Path, required=True)
    inspect_vendor.set_defaults(handler=_inspect_vendor_bundle)

    extract_vendor = commands.add_parser("extract-vendor-bundle")
    extract_vendor.add_argument("--mtd3-mount", type=Path, required=True)
    extract_vendor.add_argument(
        "--mountinfo",
        type=Path,
        default=Path("/proc/self/mountinfo"),
        help="kernel mountinfo used to prove the exact mtd3 JFFS2 mount is read-only",
    )
    extract_vendor.add_argument("--output-dir", type=Path, required=True)
    extract_vendor.set_defaults(handler=_extract_vendor_bundle)

    runtime_gate = commands.add_parser("validate-runtime-report")
    runtime_gate.add_argument("--vendor-bundle-dir", type=Path, required=True)
    runtime_gate.add_argument("--report", type=Path, required=True)
    runtime_gate.set_defaults(handler=_validate_runtime_gate)

    recovery_gate = commands.add_parser("validate-recovery-boundary")
    recovery_gate.add_argument("--recovery-dir", type=Path, required=True)
    recovery_gate.add_argument("--preserved-readback-dir", type=Path, required=True)
    recovery_gate.set_defaults(handler=_validate_recovery_boundary)

    build_site = commands.add_parser("prepare-vendor-build-site")
    build_site.add_argument("--vendor-bundle-dir", type=Path, required=True)
    build_site.add_argument("--output-dir", type=Path, required=True)
    build_site.set_defaults(handler=_prepare_vendor_build_site)
    from .cli_parser_stock_recovery import register_stock_recovery_commands

    register_stock_recovery_commands(facade, commands)
    return parser
