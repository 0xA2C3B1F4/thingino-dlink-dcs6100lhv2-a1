"""Final-root vendor media closure and Prudynt configuration."""

from __future__ import annotations


def _os_release_values(facade: object, path: Path) -> dict[str, str]:
    Path = getattr(facade, 'Path')
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values

def _require_proven_media_runtime(facade: object, root: Path, *, require_vendor: bool = True) -> None:
    EXPECTED_IMAGE_ID = getattr(facade, 'EXPECTED_IMAGE_ID')
    FORBIDDEN_MEDIA_MARKERS = getattr(facade, 'FORBIDDEN_MEDIA_MARKERS')
    FinalRootError = getattr(facade, 'FinalRootError')
    MEDIA_MARKERS = getattr(facade, 'MEDIA_MARKERS')
    Path = getattr(facade, 'Path')
    _os_release_values = getattr(facade, '_os_release_values')
    release = _os_release_values(root / "etc/os-release")
    if release.get("LIBC") != "glibc" or release.get("TOOLCHAIN") != "glibc":
        raise FinalRootError("base root is not the live-proven glibc runtime")
    if release.get("IMAGE_ID") != EXPECTED_IMAGE_ID:
        raise FinalRootError("base root targets the wrong Thingino image")
    prudynt = (root / "usr/bin/prudynt").read_bytes()
    if b"/lib/ld.so.1" not in prudynt:
        raise FinalRootError("Prudynt does not use the glibc interpreter")
    for marker in MEDIA_MARKERS:
        if marker not in prudynt:
            raise FinalRootError("Prudynt lacks the normal D-Link media implementation")
    for marker in FORBIDDEN_MEDIA_MARKERS:
        if marker in prudynt:
            raise FinalRootError("Prudynt contains a non-normal D-Link media path")
    if require_vendor:
        libimp = (root / "usr/lib/libimp.so").read_bytes()
        if not libimp.startswith(b"\x7fELF") or b"1.1.4\0" not in libimp:
            raise FinalRootError("final root does not contain camera-local IMP 1.1.4")
        for library in (b"libimp.so\0", b"libalog.so\0", b"libsysutils.so\0"):
            if library not in prudynt:
                raise FinalRootError("Prudynt lacks the camera-local vendor dependency closure")

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
        raise FinalRootError("camera-local vendor media closure is incomplete")
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
            raise FinalRootError(f"installed camera-local media changed: {artifact.name}")
    for destination, digest in source_support.items():
        if hashlib.sha256((root / destination).read_bytes()).hexdigest() != digest:
            raise FinalRootError("camera-local bundle replaced source-built support libraries")


