"""Guided model-universal firmware provisioning and camera authorization."""

from __future__ import annotations

import argparse
import hashlib


def _recovery(facade: object, arguments: argparse.Namespace):
    exact = arguments.recovery_dir
    functional = arguments.functional_recovery_dir
    if (exact is None) == (functional is None):
        raise getattr(facade, "UserInstallerError")(
            "select exactly one recovery evidence class"
        )
    if functional is not None:
        validate = getattr(facade, "validate_functional_recovery_boundary")
        return validate(
            recovery_dir=functional,
            preserved_readback_dir=arguments.preserved_readback_dir,
        )
    validate = getattr(facade, "validate_existing_recovery_boundary")
    return validate(
        recovery_dir=exact,
        preserved_readback_dir=arguments.preserved_readback_dir,
    )


def _universal_provision(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    read_snapshot = getattr(facade, "read_snapshot")
    validate_bundle = getattr(facade, "validate_universal_final_bundle")
    create = getattr(facade, "create_provisioning_sidecar")
    recovery = _recovery(facade, arguments)
    bundle = validate_bundle(
        read_snapshot(arguments.universal_bundle),
        public_key=arguments.universal_public_key,
    )
    provisioned, data_image = create(
        private_config_dir=arguments.private_config_dir,
        session_dir=arguments.session_dir,
        camera_identity_sha256=recovery.camera_identity_sha256,
        universal_firmware_sha256=bundle.sha256,
        universal_system_rootfs=bundle.members["images/system.squashfs"],
        signing_key=arguments.signing_key,
        unsquashfs=arguments.unsquashfs,
        mkfs_jffs2=arguments.mkfs_jffs2,
        output_path=arguments.output,
        data_output_path=arguments.data_output,
    )
    return _document(
        "universal provision",
        ok=True,
        phase="camera-provisioning-sidecar-created",
        next_command="thingino-dlink universal authorize",
        result={
            "artifact_scope": "per-camera-provisioning",
            "camera_bound": True,
            "contains_secrets": True,
            "output": str(arguments.output),
            "provisioning_data_output": str(arguments.data_output),
            "provisioning_data_sha256": data_image.sha256,
            "provisioning_data_size": len(data_image.raw),
            "provisioning_id": provisioned.provisioning_id,
            "provisioning_sidecar_sha256": provisioned.sha256,
            "universal_firmware_sha256": bundle.sha256,
            "universal_firmware_unchanged": True,
            "write_set": [],
        },
    )


def _universal_authorize(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    read_snapshot = getattr(facade, "read_snapshot")
    read_private_data = getattr(facade, "read_private_provisioning_data")
    validate_bundle = getattr(facade, "validate_universal_final_bundle")
    validate_provisioning = getattr(facade, "validate_provisioning_sidecar")
    session_identity = getattr(facade, "recovery_session_identity")
    require_signer = getattr(facade, "require_provisioning_signing_key")
    create = getattr(facade, "create_camera_authorization")
    build_stage2 = getattr(facade, "build_stage2")
    recovery = _recovery(facade, arguments)
    bundle = validate_bundle(
        read_snapshot(arguments.universal_bundle),
        public_key=arguments.universal_public_key,
    )
    session_sha256 = session_identity(arguments.session_dir)
    provisioning = validate_provisioning(
        arguments.provisioning,
        public_key=arguments.provisioning_public_key,
        expected_camera_identity_sha256=recovery.camera_identity_sha256,
        expected_universal_firmware_sha256=bundle.sha256,
        expected_recovery_session_sha256=session_sha256,
    )
    require_signer(provisioning, arguments.signing_key)
    provisioning_data = read_private_data(arguments.provisioning_data)
    if (
        len(provisioning_data) != provisioning.provisioning_data_size
        or hashlib.sha256(provisioning_data).hexdigest()
        != provisioning.provisioning_data_sha256
    ):
        raise getattr(facade, "UserInstallerError")(
            "provisioning data image differs from its signed sidecar"
        )
    stage2 = build_stage2(
        final_kernel=bundle.members["images/kernel.uimage"],
        system_rootfs=bundle.members["images/system.squashfs"],
        data_mode=arguments.data_action,
    )
    authorization = create(
        recovery=recovery,
        universal_bundle=bundle,
        universal_stage2_sha256=hashlib.sha256(stage2).hexdigest(),
        provisioning_sidecar_sha256=provisioning.sha256,
        provisioning_data_sha256=provisioning.provisioning_data_sha256,
        provisioning_data_size=provisioning.provisioning_data_size,
        provisioning_id=provisioning.provisioning_id,
        recovery_session_sha256=session_sha256,
        data_action=arguments.data_action,
        signing_key=arguments.signing_key,
        output_dir=arguments.output_dir,
    )
    return _document(
        "universal authorize",
        ok=True,
        phase="camera-bound-universal-authorization-created",
        next_command=None,
        result={
            "artifact_scope": "per-camera-authorization",
            "authorization_dir": str(arguments.output_dir),
            "authorized_future_write_phases": {
                "stock_bootstrap_physical_mtd": [1, 2],
                "stage1_final_physical_mtd": [1, 3],
            },
            "authorization_sha256": authorization.authorization_sha256,
            "camera_bound": True,
            "data_action": arguments.data_action,
            "provisioning_data_sha256": provisioning.provisioning_data_sha256,
            "provisioning_id": provisioning.provisioning_id,
            "provisioning_sidecar_sha256": provisioning.sha256,
            "safe_next_action": "wait-for-on-camera-authorization-release-gate",
            "universal_firmware_sha256": bundle.sha256,
            "universal_firmware_unchanged": True,
            "write_set": [],
        },
    )
