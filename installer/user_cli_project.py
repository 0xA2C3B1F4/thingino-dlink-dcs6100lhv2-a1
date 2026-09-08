"""Argument and terminal adapter for explicit installation projects."""

from __future__ import annotations

import json
import argparse
import os
import stat
from pathlib import Path

from .install_project import (
    FAMILIES, PATH_FIELDS, ProjectError, init_project, load_project,
    project_status, save_project, select_project, selected_paths, snapshot, validate_camera_selection, fingerprint,
    default_selections, ROLE_COMMANDS, attach_inputs, begin_operation,
)


def register(parser, commands, facade):
    project = commands.add_parser("project", help="remember paths for one camera; never resume a write")
    children = project.add_subparsers(dest="project_command", required=True)
    for name in ("init", "attach", "status"):
        child = children.add_parser(name)
        facade._add_common(child, inherited=True)
        if name == "init":
            child.add_argument("--name", required=True, help="local camera label; no device identifier")
            child.add_argument("--paths", type=Path,
                               help="JSON command-to-path mapping; see installer-projects.md")
            child.add_argument("--build-root", type=Path,
                               help="remember this workspace for all local-build commands")
        if name == "attach":
            for role in ROLE_COMMANDS:
                child.add_argument("--" + role, type=Path, help="select this existing input once for its dependent stages")
        child.set_defaults(handler=lambda args: run(args, facade))


def run(args, facade):
    path = select_project(args.project or [], os.environ.get("DCS6100_PROJECT"))
    if path is None:
        raise ProjectError("missing_input", "select --project or DCS6100_PROJECT", ("--project",))
    if args.project_command == "init":
        selections = default_selections(path, args.build_root)
        if args.paths is not None:
            try:
                overrides = json.loads(args.paths.read_text())
                from .install_project import validate_selections
                validate_selections(overrides)
                for command, values in overrides.items():
                    if args.build_root is not None and "build-root" in values and Path(values["build-root"]).resolve() != args.build_root.expanduser().resolve():
                        raise ProjectError("project_conflict", "--paths and --build-root select different build workspaces")
                    selections.setdefault(command, {}).update(values)
            except ProjectError:
                raise
            except (OSError, ValueError) as exc:
                raise ProjectError("invalid_project", "cannot read path selections") from exc
        project = init_project(path, name=args.name, selections=selections)
    else:
        project = load_project(path)
    result = project_status(project)
    if args.project_command == "attach":
        selected = {role: getattr(args, role.replace("-", "_")) for role in ROLE_COMMANDS
                    if getattr(args, role.replace("-", "_")) is not None}
        if not selected:
            raise ProjectError("missing_input", "select at least one existing input to attach")
        result = attach_inputs(project, selected)
    return facade._document("project " + args.project_command, ok=True,
                            phase="project-" + args.project_command,
                            result=result)


def expand(raw: list[str]) -> list[str]:
    """Inject only remembered path options, leaving argparse's gates intact."""
    explicit_projects = []
    explicit_paths = {}
    for index, item in enumerate(raw):
        flag, equals, value = item.partition("=")
        if flag == "--project" or flag.removeprefix("--") in PATH_FIELDS:
            if not equals:
                if index + 1 == len(raw):
                    continue  # argparse reports the missing option value
                value = raw[index + 1]
            if flag == "--project":
                explicit_projects.append(value)
            else:
                key = flag.removeprefix("--")
                if key in explicit_paths and explicit_paths[key] != value:
                    raise ProjectError("project_conflict", f"conflicting --{key} selections")
                explicit_paths[key] = value
    path = select_project(explicit_projects, os.environ.get("DCS6100_PROJECT"))
    if path is None or "--help" in raw or "-h" in raw:
        return raw
    # Read the first positional token, skipping option values. Family names
    # appearing in a label or option value must never select a command.
    positionals = []
    index = 0
    boolean_options = {"--json", "--non-interactive", "--events-jsonl", "--plan-only", "--webrtc"}
    while index < len(raw):
        item = raw[index]
        if item.startswith("--"):
            index += 1 if "=" in item or item in boolean_options else 2
        else:
            positionals.append(index)
            index += 1
    family_index = positionals[0] if positionals and raw[positionals[0]] in FAMILIES else None
    if family_index is None:
        return raw
    if family_index + 1 >= len(raw):
        return raw
    command = " ".join(raw[family_index:family_index + 2])
    project = load_project(path)
    additions = selected_paths(project, command, explicit_paths)
    if command == "local-build build-universal" and "--webrtc" in raw:
        additions.pop("raptor-rwd-artifact", None)
    if "work-dir" not in explicit_paths and "work-dir" not in additions:
        additions["work-dir"] = str(path.parent / (path.stem + ".work"))
    if command.startswith("local-build ") and "build-root" not in explicit_paths and "build-root" not in additions:
        raise ProjectError("missing_input", "project requires an explicit build workspace", ("--build-root",))
    return raw + [part for key, value in additions.items() for part in ("--" + key, value)]


