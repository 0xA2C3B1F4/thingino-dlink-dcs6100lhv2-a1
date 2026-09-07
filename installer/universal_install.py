"""Host gate binding one universal build to one camera and provisioning set."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .camera_authorization import (
    ValidatedCameraAuthorization,
    validate_camera_authorization,
)
from .final_bundle import ValidatedFinalBundle, validate_universal_final_bundle
from . import media
from .media import validate_install_set
from .media_preflight import MediaPreflight
from .provisioning import (
    ValidatedProvisioning,
    read_private_provisioning_data,
    validate_provisioning_sidecar,
)
from .recovery_gate import RecoveryDecision
from .sd_package import parse_package
from .stage1.build import BOOTSTRAP_FILENAME
from .stage2 import FILENAME as STAGE2_FILENAME, Stage2Payload


INSTALL_MANIFEST = "install-set.manifest.json"
STAGE1_ROOT = "stage1-bootstrap.squashfs"
UNIVERSAL_BUNDLE = "thingino-universal.tgb"
INSTALL_SET_MEMBERS = {
    BOOTSTRAP_FILENAME,
    STAGE2_FILENAME,
    INSTALL_MANIFEST,
    STAGE1_ROOT,
    UNIVERSAL_BUNDLE,
}
MAX_INSTALL_MEMBER = 16 * 1024 * 1024
PROVISIONING_CARD_NAME = "THINGINO.PROVISION"
AUTHORIZATION_CARD_NAME = "INSTALL.AUTH"
AUTHORIZATION_SIGNATURE_CARD_NAME = "INSTALL.AUTH.SIG"
AUTHORIZATION_BINARY_CARD_NAME = "INSTALL.AUTH.BIN"


class UniversalInstallError(ValueError):
    """Universal firmware, camera evidence, and provisioning are not one tuple."""


@dataclass(frozen=True, slots=True)
class ValidatedUniversalInstall:
    bundle: ValidatedFinalBundle
    stage2: Stage2Payload
    provisioning: ValidatedProvisioning
    provisioning_data: bytes = field(repr=False)
    authorization: ValidatedCameraAuthorization
    bootstrap: bytes
    manifest: bytes


def _read_regular(path: Path, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise UniversalInstallError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > limit
        ):
            raise UniversalInstallError(f"{label} is not a bounded regular file")
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
        raise UniversalInstallError(f"{label} changed while being read")
    return raw


def validate_camera_bound_universal_install(
    *,
    install_set_dir: Path,
    universal_bundle_public_key: Path,
    recovery: RecoveryDecision,
    provisioning_path: Path,
    provisioning_data_path: Path,
    authorization_dir: Path,
    authorization_public_key: Path,
    recovery_session_sha256: str,
) -> ValidatedUniversalInstall:
    """Require all cross-camera swap checks before removable-media staging."""

    if not recovery.camera_identity_sha256:
        raise UniversalInstallError("same-device recovery lacks camera identity")
    if install_set_dir.is_symlink() or not install_set_dir.is_dir():
        raise UniversalInstallError("universal install-set directory is invalid")
    if {entry.name for entry in install_set_dir.iterdir()} != INSTALL_SET_MEMBERS:
        raise UniversalInstallError("universal install-set directory is not exact")
    bundle_raw = _read_regular(
        install_set_dir / UNIVERSAL_BUNDLE,
        "universal final bundle",
        MAX_INSTALL_MEMBER,
    )
    try:
        bundle = validate_universal_final_bundle(
            bundle_raw, public_key=universal_bundle_public_key
        )
    except ValueError as exc:
        raise UniversalInstallError(str(exc)) from exc
    bootstrap = _read_regular(
        install_set_dir / BOOTSTRAP_FILENAME,
        "universal bootstrap",
        MAX_INSTALL_MEMBER,
    )
    stage2_raw = _read_regular(
        install_set_dir / STAGE2_FILENAME,
        "universal stage 2",
        MAX_INSTALL_MEMBER,
    )
    manifest_raw = _read_regular(
        install_set_dir / INSTALL_MANIFEST,
        "universal install-set manifest",
        128 * 1024,
    )
    stage1_root = _read_regular(
        install_set_dir / STAGE1_ROOT,
        "universal stage-1 root",
        MAX_INSTALL_MEMBER,
    )
    try:
        stage2 = validate_install_set(
            bootstrap_bytes=bootstrap,
            stage2_bytes=stage2_raw,
            manifest_bytes=manifest_raw,
            bootstrap_name=BOOTSTRAP_FILENAME,
        )
        manifest = json.loads(manifest_raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UniversalInstallError(str(exc)) from exc
    if (
        manifest.get("artifact_scope") != "model-universal"
        or manifest.get("provisioning") != "separate-per-camera-audit-and-jffs2"
        or manifest.get("universal_firmware_sha256") != bundle.sha256
    ):
        raise UniversalInstallError("install set is not bound to the universal bundle")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or artifacts.get(UNIVERSAL_BUNDLE) != {
        "sha256": bundle.sha256,
        "size": len(bundle.raw),
    }:
        raise UniversalInstallError("install manifest does not bind universal bundle")
    package = parse_package(bootstrap, require_project_header=True)
    if (
        stage2.kernel != bundle.members["images/kernel.uimage"]
        or stage2.system != bundle.members["images/system.squashfs"]
        or package.records[1].payload != bundle.members["images/bootstrap.squashfs"]
        or package.records[1].payload != stage1_root
    ):
        raise UniversalInstallError("install set components differ from universal bundle")
    try:
        provisioning = validate_provisioning_sidecar(
            provisioning_path,
            public_key=authorization_public_key,
            expected_camera_identity_sha256=recovery.camera_identity_sha256,
            expected_universal_firmware_sha256=bundle.sha256,
            expected_recovery_session_sha256=recovery_session_sha256,
        )
        provisioning_data = read_private_provisioning_data(
            provisioning_data_path
        )
        if (
            len(provisioning_data) != provisioning.provisioning_data_size
            or hashlib.sha256(provisioning_data).hexdigest()
            != provisioning.provisioning_data_sha256
        ):
            raise UniversalInstallError(
                "provisioning data image differs from its signed sidecar"
            )
        authorization = validate_camera_authorization(
            authorization_dir,
            public_key=authorization_public_key,
            expected_camera_identity_sha256=recovery.camera_identity_sha256,
            expected_camera_authorization_key_sha256=(
                recovery.camera_authorization_key_sha256
            ),
            expected_universal_firmware_sha256=bundle.sha256,
            expected_universal_stage2_sha256=stage2.sha256,
            expected_provisioning_sidecar_sha256=provisioning.sha256,
            expected_provisioning_data_sha256=provisioning.provisioning_data_sha256,
            expected_provisioning_data_size=provisioning.provisioning_data_size,
            expected_provisioning_id=provisioning.provisioning_id,
            expected_recovery_session_sha256=recovery_session_sha256,
            expected_data_action=stage2.data_mode,
        )
    except ValueError as exc:
        raise UniversalInstallError(str(exc)) from exc
    return ValidatedUniversalInstall(
        bundle=bundle,
        stage2=stage2,
        provisioning=provisioning,
        provisioning_data=provisioning_data,
        authorization=authorization,
        bootstrap=bootstrap,
        manifest=manifest_raw,
    )


def stage_camera_bound_universal_install(
    validated: ValidatedUniversalInstall,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> dict[str, str]:
    """Stage camera material first and activate the universal bootstrap last."""

    if confirmed_physical_device != preflight.physical_device:
        raise UniversalInstallError("physical-device confirmation differs")
    if root.resolve(strict=True) != preflight.mount_root:
        raise UniversalInstallError("universal staging root changed after preflight")
    root_identity = root.stat(follow_symlinks=False)
    if (
        getattr(preflight, "mount_device_id", 0)
        and (
            root_identity.st_dev != preflight.mount_device_id
            or root_identity.st_ino != preflight.mount_inode
        )
    ):
        raise UniversalInstallError(
            "universal staging media identity changed after preflight"
        )
    media.validate_sd_root(root)
    passive_bootstrap = root / media.PASSIVE_BOOTSTRAP_FILENAME
    staged_stage2 = root / STAGE2_FILENAME
    resume_passive_install_set = any(
        path.exists() or path.is_symlink()
        for path in (passive_bootstrap, staged_stage2)
    )
    if resume_passive_install_set and (
        passive_bootstrap.is_symlink()
        or staged_stage2.is_symlink()
        or not passive_bootstrap.is_file()
        or not staged_stage2.is_file()
    ):
        raise UniversalInstallError(
            "existing passive universal install set is incomplete or ambiguous"
        )
    replace_passive_install_set = resume_passive_install_set and (
        passive_bootstrap.read_bytes() != validated.bootstrap
        or staged_stage2.read_bytes() != validated.stage2.raw
    )
    authorization = validated.authorization
    payloads = {
        PROVISIONING_CARD_NAME: validated.provisioning_data,
        AUTHORIZATION_CARD_NAME: authorization.raw_manifest,
        AUTHORIZATION_SIGNATURE_CARD_NAME: authorization.signature,
        AUTHORIZATION_BINARY_CARD_NAME: authorization.binary,
    }
    destinations = {name: root / name for name in payloads}
    temporaries = {name: root / ("." + name + ".part") for name in payloads}
    owned_sidecars = {
        root / ("._" + path.name)
        for path in (*destinations.values(), *temporaries.values())
    }
    if any(
        path.exists() or path.is_symlink()
        for path in (*destinations.values(), *temporaries.values(), *owned_sidecars)
    ):
        raise UniversalInstallError("reserved universal staging path already exists")
    activated: list[Path] = []
    try:
        for name in sorted(payloads):
            media._write_verified_temporary(temporaries[name], payloads[name])
        for name in sorted(payloads):
            os.replace(temporaries[name], destinations[name])
            activated.append(destinations[name])
            media._sync_directory(root)
        for name, raw in payloads.items():
            path = destinations[name]
            if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
                raise UniversalInstallError(f"universal sidecar readback differs: {name}")
        install_set_arguments = {
            "bootstrap_bytes": validated.bootstrap,
            "stage2_bytes": validated.stage2.raw,
            "manifest_bytes": validated.manifest,
            "root": root,
            "bootstrap_name": BOOTSTRAP_FILENAME,
            "preflight": preflight,
            "confirmed_physical_device": confirmed_physical_device,
        }
        if resume_passive_install_set:
            if replace_passive_install_set:
                media.replace_passive_bootstrap(
                    old_bootstrap_bytes=passive_bootstrap.read_bytes(),
                    old_stage2_bytes=staged_stage2.read_bytes(),
                    new_bootstrap_bytes=validated.bootstrap,
                    stage2_bytes=validated.stage2.raw,
                    manifest_bytes=validated.manifest,
                    root=root,
                    bootstrap_name=BOOTSTRAP_FILENAME,
                    preflight=preflight,
                    confirmed_physical_device=confirmed_physical_device,
                )
            staged = media.activate_staged_install_set(
                **install_set_arguments,
                recovery_authorization_bytes=authorization.binary,
                recovery_provisioning_bytes=validated.provisioning_data,
            )
        else:
            staged = media.stage_verified_install_set(**install_set_arguments)
        for sidecar in owned_sidecars:
            sidecar.unlink(missing_ok=True)
        media._sync_directory(root)
        return staged | {
            name: hashlib.sha256(raw).hexdigest()
            for name, raw in payloads.items()
        }
    except BaseException:
        for path in activated:
            path.unlink(missing_ok=True)
        media._sync_directory(root)
        raise
    finally:
        removed = False
        for path in (*temporaries.values(), *owned_sidecars):
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
                removed = True
        if removed:
            media._sync_directory(root)


def handoff_camera_bound_universal_install(
    validated: ValidatedUniversalInstall,
    *,
    root: Path,
    preflight: MediaPreflight,
    confirmed_physical_device: str,
) -> dict[str, str]:
    """Make the stock selector inert after its verified mtd1+mtd2 phase."""

    if confirmed_physical_device != preflight.physical_device:
        raise UniversalInstallError("physical-device confirmation differs")
    if root.resolve(strict=True) != preflight.mount_root:
        raise UniversalInstallError("universal handoff root changed after preflight")
    root_identity = root.stat(follow_symlinks=False)
    if (
        getattr(preflight, "mount_device_id", 0)
        and (
            root_identity.st_dev != preflight.mount_device_id
            or root_identity.st_ino != preflight.mount_inode
        )
    ):
        raise UniversalInstallError(
            "universal handoff media identity changed after preflight"
        )
    media.validate_sd_root(root, allowed_matching_filename=BOOTSTRAP_FILENAME)

    authorization = validated.authorization
    payloads = {
        PROVISIONING_CARD_NAME: validated.provisioning_data,
        AUTHORIZATION_CARD_NAME: authorization.raw_manifest,
        AUTHORIZATION_SIGNATURE_CARD_NAME: authorization.signature,
        AUTHORIZATION_BINARY_CARD_NAME: authorization.binary,
    }

    def verify_camera_payloads() -> None:
        for name, raw in payloads.items():
            path = root / name
            sidecar = root / ("._" + name)
            if (
                path.is_symlink()
                or not path.is_file()
                or sidecar.exists()
                or path.read_bytes() != raw
            ):
                raise UniversalInstallError(
                    f"universal camera sidecar differs at handoff: {name}"
                )

    verify_camera_payloads()
    deactivated = media.deactivate_staged_install_set(
        bootstrap_bytes=validated.bootstrap,
        stage2_bytes=validated.stage2.raw,
        manifest_bytes=validated.manifest,
        root=root,
        bootstrap_name=BOOTSTRAP_FILENAME,
        preflight=preflight,
        confirmed_physical_device=confirmed_physical_device,
    )
    verify_camera_payloads()
    return deactivated | {
        name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()
    }
