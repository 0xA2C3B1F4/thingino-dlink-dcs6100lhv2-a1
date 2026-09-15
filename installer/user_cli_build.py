"""Build command handlers with explicit CLI dependencies."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .local_build import default_local_build_private_root
from .private_config import inspect_private_config, rotate_private_config_role
from .runtime_candidate import (
    rollback_runtime_candidate,
    runtime_candidate_status,
    stage_runtime_candidate,
)
from .vendor_bundle import VendorBundle


# These call signatures describe only the operations a handler uses. The public
# CLI passes its current bindings, preserving existing patch points without
# giving command implementations access to the entire CLI module.
class CommandDocument(Protocol):
    def __call__(
        self, command: str, *, ok: bool, phase: str,
        next_command: str | None = None, result: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


class ResolveWorkspace(Protocol):
    def __call__(self, *, build_root: Path | None, work_dir: Path) -> Path: ...


class PrepareWorkspace(Protocol):
    def __call__(self, *, build_root: Path, build_count: int) -> dict[str, object]: ...


class RememberWorkspace(Protocol):
    def __call__(self, *, build_root: Path, work_dir: Path) -> Path: ...


class WorkspaceOperation(Protocol):
    def __call__(self, *, build_root: Path) -> dict[str, object]: ...


class ValidateSettingInputs(Protocol):
    def __call__(
        self, *, build_root: Path, private_root: Path, vendor_bundle_dir: Path,
        media_closure_dir: Path, session_dir: Path,
    ) -> dict[str, object]: ...


class ReadPrivateInput(Protocol):
    def __call__(self, *, secrets_fd: int | None) -> tuple[str, str, str, str]: ...


class ConfigureSettings(Protocol):
    def __call__(
        self, *, build_root: Path, work_dir: Path, private_root: Path,
        vendor_bundle_dir: Path, media_closure_dir: Path, session_dir: Path,
        data_mode: str, ssid: str, passphrase: str,
        confirmation_ssid: str, confirmation_passphrase: str,
    ) -> dict[str, object]: ...


class LoadSettings(Protocol):
    def __call__(
        self, *, settings_path: Path | None, work_dir: Path,
    ) -> dict[str, object]: ...


class BuildInstallSet(Protocol):
    def __call__(
        self, *, build_root: Path, vendor_bundle_dir: Path,
        media_closure_dir: Path, private_config_dir: Path,
        expected_wpa_config_path: Path, session_dir: Path,
        data_mode: str, build_count: int,
    ) -> dict[str, object]: ...


class BuildUniversalInstallSet(Protocol):
    def __call__(
        self, *, build_root: Path, vendor_bundle_dir: Path,
        signing_key: Path, build_count: int,
        progress: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]: ...


class EnsureKeypair(Protocol):
    def __call__(
        self, private_key: Path, public_key: Path | None,
    ) -> dict[str, object]: ...


def _inspect_vendor_bundle(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    load_vendor_bundle: Callable[[Path], VendorBundle],
) -> dict[str, object]:
    bundle = load_vendor_bundle(arguments.vendor_bundle_dir)
    files = [
        {
            "destination": artifact.destination,
            "name": artifact.name,
            "sha256": artifact.sha256,
            "size": len(artifact.raw),
        }
        for artifact in bundle.artifacts
    ]
    return _document(
        "inspect-vendor-bundle",
        ok=True,
        phase="vendor-bundle-inspected",
        result={
            "bundle_sha256": bundle.bundle_sha256,
            "file_count": len(files),
            "files": files,
            "firmware_version": bundle.firmware_version,
            "manifest_sha256": bundle.manifest_sha256,
        },
    )


def _local_build_prepare(
    arguments: argparse.Namespace,
    *,
    _local_build_document: Callable[[str, dict[str, object]], dict[str, object]],
    resolve_local_build_workspace: ResolveWorkspace,
    prepare_local_build_workspace: PrepareWorkspace,
    remember_local_build_workspace: RememberWorkspace,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = prepare_local_build_workspace(
        build_root=build_root,
        build_count=arguments.build_count,
    )
    pointer = remember_local_build_workspace(
        build_root=Path(str(result["build_root"])),
        work_dir=arguments.work_dir,
    )
    result = {**result, "workspace_pointer": str(pointer)}
    return _local_build_document("local-build prepare", result)


def _local_build_status(
    arguments: argparse.Namespace,
    *,
    _local_build_document: Callable[[str, dict[str, object]], dict[str, object]],
    resolve_local_build_workspace: ResolveWorkspace,
    local_build_workspace_status: WorkspaceOperation,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = local_build_workspace_status(build_root=build_root)
    return _local_build_document("local-build status", result)


def _local_build_bootstrap(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    resolve_local_build_workspace: ResolveWorkspace,
    bootstrap_public_build_inputs: WorkspaceOperation,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = bootstrap_public_build_inputs(build_root=build_root)
    return _document(
        "local-build bootstrap",
        ok=True,
        phase="local-build-public-bootstrap-ready",
        result=result,
    )


def _local_build_acquire(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    resolve_local_build_workspace: ResolveWorkspace,
    acquire_locked_public_inputs: WorkspaceOperation,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = acquire_locked_public_inputs(build_root=build_root)
    return _document(
        "local-build acquire",
        ok=True,
        phase="local-build-locked-public-inputs-ready",
        result=result,
    )


def _local_build_recovery_assets(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    resolve_local_build_workspace: ResolveWorkspace,
    build_local_recovery_assets: WorkspaceOperation,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    result = build_local_recovery_assets(build_root=build_root)
    return _document(
        "local-build recovery-assets",
        ok=True,
        phase="local-build-recovery-assets-ready",
        next_command="thingino-dlink stock-recovery backup-prepare",
        result=result,
    )


def _prompt_local_build_value(
    *,
    error_type: type[ValueError],
    label: str,
    explanation: str,
    default: str,
) -> str:
    print(explanation, file=sys.stderr)
    print(f"{label} [{default}]: ", end="", file=sys.stderr, flush=True)
    value = sys.stdin.readline()
    if value == "":
        raise error_type(
            f"cannot read interactive setting: {label}"
        )
    return value.strip() or default


def _local_build_configure(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    resolve_local_build_workspace: ResolveWorkspace,
    error_type: type[ValueError],
    validate_local_build_setting_inputs: ValidateSettingInputs,
    read_confirmed_private_input: ReadPrivateInput,
    configure_local_build_settings: ConfigureSettings,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    default_private = default_local_build_private_root(build_root)
    private_root = arguments.private_root or Path(
        _prompt_local_build_value(
            error_type=error_type,
            label="Private input root",
            explanation=(
                "Stores local camera files, generated credentials, and the saved build plan. "
                "It must be owned by you with mode 0700."
            ),
            default=str(default_private),
        )
    )
    defaults = {
        "vendor_bundle_dir": private_root / "vendor-bundle",
        "media_closure_dir": private_root / "media-closure",
        "session_dir": private_root / "recovery-session",
    }
    prompts = {
        "vendor_bundle_dir": (
            "Vendor bundle directory",
            "Output of the read-only stock-mtd3 vendor acquisition for this A1 camera.",
        ),
        "media_closure_dir": (
            "Media closure directory",
            "Previously validated, hash-locked C1 media runtime closure.",
        ),
        "session_dir": (
            "Recovery session directory",
            "Private recovery session whose SSH identity and service credential bind this build.",
        ),
    }
    selected: dict[str, Path] = {}
    for field, default in defaults.items():
        provided = getattr(arguments, field)
        label, explanation = prompts[field]
        selected[field] = provided or Path(
            _prompt_local_build_value(
                error_type=error_type,
                label=label,
                explanation=explanation,
                default=str(default),
            )
        )
    data_mode = arguments.data_mode or _prompt_local_build_value(
        error_type=error_type,
        label="Data action",
        explanation=(
            "initialize creates the first backup/checkpoint; preserve keeps the data region "
            "byte-identical; factory-reset explicitly erases only the data region."
        ),
        default="initialize",
    )
    if data_mode not in {"initialize", "preserve", "factory-reset"}:
        raise error_type(
            "data action must be initialize, preserve, or factory-reset"
        )
    validate_local_build_setting_inputs(
        build_root=build_root,
        private_root=private_root,
        vendor_bundle_dir=selected["vendor_bundle_dir"],
        media_closure_dir=selected["media_closure_dir"],
        session_dir=selected["session_dir"],
    )
    ssid, passphrase, confirmation_ssid, confirmation_passphrase = (
        read_confirmed_private_input(secrets_fd=arguments.secrets_fd)
    )
    result = configure_local_build_settings(
        build_root=build_root,
        work_dir=arguments.work_dir,
        private_root=private_root,
        vendor_bundle_dir=selected["vendor_bundle_dir"],
        media_closure_dir=selected["media_closure_dir"],
        session_dir=selected["session_dir"],
        data_mode=data_mode,
        ssid=ssid,
        passphrase=passphrase,
        confirmation_ssid=confirmation_ssid,
        confirmation_passphrase=confirmation_passphrase,
    )
    return _document(
        "local-build configure",
        ok=True,
        phase="local-build-private-inputs-configured",
        result=result,
    )


def _local_build_build(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    resolve_local_build_workspace: ResolveWorkspace,
    error_type: type[ValueError],
    load_local_build_settings: LoadSettings,
    build_local_install_set: BuildInstallSet,
) -> dict[str, object]:
    raise error_type(
        "local-build build is retired; use local-build build-universal"
    )

    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    fields = (
        "vendor_bundle_dir",
        "media_closure_dir",
        "private_config_dir",
        "expected_wpa_config",
        "session_dir",
    )
    provided = {field: getattr(arguments, field) for field in fields}
    used = [field for field, value in provided.items() if value is not None]
    if arguments.settings is not None and used:
        raise error_type(
            "--settings cannot be combined with explicit private input paths"
        )
    if used:
        if len(used) != len(fields):
            missing = ", ".join(
                f"--{field.replace('_', '-')}"
                for field, value in provided.items()
                if value is None
            )
            raise error_type(
                f"explicit build mode requires all private input paths; missing {missing}"
            )
        selected = provided
        data_mode = arguments.data_mode or "initialize"
    else:
        settings = load_local_build_settings(
            settings_path=arguments.settings,
            work_dir=arguments.work_dir,
        )
        if Path(str(settings["build_root"])).resolve(strict=False) != build_root.resolve(strict=False):
            raise error_type(
                "saved settings belong to a different local build workspace"
            )
        selected = {field: Path(str(settings[field])) for field in fields}
        saved_mode = str(settings["data_mode"])
        if arguments.data_mode is not None and arguments.data_mode != saved_mode:
            raise error_type(
                "--data-mode differs from the configured data action"
            )
        data_mode = saved_mode
    result = build_local_install_set(
        build_root=build_root,
        vendor_bundle_dir=selected["vendor_bundle_dir"],
        media_closure_dir=selected["media_closure_dir"],
        private_config_dir=selected["private_config_dir"],
        expected_wpa_config_path=selected["expected_wpa_config"],
        session_dir=selected["session_dir"],
        data_mode=data_mode,
        build_count=getattr(arguments, "build_count", 1),
    )
    return _document(
        "local-build build",
        ok=True,
        phase="local-build-install-set-inspected",
        result=result,
    )


def _local_build_build_universal(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    resolve_local_build_workspace: ResolveWorkspace,
    ensure_ed25519_keypair: EnsureKeypair,
    build_local_universal_install_set: BuildUniversalInstallSet,
) -> dict[str, object]:
    build_root = resolve_local_build_workspace(
        build_root=arguments.build_root,
        work_dir=arguments.work_dir,
    )
    signing_key = arguments.signing_key
    if signing_key is None:
        signing_key = (
            build_root.parent
            / f"{build_root.name}-private"
            / "model-signing"
            / "release-ed25519.pem"
        )
    keypair = ensure_ed25519_keypair(
        signing_key,
        getattr(arguments, "signing_public_key", None),
    )
    from .user_cli_project import progress_callback

    result = build_local_universal_install_set(
        build_root=build_root,
        vendor_bundle_dir=arguments.vendor_bundle_dir,
        signing_key=Path(str(keypair["private_key"])),
        data_mode=getattr(arguments, "data_mode", "initialize"),
        build_count=getattr(arguments, "build_count", 1),
        progress=progress_callback(arguments),
    )
    result = {**result, "model_signing": keypair}
    return _document(
        "local-build build-universal",
        ok=True,
        phase="local-build-model-universal-install-set-inspected",
        next_command="thingino-dlink universal init-session",
        result=result,
    )


def _build_personal_mtd3(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    _load_config: Callable[[Path], dict[str, object]],
    _validate_config: Callable[[dict[str, object]], dict[str, object]],
) -> dict[str, object]:
    # This compatibility handler is intentionally lazy: the production export
    # omits the personal installer module, while the development checkout may
    # still use it for migration tests.
    from .development_install import build_personal_candidate

    work_dir = arguments.work_dir.expanduser().resolve(strict=True)
    validated = _validate_config(_load_config(work_dir))
    session_dir = arguments.session_dir or validated["session_dir"]
    result = build_personal_candidate(
        session_dir=session_dir,
        base_rootfs_path=validated["base_rootfs"],
        private_config_dir=arguments.private_config_dir,
        expected_wpa_config_path=arguments.expected_wpa_config,
        vendor_bundle_dir=arguments.vendor_bundle_dir,
        media_closure_dir=validated["media_closure_dir"],
        output_dir=arguments.output_dir,
        mksquashfs=validated["mksquashfs"],
        unsquashfs=validated["unsquashfs"],
    )
    return _document(
        "build-personal-mtd3",
        ok=True,
        phase="personal-mtd3-built",
        result=result,
    )


def _runtime_candidate_stage(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
) -> dict[str, object]:
    result = stage_runtime_candidate(
        session_dir=arguments.session_dir,
        host=arguments.host,
        rootfs_path=arguments.rootfs,
        provenance_path=arguments.image_provenance,
        expected_mtd3_sha256=arguments.expected_mtd3_sha256,
        unsquashfs=arguments.unsquashfs,
    )
    rollback = shlex.join(
        [
            "python3",
            "-m",
            "installer.user_cli",
            "runtime-candidate",
            "rollback",
            "--json",
            "--session-dir",
            str(arguments.session_dir),
            "--host",
            arguments.host,
            "--approve-volatile-runtime-restart",
        ]
    )
    return _document(
        "runtime-candidate stage",
        ok=True,
        phase="runtime-candidate-active",
        next_command=rollback,
        result={**result, "rollback_command": rollback},
    )


def _runtime_candidate_status(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
) -> dict[str, object]:
    result = runtime_candidate_status(
        session_dir=arguments.session_dir,
        host=arguments.host,
    )
    return _document(
        "runtime-candidate status",
        ok=True,
        phase="runtime-candidate-status",
        result=result,
    )


def _runtime_candidate_rollback(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
) -> dict[str, object]:
    result = rollback_runtime_candidate(
        session_dir=arguments.session_dir,
        host=arguments.host,
    )
    return _document(
        "runtime-candidate rollback",
        ok=True,
        phase="runtime-candidate-rolled-back",
        result=result,
    )


def _private_config_inspect(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
) -> dict[str, object]:
    result = inspect_private_config(
        output_dir=arguments.private_config_dir,
        session_dir=arguments.session_dir,
    )
    return _document(
        "private-config inspect",
        ok=True,
        phase="private-config-inspected",
        result=result,
    )


def _private_config_rotate(
    arguments: argparse.Namespace,
    *,
    _document: CommandDocument,
    error_type: type[ValueError],
    _regular: Callable[[Path, str], Path],
) -> dict[str, object]:
    wpa_config = None
    if arguments.wpa_config is not None:
        path = _regular(arguments.wpa_config, "private WPA configuration")
        if path.stat().st_size > 4096:
            raise error_type("private WPA configuration exceeds its size limit")
        wpa_config = path.read_bytes()
    result = rotate_private_config_role(
        output_dir=arguments.private_config_dir,
        role=arguments.role,
        session_dir=arguments.session_dir,
        wpa_config=wpa_config,
    )
    return _document(
        "private-config rotate",
        ok=True,
        phase="private-config-rotated",
        result=result,
    )
