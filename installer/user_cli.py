"""Resumable guided and JSON installer for one DCS-6100LHV2 A1 camera."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.platform.media_preflight import (
    PreflightError,
    create_preflight_document,
)


_LEGACY_INSTALLER_MODULES = (
    Path(__file__).with_name("development_install.py"),
    Path(__file__).with_name("user_cli_guided.py"),
    Path(__file__).with_name("workflow_preflight.py"),
)
LEGACY_INSTALLER_AVAILABLE = all(path.is_file() for path in _LEGACY_INSTALLER_MODULES)

from .candidate_log import (
    CandidateLogError,
    candidate_status,
    create_candidate,
    decide_candidate,
    record_candidate_run,
)
if LEGACY_INSTALLER_AVAILABLE:
    from .development_install import (
        STATE_NAME,
        DevelopmentInstallError,
        build_personal_candidate,
        complete_personal_install,
    )
else:
    STATE_NAME = "install-state.private.json"

    class DevelopmentInstallError(ValueError):
        """The legacy personal installer is not part of the public export."""

    def _legacy_installer_unavailable(*args, **kwargs):
        raise DevelopmentInstallError(
            "the legacy personal installer is not part of this export"
        )

    build_personal_candidate = _legacy_installer_unavailable
    complete_personal_install = _legacy_installer_unavailable
from .collector.build import CollectorBuildError, build_collector_root
from .full_backup import (
    FullBackupError,
    capture_complete_backup_from_ram_collector,
    capture_functional_backup_from_uartless_collector,
    validate_complete_backup,
)
from .camera_authorization import (
    CameraAuthorizationError,
    create_camera_authorization,
)
from .final_bundle import (
    BundleError,
    ensure_ed25519_keypair,
    validate_universal_final_bundle,
)
from .provisioning import (
    ProvisioningError,
    create_provisioning_sidecar,
    read_private_provisioning_data,
    recovery_session_identity,
    require_provisioning_signing_key,
    validate_provisioning_sidecar,
)
from .layout import TARGET
from .live_ram import (
    LiveRamError,
    inspect_live_ram_set,
    prepare_live_ram_set,
    run_live_ram_plan,
    stage_live_ram_set,
)
from .local_build import (
    LocalBuildError,
    configure_local_build_settings,
    default_local_build_private_root,
    load_local_build_settings,
    local_build_workspace_status,
    prepare_local_build_workspace,
    remember_local_build_workspace,
    resolve_local_build_workspace,
    validate_local_build_setting_inputs,
)
from .local_build_bootstrap import (
    LocalBuildBootstrapError,
    bootstrap_public_build_inputs,
)
from .local_build_acquire import (
    LocalBuildAcquireError,
    acquire_locked_public_inputs,
)
from .local_build_run import (
    LocalBuildRunError,
    build_local_install_set,
    build_local_recovery_assets,
    build_local_universal_install_set,
)
from .media import (
    MediaError,
    UARTLESS_CAPTURE_ACTIVE_FILENAME,
    UARTLESS_CAPTURE_PASSIVE_FILENAME,
    activate_passive_verified_package,
    deactivate_verified_package,
    evacuate_existing_stock_backups,
    load_media_preflight,
    stage_passive_verified_package,
    stage_verified_install_set,
    stage_verified_package,
    validate_install_set,
)
from .media_closure import MediaClosureError, load_media_closure
from .mtd3_image import Mtd3ImageError, validate_personal_mtd3_image
from .platform_wifi import PlatformWifiError, join_recovery_ap, join_station_wifi
from .private_config import (
    PrivateConfigError,
    derive_rtsp_viewer_credential,
    generate_private_config,
    inspect_private_config,
    load_private_config_for_session,
    read_authorized_key,
    read_confirmed_private_input,
    read_private_input,
    render_private_wpa_config,
    rotate_private_config_role,
)
from .ram_boot import build_ramdisk_uimage
from .recovery import (
    RecoveryError,
    inspect_same_device_stock_restore,
    prepare_same_device_stock_restore,
)
from .recovery_gate import (
    RecoveryGateError,
    validate_existing_recovery_boundary,
    validate_functional_recovery_boundary,
)
from .recovery_ap.host import (
    RecoveryApHostError,
    collect_runtime_snapshot,
    load_host_session,
    load_service_credential,
    prove_thingino_health,
    prove_thingino_media,
)
from .recovery_ap.session import (
    RecoveryApSessionError,
    ensure_uartless_station_host_pin,
    ensure_uartless_provisioning_session,
)
from .recovery_package_binding import (
    RecoveryPackageBindingError,
    read_embedded_session_file,
    validate_recovery_package_binding,
)
from .rtsp_verify import RtspVerificationError, verify_rtsp_h264_1080p
from .runtime_diagnostics import (
    FAULT_CLASSES,
    RuntimeDiagnosticsError,
    record_hypothesis,
    store_runtime_snapshot,
    validate_runtime_snapshot,
)
from .runtime_candidate import (
    RuntimeCandidateError,
    rollback_runtime_candidate,
    runtime_candidate_status,
    stage_runtime_candidate,
)
from .stage2 import build_stage2
from .sd_package import (
    PackageError,
    atomic_write,
    matching_update_filenames,
    package_manifest,
    parse_package,
    read_snapshot,
    validate_bootstrap,
)
from .stock_restore.build import StockRestoreBuildError
from .stock_restore.kernel import StockRestoreKernelError
from .stock_restore.set import (
    StockRestoreSetError,
    authorize_stock_restore,
    authorize_stock_restore_bootstrap,
    deactivate_stock_restore_bootstrap,
    expected_bootstrap_confirmation,
    expected_handoff_confirmation,
    expected_live_confirmation,
    expected_retry_confirmation,
    inspect_stock_restore_bootstrap_set,
    inspect_stock_restore_ram_set,
    passivate_completed_stock_restore,
    prepare_stock_restore_bootstrap_set,
    prepare_stock_restore_ram_set,
    reauthorize_interrupted_stock_restore,
    stage_stock_restore_bootstrap_set,
    stage_stock_restore_ram_set,
)
if LEGACY_INSTALLER_AVAILABLE:
    from .workflow_preflight import (
        MODES as WORKFLOW_MODES,
        WorkflowPreflightError,
        observe_camera_state,
        run_workflow_preflight,
    )
else:
    WORKFLOW_MODES = ()

    class WorkflowPreflightError(ValueError):
        """The legacy workflow preflight is not part of the public export."""

    def observe_camera_state(*args, **kwargs):
        raise WorkflowPreflightError(
            "the legacy workflow preflight is not part of this export"
        )

    def run_workflow_preflight(*args, **kwargs):
        raise WorkflowPreflightError(
            "the legacy workflow preflight is not part of this export"
        )
from .universal_install import (
    UniversalInstallError,
    handoff_camera_bound_universal_install,
    stage_camera_bound_universal_install,
    validate_camera_bound_universal_install,
)
from .vendor_bundle import VendorBundleError, load_vendor_bundle


SCHEMA_VERSION = 1
CONFIG_NAME = "installer-config.private.json"
CARD_PREFLIGHT_NAME = "card-preflight.private.json"
BOOTSTRAP_NAME = "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"


class UserInstallerError(ValueError):
    """The guided installer cannot continue safely."""


class ArgumentParsingError(ValueError):
    """The command line cannot be represented by a parsed Namespace."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParsingError(message)


