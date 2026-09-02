"""Install-set and removable-media CLI command implementations."""

from __future__ import annotations


def _inspect_sd(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    parse_package = getattr(facade, 'parse_package')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_stock_source = getattr(facade, 'validate_stock_source')
    package = parse_package(read_snapshot(arguments.package))
    if arguments.policy == "bootstrap":
        validate_bootstrap(package)
    else:
        validate_stock_source(package)
    _emit(
        {
            "ok": True,
            "policy": arguments.policy,
            "records": [
                {
                    "erase_span": record.erase_span,
                    "flash_offset": record.flash_offset,
                    "payload_length": record.payload_length,
                }
                for record in package.records
            ],
            "sha256": package.sha256,
            "size": len(package.raw),
        }
    )

def _build_bootstrap(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    PackageError = getattr(facade, 'PackageError')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    generate_bootstrap = getattr(facade, 'generate_bootstrap')
    package_manifest = getattr(facade, 'package_manifest')
    parse_package = getattr(facade, 'parse_package')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_stage1_images = getattr(facade, 'validate_stage1_images')
    kernel = read_snapshot(arguments.kernel)
    rootfs = read_snapshot(arguments.rootfs)
    kernel_info, rootfs_used = validate_stage1_images(kernel, rootfs)
    raw = generate_bootstrap(kernel, rootfs)
    package = parse_package(raw, require_project_header=True)
    validate_bootstrap(package)
    output = arguments.output_dir
    package_path = output / DEFAULT_SD_NAME
    manifest_path = output / "bootstrap.manifest.json"
    if package_path.exists() or manifest_path.exists():
        raise PackageError("refusing to overwrite an existing bootstrap result")
    atomic_write(package_path, raw)
    try:
        atomic_write(
            manifest_path,
            package_manifest(package, purpose="stage1-bootstrap"),
        )
    except BaseException:
        package_path.unlink(missing_ok=True)
        raise
    _emit(
        {
            "kernel_entry": kernel_info.entry_point,
            "kernel_expanded_size": kernel_info.expanded_size,
            "ok": True,
            "output_filename": package_path.name,
            "package_sha256": package.sha256,
            "package_size": len(raw),
            "rootfs_bytes_used": rootfs_used,
            "writes_mtd0": False,
        }
    )

def _build_recovery_ap_bootstrap(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    PackageError = getattr(facade, 'PackageError')
    TARGET = getattr(facade, 'TARGET')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    generate_bootstrap = getattr(facade, 'generate_bootstrap')
    package_manifest = getattr(facade, 'package_manifest')
    parse_package = getattr(facade, 'parse_package')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_bootstrap = getattr(facade, 'validate_bootstrap')
    validate_recovery_ap_kernel = getattr(facade, 'validate_recovery_ap_kernel')
    validate_squashfs = getattr(facade, 'validate_squashfs')
    kernel = read_snapshot(arguments.kernel)
    rootfs = read_snapshot(arguments.rootfs)
    kernel_info = validate_recovery_ap_kernel(
        kernel=kernel,
        linux_config=read_snapshot(arguments.linux_config),
    ).image
    rootfs_used = validate_squashfs(
        rootfs,
        partition_limit=TARGET.partition(2).size,
    )
    raw = generate_bootstrap(kernel, rootfs)
    package = parse_package(raw, require_project_header=True)
    validate_bootstrap(package)
    output = arguments.output_dir
    package_path = output / DEFAULT_SD_NAME
    manifest_path = output / "bootstrap.manifest.json"
    if package_path.exists() or manifest_path.exists():
        raise PackageError("refusing to overwrite an existing bootstrap result")
    atomic_write(package_path, raw)
    try:
        atomic_write(
            manifest_path,
            package_manifest(package, purpose="recovery-ap-bootstrap"),
        )
    except BaseException:
        package_path.unlink(missing_ok=True)
        raise
    _emit(
        {
            "kernel_entry": kernel_info.entry_point,
            "kernel_expanded_size": kernel_info.expanded_size,
            "ok": True,
            "output_filename": package_path.name,
            "package_sha256": package.sha256,
            "package_size": len(raw),
            "rootfs_bytes_used": rootfs_used,
            "writes_mtd0": False,
            "written_mtd": [1, 2],
        }
    )

def _build_stage1_recovery(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    build_stage1_recovery = getattr(facade, 'build_stage1_recovery')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_stage1_images = getattr(facade, 'validate_stage1_images')
    write_recovery_result = getattr(facade, 'write_recovery_result')
    kernel = read_snapshot(arguments.kernel)
    rootfs = read_snapshot(arguments.rootfs)
    validate_stage1_images(kernel, rootfs)
    result = build_stage1_recovery(kernel, rootfs)
    package_path = arguments.output_dir / DEFAULT_SD_NAME
    manifest_path = arguments.output_dir / "stage1-recovery.manifest.json"
    write_recovery_result(
        result,
        package_path=package_path,
        manifest_path=manifest_path,
    )
    _emit(
        {
            "kind": result.kind,
            "ok": True,
            "output_filename": package_path.name,
            "stock_restore": False,
            "writes_mtd0": False,
        }
    )

def _inspect_official(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    validate_official_input = getattr(facade, 'validate_official_input')
    source = validate_official_input(arguments.input)
    _emit(
        {
            "can_restore_stock_kernel_rootfs": source.can_restore_stock_kernel_rootfs,
            "filename": source.filename,
            "format": source.format,
            "ok": True,
            "sha256": source.sha256,
            "size": source.size,
            "version": source.version,
        }
    )

def _stage_media(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    read_snapshot = getattr(facade, 'read_snapshot')
    stage_verified_package = getattr(facade, 'stage_verified_package')
    package = read_snapshot(arguments.package)
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digest = stage_verified_package(
        package,
        root=arguments.mount_root,
        output_name=DEFAULT_SD_NAME,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "output_filename": DEFAULT_SD_NAME,
            "package_sha256": digest,
            "physical_device": preflight.physical_device,
        }
    )

def _deactivate_staged_package(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    deactivate_verified_package = getattr(facade, 'deactivate_verified_package')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    read_snapshot = getattr(facade, 'read_snapshot')
    package = read_snapshot(arguments.package)
    existing_passive_bytes = None
    if getattr(arguments, "existing_passive_package", None) is not None:
        existing_passive_bytes = read_snapshot(arguments.existing_passive_package)
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digest = deactivate_verified_package(
        package,
        root=arguments.mount_root,
        active_name=DEFAULT_SD_NAME,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
        existing_passive_bytes=existing_passive_bytes,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "output_filename": "RECOVERY.OFF",
            "package_sha256": digest,
            "physical_device": preflight.physical_device,
        }
    )

def _stage_install_set(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    read_snapshot = getattr(facade, 'read_snapshot')
    stage_verified_install_set = getattr(facade, 'stage_verified_install_set')
    bootstrap_path = arguments.install_set_dir / DEFAULT_SD_NAME
    stage2_path = arguments.install_set_dir / "THINGINO2.BIN"
    manifest_path = arguments.install_set_dir / "install-set.manifest.json"
    bootstrap = read_snapshot(bootstrap_path)
    stage2 = read_snapshot(stage2_path)
    manifest = read_snapshot(manifest_path)
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digests = stage_verified_install_set(
        bootstrap_bytes=bootstrap,
        stage2_bytes=stage2,
        manifest_bytes=manifest,
        root=arguments.mount_root,
        bootstrap_name=DEFAULT_SD_NAME,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "files": digests,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "physical_device": preflight.physical_device,
        }
    )

def _inspect_install_set(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    MediaError = getattr(facade, 'MediaError')
    _emit = getattr(facade, '_emit')
    hashlib = getattr(facade, 'hashlib')
    json = getattr(facade, 'json')
    read_snapshot = getattr(facade, 'read_snapshot')
    validate_install_set = getattr(facade, 'validate_install_set')
    bootstrap_path = arguments.install_set_dir / DEFAULT_SD_NAME
    stage2_path = arguments.install_set_dir / "THINGINO2.BIN"
    rootfs_path = arguments.install_set_dir / "stage1-bootstrap.squashfs"
    manifest_path = arguments.install_set_dir / "install-set.manifest.json"
    paths = (bootstrap_path, stage2_path, rootfs_path, manifest_path)
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise MediaError("install set is missing a regular required artifact")
    bootstrap = read_snapshot(bootstrap_path)
    stage2 = read_snapshot(stage2_path)
    rootfs = read_snapshot(rootfs_path)
    manifest_raw = read_snapshot(manifest_path)
    payload = validate_install_set(
        bootstrap_bytes=bootstrap,
        stage2_bytes=stage2,
        manifest_bytes=manifest_raw,
        bootstrap_name=DEFAULT_SD_NAME,
    )
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaError("install-set manifest is invalid JSON") from exc
    expected_artifacts = {
        DEFAULT_SD_NAME: bootstrap,
        "THINGINO2.BIN": stage2,
        "stage1-bootstrap.squashfs": rootfs,
    }
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(expected_artifacts):
        raise MediaError("install-set manifest artifact allowlist changed")
    for name, raw in expected_artifacts.items():
        if artifacts.get(name) != {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }:
            raise MediaError(f"install-set manifest does not bind {name}")
    _emit(
        {
            "artifact_sizes": {
                name: len(raw) for name, raw in expected_artifacts.items()
            },
            "data_mode": payload.data_mode,
            "data_span": payload.data_flash_span,
            "layout": "dcs6100lhv2-a1-mtd3-split-v1",
            "ok": True,
            "schema_version": manifest.get("schema_version"),
            "system_span": payload.system_flash_span,
        }
    )

def _activate_staged_install_set(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    activate_staged_install_set = getattr(facade, 'activate_staged_install_set')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    read_snapshot = getattr(facade, 'read_snapshot')
    bootstrap_path = arguments.install_set_dir / DEFAULT_SD_NAME
    stage2_path = arguments.install_set_dir / "THINGINO2.BIN"
    manifest_path = arguments.install_set_dir / "install-set.manifest.json"
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digests = activate_staged_install_set(
        bootstrap_bytes=read_snapshot(bootstrap_path),
        stage2_bytes=read_snapshot(stage2_path),
        manifest_bytes=read_snapshot(manifest_path),
        root=arguments.mount_root,
        bootstrap_name=DEFAULT_SD_NAME,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "files": digests,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "physical_device": preflight.physical_device,
        }
    )

def _deactivate_staged_install_set(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    deactivate_staged_install_set = getattr(facade, 'deactivate_staged_install_set')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    read_snapshot = getattr(facade, 'read_snapshot')
    bootstrap_path = arguments.install_set_dir / DEFAULT_SD_NAME
    stage2_path = arguments.install_set_dir / "THINGINO2.BIN"
    manifest_path = arguments.install_set_dir / "install-set.manifest.json"
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    existing_passive_bytes = None
    if arguments.existing_passive_install_set_dir is not None:
        existing_passive_bytes = read_snapshot(
            arguments.existing_passive_install_set_dir / DEFAULT_SD_NAME
        )
    digests = deactivate_staged_install_set(
        bootstrap_bytes=read_snapshot(bootstrap_path),
        stage2_bytes=read_snapshot(stage2_path),
        manifest_bytes=read_snapshot(manifest_path),
        root=arguments.mount_root,
        bootstrap_name=DEFAULT_SD_NAME,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
        existing_passive_bytes=existing_passive_bytes,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "files": digests,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "physical_device": preflight.physical_device,
        }
    )

def _archive_existing_stock_backup(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    archive_existing_stock_backup = getattr(facade, 'archive_existing_stock_backup')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digests = archive_existing_stock_backup(
        root=arguments.mount_root,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "files": digests,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "physical_device": preflight.physical_device,
        }
    )

def _evacuate_existing_stock_backups(facade: object, arguments: argparse.Namespace) -> None:
    _emit = getattr(facade, '_emit')
    evacuate_existing_stock_backups = getattr(facade, 'evacuate_existing_stock_backups')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digests = evacuate_existing_stock_backups(
        root=arguments.mount_root,
        destination_dir=arguments.output_dir,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "files": digests,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "output_directory": arguments.output_dir.name,
            "physical_device": preflight.physical_device,
        }
    )

def _replace_passive_bootstrap(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    load_media_preflight = getattr(facade, 'load_media_preflight')
    read_snapshot = getattr(facade, 'read_snapshot')
    replace_passive_bootstrap = getattr(facade, 'replace_passive_bootstrap')
    old_bootstrap_path = arguments.old_install_set_dir / DEFAULT_SD_NAME
    bootstrap_path = arguments.install_set_dir / DEFAULT_SD_NAME
    stage2_path = arguments.install_set_dir / "THINGINO2.BIN"
    manifest_path = arguments.install_set_dir / "install-set.manifest.json"
    preflight = load_media_preflight(
        arguments.preflight,
        expected_root=arguments.mount_root,
    )
    digests = replace_passive_bootstrap(
        old_bootstrap_bytes=read_snapshot(old_bootstrap_path),
        old_stage2_bytes=read_snapshot(
            arguments.old_install_set_dir / "THINGINO2.BIN"
        ),
        legacy_migration_profile_bytes=(
            read_snapshot(arguments.legacy_migration_profile)
            if arguments.legacy_migration_profile is not None
            else None
        ),
        new_bootstrap_bytes=read_snapshot(bootstrap_path),
        stage2_bytes=read_snapshot(stage2_path),
        manifest_bytes=read_snapshot(manifest_path),
        root=arguments.mount_root,
        bootstrap_name=DEFAULT_SD_NAME,
        preflight=preflight,
        confirmed_physical_device=arguments.confirm_physical_device,
    )
    _emit(
        {
            "capacity_bytes": preflight.capacity_bytes,
            "files": digests,
            "filesystem": preflight.filesystem,
            "model": preflight.model,
            "ok": True,
            "physical_device": preflight.physical_device,
        }
    )

def _create_legacy_migration_profile(facade: object, arguments: argparse.Namespace) -> None:
    DEFAULT_SD_NAME = getattr(facade, 'DEFAULT_SD_NAME')
    _emit = getattr(facade, '_emit')
    atomic_write = getattr(facade, 'atomic_write')
    build_legacy_migration_profile = getattr(facade, 'build_legacy_migration_profile')
    read_snapshot = getattr(facade, 'read_snapshot')
    bootstrap_path = arguments.old_install_set_dir / DEFAULT_SD_NAME
    stage2_path = arguments.old_install_set_dir / "THINGINO2.BIN"
    raw = build_legacy_migration_profile(
        bootstrap_name=DEFAULT_SD_NAME,
        old_bootstrap_bytes=read_snapshot(bootstrap_path),
        old_stage2_bytes=read_snapshot(stage2_path),
    )
    atomic_write(arguments.output, raw)
    _emit(
        {
            "filename": arguments.output.name,
            "ok": True,
            "private": True,
            "size": len(raw),
        }
    )
