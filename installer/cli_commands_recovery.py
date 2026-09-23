"""Recovery, collector, and camera-state CLI command implementations."""

from __future__ import annotations


def _build_read_only_collector(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    build_collector_root = getattr(facade, 'build_collector_root')
    result = build_collector_root(
        mmc_module=arguments.mmc_module.read_bytes(),
        output=arguments.output,
        clang=arguments.clang,
        lld=arguments.lld,
        mksquashfs=arguments.mksquashfs,
        unsquashfs=arguments.unsquashfs,
        capture_mode=arguments.capture_mode,
    )
    _emit(
        {
            key: value
            for key, value in {"ok": True, **result}.items()
            if key != "output_sha256"
        }
    )

def _validate_read_only_collector(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    validate_collector_output = getattr(facade, 'validate_collector_output')
    decision = validate_collector_output(
        collector_output_dir=arguments.collector_output_dir,
        recovery_dir=arguments.recovery_dir,
    )
    _emit(
        {
            "audio_process_archived": decision.audio_process_archived,
            "mode": "existing-verified-same-device-pair",
            "nor_writes": False,
            "ok": True,
            "recovery_images": decision.recovery_images,
            "secret_mtd5_exposed": False,
            "vendor_files": list(decision.vendor_files),
        }
    )

def _render_collector_kernel_fragment(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    render_collector_kernel_fragment = getattr(facade, 'render_collector_kernel_fragment')
    raw = render_collector_kernel_fragment()
    atomic_write(arguments.output, raw)
    _emit(
        {
            "nor_writes": False,
            "ok": True,
            "output": arguments.output.name,
            "size": len(raw),
        }
    )

def _validate_collector_kernel(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_collector_kernel = getattr(facade, 'validate_collector_kernel')
    decision = validate_collector_kernel(
        kernel=read_snapshot(arguments.kernel),
        linux_config=read_snapshot(arguments.linux_config),
    )
    _emit(
        {
            "bootargs_source": "volatile-uboot",
            "expanded_size": decision.image.expanded_size,
            "nor_writes": False,
            "ok": True,
            "payload_size": decision.image.payload_size,
        }
    )

def _validate_uartless_gate(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    validate_uartless_report = getattr(facade, 'validate_uartless_report')
    decision = validate_uartless_report(
        report_path=arguments.report,
        scope=arguments.scope,
    )
    _emit(
        {
            "checks": list(decision.checks),
            "evidence_items": len(decision.evidence_digests),
            "forbidden_dependencies": False,
            "ok": True,
            "scope": decision.scope,
        }
    )

def _render_recovery_ap_kernel_fragment(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    render_recovery_ap_kernel_fragment = getattr(facade, 'render_recovery_ap_kernel_fragment')
    raw = render_recovery_ap_kernel_fragment()
    atomic_write(arguments.output, raw)
    _emit(
        {
            "nor_writes": False,
            "ok": True,
            "output": arguments.output.name,
            "size": len(raw),
        }
    )

def _validate_recovery_ap_kernel(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_recovery_ap_kernel = getattr(facade, 'validate_recovery_ap_kernel')
    decision = validate_recovery_ap_kernel(
        kernel=read_snapshot(arguments.kernel),
        linux_config=read_snapshot(arguments.linux_config),
    )
    _emit(
        {
            "command_line": decision.command_line,
            "expanded_size": decision.image.expanded_size,
            "nor_writes": False,
            "ok": True,
            "payload_size": decision.image.payload_size,
        }
    )

def _build_recovery_ap_root(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    build_recovery_ap_root = getattr(facade, 'build_recovery_ap_root')
    read_snapshot = getattr(facade, 'read_snapshot')
    result = build_recovery_ap_root(
        target_archive=arguments.target_archive,
        wifi_module=read_snapshot(arguments.wifi_module),
        mmc_module=read_snapshot(arguments.mmc_module),
        dtrng_module=read_snapshot(arguments.dtrng_module),
        entropy_seed=read_snapshot(arguments.entropy_seed),
        output=arguments.output,
        session_media_dir=arguments.session_media_dir,
        clang=arguments.clang,
        lld=arguments.lld,
        mksquashfs=arguments.mksquashfs,
        unsquashfs=arguments.unsquashfs,
    )
    _emit({"ok": True, **result})

def _create_recovery_ap_session(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    create_recovery_ap_session = getattr(facade, 'create_recovery_ap_session')
    result = create_recovery_ap_session(
        output_dir=arguments.output_dir,
        ssh_keygen=arguments.ssh_keygen,
        dropbearkey=arguments.dropbearkey,
    )
    _emit(
        {
            "contains_secrets": True,
            "nor_writes": False,
            "ok": True,
            "output_directory": result.output_dir.name,
            "setup_ssid": result.setup_ssid,
        }
    )

def _materialize_recovery_ap_session(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    materialize_recovery_ap_session = getattr(facade, 'materialize_recovery_ap_session')
    result = materialize_recovery_ap_session(
        output_dir=arguments.output_dir,
        session_media_dir=arguments.session_media_dir,
        identity=arguments.identity,
        ssh_keygen=arguments.ssh_keygen,
        dropbearkey=arguments.dropbearkey,
        service_credential=arguments.service_credential,
    )
    _emit(
        {
            "contains_secrets": True,
            "nor_writes": False,
            "ok": True,
            "output_directory": result.output_dir.name,
            "setup_ssid": result.setup_ssid,
        }
    )

def _probe_recovery_ap(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    probe_recovery_ap = getattr(facade, 'probe_recovery_ap')
    resolve_recovery_ap_station = getattr(facade, 'resolve_recovery_ap_station')
    discovery: dict[str, object] = {}
    if arguments.discover_station:
        mdns_name, host = resolve_recovery_ap_station(arguments.session_dir)
        discovery = {
            "discovery": "session-pinned-mdns",
            "station_mdns_name": mdns_name,
        }
    else:
        host = arguments.host or "192.168.88.1"
    _emit(
        {
            "ok": True,
            **discovery,
            **probe_recovery_ap(
                session_dir=arguments.session_dir,
                host=host,
                expected_state=arguments.expected_state,
            ),
        }
    )

def _diagnose_thingino_failure(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    diagnose_thingino_failure = getattr(facade, 'diagnose_thingino_failure')
    _emit(
        {
            "ok": True,
            **diagnose_thingino_failure(
                session_dir=arguments.session_dir,
                host=arguments.host,
            ),
        }
    )

def _provision_recovery_ap(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    provision_recovery_ap = getattr(facade, 'provision_recovery_ap')
    read_private_input = getattr(facade, 'read_private_input')
    ssid, passphrase = read_private_input(secrets_fd=arguments.secrets_fd)
    _emit(
        {
            "ok": True,
            **provision_recovery_ap(
                session_dir=arguments.session_dir,
                host=arguments.host,
                ssid=ssid,
                passphrase=passphrase,
            ),
        }
    )

def _install_recovery_ap(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    install_recovery_ap = getattr(facade, 'install_recovery_ap')
    _emit(
        {
            "ok": True,
            **install_recovery_ap(
                session_dir=arguments.session_dir,
                host=arguments.host,
                image_path=arguments.image,
            ),
        }
    )

def _extract_camera_vendor_bundle(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    extract_camera_vendor_bundle = getattr(facade, 'extract_camera_vendor_bundle')
    _emit(
        {
            "ok": True,
            **extract_camera_vendor_bundle(
                session_dir=arguments.session_dir,
                host=arguments.host,
                output_dir=arguments.output_dir,
            ),
        }
    )

def _build_personal_mtd3(facade: object, arguments: argparse.Namespace) -> None:
    Mtd3ImageError = getattr(facade, 'Mtd3ImageError')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    build_personal_mtd3_image = getattr(facade, 'build_personal_mtd3_image')
    read_snapshot = getattr(facade, 'read_snapshot')
    if arguments.output.exists() or arguments.output.is_symlink():
        raise Mtd3ImageError("refusing to overwrite a personal mtd3 image")
    image = build_personal_mtd3_image(read_snapshot(arguments.system_rootfs))
    atomic_write(arguments.output, image.raw)
    _emit(
        {
            "image_sha256": image.image_sha256,
            "ok": True,
            "output": arguments.output.name,
            "payload_sha256": image.payload_sha256,
            "size": len(image.raw),
            "written_mtd": [3],
        }
    )

def _install_personal_mtd3(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    install_personal_mtd3 = getattr(facade, 'install_personal_mtd3')
    _emit(
        {
            "ok": True,
            **install_personal_mtd3(
                session_dir=arguments.session_dir,
                host=arguments.host,
                image_path=arguments.image,
                vendor_bundle_dir=arguments.vendor_bundle_dir,
                provenance_path=arguments.image_provenance,
            ),
        }
    )

def _reconcile_camera_state(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    reconcile_recovery_ap_state = getattr(facade, 'reconcile_recovery_ap_state')
    _emit(
        {
            "ok": True,
            **reconcile_recovery_ap_state(
                session_dir=arguments.session_dir,
                host=arguments.host,
                image_path=arguments.image,
                vendor_bundle_dir=arguments.vendor_bundle_dir,
                provenance_path=arguments.image_provenance,
            ),
        }
    )

def _inspect_recovery_state(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    inspect_recovery_ap_nor = getattr(facade, 'inspect_recovery_ap_nor')
    state = inspect_recovery_ap_nor(
        session_dir=arguments.session_dir,
        host=arguments.host,
    )
    _emit(
        {
            "layout": state.layout,
            "mtd1_kind": state.mtd1_kind,
            "mtd1_sha256": state.mtd1_sha256,
            "mtd2_kind": state.mtd2_kind,
            "mtd2_sha256": state.mtd2_sha256,
            "mtd3_kind": state.mtd3_kind,
            "mtd3_mounted": state.mtd3_mounted,
            "mtd3_payload_sha256": state.mtd3_payload_sha256,
            "mtd3_sha256": state.mtd3_sha256,
            "nor_writes": False,
            "ok": True,
        }
    )

def _activate_personal_mtd3(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    activate_personal_mtd3 = getattr(facade, 'activate_personal_mtd3')
    _emit(
        {
            "ok": True,
            **activate_personal_mtd3(
                session_dir=arguments.session_dir,
                host=arguments.host,
                image_sha256=arguments.image_sha256,
            ),
        }
    )

def _prove_thingino_health(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    prove_thingino_health = getattr(facade, 'prove_thingino_health')
    result = prove_thingino_health(
        session_dir=arguments.session_dir,
        expected_mtd3_sha256=arguments.expected_mtd3_sha256,
    )
    _emit({"discovery": "session-pinned-mdns", "ok": True, **result})

def _complete_personal_install(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    complete_personal_install = getattr(facade, 'complete_personal_install')
    read_private_input = getattr(facade, 'read_private_input')
    read_snapshot = getattr(facade, 'read_snapshot')
    ssid: str | None = None
    passphrase: str | None = None
    if not (arguments.work_dir / "install-config").exists():
        ssid, passphrase = read_private_input(secrets_fd=arguments.secrets_fd)
    def join_ap() -> None:
        join_recovery_ap(
            session_dir=arguments.session_dir,
            helper=arguments.macos_wifi_helper,
        )

    result = complete_personal_install(
        session_dir=arguments.session_dir,
        base_rootfs=read_snapshot(arguments.base_rootfs),
        media_closure_dir=arguments.media_closure_dir,
        work_dir=arguments.work_dir,
        ssid=ssid,
        passphrase=passphrase,
        expected_wpa_config_path=arguments.expected_wpa_config,
        mksquashfs=arguments.mksquashfs,
        unsquashfs=arguments.unsquashfs,
        ap_host=arguments.host,
        station_timeout=arguments.station_timeout,
        join_ap=join_ap if arguments.macos_wifi_helper is not None else None,
    )
    _emit({"ok": True, **result})
