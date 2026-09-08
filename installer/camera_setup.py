"""Camera provisioning operations shared by terminal and graphical clients."""

from __future__ import annotations

import hashlib
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .install_project import ProjectError, session_fingerprint
from .install_results import document as _document, InstallationResult, InstallationEvent, EventSink
from .sd_package import read_snapshot
from .stage2 import build_stage2


class OperationError(ProjectError):
    def __init__(self, message: str, code: str = "operation_rejected"):
        super().__init__(code, message)


@dataclass(frozen=True, kw_only=True)
class RecoveryInputs:
    preserved_readback_dir: Path
    recovery_dir: Path | None = None
    functional_recovery_dir: Path | None = None


@dataclass(frozen=True, kw_only=True)
class SessionInputs:
    functional_recovery_dir: Path
    preserved_readback_dir: Path
    output_dir: Path
    config_output_dir: Path
    ssh_keygen: Path | None = None
    dropbearkey: Path | None = None


@dataclass(frozen=True)
class WifiInput:
    ssid: str = field(repr=False)
    passphrase: str = field(repr=False)
    confirmation_ssid: str = field(repr=False)
    confirmation_passphrase: str = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class ConfigurationInputs:
    session_dir: Path
    output_dir: Path
    wifi: WifiInput = field(repr=False)
    signing_key: Path | None = None
    signing_public_key: Path | None = None


@dataclass(frozen=True, kw_only=True)
class ProvisionInputs(RecoveryInputs):
    universal_bundle: Path
    universal_public_key: Path
    private_config_dir: Path
    session_dir: Path
    signing_key: Path
    unsquashfs: Path | None = None
    mkfs_jffs2: Path | None = None
    output: Path
    data_output: Path


@dataclass(frozen=True, kw_only=True)
class AuthorizationInputs(RecoveryInputs):
    universal_bundle: Path
    universal_public_key: Path
    provisioning: Path
    provisioning_data: Path
    provisioning_public_key: Path
    session_dir: Path
    signing_key: Path
    output_dir: Path
    data_action: str = "initialize"


@dataclass(frozen=True, kw_only=True)
class VerifyInputs:
    session_dir: Path
    dropbearkey: Path | None = None


def validate_recovery(request: RecoveryInputs):
    from .recovery_gate import validate_existing_recovery_boundary, validate_functional_recovery_boundary
    if (request.recovery_dir is None) == (request.functional_recovery_dir is None):
        raise OperationError("select exactly one recovery evidence class")
    validate = validate_functional_recovery_boundary if request.functional_recovery_dir is not None else validate_existing_recovery_boundary
    return validate(recovery_dir=request.functional_recovery_dir or request.recovery_dir,
                    preserved_readback_dir=request.preserved_readback_dir)


def resolve_tool(selected: Path | None, name: str) -> Path:
    found = str(selected) if selected is not None else shutil.which(name)
    if found is None:
        raise ProjectError("missing_input", "required host tool is unavailable", (name,))
    return Path(found).expanduser().resolve(strict=True)


