"""Prepare a private, install-scoped final Thingino SquashFS."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .artifacts import SOURCE_DATE_EPOCH, validate_squashfs
from .layout import TARGET
from .media_closure import MediaClosure, load_media_closure
from .mtd3_image import FOOTER_SIZE
from .private_config import (
    PrivateConfigError,
    load_private_config_for_session,
    load_private_wpa_config,
    station_wifi_binding,
)
from .runtime_policy import (
    DROPBEAR_DEVELOPMENT_ARGUMENTS,
    DROPBEAR_KEY_ONLY_ARGUMENTS,
    EMPTY_RESOLVER_POLICY,
    LOOPBACK_INTERFACE,
    NETWORK_DEFAULT_ROUTE_GUARD,
    NETWORK_INTERFACES,
    UDHCPC_NO_DEFAULT,
    WLAN_DHCP_NO_DEFAULT,
    normalize_ed25519_authorized_key,
)
from .sd_package import atomic_write
from .stage1.build import (
    FINAL_FORBIDDEN_PATHS,
    Stage1BuildError,
    validate_final_root,
)
from .vendor_bundle import VendorBundle, load_vendor_bundle


KERNEL_RELEASE = "3.10.14__isvp_swan_1.0__"
OUTPUT_NAME = "system.private.squashfs"
TOKEN_PATTERN = re.compile(rb"[0-9a-f]{64}\n")
SHA512_CRYPT_PATTERN = re.compile(r"\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}")
TEMPLATE_ROOT = Path(__file__).with_name("templates")
EXPECTED_IMAGE_ID = "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu"
HA_LIVE_IMAGE_INTERVAL_SECONDS = 5
MEDIA_MARKERS = (
    b"D-Link 1080p encoder receive path is ready",
    b"/run/prudynt-dlink-media.ready",
    b"/etc/sensor/os02g10-t31.bin",
    b"IMP_OSD_SetPoolSize",
    b"/snapshot",
    b"loopback ingress required",
    b"per-request JPEG quality and size are unsupported",
    b"http.loopback_only",
    b"127.0.0.1:",
)
FORBIDDEN_MEDIA_MARKERS = (
    b"D-Link minimal pipeline",
    b"skipping incompatible hardware OSD pool",
)
NATIVE_MEDIA_PATHS = (
    "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/tx-isp-t31.ko",
    "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/sensor_os02g10_t31.ko",
    "usr/share/sensor/os02g10-t31.bin",
)


class FinalRootError(ValueError):
    """The base root or private input violates final-root policy."""


def _run(arguments: list[str], label: str, *, input_bytes: bytes | None = None) -> bytes:
    from .final_root_security import _run as _impl

    return _impl(sys.modules[__name__], arguments, label, input_bytes=input_bytes)


def _write_private(path: Path, raw: bytes) -> None:
    from .final_root_security import _write_private as _impl

    return _impl(sys.modules[__name__], path, raw)


def _materialize_wpa_runtime_policy(wpa_config: bytes) -> bytes:
    from .final_root_security import _materialize_wpa_runtime_policy as _impl

    return _impl(sys.modules[__name__], wpa_config)


def _validate_built_station_wifi(
    *, image: Path, expected: bytes, unsquashfs: Path
) -> None:
    from .final_root_security import _validate_built_station_wifi as _impl

    return _impl(sys.modules[__name__], image=image, expected=expected, unsquashfs=unsquashfs)


def _write_executable(path: Path, raw: bytes) -> None:
    from .final_root_security import _write_executable as _impl

    return _impl(sys.modules[__name__], path, raw)


def _extract_base_root(*, unsquashfs: Path, source: Path, destination: Path) -> None:
    from .final_root_security import _extract_base_root as _impl

    return _impl(sys.modules[__name__], unsquashfs=unsquashfs, source=source, destination=destination)


def _read_private(path: Path, label: str, *, limit: int) -> bytes:
    from .final_root_security import _read_private as _impl

    return _impl(sys.modules[__name__], path, label, limit=limit)


def _set_root_password(path: Path, credential: bytes) -> str:
    from .final_root_security import _set_root_password as _impl

    return _impl(sys.modules[__name__], path, credential)


def _replace_exact(path: Path, old: str, new: str, label: str) -> None:
    from .final_root_security import _replace_exact as _impl

    return _impl(sys.modules[__name__], path, old, new, label)


def _configure_key_only_ssh(
    root: Path,
    authorized_key: bytes,
    dropbear_host_key: bytes,
    credential: bytes,
) -> None:
    from .final_root_security import _configure_key_only_ssh as _impl

    return _impl(sys.modules[__name__], root, authorized_key, dropbear_host_key, credential)


def _configure_no_default_route(root: Path) -> None:
    from .final_root_security import _configure_no_default_route as _impl

    return _impl(sys.modules[__name__], root)


def _patch_json(
    path: Path, mutator: Callable[[dict[str, object]], None]
) -> None:
    from .final_root_security import _patch_json as _impl

    return _impl(sys.modules[__name__], path, mutator)


def _configure_control(document: dict[str, object], credential: bytes) -> None:
    from .final_root_security import _configure_control as _impl

    return _impl(sys.modules[__name__], document, credential)


def _configure_ha_live_image(document: dict[str, object]) -> None:
    from .final_root_security import _configure_ha_live_image as _impl

    return _impl(sys.modules[__name__], document)


def _remove_unsafe_paths(root: Path) -> None:
    from .final_root_security import _remove_unsafe_paths as _impl

    return _impl(sys.modules[__name__], root)


def _os_release_values(path: Path) -> dict[str, str]:
    from .final_root_media import _os_release_values as _impl

    return _impl(sys.modules[__name__], path)


def _require_proven_media_runtime(root: Path, *, require_vendor: bool = True) -> None:
    from .final_root_media import _require_proven_media_runtime as _impl

    return _impl(sys.modules[__name__], root, require_vendor=require_vendor)


def _snapshot_native_media(root: Path) -> dict[str, str]:
    from .final_root_media import _snapshot_native_media as _impl

    return _impl(sys.modules[__name__], root)


def _install_vendor_bundle(root: Path, bundle: VendorBundle) -> None:
    from .final_root_media import _install_vendor_bundle as _impl

    return _impl(sys.modules[__name__], root, bundle)


def _install_media_closure(
    root: Path,
    closure: MediaClosure,
) -> dict[str, object]:
    from .final_root_media import _install_media_closure as _impl

    return _impl(sys.modules[__name__], root, closure)


def _global_glibc_library(root: Path, name: str) -> Path:
    from .final_root_media import _global_glibc_library as _impl

    return _impl(sys.modules[__name__], root, name)


def _require_native_media_unchanged(root: Path, expected: dict[str, str]) -> None:
    from .final_root_media import _require_native_media_unchanged as _impl

    return _impl(sys.modules[__name__], root, expected)


def _move_required(root: Path, source: str, destination: str) -> None:
    from .final_root_media import _move_required as _impl

    return _impl(sys.modules[__name__], root, source, destination)


def _configure_media_first_init(root: Path) -> None:
    from .final_root_media import _configure_media_first_init as _impl

    return _impl(sys.modules[__name__], root)


def _require_section(document: dict[str, object], name: str) -> dict[str, object]:
    from .final_root_media import _require_section as _impl

    return _impl(sys.modules[__name__], document, name)


def _configure_prudynt_media(document: dict[str, object]) -> None:
    from .final_root_media import _configure_prudynt_media as _impl

    return _impl(sys.modules[__name__], document)


def _configure_prudynt_management_credential(
    document: dict[str, object], password: str
) -> None:
    from .final_root_media import _configure_prudynt_management_credential as _impl

    return _impl(sys.modules[__name__], document, password)


def _configure_prudynt_jpeg_idle(document: dict[str, object]) -> None:
    from .final_root_media import _configure_prudynt_jpeg_idle as _impl

    return _impl(sys.modules[__name__], document)


def _configure_prudynt_http_ingress(document: dict[str, object]) -> None:
    from .final_root_media import _configure_prudynt_http_ingress as _impl

    return _impl(sys.modules[__name__], document)


def prepare_final_root(
    *,
    base_rootfs: bytes,
    wpa_config: bytes,
    credential: bytes,
    api_key: bytes,
    authorized_key: bytes,
    dropbear_host_key: bytes,
    station_hostname: str,
    vendor_bundle: VendorBundle,
    media_closure: MediaClosure,
    output_dir: Path,
    mksquashfs: Path,
    unsquashfs: Path,
    output_size: int | None = None,
) -> dict[str, object]:
    payload_limit = TARGET.partition(3).size - FOOTER_SIZE
    if output_size is not None and output_size > payload_limit:
        raise FinalRootError(
            "requested final-root output size exceeds mtd3 SquashFS payload"
        )
    if output_size is not None and output_size <= 0:
        raise FinalRootError("requested final-root output size must be positive")
    validate_squashfs(base_rootfs)
    if TOKEN_PATTERN.fullmatch(credential) is None:
        raise FinalRootError("private credential is not a 256-bit hexadecimal token")
    if TOKEN_PATTERN.fullmatch(api_key) is None:
        raise FinalRootError("WebUI API key is not a 256-bit hexadecimal token")
    if b"ssid=" not in wpa_config or b"psk=" not in wpa_config:
        raise FinalRootError("private WPA configuration is incomplete")
    if re.fullmatch(r"dcs6100-[0-9a-f]{8}", station_hostname) is None:
        raise FinalRootError("station mDNS hostname is not bound to the private session")
    if output_dir.exists():
        raise FinalRootError("refusing to reuse a private final-root output directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        root = work / "root"
        base_path = work / "base.squashfs"
        output = work / OUTPUT_NAME
        base_path.write_bytes(base_rootfs)
        _extract_base_root(
            unsquashfs=unsquashfs,
            source=base_path,
            destination=root,
        )
        _remove_unsafe_paths(root)
        _require_proven_media_runtime(root, require_vendor=False)
        native_media = _snapshot_native_media(root)
        _install_vendor_bundle(root, vendor_bundle)
        _require_native_media_unchanged(root, native_media)
        _require_proven_media_runtime(root)
        media_provenance = _install_media_closure(root, media_closure)
        _require_proven_media_runtime(root)
        _configure_media_first_init(root)
        _write_private(
            root / "etc/wpa_supplicant.conf",
            _materialize_wpa_runtime_policy(wpa_config),
        )
        _configure_key_only_ssh(root, authorized_key, dropbear_host_key, credential)
        _write_private(root / "etc/hostname", (station_hostname + "\n").encode("ascii"))
        _configure_no_default_route(root)
        password = credential.decode("ascii").strip()

        def patch_onvif(document: dict[str, object]) -> None:
            server = document.get("server")
            if not isinstance(server, dict):
                raise FinalRootError("ONVIF lacks server configuration")
            server["username"] = "root"
            server["password"] = password

        def patch_thingino(document: dict[str, object]) -> None:
            gpio = document.get("gpio")
            if not isinstance(gpio, dict):
                raise FinalRootError("Thingino lacks GPIO configuration")
            gpio["ircut"] = "50 49"
            daynight = document.get("daynight")
            if not isinstance(daynight, dict):
                raise FinalRootError("Thingino lacks day/night configuration")
            controls = daynight.get("controls")
            if not isinstance(controls, dict):
                raise FinalRootError("Thingino lacks day/night controls")
            controls["color"] = True
            document["enable_updates"] = False
            document["hostname"] = station_hostname
            _configure_control(document, credential)
            _configure_ha_live_image(document)

        _patch_json(root / "etc/onvif.json", patch_onvif)
        def patch_prudynt(document: dict[str, object]) -> None:
            # The private C1 closure carries a proven reference configuration,
            # but its legacy stream1 sentinels are not the production profile.
            # Re-apply the device-bound media policy after installing that
            # closure so the final image cannot contain enabled:true together
            # with fps:0 or buffers:-1.
            _configure_prudynt_media(document)
            _configure_prudynt_management_credential(document, password)
            _configure_prudynt_jpeg_idle(document)
            _configure_prudynt_http_ingress(document)

        _patch_json(root / "etc/prudynt.json", patch_prudynt)
        runtime_config = (root / "etc/prudynt.json").read_bytes()
        runtime_config_sha256 = hashlib.sha256(runtime_config).hexdigest()
        media_provenance["runtime_config_source_sha256"] = media_provenance[
            "runtime_config_sha256"
        ]
        media_provenance["runtime_config_sha256"] = runtime_config_sha256
        runtime_entries = [
            entry
            for entry in media_provenance["files"]
            if entry["path"] == "etc/prudynt.json"
        ]
        if len(runtime_entries) != 1:
            raise FinalRootError("media provenance lacks one Prudynt configuration")
        runtime_entry = runtime_entries[0]
        runtime_entry["source_sha256"] = runtime_entry["sha256"]
        runtime_entry["sha256"] = runtime_config_sha256
        runtime_entry["size"] = len(runtime_config)
        _write_private(
            root / "etc/dlink-media-closure.private.json",
            (json.dumps(media_provenance, indent=2, sort_keys=True) + "\n").encode(),
        )
        _patch_json(root / "etc/thingino.json", patch_thingino)
        _write_private(
            root / "etc/thingino-api.key",
            api_key,
        )
        _write_private(
            root / "etc/dcs6100-personal-image.json",
            (
                json.dumps(
                    {
                        "schema_version": 1,
                        "station_mdns_name": station_hostname + ".local",
                        "target": "DCS-6100LHV2-A1",
                        "vendor_bundle_sha256": vendor_bundle.bundle_sha256,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("ascii"),
        )
        _write_executable(
            root / "usr/sbin/thingino-health",
            (TEMPLATE_ROOT / "thingino-health").read_bytes(),
        )
        _write_executable(
            root / "usr/sbin/dlink-media-verify",
            (TEMPLATE_ROOT / "dlink-media-verify").read_bytes(),
        )
        _write_executable(
            root / "usr/sbin/dlink-runtime-snapshot",
            (TEMPLATE_ROOT / "dlink-runtime-snapshot").read_bytes(),
        )
        _run(
            [
                str(mksquashfs),
                str(root),
                str(output),
                "-comp",
                "xz",
                "-b",
                "262144",
                "-noappend",
                "-no-tailends",
                "-all-root",
                "-no-xattrs",
                "-no-progress",
                "-repro-time",
                str(SOURCE_DATE_EPOCH),
            ],
            "private final-root build",
        )
        raw = output.read_bytes()
        validate_squashfs(raw)
        if len(raw) > payload_limit:
            raise FinalRootError("private final-root exceeds mtd3 SquashFS payload")
        if output_size is not None:
            if output_size < len(raw):
                raise FinalRootError(
                    "requested final-root output size is smaller than SquashFS"
                )
            if output_size != len(raw):
                raw += b"\0" * (output_size - len(raw))
                output.write_bytes(raw)
            validate_squashfs(raw, partition_limit=payload_limit)
        validate_final_root(raw, unsquashfs=unsquashfs, temporary_parent=work)
        materialized_wpa = _materialize_wpa_runtime_policy(wpa_config)
        _validate_built_station_wifi(
            image=output,
            expected=materialized_wpa,
            unsquashfs=unsquashfs,
        )
        manifest = {
            "schema_version": 1,
            "status": "private final-root input; not an install authorization",
            "source_sha256": hashlib.sha256(base_rootfs).hexdigest(),
            "system": {
                "filename": OUTPUT_NAME,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "policies": {
                "unique_credentials": True,
                "unique_management_credentials": True,
                "private_wifi": True,
                "station_wifi_bound": True,
                "station_wifi_sha256": hashlib.sha256(wpa_config).hexdigest(),
                "ssh_authentication": "ed25519-key-only",
                "web_ui_authentication": "root-per-install-credential-sha512-crypt",
                "ssh_host_key": "session-pinned-ed25519",
                "station_mdns_name": station_hostname + ".local",
                "automatic_default_route": False,
                "ipv6": True,
                "ircut": "50 49",
                "media_runtime": "source-built-prudynt-global-glibc-c1-closure",
                "media_start": "automatic-S31prudynt",
                "media_memory_preflight": "drop-clean-caches-before-media-modules",
                "media_closure_sha256": media_closure.closure_sha256,
                "media_closure_manifest_sha256": media_closure.manifest_sha256,
                "media_runtime_config_sha256": media_provenance[
                    "runtime_config_sha256"
                ],
                "media_credentials": "root-per-install-management-credential",
                "runtime_dlopen": list(media_closure.runtime_dlopen),
                "native_media": "hash-locked-c1-tx-isp-sensor-iq",
                "vendor_libraries": "hash-locked-c1-global-closure",
                "vendor_bundle_sha256": vendor_bundle.bundle_sha256,
                "vendor_manifest_sha256": vendor_bundle.manifest_sha256,
                "sensor_unknown_fallback": "json-os02g10",
                "generic_updater_removed": True,
                "polluted_module_paths_removed": True,
                "output_size": len(raw),
            },
        }
        atomic_write(
            work / "final-root.private.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        base_path.unlink()
        shutil.rmtree(root)
        os.chmod(work, 0o700)
        for path in work.iterdir():
            path.chmod(0o600)
        os.replace(work, output_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def prepare_from_private_directory(
    *,
    base_rootfs_path: Path,
    private_config_dir: Path,
    expected_wpa_config_path: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    session_dir: Path,
    output_dir: Path,
    mksquashfs: Path,
    unsquashfs: Path,
    output_size: int | None = None,
) -> dict[str, object]:
    from .recovery_ap.host import load_host_session

    session = load_host_session(session_dir)
    try:
        private = load_private_config_for_session(
            output_dir=private_config_dir,
            session_dir=session_dir,
        )
    except PrivateConfigError as exc:
        raise FinalRootError(str(exc)) from exc
    try:
        expected_wpa = load_private_wpa_config(
            expected_wpa_config_path,
            independent_from=private_config_dir / "wpa_supplicant.conf",
        )
    except PrivateConfigError as exc:
        raise FinalRootError(str(exc)) from exc
    if not hmac.compare_digest(
        station_wifi_binding(private.wpa_config),
        station_wifi_binding(expected_wpa),
    ):
        raise FinalRootError(
            "sealed private configuration does not match expected station Wi-Fi"
        )
    return prepare_final_root(
        base_rootfs=base_rootfs_path.read_bytes(),
        wpa_config=private.wpa_config,
        credential=private.credential,
        api_key=private.api_key,
        authorized_key=private.authorized_key,
        dropbear_host_key=_read_private(
            session_dir / "media/RECOVERY/HOST.KEY",
            "session Dropbear host key",
            limit=4096,
        ),
        station_hostname=session.station_mdns_name.removesuffix(".local"),
        vendor_bundle=load_vendor_bundle(vendor_bundle_dir),
        media_closure=load_media_closure(media_closure_dir),
        output_dir=output_dir,
        mksquashfs=mksquashfs,
        unsquashfs=unsquashfs,
        output_size=output_size,
    )
