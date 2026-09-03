"""Guided model-universal firmware provisioning and camera authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil


UNIVERSAL_WRITE_CONFIRMATION = "STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3"
UNIVERSAL_HANDOFF_CONFIRMATION = "MTD1-MTD2-WRITTEN"


def _universal_init_session(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    UserInstallerError = getattr(facade, "UserInstallerError")
    validate = getattr(facade, "validate_functional_recovery_boundary")
    ensure = getattr(facade, "ensure_uartless_provisioning_session")
    recovery = validate(
        recovery_dir=arguments.functional_recovery_dir,
        preserved_readback_dir=arguments.preserved_readback_dir,
    )
    ssh_keygen = arguments.ssh_keygen
    if ssh_keygen is None:
        discovered = shutil.which("ssh-keygen")
        if discovered is None:
            raise UserInstallerError("ssh-keygen is required for UARTless provisioning")
        ssh_keygen = getattr(facade, "Path")(discovered)
    dropbearkey = arguments.dropbearkey
    if dropbearkey is None:
        discovered = shutil.which("dropbearkey")
        if discovered is None:
            raise UserInstallerError(
                "dropbearkey is required for UARTless provisioning; "
                "on macOS install Homebrew dropbear"
            )
        dropbearkey = getattr(facade, "Path")(discovered)
    session = ensure(
        output_dir=arguments.output_dir,
        ssh_keygen=ssh_keygen,
        dropbearkey=dropbearkey,
        camera_identity_sha256=recovery.camera_identity_sha256,
    )
    next_command = (
        "thingino-dlink universal configure "
        f"--session-dir {shlex.quote(str(session.output_dir))} "
        f"--output-dir {shlex.quote(str(arguments.config_output_dir))}"
    )
    return _document(
        "universal init-session",
        ok=True,
        phase="uartless-local-provisioning-session-ready",
        next_command=next_command,
        result={
            "camera_bound": True,
            "contains_secrets": True,
            "session_dir": str(session.output_dir),
            "safe_next_action": "run-universal-configure-in-an-interactive-terminal",
            "write_set": [],
        },
    )


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


def _universal_configure(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _document = getattr(facade, "_document")
    UserInstallerError = getattr(facade, "UserInstallerError")
    ensure_keypair = getattr(facade, "ensure_ed25519_keypair")
    generate = getattr(facade, "generate_private_config")
    load_private = getattr(facade, "load_private_config_for_session")
    load_session = getattr(facade, "load_host_session")
    load_credential = getattr(facade, "load_service_credential")
    read_key = getattr(facade, "read_authorized_key")
    read_wifi = getattr(facade, "read_confirmed_private_input")
    render_wpa = getattr(facade, "render_private_wpa_config")

    session = load_session(arguments.session_dir)
    signing_key = arguments.signing_key
    if signing_key is None:
        signing_key = (
            arguments.output_dir.parent
            / "authorization-signing"
            / "ed25519.pem"
        )
    keypair = ensure_keypair(signing_key, arguments.signing_public_key)
    ssid, passphrase, confirmation_ssid, confirmation_passphrase = read_wifi(
        secrets_fd=arguments.secrets_fd
    )
    if render_wpa(ssid=ssid, passphrase=passphrase) != render_wpa(
        ssid=confirmation_ssid,
        passphrase=confirmation_passphrase,
    ):
        raise UserInstallerError("station Wi-Fi confirmation does not match")
    generated = generate(
        output_dir=arguments.output_dir,
        ssid=ssid,
        passphrase=passphrase,
        authorized_key=read_key(session.identity.with_suffix(".pub")),
        credential=load_credential(arguments.session_dir) + b"\n",
    )
    private = load_private(
        output_dir=generated.output_dir,
        session_dir=arguments.session_dir,
    )
    return _document(
        "universal configure",
        ok=True,
        phase="camera-private-inputs-configured",
        next_command="thingino-dlink universal provision",
        result={
            "camera_bound": True,
            "contains_secrets": True,
            "credential_set_id": private.credential_set_id,
            "private_config_dir": str(generated.output_dir),
            "signing": keypair,
            "station_psk_derived": True,
            "write_set": [],
        },
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


def _universal_stage(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _directory = getattr(facade, "_directory")
    _document = getattr(facade, "_document")
    UserInstallerError = getattr(facade, "UserInstallerError")
    atomic_write = getattr(facade, "atomic_write")
    create_preflight = getattr(facade, "create_preflight_document")
    load_preflight = getattr(facade, "load_media_preflight")
    session_identity = getattr(facade, "recovery_session_identity")
    stage = getattr(facade, "stage_camera_bound_universal_install")
    validate = getattr(facade, "validate_camera_bound_universal_install")

    recovery = _recovery(facade, arguments)
    target_confirmation = arguments.confirm_target
    if target_confirmation is None and not arguments.json:
        target_confirmation = input(
            "Check the camera label and type DCS-6100LHV2-A1 to confirm the target: "
        ).strip()
    if target_confirmation != "DCS-6100LHV2-A1":
        raise UserInstallerError("target label was not confirmed as DCS-6100LHV2 A1")
    write_confirmation = arguments.confirm_write_set
    if write_confirmation is None and not arguments.json:
        write_confirmation = input(
            "This card boots the stock updater for mtd1+mtd2, then writes final mtd1+mtd3. "
            f"Type {UNIVERSAL_WRITE_CONFIRMATION} to continue: "
        ).strip()
    if write_confirmation != UNIVERSAL_WRITE_CONFIRMATION:
        raise UserInstallerError("universal physical write set was not confirmed")

    mount_root = _directory(arguments.mount_root, "mounted SD root")
    preflight_document = create_preflight(
        whole_device=arguments.whole_device,
        mount_root=mount_root,
    )
    work_dir = arguments.work_dir.expanduser()
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work_dir.chmod(0o700)
    preflight_path = work_dir / "universal-card-preflight.private.json"
    atomic_write(
        preflight_path,
        (json.dumps(preflight_document, indent=2, sort_keys=True) + "\n").encode(),
        mode=0o600,
    )
    preflight = load_preflight(preflight_path, expected_root=mount_root)
    device_confirmation = arguments.confirm_physical_device
    if device_confirmation is None and not arguments.json:
        device_confirmation = input(
            f"Type the exact whole device {preflight.physical_device} to confirm SD staging: "
        ).strip()
    if device_confirmation is None:
        raise UserInstallerError("JSON mode requires --confirm-physical-device")

    validated = validate(
        install_set_dir=arguments.install_set_dir,
        universal_bundle_public_key=arguments.universal_public_key,
        recovery=recovery,
        provisioning_path=arguments.provisioning,
        provisioning_data_path=arguments.provisioning_data,
        authorization_dir=arguments.authorization_dir,
        authorization_public_key=arguments.authorization_public_key,
        recovery_session_sha256=session_identity(arguments.session_dir),
    )
    staged = stage(
        validated,
        root=mount_root,
        preflight=preflight,
        confirmed_physical_device=device_confirmation,
    )
    return _document(
        "universal stage",
        ok=True,
        phase="camera-bound-universal-card-staged",
        next_command="thingino-dlink universal handoff",
        physical_actions=[
            "Confirm the bottom label says DCS-6100LHV2 and hardware revision A1.",
            "Power the camera off before inserting or removing the verified SD card.",
            "The first boot writes stock physical mtd1 and mtd2; the next phase writes final mtd1 and physical mtd3.",
            "Do not interrupt power while either phase reports a write or readback.",
        ],
        result={
            "armed": True,
            "future_physical_write_phases": {
                "stock_bootstrap": [1, 2],
                "stage1_final": [1, 3],
            },
            "nor_written_by_host": False,
            "physical_device": preflight.physical_device,
            "safe_next_action": (
                "boot-stock-updater-once-then-return-sd-for-universal-handoff"
            ),
            "staged_files": staged,
            "write_set": [],
        },
    )


def _universal_handoff(
    facade: object, arguments: argparse.Namespace
) -> dict[str, object]:
    _directory = getattr(facade, "_directory")
    _document = getattr(facade, "_document")
    UserInstallerError = getattr(facade, "UserInstallerError")
    atomic_write = getattr(facade, "atomic_write")
    create_preflight = getattr(facade, "create_preflight_document")
    handoff = getattr(facade, "handoff_camera_bound_universal_install")
    load_preflight = getattr(facade, "load_media_preflight")
    session_identity = getattr(facade, "recovery_session_identity")
    validate = getattr(facade, "validate_camera_bound_universal_install")

    recovery = _recovery(facade, arguments)
    target_confirmation = arguments.confirm_target
    if target_confirmation is None and not arguments.json:
        target_confirmation = input(
            "Check the camera label and type DCS-6100LHV2-A1 to confirm the target: "
        ).strip()
    if target_confirmation != "DCS-6100LHV2-A1":
        raise UserInstallerError("target label was not confirmed as DCS-6100LHV2 A1")
    stock_confirmation = arguments.confirm_stock_uboot_result
    if stock_confirmation is None and not arguments.json:
        stock_confirmation = input(
            "Confirm that the stock updater reported verified mtd1+mtd2 completion. "
            f"Type {UNIVERSAL_HANDOFF_CONFIRMATION} to continue: "
        ).strip()
    if stock_confirmation != UNIVERSAL_HANDOFF_CONFIRMATION:
        raise UserInstallerError(
            "universal handoff requires exact MTD1-MTD2-WRITTEN confirmation"
        )

    mount_root = _directory(arguments.mount_root, "mounted SD root")
    preflight_document = create_preflight(
        whole_device=arguments.whole_device,
        mount_root=mount_root,
    )
    work_dir = arguments.work_dir.expanduser()
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work_dir.chmod(0o700)
    preflight_path = work_dir / "universal-handoff-preflight.private.json"
    atomic_write(
        preflight_path,
        (json.dumps(preflight_document, indent=2, sort_keys=True) + "\n").encode(),
        mode=0o600,
    )
    preflight = load_preflight(preflight_path, expected_root=mount_root)
    device_confirmation = arguments.confirm_physical_device
    if device_confirmation is None and not arguments.json:
        device_confirmation = input(
            f"Type the exact whole device {preflight.physical_device} to confirm SD handoff: "
        ).strip()
    if device_confirmation is None:
        raise UserInstallerError("JSON mode requires --confirm-physical-device")

    validated = validate(
        install_set_dir=arguments.install_set_dir,
        universal_bundle_public_key=arguments.universal_public_key,
        recovery=recovery,
        provisioning_path=arguments.provisioning,
        provisioning_data_path=arguments.provisioning_data,
        authorization_dir=arguments.authorization_dir,
        authorization_public_key=arguments.authorization_public_key,
        recovery_session_sha256=session_identity(arguments.session_dir),
    )
    retained = handoff(
        validated,
        root=mount_root,
        preflight=preflight,
        confirmed_physical_device=device_confirmation,
    )
    return _document(
        "universal handoff",
        ok=True,
        phase="camera-bound-universal-stock-selector-passive",
        next_command=None,
        physical_actions=[
            "Power the camera off before inserting or removing the verified SD card.",
            "The next boot runs Stage 1 and writes final physical mtd1 and mtd3.",
            "Do not interrupt power while Stage 1 reports a write or readback.",
        ],
        result={
            "armed": False,
            "future_physical_write_phases": {"stage1_final": [1, 3]},
            "nor_written_by_host": False,
            "physical_device": preflight.physical_device,
            "retained_files": retained,
            "safe_next_action": "boot-camera-with-passive-card-to-run-stage1",
            "write_set": [],
        },
    )