def _default_work_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/thingino-dlink/dcs6100lhv2-a1"
    data = os.environ.get("XDG_DATA_HOME")
    root = Path(data) if data else Path.home() / ".local/share"
    return root / "thingino-dlink/dcs6100lhv2-a1"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


from .install_results import _target, document as _document, failure_details


def _render(document: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        return
    status = "OK" if document["ok"] else "STOPPED"
    print(f"thingino-dlink {document['command']}: {status}")
    if document.get("error"):
        print(f"Reason: {document['error']}")
    print(f"Phase: {document['phase']}")
    nor = document["nor"]
    assert isinstance(nor, dict)
    print("NOR write active now: no")
    print(f"MTD written by this run: {nor['written_mtd']}")
    print(
        "Full physical readback: "
        + ("verified" if nor["full_physical_readback_verified"] else "not claimed")
    )
    actions = document.get("physical_actions", [])
    if actions:
        print("Physical actions:")
        for number, action in enumerate(actions, 1):
            print(f"  {number}. {action}")
    result = document.get("result", {})
    if isinstance(result, dict):
        for key in (
            "name",
            "records",
            "next_action",
            "missing_prerequisites",
            "superseded_host_records",
            "required_confirmations",
            "plan",
            "plan_sha256",
            "local_phase",
            "build_root",
            "build_count",
            "host_platform",
            "docker_ready",
            "free_gib",
            "required_free_gib",
            "ready_to_build",
            "safe_next_action",
            "last_camera_state",
            "mtd3_kind",
            "mtd3_sha256",
            "station_mdns_name",
            "station_ipv4",
            "media_gate",
            "duplicate_backup_accepted",
            "full_flash_reconstruction_accepted",
            "same_device_binding_accepted",
            "restore_status",
            "write_set",
            "physical_restore_proven",
            "bundle_sha256",
            "manifest_sha256",
            "firmware_version",
            "file_count",
        ):
            if result.get(key) is not None:
                print(f"{key}: {result[key]}")
    if document.get("next_command"):
        print(f"Next command: {document['next_command']}")


def _config_path(work_dir: Path) -> Path:
    return work_dir / CONFIG_NAME


def _load_json(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise UserInstallerError(f"{label} is missing or not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserInstallerError(f"{label} is invalid") from exc
    if not isinstance(document, dict):
        raise UserInstallerError(f"{label} is invalid")
    return document


def _load_config(work_dir: Path) -> dict[str, object]:
    document = _load_json(_config_path(work_dir), "installer configuration")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("target") != _target():
        raise UserInstallerError("installer configuration targets another schema or camera")
    return document


def _path_field(config: dict[str, object], name: str) -> Path:
    value = config.get(name)
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise UserInstallerError(f"installer configuration lacks {name}")
    return Path(value)


def _regular(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if path.is_symlink() or not resolved.is_file():
        raise UserInstallerError(f"{label} is not a regular file")
    return resolved


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if path.is_symlink() or not resolved.is_dir():
        raise UserInstallerError(f"{label} is not a real directory")
    return resolved


def _tool(value: Path | None, name: str) -> Path:
    candidate = value
    if candidate is None:
        found = shutil.which(name)
        if found is None:
            raise UserInstallerError(f"required host tool is missing: {name}")
        candidate = Path(found)
    resolved = candidate.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise UserInstallerError(f"required host tool is not a regular file: {name}")
    if not os.access(resolved, os.X_OK):
        raise UserInstallerError(f"required host tool is not executable: {name}")
    return resolved


def _session_binding_sha256(session: Path) -> str:
    """Bind resumable state to the public identity of one private session."""

    digest = hashlib.sha256()
    for relative in (
        "host/identity.pub",
        "host/known_hosts",
        "host/session.json",
    ):
        path = session / relative
        if path.is_symlink() or not path.is_file():
            raise UserInstallerError("recovery session identity is incomplete")
        raw = path.read_bytes()
        digest.update(relative.encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _read_embedded_session_file(
    *, unsquashfs: Path, squashfs: Path, relative: str, limit: int
) -> bytes:
    try:
        return read_embedded_session_file(
            unsquashfs=unsquashfs,
            squashfs=squashfs,
            relative=relative,
            limit=limit,
            run=subprocess.run,
        )
    except RecoveryPackageBindingError as exc:
        raise UserInstallerError(str(exc)) from exc


def _validate_recovery_package_binding(
    *, package_path: Path, session: Path, unsquashfs: Path
) -> dict[str, object]:
    """Validate mtd1/mtd2-only framing and its exact embedded session."""
    try:
        return validate_recovery_package_binding(
            package_path=package_path,
            session=session,
            unsquashfs=unsquashfs,
            run=subprocess.run,
            read_package=read_snapshot,
            parse=parse_package,
            validate=validate_bootstrap,
        )
    except RecoveryPackageBindingError as exc:
        raise UserInstallerError(str(exc)) from exc


def _validate_config(config: dict[str, object]) -> dict[str, object]:
    session = _directory(_path_field(config, "session_dir"), "session directory")
    host_session = load_host_session(session)
    base = _regular(_path_field(config, "base_rootfs"), "base Thingino rootfs")
    media_closure = _directory(
        _path_field(config, "media_closure_dir"), "private media closure"
    )
    mksquashfs = _tool(_path_field(config, "mksquashfs"), "mksquashfs")
    unsquashfs = _tool(_path_field(config, "unsquashfs"), "unsquashfs")
    helper = _regular(_path_field(config, "macos_wifi_helper"), "macOS Wi-Fi helper")
    station_value = config.get("macos_station_wifi_helper")
    if station_value is None:
        station_value = str(
            Path(__file__).resolve().parents[1]
            / "scripts/platform/macos_station_wifi.swift"
        )
    if not isinstance(station_value, str):
        raise UserInstallerError("macos_station_wifi_helper is invalid")
    station_helper = _regular(Path(station_value), "macOS station Wi-Fi helper")
    package_value = config.get("recovery_package")
    if package_value is None and isinstance(config.get("install_set_dir"), str):
        package_value = str(Path(str(config["install_set_dir"])) / BOOTSTRAP_NAME)
    recovery_package: Path | None = None
    recovery_binding: dict[str, object] | None = None
    if package_value is not None:
        if not isinstance(package_value, str):
            raise UserInstallerError("recovery_package is invalid")
        recovery_package = _regular(Path(package_value), "recovery mtd1/mtd2 package")
        recovery_binding = _validate_recovery_package_binding(
            package_path=recovery_package,
            session=session,
            unsquashfs=unsquashfs,
        )
        for field in ("package_sha256", "session_binding_sha256"):
            recorded = config.get(f"recovery_{field}")
            if recorded is not None and recorded != recovery_binding[field]:
                raise UserInstallerError("recovery package or session changed after preflight")
    return {
        "base_rootfs": base,
        "media_closure_dir": media_closure,
        "recovery_package": recovery_package,
        "recovery_package_sha256": (
            recovery_binding["package_sha256"] if recovery_binding is not None else None
        ),
        "recovery_session_binding_sha256": (
            recovery_binding["session_binding_sha256"]
            if recovery_binding is not None
            else None
        ),
        "macos_wifi_helper": helper,
        "macos_station_wifi_helper": station_helper,
        "mksquashfs": mksquashfs,
        "session_dir": session,
        "station_mdns_name": host_session.station_mdns_name,
        "unsquashfs": unsquashfs,
    }


def _preflight(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _preflight as implementation

    return implementation(sys.modules[__name__], arguments)


def _prepare_card(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _prepare_card as implementation

    return implementation(sys.modules[__name__], arguments)


def _stage_install_set(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _stage_install_set as implementation

    return implementation(sys.modules[__name__], arguments)


def _install(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _install as implementation

    return implementation(sys.modules[__name__], arguments)


def _state(work_dir: Path) -> dict[str, object] | None:
    state_path = work_dir / "install" / STATE_NAME
    if not state_path.exists():
        return None
    state = _load_json(state_path, "installer state")
    if state.get("schema_version") != 1:
        raise UserInstallerError("installer state schema is unsupported")
    return state


def _next_for_state(state: dict[str, object] | None) -> str:
    if state is None:
        return "thingino-dlink install"
    if state.get("phase") == "healthy":
        return "thingino-dlink verify"
    return "thingino-dlink install"


def _status(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _status as implementation

    return implementation(sys.modules[__name__], arguments)


def _record_proof(
    work_dir: Path,
    key: str,
    proof: dict[str, object],
    *,
    management: dict[str, object] | None = None,
) -> None:
    state = _state(work_dir)
    if state is None:
        raise UserInstallerError("installation state is missing")
    state[key] = {**proof, "proven_at": _now()}
    if management is not None:
        state["last_camera"] = {
            "authenticated_control": management.get("authenticated_control") is True,
            "file_transfer_bytes": management.get("file_transfer_bytes"),
            "file_transfer_sha256": management.get("file_transfer_sha256"),
            "mdns_resolution": management.get("mdns_resolution"),
            "mtd3_kind": "personal",
            "mtd3_sha256": management.get("mtd3_sha256"),
            "proven_at": _now(),
            "read_back_verified": management.get("mtd3_read_back_verified") is True,
            "ssh_authentication": management.get("ssh_authentication"),
            "state": "station",
            "station_ipv4": management.get("station_ipv4"),
            "station_mdns_name": management.get("station_mdns_name"),
        }
    atomic_write(
        work_dir / "install" / STATE_NAME,
        (json.dumps(state, indent=2, sort_keys=True) + "\n").encode(),
    )
    (work_dir / "install" / STATE_NAME).chmod(0o600)


def _expected_personal_image_sha256(work_dir: Path) -> str:
    image_path = work_dir / "install/personal-mtd3.bin"
    if image_path.is_symlink() or not image_path.is_file():
        raise UserInstallerError("personal mtd3 image is missing")
    try:
        image = validate_personal_mtd3_image(image_path.read_bytes())
    except (OSError, Mtd3ImageError) as exc:
        raise UserInstallerError("personal mtd3 image is invalid") from exc
    return image.image_sha256


def _verify(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _verify as implementation

    return implementation(sys.modules[__name__], arguments)


def _verify_media(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _verify_media as implementation

    return implementation(sys.modules[__name__], arguments)


def _workflow_preflight(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_guided import _workflow_preflight as implementation

    return implementation(sys.modules[__name__], arguments)


def _local_build_document(
    command: str, result: dict[str, object]
) -> dict[str, object]:
    docker = result.get("docker")
    rendered = {
        **result,
        "docker_ready": (
            docker.get("ready") is True if isinstance(docker, dict) else False
        ),
    }
    ready = rendered["ready_to_build"] is True
    return _document(
        command,
        ok=True,
        phase=(
            "local-build-workspace-ready"
            if ready
            else "local-build-host-action-required"
        ),
        result=rendered,
    )


def _local_build_prepare(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_prepare as implementation

    return implementation(
        arguments,
        _local_build_document=_local_build_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        prepare_local_build_workspace=prepare_local_build_workspace,
        remember_local_build_workspace=remember_local_build_workspace,
    )


def _inspect_vendor_bundle(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _inspect_vendor_bundle as implementation

    return implementation(
        arguments,
        _document=_document,
        load_vendor_bundle=load_vendor_bundle,
    )


def _local_build_status(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_status as implementation

    return implementation(
        arguments,
        _local_build_document=_local_build_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        local_build_workspace_status=local_build_workspace_status,
    )


def _local_build_bootstrap(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_bootstrap as implementation

    return implementation(
        arguments,
        _document=_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        bootstrap_public_build_inputs=bootstrap_public_build_inputs,
    )


def _local_build_acquire(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_acquire as implementation

    return implementation(
        arguments,
        _document=_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        acquire_locked_public_inputs=acquire_locked_public_inputs,
    )


def _local_build_recovery_assets(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_recovery_assets as implementation

    return implementation(
        arguments,
        _document=_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        build_local_recovery_assets=build_local_recovery_assets,
    )


def _local_build_configure(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_configure as implementation

    return implementation(
        arguments,
        _document=_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        error_type=UserInstallerError,
        validate_local_build_setting_inputs=validate_local_build_setting_inputs,
        read_confirmed_private_input=read_confirmed_private_input,
        configure_local_build_settings=configure_local_build_settings,
    )


def _local_build_build(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _local_build_build as implementation

    return implementation(
        arguments,
        _document=_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        error_type=UserInstallerError,
        load_local_build_settings=load_local_build_settings,
        build_local_install_set=build_local_install_set,
    )


def _local_build_build_universal(
    arguments: argparse.Namespace,
) -> dict[str, object]:
    from .user_cli_build import _local_build_build_universal as implementation

    return implementation(
        arguments,
        _document=_document,
        resolve_local_build_workspace=resolve_local_build_workspace,
        ensure_ed25519_keypair=ensure_ed25519_keypair,
        build_local_universal_install_set=build_local_universal_install_set,
    )


def _universal_provision(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_provision as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_configure(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_configure as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_init_session(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_init_session as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_authorize(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_authorize as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_stage(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_stage as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_evacuate_recovery(
    arguments: argparse.Namespace,
) -> dict[str, object]:
    from .user_cli_universal import _universal_evacuate_recovery as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_quarantine_inconsistent_media(
    arguments: argparse.Namespace,
) -> dict[str, object]:
    from .user_cli_universal import (
        _universal_quarantine_inconsistent_media as implementation,
    )

    return implementation(sys.modules[__name__], arguments)


def _universal_handoff(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_handoff as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_verify(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_verify as implementation

    return implementation(sys.modules[__name__], arguments)


def _universal_verify_readback(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_universal import _universal_verify_readback as implementation

    return implementation(sys.modules[__name__], arguments)


def _build_personal_mtd3(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _build_personal_mtd3 as implementation

    return implementation(
        arguments,
        _document=_document,
        _load_config=_load_config,
        _validate_config=_validate_config,
    )


def _runtime_candidate_stage(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _runtime_candidate_stage as implementation

    return implementation(
        arguments,
        _document=_document,
    )


def _runtime_candidate_status(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _runtime_candidate_status as implementation

    return implementation(
        arguments,
        _document=_document,
    )


def _runtime_candidate_rollback(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _runtime_candidate_rollback as implementation

    return implementation(
        arguments,
        _document=_document,
    )


def _private_config_inspect(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _private_config_inspect as implementation

    return implementation(
        arguments,
        _document=_document,
    )


def _private_config_rotate(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_build import _private_config_rotate as implementation

    return implementation(
        arguments,
        _document=_document,
        error_type=UserInstallerError,
        _regular=_regular,
    )


def _candidate_store(arguments: argparse.Namespace) -> Path:
    from .user_cli_diagnostics import _candidate_store as implementation

    return implementation(sys.modules[__name__], arguments)


def _candidate_create(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_diagnostics import _candidate_create as implementation

    return implementation(sys.modules[__name__], arguments)


def _candidate_record(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_diagnostics import _candidate_record as implementation

    return implementation(sys.modules[__name__], arguments)


def _candidate_status(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_diagnostics import _candidate_status as implementation

    return implementation(sys.modules[__name__], arguments)


def _candidate_decide(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_diagnostics import _candidate_decide as implementation

    return implementation(sys.modules[__name__], arguments)


def _diagnose_runtime(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_diagnostics import _diagnose_runtime as implementation

    return implementation(sys.modules[__name__], arguments)


def _hypothesis_record(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_diagnostics import _hypothesis_record as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_backup_prepare(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_backup_prepare as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_backup_capture(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_backup_capture as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_backup_validate(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_backup_validate as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_uartless_prepare(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_uartless_prepare as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_uartless_reuse(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_uartless_reuse as implementation
    return implementation(sys.modules[__name__], arguments)


def _stock_uartless_authorize(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_uartless_authorize as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_uartless_handoff(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_uartless_handoff as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_uartless_validate(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_uartless_validate as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_restore_prepare(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_restore_prepare as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_restore_inspect(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_restore_inspect as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_live_prepare(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_live_prepare as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_live_authorize(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_live_authorize as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_live_restore(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_live_restore as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_live_passivate(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_live_passivate as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_sd_prepare(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_sd_prepare as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_sd_authorize(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_sd_authorize as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_sd_confirmation(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_sd_confirmation as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_sd_handoff(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_sd_handoff as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_sd_retry(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_sd_retry as implementation

    return implementation(sys.modules[__name__], arguments)


def _stock_sd_passivate(arguments: argparse.Namespace) -> dict[str, object]:
    from .user_cli_stock_recovery import _stock_sd_passivate as implementation

    return implementation(sys.modules[__name__], arguments)


def _add_common(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    default = argparse.SUPPRESS if inherited else False
    parser.add_argument(
        "--json",
        action="store_true",
        default=default,
        help="emit the stable JSON result",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=argparse.SUPPRESS if inherited else _default_work_dir(),
    )
    parser.add_argument("--project", action="append",
                        default=argparse.SUPPRESS if inherited else None,
                        help="explicit private camera project, or DCS6100_PROJECT")
    parser.add_argument("--non-interactive", action="store_true", default=default,
                        help="never prompt; missing inputs return a structured error")
    parser.add_argument("--events-jsonl", action="store_true", default=default,
                        help="emit bounded progress events as JSON Lines on stderr")


def build_parser() -> argparse.ArgumentParser:
    from .user_cli_parser import build_parser as build_parser_impl

    return build_parser_impl(sys.modules[__name__])


def main(argv: list[str] | None = None) -> int:
    from . import user_cli_project as projects
    from .install_project import ProjectError
    parser = build_parser()
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        arguments = parser.parse_args(projects.expand(raw_arguments))
        projects.require_noninteractive_inputs(arguments)
    except (ArgumentParsingError, ProjectError, OSError) as exc:
        if "--json" in raw_arguments or "--non-interactive" in raw_arguments:
            known_commands = {
                "preflight",
                "prepare-card",
                "stage-install-set",
                "install",
                "status",
                "verify",
                "verify-media",
                "build-personal-mtd3",
                "workflow-preflight",
                "inspect-vendor-bundle",
                "local-build",
                "universal",
                "runtime-candidate",
                "private-config",
                "candidate",
                "diagnose-runtime",
                "hypothesis-record",
                "stock-recovery",
                "project",
            }
            command = next(
                (value for value in raw_arguments if value in known_commands),
                "unknown",
            )
            failure = _document(
                command,
                ok=False,
                phase="argument-error",
                next_command=(
                    f"thingino-dlink {command}"
                    if command in known_commands
                    else "thingino-dlink --help"
                ),
                error=str(exc),
            )
            missing = (
                list(exc.missing) if isinstance(exc, ProjectError)
                else projects.missing_options(parser, raw_arguments)
            )
            failure["error_code"] = getattr(exc, "code", "missing_input" if missing else "invalid_arguments")
            failure["missing_inputs"] = missing
            _render(failure, json_mode=True)
            return 2
        parser.print_usage(sys.stderr)
        print(f"thingino-dlink: error: {exc}", file=sys.stderr)
        return 2
    if not arguments.json and arguments.command == "install":
        print("Installer will join the session recovery AP, inspect live NOR, and write only mtd3 if needed.")
        print("Do not remove power while the installer reports an mtd3 write/readback operation.")
    try:
        active_project = projects.begin(arguments)
        projects.event(arguments, "started")
        document = arguments.handler(arguments)
        projects.finish(active_project, document)
    except (
        BundleError,
        CameraAuthorizationError,
        CandidateLogError,
        CollectorBuildError,
        DevelopmentInstallError,
        FullBackupError,
        MediaError,
        LocalBuildError,
        LocalBuildAcquireError,
        LocalBuildBootstrapError,
        LocalBuildRunError,
        LiveRamError,
        Mtd3ImageError,
        PlatformWifiError,
        PackageError,
        PreflightError,
        PrivateConfigError,
        ProvisioningError,
        RecoveryError,
        RecoveryGateError,
        RecoveryApHostError,
        RecoveryApSessionError,
        RtspVerificationError,
        RuntimeDiagnosticsError,
        RuntimeCandidateError,
        StockRestoreBuildError,
        StockRestoreKernelError,
        StockRestoreSetError,
        UserInstallerError,
        UniversalInstallError,
        VendorBundleError,
        WorkflowPreflightError,
        OSError,
        ProjectError,
        KeyboardInterrupt,
    ) as exc:
        next_command = f"thingino-dlink {arguments.command}"
        written_mtd: list[int] = []
        read_back_verified = False
        result: dict[str, object] = {}
        if arguments.command == "install":
            try:
                failed_state = _state(arguments.work_dir.expanduser().resolve())
            except (OSError, UserInstallerError):
                failed_state = None
            if failed_state is not None:
                current = failed_state.get("current_run")
                camera = failed_state.get("last_camera")
                if isinstance(current, dict):
                    recorded = current.get("written_mtd")
                    if recorded in ([], [3]):
                        written_mtd = recorded
                    read_back_verified = current.get("read_back_verified") is True
                result = {
                    "last_camera": camera if isinstance(camera, dict) else {},
                    "local_phase": failed_state.get("phase"),
                    "write_history": failed_state.get("write_history", []),
                }
        document = _document(
            arguments.command,
            ok=False,
            phase="stopped",
            written_mtd=written_mtd,
            read_back_verified=read_back_verified,
            next_command=next_command,
            result=result,
            error=str(exc),
        )
        document.update(failure_details(exc))
        projects.event(arguments, "stopped")
        _render(document, json_mode=arguments.json)
        return 130 if isinstance(exc, KeyboardInterrupt) else 2
    projects.event(arguments, "completed" if document.get("ok") else "stopped")
    _render(document, json_mode=arguments.json)
    if arguments.command == "stock-recovery" and document.get("ok") is False:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
