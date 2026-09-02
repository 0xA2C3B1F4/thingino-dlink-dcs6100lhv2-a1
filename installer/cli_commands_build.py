"""Final bundle, private configuration, and build CLI command implementations."""

from __future__ import annotations


def _build_final_bundle(facade: object, arguments: argparse.Namespace) -> None:
    BundleError = getattr(facade, 'BundleError')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    build_final_bundle = getattr(facade, 'build_final_bundle')
    hashlib = getattr(facade, 'hashlib')
    read_snapshot = getattr(facade, 'read_snapshot')
    raw = build_final_bundle(
        kernel=read_snapshot(arguments.kernel),
        bootstrap_rootfs=read_snapshot(arguments.bootstrap_rootfs),
        system_rootfs=read_snapshot(arguments.system_rootfs),
        data_jffs2=read_snapshot(arguments.data_jffs2),
        linux_config=read_snapshot(arguments.linux_config),
        signing_key=arguments.signing_key,
    )
    destination = arguments.output_dir / "thingino-final.tgb"
    if destination.exists():
        raise BundleError("refusing to overwrite an existing final bundle")
    atomic_write(destination, raw)
    _emit(
        {
            "filename": destination.name,
            "ok": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    )

def _verify_final_bundle(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    hashlib = getattr(facade, 'hashlib')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_final_bundle = getattr(facade, 'validate_final_bundle')
    bundle = validate_final_bundle(
        read_snapshot(arguments.bundle), public_key=arguments.public_key
    )
    _emit(
        {
            "ok": True,
            "preserved_mtd": bundle.manifest["preserved_mtd"],
            "sha256": hashlib.sha256(bundle.raw).hexdigest(),
            "size": len(bundle.raw),
            "write_order": bundle.manifest["write_order"],
        }
    )

def _render_final_kernel_fragment(facade: object, arguments: argparse.Namespace) -> None:
    BundleError = getattr(facade, 'BundleError')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    hashlib = getattr(facade, 'hashlib')
    read_snapshot = getattr(facade, 'read_snapshot')
    render_final_kernel_fragment = getattr(facade, 'render_final_kernel_fragment')
    validate_squashfs = getattr(facade, 'validate_squashfs')
    system_rootfs = read_snapshot(arguments.system_rootfs)
    validate_squashfs(system_rootfs)
    fragment = render_final_kernel_fragment(len(system_rootfs))
    if arguments.output.exists():
        raise BundleError("refusing to overwrite an existing kernel fragment")
    atomic_write(arguments.output, fragment)
    _emit(
        {
            "filename": arguments.output.name,
            "ok": True,
            "sha256": hashlib.sha256(fragment).hexdigest(),
            "system_rootfs_size": len(system_rootfs),
        }
    )

def _render_installer_kernel_fragment(facade: object, arguments: argparse.Namespace) -> None:
    Stage1BuildError = getattr(facade, 'Stage1BuildError')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    hashlib = getattr(facade, 'hashlib')
    read_snapshot = getattr(facade, 'read_snapshot')
    render_installer_kernel_fragment = getattr(facade, 'render_installer_kernel_fragment')
    validate_squashfs = getattr(facade, 'validate_squashfs')
    system_rootfs = read_snapshot(arguments.system_rootfs)
    validate_squashfs(system_rootfs)
    fragment = render_installer_kernel_fragment(len(system_rootfs))
    if arguments.output.exists():
        raise Stage1BuildError("refusing to overwrite an existing kernel fragment")
    atomic_write(arguments.output, fragment)
    _emit(
        {
            "filename": arguments.output.name,
            "ok": True,
            "sha256": hashlib.sha256(fragment).hexdigest(),
            "system_rootfs_size": len(system_rootfs),
        }
    )

def _create_private_bootstrap_config(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    generate_private_config = getattr(facade, 'generate_private_config')
    import_private_wpa_config = getattr(facade, 'import_private_wpa_config')
    read_authorized_key = getattr(facade, 'read_authorized_key')
    read_private_input = getattr(facade, 'read_private_input')
    authorized_key = read_authorized_key(arguments.authorized_key)
    if arguments.wpa_config is not None:
        result = import_private_wpa_config(
            output_dir=arguments.output_dir,
            source=arguments.wpa_config,
            authorized_key=authorized_key,
        )
    else:
        ssid, passphrase = read_private_input(secrets_fd=arguments.secrets_fd)
        result = generate_private_config(
            output_dir=arguments.output_dir,
            ssid=ssid,
            passphrase=passphrase,
            authorized_key=authorized_key,
        )
    _emit(
        {
            "files": list(result.files),
            "ok": True,
            "output_directory": result.output_dir.name,
        }
    )

def _seal_private_install_config(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    seal_private_config_for_session = getattr(facade, 'seal_private_config_for_session')
    result = seal_private_config_for_session(
        output_dir=arguments.private_config_dir,
        session_dir=arguments.session_dir,
    )
    _emit(
        {
            "bindings": "verified",
            "files": list(result.files),
            "ok": True,
            "rotation": "none",
            "schema_version": 2,
        }
    )

def _prepare_final_root(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    prepare_from_private_directory = getattr(facade, 'prepare_from_private_directory')
    manifest = prepare_from_private_directory(
        base_rootfs_path=arguments.base_rootfs,
        private_config_dir=arguments.private_config_dir,
        expected_wpa_config_path=arguments.expected_wpa_config,
        vendor_bundle_dir=arguments.vendor_bundle_dir,
        media_closure_dir=arguments.media_closure_dir,
        session_dir=arguments.session_dir,
        output_dir=arguments.output_dir,
        mksquashfs=arguments.mksquashfs,
        unsquashfs=arguments.unsquashfs,
        output_size=arguments.output_size,
    )
    _emit(
        {
            "filename": manifest["system"]["filename"],
            "ok": True,
            "output_directory": arguments.output_dir.name,
            "sha256": manifest["system"]["sha256"],
            "size": manifest["system"]["size"],
        }
    )

def _inspect_vendor_bundle(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    load_vendor_bundle = getattr(facade, 'load_vendor_bundle')
    bundle = load_vendor_bundle(arguments.vendor_bundle_dir)
    _emit(
        {
            "bundle_sha256": bundle.bundle_sha256,
            "files": [
                {
                    "destination": artifact.destination,
                    "name": artifact.name,
                    "sha256": artifact.sha256,
                    "size": len(artifact.raw),
                }
                for artifact in bundle.artifacts
            ],
            "firmware_version": bundle.firmware_version,
            "manifest_sha256": bundle.manifest_sha256,
            "ok": True,
        }
    )

def _extract_vendor_bundle(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    extract_vendor_bundle = getattr(facade, 'extract_vendor_bundle')
    bundle = extract_vendor_bundle(
        mtd3_mount=arguments.mtd3_mount,
        mountinfo_path=arguments.mountinfo,
        output_dir=arguments.output_dir,
    )
    _emit(
        {
            "bundle_sha256": bundle.bundle_sha256,
            "files": [artifact.name for artifact in bundle.artifacts],
            "nor_writes": False,
            "ok": True,
            "output_directory": arguments.output_dir.name,
        }
    )

def _validate_runtime_gate(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    load_vendor_bundle = getattr(facade, 'load_vendor_bundle')
    validate_runtime_report = getattr(facade, 'validate_runtime_report')
    bundle = load_vendor_bundle(arguments.vendor_bundle_dir)
    decision = validate_runtime_report(arguments.report, bundle)
    _emit(
        {
            "audio_process_required": decision.audio_process_required,
            "installer_lock_gate": "passed",
            "nor_writes": False,
            "ok": True,
            "vendor_bundle_sha256": decision.vendor_bundle_sha256,
        }
    )

def _validate_recovery_boundary(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    validate_existing_recovery_boundary = getattr(facade, 'validate_existing_recovery_boundary')
    decision = validate_existing_recovery_boundary(
        recovery_dir=arguments.recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
    )
    _emit(
        {
            "mode": decision.mode,
            "nor_writes": False,
            "ok": True,
            "preserved_mtd": list(decision.preserved_mtd),
            "recovery_images": decision.recovery_images,
            "secret_mtd5_exposed": False,
        }
    )

def _prepare_vendor_build_site(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    prepare_vendor_build_site = getattr(facade, 'prepare_vendor_build_site')
    site = prepare_vendor_build_site(
        vendor_bundle_dir=arguments.vendor_bundle_dir,
        output_dir=arguments.output_dir,
    )
    _emit(
        {
            "archive_only_files_excluded": True,
            "files": list(site.files),
            "ok": True,
            "output_directory": arguments.output_dir.name,
            "public_ingenic_lib_archive": False,
            "source_vendor_bundle_sha256": site.source_bundle_sha256,
        }
    )

def _build_ramdisk_wrapper(facade: object, arguments: argparse.Namespace) -> None:
    RamBootError = getattr(facade, 'RamBootError')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    build_ramdisk_uimage = getattr(facade, 'build_ramdisk_uimage')
    parse_ramdisk_uimage = getattr(facade, 'parse_ramdisk_uimage')
    read_snapshot = getattr(facade, 'read_snapshot')
    if arguments.output.exists():
        raise RamBootError("refusing to overwrite an existing RAM-disk wrapper")
    rootfs = read_snapshot(arguments.rootfs)
    wrapper = build_ramdisk_uimage(rootfs)
    if parse_ramdisk_uimage(wrapper) != rootfs:
        raise RamBootError("RAM-disk wrapper read-back mismatch")
    atomic_write(arguments.output, wrapper)
    _emit(
        {
            "ok": True,
            "output": arguments.output.name,
            "payload_size": len(rootfs),
            "size": len(wrapper),
        }
    )
