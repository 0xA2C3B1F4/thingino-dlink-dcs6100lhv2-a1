"""Private per-camera provisioning sidecar kept outside universal firmware."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .final_bundle import (
    _public_key_id,
    _public_key_id_from_private,
    _sign,
    _verify,
)
from .layout import TARGET
from .mtd3_split import DATA_FLASH_SPAN
from .private_config import PrivateConfigError, load_private_config_for_session
from .recovery_ap.host import load_host_session
from .provisioning_data import (
    ProvisioningDataImage,
    build_provisioning_data_image,
)
from .sd_package import atomic_write


PROVISIONING_KIND = "dcs6100-private-provisioning-sidecar-v1"
PROVISIONING_MEMBERS = {
    "manifest.json",
    "manifest.ed25519",
    "payload/authorized_keys",
    "payload/dropbear_ed25519_host_key",
    "payload/installer.credential",
    "payload/webui-api.key",
    "payload/wpa_supplicant.conf",
}
PAYLOAD_NAMES = tuple(sorted(PROVISIONING_MEMBERS - {"manifest.json", "manifest.ed25519"}))
MAX_PROVISIONING_SIZE = 128 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")


class ProvisioningError(ValueError):
    """A private provisioning sidecar is malformed or bound elsewhere."""


@dataclass(frozen=True, slots=True)
class ValidatedProvisioning:
    raw: bytes = field(repr=False)
    manifest: dict[str, object]
    payloads: dict[str, bytes] = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def recovery_session_sha256(self) -> str:
        return str(self.manifest["recovery_session_sha256"])

    @property
    def provisioning_data_sha256(self) -> str:
        return str(self.manifest["provisioning_data"]["sha256"])

    @property
    def provisioning_data_size(self) -> int:
        return int(self.manifest["provisioning_data"]["size"])

    @property
    def provisioning_id(self) -> str:
        return str(self.manifest["provisioning_id"])


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ProvisioningError(f"{label} is not a lowercase SHA-256")
    return value


def _read_private(path: Path, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProvisioningError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > limit
            or before.st_mode & 0o077
        ):
            raise ProvisioningError(f"{label} violates its private file policy")
        raw = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or len(raw) != before.st_size
    ):
        raise ProvisioningError(f"{label} changed while being read")
    return raw


def read_private_provisioning_data(path: Path) -> bytes:
    raw = _read_private(path, "provisioning JFFS2 data image", DATA_FLASH_SPAN)
    if len(raw) != DATA_FLASH_SPAN or raw[:2] != b"\x85\x19":
        raise ProvisioningError(
            "provisioning data is not an exact-span little-endian JFFS2 image"
        )
    return raw


def recovery_session_identity(session_dir: Path) -> str:
    """Derive a non-secret binding from one already validated recovery session."""

    session = load_host_session(session_dir)
    identity_public = _read_private(
        session.identity.with_suffix(".pub"), "recovery public identity", 4096
    )
    known_hosts = _read_private(session.known_hosts, "recovery known-host binding", 4096)
    digest = hashlib.sha256()
    digest.update(b"thingino-recovery-session-public-identity-v1\0")
    for value in (
        identity_public,
        known_hosts,
        session.station_mdns_name.encode("ascii"),
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _zip_member(path: str, payload: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    return info, payload


def create_provisioning_sidecar(
    *,
    private_config_dir: Path,
    session_dir: Path,
    camera_identity_sha256: str,
    universal_firmware_sha256: str,
    universal_system_rootfs: bytes,
    signing_key: Path,
    unsquashfs: Path,
    mkfs_jffs2: Path,
    output_path: Path,
    data_output_path: Path,
) -> tuple[ValidatedProvisioning, ProvisioningDataImage]:
    """Create private configuration bytes without rebuilding universal firmware."""

    camera_identity_sha256 = _require_digest(
        camera_identity_sha256, "camera identity"
    )
    universal_firmware_sha256 = _require_digest(
        universal_firmware_sha256, "universal firmware identity"
    )
    if output_path.exists() or output_path.is_symlink():
        raise ProvisioningError("refusing to overwrite provisioning sidecar")
    if data_output_path.exists() or data_output_path.is_symlink():
        raise ProvisioningError("refusing to overwrite provisioning data image")
    try:
        private = load_private_config_for_session(
            output_dir=private_config_dir,
            session_dir=session_dir,
        )
    except PrivateConfigError as exc:
        raise ProvisioningError(str(exc)) from exc
    session = load_host_session(session_dir)
    dropbear_host_key = _read_private(
        session_dir / "media/RECOVERY/HOST.KEY",
        "recovery Dropbear host key",
        16 * 1024,
    )
    payloads = {
        "payload/authorized_keys": private.authorized_key,
        "payload/dropbear_ed25519_host_key": dropbear_host_key,
        "payload/installer.credential": private.credential,
        "payload/webui-api.key": private.api_key,
        "payload/wpa_supplicant.conf": private.wpa_config,
    }
    session_sha256 = recovery_session_identity(session_dir)
    provisioning_identity = hashlib.sha256()
    provisioning_identity.update(b"thingino-private-provisioning-id-v1\0")
    for value in (
        camera_identity_sha256,
        universal_firmware_sha256,
        session_sha256,
        private.credential_set_id,
    ):
        encoded = value.encode("ascii")
        provisioning_identity.update(len(encoded).to_bytes(8, "big"))
        provisioning_identity.update(encoded)
    for name in sorted(payloads):
        provisioning_identity.update(name.encode("ascii") + b"\0")
        provisioning_identity.update(hashlib.sha256(payloads[name]).digest())
    provisioning_id = provisioning_identity.hexdigest()
    try:
        data_image = build_provisioning_data_image(
            universal_rootfs=universal_system_rootfs,
            wpa_config=private.wpa_config,
            credential=private.credential,
            api_key=private.api_key,
            authorized_key=private.authorized_key,
            dropbear_host_key=dropbear_host_key,
            station_hostname=session.station_mdns_name.removesuffix(".local"),
            camera_identity_sha256=camera_identity_sha256,
            universal_firmware_sha256=universal_firmware_sha256,
            recovery_session_sha256=session_sha256,
            provisioning_id=provisioning_id,
            credential_set_id=private.credential_set_id,
            unsquashfs=unsquashfs,
            mkfs_jffs2=mkfs_jffs2,
            output_path=data_output_path,
        )
    except ValueError as exc:
        raise ProvisioningError(str(exc)) from exc
    document = {
        "artifact_kind": PROVISIONING_KIND,
        "camera_identity_sha256": camera_identity_sha256,
        "contains_secrets": True,
        "credential_set_id": private.credential_set_id,
        "delivery": {
            "firmware_member": False,
            "kind": "pre-activation-private-jffs2-overlay",
            "one_camera": True,
        },
        "payloads": {
            name: {
                "sha256": hashlib.sha256(payloads[name]).hexdigest(),
                "size": len(payloads[name]),
            }
            for name in PAYLOAD_NAMES
        },
        "provisioning_data": {
            "filesystem": "jffs2",
            "sha256": data_image.sha256,
            "size": len(data_image.raw),
        },
        "provisioning_id": provisioning_id,
        "recovery_session_sha256": session_sha256,
        "schema_version": 1,
        "signing_key_sha256": _public_key_id_from_private(signing_key),
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
            "nor_size": TARGET.nor_size,
        },
        "universal_firmware_sha256": universal_firmware_sha256,
    }
    manifest = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    signature = _sign(manifest, signing_key)
    members = {**payloads, "manifest.json": manifest, "manifest.ed25519": signature}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        for name in sorted(members):
            info, payload = _zip_member(name, members[name])
            archive.writestr(info, payload)
    raw = output.getvalue()
    if len(raw) > MAX_PROVISIONING_SIZE:
        raise ProvisioningError("provisioning sidecar exceeds its size limit")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output_path, raw, mode=0o600)
    return ValidatedProvisioning(raw, document, payloads), data_image


def require_provisioning_signing_key(
    provisioning: ValidatedProvisioning, signing_key: Path
) -> None:
    if provisioning.manifest.get("signing_key_sha256") != _public_key_id_from_private(
        signing_key
    ):
        raise ProvisioningError(
            "authorization signing key differs from provisioning signer"
        )


def validate_provisioning_sidecar(
    path: Path,
    *,
    public_key: Path,
    expected_camera_identity_sha256: str,
    expected_universal_firmware_sha256: str,
    expected_recovery_session_sha256: str,
) -> ValidatedProvisioning:
    raw = _read_private(path, "provisioning sidecar", MAX_PROVISIONING_SIZE)
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != PROVISIONING_MEMBERS:
                raise ProvisioningError("provisioning member allowlist changed")
            members: dict[str, bytes] = {}
            for info in infos:
                if info.compress_type != zipfile.ZIP_STORED:
                    raise ProvisioningError("provisioning members must be stored")
                if info.file_size > MAX_PROVISIONING_SIZE:
                    raise ProvisioningError("provisioning member exceeds its size limit")
                members[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ProvisioningError("provisioning sidecar ZIP is invalid") from exc
    canonical_zip = io.BytesIO()
    with zipfile.ZipFile(canonical_zip, "w", allowZip64=False) as archive:
        for name in sorted(members):
            info, payload = _zip_member(name, members[name])
            archive.writestr(info, payload)
    if canonical_zip.getvalue() != raw:
        raise ProvisioningError("provisioning ZIP encoding is not canonical")
    manifest_raw = members["manifest.json"]
    try:
        _verify(manifest_raw, members["manifest.ed25519"], public_key)
    except Exception as exc:
        raise ProvisioningError("provisioning signature is invalid") from exc
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvisioningError("provisioning manifest is invalid") from exc
    canonical = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if canonical != manifest_raw or not isinstance(manifest, dict):
        raise ProvisioningError("provisioning manifest is not canonical")
    expected_keys = {
        "artifact_kind",
        "camera_identity_sha256",
        "contains_secrets",
        "credential_set_id",
        "delivery",
        "payloads",
        "provisioning_data",
        "provisioning_id",
        "recovery_session_sha256",
        "schema_version",
        "signing_key_sha256",
        "target",
        "universal_firmware_sha256",
    }
    if set(manifest) != expected_keys:
        raise ProvisioningError("provisioning manifest fields changed")
    if manifest.get("schema_version") != 1 or manifest.get("artifact_kind") != PROVISIONING_KIND:
        raise ProvisioningError("provisioning schema changed")
    if manifest.get("contains_secrets") is not True or manifest.get("delivery") != {
        "firmware_member": False,
        "kind": "pre-activation-private-jffs2-overlay",
        "one_camera": True,
    }:
        raise ProvisioningError("provisioning privacy or delivery policy changed")
    if manifest.get("target") != {
        "hardware_revision": TARGET.hardware_revision,
        "model": TARGET.model,
        "nor_size": TARGET.nor_size,
    }:
        raise ProvisioningError("provisioning targets the wrong camera")
    if manifest.get("signing_key_sha256") != _public_key_id(public_key):
        raise ProvisioningError("provisioning signer differs")
    expected = {
        "camera_identity_sha256": expected_camera_identity_sha256,
        "universal_firmware_sha256": expected_universal_firmware_sha256,
        "recovery_session_sha256": expected_recovery_session_sha256,
    }
    for field, value in expected.items():
        _require_digest(value, field)
        if manifest.get(field) != value:
            raise ProvisioningError(f"provisioning {field} differs")
    provisioning_data = manifest.get("provisioning_data")
    if (
        not isinstance(provisioning_data, dict)
        or set(provisioning_data) != {"filesystem", "sha256", "size"}
        or provisioning_data.get("filesystem") != "jffs2"
        or provisioning_data.get("size") != DATA_FLASH_SPAN
    ):
        raise ProvisioningError("provisioning data image contract changed")
    _require_digest(provisioning_data.get("sha256"), "provisioning data identity")
    _require_digest(manifest.get("provisioning_id"), "provisioning ID")
    payload_manifest = manifest.get("payloads")
    if not isinstance(payload_manifest, dict) or set(payload_manifest) != set(PAYLOAD_NAMES):
        raise ProvisioningError("provisioning payload manifest changed")
    payloads = {name: members[name] for name in PAYLOAD_NAMES}
    for name, payload in payloads.items():
        identity = payload_manifest[name]
        if identity != {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }:
            raise ProvisioningError(f"provisioning payload identity differs: {name}")
    return ValidatedProvisioning(raw, manifest, payloads)
