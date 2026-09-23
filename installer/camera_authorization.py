"""Signed one-camera authorization for one model-universal firmware artifact."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .final_bundle import (
    ValidatedFinalBundle,
    _public_key_id,
    _public_key_id_from_private,
    _sign,
    _verify,
)
from .layout import TARGET
from .install_policy import universal_physical_write_policy
from .mtd3_split import DATA_FLASH_SPAN
from .recovery_gate import RecoveryDecision
from .sd_package import atomic_write


AUTHORIZATION_MANIFEST = "authorization.json"
AUTHORIZATION_SIGNATURE = "authorization.ed25519"
AUTHORIZATION_BINARY = "authorization.bin"
AUTHORIZATION_KIND = "dcs6100-per-camera-install-authorization-v1"
AUTHORIZATION_BINARY_MAGIC = b"DCS6AUTHV1\0\0\0\0\0\0"
AUTHORIZATION_BINARY_SIZE = 256
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_DOCUMENT = 16 * 1024


class CameraAuthorizationError(ValueError):
    """A camera authorization is malformed, unsigned, or bound elsewhere."""


@dataclass(frozen=True, slots=True)
class ValidatedCameraAuthorization:
    raw_manifest: bytes
    signature: bytes
    binary: bytes
    document: dict[str, object]

    @property
    def authorization_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(b"thingino-camera-authorization-v1\0")
        digest.update(len(self.raw_manifest).to_bytes(8, "big"))
        digest.update(self.raw_manifest)
        digest.update(len(self.signature).to_bytes(8, "big"))
        digest.update(self.signature)
        digest.update(len(self.binary).to_bytes(8, "big"))
        digest.update(self.binary)
        return digest.hexdigest()

    @property
    def camera_identity_sha256(self) -> str:
        return str(self.document["camera_identity_sha256"])

    @property
    def universal_firmware_sha256(self) -> str:
        return str(self.document["universal_firmware_sha256"])


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise CameraAuthorizationError(f"{label} is not a lowercase SHA-256")
    return value


def _authorization_binary(
    document: dict[str, object], authorization_key_sha256: str
) -> bytes:
    authorization_key_sha256 = _require_digest(
        authorization_key_sha256, "camera authorization key"
    )
    action_ids = {"initialize": 0, "preserve": 1, "factory-reset": 2}
    action = document.get("data_action")
    if action not in action_ids:
        raise CameraAuthorizationError("camera authorization data action is invalid")
    provisioning_size = document.get("provisioning_data_size")
    if (
        isinstance(provisioning_size, bool)
        or not isinstance(provisioning_size, int)
        or provisioning_size != DATA_FLASH_SPAN
    ):
        raise CameraAuthorizationError(
            "camera authorization provisioning size is invalid"
        )
    header = struct.pack(
        ">16sII32s32s32s32sII32s",
        AUTHORIZATION_BINARY_MAGIC,
        1,
        AUTHORIZATION_BINARY_SIZE,
        bytes.fromhex(_require_digest(document.get("camera_identity_sha256"), "camera identity")),
        bytes.fromhex(_require_digest(document.get("universal_firmware_sha256"), "universal firmware identity")),
        bytes.fromhex(_require_digest(document.get("universal_stage2_sha256"), "universal stage-2 identity")),
        bytes.fromhex(_require_digest(document.get("provisioning_data_sha256"), "provisioning data identity")),
        provisioning_size,
        action_ids[str(action)],
        b"\0" * 32,
    ).ljust(AUTHORIZATION_BINARY_SIZE, b"\0")
    digest_offset = struct.calcsize(">16sII32s32s32s32sII")
    digest = hmac.new(
        bytes.fromhex(authorization_key_sha256), header, hashlib.sha256
    ).digest()
    return header[:digest_offset] + digest + header[digest_offset + 32 :]


def _read_regular(path: Path, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CameraAuthorizationError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > limit
            or before.st_mode & 0o077
        ):
            raise CameraAuthorizationError(f"{label} violates its private file policy")
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
        raise CameraAuthorizationError(f"{label} changed while being read")
    return raw


def _document(
    *,
    recovery: RecoveryDecision,
    universal_firmware_sha256: str,
    universal_stage2_sha256: str,
    provisioning_sidecar_sha256: str,
    provisioning_data_sha256: str,
    provisioning_data_size: int,
    provisioning_id: str,
    recovery_session_sha256: str,
    data_action: str,
    signing_key_sha256: str,
) -> dict[str, object]:
    if not recovery.camera_identity_sha256:
        raise CameraAuthorizationError("recovery decision lacks a camera identity")
    if not recovery.camera_authorization_key_sha256:
        raise CameraAuthorizationError("recovery decision lacks an authorization key")
    if data_action not in {"initialize", "preserve"}:
        raise CameraAuthorizationError(
            "universal camera authorization requires initialize or preserve data action"
        )
    return {
        "artifact_kind": AUTHORIZATION_KIND,
        "camera_identity_sha256": _require_digest(
            recovery.camera_identity_sha256, "camera identity"
        ),
        "data_action": data_action,
        "provisioning_data_sha256": _require_digest(
            provisioning_data_sha256, "provisioning data identity"
        ),
        "provisioning_data_size": provisioning_data_size,
        "provisioning_id": _require_digest(provisioning_id, "provisioning ID"),
        "provisioning_sidecar_sha256": _require_digest(
            provisioning_sidecar_sha256, "provisioning sidecar identity"
        ),
        "recovery": {
            "functional_recovery_accepted": recovery.functional_recovery_accepted,
            "mode": recovery.mode,
            "original_complete_backup_accepted": (
                recovery.original_complete_backup_accepted
            ),
            "recovery_images": recovery.recovery_images,
        },
        "recovery_session_sha256": _require_digest(
            recovery_session_sha256, "recovery session identity"
        ),
        "schema_version": 1,
        "signing_key_sha256": _require_digest(
            signing_key_sha256, "authorization signing key identity"
        ),
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
            "nor_size": TARGET.nor_size,
        },
        "universal_firmware_sha256": _require_digest(
            universal_firmware_sha256, "universal firmware identity"
        ),
        "universal_stage2_sha256": _require_digest(
            universal_stage2_sha256, "universal stage-2 identity"
        ),
        "write_policy": universal_physical_write_policy(data_action),
    }


def create_camera_authorization(
    *,
    recovery: RecoveryDecision,
    universal_bundle: ValidatedFinalBundle,
    universal_stage2_sha256: str,
    provisioning_sidecar_sha256: str,
    provisioning_data_sha256: str,
    provisioning_data_size: int,
    provisioning_id: str,
    recovery_session_sha256: str,
    data_action: str,
    signing_key: Path,
    output_dir: Path,
) -> ValidatedCameraAuthorization:
    """Create a signed authorization without changing universal firmware bytes."""

    if universal_bundle.manifest.get("artifact_scope") != "model-universal":
        raise CameraAuthorizationError("authorization requires model-universal firmware")
    if universal_bundle.members.get("images/data.jffs2") != b"":
        raise CameraAuthorizationError("universal firmware contains per-camera data")
    if output_dir.exists() or output_dir.is_symlink():
        raise CameraAuthorizationError("refusing to overwrite camera authorization")
    document = _document(
        recovery=recovery,
        universal_firmware_sha256=universal_bundle.sha256,
        universal_stage2_sha256=universal_stage2_sha256,
        provisioning_sidecar_sha256=provisioning_sidecar_sha256,
        provisioning_data_sha256=provisioning_data_sha256,
        provisioning_data_size=provisioning_data_size,
        provisioning_id=provisioning_id,
        recovery_session_sha256=recovery_session_sha256,
        data_action=data_action,
        signing_key_sha256=_public_key_id_from_private(signing_key),
    )
    manifest = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    signature = _sign(manifest, signing_key)
    binary = _authorization_binary(
        document, recovery.camera_authorization_key_sha256
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    temporary.chmod(0o700)
    try:
        atomic_write(temporary / AUTHORIZATION_MANIFEST, manifest, mode=0o600)
        atomic_write(temporary / AUTHORIZATION_SIGNATURE, signature, mode=0o600)
        atomic_write(temporary / AUTHORIZATION_BINARY, binary, mode=0o600)
        os.replace(temporary, output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return ValidatedCameraAuthorization(manifest, signature, binary, document)


def validate_camera_authorization(
    authorization_dir: Path,
    *,
    public_key: Path,
    expected_camera_identity_sha256: str,
    expected_camera_authorization_key_sha256: str,
    expected_universal_firmware_sha256: str,
    expected_universal_stage2_sha256: str,
    expected_provisioning_sidecar_sha256: str,
    expected_provisioning_data_sha256: str,
    expected_provisioning_data_size: int,
    expected_provisioning_id: str,
    expected_recovery_session_sha256: str,
    expected_data_action: str,
) -> ValidatedCameraAuthorization:
    """Validate the exact camera/firmware/provisioning/session tuple."""

    if authorization_dir.is_symlink() or not authorization_dir.is_dir():
        raise CameraAuthorizationError("camera authorization directory is invalid")
    if authorization_dir.stat(follow_symlinks=False).st_mode & 0o077:
        raise CameraAuthorizationError("camera authorization directory is not private")
    if {entry.name for entry in authorization_dir.iterdir()} != {
        AUTHORIZATION_MANIFEST,
        AUTHORIZATION_SIGNATURE,
        AUTHORIZATION_BINARY,
    }:
        raise CameraAuthorizationError("camera authorization directory is not exact")
    manifest = _read_regular(
        authorization_dir / AUTHORIZATION_MANIFEST,
        "camera authorization manifest",
        _MAX_DOCUMENT,
    )
    signature = _read_regular(
        authorization_dir / AUTHORIZATION_SIGNATURE,
        "camera authorization signature",
        1024,
    )
    binary = _read_regular(
        authorization_dir / AUTHORIZATION_BINARY,
        "camera authorization binary",
        AUTHORIZATION_BINARY_SIZE,
    )
    if len(binary) != AUTHORIZATION_BINARY_SIZE:
        raise CameraAuthorizationError("camera authorization binary size differs")
    try:
        _verify(manifest, signature, public_key)
    except Exception as exc:
        raise CameraAuthorizationError("camera authorization signature is invalid") from exc
    try:
        document = json.loads(manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CameraAuthorizationError("camera authorization manifest is invalid") from exc
    canonical = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if canonical != manifest or not isinstance(document, dict):
        raise CameraAuthorizationError("camera authorization manifest is not canonical")
    expected_keys = {
        "artifact_kind",
        "camera_identity_sha256",
        "data_action",
        "provisioning_data_sha256",
        "provisioning_data_size",
        "provisioning_id",
        "provisioning_sidecar_sha256",
        "recovery",
        "recovery_session_sha256",
        "schema_version",
        "signing_key_sha256",
        "target",
        "universal_firmware_sha256",
        "universal_stage2_sha256",
        "write_policy",
    }
    if set(document) != expected_keys:
        raise CameraAuthorizationError("camera authorization fields changed")
    if document.get("schema_version") != 1 or document.get("artifact_kind") != AUTHORIZATION_KIND:
        raise CameraAuthorizationError("camera authorization schema changed")
    if document.get("target") != {
        "hardware_revision": TARGET.hardware_revision,
        "model": TARGET.model,
        "nor_size": TARGET.nor_size,
    }:
        raise CameraAuthorizationError("camera authorization targets the wrong device")
    if expected_data_action not in {"initialize", "preserve"}:
        raise CameraAuthorizationError(
            "universal camera authorization requires initialize or preserve data action"
        )
    if document.get("write_policy") != universal_physical_write_policy(
        expected_data_action
    ):
        raise CameraAuthorizationError("camera authorization write policy changed")
    if document.get("signing_key_sha256") != _public_key_id(public_key):
        raise CameraAuthorizationError("camera authorization signer differs")
    expected = {
        "camera_identity_sha256": expected_camera_identity_sha256,
        "universal_firmware_sha256": expected_universal_firmware_sha256,
        "universal_stage2_sha256": expected_universal_stage2_sha256,
        "provisioning_sidecar_sha256": expected_provisioning_sidecar_sha256,
        "provisioning_data_sha256": expected_provisioning_data_sha256,
        "provisioning_data_size": expected_provisioning_data_size,
        "provisioning_id": expected_provisioning_id,
        "recovery_session_sha256": expected_recovery_session_sha256,
        "data_action": expected_data_action,
    }
    for field, value in expected.items():
        if field not in {"data_action", "provisioning_data_size"}:
            _require_digest(value, field)
        if document.get(field) != value:
            raise CameraAuthorizationError(f"camera authorization {field} differs")
    recovery = document.get("recovery")
    if not isinstance(recovery, dict) or set(recovery) != {
        "functional_recovery_accepted",
        "mode",
        "original_complete_backup_accepted",
        "recovery_images",
    }:
        raise CameraAuthorizationError("camera authorization recovery class changed")
    expected_binary = _authorization_binary(
        document, expected_camera_authorization_key_sha256
    )
    if binary != expected_binary:
        raise CameraAuthorizationError("camera authorization binary differs")
    return ValidatedCameraAuthorization(manifest, signature, binary, document)
