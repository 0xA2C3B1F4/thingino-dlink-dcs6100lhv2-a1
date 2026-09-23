"""Compose the Raptor-only universal final root."""

from __future__ import annotations

import hashlib
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
from .mtd3_split import SYSTEM_FLASH_SPAN
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
from .vendor_bundle import VendorBundle


KERNEL_RELEASE = "3.10.14__isvp_swan_1.0__"
OUTPUT_NAME = "system.private.squashfs"
UNIVERSAL_OUTPUT_NAME = "system.universal.squashfs"
UNIVERSAL_HOSTNAME = "dcs6100-unprovisioned"
UNIVERSAL_MARKER = "etc/dcs6100-universal-image.json"
UNIVERSAL_DISABLED_INIT = (
    "etc/init.d/S30dropbear",
    "etc/init.d/S38wpa_supplicant",
    "etc/init.d/S40network",
    "etc/init.d/S60uhttpd",
    "etc/init.d/S94onvif-httpd",
    "etc/init.d/S95thingino-control",
)
SHA512_CRYPT_PATTERN = re.compile(r"\$6\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}")
TEMPLATE_ROOT = Path(__file__).with_name("templates")
EXPECTED_IMAGE_ID = "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu"
HA_LIVE_IMAGE_INTERVAL_SECONDS = 5
FINAL_ROOT_SQUASHFS_BLOCK_SIZE = 256 * 1024
NATIVE_MEDIA_PATHS = (
    "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/tx-isp-t31.ko",
    "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/sensor_os02g10_t31.ko",
    "usr/share/sensor/os02g10-t31.bin",
)
FINAL_FORBIDDEN_PATHS = {
    "etc/init.d/S40wired-gateway",
    "etc/init.d/S41ifplugd",
    "etc/network/interfaces.d/eth0",
    "etc/profile.d/chpasswd",
    "etc/init.d/S97sysupgrade",
    "etc/init.d/S14volatile-config",
    "etc/init.d/S15thingino-button",
    "etc/init.d/S07dusk2dawn",
    "etc/init.d/S10mdev",
    "etc/init.d/S12prudynt",
    "etc/init.d/S13dlink-media-ready",
    "etc/init.d/S56prudynt",
    "etc/init.d/dlink-media-start",
    "etc/modules.d/gpio-userkeys",
    "etc/thingino-button.conf",
    "opt/dlink-media/closure",
    "opt/dlink-media/runtime",
    "usr/sbin/flash_eraseall",
    "usr/sbin/flashcp",
    "usr/sbin/fw_printenv",
    "usr/sbin/fw_setenv",
    "usr/sbin/sysupgrade",
    "usr/sbin/sysupgrade-stage2",
    "usr/sbin/thingino-agentd",
    "usr/sbin/thingino-agent-rs",
    "usr/sbin/thingino-agentctl",
    "usr/libexec/thingino-agent/lib.sh",
    "usr/libexec/thingino-agent/adapters/null.sh",
    "usr/libexec/thingino-agent/adapters/prudynt.sh",
    "usr/libexec/thingino-agent/listener",
    "usr/libexec/thingino-agent/tls-proxy",
    "etc/init.d/S95thingino-agent",
    "usr/libexec/thingino-webui",
    "usr/sbin/recordmgr",
    "usr/sbin/daynight",
    "usr/sbin/dusk2dawn",
    "etc/init.d/S95recordmgr",
    "etc/init.d/S48webui-config",
    "etc/init.d/S91mqttsub",
    "usr/sbin/mqtt-sub-dispatcher",
    "usr/sbin/telegram-cam-register",
    "usr/sbin/telegram-cam-agent",
    "usr/bin/prudynt",
    "usr/bin/prudyntctl",
    "usr/bin/daynightd",
    "etc/prudynt.json",
    "etc/init.d/S10daynightd",
    "etc/init.d/S31prudynt",
    "etc/init.d/S13prudynt-rwd",
    "etc/init.d/S96rwd",
    "etc/init.d/S98recorder",
}


class FinalRootError(ValueError):
    """A base root cannot satisfy the Raptor final-root contract."""


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