def initialize_session(request: SessionInputs, *, emit: EventSink | None = None) -> InstallationResult:
    from .recovery_gate import validate_functional_recovery_boundary as validate
    from .recovery_ap.session import ensure_uartless_provisioning_session as ensure
    if emit:
        emit(InstallationEvent("validating", "initialize_session"))
    recovery = validate(
        recovery_dir=request.functional_recovery_dir,
        preserved_readback_dir=request.preserved_readback_dir,
    )
    ssh_keygen = request.ssh_keygen
    if ssh_keygen is None:
        discovered = shutil.which("ssh-keygen")
        if discovered is None:
            raise OperationError("ssh-keygen is required for UARTless provisioning")
        try:
            ssh_keygen = Path(discovered).resolve(strict=True)
        except OSError as exc:
            raise OperationError(
                "cannot resolve the discovered ssh-keygen executable"
            ) from exc
    dropbearkey = request.dropbearkey
    if dropbearkey is None:
        discovered = shutil.which("dropbearkey")
        if discovered is None:
            raise OperationError(
                "dropbearkey is required for UARTless provisioning; "
                "on macOS install Homebrew dropbear"
            )
        try:
            dropbearkey = Path(discovered).resolve(strict=True)
        except OSError as exc:
            raise OperationError(
                "cannot resolve the discovered dropbearkey executable"
            ) from exc
    session = ensure(
        output_dir=request.output_dir,
        ssh_keygen=ssh_keygen,
        dropbearkey=dropbearkey,
        camera_identity_sha256=recovery.camera_identity_sha256,
    )
    next_command = (
        "thingino-dlink universal configure "
        f"--session-dir {shlex.quote(str(session.output_dir))} "
        f"--output-dir {shlex.quote(str(request.config_output_dir))}"
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


def configure_camera(request: ConfigurationInputs, *, emit: EventSink | None = None) -> InstallationResult:
    from .final_bundle import ensure_ed25519_keypair as ensure_keypair
    from .private_config import generate_private_config as generate, load_private_config_for_session as load_private, read_authorized_key as read_key, render_private_wpa_config as render_wpa
    from .recovery_ap.host import load_host_session as load_session, load_service_credential as load_credential
    if emit:
        emit(InstallationEvent("validating", "configure_camera"))

    session = load_session(request.session_dir)
    signing_key = request.signing_key
    if signing_key is None:
        signing_key = (
            request.output_dir.parent
            / "authorization-signing"
            / "ed25519.pem"
        )
    keypair = ensure_keypair(signing_key, request.signing_public_key)
    ssid, passphrase = request.wifi.ssid, request.wifi.passphrase
    confirmation_ssid, confirmation_passphrase = request.wifi.confirmation_ssid, request.wifi.confirmation_passphrase
    if render_wpa(ssid=ssid, passphrase=passphrase) != render_wpa(
        ssid=confirmation_ssid,
        passphrase=confirmation_passphrase,
    ):
        raise OperationError("station Wi-Fi confirmation does not match")
    generated = generate(
        output_dir=request.output_dir,
        ssid=ssid,
        passphrase=passphrase,
        authorized_key=read_key(session.identity.with_suffix(".pub")),
        credential=load_credential(request.session_dir) + b"\n",
    )
    private = load_private(
        output_dir=generated.output_dir,
        session_dir=request.session_dir,
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


def provision_camera(request: ProvisionInputs, *, emit: EventSink | None = None) -> InstallationResult:
    from .final_bundle import validate_universal_final_bundle as validate_bundle
    from .provisioning import create_provisioning_sidecar as create
    if emit:
        emit(InstallationEvent("validating", "provision_camera"))
    recovery = validate_recovery(request)
    bundle = validate_bundle(
        read_snapshot(request.universal_bundle),
        public_key=request.universal_public_key,
    )
    provisioned, data_image = create(
        private_config_dir=request.private_config_dir,
        session_dir=request.session_dir,
        camera_identity_sha256=recovery.camera_identity_sha256,
        universal_firmware_sha256=bundle.sha256,
        universal_system_rootfs=bundle.members["images/system.squashfs"],
        signing_key=request.signing_key,
        unsquashfs=resolve_tool(request.unsquashfs, "unsquashfs"),
        mkfs_jffs2=resolve_tool(request.mkfs_jffs2, "mkfs.jffs2"),
        output_path=request.output,
        data_output_path=request.data_output,
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
            "output": str(request.output),
            "provisioning_data_output": str(request.data_output),
            "provisioning_data_sha256": data_image.sha256,
            "provisioning_data_size": len(data_image.raw),
            "provisioning_id": provisioned.provisioning_id,
            "provisioning_sidecar_sha256": provisioned.sha256,
            "universal_firmware_sha256": bundle.sha256,
            "universal_firmware_unchanged": True,
            "write_set": [],
        },
    )


def authorize_camera(request: AuthorizationInputs, *, emit: EventSink | None = None) -> InstallationResult:
    from .provisioning import read_private_provisioning_data as read_private_data, validate_provisioning_sidecar as validate_provisioning, recovery_session_identity as session_identity, require_provisioning_signing_key as require_signer
    from .final_bundle import validate_universal_final_bundle as validate_bundle
    from .camera_authorization import create_camera_authorization as create
    if emit:
        emit(InstallationEvent("validating", "authorize_camera"))
    recovery = validate_recovery(request)
    bundle = validate_bundle(
        read_snapshot(request.universal_bundle),
        public_key=request.universal_public_key,
    )
    session_sha256 = session_identity(request.session_dir)
    provisioning = validate_provisioning(
        request.provisioning,
        public_key=request.provisioning_public_key,
        expected_camera_identity_sha256=recovery.camera_identity_sha256,
        expected_universal_firmware_sha256=bundle.sha256,
        expected_recovery_session_sha256=session_sha256,
    )
    require_signer(provisioning, request.signing_key)
    provisioning_data = read_private_data(request.provisioning_data)
    if (
        len(provisioning_data) != provisioning.provisioning_data_size
        or hashlib.sha256(provisioning_data).hexdigest()
        != provisioning.provisioning_data_sha256
    ):
        raise OperationError(
            "provisioning data image differs from its signed sidecar"
        )
    stage2 = build_stage2(
        final_kernel=bundle.members["images/kernel.uimage"],
        system_rootfs=bundle.members["images/system.squashfs"],
        data_mode=request.data_action,
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
        data_action=request.data_action,
        signing_key=request.signing_key,
        output_dir=request.output_dir,
    )
    return _document(
        "universal authorize",
        ok=True,
        phase="camera-bound-universal-authorization-created",
        next_command=None,
        result={
            "artifact_scope": "per-camera-authorization",
            "authorization_dir": str(request.output_dir),
            "authorized_future_write_phases": {
                "stock_bootstrap_physical_mtd": [1, 2],
                "stage1_final_physical_mtd": [1, 3],
            },
            "authorization_sha256": authorization.authorization_sha256,
            "camera_bound": True,
            "data_action": request.data_action,
            "provisioning_data_sha256": provisioning.provisioning_data_sha256,
            "provisioning_id": provisioning.provisioning_id,
            "provisioning_sidecar_sha256": provisioning.sha256,
            "safe_next_action": "wait-for-on-camera-authorization-release-gate",
            "universal_firmware_sha256": bundle.sha256,
            "universal_firmware_unchanged": True,
            "write_set": [],
        },
    )


def verify_camera(request: VerifyInputs, *, emit: EventSink | None = None) -> InstallationResult:
    from .recovery_ap.session import ensure_uartless_station_host_pin as ensure_station_pin
    from .recovery_ap.host import prove_thingino_health
    if emit:
        emit(InstallationEvent("validating", "verify_camera"))

    session_identity = session_fingerprint(request.session_dir)
    # Verification must use the already provisioned private host key. Creating a
    # replacement key here would pin a different camera identity.
    from .recovery_ap.session import _read_private
    _read_private(request.session_dir / "host/dropbear_ed25519_host_key", "provisioned host key", 16 * 1024)
    dropbearkey = request.dropbearkey
    if dropbearkey is None:
        discovered = shutil.which("dropbearkey")
        if discovered is None:
            raise OperationError(
                "dropbearkey is required for UARTless station verification"
            )
        dropbearkey = Path(discovered).resolve(strict=True)
    station_pin_created = ensure_station_pin(
        session_dir=request.session_dir,
        dropbearkey=dropbearkey,
    )
    health = prove_thingino_health(session_dir=request.session_dir)
    if session_fingerprint(request.session_dir) != session_identity:
        raise ProjectError("changed_input", "session identity changed during verification")
    ensure_station_pin(session_dir=request.session_dir, dropbearkey=dropbearkey)
    return _document(
        "universal verify",
        ok=True,
        phase="camera-bound-universal-management-verified",
        read_back_verified=health.get("mtd3_read_back_verified") is True,
        next_command=None,
        result={
            **health,
            "safe_next_action": "installation-complete",
            "station_host_pin_created": station_pin_created,
            "station_host_pin": str(request.session_dir / "host/station_known_hosts"),
            "uart_required": False,
            "write_set": [],
        },
    )
