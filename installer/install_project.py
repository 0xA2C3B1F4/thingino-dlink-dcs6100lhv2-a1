"""Private path selections and advisory completion records for one camera.

This file never grants permission to write and never replaces an artifact,
recovery, signature, or current-media validator.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import re
from dataclasses import dataclass
from pathlib import Path

from .sd_package import atomic_write


class ProjectError(ValueError):
    def __init__(self, code: str, message: str, missing: tuple[str, ...] = ()):
        super().__init__(message)
        self.code = code
        self.missing = missing


# Only paths can be remembered. Physical devices, confirmations, credentials,
# data actions, executable paths and a current independent WPA confirmation
# must always come from the current invocation.
PATH_FIELDS = frozenset({
    "build-root", "work-dir", "private-root", "vendor-bundle-dir",
    "media-closure-dir", "session-dir", "raptor-rwd-artifact", "settings",
    "private-config-dir", "recovery-dir", "functional-recovery-dir",
    "preserved-readback-dir", "install-set-dir", "universal-bundle",
    "universal-public-key", "provisioning", "provisioning-data",
    "provisioning-public-key", "authorization-dir", "authorization-public-key",
    "output-dir", "config-output-dir", "output", "data-output", "input-dir",
    "collector-dir", "package", "package-manifest", "kernel", "linux-config",
    "mmc-module", "restore-output-dir",
    "signing-key", "signing-public-key",
})
FAMILIES = frozenset({"local-build", "stock-recovery", "universal"})
UNTRACKED = frozenset({"build-root", "work-dir", "private-root", "collector-dir"})
OUTPUTS = frozenset({"output-dir", "config-output-dir", "output", "data-output",
                     "restore-output-dir"})


@dataclass(frozen=True)
class Project:
    path: Path
    name: str
    selections: dict[str, dict[str, str]]
    records: dict[str, dict[str, object]]


UNIVERSAL_RECOVERY_STEPS = ("universal init-session", "universal provision", "universal authorize", "universal stage", "universal handoff")
ROLE_COMMANDS = {
    "functional-recovery-dir": UNIVERSAL_RECOVERY_STEPS,
    "recovery-dir": UNIVERSAL_RECOVERY_STEPS[1:],
    "preserved-readback-dir": UNIVERSAL_RECOVERY_STEPS,
    "vendor-bundle-dir": ("local-build build-universal", "local-build configure"),
    "install-set-dir": ("universal stage", "universal handoff"),
    "universal-bundle": ("universal provision", "universal authorize"),
    "universal-public-key": ("universal provision", "universal authorize", "universal stage", "universal handoff"),
    "session-dir": tuple("universal " + name for name in ("configure", "provision", "authorize", "stage", "handoff", "verify")),
    "private-config-dir": ("universal provision",),
    "provisioning": ("universal authorize", "universal stage", "universal handoff"),
    "provisioning-data": ("universal authorize", "universal stage", "universal handoff"),
    "authorization-dir": ("universal stage", "universal handoff"),
    "authorization-public-key": ("universal stage", "universal handoff"),
    "provisioning-public-key": ("universal authorize",),
    "signing-key": ("universal provision", "universal authorize"),
    "package": tuple("stock-recovery " + name for name in ("uartless-prepare", "uartless-authorize", "uartless-handoff", "uartless-validate")),
    "package-manifest": tuple("stock-recovery " + name for name in ("uartless-prepare", "uartless-authorize", "uartless-handoff")),
    "kernel": ("stock-recovery backup-prepare",),
    "linux-config": ("stock-recovery backup-prepare", "stock-recovery backup-capture"),
    "mmc-module": ("stock-recovery backup-prepare",),
    "settings": ("local-build build",),
    "raptor-rwd-artifact": ("local-build build-universal",),
    "media-closure-dir": ("local-build build-universal",),
}


def default_selections(path: Path, build_root: Path | None = None) -> dict[str, dict[str, str]]:
    root = path.parent / (path.stem + "-private")
    selections = {
        "stock-recovery uartless-validate": {"output-dir": str(root / "recovery")},
        "universal init-session": {"output-dir": str(root / "session"), "config-output-dir": str(root / "config")},
        "universal configure": {"output-dir": str(root / "config")},
        "universal provision": {"output": str(root / "provisioning.zip"), "data-output": str(root / "provisioning.jffs2")},
        "universal authorize": {"output-dir": str(root / "authorization")},
        "universal evacuate-recovery": {"output-dir": str(root / "evacuated-stock")},
    }
    if build_root is not None:
        for name in ("prepare", "status", "bootstrap", "acquire", "recovery-assets", "configure", "build", "build-universal"):
            selections["local-build " + name] = {"build-root": str(build_root.expanduser().resolve())}
    return selections


def link_inputs(project: Project, inputs: dict[str, str], *, replace_existing: bool = False) -> None:
    for role, value in inputs.items():
        if role not in ROLE_COMMANDS:
            raise ProjectError("invalid_input", "unsupported project input role")
        for command in ROLE_COMMANDS[role]:
            paths = project.selections.setdefault(command, {})
            if role in paths and Path(paths[role]).resolve() != Path(value).resolve() and not replace_existing:
                raise ProjectError("project_conflict", "accepted output differs from a selected downstream input")
            paths[role] = str(Path(value).expanduser().resolve())


def attach_inputs(project: Project, inputs: dict[str, Path]) -> dict[str, object]:
    import copy
    updated = Project(project.path, project.name, copy.deepcopy(project.selections), copy.deepcopy(project.records))
    for value in inputs.values():
        fingerprint(value)
    paths = {key: str(value.expanduser().resolve()) for key, value in inputs.items()}
    if "install-set-dir" in paths and "universal-bundle" not in paths:
        paths["universal-bundle"] = str(Path(paths["install-set-dir"]) / "thingino-universal.tgb")
        fingerprint(Path(paths["universal-bundle"]))
    recovery = paths.get("functional-recovery-dir") or paths.get("recovery-dir")
    if recovery and "preserved-readback-dir" not in paths:
        paths["preserved-readback-dir"] = str(Path(recovery) / "preserved")
    link_inputs(updated, paths, replace_existing=True)
    validate_camera_selection(updated)
    # Explicit attachments select external host outputs, not a repeated run.
    # Never retire interrupted or media-write records.
    replaced = []
    output_roles = {
        "universal init-session": {"session-dir"},
        "universal configure": {"private-config-dir"},
        "universal provision": {"provisioning", "provisioning-data"},
        "universal authorize": {"authorization-dir"},
    }
    aliases = {"output": "provisioning", "provisioning_data_output": "provisioning-data"}
    for command, required in output_roles.items():
        record = updated.records.get(command, {})
        if record.get("state") != "completed" or not required.issubset(paths):
            continue
        # Selecting config alone cannot dismiss a changed signer. Other output
        # artifacts must remain intact or also be explicitly reselected.
        try:
            intact = all(aliases.get(role, role.replace("_", "-")) in paths
                         or tracked_fingerprint(role, Path(artifact["path"])) == artifact["sha256"]
                         for role, artifact in record.get("artifacts", {}).items())
        except (OSError, ProjectError):
            intact = False
        if intact:
            del updated.records[command]
            replaced.append(command)
    save_project(updated)
    return {**project_status(updated), "superseded_host_records": replaced}


def begin_operation(project: Project, command: str, selected: dict[str, str]) -> tuple[Project, str, dict[str, str]]:
    selected_paths(project, command, selected)
    project.selections[command] = selected
    validate_camera_selection(project)
    inputs = snapshot(selected)
    project.records[command] = {"state": "started", "inputs": inputs}
    save_project(project)
    return project, command, inputs


def select_project(explicit: list[str], environment: str | None) -> Path | None:
    if any(Path(value).expanduser().is_symlink() for value in explicit + ([environment] if environment else [])):
        raise ProjectError("invalid_project", "project cannot be a symlink")
    values = [Path(value).expanduser().resolve() for value in explicit]
    if environment:
        values.append(Path(environment).expanduser().resolve())
    if len(set(values)) > 1:
        raise ProjectError("project_conflict", "project selections disagree")
    return values[0] if values else None


def validate_selections(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ProjectError("invalid_project", "selections must be an object")
    for command, paths in value.items():
        if (not isinstance(command, str) or len(command.split()) != 2
                or command.split()[0] not in FAMILIES or not isinstance(paths, dict)):
            raise ProjectError("invalid_project", "select paths for an installation subcommand")
        for key, path in paths.items():
            if key not in PATH_FIELDS or not isinstance(path, str) or not Path(path).is_absolute():
                raise ProjectError("invalid_project", "selections require allowed absolute paths")
        if "recovery-dir" in paths and "functional-recovery-dir" in paths:
            raise ProjectError("project_conflict", "select exactly one recovery class")
    return value


def _read(path: Path) -> dict[str, object]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size > 1024 * 1024):
                raise ProjectError("invalid_project", "project must be a private bounded regular file")
            value = json.load(stream)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError("invalid_project", "cannot read project document") from exc
    if not isinstance(value, dict):
        raise ProjectError("invalid_project", "project must be an object")
    return value


def load_project(path: Path) -> Project:
    value = _read(path)
    if (type(value.get("schema_version")) is not int or value.get("schema_version") != 1 or not isinstance(value.get("name"), str)
            or not isinstance(value.get("records"), dict)):
        raise ProjectError("invalid_project", "unsupported project document")
    validate_records(value["records"])
    return Project(path, value["name"], validate_selections(value.get("selections")), value["records"])


def validate_records(records: object) -> None:
    if not isinstance(records, dict):
        raise ProjectError("invalid_project", "records must be an object")
    def digest(value: object) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    for command, record in records.items():
        if (not isinstance(command, str) or len(command.split()) != 2 or command.split()[0] not in FAMILIES
                or not isinstance(record, dict)
                or record.get("state") not in ("started", "completed")
                or set(record) - {"state", "inputs", "outputs", "artifacts"}):
            raise ProjectError("invalid_project", "invalid completion record")
        for field in ("inputs", "outputs"):
            values = record.get(field, {} if field == "outputs" and record["state"] == "started" else None)
            if not isinstance(values, dict) or any(key not in PATH_FIELDS or not digest(value) for key, value in values.items()):
                raise ProjectError("invalid_project", "invalid recorded content identities")
        artifacts = record.get("artifacts", {})
        if not isinstance(artifacts, dict):
            raise ProjectError("invalid_project", "artifacts must be an object")
        for key, artifact in artifacts.items():
            if (not isinstance(key, str) or not isinstance(artifact, dict)
                    or set(artifact) != {"path", "sha256"}
                    or not isinstance(artifact["path"], str)
                    or not Path(artifact["path"]).is_absolute()
                    or not digest(artifact["sha256"])):
                raise ProjectError("invalid_project", "invalid recorded artifact")


def save_project(project: Project) -> None:
    if project.path.is_symlink():
        raise ProjectError("invalid_project", "project cannot be a symlink")
    atomic_write(project.path, (json.dumps({
        "schema_version": 1, "name": project.name,
        "selections": project.selections, "records": project.records,
    }, sort_keys=True, indent=2) + "\n").encode(), mode=0o600)


def init_project(path: Path, *, name: str, selections: dict[str, dict[str, str]] | None = None,
                 build_root: Path | None = None) -> Project:
    if path.exists() or path.is_symlink():
        raise ProjectError("project_exists", "refusing to replace a project")
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ProjectError("invalid_project", "provide a short local camera label")
    selected = default_selections(path, build_root) if selections is None else selections
    project = Project(path, name, validate_selections(selected), {})
    private_root = path.parent / (path.stem + "-private")
    if any(str(private_root) in value for paths in selected.values() for value in paths.values()):
        if private_root.exists():
            if private_root.is_symlink() or not private_root.is_dir() or private_root.stat().st_mode & 0o077:
                raise ProjectError("invalid_project", "project private root must be a private directory")
        else:
            private_root.mkdir(parents=True, mode=0o700)
    save_project(project)
    return project


def selected_paths(project: Project, command: str, explicit: dict[str, str]) -> dict[str, str]:
    selected = dict(project.selections.get(command, {}))
    for key, value in explicit.items():
        if key in selected and Path(value).expanduser().resolve() != Path(selected[key]).resolve():
            raise ProjectError("project_conflict", f"explicit --{key} differs from project")
    return {key: value for key, value in selected.items() if key not in explicit}


def fingerprint(path: Path, *, _exclude: frozenset[str] = frozenset()) -> str:
    """Bounded content identity; no content or filenames escape into events."""
    digest = hashlib.sha256()
    remaining = 256 * 1024 * 1024
    entries = 0
    def visit(current: Path, relative: str) -> None:
        nonlocal remaining, entries
        entries += 1
        if entries > 4096:
            raise ProjectError("input_too_large", "project evidence contains too many entries")
        info = current.lstat()
        digest.update(relative.encode() + b"\0")
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"directory\0")
            for child in sorted(current.iterdir()):
                child_relative = relative + "/" + child.name
                if child_relative not in _exclude:
                    visit(child, child_relative)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            digest.update(b"file\0" + str(info.st_size).encode() + b"\0")
            remaining -= info.st_size
            if remaining < 0:
                raise ProjectError("input_too_large", "project evidence exceeds tracking limit")
            with current.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            after = current.stat()
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                raise ProjectError("changed_input", "project evidence changed while reading")
        else:
            raise ProjectError("invalid_input", "project evidence must not contain links or special files")
    visit(path, "")
    return digest.hexdigest()


def validate_camera_selection(project: Project) -> None:
    """Revalidate available recovery evidence, never trust a stored camera ID."""
    from .recovery_gate import validate_existing_recovery_boundary, validate_functional_recovery_boundary
    identities = set()
    checked = set()
    for paths in project.selections.values():
        readback = paths.get("preserved-readback-dir")
        for field, validator in (("recovery-dir", validate_existing_recovery_boundary),
                                 ("functional-recovery-dir", validate_functional_recovery_boundary)):
            recovery = paths.get(field)
            pair = (field, recovery, readback)
            if not recovery or not readback or pair in checked:
                continue
            checked.add(pair)
            if not Path(recovery).exists() or not Path(readback).exists():
                continue
            decision = validator(recovery_dir=Path(recovery), preserved_readback_dir=Path(readback))
            identities.add(decision.camera_identity_sha256)
    if len(identities) > 1:
        raise ProjectError("project_conflict", "project recovery selections belong to different cameras")


def session_fingerprint(path: Path) -> str:
    """Track all session inputs except the pin derived from its retained host key.

    Pin validation belongs to verify, which records that derived file separately.
    The manifest, camera binding, private/public keys and every other file remain
    immutable inputs. Old whole-tree records conservatively become needs-review.
    """
    from .recovery_ap.host import load_host_session, load_service_credential
    try:
        session = load_host_session(path)
        if session.session_kind == "uartless-functional-provisioning":
            load_service_credential(path)
            return fingerprint(path, _exclude=frozenset({"/host/station_known_hosts"}))
        return fingerprint(path)
    except ValueError as exc:
        raise ProjectError("invalid_input", "selected session binding is invalid") from exc


def tracked_fingerprint(role: str, path: Path) -> str:
    manifest = path / "host/session.json"
    if role in {"session-dir", "session_dir"} and (manifest.exists() or manifest.is_symlink()):
        return session_fingerprint(path)
    return fingerprint(path)


def snapshot(paths: dict[str, str], *, outputs: bool = False, command: str = "") -> dict[str, str]:
    result = {}
    for key, value in paths.items():
        if outputs and command == "universal init-session" and key == "config-output-dir":
            continue  # configure creates this later; init-session only names it
        if key in UNTRACKED or (key in OUTPUTS) != outputs:
            continue
        try:
            role = "session-dir" if command == "universal init-session" and key == "output-dir" else key
            result[key] = tracked_fingerprint(role, Path(value))
        except FileNotFoundError as exc:
            raise ProjectError("missing_input", "selected project input or output is missing", ("--" + key,)) from exc
    return result


def finish_operation(active: tuple[Project, str, dict[str, str]] | None, document: dict[str, object]) -> None:
    if active is None or document.get("ok") is not True:
        return
    project, command, inputs = active
    paths = project.selections.get(command, {})
    # A successful command is not a physical boot acknowledgment. Changed
    # dependencies invalidate the advisory record, even after success.
    if snapshot(paths) != inputs:
        raise ProjectError("changed_input", "inputs changed during the operation; inspect its outputs")
    result = document.get("result", {})
    artifacts = {}
    for key in ("install_set_dir", "collector_assets_dir", "session_dir", "private_config_dir",
                "authorization_dir", "output", "provisioning_data_output", "settings_path", "station_host_pin"):
        value = result.get(key)
        if isinstance(value, str):
            artifacts[key] = {"path": value, "sha256": tracked_fingerprint(key, Path(value))}
    linked = {}
    result_roles = {
        "session_dir": "session-dir", "private_config_dir": "private-config-dir",
        "authorization_dir": "authorization-dir", "install_set_dir": "install-set-dir",
        "functional_recovery_dir": "functional-recovery-dir", "recovery_dir": "recovery-dir",
        "preserved_readback_dir": "preserved-readback-dir", "vendor_bundle_dir": "vendor-bundle-dir",
        "settings_path": "settings", "universal_firmware": "universal-bundle",
    }
    for key, role in result_roles.items():
        if isinstance(result.get(key), str):
            linked[role] = result[key]
    if command == "local-build build":
        linked.pop("install-set-dir", None)  # personalized sets keep the legacy route
    if command == "universal provision":
        for key, role in (("output", "provisioning"), ("provisioning_data_output", "provisioning-data")):
            if isinstance(result.get(key), str):
                linked[role] = result[key]
    if command == "local-build recovery-assets":
        files = result.get("files", {})
        for key, role in (("uartless_package", "package"), ("uartless_package_manifest", "package-manifest"),
                          ("kernel", "kernel"), ("linux_config", "linux-config"), ("mmc_module", "mmc-module")):
            if isinstance(files.get(key), str):
                linked[role] = files[key]
    signer = result.get("signing", {}) if command == "universal configure" else result.get("model_signing", {})
    if isinstance(signer, dict) and isinstance(signer.get("public_key"), str):
        if command == "universal configure":
            linked.update({"signing-key": signer["private_key"],
                           "authorization-public-key": signer["public_key"],
                           "provisioning-public-key": signer["public_key"]})
        elif command == "local-build build-universal":
            linked["universal-public-key"] = signer["public_key"]
    if command == "local-build build-universal" and "install-set-dir" in linked:
        linked["universal-bundle"] = str(Path(linked["install-set-dir"]) / "thingino-universal.tgb")
    for role, value in linked.items():
        artifacts[role] = {"path": value, "sha256": tracked_fingerprint(role, Path(value))}
    link_inputs(project, linked)
    project.records[command] = {"state": "completed", "inputs": inputs,
                                "outputs": snapshot(paths, outputs=True, command=command), "artifacts": artifacts}
    save_project(project)


def project_status(project: Project) -> dict[str, object]:
    records = {}
    for command, record in project.records.items():
        current = False
        try:
            validate_records({command: record})
            paths = project.selections.get(command, {})
            current = (record.get("state") == "completed"
                       and record.get("inputs") == snapshot(paths)
                       and record.get("outputs") == snapshot(paths, outputs=True, command=command))
            for role, artifact in record.get("artifacts", {}).items():
                current = current and tracked_fingerprint(role, Path(artifact["path"])) == artifact["sha256"]
        except (OSError, ProjectError, AttributeError, KeyError, TypeError):
            current = False
        records[command] = {"state": "completed" if current else "needs-review"}
    next_action = next_project_action(project, records)
    return {"name": project.name, "records": records,
            "physical_boot_verified": False, "write_authorized": False,
            "next_action": next_action, "missing_prerequisites": next_action["missing_inputs"]}


def next_project_action(project: Project, records: dict[str, object]) -> dict[str, object]:
    for command, record in project.records.items():
        if isinstance(record, dict) and record.get("state") == "started":
            return {"command": "project status", "arguments": [], "writes": False,
                    "safe_next_action": "inspect-interrupted-operation", "operation": command,
                    "missing_inputs": ["independent review of outputs and physical state"]}
    # Selected downstream paths do not repair stale producer evidence.
    producer_order = ("universal init-session", "universal configure", "universal provision", "universal authorize")
    for command in (*producer_order, *(name for name in records if name not in producer_order)):
        if records.get(command, {}).get("state") == "needs-review":
            return {"command": "project status", "arguments": [], "writes": False,
                    "safe_next_action": "inspect-stale-operation", "operation": command,
                    "missing_inputs": [f"inspect changed or missing inputs and accepted outputs of {command}",
                                       "restore recorded files or explicitly attach reviewed replacement host outputs; inspect physical state before any media operation"]}
    selected = {key: value for paths in project.selections.values() for key, value in paths.items()}
    has_recovery = any(key in selected for key in ("recovery-dir", "functional-recovery-dir"))
    steps = []
    if not has_recovery:
        steps.extend(("local-build " + name, ("build-root",), False) for name in ("prepare", "bootstrap", "acquire", "recovery-assets"))
        steps.extend(("stock-recovery " + name, ("package", "package-manifest"), True)
                     for name in ("uartless-prepare", "uartless-authorize", "uartless-handoff"))
        steps.append(("stock-recovery uartless-validate", ("package", "output-dir"), False))
    if "universal-bundle" not in selected:
        steps.append(("local-build build-universal", ("build-root", "vendor-bundle-dir"), False))
    if "session-dir" not in selected:
        if "recovery-dir" in selected:
            steps.append(("project attach", ("session-dir",), False))
        else:
            steps.append(("universal init-session", ("functional-recovery-dir", "preserved-readback-dir", "output-dir"), False))
    if "private-config-dir" not in selected:
        steps.append(("universal configure", ("session-dir", "output-dir"), False))
    if "provisioning" not in selected or "provisioning-data" not in selected:
        steps.append(("universal provision", ("universal-bundle", "universal-public-key", "private-config-dir", "session-dir", "signing-key"), False))
    if "authorization-dir" not in selected:
        steps.append(("universal authorize", ("universal-bundle", "provisioning", "provisioning-data", "provisioning-public-key", "signing-key"), False))
    steps.extend((("universal stage", ("install-set-dir", "universal-public-key", "authorization-dir", "authorization-public-key"), True),
                  ("universal handoff", ("install-set-dir", "authorization-dir"), True),
                  ("universal verify", ("session-dir",), False)))
    for command, required, media_write in steps:
        if records.get(command, {}).get("state") == "completed":
            continue
        paths = project.selections.get(command, {})
        missing = ["--" + role for role in required if role not in paths
                   or (role not in OUTPUTS | UNTRACKED and not Path(paths[role]).exists())]
        if command in UNIVERSAL_RECOVERY_STEPS:
            evidence = paths.get("functional-recovery-dir") or paths.get("recovery-dir")
            if not evidence or not Path(evidence).exists():
                missing.append("same-camera recovery evidence")
            readback = paths.get("preserved-readback-dir")
            if not readback or not Path(readback).exists():
                missing.append("current protected-partition readbacks")
        if media_write:
            missing.extend(["current removable-media selection", "exact plan confirmations"])
        if command.endswith("handoff"):
            missing.append("operator-observed stock mtd1/mtd2 completion")
        if command == "universal configure":
            missing.append("confirmed private Wi-Fi input")
        return {"command": command, "arguments": ["--plan-only"] if media_write else [],
                "writes": False if media_write else command not in {"universal verify", "local-build status"},
                "safe_next_action": "inspect-plan" if media_write else "run-named-operation",
                "missing_inputs": missing}
    return {"command": "universal verify", "arguments": [], "writes": False,
            "safe_next_action": "verify-current-camera-state", "missing_inputs": []}
