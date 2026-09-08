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
    parser = _ArgumentParser(prog="thingino-dlink", description=(
        "Install DCS-6100LHV2 A1 using validated local artifacts and same-camera recovery. "
        "Start with local-build prepare, then follow each command's next_command. "
        "Use project init to remember explicit paths for one camera. "
        "SD operations require current media identity and exact confirmations."
    ), epilog=(
        'After selecting DCS6100_PROJECT: Interactive: thingino-dlink universal configure. '
        'Automation: thingino-dlink --non-interactive --json local-build status. '
        'Public guide: docs/installer-projects.md.'
    ))
    _add_common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    from .user_cli_project import register
    register(parser, commands, facade)

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

    local_build = commands.add_parser("local-build", help="prepare, configure and build validated local artifacts",
        description="Start with prepare to check the host and external workspace. Bootstrap and acquire fetch locked public inputs. Recovery-assets prepares capture artifacts. Configure selects camera-private inputs; build or build-universal produces an inspected install set. These commands write the selected host workspace, not camera NOR.")
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

    universal = commands.add_parser("universal", help="provision and stage one camera's universal installation",
        description="Use an inspected universal build and validated same-camera recovery. Run init-session, configure, provision and authorize before stage. Stage writes SD files; physical boot later writes stock mtd1/mtd2 and final mtd1/mtd3. Handoff requires an observed stock-updater result; verify checks the running camera.")
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
    universal_stage.add_argument("--plan-only", action="store_true",
                                 help="validate artifacts and current media; return plan without writing")
    universal_stage.add_argument("--confirm-plan", help="SHA-256 from this invocation's reviewed --plan-only result")
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
    _installation_help(parser)
    return parser