def _build_final_root_squashfs(
    *, mksquashfs: Path, root: Path, output: Path, label: str
) -> None:
    _run(
        [
            str(mksquashfs),
            str(root),
            str(output),
            "-comp",
            "xz",
            "-b",
            str(FINAL_ROOT_SQUASHFS_BLOCK_SIZE),
            "-noappend",
            "-no-tailends",
            "-all-root",
            "-no-xattrs",
            "-no-progress",
            "-repro-time",
            str(SOURCE_DATE_EPOCH),
        ],
        label,
    )


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
    from .final_root_media import _os_release_values as implementation

    return implementation(sys.modules[__name__], path)


def _require_proven_media_runtime(
    root: Path, *, require_vendor: bool = True, support_only: bool = False
) -> None:
    from .final_root_media import _require_proven_media_runtime as implementation

    return implementation(
        sys.modules[__name__],
        root,
        require_vendor=require_vendor,
        support_only=support_only,
    )


def _snapshot_native_media(root: Path) -> dict[str, str]:
    from .final_root_media import _snapshot_native_media as implementation

    return implementation(sys.modules[__name__], root)


def _install_vendor_bundle(root: Path, bundle: VendorBundle) -> None:
    from .final_root_media import _install_vendor_bundle as implementation

    return implementation(sys.modules[__name__], root, bundle)


def _record_source_media(
    root: Path, bundle: VendorBundle, *, support_only: bool = False
) -> dict[str, object]:
    from .final_root_media import _record_source_media as implementation

    return implementation(
        sys.modules[__name__], root, bundle, support_only=support_only
    )


def _global_glibc_library(root: Path, name: str) -> Path:
    from .final_root_media import _global_glibc_library as implementation

    return implementation(sys.modules[__name__], root, name)


def _require_native_media_unchanged(
    root: Path, expected: dict[str, str]
) -> None:
    from .final_root_media import _require_native_media_unchanged as implementation

    return implementation(sys.modules[__name__], root, expected)


def _move_required(root: Path, source: str, destination: str) -> None:
    from .final_root_media import _move_required as implementation

    return implementation(sys.modules[__name__], root, source, destination)


def _configure_media_first_init(
    root: Path, *, support_only: bool = False
) -> None:
    from .final_root_media import _configure_media_first_init as implementation

    return implementation(sys.modules[__name__], root, support_only=support_only)


