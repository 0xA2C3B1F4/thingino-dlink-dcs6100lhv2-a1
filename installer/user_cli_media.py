"""Terminal selection and presentation for shared media operations."""

from __future__ import annotations

import json
from pathlib import Path

from .install_actions import WriteConfirmation, WritePlan
from .install_project import ProjectError
from .media_selection import discover_media


def select_media(arguments) -> None:
    whole = getattr(arguments, "whole_device", None)
    root = getattr(arguments, "mount_root", None)
    legacy = getattr(arguments, "media_preflight", None)
    if whole is None and legacy is not None and root is not None:
        from .media_preflight import load_media_preflight
        saved = load_media_preflight(legacy, expected_root=root)
        if getattr(arguments, "confirm_physical_device", None) == saved.physical_device:
            whole = saved.physical_device
    if whole is not None and root is not None:
        arguments.whole_device, arguments.mount_root = whole, root
        return
    if arguments.json or getattr(arguments, "non_interactive", False):
        missing = tuple(flag for flag, value in (("--whole-device", whole), ("--mount-root", root)) if value is None)
        raise ProjectError("missing_input", "automation must select current media explicitly", missing)
    choices = [choice for choice in discover_media()
               if (whole is None or choice.physical_device == whole)
               and (root is None or choice.mount_root == Path(root).resolve())]
    if not choices:
        raise ProjectError("media_not_found", "no validated mounted removable FAT32 card found")
    for number, choice in enumerate(choices, 1):
        print(f"{number}. {choice.physical_device}: {choice.model}, {choice.capacity_bytes} bytes, {choice.mount_root}")
    selected = input("Select the current SD card number, even when only one is listed: ").strip()
    if not selected.isdigit() or not 1 <= int(selected) <= len(choices):
        raise ProjectError("invalid_selection", "no current card was selected")
    choice = choices[int(selected) - 1]
    arguments.whole_device, arguments.mount_root = choice.physical_device, choice.mount_root


def confirm_plan(arguments, plan: WritePlan) -> WriteConfirmation:
    expected = plan.required_confirmations()
    supplied = {
        "plan_sha256": getattr(arguments, "confirm_plan", None),
        "physical_device": getattr(arguments, "confirm_physical_device", None),
        "target": getattr(arguments, "confirm_target", None),
        "write_set": getattr(arguments, "confirm_stock_uboot_result", None)
                     if plan.operation.endswith("handoff") else getattr(arguments, "confirm_write_set", None),
    }
    if not arguments.json and not getattr(arguments, "non_interactive", False):
        print(json.dumps({"plan": plan.document(), "confirmations": expected}, indent=2, sort_keys=True))
        for field, value in expected.items():
            supplied[field] = input(f"Confirm {field} by typing {value}: ").strip()
    elif not getattr(arguments, "non_interactive", False) and plan.operation != "stock-recovery uartless-reuse":
        # Preserve old long-form contracts while passing a content-bound
        # confirmation to the shared service. New automation supplies all fields.
        supplied["plan_sha256"] = supplied["plan_sha256"] or expected["plan_sha256"]
        if plan.operation in {"stock-recovery uartless-prepare", "universal evacuate-recovery"}:
            supplied["write_set"] = supplied["write_set"] or expected["write_set"]
        if plan.operation.startswith("stock-recovery") or plan.operation.endswith("evacuate-recovery"):
            supplied["target"] = supplied["target"] or expected["target"]
    flags = {"plan_sha256": "--confirm-plan", "physical_device": "--confirm-physical-device",
             "target": "--confirm-target", "write_set": "--confirm-stock-uboot-result" if plan.operation.endswith("handoff") else "--confirm-write-set"}
    missing = tuple(flags[field] for field, value in supplied.items() if value is None)
    if missing:
        raise ProjectError("missing_input", "confirm the current write plan", missing)
    return WriteConfirmation(**supplied)
