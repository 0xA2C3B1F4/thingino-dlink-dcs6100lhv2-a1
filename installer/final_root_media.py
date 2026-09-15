"""Raptor-only vendor support checks for final-root composition."""

from __future__ import annotations

from pathlib import Path

def _os_release_values(facade: object, path: Path) -> dict[str, str]:
    Path = getattr(facade, 'Path')
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values

def _require_proven_media_runtime(
    facade: object,
    root: Path,
    *,
    require_vendor: bool = True,
    support_only: bool = False,
) -> None:
    """Require the source-built camera support layer used by Raptor."""

    FinalRootError = getattr(facade, "FinalRootError")
    EXPECTED_IMAGE_ID = getattr(facade, "EXPECTED_IMAGE_ID")
    release = _os_release_values(facade, root / "etc/os-release")
    if release.get("LIBC") != "glibc" or release.get("TOOLCHAIN") != "glibc":
        raise FinalRootError("base root is not the live-proven glibc runtime")
    if release.get("IMAGE_ID") != EXPECTED_IMAGE_ID:
        raise FinalRootError("base root targets the wrong Thingino image")
    if not support_only:
        raise FinalRootError(
            "legacy personalized media runtime is retired; compose full Raptor"
        )
    global_library = getattr(facade, "_global_glibc_library")
    for relative in ("lib/ld.so.1", "lib/libc.so.6"):
        global_library(root, relative.removeprefix("lib/"))
    for name in ("libalog.so", "libsysutils.so", "libaudioProcess.so"):
        library = root / "usr/lib" / name
        if (
            library.is_symlink()
            or not library.is_file()
            or not library.read_bytes().startswith(b"\x7fELF")
        ):
            raise FinalRootError(f"base root lacks source-built camera support: {name}")
    if require_vendor:
        libimp = root / "usr/lib/libimp.so"
        if libimp.is_symlink() or not libimp.is_file():
            raise FinalRootError("final root lacks camera-local IMP support")
        raw = libimp.read_bytes()
        if not raw.startswith(b"\x7fELF") or b"1.1.4\0" not in raw:
            raise FinalRootError("final root does not contain camera-local IMP 1.1.4")


