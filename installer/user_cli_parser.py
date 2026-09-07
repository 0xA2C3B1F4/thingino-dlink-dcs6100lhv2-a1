"""Register the unchanged command line against the public facade."""

from __future__ import annotations


def build_parser(facade: object) -> argparse.ArgumentParser:
    FAULT_CLASSES = getattr(facade, 'FAULT_CLASSES')
    Path = getattr(facade, 'Path')
    WORKFLOW_MODES = getattr(facade, 'WORKFLOW_MODES')
    _ArgumentParser = getattr(facade, '_ArgumentParser')
    _add_common = getattr(facade, '_add_common')
    _build_personal_mtd3 = getattr(facade, '_build_personal_mtd3')
    _candidate_create = getattr(facade, '_candidate_create')
    _candidate_decide = getattr(facade, '_candidate_decide')
    _candidate_record = getattr(facade, '_candidate_record')
    _candidate_status = getattr(facade, '_candidate_status')
    _diagnose_runtime = getattr(facade, '_diagnose_runtime')
    _hypothesis_record = getattr(facade, '_hypothesis_record')
    _install = getattr(facade, '_install')
    _inspect_vendor_bundle = getattr(facade, '_inspect_vendor_bundle')
    _local_build_acquire = getattr(facade, '_local_build_acquire')
    _local_build_bootstrap = getattr(facade, '_local_build_bootstrap')
    _local_build_build = getattr(facade, '_local_build_build')
    _local_build_build_universal = getattr(facade, '_local_build_build_universal')
    _local_build_configure = getattr(facade, '_local_build_configure')
    _local_build_prepare = getattr(facade, '_local_build_prepare')
    _local_build_recovery_assets = getattr(facade, '_local_build_recovery_assets')
    _local_build_status = getattr(facade, '_local_build_status')
    _universal_authorize = getattr(facade, '_universal_authorize')
    _universal_configure = getattr(facade, '_universal_configure')
    _universal_evacuate_recovery = getattr(facade, '_universal_evacuate_recovery')
    _universal_handoff = getattr(facade, '_universal_handoff')
    _universal_init_session = getattr(facade, '_universal_init_session')
    _universal_provision = getattr(facade, '_universal_provision')
    _universal_stage = getattr(facade, '_universal_stage')
    _universal_verify = getattr(facade, '_universal_verify')
    _preflight = getattr(facade, '_preflight')
    _prepare_card = getattr(facade, '_prepare_card')
    _private_config_inspect = getattr(facade, '_private_config_inspect')
    _private_config_rotate = getattr(facade, '_private_config_rotate')
    _runtime_candidate_rollback = getattr(facade, '_runtime_candidate_rollback')
    _runtime_candidate_stage = getattr(facade, '_runtime_candidate_stage')
    _runtime_candidate_status = getattr(facade, '_runtime_candidate_status')
    _stage_install_set = getattr(facade, '_stage_install_set')
    _status = getattr(facade, '_status')
    _verify = getattr(facade, '_verify')
    _verify_media = getattr(facade, '_verify_media')
    _workflow_preflight = getattr(facade, '_workflow_preflight')
    parser = _ArgumentParser(prog="thingino-dlink")
    _add_common(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_vendor = commands.add_parser("inspect-vendor-bundle")
    _add_common(inspect_vendor, inherited=True)
    inspect_vendor.add_argument("--vendor-bundle-dir", type=Path, required=True)
    inspect_vendor.set_defaults(handler=_inspect_vendor_bundle)

    preflight = commands.add_parser("preflight")
    _add_common(preflight, inherited=True)
    preflight.add_argument("--session-dir", type=Path)
    preflight.add_argument("--base-rootfs", type=Path)
    preflight.add_argument("--media-closure-dir", type=Path)
    package = preflight.add_mutually_exclusive_group()
    package.add_argument("--install-set-dir", type=Path)
    package.add_argument("--recovery-package", type=Path)
    preflight.add_argument("--mksquashfs", type=Path)
    preflight.add_argument("--unsquashfs", type=Path)
    preflight.add_argument("--macos-wifi-helper", type=Path)
    preflight.add_argument("--macos-station-wifi-helper", type=Path)
    preflight.set_defaults(handler=_preflight)

    card = commands.add_parser("prepare-card")
    _add_common(card, inherited=True)
    card.add_argument(
        "--whole-device", required=True, help="physical removable whole-disk ID"
    )
    card.add_argument(
        "--mount-root", type=Path, required=True, help="mounted FAT32 card root"
    )
    card_recovery = card.add_mutually_exclusive_group(required=True)
    card_recovery.add_argument(
        "--recovery-dir",
        type=Path,
        help="validated exact complete same-camera mtd0-mtd5 backup",
    )
    card_recovery.add_argument(
        "--functional-recovery-dir",
        type=Path,
        help="validated UARTless schema-3 functional same-camera recovery",
    )
    card.add_argument(
        "--preserved-readback-dir",
        type=Path,
        required=True,
        help="current same-camera protected-partition readback evidence",
    )
    card.add_argument(
        "--confirm-physical-device", help="repeat the exact whole-device ID"
    )
    card.add_argument(
        "--confirm-target", help="literal target confirmation DCS-6100LHV2-A1"
    )
    card.set_defaults(handler=_prepare_card)

    stage_install_set = commands.add_parser("stage-install-set")
    _add_common(stage_install_set, inherited=True)
    stage_install_set.add_argument(
        "--install-set-dir",
        type=Path,
        required=True,
        help="exact install_set_dir reported by local-build build",
    )
    stage_install_set.add_argument(
        "--whole-device", required=True, help="physical removable whole-disk ID"
    )
    stage_install_set.add_argument(
        "--mount-root", type=Path, required=True, help="mounted FAT32 card root"
    )
    stage_recovery = stage_install_set.add_mutually_exclusive_group(required=True)
    stage_recovery.add_argument(
        "--recovery-dir",
        type=Path,
        help="validated exact complete same-camera mtd0-mtd5 backup",
    )
    stage_recovery.add_argument(
        "--functional-recovery-dir",
        type=Path,
        help="validated UARTless schema-3 functional same-camera recovery",
    )
    stage_install_set.add_argument(
        "--preserved-readback-dir",
        type=Path,
        required=True,
        help="current same-camera protected-partition readback evidence",
    )
    stage_install_set.add_argument(
        "--expected-data-mode",
        choices=("initialize", "preserve", "factory-reset"),
        required=True,
        help="data action selected during local-build configure",
    )
    stage_install_set.add_argument(
        "--confirm-physical-device", help="repeat the exact whole-device ID"
    )
    stage_install_set.add_argument(
        "--confirm-target", help="literal target confirmation DCS-6100LHV2-A1"
    )
    stage_install_set.set_defaults(handler=_stage_install_set)

    install = commands.add_parser("install")
    _add_common(install, inherited=True)
    install.add_argument("--secrets-fd", type=int)
    install_recovery = install.add_mutually_exclusive_group(required=True)
    install_recovery.add_argument(
        "--recovery-dir",
        type=Path,
        help="validated exact complete same-camera mtd0-mtd5 backup",
    )
    install_recovery.add_argument(
        "--functional-recovery-dir",
        type=Path,
        help="validated UARTless schema-3 functional same-camera recovery",
    )
    install.add_argument(
        "--preserved-readback-dir",
        type=Path,
        required=True,
        help="current same-camera protected-partition readback evidence",
    )
    install.add_argument(
        "--expected-wpa-config",
        type=Path,
        help="required when resuming an existing private install configuration",
    )
    install.add_argument("--station-timeout", type=int, default=180)
    install.add_argument(
        "--wifi-mode",
        choices=("auto", "manual"),
        default="auto",
        help="try a host adapter first or wait for manual system-settings joins",
    )
    install.add_argument(
        "--manual-wifi-timeout",
        type=int,
        default=120,
        help="seconds to wait for a manually selected recovery AP",
    )
    install.set_defaults(handler=_install)

    status = commands.add_parser("status")
    _add_common(status, inherited=True)
    status.set_defaults(handler=_status)

    verify = commands.add_parser("verify")
    _add_common(verify, inherited=True)
    verify.set_defaults(handler=_verify)

    media = commands.add_parser("verify-media")
    _add_common(media, inherited=True)
    media.add_argument(
        "--media-closure-dir",
        type=Path,
        help="use an exact private C1 closure without changing the saved config",
    )
    media.set_defaults(handler=_verify_media)

    personal_build = commands.add_parser("build-personal-mtd3")
    _add_common(personal_build, inherited=True)
    personal_build.add_argument("--private-config-dir", type=Path, required=True)
    personal_build.add_argument("--expected-wpa-config", type=Path, required=True)
    personal_build.add_argument("--vendor-bundle-dir", type=Path, required=True)
    personal_build.add_argument("--output-dir", type=Path, required=True)
    personal_build.add_argument("--session-dir", type=Path)
    personal_build.set_defaults(handler=_build_personal_mtd3)

    workflow = commands.add_parser("workflow-preflight")
    _add_common(workflow, inherited=True)
    workflow.add_argument("--mode", choices=WORKFLOW_MODES, required=True)
    workflow.add_argument("--project-root", type=Path, default=Path.cwd())
    workflow.add_argument(
        "--data-volume",
        type=Path,
        required=True,
        help="mounted external volume that contains the project and build data",
    )
    workflow.add_argument("--session-dir", type=Path)
    workflow.add_argument("--vendor-bundle-dir", type=Path)
    workflow.add_argument("--private-config-dir", type=Path)
    workflow.add_argument("--expected-wpa-config", type=Path)
    workflow.add_argument("--image", type=Path)
    workflow.add_argument("--image-provenance", type=Path)
    workflow.add_argument("--rootfs", type=Path)
    workflow.add_argument("--output-dir", type=Path)
    workflow.add_argument("--observe-camera", action="store_true")
    workflow.add_argument("--recovery-host", default="192.168.88.1")
    workflow.add_argument("--station-ipv4")
    workflow.set_defaults(handler=_workflow_preflight)

    local_build = commands.add_parser("local-build")
    local_build_commands = local_build.add_subparsers(
        dest="local_build_command", required=True
    )
    local_build_prepare = local_build_commands.add_parser("prepare")
    _add_common(local_build_prepare, inherited=True)
    local_build_prepare.add_argument("--build-root", type=Path)
    local_build_prepare.add_argument(
        "--build-count",
        type=int,
        choices=(1, 2),
        default=1,
        help="reserve capacity for one normal build or two reproducibility builds",
    )
    local_build_prepare.set_defaults(handler=_local_build_prepare)
    local_build_status = local_build_commands.add_parser("status")
    _add_common(local_build_status, inherited=True)
    local_build_status.add_argument("--build-root", type=Path)
    local_build_status.set_defaults(handler=_local_build_status)
    local_build_bootstrap = local_build_commands.add_parser("bootstrap")
    _add_common(local_build_bootstrap, inherited=True)
    local_build_bootstrap.add_argument("--build-root", type=Path)
    local_build_bootstrap.set_defaults(handler=_local_build_bootstrap)
    local_build_acquire = local_build_commands.add_parser("acquire")
    _add_common(local_build_acquire, inherited=True)
    local_build_acquire.add_argument("--build-root", type=Path)
    local_build_acquire.set_defaults(handler=_local_build_acquire)
    local_build_recovery_assets = local_build_commands.add_parser("recovery-assets")
    _add_common(local_build_recovery_assets, inherited=True)
    local_build_recovery_assets.add_argument("--build-root", type=Path)
    local_build_recovery_assets.set_defaults(handler=_local_build_recovery_assets)
    local_build_configure = local_build_commands.add_parser("configure")
    _add_common(local_build_configure, inherited=True)
    local_build_configure.add_argument("--build-root", type=Path)
    local_build_configure.add_argument(
        "--private-root",
        type=Path,
        help="mode-0700 directory for generated and selected private inputs",
    )
    local_build_configure.add_argument(
        "--vendor-bundle-dir",
        type=Path,
        help="validated bundle acquired read-only from this camera's stock mtd3",
    )
    local_build_configure.add_argument(
        "--media-closure-dir",
        type=Path,
        help="validated hash-locked C1 media runtime closure",
    )
    local_build_configure.add_argument(
        "--session-dir",
        type=Path,
        help="private recovery session bound to this camera workflow",
    )
    local_build_configure.add_argument(
        "--raptor-rwd-artifact",
        type=Path,
        help="reviewed source-built Raptor RWD artifact",
    )
    local_build_configure.add_argument(
        "--data-mode",
        choices=("initialize", "preserve", "factory-reset"),
        help="explicit mtd3 data action recorded in the install set",
    )
    local_build_configure.add_argument(
        "--secrets-fd",
        type=int,
        help="inherited descriptor containing confirmed Wi-Fi JSON; never argv",
    )
    local_build_configure.set_defaults(handler=_local_build_configure)
    local_build_build = local_build_commands.add_parser("build")
    _add_common(local_build_build, inherited=True)
    local_build_build.add_argument("--build-root", type=Path)
    local_build_build.add_argument(
        "--build-count",
        type=int,
        choices=(1, 2),
        default=1,
        help="run one clean build, or two for reproducibility evidence",
    )
    local_build_build.add_argument(
        "--settings",
        type=Path,
        help="private settings file; defaults to the configure-recorded file",
    )
    local_build_build.add_argument(
        "--vendor-bundle-dir", type=Path, help="advanced: validated vendor bundle"
    )
    local_build_build.add_argument(
        "--media-closure-dir", type=Path, help="advanced: validated media closure"
    )
    local_build_build.add_argument(
        "--private-config-dir",
        type=Path,
        help="advanced: sealed session-bound install configuration",
    )
    local_build_build.add_argument(
        "--expected-wpa-config",
        type=Path,
        help="advanced: independent mode-0600 canonical station confirmation",
    )
    local_build_build.add_argument(
        "--session-dir", type=Path, help="advanced: bound private recovery session"
    )
    local_build_build.add_argument(
        "--raptor-rwd-artifact",
        type=Path,
        help="advanced: reviewed source-built Raptor RWD archive",
    )
    local_build_build.add_argument(
        "--data-mode",
        choices=("initialize", "preserve", "factory-reset"),
        default=None,
        help="advanced override; must match a saved plan when one is used",
    )
    local_build_build.set_defaults(handler=_local_build_build)
    local_build_universal = local_build_commands.add_parser("build-universal")
    _add_common(local_build_universal, inherited=True)
    local_build_universal.add_argument("--build-root", type=Path)
    local_build_universal.add_argument(
        "--build-count",
        type=int,
        choices=(1, 2),
        default=1,
        help="run one clean build, or two for reproducibility evidence",
    )
    local_build_universal.add_argument(
        "--vendor-bundle-dir", type=Path, required=True
    )
    local_build_universal.add_argument(
        "--media-closure-dir",
        type=Path,
        help="advanced: accepted legacy C1 closure instead of the public matched-media profile",
    )
    local_build_universal.add_argument(
        "--raptor-rwd-artifact",
        type=Path,
        help="advanced: add the optional reviewed WebRTC component",
    )
    local_build_universal.add_argument(
        "--signing-key",
        type=Path,
        help="stable model signer; generated in private build storage when omitted",
    )
    local_build_universal.add_argument(
        "--signing-public-key",
        type=Path,
        help="public half written or validated beside --signing-key",
    )
    local_build_universal.set_defaults(handler=_local_build_build_universal)

    universal = commands.add_parser("universal")
    universal_commands = universal.add_subparsers(
        dest="universal_command", required=True
    )

    def add_recovery_inputs(parser: argparse.ArgumentParser) -> None:
        recovery = parser.add_mutually_exclusive_group(required=True)
        recovery.add_argument("--recovery-dir", type=Path)
        recovery.add_argument("--functional-recovery-dir", type=Path)
        parser.add_argument("--preserved-readback-dir", type=Path, required=True)

    universal_init_session = universal_commands.add_parser("init-session")
    _add_common(universal_init_session, inherited=True)
    universal_init_session.add_argument(
        "--functional-recovery-dir", type=Path, required=True
    )
    universal_init_session.add_argument(
        "--preserved-readback-dir", type=Path, required=True
    )
    universal_init_session.add_argument("--output-dir", type=Path, required=True)
    universal_init_session.add_argument(
        "--config-output-dir", type=Path, required=True
    )
    universal_init_session.add_argument(
        "--ssh-keygen",
        type=Path,
        help="OpenSSH ssh-keygen; defaults to the reviewed host executable",
    )
    universal_init_session.add_argument(
        "--dropbearkey",
        type=Path,
        help="Dropbear host-key generator; defaults to the reviewed host executable",
    )
    universal_init_session.set_defaults(handler=_universal_init_session)

    universal_configure = universal_commands.add_parser("configure")
    _add_common(universal_configure, inherited=True)
    universal_configure.add_argument("--session-dir", type=Path, required=True)
    universal_configure.add_argument("--output-dir", type=Path, required=True)
    universal_configure.add_argument(
        "--signing-key",
        type=Path,
        help="stable camera signer; generated beside the output directory when omitted",
    )
    universal_configure.add_argument(
        "--signing-public-key",
        type=Path,
        help="public half written or validated beside --signing-key",
    )
    universal_configure.add_argument(
        "--secrets-fd",
        type=int,
        help="inherited descriptor containing confirmed Wi-Fi JSON; never argv",
    )
    universal_configure.set_defaults(handler=_universal_configure)

    universal_provision = universal_commands.add_parser("provision")
    _add_common(universal_provision, inherited=True)
    add_recovery_inputs(universal_provision)
    universal_provision.add_argument("--universal-bundle", type=Path, required=True)
    universal_provision.add_argument(
        "--universal-public-key", type=Path, required=True
    )
    universal_provision.add_argument("--private-config-dir", type=Path, required=True)
    universal_provision.add_argument("--session-dir", type=Path, required=True)
    universal_provision.add_argument("--signing-key", type=Path, required=True)
    universal_provision.add_argument("--unsquashfs", type=Path, required=True)
    universal_provision.add_argument("--mkfs-jffs2", type=Path, required=True)
    universal_provision.add_argument("--output", type=Path, required=True)
    universal_provision.add_argument("--data-output", type=Path, required=True)
    universal_provision.set_defaults(handler=_universal_provision)

    universal_authorize = universal_commands.add_parser("authorize")
    _add_common(universal_authorize, inherited=True)
    add_recovery_inputs(universal_authorize)
    universal_authorize.add_argument("--universal-bundle", type=Path, required=True)
    universal_authorize.add_argument(
        "--universal-public-key", type=Path, required=True
    )
    universal_authorize.add_argument("--provisioning", type=Path, required=True)
    universal_authorize.add_argument("--provisioning-data", type=Path, required=True)
    universal_authorize.add_argument(
        "--provisioning-public-key", type=Path, required=True
    )
    universal_authorize.add_argument("--session-dir", type=Path, required=True)
    universal_authorize.add_argument("--signing-key", type=Path, required=True)
    universal_authorize.add_argument("--output-dir", type=Path, required=True)
    universal_authorize.add_argument(
        "--data-action", choices=("initialize",), default="initialize"
    )
    universal_authorize.set_defaults(handler=_universal_authorize)

    universal_stage = universal_commands.add_parser("stage")
    _add_common(universal_stage, inherited=True)
    add_recovery_inputs(universal_stage)
    universal_stage.add_argument("--install-set-dir", type=Path, required=True)
    universal_stage.add_argument(
        "--universal-public-key", type=Path, required=True
    )
    universal_stage.add_argument("--provisioning", type=Path, required=True)
    universal_stage.add_argument("--provisioning-data", type=Path, required=True)
    universal_stage.add_argument("--authorization-dir", type=Path, required=True)
    universal_stage.add_argument(
        "--authorization-public-key", type=Path, required=True
    )
    universal_stage.add_argument("--session-dir", type=Path, required=True)
    universal_stage.add_argument(
        "--whole-device", required=True, help="physical removable whole-disk ID"
    )
    universal_stage.add_argument(
        "--mount-root", type=Path, required=True, help="mounted FAT32 card root"
    )
    universal_stage.add_argument("--confirm-physical-device")
    universal_stage.add_argument("--confirm-target")
    universal_stage.add_argument("--confirm-write-set")
    universal_stage.set_defaults(handler=_universal_stage)

    universal_evacuate = universal_commands.add_parser("evacuate-recovery")
    _add_common(universal_evacuate, inherited=True)
    universal_evacuate.add_argument(
        "--whole-device", required=True, help="physical removable whole-disk ID"
    )
    universal_evacuate.add_argument(
        "--mount-root", type=Path, required=True, help="mounted FAT32 card root"
    )
    universal_evacuate.add_argument("--output-dir", type=Path, required=True)
    universal_evacuate.add_argument("--confirm-physical-device")
    universal_evacuate.set_defaults(handler=_universal_evacuate_recovery)

    universal_handoff = universal_commands.add_parser("handoff")
    _add_common(universal_handoff, inherited=True)
    add_recovery_inputs(universal_handoff)
    universal_handoff.add_argument("--install-set-dir", type=Path, required=True)
    universal_handoff.add_argument(
        "--universal-public-key", type=Path, required=True
    )
    universal_handoff.add_argument("--provisioning", type=Path, required=True)
    universal_handoff.add_argument("--provisioning-data", type=Path, required=True)
    universal_handoff.add_argument("--authorization-dir", type=Path, required=True)
    universal_handoff.add_argument(
        "--authorization-public-key", type=Path, required=True
    )
    universal_handoff.add_argument("--session-dir", type=Path, required=True)
    universal_handoff.add_argument(
        "--whole-device", required=True, help="physical removable whole-disk ID"
    )
    universal_handoff.add_argument(
        "--mount-root", type=Path, required=True, help="mounted FAT32 card root"
    )
    universal_handoff.add_argument("--confirm-physical-device")
    universal_handoff.add_argument("--confirm-target")
    universal_handoff.add_argument("--confirm-stock-uboot-result")
    universal_handoff.set_defaults(handler=_universal_handoff)

    universal_verify = universal_commands.add_parser("verify")
    _add_common(universal_verify, inherited=True)
    universal_verify.add_argument("--session-dir", type=Path, required=True)
    universal_verify.add_argument("--dropbearkey", type=Path)
    universal_verify.set_defaults(handler=_universal_verify)

    runtime = commands.add_parser("runtime-candidate")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_stage = runtime_commands.add_parser("stage")
    _add_common(runtime_stage, inherited=True)
    runtime_stage.add_argument("--session-dir", type=Path, required=True)
    runtime_stage.add_argument("--host", required=True)
    runtime_stage.add_argument("--rootfs", type=Path, required=True)
    runtime_stage.add_argument("--image-provenance", type=Path, required=True)
    runtime_stage.add_argument("--expected-mtd3-sha256", required=True)
    runtime_stage.add_argument("--unsquashfs", type=Path)
    runtime_stage.add_argument(
        "--approve-volatile-runtime-restart",
        action="store_true",
        required=True,
    )
    runtime_stage.set_defaults(handler=_runtime_candidate_stage)
    runtime_status = runtime_commands.add_parser("status")
    _add_common(runtime_status, inherited=True)
    runtime_status.add_argument("--session-dir", type=Path, required=True)
    runtime_status.add_argument("--host", required=True)
    runtime_status.set_defaults(handler=_runtime_candidate_status)
    runtime_rollback = runtime_commands.add_parser("rollback")
    _add_common(runtime_rollback, inherited=True)
    runtime_rollback.add_argument("--session-dir", type=Path, required=True)
    runtime_rollback.add_argument("--host", required=True)
    runtime_rollback.add_argument(
        "--approve-volatile-runtime-restart",
        action="store_true",
        required=True,
    )
    runtime_rollback.set_defaults(handler=_runtime_candidate_rollback)

    private = commands.add_parser("private-config")
    private_commands = private.add_subparsers(dest="private_command", required=True)
    private_inspect = private_commands.add_parser("inspect")
    _add_common(private_inspect, inherited=True)
    private_inspect.add_argument("--private-config-dir", type=Path, required=True)
    private_inspect.add_argument("--session-dir", type=Path)
    private_inspect.set_defaults(handler=_private_config_inspect)
    private_rotate = private_commands.add_parser("rotate")
    _add_common(private_rotate, inherited=True)
    private_rotate.add_argument("--private-config-dir", type=Path, required=True)
    private_rotate.add_argument("--role", required=True)
    private_rotate.add_argument("--session-dir", type=Path)
    private_rotate.add_argument("--wpa-config", type=Path)
    private_rotate.set_defaults(handler=_private_config_rotate)

    candidate = commands.add_parser("candidate")
    candidate_commands = candidate.add_subparsers(dest="candidate_command", required=True)
    candidate_create = candidate_commands.add_parser("create")
    _add_common(candidate_create, inherited=True)
    candidate_create.add_argument("--spec", type=Path, required=True)
    candidate_create.set_defaults(handler=_candidate_create)
    candidate_record = candidate_commands.add_parser("record")
    _add_common(candidate_record, inherited=True)
    candidate_record.add_argument("--candidate-id", required=True)
    candidate_record.add_argument("--run", type=Path, required=True)
    candidate_record.set_defaults(handler=_candidate_record)
    candidate_show = candidate_commands.add_parser("status")
    _add_common(candidate_show, inherited=True)
    candidate_show.add_argument("--candidate-id", required=True)
    candidate_show.set_defaults(handler=_candidate_status)
    candidate_decision = candidate_commands.add_parser("decide")
    _add_common(candidate_decision, inherited=True)
    candidate_decision.add_argument("--candidate-id", required=True)
    candidate_decision.add_argument(
        "--decision", choices=("accepted", "rejected"), required=True
    )
    candidate_decision.add_argument("--reason", required=True)
    candidate_decision.set_defaults(handler=_candidate_decide)

    diagnose = commands.add_parser("diagnose-runtime")
    _add_common(diagnose, inherited=True)
    source = diagnose.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot-file", type=Path)
    source.add_argument("--session-dir", type=Path)
    diagnose.add_argument("--station-ipv4")
    diagnose.add_argument("--fault-class", choices=sorted(FAULT_CLASSES), required=True)
    diagnose.add_argument("--candidate-id")
    diagnose.set_defaults(handler=_diagnose_runtime)

    hypothesis = commands.add_parser("hypothesis-record")
    _add_common(hypothesis, inherited=True)
    hypothesis.add_argument("--snapshot-sha256", required=True)
    hypothesis.add_argument("--hypothesis-id", required=True)
    hypothesis.add_argument("--change-identity", required=True)
    hypothesis.add_argument(
        "--result",
        choices=("pending", "supported", "rejected", "inconclusive"),
        required=True,
    )
    hypothesis.add_argument("--evidence", required=True)
    hypothesis.set_defaults(handler=_hypothesis_record)
    from .user_cli_parser_stock_recovery import register_stock_recovery_commands

    register_stock_recovery_commands(facade, commands)
    return parser
