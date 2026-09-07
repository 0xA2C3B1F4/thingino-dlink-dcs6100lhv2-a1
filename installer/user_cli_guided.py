"""One command family behind the stable guided CLI facade."""

from __future__ import annotations


def _validate_selected_recovery(facade: object, arguments: argparse.Namespace) -> object:
    exact = getattr(arguments, "recovery_dir", None)
    functional = getattr(arguments, "functional_recovery_dir", None)
    if (exact is None) == (functional is None):
        raise getattr(facade, "UserInstallerError")(
            "select exactly one exact or functional recovery directory"
        )
    if functional is not None:
        validate = getattr(facade, "validate_functional_recovery_boundary")
        return validate(
            recovery_dir=functional,
            preserved_readback_dir=arguments.preserved_readback_dir,
        )
    validate = getattr(facade, "validate_existing_recovery_boundary")
    return validate(
        recovery_dir=exact,
        preserved_readback_dir=arguments.preserved_readback_dir,
    )


def _preflight(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    BOOTSTRAP_NAME = getattr(facade, 'BOOTSTRAP_NAME')
    Path = getattr(facade, 'Path')
    SCHEMA_VERSION = getattr(facade, 'SCHEMA_VERSION')
    UserInstallerError = getattr(facade, 'UserInstallerError')
    __file__ = getattr(facade, '__file__')
    _config_path = getattr(facade, '_config_path')
    _directory = getattr(facade, '_directory')
    _document = getattr(facade, '_document')
    _load_config = getattr(facade, '_load_config')
    _now = getattr(facade, '_now')
    _regular = getattr(facade, '_regular')
    _target = getattr(facade, '_target')
    _tool = getattr(facade, '_tool')
    _validate_config = getattr(facade, '_validate_config')
    atomic_write = getattr(facade, 'atomic_write')
    json = getattr(facade, 'json')
    os = getattr(facade, 'os')
    work_dir = arguments.work_dir.expanduser().resolve()
    existing: dict[str, object] | None = None
    if _config_path(work_dir).is_file():
        existing = _load_config(work_dir)
    configurable_fields = (
        "session_dir",
        "base_rootfs",
        "media_closure_dir",
        "install_set_dir",
        "recovery_package",
        "mksquashfs",
        "unsquashfs",
        "macos_wifi_helper",
        "macos_station_wifi_helper",
    )
    provided_fields = [
        field for field in configurable_fields if getattr(arguments, field) is not None
    ]
    if existing is not None and not provided_fields:
        config = dict(existing)
    elif existing is not None and provided_fields in (
        ["install_set_dir"],
        ["recovery_package"],
    ):
        package_argument = (
            arguments.recovery_package
            if arguments.recovery_package is not None
            else arguments.install_set_dir / BOOTSTRAP_NAME
        )
        requested_package = _regular(
            package_argument, "recovery mtd1/mtd2 package"
        )
        recorded_package = existing.get("recovery_package")
        if recorded_package is not None and Path(str(recorded_package)).resolve() != requested_package:
            raise UserInstallerError(
                "refusing to replace an existing recovery package binding"
            )
        config = {
            **existing,
            "recovery_package": str(requested_package),
        }
    elif existing is not None and provided_fields == ["media_closure_dir"]:
        if existing.get("media_closure_dir") is not None:
            raise UserInstallerError("refusing to replace the bound media closure")
        config = {
            **existing,
            "media_closure_dir": str(
                _directory(arguments.media_closure_dir, "private media closure")
            ),
        }
    else:
        if (
            arguments.session_dir is None
            or arguments.base_rootfs is None
            or arguments.media_closure_dir is None
        ):
            raise UserInstallerError(
                "first preflight requires --session-dir, --base-rootfs, and --media-closure-dir"
            )
        project_root = Path(__file__).resolve().parents[1]
        project_helper = project_root / "scripts/platform/macos_recovery_wifi.swift"
        project_station_helper = project_root / "scripts/platform/macos_station_wifi.swift"
        values = {
            "base_rootfs": str(_regular(arguments.base_rootfs, "base Thingino rootfs")),
            "media_closure_dir": str(
                _directory(arguments.media_closure_dir, "private media closure")
            ),
            "recovery_package": (
                str(_regular(arguments.recovery_package, "recovery mtd1/mtd2 package"))
                if arguments.recovery_package is not None
                else (
                    str(
                        _regular(
                            arguments.install_set_dir / BOOTSTRAP_NAME,
                            "recovery mtd1/mtd2 package",
                        )
                    )
                    if arguments.install_set_dir is not None
                    else None
                )
            ),
            "macos_wifi_helper": str(
                _regular(arguments.macos_wifi_helper or project_helper, "macOS Wi-Fi helper")
            ),
            "macos_station_wifi_helper": str(
                _regular(
                    arguments.macos_station_wifi_helper or project_station_helper,
                    "macOS station Wi-Fi helper",
                )
            ),
            "mksquashfs": str(_tool(arguments.mksquashfs, "mksquashfs")),
            "session_dir": str(_directory(arguments.session_dir, "session directory")),
            "unsquashfs": str(_tool(arguments.unsquashfs, "unsquashfs")),
        }
        config = {
            **values,
            "created_at": _now(),
            "schema_version": SCHEMA_VERSION,
            "target": _target(),
        }
        if existing is not None:
            comparable = {
                key: existing.get(key)
                for key in (
                    "base_rootfs",
                    "media_closure_dir",
                    "recovery_package",
                    "macos_wifi_helper",
                    "macos_station_wifi_helper",
                    "mksquashfs",
                    "session_dir",
                    "target",
                    "unsquashfs",
                )
            }
            requested = {key: config.get(key) for key in comparable}
            if comparable != requested:
                raise UserInstallerError(
                    "refusing to replace an existing installer configuration"
                )
            config = existing
    validated = _validate_config(config)
    binding_fields = {
        "recovery_package_sha256": validated["recovery_package_sha256"],
        "recovery_session_binding_sha256": validated[
            "recovery_session_binding_sha256"
        ],
    }
    config.update(binding_fields)
    if existing is None or config != existing:
        work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(work_dir, 0o700)
        atomic_write(
            _config_path(work_dir),
            (json.dumps(config, indent=2, sort_keys=True) + "\n").encode(),
        )
        _config_path(work_dir).chmod(0o600)
    return _document(
        "preflight",
        ok=True,
        phase="host-ready",
        next_command="thingino-dlink prepare-card",
        result={
            "recovery_package_available": validated["recovery_package"] is not None,
            "recovery_package_sha256": validated["recovery_package_sha256"],
            "recovery_session_bound": (
                validated["recovery_session_binding_sha256"] is not None
            ),
            "session_mdns_name": validated["station_mdns_name"],
            "work_directory": str(work_dir),
        },
    )

def _prepare_card(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    BOOTSTRAP_NAME = getattr(facade, 'BOOTSTRAP_NAME')
    CARD_PREFLIGHT_NAME = getattr(facade, 'CARD_PREFLIGHT_NAME')
    Path = getattr(facade, 'Path')
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _directory = getattr(facade, '_directory')
    _document = getattr(facade, '_document')
    _load_config = getattr(facade, '_load_config')
    _validate_config = getattr(facade, '_validate_config')
    atomic_write = getattr(facade, 'atomic_write')
    create_preflight_document = getattr(facade, 'create_preflight_document')
    hashlib = getattr(facade, 'hashlib')
    json = getattr(facade, 'json')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    matching_update_filenames = getattr(facade, 'matching_update_filenames')
    parse_package = getattr(facade, 'parse_package')
    read_snapshot = getattr(facade, 'read_snapshot')
    stage_verified_package = getattr(facade, 'stage_verified_package')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    work_dir = arguments.work_dir.expanduser().resolve(strict=True)
    validated = _validate_config(_load_config(work_dir))
    recovery_decision = _validate_selected_recovery(facade, arguments)
    recovery_package = validated["recovery_package"]
    if not isinstance(recovery_package, Path):
        raise UserInstallerError("preflight did not bind a recovery mtd1/mtd2 package")
    target_confirmation = arguments.confirm_target
    if target_confirmation is None and not arguments.json:
        target_confirmation = input(
            "Check the camera label and type DCS-6100LHV2-A1 to confirm the target: "
        ).strip()
    if target_confirmation != "DCS-6100LHV2-A1":
        raise UserInstallerError("target label was not confirmed as DCS-6100LHV2 A1")
    mount_root = _directory(arguments.mount_root, "mounted SD root")
    preflight_document = create_preflight_document(
        whole_device=arguments.whole_device,
        mount_root=mount_root,
    )
    preflight_path = work_dir / CARD_PREFLIGHT_NAME
    atomic_write(
        preflight_path,
        (json.dumps(preflight_document, indent=2, sort_keys=True) + "\n").encode(),
    )
    preflight_path.chmod(0o600)
    preflight = load_media_preflight(preflight_path, expected_root=mount_root)
    confirmation = arguments.confirm_physical_device
    if confirmation is None and not arguments.json:
        confirmation = input(
            f"Type the exact whole device {preflight.physical_device} to confirm SD staging: "
        ).strip()
    if confirmation is None:
        raise UserInstallerError("JSON mode requires --confirm-physical-device")
    package = read_snapshot(recovery_package)
    try:
        validate_bootstrap(parse_package(package, require_project_header=True))
    except ValueError as exc:
        raise UserInstallerError("recovery package changed or is unsafe") from exc
    destination = mount_root / BOOTSTRAP_NAME
    existing_matches = matching_update_filenames(
        entry.name
        for entry in mount_root.iterdir()
        if entry.is_file() and not entry.is_symlink()
    )
    if (
        existing_matches == [BOOTSTRAP_NAME]
        and destination.is_file()
        and not destination.is_symlink()
        and destination.read_bytes() == package
    ):
        files = {BOOTSTRAP_NAME: hashlib.sha256(package).hexdigest()}
        already_prepared = True
    else:
        package_sha256 = stage_verified_package(
            package,
            root=mount_root,
            output_name=BOOTSTRAP_NAME,
            preflight=preflight,
            confirmed_physical_device=confirmation,
        )
        files = {BOOTSTRAP_NAME: package_sha256}
        already_prepared = False
    return _document(
        "prepare-card",
        ok=True,
        phase="recovery-card-ready",
        physical_actions=[
            "Confirm the bottom label says DCS-6100LHV2 and hardware revision A1.",
            "Power the camera off.",
            "Insert this verified SD card.",
            "Power the camera on; stock U-Boot will write only mtd1 and mtd2.",
            "Wait for the flashing-complete LED pattern, then power the camera off.",
            "Remove the SD card and power the camera on again.",
        ],
        next_command="thingino-dlink install",
        result={
            "already_prepared": already_prepared,
            "duplicate_backup_accepted": recovery_decision.recovery_images == 2,
            "full_flash_reconstruction_accepted": getattr(
                recovery_decision, "original_complete_backup_accepted", True
            ),
            "functional_recovery_accepted": getattr(
                recovery_decision, "functional_recovery_accepted", False
            ),
            "original_preserved_mtd": list(
                getattr(
                    recovery_decision,
                    "original_preserved_mtd",
                    (0, 1, 2, 3, 4, 5),
                )
            ),
            "recovery_mode": getattr(
                recovery_decision, "mode", "existing-verified-same-device-pair"
            ),
            "same_device_binding_accepted": True,
            "files": files,
            "future_physical_boot_writes_mtd": [1, 2],
            "operator_confirmed_target": "DCS-6100LHV2-A1",
            "physical_device": preflight.physical_device,
        },
    )

def _stage_install_set(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    BOOTSTRAP_NAME = getattr(facade, 'BOOTSTRAP_NAME')
    CARD_PREFLIGHT_NAME = getattr(facade, 'CARD_PREFLIGHT_NAME')
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _directory = getattr(facade, '_directory')
    _document = getattr(facade, '_document')
    _regular = getattr(facade, '_regular')
    atomic_write = getattr(facade, 'atomic_write')
    create_preflight_document = getattr(facade, 'create_preflight_document')
    json = getattr(facade, 'json')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    os = getattr(facade, 'os')
    read_snapshot = getattr(facade, 'read_snapshot')
    stage_verified_install_set = getattr(facade, 'stage_verified_install_set')
    validate_install_set = getattr(facade, 'validate_install_set')
    recovery_decision = _validate_selected_recovery(facade, arguments)
    install_set = _directory(arguments.install_set_dir, "install-set directory")
    bootstrap = read_snapshot(_regular(install_set / BOOTSTRAP_NAME, "bootstrap"))
    stage2 = read_snapshot(_regular(install_set / "THINGINO2.BIN", "stage 2"))
    manifest = read_snapshot(
        _regular(install_set / "install-set.manifest.json", "install-set manifest")
    )
    payload = validate_install_set(
        bootstrap_bytes=bootstrap,
        stage2_bytes=stage2,
        manifest_bytes=manifest,
        bootstrap_name=BOOTSTRAP_NAME,
    )
    if payload.data_mode != arguments.expected_data_mode:
        raise UserInstallerError("install-set data mode was not confirmed")
    target_confirmation = arguments.confirm_target
    if target_confirmation is None and not arguments.json:
        target_confirmation = input(
            "Check the camera label and type DCS-6100LHV2-A1 to confirm the target: "
        ).strip()
    if target_confirmation != "DCS-6100LHV2-A1":
        raise UserInstallerError("target label was not confirmed as DCS-6100LHV2 A1")
    mount_root = _directory(arguments.mount_root, "mounted SD root")
    preflight_document = create_preflight_document(
        whole_device=arguments.whole_device,
        mount_root=mount_root,
    )
    work_dir = arguments.work_dir.expanduser().resolve()
    if work_dir.exists() and (work_dir.is_symlink() or not work_dir.is_dir()):
        raise UserInstallerError("work directory is not a real directory")
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(work_dir, 0o700)
    preflight_path = work_dir / CARD_PREFLIGHT_NAME
    atomic_write(
        preflight_path,
        (json.dumps(preflight_document, indent=2, sort_keys=True) + "\n").encode(),
    )
    preflight_path.chmod(0o600)
    preflight = load_media_preflight(preflight_path, expected_root=mount_root)
    confirmation = arguments.confirm_physical_device
    if confirmation is None and not arguments.json:
        confirmation = input(
            f"Type the exact whole device {preflight.physical_device} to confirm SD staging: "
        ).strip()
    if confirmation is None:
        raise UserInstallerError("JSON mode requires --confirm-physical-device")
    files = stage_verified_install_set(
        bootstrap_bytes=bootstrap,
        stage2_bytes=stage2,
        manifest_bytes=manifest,
        root=mount_root,
        bootstrap_name=BOOTSTRAP_NAME,
        preflight=preflight,
        confirmed_physical_device=confirmation,
    )
    return _document(
        "stage-install-set",
        ok=True,
        phase="install-card-ready",
        physical_actions=[
            "Confirm the bottom label says DCS-6100LHV2 and hardware revision A1.",
            "Power the camera off before inserting or removing the verified SD card.",
            "Do not interrupt power while the installer reports a write or readback.",
            "After the completion signal, power off and remove the SD card before reboot.",
        ],
        result={
            "data_mode": payload.data_mode,
            "duplicate_backup_accepted": recovery_decision.recovery_images == 2,
            "full_flash_reconstruction_accepted": getattr(
                recovery_decision, "original_complete_backup_accepted", True
            ),
            "functional_recovery_accepted": getattr(
                recovery_decision, "functional_recovery_accepted", False
            ),
            "original_preserved_mtd": list(
                getattr(
                    recovery_decision,
                    "original_preserved_mtd",
                    (0, 1, 2, 3, 4, 5),
                )
            ),
            "recovery_mode": getattr(
                recovery_decision, "mode", "existing-verified-same-device-pair"
            ),
            "same_device_binding_accepted": True,
            "files": files,
            "operator_confirmed_target": "DCS-6100LHV2-A1",
            "physical_device": preflight.physical_device,
        },
    )

def _install(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _document = getattr(facade, '_document')
    _load_config = getattr(facade, '_load_config')
    _validate_config = getattr(facade, '_validate_config')
    complete_personal_install = getattr(facade, 'complete_personal_install')
    read_private_input = getattr(facade, 'read_private_input')
    read_snapshot = getattr(facade, 'read_snapshot')
    _validate_selected_recovery(facade, arguments)
    work_dir = arguments.work_dir.expanduser().resolve(strict=True)
    validated = _validate_config(_load_config(work_dir))
    install_work = work_dir / "install"
    ssid: str | None = None
    passphrase: str | None = None
    if not (install_work / "install-config").exists():
        ssid, passphrase = read_private_input(secrets_fd=arguments.secrets_fd)

    wifi_mode = getattr(arguments, "wifi_mode", "auto")
    manual_wifi_timeout = getattr(arguments, "manual_wifi_timeout", 120)
    if not 30 <= manual_wifi_timeout <= 300:
        raise UserInstallerError("manual Wi-Fi timeout must be 30 through 300 seconds")

    def wifi_notice(message: str) -> None:
        if not arguments.json:
            print(f"Wi-Fi action: {message}")

    def join_ap() -> None:
        join_recovery_ap(
            session_dir=validated["session_dir"],
            macos_helper=validated["macos_wifi_helper"],
            mode=wifi_mode,
            manual_timeout=manual_wifi_timeout,
            notify=wifi_notice,
        )

    def join_station() -> None:
        join_station_wifi(
            wpa_config=install_work / "install-config/wpa_supplicant.conf",
            macos_helper=validated["macos_station_wifi_helper"],
            mode=wifi_mode,
            notify=wifi_notice,
        )

    def progress(message: str, writing_now: bool) -> None:
        if arguments.json:
            return
        print(f"Installer stage: {message}")
        print(f"NOR write active now: {'yes' if writing_now else 'no'}")

    result = complete_personal_install(
        session_dir=validated["session_dir"],
        base_rootfs=read_snapshot(validated["base_rootfs"]),
        media_closure_dir=validated["media_closure_dir"],
        work_dir=install_work,
        ssid=ssid,
        passphrase=passphrase,
        expected_wpa_config_path=getattr(arguments, "expected_wpa_config", None),
        mksquashfs=validated["mksquashfs"],
        unsquashfs=validated["unsquashfs"],
        station_timeout=arguments.station_timeout,
        join_ap=join_ap,
        join_station=join_station,
        progress=progress,
    )
    written = result.get("written_mtd", [])
    readback = (
        written in ([], [3])
        and result.get("mtd3_read_back_verified") is True
        and result.get("mtd3_sha256") == result.get("image_sha256")
    )
    return _document(
        "install",
        ok=True,
        phase="installer-complete",
        written_mtd=written if isinstance(written, list) else [],
        read_back_verified=readback,
        next_command="thingino-dlink verify",
        result=result,
    )

def _status(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _config_path = getattr(facade, '_config_path')
    _document = getattr(facade, '_document')
    _load_config = getattr(facade, '_load_config')
    _next_for_state = getattr(facade, '_next_for_state')
    _state = getattr(facade, '_state')
    work_dir = arguments.work_dir.expanduser().resolve()
    if not _config_path(work_dir).is_file():
        return _document(
            "status",
            ok=True,
            phase="not-configured",
            next_command="thingino-dlink preflight",
            result={
                "current_run_written_mtd": [],
                "last_camera_proven_at": None,
                "last_camera_state": None,
                "local_phase": "not-configured",
                "mtd3_kind": None,
                "mtd3_sha256": None,
                "write_history": [],
            },
        )
    _load_config(work_dir)
    state = _state(work_dir)
    if state is None:
        phase = "configured"
        current: dict[str, object] = {"written_mtd": []}
        camera: dict[str, object] = {}
        history: list[object] = []
    else:
        phase = str(state.get("phase", "unknown"))
        current_value = state.get("current_run", {"written_mtd": []})
        camera_value = state.get("last_camera", {})
        history_value = state.get("write_history", [])
        current = current_value if isinstance(current_value, dict) else {"written_mtd": []}
        camera = camera_value if isinstance(camera_value, dict) else {}
        history = history_value if isinstance(history_value, list) else []
    written = current.get("written_mtd", [])
    return _document(
        "status",
        ok=True,
        phase=phase,
        written_mtd=written if isinstance(written, list) else [],
        read_back_verified=current.get("read_back_verified") is True,
        next_command=_next_for_state(state),
        result={
            "current_run_written_mtd": written,
            "last_camera_proven_at": camera.get("proven_at"),
            "last_camera_state": camera.get("state"),
            "local_phase": phase,
            "mtd3_kind": camera.get("mtd3_kind"),
            "mtd3_sha256": camera.get("mtd3_sha256"),
            "write_history": history,
        },
    )

def _verify(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    _document = getattr(facade, '_document')
    _expected_personal_image_sha256 = getattr(facade, '_expected_personal_image_sha256')
    _load_config = getattr(facade, '_load_config')
    _record_proof = getattr(facade, '_record_proof')
    _validate_config = getattr(facade, '_validate_config')
    prove_thingino_health = getattr(facade, 'prove_thingino_health')
    work_dir = arguments.work_dir.expanduser().resolve(strict=True)
    validated = _validate_config(_load_config(work_dir))
    expected = _expected_personal_image_sha256(work_dir)
    health = prove_thingino_health(
        session_dir=validated["session_dir"],
        expected_mtd3_sha256=expected,
    )
    _record_proof(
        work_dir,
        "last_management_verification",
        health,
        management=health,
    )
    return _document(
        "verify",
        ok=True,
        phase="management-verified",
        read_back_verified=health.get("mtd3_read_back_verified") is True,
        next_command="thingino-dlink verify-media",
        result=health,
    )

def _verify_media(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    MediaClosureError = getattr(facade, 'MediaClosureError')
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _directory = getattr(facade, '_directory')
    _document = getattr(facade, '_document')
    _expected_personal_image_sha256 = getattr(facade, '_expected_personal_image_sha256')
    _load_config = getattr(facade, '_load_config')
    _path_field = getattr(facade, '_path_field')
    _record_proof = getattr(facade, '_record_proof')
    _validate_config = getattr(facade, '_validate_config')
    load_media_closure = getattr(facade, 'load_media_closure')
    derive_rtsp_viewer_credential = getattr(facade, 'derive_rtsp_viewer_credential')
    load_service_credential = getattr(facade, 'load_service_credential')
    prove_thingino_health = getattr(facade, 'prove_thingino_health')
    prove_thingino_media = getattr(facade, 'prove_thingino_media')
    verify_rtsp_h264_1080p = getattr(facade, 'verify_rtsp_h264_1080p')
    work_dir = arguments.work_dir.expanduser().resolve(strict=True)
    config = _load_config(work_dir)
    requested_closure = getattr(arguments, "media_closure_dir", None)
    if requested_closure is not None:
        requested = _directory(requested_closure, "private media closure")
        configured = config.get("media_closure_dir")
        if configured is not None:
            bound = _directory(
                _path_field(config, "media_closure_dir"), "private media closure"
            )
            if requested != bound:
                raise UserInstallerError("refusing to replace the bound media closure")
        config = {**config, "media_closure_dir": str(requested)}
    validated = _validate_config(config)
    expected = _expected_personal_image_sha256(work_dir)
    health = prove_thingino_health(
        session_dir=validated["session_dir"],
        expected_mtd3_sha256=expected,
    )
    media = prove_thingino_media(
        session_dir=validated["session_dir"],
        station_ipv4=str(health["station_ipv4"]),
    )
    try:
        load_media_closure(validated["media_closure_dir"])
    except MediaClosureError as exc:
        raise UserInstallerError("cannot read the hash-locked C1 media closure") from exc
    password = load_service_credential(validated["session_dir"])
    rtsp = verify_rtsp_h264_1080p(
        host=str(media["station_ipv4"]),
        username="viewer",
        password=derive_rtsp_viewer_credential(password + b"\n")[:-1],
    )
    media = {
        **media,
        "installer_health_dependency": "passed-live",
        "rtsp": rtsp,
        "rtsp_image_verified": True,
    }
    proof = {"management": health, "media": media}
    _record_proof(
        work_dir,
        "last_media_verification",
        proof,
        management=health,
    )
    return _document(
        "verify-media",
        ok=True,
        phase="media-verified",
        read_back_verified=health.get("mtd3_read_back_verified") is True,
        result=media,
    )

def _workflow_preflight(facade: object, arguments: argparse.Namespace) -> dict[str, object]:
    UserInstallerError = getattr(facade, 'UserInstallerError')
    _document = getattr(facade, '_document')
    observe_camera_state = getattr(facade, 'observe_camera_state')
    run_workflow_preflight = getattr(facade, 'run_workflow_preflight')
    inputs = {
        "project_root": arguments.project_root,
        "mode": arguments.mode,
        "data_volume": arguments.data_volume,
        "session_dir": arguments.session_dir,
        "vendor_bundle_dir": arguments.vendor_bundle_dir,
        "private_config_dir": arguments.private_config_dir,
        "expected_wpa_config_path": getattr(
            arguments, "expected_wpa_config", None
        ),
        "image_path": arguments.image,
        "provenance_path": arguments.image_provenance,
        "rootfs_path": arguments.rootfs,
        "output_dir": arguments.output_dir,
        "work_dir": arguments.work_dir,
        "station_ipv4": getattr(arguments, "station_ipv4", None),
    }
    camera = None
    if arguments.observe_camera:
        if arguments.session_dir is None:
            raise UserInstallerError("camera observation requires --session-dir")
        if arguments.mode == "runtime-candidate":
            host_gate = run_workflow_preflight(**inputs, camera_observation=None)
            if host_gate["ok"] is not True:
                return _document(
                    "workflow-preflight",
                    ok=False,
                    phase="workflow-stopped",
                    result=host_gate,
                )
        camera = observe_camera_state(
            session_dir=arguments.session_dir,
            recovery_host=arguments.recovery_host,
            station_ipv4=getattr(arguments, "station_ipv4", None),
        )
    result = run_workflow_preflight(**inputs, camera_observation=camera)
    return _document(
        "workflow-preflight",
        ok=result["ok"] is True,
        phase="workflow-ready" if result["ok"] is True else "workflow-stopped",
        next_command=(
            result.get("next_command")
            if isinstance(result.get("next_command"), str)
            else None
        ),
        result=result,
    )
