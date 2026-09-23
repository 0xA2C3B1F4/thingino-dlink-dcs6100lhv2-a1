"""Exact authenticated command grammar for the bounded Raptor RAM experiment."""

from pathlib import Path
import re
import shlex

TEMPLATE = Path(__file__).with_name("templates") / "raptor-transfer.sh"
BINARIES = "rac rad raptorctl rhd ric ringdump rmd rmr rod rsd rvd".split()
MEMBERS = {"baseline.absent", "baseline.sha256", "candidate.json", "etc/raptor.conf",
           "payload.sha256", "supervisor.sh", "usr/sbin/thingino-controld",
           "usr/lib/librss_common.so", "usr/lib/librss_ipc.so"} | {
               "usr/bin/" + name for name in BINARIES}
ACTIONS = {"preflight", "start", "renew", "status", "rollback"}
MAX_COMMAND_BYTES = 9000
INSPECTION_CHECKS = {
    0: "ready", 30: "volatile-parent", 31: "volatile-mounts",
    32: "nonce-format", 33: "root-directory", 34: "pending-regular",
    35: "pending-bounds", 36: "pending-nonce", 37: "member-directories",
    38: "digest-format", 39: "root-owner-mode", 40: "pending-size",
    41: "pending-canonical", 42: "first-member-regular", 43: "first-member-size",
    44: "first-member-digest", 45: "transfer-regular", 46: "transfer-size",
    47: "transfer-canonical", 48: "lock-directory", 49: "lock-empty",
    50: "inventory-directory", 51: "inventory-symlink", 52: "inventory-extra",
    60: "proc-stat-read", 61: "proc-stat-comm", 62: "proc-stat-numeric",
    63: "proc-stat-leading-zero", 64: "proc-stat-state", 65: "ancestor-depth",
    66: "ancestor-identity", 67: "ancestor-cycle", 68: "scan-pid-identity",
    69: "scan-exe-readlink", 70: "scan-cmdline-read", 71: "scan-identity-race",
    72: "scan-root-reference", 73: "scan-noexe-cmdline", 74: "scan-noexe-live",
    80: "argument-count", 81: "root-identity-read", 82: "root-device-read",
    83: "parent-device", 84: "scan-delay", 85: "destination-present",
    86: "root-identity-race",
}

# Explicit, reviewed transitive dependencies; never infer shell dependencies.
COMMON = {"setup", "hex", "regular", "digest", "volatile", "dispatch"}
SECTIONS = {
    "initialize": COMMON | {"initialize"},
    "receive": COMMON | {"bound", "members", "member", "transfer_lock", "empty_directory", "release_transfer_lock", "receive_member"},
    "seal": COMMON | {"bound", "members", "member", "transfer_lock", "empty_directory", "release_transfer_lock", "verify_members", "seal"},
    "invoke": COMMON | {"bound", "members", "member", "verify_members", "invoke"},
    "diagnose": COMMON | {"bound", "members", "empty_directory", "diagnose"},
    "quarantine": COMMON | {"bound", "empty_directory", "partial_inventory", "proc_stat", "quarantine_ancestors", "quarantine_process_scan", "quarantine"},
}


SECTIONS["inspect-quarantine"] = (SECTIONS["quarantine"] - {"dispatch", "quarantine"}) | {"inspection", "inspection_entry"}


def receiver_script(action: str) -> str:
    if action not in SECTIONS:
        raise ValueError("unknown receiver action")
    source = TEMPLATE.read_text()
    pieces = re.split(r"^# receiver-section: ([a-z_]+)\n", source, flags=re.MULTILINE)
    expected = set().union(*SECTIONS.values()) | {"library"}
    names = pieces[1::2]
    if pieces[0] != "#!/bin/sh\n" or len(names) != len(set(names)) or set(names) != expected:
        raise ValueError("receiver source section contract changed")
    return pieces[0] + "".join(body for name, body in zip(names, pieces[2::2]) if name in SECTIONS[action])


def command(action: str, *values: str) -> str:
    def valid_hex(value: str, size: int) -> bool:
        return re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is not None

    valid = bool(values) and valid_hex(values[0], 32)
    if action in {"initialize", "diagnose"}:
        valid = valid and len(values) == 3 and all(valid_hex(v, 64) for v in values[1:])
    elif action in {"quarantine", "inspect-quarantine"}:
        valid = valid and len(values) == 4 and all(valid_hex(v, 64) for v in values[1:])
    elif action == "receive":
        valid = (valid and len(values) == 4 and values[1] in MEMBERS
                 and re.fullmatch(r"[1-9][0-9]{0,6}", values[2]) is not None
                 and int(values[2]) <= 2097152 and valid_hex(values[3], 64))
    elif action == "seal":
        valid = valid and len(values) == 2 and valid_hex(values[1], 64)
    elif action == "invoke":
        valid = valid and len(values) == 3 and valid_hex(values[1], 64) and values[2] in ACTIONS
    else:
        valid = False
    if not valid:
        raise ValueError("outside the Raptor runtime command grammar")
    rendered = shlex.join(["/bin/sh", "-c", receiver_script(action), "raptor-transfer", action, *values])
    if len(rendered.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ValueError("Raptor runtime command exceeds 9000-byte transport limit")
    return rendered


def is_runtime_command(value: str) -> bool:
    if len(value.encode("utf-8")) > MAX_COMMAND_BYTES:
        return False
    try:
        parts = shlex.split(value)
        if len(parts) < 6 or parts[:2] != ["/bin/sh", "-c"] or parts[3] != "raptor-transfer":
            return False
        return value == command(parts[4], *parts[5:])
    except (ValueError, OSError):
        return False
