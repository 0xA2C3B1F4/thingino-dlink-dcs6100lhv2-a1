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
    _local_build_acquire = getattr(facade, '_local_build_acquire')
    _local_build_bootstrap = getattr(facade, '_local_build_bootstrap')
    _local_build_build = getattr(facade, '_local_build_build')
    _local_build_configure = getattr(facade, '_local_build_configure')
    _local_build_prepare = getattr(facade, '_local_build_prepare')
    _local_build_status = getattr(facade, '_local_build_status')
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
    card.add_argument(
        "--recovery-dir",
        type=Path,
        required=True,
        help="validated complete same-camera mtd0-mtd5 backup",
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
    stage_install_set.add_argument(
        "--recovery-dir",
        type=Path,
        required=True,
        help="validated complete same-camera mtd0-mtd5 backup",
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
    install.add_argument(
        "--recovery-dir",
        type=Path,
        required=True,
        help="validated complete same-camera mtd0-mtd5 backup",
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
        default=2,
        help="reserve capacity for one build or the two clean release builds",
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