def _record_source_media(
    facade: object,
    root: Path,
    bundle: VendorBundle,
) -> dict[str, object]:
    """Bind the public source build and stock vendor runtime without a private closure."""

    FinalRootError = getattr(facade, "FinalRootError")
    KERNEL_RELEASE = getattr(facade, "KERNEL_RELEASE")
    Path = getattr(facade, "Path")
    hashlib = getattr(facade, "hashlib")

    prudynt_path = root / "usr/bin/prudynt"
    config_path = root / "etc/prudynt.json"
    if prudynt_path.is_symlink() or not prudynt_path.is_file():
        raise FinalRootError("base root lacks its source-built Prudynt")
    if config_path.is_symlink() or not config_path.is_file():
        raise FinalRootError("base root lacks its source-built Prudynt configuration")

    source_built_init: list[dict[str, object]] = []
    for name in ("F01datetime", "S06ircut", "S10daynightd", "S11modules", "S31prudynt"):
        path = root / "etc/init.d" / name
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(f"base root lacks source-built media init: {name}")
        raw = path.read_bytes()
        source_built_init.append(
            {
                "destination": f"/etc/init.d/{name}",
                "origin": "pinned-source-build",
                "path": f"init/{name}",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )

    files: list[dict[str, object]] = []
    config = config_path.read_bytes()
    config_sha256 = hashlib.sha256(config).hexdigest()
    files.append(
        {
            "destination": "/etc/prudynt.json",
            "origin": "pinned-source-build",
            "path": "etc/prudynt.json",
            "sha256": config_sha256,
            "size": len(config),
            "source_sha256": config_sha256,
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
        raise FinalRootError("camera-local vendor runtime closure is incomplete")
    installed_vendor_names = {
        "libimp.so",
        "tx-isp-t31.ko",
        "sensor_os02g10_t31.ko",
        "os02g10-t31.bin",
    }
    for name in sorted(installed_vendor_names):
        artifact = artifacts[name]
        if artifact.destination is None:
            raise FinalRootError("camera-local vendor runtime lacks a destination")
        destination = root / artifact.destination
        if destination.is_symlink() or not destination.is_file():
            raise FinalRootError(f"camera-local vendor runtime is missing: {name}")
        raw = destination.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise FinalRootError(f"camera-local vendor runtime changed: {name}")
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
            raise FinalRootError("camera-local vendor runtime lacks a destination")
        relative = artifact.destination
        destination = root / relative
        if destination.is_symlink() or not destination.is_file():
            raise FinalRootError(f"source-built support library is missing: {relative}")
        raw = destination.read_bytes()
        entry = {
            "destination": "/" + relative,
            "origin": "pinned-public-source-build",
            "path": f"source/{name}",
            "sha256": hashlib.sha256(raw).hexdigest(),
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

    prudynt = prudynt_path.read_bytes()
    return {
        "schema_version": 4,
        "target": "DCS-6100LHV2-A1",
        "runtime": "source-built-prudynt-global-glibc-c1-closure",
        "prudynt": {
            "origin": "pinned-source-build",
            "sha256": hashlib.sha256(prudynt).hexdigest(),
            "size": len(prudynt),
        },
        "source_built_init": source_built_init,
        "startup_order": ["S06ircut", "S10daynightd", "S11modules", "S31prudynt"],
        "runtime_dlopen": ["libaudioProcess.so"],
        "runtime_config_sha256": config_sha256,
        "runtime_config_source_sha256": config_sha256,
        "vendor_bundle_sha256": bundle.bundle_sha256,
        "vendor_manifest_sha256": bundle.manifest_sha256,
        "kernel_release": KERNEL_RELEASE,
        "acquired_stock_inputs": acquired_inputs,
        "source_built_support": source_support,
        "files": files,
    }

def _install_media_closure(facade: object,
    root: Path,
    closure: MediaClosure,
) -> dict[str, object]:
    FinalRootError = getattr(facade, 'FinalRootError')
    KERNEL_RELEASE = getattr(facade, 'KERNEL_RELEASE')
    MediaClosure = getattr(facade, 'MediaClosure')
    Path = getattr(facade, 'Path')
    _global_glibc_library = getattr(facade, '_global_glibc_library')
    _write_private = getattr(facade, '_write_private')
    hashlib = getattr(facade, 'hashlib')
    json = getattr(facade, 'json')
    for relative in ("opt/dlink-media/closure", "opt/dlink-media/runtime"):
        path = root / relative
        if path.exists() or path.is_symlink():
            raise FinalRootError("base root already contains a duplicate media runtime")

    prudynt_path = root / "usr/bin/prudynt"
    if prudynt_path.is_symlink() or not prudynt_path.is_file():
        raise FinalRootError("base root lacks its source-built Prudynt")
    prudynt_raw = prudynt_path.read_bytes()
    prudynt_sha256 = hashlib.sha256(prudynt_raw).hexdigest()

    installed: list[dict[str, object]] = []
    source_built_init: list[dict[str, object]] = []
    for artifact in closure.files:
        if artifact.path == "bin/prudynt":
            # The private closure predates the source-first build and still
            # carries its proven reference binary. Keep it as validation input,
            # but never replace the patched Prudynt built from pinned sources.
            continue
        if artifact.path.startswith("init/"):
            # The closure also predates the source-first init changes. Its
            # scripts remain proven reference inputs, but replacing the pinned
            # build output here would silently undo later safety fixes (for
            # example direct IR-cut GPIO control and Prudynt's stack limit).
            name = artifact.path.removeprefix("init/")
            destination = root / "etc/init.d" / name
            if destination.is_symlink() or not destination.is_file():
                raise FinalRootError(
                    f"base root lacks source-built media init: {artifact.path}"
                )
            raw = destination.read_bytes()
            source_built_init.append(
                {
                    "destination": "/" + destination.relative_to(root).as_posix(),
                    "origin": "pinned-source-build",
                    "path": artifact.path,
                    "reference_sha256": artifact.sha256,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
            continue
        if artifact.path == "etc/prudynt.json":
            destination = root / "etc/prudynt.json"
            mode = 0o600
        elif artifact.path.startswith("lib/"):
            name = artifact.path.removeprefix("lib/")
            library_entry = root / "lib" / name
            if name == "libaudioProcess.so" and library_entry.is_symlink():
                raise FinalRootError("base root has unsafe libaudioProcess.so alias")
            if name == "libaudioProcess.so" and not library_entry.exists():
                destination = root / "usr/lib" / name
            else:
                destination = _global_glibc_library(root, name)
            mode = 0o755 if name in {"ld.so.1", "libaudioProcess.so"} else 0o644
        elif artifact.path.startswith("modules/"):
            destination = (
                root
                / "usr/lib/modules"
                / KERNEL_RELEASE
                / "ingenic"
                / artifact.path.removeprefix("modules/")
            )
            mode = 0o644
        elif artifact.path.startswith("sensor/"):
            destination = root / "usr/share/sensor" / artifact.path.removeprefix("sensor/")
            mode = 0o644
        else:
            raise FinalRootError(f"unsupported C1 closure path: {artifact.path}")
        if destination.is_symlink():
            raise FinalRootError(f"base root has unsafe C1 destination: {artifact.path}")
        if not destination.is_file() and (
            artifact.path != "lib/libaudioProcess.so" or destination.exists()
        ):
            raise FinalRootError(f"base root lacks C1 destination: {artifact.path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(artifact.raw)
        destination.chmod(mode)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != artifact.sha256:
            raise FinalRootError(f"installed C1 identity mismatch: {artifact.path}")
        installed.append(
            {
                "destination": "/" + destination.relative_to(root).as_posix(),
                "path": artifact.path,
                "sha256": artifact.sha256,
                "size": len(artifact.raw),
            }
        )
    if hashlib.sha256(prudynt_path.read_bytes()).hexdigest() != prudynt_sha256:
        raise FinalRootError("media closure replaced the source-built Prudynt")
    provenance = {
        "schema_version": 3,
        "target": "DCS-6100LHV2-A1",
        "runtime": "source-built-prudynt-global-glibc-c1-closure",
        "prudynt": {
            "origin": "pinned-source-build",
            "sha256": prudynt_sha256,
            "size": len(prudynt_raw),
        },
        "source_built_init": source_built_init,
        "closure_sha256": closure.closure_sha256,
        "manifest_sha256": closure.manifest_sha256,
        "startup_order": list(closure.startup_order),
        "runtime_dlopen": list(closure.runtime_dlopen),
        "runtime_config_sha256": closure.by_path()["etc/prudynt.json"].sha256,
        "files": installed,
    }
    _write_private(
        root / "etc/dlink-media-closure.private.json",
        (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode(),
    )
    return provenance

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

def _configure_media_first_init(facade: object, root: Path) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    Path = getattr(facade, 'Path')
    TEMPLATE_ROOT = getattr(facade, 'TEMPLATE_ROOT')
    _move_required = getattr(facade, '_move_required')
    _write_executable = getattr(facade, '_write_executable')
    init_root = root / "etc/init.d"
    for name in (
        "F01datetime",
        "S06ircut",
        "S10daynightd",
        "S11modules",
        "S30mdev",
        "S31prudynt",
    ):
        path = init_root / name
        if path.is_symlink() or not path.is_file():
            raise FinalRootError(f"base root lacks required media init script: {name}")
    for name in (
        "S10mdev",
        "S12prudynt",
        "S13dlink-media-ready",
        "S14volatile-config",
        "S15thingino-button",
        "S56prudynt",
        "dlink-media-start",
    ):
        legacy = init_root / name
        if legacy.is_symlink() or legacy.is_file():
            legacy.unlink()
        elif legacy.exists():
            raise FinalRootError(f"legacy media init path changed type: {name}")
    for relative in ("etc/modules.d/gpio-userkeys", "etc/thingino-button.conf"):
        legacy = root / relative
        if legacy.is_symlink() or legacy.is_file():
            legacy.unlink()
        elif legacy.exists():
            raise FinalRootError(f"legacy reset input path changed type: {relative}")
    _move_required(root, "etc/init.d/S09mmc", "etc/init.d/S14mmc")
    _move_required(root, "etc/init.d/S02ssl", "etc/init.d/S59ssl")
    _write_executable(
        init_root / "S04loopback",
        (TEMPLATE_ROOT / "S04loopback").read_bytes(),
    )
    _write_executable(
        init_root / "S05dlink-media-preconditions",
        (TEMPLATE_ROOT / "S05dlink-media-preconditions").read_bytes(),
    )
    (init_root / "S31prudynt").chmod(0o755)
    _write_executable(
        init_root / "S55installer-health",
        (TEMPLATE_ROOT / "S55installer-health").read_bytes(),
    )
    # Acceptance stays separate from installer health and starts no process.
    _write_executable(
        init_root / "dlink-media-acceptance",
        (TEMPLATE_ROOT / "S13dlink-media-ready").read_bytes(),
    )

def _require_section(facade: object, document: dict[str, object], name: str) -> dict[str, object]:
    FinalRootError = getattr(facade, 'FinalRootError')
    value = document.get(name)
    if not isinstance(value, dict):
        raise FinalRootError(f"Prudynt lacks {name} configuration")
    return value

def _configure_prudynt_media(facade: object, document: dict[str, object]) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    _require_section = getattr(facade, '_require_section')
    # Stock TX-ISP does not expose Thingino's /proc/jz/sensor defaults.
    # Supply the complete board wiring and geometry instead of falling back
    # to Prudynt's generic I2C address (0x37) and zero-sized sensor.
    _require_section(document, "sensor").update(
        {
            "model": "os02g10",
            "fps": 25,
            "gpio_reset": 18,
            "height": 1080,
            "i2c_address": 0x3C,
            "i2c_bus": 0,
            "width": 1920,
        }
    )
    _require_section(document, "stream0").update(
        {
            "enabled": True,
            "width": 1920,
            "height": 1080,
            "fps": 15,
            "buffers": 1,
            "bitrate": 3000,
            "audio_enabled": True,
        }
    )
    _require_section(document, "stream1").update(
        {"enabled": True, "fps": 15, "buffers": 1, "audio_enabled": True}
    )
    for name in ("stream2", "stream3"):
        _require_section(document, name)["enabled"] = False
    audio = _require_section(document, "audio")
    audio.update(
        {
            "mic_enabled": True,
            "mic_format": "AAC",
            "mic_is_digital": True,
            "spk_enabled": False,
            "tap_enabled": False,
        }
    )
    rtsp = _require_section(document, "rtsp")
    rtsp["audio_only_enabled"] = False
    motion = _require_section(document, "motion")
    motion["enabled"] = False
    osd = _require_section(document, "osd")
    for name in ("burnin", "privacy", "sei"):
        value = osd.get(name)
        if not isinstance(value, dict):
            raise FinalRootError(f"Prudynt lacks osd.{name} configuration")
        value["enabled"] = name == "burnin"
    burnin = osd["burnin"]
    if isinstance(burnin, dict):
        burnin["format"] = "%F %T %Z"

def _configure_prudynt_viewer_credential(facade: object,
    document: dict[str, object], password: str
) -> None:
    _require_section = getattr(facade, '_require_section')
    rtsp = _require_section(document, "rtsp")
    rtsp["username"] = "viewer"
    rtsp["password"] = password

def _configure_prudynt_jpeg_idle(facade: object, document: dict[str, object]) -> None:
    FinalRootError = getattr(facade, 'FinalRootError')
    _require_section = getattr(facade, '_require_section')
    """Keep JPEG on demand and bound the fixed WebUI preview profile."""

    for name in ("stream2", "stream3"):
        stream = _require_section(document, name)
        for field in ("fps", "jpeg_idle_fps", "jpeg_quality"):
            if field not in stream:
                raise FinalRootError(f"Prudynt lacks {name}.{field} configuration")
        stream["fps"] = 5
        stream["jpeg_idle_fps"] = 0
        stream["jpeg_quality"] = 70

def _configure_prudynt_http_ingress(facade: object, document: dict[str, object]) -> None:
    _require_section = getattr(facade, '_require_section')
    """Keep Prudynt media private behind the authenticated uhttpd ingress."""

    http = _require_section(document, "http")
    http["loopback_only"] = True
