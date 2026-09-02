"""Create and inspect task-owned local firmware-build workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .sd_package import atomic_write


SCHEMA_VERSION = 1
WORKSPACE_NAME = "local-build-workspace.json"
WORKSPACE_POINTER_NAME = "local-build-current.json"
SETTINGS_NAME = "local-build-settings.private.json"
SETTINGS_POINTER_NAME = "local-build-settings-current.json"
PROJECT_ID = "thingino-dlink-dcs6100lhv2-a1"
BUILD_ROOT_ENVIRONMENT = "DCS6100_BUILD_ROOT"
BYTES_PER_GIB = 1024**3
MIN_FREE_BYTES_PER_BUILD = 80 * BYTES_PER_GIB
SUPPORTED_BUILD_COUNTS = (1, 2)
DATA_MODES = ("initialize", "preserve", "factory-reset")


class LocalBuildError(ValueError):
    """The local build workspace cannot be used safely."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _project_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    markers = (
        root / "installer/user_cli.py",
        root / "profiles/dlink-dcs6100lhv2-a1/artifact-limits.json",
        root / "sources.lock.json",
    )
    if not all(path.is_file() and not path.is_symlink() for path in markers):
        raise LocalBuildError("project checkout is incomplete")
    return root


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalBuildError("project checkout is not a readable Git worktree") from exc
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and not candidate.is_symlink():
        parent = candidate.parent
        if parent == candidate:
            raise LocalBuildError("build root has no existing ancestor")
        candidate = parent
    if candidate.is_symlink() or not candidate.is_dir():
        raise LocalBuildError("build root ancestor is not a real directory")
    return candidate


def _reject_existing_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            break
        if current.is_symlink():
            raise LocalBuildError("build root path contains a symbolic link")


def _resolve_build_root(path: Path, *, project_root: Path) -> tuple[Path, Path]:
    absolute = _absolute(path)
    _reject_existing_symlink_components(absolute)
    ancestor = _existing_ancestor(absolute)
    try:
        relative = absolute.relative_to(ancestor)
    except ValueError as exc:
        raise LocalBuildError("build root cannot be resolved safely") from exc
    root = (ancestor.resolve(strict=True) / relative).resolve(strict=False)
    if root == Path(root.anchor):
        raise LocalBuildError("build root must not be the filesystem root")
    if root == project_root or root.is_relative_to(project_root):
        raise LocalBuildError("build root must be outside the project checkout")
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise LocalBuildError("build root ancestor is not writable")
    return root, ancestor


def _required_free_bytes(build_count: int) -> int:
    if build_count not in SUPPORTED_BUILD_COUNTS:
        raise LocalBuildError("build count must be one or two")
    return build_count * MIN_FREE_BYTES_PER_BUILD


def _docker_status() -> dict[str, object]:
    executable = shutil.which("docker")
    if executable is None:
        return {
            "executable": None,
            "ready": False,
            "reason": "docker-command-missing",
            "server_version": None,
        }
    try:
        completed = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "executable": executable,
            "ready": False,
            "reason": "docker-runtime-unreachable",
            "server_version": None,
        }
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version:
        return {
            "executable": executable,
            "ready": False,
            "reason": "docker-runtime-unreachable",
            "server_version": None,
        }
    return {
        "executable": executable,
        "ready": True,
        "reason": None,
        "server_version": version,
    }