def _record_source_media(
    facade: object,
    root: Path,
    bundle: object,
    *,
    support_only: bool = False,
) -> dict[str, object]:
    """Record source-built support and read-only stock inputs."""

    FinalRootError = getattr(facade, "FinalRootError")
    KERNEL_RELEASE = getattr(facade, "KERNEL_RELEASE")
    hashlib_module = getattr(facade, "hashlib")
    if not support_only:
        raise FinalRootError(
            "legacy personalized media provenance is retired; compose full Raptor"
        )
    init_names = ("F01datetime", "S06ircut", "S11modules")
    source_built_init: list[dict[str, object]] = []
    for name in init_names:
        path = root / "etc/init.d" / name
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(f"base root lacks source-built support init: {name}")
        raw = path.read_bytes()
        source_built_init.append(
            {
                "destination": f"/etc/init.d/{name}",
                "origin": "pinned-source-build",
                "path": f"init/{name}",
                "sha256": hashlib_module.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )

    vendor_names = {
        "libimp.so",
        "libalog.so",
        "libsysutils.so",
        "libaudioProcess.so",
        "tx-isp-t31.ko",
        "sensor_os02g10_t31.ko",
        "os02g10-t31.bin",
    }
    artifacts = {artifact.name: artifact for artifact in bundle.artifacts}
    if set(artifacts) != vendor_names:
        raise FinalRootError("camera-local support runtime closure is incomplete")

    files: list[dict[str, object]] = []
    installed_vendor_names = {
        "libimp.so",
        "tx-isp-t31.ko",
        "sensor_os02g10_t31.ko",
        "os02g10-t31.bin",
    }
    for name in sorted(installed_vendor_names):
        artifact = artifacts[name]
        if artifact.destination is None:
            raise FinalRootError("camera-local support runtime lacks a destination")
        destination = root / artifact.destination
        if destination.is_symlink() or not destination.is_file():
            raise FinalRootError(f"camera-local support runtime is missing: {name}")
        raw = destination.read_bytes()
        if hashlib_module.sha256(raw).hexdigest() != artifact.sha256:
            raise FinalRootError(f"camera-local support runtime changed: {name}")
        files.append(
            {
                "destination": "/" + artifact.destination,
                "origin": "camera-read-only-stock-mtd3",
                "path": f"vendor/{name}",
                "sha256": artifact.sha256,
                "size": len(raw),
            }
        )

    source_support_names = {
        "libalog.so",
        "libsysutils.so",
        "libaudioProcess.so",
    }
    source_support: list[dict[str, object]] = []
    for name in sorted(source_support_names):
        artifact = artifacts[name]
        if artifact.destination is None:
            raise FinalRootError("camera-local support runtime lacks a destination")
        destination = root / artifact.destination
        if destination.is_symlink() or not destination.is_file():
            raise FinalRootError(
                f"source-built support library is missing: {artifact.destination}"
            )
        raw = destination.read_bytes()
        entry = {
            "destination": "/" + artifact.destination,
            "origin": "pinned-public-source-build",
            "path": f"source/{name}",
            "sha256": hashlib_module.sha256(raw).hexdigest(),
            "size": len(raw),
        }
        files.append(entry)
        source_support.append(entry)

    acquired_inputs = [
        {
            "destination": "/" + artifact.destination,
            "name": artifact.name,
            "sha256": artifact.sha256,
            "size": len(artifact.raw),
        }
        for artifact in sorted(bundle.artifacts, key=lambda item: item.name)
        if artifact.destination is not None
    ]
    return {
        "schema_version": 4,
        "target": "DCS-6100LHV2-A1",
        "runtime": "source-built-camera-support-v1",
        "source_built_init": source_built_init,
        "startup_order": [name for name in init_names if name.startswith("S")],
        "vendor_bundle_sha256": bundle.bundle_sha256,
        "vendor_manifest_sha256": bundle.manifest_sha256,
        "kernel_release": KERNEL_RELEASE,
        "acquired_stock_inputs": acquired_inputs,
        "source_built_support": source_support,
        "files": files,
    }


def _snapshot_native_media(facade: object, root: Path) -> dict[str, str]:
    FinalRootError = getattr(facade, 'FinalRootError')
    NATIVE_MEDIA_PATHS = getattr(facade, 'NATIVE_MEDIA_PATHS')
    Path = getattr(facade, 'Path')
    hashlib = getattr(facade, 'hashlib')
    snapshot: dict[str, str] = {}
    for relative in NATIVE_MEDIA_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(f"base root lacks Thingino native media input: {relative}")
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot

def _install_vendor_bundle(facade: object, root: Path, bundle: VendorBundle) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    VendorBundle = getattr(facade, 'VendorBundle')
    hashlib = getattr(facade, 'hashlib')
    rootfs_artifacts = tuple(artifact for artifact in bundle.artifacts if artifact.rootfs)
    if {artifact.name for artifact in rootfs_artifacts} != {
        "libimp.so",
        "libalog.so",
        "libsysutils.so",
        "libaudioProcess.so",
        "tx-isp-t31.ko",
        "sensor_os02g10_t31.ko",
        "os02g10-t31.bin",
    }:
        raise FinalRootError("camera-local vendor support bundle is incomplete")
    source_support_names = {
        "libalog.so",
        "libsysutils.so",
        "libaudioProcess.so",
    }
    source_support: dict[str, str] = {}
    for artifact in rootfs_artifacts:
        if artifact.name not in source_support_names or artifact.destination is None:
            continue
        path = root / artifact.destination
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(
                f"base root lacks the source-built support library: {artifact.name}"
            )
        source_support[artifact.destination] = hashlib.sha256(path.read_bytes()).hexdigest()
    for artifact in rootfs_artifacts:
        if artifact.destination is None:
            raise FinalRootError("rootfs vendor artifact lacks a destination")
        if artifact.name in source_support_names:
            continue
        path = root / artifact.destination
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(
                f"base root lacks the vendor media destination: {artifact.destination}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(artifact.raw)
        path.chmod(0o644)
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
            raise FinalRootError(f"installed camera-local support changed: {artifact.name}")
    for destination, digest in source_support.items():
        if hashlib.sha256((root / destination).read_bytes()).hexdigest() != digest:
            raise FinalRootError("camera-local bundle replaced source-built support libraries")

def _global_glibc_library(facade: object, root: Path, name: str) -> Path:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    """Return the regular global SONAME target without replacing its aliases."""
    alias = root / "lib" / name
    if not alias.exists():
        raise FinalRootError(f"base root lacks global glibc runtime entry: {name}")
    try:
        target = alias.resolve(strict=True)
        target.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise FinalRootError(f"global glibc runtime entry escapes final root: {name}") from exc
    if target.is_symlink() or not target.is_file():
        raise FinalRootError(f"global glibc runtime target is not regular: {name}")
    return target

def _require_native_media_unchanged(facade: object, root: Path, expected: dict[str, str]) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    _snapshot_native_media = getattr(facade, '_snapshot_native_media')
    if _snapshot_native_media(root) != expected:
        raise FinalRootError("camera-local bundle replaced Thingino native media inputs")

def _move_required(facade: object, root: Path, source: str, destination: str) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    os = getattr(facade, 'os')
    source_path = root / source
    destination_path = root / destination
    if not source_path.is_file() or source_path.is_symlink():
        raise FinalRootError(f"base root lacks required init script: {source}")
    if destination_path.exists() or destination_path.is_symlink():
        raise FinalRootError(f"base root already contains reserved init path: {destination}")
    os.replace(source_path, destination_path)
    destination_path.chmod(0o755)

def _configure_media_first_init(
    facade: object, root: Path, *, support_only: bool = False
) -> None:
    FinalRootError = getattr(facade, "FinalRootError")
    TEMPLATE_ROOT = getattr(facade, "TEMPLATE_ROOT")
    move_required = getattr(facade, "_move_required")
    write_executable = getattr(facade, "_write_executable")
    if not support_only:
        raise FinalRootError(
            "legacy personalized media init is retired; compose full Raptor"
        )
    init_root = root / "etc/init.d"
    for name in ("F01datetime", "S06ircut", "S11modules", "S30mdev"):
        path = init_root / name
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(f"base root lacks required support init: {name}")
    for name in (
        "S10mdev",
        "S12prudynt",
        "S13dlink-media-ready",
        "S14volatile-config",
        "S15thingino-button",
        "S56prudynt",
        "dlink-media-start",
    ):
        retired = init_root / name
        if retired.is_symlink() or retired.is_file():
            retired.unlink()
        elif retired.exists():
            raise FinalRootError(f"retired media init path changed type: {name}")
    for relative in ("etc/modules.d/gpio-userkeys", "etc/thingino-button.conf"):
        retired = root / relative
        if retired.is_symlink() or retired.is_file():
            retired.unlink()
        elif retired.exists():
            raise FinalRootError(f"retired reset input path changed type: {relative}")
    move_required(root, "etc/init.d/S09mmc", "etc/init.d/S14mmc")
    move_required(root, "etc/init.d/S02ssl", "etc/init.d/S59ssl")
    for name in (
        "S04loopback",
        "S05dlink-media-preconditions",
        "S55installer-health",
    ):
        write_executable(init_root / name, (TEMPLATE_ROOT / name).read_bytes())
    write_executable(
        init_root / "dlink-media-acceptance",
        (TEMPLATE_ROOT / "S13dlink-media-ready").read_bytes(),
    )

