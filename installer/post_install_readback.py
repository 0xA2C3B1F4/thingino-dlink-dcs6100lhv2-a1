"""Read-only, camera-bound verification of the installed split NOR layout."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .camera_setup import OperationError
from .install_project import ProjectError, session_fingerprint
from .install_results import InstallationEvent, InstallationResult, EventSink, document
from .layout import MTD_PHYSICAL_ERASE_SIZE, TARGET
from .mtd3_split import DATA_FLASH_SPAN, SYSTEM_FLASH_SPAN
from .recovery_ap.host import (
    RecoveryApHostSession,
    _exchange,
    _private_file,
    load_host_session,
    resolve_recovery_ap_station,
)
from .recovery_gate import (
    validate_existing_recovery_boundary,
    validate_functional_recovery_boundary,
)
from .universal_install import (
    INSTALL_MANIFEST,
    INSTALL_SET_MEMBERS,
    MAX_INSTALL_MEMBER,
    STAGE1_ROOT,
    UNIVERSAL_BUNDLE,
    _read_regular,
)


READBACK_TIMEOUT_SECONDS = 90.0
POST_INSTALL_READBACK_COMMAND = (
    "set -eu; "
    "printf 'BOOT_BEFORE='; cat /proc/sys/kernel/random/boot_id; "
    "printf 'MTD_BEGIN\\n'; cat /proc/mtd; printf 'MTD_END\\n'; "
    "sha256sum /dev/mtd0 /dev/mtd1 /dev/mtd3 /dev/mtd5 /dev/mtd6; "
    "printf 'BOOT_AFTER='; cat /proc/sys/kernel/random/boot_id"
)
_MTD_LAYOUT = (
    (0, 0x040000, "boot"),
    (1, 0x1C0000, "kernel"),
    (2, 0x480000, "bootstrap"),
    (3, SYSTEM_FLASH_SPAN, "system"),
    (4, DATA_FLASH_SPAN, "data"),
    (5, 0x180000, "vendor"),
    (6, 0x040000, "factory"),
)
_CHECKED = {0: "physical-mtd0", 1: "physical-mtd1", 3: "physical-mtd3-system",
            5: "physical-mtd4", 6: "physical-mtd5"}


@dataclass(frozen=True, kw_only=True)
class PostInstallReadbackInputs:
    install_set_dir: Path
    universal_public_key: Path
    preserved_readback_dir: Path
    session_dir: Path
    recovery_dir: Path | None = None
    functional_recovery_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class _ValidatedInputs:
    session: RecoveryApHostSession
    session_identity: str
    transport_identity: str
    expected_sha256: dict[int, str]
    bundle_sha256: str
    camera_identity_sha256: str
    data_mode: str


def _reject_symlink_path(path: Path, label: str) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise OperationError(f"{label} has a symlink path component")


def _padded_sha256(raw: bytes, size: int, label: str) -> str:
    if not raw or len(raw) > size:
        raise OperationError(f"{label} does not fit its installed partition")
    return hashlib.sha256(raw + b"\xff" * (size - len(raw))).hexdigest()


def _transport_fingerprint(session: RecoveryApHostSession) -> str:
    if session.station_known_hosts is None:
        raise OperationError("session station host pin is missing")
    digest = hashlib.sha256(b"thingino-post-install-readback-transport-v1\0")
    for path, label, limit in (
        (session.identity, "SSH identity", 16 * 1024),
        (session.station_known_hosts, "station host pin", 4096),
    ):
        _reject_symlink_path(path, label)
        raw = _private_file(path, label, limit)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _validate_install_set(root: Path, public_key: Path):
    from .final_bundle import validate_universal_final_bundle
    from .media import validate_install_set
    from .sd_package import parse_package
    from .stage1.build import BOOTSTRAP_FILENAME
    from .stage2 import FILENAME as STAGE2_FILENAME

    if root.is_symlink() or not root.is_dir():
        raise OperationError("universal install-set directory is invalid")
    if {entry.name for entry in root.iterdir()} != INSTALL_SET_MEMBERS:
        raise OperationError("universal install-set directory is not exact")
    try:
        bundle_raw = _read_regular(root / UNIVERSAL_BUNDLE, "universal final bundle", MAX_INSTALL_MEMBER)
        bundle = validate_universal_final_bundle(bundle_raw, public_key=public_key)
        bootstrap = _read_regular(root / BOOTSTRAP_FILENAME, "universal bootstrap", MAX_INSTALL_MEMBER)
        stage2_raw = _read_regular(root / STAGE2_FILENAME, "universal stage 2", MAX_INSTALL_MEMBER)
        manifest_raw = _read_regular(root / INSTALL_MANIFEST, "universal install-set manifest", 128 * 1024)
        stage1_root = _read_regular(root / STAGE1_ROOT, "universal stage-1 root", MAX_INSTALL_MEMBER)
        stage2 = validate_install_set(
            bootstrap_bytes=bootstrap,
            stage2_bytes=stage2_raw,
            manifest_bytes=manifest_raw,
            bootstrap_name=BOOTSTRAP_FILENAME,
        )
        manifest = json.loads(manifest_raw)
        package = parse_package(bootstrap, require_project_header=True)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationError("signed universal install set is invalid") from exc
    if (
        manifest.get("artifact_scope") != "model-universal"
        or manifest.get("universal_firmware_sha256") != bundle.sha256
        or manifest.get("artifacts", {}).get(UNIVERSAL_BUNDLE)
        != {"sha256": bundle.sha256, "size": len(bundle.raw)}
        or stage2.kernel != bundle.members["images/kernel.uimage"]
        or stage2.system != bundle.members["images/system.squashfs"]
        or package.records[1].payload != bundle.members["images/bootstrap.squashfs"]
        or package.records[1].payload != stage1_root
    ):
        raise OperationError("install set is not bound to the signed universal bundle")
    return bundle, stage2


def _validate_inputs(request: PostInstallReadbackInputs) -> _ValidatedInputs:
    if (request.recovery_dir is None) == (request.functional_recovery_dir is None):
        raise OperationError("select exactly one recovery evidence class")
    selected_recovery = request.functional_recovery_dir or request.recovery_dir
    assert selected_recovery is not None
    for path, label in (
        (request.install_set_dir, "install set"),
        (request.universal_public_key, "universal public key"),
        (request.preserved_readback_dir, "preserved readback"),
        (request.session_dir, "session"),
        (selected_recovery, "recovery evidence"),
    ):
        _reject_symlink_path(path, label)
    validator = (
        validate_functional_recovery_boundary
        if request.functional_recovery_dir is not None
        else validate_existing_recovery_boundary
    )
    recovery = validator(
        recovery_dir=selected_recovery,
        preserved_readback_dir=request.preserved_readback_dir,
    )
    bundle, stage2 = _validate_install_set(request.install_set_dir, request.universal_public_key)
    session_identity = session_fingerprint(request.session_dir)
    session = load_host_session(request.session_dir)
    if (
        session.session_kind != "uartless-functional-provisioning"
        or session.station_known_hosts is None
        or session.camera_identity_sha256 != recovery.camera_identity_sha256
    ):
        raise OperationError("session is not pinned to the selected same-camera recovery")
    if session_fingerprint(request.session_dir) != session_identity:
        raise ProjectError("changed_input", "session identity changed during validation")
    transport_identity = _transport_fingerprint(session)
    protected = {}
    camera_identity = hashlib.sha256()
    camera_identity.update(b"thingino-dcs6100-camera-identity-v1\0")
    camera_identity.update(TARGET.model.encode("ascii") + b"\0")
    camera_identity.update(TARGET.hardware_revision.encode("ascii") + b"\0")
    camera_identity.update(TARGET.nor_size.to_bytes(8, "big"))
    for index in (0, 4, 5):
        size = TARGET.partition(index).size
        raw = _read_regular(
            request.preserved_readback_dir / f"mtd{index}.bin",
            f"preserved mtd{index}",
            size,
        )
        if len(raw) != size:
            raise OperationError(f"preserved mtd{index} has the wrong size")
        protected[index] = raw
        camera_identity.update(bytes((index,)))
        camera_identity.update(size.to_bytes(8, "big"))
        camera_identity.update(raw)
    if camera_identity.hexdigest() != recovery.camera_identity_sha256:
        raise ProjectError("changed_input", "preserved readback changed during validation")
    expected = {
        0: hashlib.sha256(protected[0]).hexdigest(),
        1: _padded_sha256(stage2.kernel, TARGET.partition(1).size, "final kernel"),
        3: _padded_sha256(stage2.system, SYSTEM_FLASH_SPAN, "final system"),
        5: hashlib.sha256(protected[4]).hexdigest(),
        6: hashlib.sha256(protected[5]).hexdigest(),
    }
    return _ValidatedInputs(
        session=session,
        session_identity=session_identity,
        transport_identity=transport_identity,
        expected_sha256=expected,
        bundle_sha256=bundle.sha256,
        camera_identity_sha256=recovery.camera_identity_sha256,
        data_mode=stage2.data_mode,
    )


def _parse_readback(raw: bytes, expected_sha256: dict[int, str]) -> tuple[str, dict[int, str]]:
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise OperationError("post-install readback is not ASCII") from exc
    if len(lines) != 17 or lines[1:3] != ["MTD_BEGIN", 'dev:    size   erasesize  name'] or lines[10] != "MTD_END":
        raise OperationError("post-install readback framing is invalid")
    if not lines[0].startswith("BOOT_BEFORE=") or not lines[16].startswith("BOOT_AFTER="):
        raise OperationError("post-install boot identity framing is invalid")
    boot_before = lines[0][len("BOOT_BEFORE="):]
    boot_after = lines[16][len("BOOT_AFTER="):]
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot_before):
        raise OperationError("post-install boot identity is invalid")
    if boot_before != boot_after:
        raise OperationError("camera rebooted during post-install readback")
    observed_layout = []
    for line in lines[3:10]:
        match = re.fullmatch(r'mtd([0-6]): ([0-9a-f]{8}) ([0-9a-f]{8}) "([a-z]+)"', line)
        if match is None:
            raise OperationError("post-install MTD layout is malformed")
        observed_layout.append((int(match[1]), int(match[2], 16), int(match[3], 16), match[4]))
    required_layout = [
        (index, size, MTD_PHYSICAL_ERASE_SIZE, name)
        for index, size, name in _MTD_LAYOUT
    ]
    if observed_layout != required_layout:
        raise OperationError("post-install MTD split layout differs")
    observed_hashes = {}
    for line in lines[11:16]:
        match = re.fullmatch(r"([0-9a-f]{64})  /dev/mtd([0-6])", line)
        if match is None:
            raise OperationError("post-install partition digest is malformed")
        index = int(match[2])
        if index in observed_hashes:
            raise OperationError("post-install partition digest is duplicated")
        observed_hashes[index] = match[1]
    if observed_hashes != expected_sha256:
        raise OperationError("post-install partition digest differs")
    return boot_before, observed_hashes


def verify_post_install_readback(
    request: PostInstallReadbackInputs, *, emit: EventSink | None = None
) -> InstallationResult:
    if emit:
        emit(InstallationEvent("validating", "verify_post_install_readback"))
    validated = _validate_inputs(request)
    mdns_name, host = resolve_recovery_ap_station(
        request.session_dir, allow_uartless_station=True
    )
    if session_fingerprint(request.session_dir) != validated.session_identity:
        raise ProjectError("changed_input", "session identity changed before verification")
    if _transport_fingerprint(validated.session) != validated.transport_identity:
        raise ProjectError("changed_input", "SSH identity or station pin changed before verification")
    response = _exchange(
        validated.session,
        host=host,
        command=POST_INSTALL_READBACK_COMMAND,
        timeout=READBACK_TIMEOUT_SECONDS,
        allow_uartless_post_install_readback=True,
    )
    boot_id, observed = _parse_readback(response, validated.expected_sha256)
    if session_fingerprint(request.session_dir) != validated.session_identity:
        raise ProjectError("changed_input", "session identity changed during verification")
    if _transport_fingerprint(validated.session) != validated.transport_identity:
        raise ProjectError("changed_input", "SSH identity or station pin changed during verification")
    checked = [
        {"logical_mtd": index, "source": _CHECKED[index], "sha256": observed[index]}
        for index in sorted(observed)
    ]
    return document(
        "universal verify-readback",
        ok=True,
        phase="camera-bound-split-layout-readback-verified",
        result={
            "boot_id": boot_id,
            "bundle_sha256": validated.bundle_sha256,
            "camera_identity_sha256": validated.camera_identity_sha256,
            "checked_partitions": checked,
            "data_mode": validated.data_mode,
            "full_flash_readback_verified": False,
            "mutable_data_exact_comparison": "out-of-scope",
            "physical_mtd2_exact_comparison": "out-of-scope",
            "read_only": True,
            "safe_next_action": "continue-release-acceptance",
            "station_ipv4": host,
            "station_mdns_name": mdns_name,
            "write_set": [],
        },
    )
