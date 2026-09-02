"""One command family behind the stable guided CLI facade."""

from __future__ import annotations

def _local_build_prepare(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _local_build_document = getattr(facade, '_local_build_document')
    prepare_local_build_workspace = getattr(facade, 'prepare_local_build_workspace')
    remember_local_build_workspace = getattr(facade, 'remember_local_build_workspace')
    resolve_local_build_workspace = getattr(facade, 'resolve_local_build_workspace')
    Path = getattr(facade, 'Path')
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = prepare_local_build_workspace(
        build_root=build_root,
        build_count=arguments.build_count,
    )
    pointer = remember_local_build_workspace(
        build_root=Path(str(result["build_root"])),
        work_dir=arguments.work_dir,
    )
    result = {**result, "workspace_pointer": str(pointer)}
    return _local_build_document("local-build prepare", result)

def _local_build_status(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _local_build_document = getattr(facade, '_local_build_document')
    local_build_workspace_status = getattr(facade, 'local_build_workspace_status')
    resolve_local_build_workspace = getattr(facade, 'resolve_local_build_workspace')
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = local_build_workspace_status(build_root=build_root)
    return _local_build_document("local-build status", result)

def _local_build_bootstrap(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    bootstrap_public_build_inputs = getattr(facade, 'bootstrap_public_build_inputs')
    resolve_local_build_workspace = getattr(facade, 'resolve_local_build_workspace')
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = bootstrap_public_build_inputs(build_root=build_root)
    return _document(
        "local-build bootstrap",
        ok=True,
        phase="local-build-public-bootstrap-ready",
        result=result,
    )

def _local_build_acquire(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    acquire_locked_public_inputs = getattr(facade, 'acquire_locked_public_inputs')
    resolve_local_build_workspace = getattr(facade, 'resolve_local_build_workspace')
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = acquire_locked_public_inputs(build_root=build_root)
    return _document(
        "local-build acquire",
        ok=True,
        phase="local-build-locked-public-inputs-ready",
        result=result,
    )


def _prompt_local_build_value(
    facade: object,
    *,
    label: str,
    explanation: str,
    default: str,
) -> str:
    sys = getattr(facade, 'sys')
    print(explanation, file=sys.stderr)
    print(f"{label} [{default}]: ", end="", file=sys.stderr, flush=True)
    value = sys.stdin.readline()
    if value == "":
        raise getattr(facade, 'UserInstallerError')(
            f"cannot read interactive setting: {label}"
        )
    return value.strip() or default


def _local_build_configure(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, '_document')
    Path = getattr(facade, 'Path')
    configure_local_build_settings = getattr(facade, 'configure_local_build_settings')
    default_local_build_private_root = getattr(facade, 'default_local_build_private_root')
    read_confirmed_private_input = getattr(facade, 'read_confirmed_private_input')
    resolve_local_build_workspace = getattr(facade, 'resolve_local_build_workspace')
    validate_local_build_setting_inputs = getattr(facade, 'validate_local_build_setting_inputs')
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    default_private = default_local_build_private_root(build_root)
    private_root = arguments.private_root or Path(
        _prompt_local_build_value(
            facade,
            label="Private input root",
            explanation=(
                "Stores local camera files, generated credentials, and the saved build plan. "
                "It must be owned by you with mode 0700."
            ),
            default=str(default_private),
        )
    )
    defaults = {
        "vendor_bundle_dir": private_root / "vendor-bundle",
        "media_closure_dir": private_root / "media-closure",
        "session_dir": private_root / "recovery-session",
        "raptor_rwd_artifact": private_root / "raptor-rwd.tar.gz",
    }
    prompts = {
        "vendor_bundle_dir": (
            "Vendor bundle directory",
            "Output of the read-only stock-mtd3 vendor acquisition for this A1 camera.",
        ),
        "media_closure_dir": (
            "Media closure directory",
            "Previously validated, hash-locked C1 media runtime closure.",
        ),
        "session_dir": (
            "Recovery session directory",
            "Private recovery session whose SSH identity and service credential bind this build.",
        ),
        "raptor_rwd_artifact": (
            "Raptor RWD artifact",
            "Reviewed source-built raptor-rwd.tar.gz matching this checkout's contract.",
        ),
    }
    selected: dict[str, Path] = {}
    for field, default in defaults.items():
        provided = getattr(arguments, field)
        label, explanation = prompts[field]
        selected[field] = provided or Path(
            _prompt_local_build_value(
                facade,
                label=label,
                explanation=explanation,
                default=str(default),
            )
        )
    data_mode = arguments.data_mode or _prompt_local_build_value(
        facade,
        label="Data action",
        explanation=(
            "initialize creates the first backup/checkpoint; preserve keeps the data region "
            "byte-identical; factory-reset explicitly erases only the data region."
        ),
        default="initialize",
    )
    if data_mode not in {"initialize", "preserve", "factory-reset"}:
        raise getattr(facade, 'UserInstallerError')(
            "data action must be initialize, preserve, or factory-reset"
        )
    validate_local_build_setting_inputs(
        build_root=build_root,
        private_root=private_root,
        vendor_bundle_dir=selected["vendor_bundle_dir"],
        media_closure_dir=selected["media_closure_dir"],
        session_dir=selected["session_dir"],
        raptor_rwd_artifact=selected["raptor_rwd_artifact"],
    )
    ssid, passphrase, confirmation_ssid, confirmation_passphrase = (
        read_confirmed_private_input(secrets_fd=arguments.secrets_fd)
    )
    result = configure_local_build_settings(
        build_root=build_root,
        work_dir=arguments.work_dir,
        private_root=private_root,
        vendor_bundle_dir=selected["vendor_bundle_dir"],
        media_closure_dir=selected["media_closure_dir"],
        session_dir=selected["session_dir"],
        raptor_rwd_artifact=selected["raptor_rwd_artifact"],
        data_mode=data_mode,
        ssid=ssid,
        passphrase=passphrase,
        confirmation_ssid=confirmation_ssid,
        confirmation_passphrase=confirmation_passphrase,
    )
    return _document(
        "local-build configure",
        ok=True,
        phase="local-build-private-inputs-configured",
        result=result,
    )


def _local_build_build(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    Path = getattr(facade, 'Path')
    build_local_install_set = getattr(facade, 'build_local_install_set')
    load_local_build_settings = getattr(facade, 'load_local_build_settings')
    resolve_local_build_workspace = getattr(facade, 'resolve_local_build_workspace')
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    fields = (
        "vendor_bundle_dir",
        "media_closure_dir",
        "private_config_dir",
        "expected_wpa_config",
        "session_dir",
        "raptor_rwd_artifact",
    )
    provided = {field: getattr(arguments, field) for field in fields}
    used = [field for field, value in provided.items() if value is not None]
    if arguments.settings is not None and used:
        raise getattr(facade, 'UserInstallerError')(
            "--settings cannot be combined with explicit private input paths"
        )
    if used:
        if len(used) != len(fields):
            missing = ", ".join(
                f"--{field.replace('_', '-')}"
                for field, value in provided.items()
                if value is None
            )
            raise getattr(facade, 'UserInstallerError')(
                f"explicit build mode requires all private input paths; missing {missing}"
            )
        selected = provided
        data_mode = arguments.data_mode or "initialize"
    else:
        settings = load_local_build_settings(
            settings_path=arguments.settings,
            work_dir=arguments.work_dir,
        )
        if Path(str(settings["build_root"])).resolve(strict=False) != build_root.resolve(strict=False):
            raise getattr(facade, 'UserInstallerError')(
                "saved settings belong to a different local build workspace"
            )
        selected = {field: Path(str(settings[field])) for field in fields}
        saved_mode = str(settings["data_mode"])
        if arguments.data_mode is not None and arguments.data_mode != saved_mode:
            raise getattr(facade, 'UserInstallerError')(
                "--data-mode differs from the configured data action"
            )
        data_mode = saved_mode
    result = build_local_install_set(
        build_root=build_root,
        vendor_bundle_dir=selected["vendor_bundle_dir"],
        media_closure_dir=selected["media_closure_dir"],
        private_config_dir=selected["private_config_dir"],
        expected_wpa_config_path=selected["expected_wpa_config"],
        session_dir=selected["session_dir"],
        raptor_rwd_artifact=selected["raptor_rwd_artifact"],
        data_mode=data_mode,
    )
    return _document(
        "local-build build",
        ok=True,
        phase="local-build-install-set-inspected",
        result=result,
    )

def _build_personal_mtd3(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    _load_config = getattr(facade, '_load_config')
    _validate_config = getattr(facade, '_validate_config')
    build_personal_candidate = getattr(facade, 'build_personal_candidate')
    work_dir = arguments.work_dir.expanduser().resolve(strict=True)
    validated = _validate_config(_load_config(work_dir))
    session_dir = arguments.session_dir or validated["session_dir"]
    result = build_personal_candidate(
        session_dir=session_dir,
        base_rootfs_path=validated["base_rootfs"],
        private_config_dir=arguments.private_config_dir,
        expected_wpa_config_path=arguments.expected_wpa_config,
        vendor_bundle_dir=arguments.vendor_bundle_dir,
        media_closure_dir=validated["media_closure_dir"],
        output_dir=arguments.output_dir,
        mksquashfs=validated["mksquashfs"],
        unsquashfs=validated["unsquashfs"],
    )
    return _document(
        "build-personal-mtd3",
        ok=True,
        phase="personal-mtd3-built",
        result=result,
    )

def _runtime_candidate_stage(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    shlex = getattr(facade, 'shlex')
    stage_runtime_candidate = getattr(facade, 'stage_runtime_candidate')
    result = stage_runtime_candidate(
        session_dir=arguments.session_dir,
        host=arguments.host,
        rootfs_path=arguments.rootfs,
        provenance_path=arguments.image_provenance,
        expected_mtd3_sha256=arguments.expected_mtd3_sha256,
        unsquashfs=arguments.unsquashfs,
    )
    rollback = shlex.join(
        [
            "python3",
            "-m",
            "installer.user_cli",
            "runtime-candidate",
            "rollback",
            "--json",
            "--session-dir",
            str(arguments.session_dir),
            "--host",
            arguments.host,
            "--approve-volatile-runtime-restart",
        ]
    )
    return _document(
        "runtime-candidate stage",
        ok=True,
        phase="runtime-candidate-active",
        next_command=rollback,
        result={**result, "rollback_command": rollback},
    )

def _runtime_candidate_status(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    runtime_candidate_status = getattr(facade, 'runtime_candidate_status')
    result = runtime_candidate_status(
        session_dir=arguments.session_dir,
        host=arguments.host,
    )
    return _document(
        "runtime-candidate status",
        ok=True,
        phase="runtime-candidate-status",
        result=result,
    )

def _runtime_candidate_rollback(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    rollback_runtime_candidate = getattr(facade, 'rollback_runtime_candidate')
    result = rollback_runtime_candidate(
        session_dir=arguments.session_dir,
        host=arguments.host,
    )
    return _document(
        "runtime-candidate rollback",
        ok=True,
        phase="runtime-candidate-rolled-back",
        result=result,
    )

def _private_config_inspect(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    inspect_private_config = getattr(facade, 'inspect_private_config')
    result = inspect_private_config(
        output_dir=arguments.private_config_dir,
        session_dir=arguments.session_dir,
    )
    return _document(
        "private-config inspect",
        ok=True,
        phase="private-config-inspected",
        result=result,
    )

def _private_config_rotate(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _document = getattr(facade, '_document')
    _regular = getattr(facade, '_regular')
    rotate_private_config_role = getattr(facade, 'rotate_private_config_role')
    wpa_config = None
    if arguments.wpa_config is not None:
        path = _regular(arguments.wpa_config, "private WPA configuration")
        if path.stat().st_size > 4096:
            raise UserInstallerError("private WPA configuration exceeds its size limit")
        wpa_config = path.read_bytes()
    result = rotate_private_config_role(
        output_dir=arguments.private_config_dir,
        role=arguments.role,
        session_dir=arguments.session_dir,
        wpa_config=wpa_config,
    )
    return _document(
        "private-config rotate",
        ok=True,
        phase="private-config-rotated",
        result=result,
    )
