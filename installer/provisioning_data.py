"""Deterministic per-camera JFFS2 overlay kept outside universal firmware."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .artifacts import SOURCE_DATE_EPOCH, validate_squashfs
from .final_root import (
    FinalRootError,
    UNIVERSAL_DISABLED_INIT,
    _configure_control,
    _configure_ha_live_image,
    _configure_key_only_ssh,
    _extract_base_root,
    _materialize_wpa_runtime_policy,
    _patch_json,
    _run,
    _validate_universal_tree,
    _write_private,
)
from .mtd3_split import DATA_FLASH_SPAN
from .private_config import derive_rtsp_viewer_credential
from . import raptor_provisioning
from .sd_package import atomic_write


PROVISIONING_DATA_KIND = "dcs6100-private-provisioning-data-v1"
PROVISIONING_DATA_NAME = "provisioning.data.jffs2"
PROVISIONING_RECEIPT = "etc/dcs6100-provisioning.json"
JFFS2_MAGIC = b"\x85\x19"


class ProvisioningDataError(ValueError):
    """The camera-private overlay cannot be safely materialized."""


@dataclass(frozen=True, slots=True)
class ProvisioningDataImage:
    raw: bytes = field(repr=False)
    receipt: dict[str, object]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


PROVISIONING_ENABLED_INIT = (raptor_provisioning.SERVICE,)

_RUNTIME_PATHS = (
    "etc/dlink-media-closure.private.json",
    "etc/dcs6100-personal-image.json",
    "etc/dropbear/dropbear_ed25519_host_key",
    "etc/hostname",
    "etc/onvif.json",
    "etc/shadow",
    "etc/thingino-api.key",
    "etc/thingino.json",
    "etc/wpa_supplicant.conf",
    "root/.ssh/authorized_keys",
    *UNIVERSAL_DISABLED_INIT,
    *PROVISIONING_ENABLED_INIT,
)


def _runtime_paths(root: Path) -> tuple[str, ...]:
    if not (root / raptor_provisioning.SERVICE).is_file():
        raise ProvisioningDataError("universal root lacks the Raptor provisioning service")
    return _RUNTIME_PATHS + raptor_provisioning.CONFIGS


def _copy_runtime_path(source_root: Path, overlay_root: Path, relative: str) -> None:
    source = source_root / relative
    destination = overlay_root / relative
    if source.is_symlink() or not source.is_file():
        raise ProvisioningDataError(f"provisioned runtime path is invalid: {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(stat.S_IMODE(source.stat(follow_symlinks=False).st_mode))


def _normalize_times(root: Path) -> None:
    timestamp = (SOURCE_DATE_EPOCH, SOURCE_DATE_EPOCH)
    for path in sorted(root.rglob("*"), key=lambda item: str(item), reverse=True):
        if path.is_symlink():
            raise ProvisioningDataError("provisioning overlay contains a symlink")
        os.utime(path, timestamp, follow_symlinks=False)
    os.utime(root, timestamp, follow_symlinks=False)


def _run_mkfs(*, tool: Path, root: Path, output: Path) -> bytes:
    _run(
        [
            str(tool),
            "--little-endian",
            "--eraseblock=0x8000",
            "--pagesize=0x100",
            f"--pad={DATA_FLASH_SPAN}",
            "--squash",
            "--faketime",
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        "private provisioning JFFS2 build",
    )
    try:
        raw = output.read_bytes()
    except OSError as exc:
        raise ProvisioningDataError("cannot read provisioning JFFS2 output") from exc
    if len(raw) != DATA_FLASH_SPAN or raw[:2] != JFFS2_MAGIC:
        raise ProvisioningDataError(
            "provisioning output is not an exact-span little-endian JFFS2 image"
        )
    return raw


def _patch_runtime(
    root: Path,
    *,
    wpa_config: bytes,
    credential: bytes,
    api_key: bytes,
    authorized_key: bytes,
    dropbear_host_key: bytes,
    station_hostname: str,
    camera_identity_sha256: str,
    universal_firmware_sha256: str,
    recovery_session_sha256: str,
    provisioning_id: str,
    credential_set_id: str,
) -> dict[str, object]:
    archive = root / "usr/share/thingino-provisioning/init"
    for relative in UNIVERSAL_DISABLED_INIT:
        archived = archive / Path(relative).name
        target = root / relative
        if archived.is_symlink() or not archived.is_file():
            raise ProvisioningDataError(f"universal init archive is invalid: {relative}")
        shutil.copyfile(archived, target)
        target.chmod(0o755)
    for relative in PROVISIONING_ENABLED_INIT:
        target = root / relative
        if target.is_symlink():
            raise ProvisioningDataError(
                f"universal provisioned init is invalid: {relative}"
            )
        if not target.exists():
            continue
        if not target.is_file():
            raise ProvisioningDataError(
                f"universal provisioned init is invalid: {relative}"
            )
        target.chmod(0o755)
    _write_private(
        root / "etc/wpa_supplicant.conf",
        _materialize_wpa_runtime_policy(wpa_config),
    )
    _configure_key_only_ssh(root, authorized_key, dropbear_host_key, credential)
    _write_private(root / "etc/hostname", (station_hostname + "\n").encode("ascii"))
    password = credential.decode("ascii").strip()
    rtsp_password = derive_rtsp_viewer_credential(credential).decode("ascii").strip()

    def patch_onvif(document: dict[str, object]) -> None:
        server = document.get("server")
        if not isinstance(server, dict):
            raise FinalRootError("provisioning ONVIF lacks server configuration")
        server["username"] = "root"
        server["password"] = password
        document["adv_enable_media2"] = True

    def patch_thingino(document: dict[str, object]) -> None:
        gpio = document.get("gpio")
        daynight = document.get("daynight")
        if not isinstance(gpio, dict) or not isinstance(daynight, dict):
            raise FinalRootError("provisioning Thingino structure changed")
        controls = daynight.get("controls")
        if not isinstance(controls, dict):
            raise FinalRootError("provisioning day/night controls changed")
        gpio["ircut"] = "50 49"
        controls["color"] = True
        document["enable_updates"] = False
        document["hostname"] = station_hostname
        document["control"] = {
            "enabled": True,
            "listen": "127.0.0.1",
            "port": 1998,
        }
        _configure_control(document, credential)
        _configure_ha_live_image(document)

    _patch_json(root / "etc/onvif.json", patch_onvif)
    _patch_json(root / "etc/thingino.json", patch_thingino)
    _write_private(root / "etc/thingino-api.key", api_key)

    if not (root / raptor_provisioning.SERVICE).is_file():
        raise ProvisioningDataError("universal root lacks the Raptor provisioning service")
    raptor_provisioning.provision_runtime(root, rtsp_password)
    _write_private(
        root / "etc/dcs6100-personal-image.json",
        (
            json.dumps(
                {
                    "artifact_scope": "per-camera-provisioning",
                    "camera_identity_sha256": camera_identity_sha256,
                    "schema_version": 2,
                    "target": "DCS-6100LHV2-A1",
                    "universal_firmware_sha256": universal_firmware_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii"),
    )
    receipt = {
        "artifact_kind": PROVISIONING_DATA_KIND,
        "camera_identity_sha256": camera_identity_sha256,
        "credential_set_id": credential_set_id,
        "provisioning_id": provisioning_id,
        "recovery_session_sha256": recovery_session_sha256,
        "schema_version": 1,
        "state": "committed",
        "target": "DCS-6100LHV2-A1",
        "universal_firmware_sha256": universal_firmware_sha256,
    }
    _write_private(
        root / PROVISIONING_RECEIPT,
        (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "ascii"
        ),
    )
    return receipt


def build_provisioning_data_image(
    *,
    universal_rootfs: bytes,
    wpa_config: bytes,
    credential: bytes,
    api_key: bytes,
    authorized_key: bytes,
    dropbear_host_key: bytes,
    station_hostname: str,
    camera_identity_sha256: str,
    universal_firmware_sha256: str,
    recovery_session_sha256: str,
    provisioning_id: str,
    credential_set_id: str,
    unsquashfs: Path,
    mkfs_jffs2: Path,
    output_path: Path,
) -> ProvisioningDataImage:
    """Materialize and reproduce one complete OverlayFS upper JFFS2 image."""

    validate_squashfs(universal_rootfs)
    if output_path.exists() or output_path.is_symlink():
        raise ProvisioningDataError("refusing to overwrite provisioning data image")
    for label, value in {
        "camera identity": camera_identity_sha256,
        "universal firmware identity": universal_firmware_sha256,
        "recovery session identity": recovery_session_sha256,
        "provisioning identity": provisioning_id,
    }.items():
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ProvisioningDataError(f"{label} is not a lowercase SHA-256")
    parent = output_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=parent))
    work.chmod(0o700)
    try:
        universal_path = work / "universal.squashfs"
        universal_path.write_bytes(universal_rootfs)
        extracted = work / "universal-root"
        _extract_base_root(
            unsquashfs=unsquashfs,
            source=universal_path,
            destination=extracted,
        )
        _validate_universal_tree(extracted)
        receipt = _patch_runtime(
            extracted,
            wpa_config=wpa_config,
            credential=credential,
            api_key=api_key,
            authorized_key=authorized_key,
            dropbear_host_key=dropbear_host_key,
            station_hostname=station_hostname,
            camera_identity_sha256=camera_identity_sha256,
            universal_firmware_sha256=universal_firmware_sha256,
            recovery_session_sha256=recovery_session_sha256,
            provisioning_id=provisioning_id,
            credential_set_id=credential_set_id,
        )
        overlay = work / "overlay"
        # The pinned Thingino 3.10 overlayfs implementation mounts the data
        # partition itself as upperdir=/overlay.  Its files must therefore be
        # rooted directly in the JFFS2 image, not under a modern
        # upperdir/workdir pair.
        upper = overlay
        upper.mkdir(parents=True, mode=0o755)
        for relative in _runtime_paths(extracted):
            if (
                relative in PROVISIONING_ENABLED_INIT
                and not (extracted / relative).exists()
            ):
                continue
            _copy_runtime_path(extracted, upper, relative)
        _copy_runtime_path(extracted, upper, PROVISIONING_RECEIPT)
        _normalize_times(overlay)
        first = _run_mkfs(tool=mkfs_jffs2, root=overlay, output=work / "first.jffs2")
        second = _run_mkfs(tool=mkfs_jffs2, root=overlay, output=work / "second.jffs2")
        if first != second:
            raise ProvisioningDataError("provisioning JFFS2 builds are not byte-identical")
        atomic_write(output_path, first, mode=0o600)
        return ProvisioningDataImage(first, receipt)
    except (FinalRootError, OSError) as exc:
        if isinstance(exc, ProvisioningDataError):
            raise
        raise ProvisioningDataError(str(exc)) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