def _lock_universal_root_account(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise FinalRootError("universal root shadow file is invalid") from exc
    roots = [index for index, line in enumerate(lines) if line.startswith("root:")]
    if len(roots) != 1:
        raise FinalRootError("universal root lacks one root account")
    fields = lines[roots[0]].split(":")
    if len(fields) != 9:
        raise FinalRootError("universal root account has invalid framing")
    fields[1] = "!"
    lines[roots[0]] = ":".join(fields)
    _write_private(path, ("\n".join(lines) + "\n").encode("utf-8"))


def _disable_universal_network_services(root: Path) -> None:
    archive = root / "usr/share/thingino-provisioning/init"
    archive.mkdir(mode=0o755, parents=True, exist_ok=True)
    for relative in UNIVERSAL_DISABLED_INIT:
        source = root / relative
        if not source.is_file() or source.is_symlink():
            raise FinalRootError(f"universal root lacks init input: {relative}")
        destination = archive / source.name
        if destination.exists():
            raise FinalRootError("universal init archive is not empty")
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
        source.chmod(0o644)


def _sanitize_universal_secret_paths(root: Path) -> None:
    for relative in (
        "etc/wpa_supplicant.conf",
        "etc/thingino-api.key",
        "etc/dropbear/dropbear_ed25519_host_key",
        "etc/dcs6100-personal-image.json",
        "root/.ssh/authorized_keys",
    ):
        path = root / relative
        if path.is_symlink():
            raise FinalRootError(f"universal secret path is a symlink: {relative}")
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    _lock_universal_root_account(root / "etc/shadow")
    _write_private(root / "etc/hostname", (UNIVERSAL_HOSTNAME + "\n").encode("ascii"))


def _validate_universal_tree(root: Path, *, allow_support_base: bool = False) -> None:
    marker = root / UNIVERSAL_MARKER
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalRootError("universal root marker is invalid") from exc
    expected = {
        "artifact_scope": "model-universal",
        "contains_device_secrets": False,
        "provisioning_required": True,
        "schema_version": 1,
        "target": "DCS-6100LHV2-A1",
    }
    if document != expected:
        raise FinalRootError("universal root marker changed")
    closure_path = root / "etc/dlink-media-closure.private.json"
    if closure_path.is_file():
        closure = json.loads(closure_path.read_bytes())
        if (
            isinstance(closure, dict)
            and closure.get("runtime") == "source-built-camera-support-v1"
            and not allow_support_base
        ):
            raise FinalRootError("support-only build intermediate is not an installable image")
    for relative in (
        "etc/wpa_supplicant.conf",
        "etc/thingino-api.key",
        "etc/dropbear/dropbear_ed25519_host_key",
        "etc/dcs6100-personal-image.json",
        "root/.ssh/authorized_keys",
    ):
        if (root / relative).exists() or (root / relative).is_symlink():
            raise FinalRootError(f"universal root retained private runtime path: {relative}")
    for relative in UNIVERSAL_DISABLED_INIT:
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o111:
            raise FinalRootError(f"universal network service is executable: {relative}")
    for relative in ("etc/init.d/S96rwd", "etc/init.d/S96raptor"):
        service = root / relative
        if service.is_symlink() or (service.exists() and (
            not service.is_file() or service.stat().st_mode & 0o111
        )):
            raise FinalRootError("universal Raptor service is executable")
    if (root / "etc/init.d/S96raptor").exists():
        from .raptor_provisioning import validate_universal_runtime

        validate_universal_runtime(root)
    shadow = (root / "etc/shadow").read_text(encoding="utf-8")
    root_lines = [line for line in shadow.splitlines() if line.startswith("root:")]
    if len(root_lines) != 1 or root_lines[0].split(":", 2)[1] not in {"!", "*"}:
        raise FinalRootError("universal root account is not locked")
    if (root / "etc/hostname").read_bytes() != (UNIVERSAL_HOSTNAME + "\n").encode("ascii"):
        raise FinalRootError("universal root hostname changed")
    archive = root / "usr/share/thingino-provisioning/init"
    if {entry.name for entry in archive.iterdir()} != {
        Path(path).name for path in UNIVERSAL_DISABLED_INIT
    }:
        raise FinalRootError("universal root init archive changed")


def _install_universal_helpers(root: Path, *, support_only: bool) -> None:
    """Install probes at the layer that owns the corresponding media stack."""

    helper_names = ["thingino-health"]
    if not support_only:
        helper_names.extend(
            ("dlink-media-verify", "dlink-runtime-snapshot", "dlink-application-verify")
        )
    for name in helper_names:
        _write_executable(root / f"usr/sbin/{name}", (TEMPLATE_ROOT / name).read_bytes())
def prepare_universal_final_root(
    *,
    base_rootfs: bytes,
    vendor_bundle: VendorBundle,
    media_closure: object | None,
    output_dir: Path,
    mksquashfs: Path,
    unsquashfs: Path,
    output_size: int | None = None,
    support_only: bool = False,
) -> dict[str, object]:
    """Build the reusable support base consumed by full Raptor composition."""

    payload_limit = SYSTEM_FLASH_SPAN
    if media_closure is not None:
        raise FinalRootError(
            "legacy media closure input is retired; use the Raptor support build"
        )
    if not support_only:
        raise FinalRootError(
            "only the Raptor support base is exportable; compose full Raptor separately"
        )
    if output_size is not None and not 0 < output_size <= payload_limit:
        raise FinalRootError("requested universal-root output size is invalid")
    validate_squashfs(base_rootfs)
    if output_dir.exists() or output_dir.is_symlink():
        raise FinalRootError("refusing to reuse a universal final-root output directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        root = work / "root"
        base_path = work / "base.squashfs"
        output = work / UNIVERSAL_OUTPUT_NAME
        base_path.write_bytes(base_rootfs)
        _extract_base_root(unsquashfs=unsquashfs, source=base_path, destination=root)
        _remove_unsafe_paths(root)
        _require_proven_media_runtime(
            root, require_vendor=False, support_only=True
        )
        _install_vendor_bundle(root, vendor_bundle)
        _require_proven_media_runtime(root, support_only=True)
        _configure_media_first_init(root, support_only=True)
        from .raptor_full_root import remove_retired_runtime

        remove_retired_runtime(root)
        media_provenance = _record_source_media(
            root, vendor_bundle, support_only=True
        )
        _require_proven_media_runtime(root, support_only=True)
        _configure_no_default_route(root)
        _sanitize_universal_secret_paths(root)
        _disable_universal_network_services(root)

        def patch_onvif(document: dict[str, object]) -> None:
            server = document.get("server")
            if not isinstance(server, dict):
                raise FinalRootError("universal ONVIF lacks server configuration")
            server["username"] = ""
            server["password"] = ""

        def patch_thingino(document: dict[str, object]) -> None:
            gpio = document.get("gpio")
            if not isinstance(gpio, dict):
                raise FinalRootError("universal Thingino lacks GPIO configuration")
            gpio["ircut"] = "50 49"
            document["enable_updates"] = False
            document["hostname"] = UNIVERSAL_HOSTNAME
            document["control"] = {"enabled": False}

        _patch_json(root / "etc/onvif.json", patch_onvif)
        _patch_json(root / "etc/thingino.json", patch_thingino)
        _write_private(
            root / "etc/dlink-media-closure.private.json",
            (json.dumps(media_provenance, indent=2, sort_keys=True) + "\n").encode(),
        )
        marker = {
            "artifact_scope": "model-universal",
            "contains_device_secrets": False,
            "provisioning_required": True,
            "schema_version": 1,
            "target": "DCS-6100LHV2-A1",
        }
        _write_private(
            root / UNIVERSAL_MARKER,
            (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "ascii"
            ),
        )
        _install_universal_helpers(root, support_only=True)
        _validate_universal_tree(root, allow_support_base=True)
        _build_final_root_squashfs(
            mksquashfs=mksquashfs,
            root=root,
            output=output,
            label="universal Raptor support-root build",
        )
        raw = output.read_bytes()
        validate_squashfs(raw)
        if len(raw) > payload_limit:
            raise FinalRootError("universal final-root exceeds the fixed mtd3 system region")
        if output_size is not None:
            if output_size < len(raw):
                raise FinalRootError(
                    "requested universal-root size is smaller than SquashFS"
                )
            if output_size != len(raw):
                raw += b"\0" * (output_size - len(raw))
                output.write_bytes(raw)
            validate_squashfs(raw, partition_limit=payload_limit)
        audit_root = work / "audit-root"
        _extract_base_root(unsquashfs=unsquashfs, source=output, destination=audit_root)
        _validate_universal_tree(audit_root, allow_support_base=True)
        manifest = {
            "artifact_scope": "model-universal",
            "contains_device_secrets": False,
            "provisioning_required": True,
            "schema_version": 1,
            "source_sha256": hashlib.sha256(base_rootfs).hexdigest(),
            "composition_required": True,
            "system": {
                "filename": UNIVERSAL_OUTPUT_NAME,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            },
            "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
            "vendor_bundle_sha256": vendor_bundle.bundle_sha256,
            "media_profile": "source-built-camera-support-v1",
            "media_closure_sha256": None,
        }
        atomic_write(
            work / "final-root.universal.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        base_path.unlink()
        shutil.rmtree(root)
        shutil.rmtree(audit_root)
        os.chmod(work, 0o700)
        for path in work.iterdir():
            path.chmod(0o600)
        os.replace(work, output_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def prepare_final_root(*args: object, **kwargs: object) -> dict[str, object]:
    """Legacy personalized image entry point retained only as a migration guard."""
    raise FinalRootError(
        "personalized final-root is retired; use the full Raptor composition"
    )


def prepare_from_private_directory(*args: object, **kwargs: object) -> dict[str, object]:
    """Legacy personalized image entry point retained only as a migration guard."""
    raise FinalRootError(
        "personalized final-root is retired; use the full Raptor composition"
    )