def _installation_help(parser):
    """Command-specific guidance beside the existing argument contracts."""
    import argparse
    descriptions = {
        "local-build prepare": "Check host prerequisites and reserve the selected external build workspace. Writes host workspace metadata. Next: bootstrap.",
        "local-build status": "Inspect the selected build workspace and missing prerequisites without building. Use before choosing the next explicit command.",
        "local-build bootstrap": "Prepare public source/build inputs in the selected workspace. Requires prepare. Next: acquire.",
        "local-build acquire": "Acquire and verify hash-locked public inputs in the selected workspace. Requires bootstrap and network access. Next: recovery-assets or a configured build.",
        "local-build recovery-assets": "Build recovery capture artifacts in the selected host workspace. Requires acquired inputs. Next: select the exact-original or UARTless functional recovery procedure.",
        "local-build configure": "Select private paths, data action and confirmed Wi-Fi input. Writes private configuration and build settings. Interactive mode asks for missing choices; automation supplies every selection and --secrets-fd. Next: build.",
        "local-build build": "Build and inspect a camera-specific install set from validated settings or explicit private inputs. Writes the selected build workspace. Requires independent current Wi-Fi confirmation. Next: inspect and stage that install set.",
        "local-build build-universal": "Build and inspect model-universal artifacts from validated vendor inputs. Writes the selected build workspace and model signing files. Next: universal init-session.",
        "universal init-session": "Validate functional recovery and protected readbacks, then create a camera-bound private session in --output-dir. Requires ssh-keygen and dropbearkey. Next: configure.",
        "universal configure": "Generate camera-private configuration in --output-dir using the selected session and confirmed Wi-Fi input. Next: provision.",
        "universal provision": "Validate the universal bundle and recovery, then write the signed camera sidecar and JFFS2 data image. Requires configured private inputs, signing tools, unsquashfs and mkfs.jffs2. On macOS pass --mkfs-jffs2 scripts/run_container_mkfs_jffs2.sh with DCS6100_BUILDER_IMAGE set to the recorded immutable image ID; see docs/installer-projects.md. Next: authorize.",
        "universal authorize": "Bind this camera, recovery session, universal firmware and provisioning in --output-dir. Writes authorization artifacts on the host. Next: inspect a stage --plan-only result.",
        "universal stage": "Validate the complete camera/artifact tuple and current card. --plan-only is read-only. Execution writes verified SD files and arms a future bootstrap, activation last. Confirm the printed plan, then follow physical boot instructions before handoff.",
        "universal handoff": "Revalidate the selected card and camera tuple after observing stock mtd1/mtd2 completion. Passivates the stock updater on SD for the next boot. Requires exact physical-result and device confirmations. Next: boot the final installer and verify.",
        "universal evacuate-recovery": "Copy and validate an existing recovery checkpoint into --output-dir before removing its SD copy. Requires current media and exact device confirmation. Next: stage the new camera-bound install set.",
        "universal verify": "Discover the selected session's mDNS name and verify the running camera using its retained SSH key and station pin. Requires dropbearkey, completed physical installation and network reachability. There is no host override. Host project status does not replace this check.",
        "project init": "Create a new private camera path project. Supply --project or DCS6100_PROJECT and a local --name; optionally --build-root or a command-specific --paths JSON map. Creates project metadata and its private output directory. Next: local-build prepare or project status.",
        "project status": "Read remembered completion and content identities without resuming work. Changed inputs or outputs require review. A completed record does not confirm a physical boot or authorize writing.",
        "project attach": "Select existing inputs once for all dependent installation stages. Requires explicit role paths; validates available same-camera recovery. Writes private project metadata. Changed inputs invalidate dependent completion. Next: project status.",
        "stock-recovery uartless-prepare": "Requires the validated recovery-assets package and manifest. Select a current mounted FAT32 card. Writes only the inert UARTCAP.PSV file on SD; no host NOR writes. Next: inspect uartless-authorize --plan-only.",
        "stock-recovery uartless-authorize": "Requires the exact staged capture package and current card. Review --plan-only and confirm WRITE-MTD1-MTD2. Renames the SD package to arm the stock updater; the later physical boot writes mtd1 and mtd2. Next: observe updater completion before uartless-handoff.",
        "stock-recovery uartless-handoff": "Requires operator-observed MTD1-MTD2-WRITTEN completion and the same capture package. Revalidates current SD and passivates its updater. Project status cannot supply the physical confirmation. Next: boot the passive card for capture, then uartless-validate.",
        "stock-recovery uartless-validate": "Requires completed DCS6100F collector output, the authorized package and a confirmed private output directory. Validates duplicate reads, preserved partitions and vendor material; writes host recovery evidence. This is functional recovery after mtd1/mtd2 replacement, not an exact-original backup. Next: local-build build-universal.",
        "stock-recovery backup-prepare": "Requires collector kernel, config, MMC module and an explicit media preflight/device confirmation. Builds and stages a read-only RAM collector on SD. No host NOR writes. Next: backup-capture with a confirmed UART device.",
        "stock-recovery backup-capture": "Requires the staged read-only collector, kernel config and exact UART-device confirmation. Boots the collector through UART to capture duplicate NOR reads onto SD. No NOR writes. Next: return the card and run backup-validate.",
        "stock-recovery backup-validate": "Requires either an existing exact recovery directory or complete collector output and a confirmed private destination. Validates exact-original duplicate backups; collector mode writes host evidence. Next: select current protected readbacks for same-camera recovery validation.",
        "stock-recovery restore-prepare": "Requires exact-original recovery and current same-camera protected readbacks. Writes a validated stock-restore artifact set to the host output directory. Next: restore-inspect; this command does not restore camera NOR.",
        "stock-recovery restore-inspect": "Requires exact recovery, current protected readbacks and the selected restore output. Revalidates the restore set without camera writes. Next: explicitly choose the legacy live or SD restore procedure.",
        "stock-recovery live-prepare": "Requires exact recovery, current readbacks and collector tools. Creates a RAM restore set and stages it on confirmed SD. Next: live-authorize; camera restoration is not yet proven.",
        "stock-recovery live-authorize": "Requires the validated RAM restore set, same-camera evidence and exact restore/device confirmations. Arms the selected SD set. Next: live-restore with separately confirmed UART transport.",
        "stock-recovery live-restore": "Requires the authorized RAM set, same-camera recovery and exact UART selection. Runs the bounded legacy physical restore. Next: inspect its result and live-passivate; never infer success from project metadata.",
        "stock-recovery live-passivate": "Requires validated recovery, RAM set and current confirmed SD. Passivates completed restore files. Next: verify the camera's physical stock boot independently.",
        "stock-recovery sd-prepare": "Requires exact recovery, protected readbacks, kernel/config/MMC inputs and confirmed SD. Builds and stages the legacy stock-restore bootstrap. Next: sd-confirmation, then explicit sd-authorize.",
        "stock-recovery sd-confirmation": "Requires the selected card and media preflight. Reads the exact authorization, handoff or retry phrase for that staged set. No writes. Next: review the named phase before explicitly invoking it.",
        "stock-recovery sd-authorize": "Requires a validated restore set and the exact current restore/device confirmations. Arms the SD bootstrap for the bounded restore. Next: observe the actual boot result before sd-handoff.",
        "stock-recovery sd-handoff": "Requires the validated set and exact observed stock-updater handoff phrase. Passivates the selected updater on SD. Next: follow the returned physical actions; project records are not boot evidence.",
        "stock-recovery sd-retry": "Requires reviewed interrupted-restore evidence and the exact retry/device confirmations. Explicitly reauthorizes the bounded restore. No automatic retry is available. Next: observe physical restore/readback results.",
        "stock-recovery sd-passivate": "Requires validated restore evidence and current confirmed SD. Passivates the completed bootstrap. Next: independently verify physical stock restoration.",
    }
    def visit(current, prefix=""):
        if prefix in descriptions:
            current.description = descriptions[prefix]
            if prefix.startswith(("universal ", "stock-recovery uartless-")):
                example = prefix + (' --mkfs-jffs2 "$DCS6100_MKFS_JFFS2"' if prefix == "universal provision" else "")
                current.epilog = ("After selecting DCS6100_PROJECT, its inputs and the host tools in the guide. Interactive: thingino-dlink " + example + ". "
                    "Automation: select --non-interactive --json; media writes require --whole-device, "
                    "--mount-root and exact confirmations from --plan-only. See docs/installer-projects.md.")
        media_commands = {"universal stage", "universal handoff", "universal evacuate-recovery",
                          "stock-recovery uartless-prepare", "stock-recovery uartless-authorize", "stock-recovery uartless-handoff"}
        if prefix in media_commands:
            for action in current._actions:
                if action.dest in {"whole_device", "mount_root", "media_preflight", "confirm_physical_device",
                                   "confirm_write_set", "confirm_stock_uboot_result"}:
                    action.required = False
            existing = {action.dest for action in current._actions}
            for flag, options in (("--whole-device", {}), ("--confirm-target", {}),
                                  ("--confirm-write-set", {}), ("--confirm-plan", {}),
                                  ("--plan-only", {"action": "store_true"})):
                if flag[2:].replace("-", "_") not in existing:
                    current.add_argument(flag, **options)
        if prefix == "stock-recovery uartless-validate":
            for action in current._actions:
                if action.dest in {"collector_dir", "confirm_output_dir"}:
                    action.required = False
            current.add_argument("--whole-device")
            current.add_argument("--mount-root", type=__import__("pathlib").Path)
        if prefix == "universal provision":
            for action in current._actions:
                if action.dest in {"unsquashfs", "mkfs_jffs2"}:
                    action.required = False
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    visit(child, (prefix + " " + name).strip())
    visit(parser)