def require_noninteractive_inputs(args) -> None:
    if not args.non_interactive:
        return
    missing = []
    if args.command == "local-build" and args.local_build_command == "configure":
        for field in ("private_root", "vendor_bundle_dir", "media_closure_dir",
                      "session_dir", "raptor_rwd_artifact", "data_mode", "secrets_fd"):
            if getattr(args, field, None) is None:
                missing.append("--" + field.replace("_", "-"))
    if args.command == "universal" and args.universal_command == "configure":
        if args.secrets_fd is None:
            missing.append("--secrets-fd")
    # Other legacy interactive flows are rejected before side effects. They can
    # be migrated individually without pretending JSON alone is headless.
    if args.command == "install":
        raise ProjectError("noninteractive_unsupported", "legacy install requires an interactive operator")
    if missing:
        raise ProjectError("missing_input", "non-interactive inputs are incomplete", tuple(missing))
    descriptor = getattr(args, "secrets_fd", None)
    if descriptor is not None and not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise ProjectError("invalid_input", "non-interactive secrets require a regular file descriptor")
    args.json = True  # existing CLI prompts use this gate


def begin(args):
    path = select_project(args.project or [], os.environ.get("DCS6100_PROJECT"))
    if path is None or args.command not in FAMILIES or getattr(args, "plan_only", False):
        return None
    subcommand = getattr(args, {"local-build": "local_build_command",
                               "stock-recovery": "stock_command",
                               "universal": "universal_command"}[args.command])
    command = args.command + " " + subcommand
    if subcommand in {"status", "restore-inspect", "sd-confirmation"}:
        return None
    project = load_project(path)
    selected = dict(project.selections.get(command, {}))
    if command == "local-build build-universal" and getattr(args, "webrtc", False):
        selected.pop("raptor-rwd-artifact", None)
    for field in PATH_FIELDS:
        value = getattr(args, field.replace("-", "_"), None)
        if value is not None:
            selected[field] = str(Path(value).expanduser().resolve())
    return begin_operation(project, command, selected)


from .install_project import finish_operation as finish


def event(args, phase: str) -> None:
    if getattr(args, "events_jsonl", False):
        import sys
        print(json.dumps({"schema_version": 1, "event": phase,
                          "command": args.command}, sort_keys=True), file=sys.stderr, flush=True)


def missing_options(parser, raw: list[str]) -> list[str]:
    """Report parser requirements from actions, never from formatted errors."""
    provided = {item.split("=", 1)[0] for item in raw if item.startswith("--")}
    missing = []
    current = parser
    offset = 0
    while current is not None:
        for action in current._actions:
            if action.required and action.option_strings and not provided.intersection(action.option_strings):
                missing.append(action.option_strings[0])
        for group in current._mutually_exclusive_groups:
            choices = [option for action in group._group_actions for option in action.option_strings]
            if group.required and not provided.intersection(choices):
                missing.append(" | ".join(choices))
        children = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
        current = None
        if children:
            while offset < len(raw):
                token = raw[offset]
                offset += 1
                if token in children.choices:
                    current = children.choices[token]
                    break
                if token.startswith("--") and "=" not in token and token not in {
                        "--json", "--non-interactive", "--events-jsonl", "--plan-only", "--webrtc"}:
                    offset += 1
    return missing


def progress_callback(args):
    def emit(update: dict[str, object]) -> None:
        if getattr(args, "events_jsonl", False):
            import sys
            print(json.dumps({"schema_version": 1, "event": "progress", **update},
                             sort_keys=True), file=sys.stderr, flush=True)
    return emit