def _manifest(root: Path) -> dict[str, object]:
    path = root / WORKSPACE_NAME
    if path.is_symlink() or not path.is_file():
        raise LocalBuildError("local build workspace manifest is missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBuildError("local build workspace manifest is invalid") from exc
    if not isinstance(document, dict):
        raise LocalBuildError("local build workspace manifest is invalid")
    required = {
        "build_count",
        "build_root",
        "cache_policy",
        "created_at",
        "created_from_head",
        "project",
        "schema_version",
        "sources_lock_sha256",
    }
    if set(document) != required:
        raise LocalBuildError("local build workspace manifest fields are invalid")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise LocalBuildError("local build workspace schema is unsupported")
    if document.get("project") != PROJECT_ID:
        raise LocalBuildError("local build workspace targets another project")
    if document.get("build_root") != str(root):
        raise LocalBuildError("local build workspace was moved after creation")
    build_count = document.get("build_count")
    if not isinstance(build_count, int) or build_count not in SUPPORTED_BUILD_COUNTS:
        raise LocalBuildError("local build workspace build count is invalid")
    if document.get("cache_policy") != "public-inputs-only":
        raise LocalBuildError("local build workspace cache policy is invalid")
    for key in ("created_at", "created_from_head", "sources_lock_sha256"):
        value = document.get(key)
        if not isinstance(value, str) or not value:
            raise LocalBuildError(f"local build workspace {key} is invalid")
    return document


def _validate_owned_tree(root: Path) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        raise LocalBuildError("local build root is not a real directory")
    if stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise LocalBuildError("local build root permissions are too broad")
    manifest = _manifest(root)
    allowed = {WORKSPACE_NAME, "cache", "runs"}
    names = {path.name for path in root.iterdir()}
    unexpected = sorted(names - allowed)
    if unexpected:
        raise LocalBuildError(
            "local build root contains unexpected entries: " + ", ".join(unexpected)
        )
    for name in ("cache", "runs"):
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise LocalBuildError(f"local build {name} directory is invalid")
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise LocalBuildError(f"local build {name} permissions are too broad")
    cache_allowed = {"downloads", "images", "sources"}
    cache_names = {path.name for path in (root / "cache").iterdir()}
    unexpected_cache = sorted(cache_names - cache_allowed)
    if unexpected_cache:
        raise LocalBuildError(
            "local build cache contains unexpected entries: "
            + ", ".join(unexpected_cache)
        )
    for name in sorted(cache_allowed):
        directory = root / "cache" / name
        if directory.is_symlink() or not directory.is_dir():
            raise LocalBuildError(f"local build cache/{name} directory is invalid")
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise LocalBuildError(
                f"local build cache/{name} permissions are too broad"
            )
    return manifest


def _workspace_result(root: Path, manifest: dict[str, object]) -> dict[str, object]:
    project_root = _project_root()
    head = _git(project_root, "rev-parse", "HEAD")
    changed = [
        line
        for line in _git(project_root, "status", "--short", "--untracked-files=all").splitlines()
        if line
    ]
    usage = shutil.disk_usage(root)
    build_count = int(manifest["build_count"])
    required = _required_free_bytes(build_count)
    docker = _docker_status()
    source_lock_matches = manifest["sources_lock_sha256"] == _sha256(
        project_root / "sources.lock.json"
    )
    free_space_ready = usage.free >= required
    platform = host_platform()
    host_supported = platform == "macos"
    ready = (
        docker["ready"] is True
        and free_space_ready
        and host_supported
        and source_lock_matches
        and not changed
    )
    if changed:
        safe_next_action = "clean-or-use-clean-project-worktree"
    elif not source_lock_matches:
        safe_next_action = "create-a-new-workspace-for-this-source-lock"
    elif not free_space_ready:
        safe_next_action = "free-build-volume-space"
    elif not host_supported:
        safe_next_action = "use-supported-macos-build-host"
    elif docker["ready"] is not True:
        safe_next_action = "install-or-start-docker"
    else:
        safe_next_action = "prepare-local-build-inputs"
    return {
        "build_count": build_count,
        "build_root": str(root),
        "cache_root": str(root / "cache"),
        "created_at": manifest["created_at"],
        "created_from_head": manifest["created_from_head"],
        "current_head": head,
        "docker": docker,
        "free_bytes": usage.free,
        "free_gib": round(usage.free / BYTES_PER_GIB, 1),
        "free_space_ready": free_space_ready,
        "host_platform": platform,
        "host_supported": host_supported,
        "project_dirty": bool(changed),
        "project_status": changed,
        "ready_to_build": ready,
        "required_free_bytes": required,
        "required_free_gib": required // BYTES_PER_GIB,
        "runs_root": str(root / "runs"),
        "safe_next_action": safe_next_action,
        "sources_lock_matches": source_lock_matches,
    }


def prepare_local_build_workspace(
    *, build_root: Path, build_count: int = 2
) -> dict[str, object]:
    """Create a private, reusable local-build root or validate an exact rerun."""

    project_root = _project_root().resolve(strict=True)
    root, ancestor = _resolve_build_root(build_root, project_root=project_root)
    required = _required_free_bytes(build_count)
    if shutil.disk_usage(ancestor).free < required:
        raise LocalBuildError(
            f"build volume has less than {required // BYTES_PER_GIB} GiB free"
        )
    if root.exists() or root.is_symlink():
        if root.is_symlink() or not root.is_dir():
            raise LocalBuildError("build root already exists and is not a real directory")
        entries = list(root.iterdir())
        if entries:
            manifest = _validate_owned_tree(root)
            if manifest["build_count"] != build_count:
                raise LocalBuildError(
                    "existing local build workspace uses another build count"
                )
            return _workspace_result(root, manifest)
    else:
        root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    for relative in (
        "cache",
        "cache/downloads",
        "cache/images",
        "cache/sources",
        "runs",
    ):
        directory = root / relative
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    head = _git(project_root, "rev-parse", "HEAD")
    document = {
        "build_count": build_count,
        "build_root": str(root),
        "cache_policy": "public-inputs-only",
        "created_at": _now(),
        "created_from_head": head,
        "project": PROJECT_ID,
        "schema_version": SCHEMA_VERSION,
        "sources_lock_sha256": _sha256(project_root / "sources.lock.json"),
    }
    atomic_write(
        root / WORKSPACE_NAME,
        (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
    )
    (root / WORKSPACE_NAME).chmod(0o600)
    return _workspace_result(root, document)


def local_build_workspace_status(*, build_root: Path) -> dict[str, object]:
    """Inspect an existing local-build root without modifying it."""

    project_root = _project_root().resolve(strict=True)
    root, _ = _resolve_build_root(build_root, project_root=project_root)
    manifest = _validate_owned_tree(root)
    return _workspace_result(root, manifest)


def remember_local_build_workspace(*, build_root: Path, work_dir: Path) -> Path:
    """Record one non-secret workspace path for later guided commands."""

    root = Path(
        str(local_build_workspace_status(build_root=build_root)["build_root"])
    )
    state = _absolute(work_dir)
    _reject_existing_symlink_components(state)
    if state.exists() or state.is_symlink():
        if state.is_symlink() or not state.is_dir():
            raise LocalBuildError("installer work directory is not a real directory")
    else:
        state.mkdir(parents=True, mode=0o700)
    os.chmod(state, 0o700)
    manifest_path = root / WORKSPACE_NAME
    pointer = {
        "build_root": str(root),
        "project": PROJECT_ID,
        "schema_version": SCHEMA_VERSION,
        "workspace_manifest_sha256": _sha256(manifest_path),
    }
    destination = state / WORKSPACE_POINTER_NAME
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise LocalBuildError("local build workspace pointer is not a regular file")
    atomic_write(
        destination,
        (json.dumps(pointer, indent=2, sort_keys=True) + "\n").encode(),
    )
    destination.chmod(0o600)
    return destination


def resolve_local_build_workspace(
    *,
    build_root: Path | None,
    work_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve an explicit, environment, or prepare-recorded workspace path."""

    values = os.environ if environment is None else environment
    environment_value = values.get(BUILD_ROOT_ENVIRONMENT)
    if environment_value is not None and not environment_value.strip():
        raise LocalBuildError(f"{BUILD_ROOT_ENVIRONMENT} is empty")
    environment_root = Path(environment_value).expanduser() if environment_value else None
    if environment_root is not None and not environment_root.is_absolute():
        raise LocalBuildError(f"{BUILD_ROOT_ENVIRONMENT} must be an absolute path")
    if build_root is not None and environment_root is not None:
        explicit = _absolute(build_root).resolve(strict=False)
        selected = environment_root.resolve(strict=False)
        if explicit != selected:
            raise LocalBuildError(
                f"--build-root and {BUILD_ROOT_ENVIRONMENT} identify different workspaces"
            )
    selected_root = build_root or environment_root
    if selected_root is not None:
        return _absolute(selected_root)

    pointer_path = _absolute(work_dir) / WORKSPACE_POINTER_NAME
    if pointer_path.is_symlink() or not pointer_path.is_file():
        raise LocalBuildError(
            "local build workspace is unknown; run local-build prepare with "
            f"--build-root or {BUILD_ROOT_ENVIRONMENT}"
        )
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBuildError("local build workspace pointer is invalid") from exc
    required = {
        "build_root",
        "project",
        "schema_version",
        "workspace_manifest_sha256",
    }
    if not isinstance(pointer, dict) or set(pointer) != required:
        raise LocalBuildError("local build workspace pointer fields are invalid")
    if (
        pointer.get("schema_version") != SCHEMA_VERSION
        or pointer.get("project") != PROJECT_ID
    ):
        raise LocalBuildError("local build workspace pointer targets another project")
    root_value = pointer.get("build_root")
    digest = pointer.get("workspace_manifest_sha256")
    if (
        not isinstance(root_value, str)
        or not Path(root_value).is_absolute()
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise LocalBuildError("local build workspace pointer identity is invalid")
    root = Path(root_value)
    manifest_path = root / WORKSPACE_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise LocalBuildError("recorded local build workspace no longer exists")
    if _sha256(manifest_path) != digest:
        raise LocalBuildError("recorded local build workspace identity changed")
    return root


def default_local_build_private_root(build_root: Path) -> Path:
    """Return the conventional private-input directory beside a build root."""

    root = _absolute(build_root).resolve(strict=False)
    return root.with_name(f"{root.name}-private")


def _private_root(path: Path) -> Path:
    absolute = _absolute(path)
    _reject_existing_symlink_components(absolute)
    if absolute.exists() or absolute.is_symlink():
        if absolute.is_symlink() or not absolute.is_dir():
            raise LocalBuildError("private input root is not a real directory")
        root = absolute.resolve(strict=True)
    else:
        absolute.mkdir(parents=True, mode=0o700)
        root = absolute.resolve(strict=True)
    metadata = root.stat(follow_symlinks=False)
    if metadata.st_uid != os.geteuid():
        raise LocalBuildError("private input root is not owned by the current user")
    if metadata.st_mode & 0o077:
        raise LocalBuildError(
            "private input root is accessible outside its owner; set its mode to 0700"
        )
    return root


def _input_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise LocalBuildError(f"{label} is symlinked")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LocalBuildError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise LocalBuildError(f"{label} is not a directory")
    return resolved


def _input_file(path: Path, label: str, *, limit: int | None = None) -> Path:
    if path.is_symlink():
        raise LocalBuildError(f"{label} is symlinked")
    try:
        resolved = path.expanduser().resolve(strict=True)
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalBuildError(f"{label} is missing") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise LocalBuildError(f"{label} is not a regular file")
    if limit is not None and metadata.st_size > limit:
        raise LocalBuildError(f"{label} exceeds its size limit")
    return resolved


def _settings_document(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    private_config_dir: Path,
    expected_wpa_config: Path,
    session_dir: Path,
    raptor_rwd_artifact: Path,
    data_mode: str,
) -> dict[str, object]:
    return {
        "build_root": str(build_root),
        "data_mode": data_mode,
        "expected_wpa_config": str(expected_wpa_config),
        "media_closure_dir": str(media_closure_dir),
        "private_config_dir": str(private_config_dir),
        "project": PROJECT_ID,
        "raptor_rwd_artifact": str(raptor_rwd_artifact),
        "schema_version": SCHEMA_VERSION,
        "session_dir": str(session_dir),
        "vendor_bundle_dir": str(vendor_bundle_dir),
    }


def _validated_settings_document(document: object) -> dict[str, object]:
    fields = {
        "build_root",
        "data_mode",
        "expected_wpa_config",
        "media_closure_dir",
        "private_config_dir",
        "project",
        "raptor_rwd_artifact",
        "schema_version",
        "session_dir",
        "vendor_bundle_dir",
    }
    if not isinstance(document, dict) or set(document) != fields:
        raise LocalBuildError("local build settings fields are invalid")
    if (
        document.get("schema_version") != SCHEMA_VERSION
        or document.get("project") != PROJECT_ID
        or document.get("data_mode") not in DATA_MODES
    ):
        raise LocalBuildError("local build settings target or data mode is invalid")
    for field in fields - {"data_mode", "project", "schema_version"}:
        value = document.get(field)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise LocalBuildError(f"local build settings path is invalid: {field}")
    return document


def _read_settings_file(path: Path) -> tuple[Path, dict[str, object]]:
    resolved = _input_file(path, "local build settings", limit=32 * 1024)
    metadata = resolved.stat(follow_symlinks=False)
    if metadata.st_uid != os.geteuid():
        raise LocalBuildError("local build settings are not owned by the current user")
    if metadata.st_mode & 0o077:
        raise LocalBuildError("local build settings are accessible outside their owner")
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBuildError("local build settings are invalid JSON") from exc
    return resolved, _validated_settings_document(document)


def remember_local_build_settings(*, settings_path: Path, work_dir: Path) -> Path:
    """Remember one private settings file without copying its values."""

    resolved, _ = _read_settings_file(settings_path)
    state = _absolute(work_dir)
    _reject_existing_symlink_components(state)
    if state.exists() or state.is_symlink():
        if state.is_symlink() or not state.is_dir():
            raise LocalBuildError("installer work directory is not a real directory")
    else:
        state.mkdir(parents=True, mode=0o700)
    os.chmod(state, 0o700)
    pointer = {
        "project": PROJECT_ID,
        "schema_version": SCHEMA_VERSION,
        "settings_path": str(resolved),
        "settings_sha256": _sha256(resolved),
    }
    destination = state / SETTINGS_POINTER_NAME
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise LocalBuildError("local build settings pointer is not a regular file")
    atomic_write(
        destination,
        (json.dumps(pointer, indent=2, sort_keys=True) + "\n").encode(),
    )
    destination.chmod(0o600)
    return destination


def load_local_build_settings(
    *, settings_path: Path | None, work_dir: Path
) -> dict[str, object]:
    """Load explicit or configure-recorded local-build inputs."""

    selected = settings_path
    expected_sha256: str | None = None
    if selected is None:
        state = _absolute(work_dir)
        _reject_existing_symlink_components(state)
        pointer_path = state / SETTINGS_POINTER_NAME
        if pointer_path.is_symlink() or not pointer_path.is_file():
            raise LocalBuildError(
                "local build inputs are not configured; run local-build configure"
            )
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalBuildError("local build settings pointer is invalid") from exc
        fields = {"project", "schema_version", "settings_path", "settings_sha256"}
        if (
            not isinstance(pointer, dict)
            or set(pointer) != fields
            or pointer.get("project") != PROJECT_ID
            or pointer.get("schema_version") != SCHEMA_VERSION
            or not isinstance(pointer.get("settings_path"), str)
            or not Path(str(pointer["settings_path"])).is_absolute()
            or not isinstance(pointer.get("settings_sha256"), str)
            or len(str(pointer["settings_sha256"])) != 64
            or any(
                character not in "0123456789abcdef"
                for character in str(pointer["settings_sha256"])
            )
        ):
            raise LocalBuildError("local build settings pointer fields are invalid")
        selected = Path(str(pointer["settings_path"]))
        expected_sha256 = str(pointer["settings_sha256"])
    assert selected is not None
    resolved, document = _read_settings_file(selected)
    if expected_sha256 is not None and _sha256(resolved) != expected_sha256:
        raise LocalBuildError("recorded local build settings changed")
    return {**document, "settings_path": str(resolved)}


def validate_local_build_setting_inputs(
    *,
    build_root: Path,
    private_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    session_dir: Path,
    raptor_rwd_artifact: Path,
) -> dict[str, object]:
    """Validate every non-secret guided input before asking for Wi-Fi."""

    from scripts.raptor_rwd_runtime import RaptorRwdRuntimeError, validate_artifact

    from .media_closure import MediaClosureError, load_media_closure
    from .recovery_ap.host import (
        RecoveryApHostError,
        load_host_session,
        load_service_credential,
    )
    from .vendor_bundle import VendorBundleError, load_vendor_bundle

    workspace = local_build_workspace_status(build_root=build_root)
    root = _private_root(private_root)
    for path, label in (
        (root / "install-config", "private install configuration"),
        (root / "expected-wpa.conf", "expected station Wi-Fi configuration"),
        (root / SETTINGS_NAME, "local build settings"),
    ):
        if path.exists() or path.is_symlink():
            raise LocalBuildError(f"refusing to overwrite existing {label}")
    vendor = _input_directory(vendor_bundle_dir, "private vendor bundle")
    media = _input_directory(media_closure_dir, "private media closure")
    session = _input_directory(session_dir, "private recovery session")
    artifact = _input_file(
        raptor_rwd_artifact,
        "Raptor RWD artifact",
        limit=128 * 1024 * 1024,
    )
    try:
        load_vendor_bundle(vendor)
        closure = load_media_closure(media)
        if closure.by_path().get("lib/libaudioProcess.so") is None:
            raise LocalBuildError("private media closure lacks libaudioProcess.so")
        load_host_session(session)
        credential = load_service_credential(session) + b"\n"
        validate_artifact(
            artifact,
            expected_sha256=_sha256(artifact),
            supervisor=_project_root() / "components/raptor-rwd/S13prudynt-rwd",
        )
    except (
        VendorBundleError,
        MediaClosureError,
        RecoveryApHostError,
        RaptorRwdRuntimeError,
    ) as exc:
        raise LocalBuildError(str(exc)) from exc
    return {
        "build_root": Path(str(workspace["build_root"])),
        "credential": credential,
        "media_closure_dir": media,
        "private_root": root,
        "raptor_rwd_artifact": artifact,
        "session_dir": session,
        "vendor_bundle_dir": vendor,
    }


def configure_local_build_settings(
    *,
    build_root: Path,
    work_dir: Path,
    private_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    session_dir: Path,
    raptor_rwd_artifact: Path,
    data_mode: str,
    ssid: str,
    passphrase: str,
    confirmation_ssid: str,
    confirmation_passphrase: str,
) -> dict[str, object]:
    """Validate private inputs and store a session-bound guided build plan."""

    from .private_config import (
        PrivateConfigError,
        generate_private_config,
        load_private_config_for_session,
        load_private_wpa_config,
        render_private_wpa_config,
    )
    if data_mode not in DATA_MODES:
        raise LocalBuildError("data mode is invalid")
    validated = validate_local_build_setting_inputs(
        build_root=build_root,
        private_root=private_root,
        vendor_bundle_dir=vendor_bundle_dir,
        media_closure_dir=media_closure_dir,
        session_dir=session_dir,
        raptor_rwd_artifact=raptor_rwd_artifact,
    )
    canonical_build_root = Path(str(validated["build_root"]))
    root = Path(str(validated["private_root"]))
    vendor = Path(str(validated["vendor_bundle_dir"]))
    media = Path(str(validated["media_closure_dir"]))
    session = Path(str(validated["session_dir"]))
    artifact = Path(str(validated["raptor_rwd_artifact"]))
    credential = validated["credential"]
    if not isinstance(credential, bytes):
        raise LocalBuildError("private recovery credential is invalid")
    try:
        primary_wpa = render_private_wpa_config(ssid=ssid, passphrase=passphrase)
        confirmed_wpa = render_private_wpa_config(
            ssid=confirmation_ssid,
            passphrase=confirmation_passphrase,
        )
    except PrivateConfigError as exc:
        raise LocalBuildError(str(exc)) from exc
    if primary_wpa != confirmed_wpa:
        raise LocalBuildError("station Wi-Fi confirmation does not match")

    private_config = root / "install-config"
    expected_wpa = root / "expected-wpa.conf"
    settings_path = root / SETTINGS_NAME
    for path, label in (
        (private_config, "private install configuration"),
        (expected_wpa, "expected station Wi-Fi configuration"),
        (settings_path, "local build settings"),
    ):
        if path.exists() or path.is_symlink():
            raise LocalBuildError(f"refusing to overwrite existing {label}")

    staging = Path(tempfile.mkdtemp(prefix=".local-build-configure-", dir=root))
    staging.chmod(0o700)
    moved: list[Path] = []
    try:
        staged_config = staging / "install-config"
        generate_private_config(
            output_dir=staged_config,
            ssid=ssid,
            passphrase=passphrase,
            authorized_key=(session / "host/identity.pub").read_bytes(),
            credential=credential,
        )
        staged_expected = staging / "expected-wpa.conf"
        atomic_write(staged_expected, confirmed_wpa, mode=0o600)
        document = _settings_document(
            build_root=canonical_build_root,
            vendor_bundle_dir=vendor,
            media_closure_dir=media,
            private_config_dir=private_config,
            expected_wpa_config=expected_wpa,
            session_dir=session,
            raptor_rwd_artifact=artifact,
            data_mode=data_mode,
        )
        staged_settings = staging / SETTINGS_NAME
        atomic_write(
            staged_settings,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o600,
        )
        for source, destination in (
            (staged_config, private_config),
            (staged_expected, expected_wpa),
            (staged_settings, settings_path),
        ):
            os.replace(source, destination)
            moved.append(destination)
        load_private_config_for_session(output_dir=private_config, session_dir=session)
        load_private_wpa_config(
            expected_wpa,
            independent_from=private_config / "wpa_supplicant.conf",
        )
        pointer = remember_local_build_settings(
            settings_path=settings_path,
            work_dir=work_dir,
        )
    except BaseException:
        for path in reversed(moved):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "configured": True,
        "data_mode": data_mode,
        "expected_wpa_config": str(expected_wpa),
        "private_config_dir": str(private_config),
        "settings_path": str(settings_path),
        "settings_pointer": str(pointer),
        "station_psk_derived": True,
    }


def host_platform() -> str:
    """Return the supported local-build host family for stable reporting."""

    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return "unsupported"
