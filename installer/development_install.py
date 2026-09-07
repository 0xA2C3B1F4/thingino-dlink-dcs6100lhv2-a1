"""Resumable host-side completion of one personal Thingino installation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .final_root import prepare_from_private_directory
from .media_closure import load_media_closure
from .mtd3_image import build_personal_mtd3_image, validate_personal_mtd3_image
from .platform_wifi import PlatformWifiError
from .private_config import generate_private_config, load_private_config_for_session
from .recovery_ap.host import (
    RecoveryApHostError,
    activate_personal_mtd3,
    extract_camera_vendor_bundle,
    install_personal_mtd3,
    load_host_session,
    load_service_credential,
    probe_recovery_ap,
    prove_thingino_health,
)
from .sd_package import atomic_write
from .vendor_bundle import load_vendor_bundle


class DevelopmentInstallError(ValueError):
    """The resumable personal-install flow failed closed."""


STATE_NAME = "install-state.private.json"


def build_personal_candidate(
    *,
    session_dir: Path,
    base_rootfs_path: Path,
    private_config_dir: Path,
    expected_wpa_config_path: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    output_dir: Path,
    mksquashfs: Path,
    unsquashfs: Path,
) -> dict[str, object]:
    """Build one personal mtd3 candidate without contacting or writing a camera."""

    if output_dir.exists():
        raise DevelopmentInstallError("personal candidate output already exists")
    if base_rootfs_path.is_symlink() or not base_rootfs_path.is_file():
        raise DevelopmentInstallError("base Thingino rootfs is not a regular file")
    load_host_session(session_dir)
    private = load_private_config_for_session(
        output_dir=private_config_dir,
        session_dir=session_dir,
    )
    vendor = load_vendor_bundle(vendor_bundle_dir)
    media = load_media_closure(media_closure_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        final_root = temporary / "final-root"
        final_result = prepare_from_private_directory(
            base_rootfs_path=base_rootfs_path,
            private_config_dir=private_config_dir,
            expected_wpa_config_path=expected_wpa_config_path,
            vendor_bundle_dir=vendor_bundle_dir,
            media_closure_dir=media_closure_dir,
            session_dir=session_dir,
            output_dir=final_root,
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
        )
        image = build_personal_mtd3_image(
            (final_root / "system.private.squashfs").read_bytes()
        )
        image_path = temporary / "personal-mtd3.bin"
        atomic_write(image_path, image.raw)
        image_path.chmod(0o600)
        result = {
            "base_rootfs_sha256": hashlib.sha256(base_rootfs_path.read_bytes()).hexdigest(),
            "credential_set_id": private.credential_set_id,
            "final_root": final_result,
            "image_path": "personal-mtd3.bin",
            "image_sha256": image.image_sha256,
            "image_size": len(image.raw),
            "media_closure_sha256": media.closure_sha256,
            "nor_writes": False,
            "provenance_path": "final-root/final-root.private.json",
            "schema_version": 1,
            "vendor_bundle_sha256": vendor.bundle_sha256,
        }
        atomic_write(
            temporary / "build-result.private.json",
            (json.dumps(result, indent=2, sort_keys=True) + "\n").encode(),
        )
        (temporary / "build-result.private.json").chmod(0o600)
        os.replace(temporary, output_dir)
        return {**result, "output_dir": str(output_dir)}
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _proof_time() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _record_station_health(
    state: dict[str, object], health: dict[str, object], image_sha256: str
) -> None:
    state["last_camera"] = {
        "authenticated_control": health.get("authenticated_control") is True,
        "file_transfer_bytes": health.get("file_transfer_bytes"),
        "file_transfer_sha256": health.get("file_transfer_sha256"),
        "mdns_resolution": health.get("mdns_resolution"),
        "mtd3_kind": "personal",
        "mtd3_sha256": health.get("mtd3_sha256"),
        "proven_at": _proof_time(),
        "ssh_authentication": health.get("ssh_authentication"),
        "state": "station",
        "station_ipv4": health.get("station_ipv4"),
        "station_mdns_name": health.get("station_mdns_name"),
    }


def _require_expected_health_image(
    health: dict[str, object], image_sha256: str
) -> None:
    if (
        health.get("mtd3_read_back_verified") is not True
        or health.get("mtd3_sha256") != image_sha256
    ):
        raise DevelopmentInstallError(
            "station health did not prove the expected complete physical mtd3"
        )


def _try_join_station(join_station: Callable[[], None] | None) -> None:
    if join_station is None:
        return
    try:
        join_station()
    except PlatformWifiError:
        pass


def _write_history(state: dict[str, object]) -> list[dict[str, object]]:
    history = state.get("write_history")
    if history is None:
        history = []
        legacy = state.pop("written_mtd", [])
        if legacy not in ([], [3]):
            raise DevelopmentInstallError("legacy mtd3 write set is invalid")
        if legacy == [3]:
            record: dict[str, object] = {
                "mtd": 3,
                "read_back_verified": True,
                "source": "legacy-state",
            }
            image_sha256 = state.get("image_sha256")
            if isinstance(image_sha256, str):
                record["image_sha256"] = image_sha256
            history.append(record)
        state["write_history"] = history
    if not isinstance(history, list) or any(
        not isinstance(record, dict)
        or record.get("mtd") != 3
        or record.get("read_back_verified") is not True
        for record in history
    ):
        raise DevelopmentInstallError("mtd3 write history is invalid")
    return history


def _write_state(work_dir: Path, state: dict[str, object]) -> None:
    state["schema_version"] = 1
    atomic_write(
        work_dir / STATE_NAME,
        (json.dumps(state, indent=2, sort_keys=True) + "\n").encode(),
    )
    (work_dir / STATE_NAME).chmod(0o600)


def _session_identity_sha256(session_dir: Path, station_mdns_name: str) -> str:
    identity_public = session_dir / "host/identity.pub"
    if identity_public.is_symlink() or not identity_public.is_file():
        raise DevelopmentInstallError("recovery session public identity is missing")
    raw = identity_public.read_bytes()
    return hashlib.sha256(
        b"thingino-dlink-session-v1\0"
        + station_mdns_name.encode("ascii")
        + b"\0"
        + raw
    ).hexdigest()


def _existing_state(
    work_dir: Path,
    base_sha256: str,
    *,
    session_identity_sha256: str,
    station_mdns_name: str,
) -> dict[str, object]:
    state_path = work_dir / STATE_NAME
    if not state_path.is_file() or state_path.is_symlink():
        raise DevelopmentInstallError("existing install work directory lacks its state")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentInstallError("install state is invalid") from exc
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or state.get("base_rootfs_sha256") != base_sha256
    ):
        raise DevelopmentInstallError("install state belongs to another base root")
    recorded_name = state.get("station_mdns_name")
    if recorded_name is not None and recorded_name != station_mdns_name:
        raise DevelopmentInstallError("install state belongs to another recovery session")
    recorded_identity = state.get("session_identity_sha256")
    if recorded_identity is not None and recorded_identity != session_identity_sha256:
        raise DevelopmentInstallError("install state belongs to another recovery session")
    state["station_mdns_name"] = station_mdns_name
    state["session_identity_sha256"] = session_identity_sha256
    return state


def complete_personal_install(
    *,
    session_dir: Path,
    base_rootfs: bytes,
    media_closure_dir: Path,
    work_dir: Path,
    ssid: str | None,
    passphrase: str | None,
    expected_wpa_config_path: Path | None,
    mksquashfs: Path,
    unsquashfs: Path,
    ap_host: str = "192.168.88.1",
    station_timeout: int = 180,
    join_ap: Callable[[], None] | None = None,
    join_station: Callable[[], None] | None = None,
    progress: Callable[[str, bool], None] | None = None,
) -> dict[str, object]:
    """Build/install once, or resume using the exact preserved personal image."""

    if station_timeout < 30 or station_timeout > 600:
        raise DevelopmentInstallError("station health timeout is outside policy")
    session = load_host_session(session_dir)
    session_identity_sha256 = _session_identity_sha256(
        session_dir, session.station_mdns_name
    )
    media_closure = load_media_closure(media_closure_dir)
    base_sha256 = hashlib.sha256(base_rootfs).hexdigest()
    if work_dir.exists():
        if work_dir.is_symlink() or not work_dir.is_dir():
            raise DevelopmentInstallError("install work path changed type")
        state = _existing_state(
            work_dir,
            base_sha256,
            session_identity_sha256=session_identity_sha256,
            station_mdns_name=session.station_mdns_name,
        )
        recorded_closure = state.get("media_closure_sha256")
        if recorded_closure not in (None, media_closure.closure_sha256):
            raise DevelopmentInstallError("install state belongs to another media closure")
        state["media_closure_sha256"] = media_closure.closure_sha256
    else:
        if ssid is None or passphrase is None:
            raise DevelopmentInstallError("new install requires hidden Wi-Fi input")
        work_dir.mkdir(parents=True, mode=0o700)
        state = {
            "base_rootfs_sha256": base_sha256,
            "media_closure_sha256": media_closure.closure_sha256,
            "phase": "created",
            "session_identity_sha256": session_identity_sha256,
            "station_mdns_name": session.station_mdns_name,
            "write_history": [],
        }
        _write_state(work_dir, state)
    write_history = _write_history(state)
    written_mtd: list[int] = []
    state["current_run"] = {
        "read_back_verified": False,
        "written_mtd": written_mtd,
    }
    _write_state(work_dir, state)
    stage_base_root(work_dir, base_rootfs)

    vendor_dir = work_dir / "vendor-bundle"
    if vendor_dir.exists():
        vendor = load_vendor_bundle(vendor_dir)
    else:
        if progress is not None:
            progress("Joining the private recovery AP and reading the vendor closure", False)
        if join_ap is not None:
            join_ap()
        probe_recovery_ap(
            session_dir=session_dir,
            host=ap_host,
            expected_state="ap",
        )
        extract_camera_vendor_bundle(
            session_dir=session_dir,
            host=ap_host,
            output_dir=vendor_dir,
        )
        vendor = load_vendor_bundle(vendor_dir)
        state.update(phase="vendor-extracted", vendor_bundle_sha256=vendor.bundle_sha256)
        _write_state(work_dir, state)
    if state.get("vendor_bundle_sha256", vendor.bundle_sha256) != vendor.bundle_sha256:
        raise DevelopmentInstallError("preserved vendor bundle changed")

    private_config = work_dir / "install-config"
    if not private_config.exists():
        if ssid is None or passphrase is None:
            raise DevelopmentInstallError("resume lacks the preserved Wi-Fi configuration")
        generate_private_config(
            output_dir=private_config,
            ssid=ssid,
            passphrase=passphrase,
            authorized_key=(session_dir / "host/identity.pub").read_bytes(),
            credential=load_service_credential(session_dir) + b"\n",
        )
        state["phase"] = "configured"
        _write_state(work_dir, state)
        expected_wpa_config_path = private_config / "wpa_supplicant.conf"
    elif expected_wpa_config_path is None:
        raise DevelopmentInstallError(
            "resume requires an explicit current station Wi-Fi configuration"
        )

    final_dir = work_dir / "final-root"
    if not final_dir.exists():
        if progress is not None:
            progress("Building the personal Thingino root from the preserved inputs", False)
        prepare_from_private_directory(
            base_rootfs_path=work_dir / "base-rootfs.squashfs",
            private_config_dir=private_config,
            expected_wpa_config_path=expected_wpa_config_path,
            vendor_bundle_dir=vendor_dir,
            media_closure_dir=media_closure_dir,
            session_dir=session_dir,
            output_dir=final_dir,
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
        )
        state["phase"] = "personal-root-built"
        _write_state(work_dir, state)

    image_path = work_dir / "personal-mtd3.bin"
    if image_path.exists():
        image = validate_personal_mtd3_image(image_path.read_bytes())
    else:
        image = build_personal_mtd3_image(
            (final_dir / "system.private.squashfs").read_bytes()
        )
        atomic_write(image_path, image.raw)
        image_path.chmod(0o600)
        state.update(phase="personal-image-built", image_sha256=image.image_sha256)
        _write_state(work_dir, state)
    if state.get("image_sha256", image.image_sha256) != image.image_sha256:
        raise DevelopmentInstallError("preserved personal image changed")

    if state.get("phase") in {"activation-requested", "healthy"}:
        if progress is not None:
            progress("Checking live station health before considering recovery", False)
        resumed = _await_station_health_or_ap(
            session_dir=session_dir,
            ap_host=ap_host,
            station_timeout=station_timeout,
            join_ap=join_ap,
            join_station=join_station,
            expected_mtd3_sha256=image.image_sha256,
        )
        if resumed is not None:
            _require_expected_health_image(resumed, image.image_sha256)
            state["phase"] = "healthy"
            state["current_run"] = {
                "image_sha256": image.image_sha256,
                "read_back_verified": True,
                "written_mtd": [],
            }
            _record_station_health(state, resumed, image.image_sha256)
            _write_state(work_dir, state)
            return _completed_result(
                health=resumed,
                image_sha256=image.image_sha256,
                station_mdns_name=session.station_mdns_name,
                work_dir=work_dir,
                written_mtd=written_mtd,
                write_history=write_history,
            )

    if join_ap is not None:
        join_ap()
    if progress is not None:
        progress("Reconciling the camera's live read-only NOR state", False)
    prior_pending = state.get("pending_mtd3")
    if prior_pending is not None and (
        not isinstance(prior_pending, dict)
        or prior_pending.get("image_sha256") != image.image_sha256
    ):
        raise DevelopmentInstallError("pending mtd3 operation belongs to another image")
    state["pending_mtd3"] = {
        "image_sha256": image.image_sha256,
        "started_at": _proof_time(),
    }
    _write_state(work_dir, state)
    install_result = install_personal_mtd3(
        session_dir=session_dir,
        host=ap_host,
        image_path=image_path,
        vendor_bundle_dir=vendor_dir,
        provenance_path=final_dir / "final-root.private.json",
        expected_image_sha256=image.image_sha256,
        progress=progress,
    )
    if install_result.get("image_sha256") != image.image_sha256:
        raise DevelopmentInstallError("camera installation result belongs to another image")
    written_mtd = install_result["written_mtd"]
    if written_mtd not in ([], [3]):
        raise DevelopmentInstallError("current mtd3 write set is invalid")
    if written_mtd == [3]:
        if install_result.get("read_back_verified") is not True:
            raise DevelopmentInstallError("mtd3 write lacks full read-back evidence")
        write_history.append(
            {
                "image_sha256": image.image_sha256,
                "mtd": 3,
                "read_back_verified": True,
                "source": "current-run",
            }
        )
    elif prior_pending is not None and not any(
        record.get("image_sha256") == image.image_sha256
        and record.get("source") == "reconciled-pending-write"
        for record in write_history
    ):
        write_history.append(
            {
                "image_sha256": image.image_sha256,
                "mtd": 3,
                "read_back_verified": True,
                "source": "reconciled-pending-write",
            }
        )
    state.pop("pending_mtd3", None)
    state.update(
        current_run={
            "image_sha256": image.image_sha256,
            "read_back_verified": True,
            "written_mtd": written_mtd,
        },
        last_camera={
            "mtd3_kind": "personal",
            "mtd3_sha256": image.image_sha256,
            "proven_at": _proof_time(),
            "read_back_verified": True,
            "state": "recovery-ap",
        },
        phase="mtd3-readback-verified",
        write_history=write_history,
    )
    _write_state(work_dir, state)
    if install_result["safe_next_action"] != "boot_and_verify":
        if progress is not None:
            progress("Activating the verified existing mtd3 image", False)
        activate_personal_mtd3(
            session_dir=session_dir,
            host=ap_host,
            image_sha256=image.image_sha256,
        )
    state["phase"] = "activation-requested"
    _write_state(work_dir, state)

    _try_join_station(join_station)
    if progress is not None:
        progress("Proving station Wi-Fi, mDNS, pinned SSH, and the 4096-byte round trip", False)

    deadline = time.monotonic() + station_timeout
    last_error = "station mDNS did not resolve"
    while time.monotonic() < deadline:
        try:
            health = prove_thingino_health(
                session_dir=session_dir,
                expected_mtd3_sha256=image.image_sha256,
            )
            _require_expected_health_image(health, image.image_sha256)
            state["phase"] = "healthy"
            _record_station_health(state, health, image.image_sha256)
            _write_state(work_dir, state)
            return _completed_result(
                health=health,
                image_sha256=image.image_sha256,
                station_mdns_name=session.station_mdns_name,
                work_dir=work_dir,
                written_mtd=written_mtd,
                write_history=write_history,
            )
        except RecoveryApHostError as exc:
            last_error = str(exc)
        time.sleep(3)

    try:
        probe_recovery_ap(
            session_dir=session_dir,
            host=ap_host,
            expected_state="ap",
        )
    except RecoveryApHostError:
        raise DevelopmentInstallError(last_error)
    state["phase"] = "returned-to-ap"
    state["last_camera"] = {
        "mtd3_kind": "personal",
        "mtd3_sha256": image.image_sha256,
        "proven_at": _proof_time(),
        "read_back_verified": True,
        "state": "recovery-ap",
    }
    _write_state(work_dir, state)
    raise DevelopmentInstallError(
        "Thingino did not pass station health and recovery returned to setup AP"
    )


def _completed_result(
    *,
    health: dict[str, object],
    image_sha256: str,
    station_mdns_name: str,
    work_dir: Path,
    written_mtd: object,
    write_history: object,
) -> dict[str, object]:
    if written_mtd not in ([], [3]):
        raise DevelopmentInstallError("recorded mtd3 write set is invalid")
    return {
        **health,
        "image_sha256": image_sha256,
        "resumable_work_directory": work_dir.name,
        "station_mdns_name": station_mdns_name,
        "write_history": write_history,
        "written_mtd": written_mtd,
    }


def _await_station_health_or_ap(
    *,
    session_dir: Path,
    ap_host: str,
    station_timeout: int,
    join_ap: Callable[[], None] | None,
    join_station: Callable[[], None] | None,
    expected_mtd3_sha256: str,
) -> dict[str, object] | None:
    """Recover an activation result without blindly rewriting a healthy mtd3."""

    deadline = time.monotonic() + station_timeout
    while time.monotonic() < deadline:
        _try_join_station(join_station)
        try:
            return prove_thingino_health(
                session_dir=session_dir,
                expected_mtd3_sha256=expected_mtd3_sha256,
            )
        except RecoveryApHostError:
            pass
        if join_ap is not None:
            join_ap()
        try:
            probe_recovery_ap(
                session_dir=session_dir,
                host=ap_host,
                expected_state="ap",
            )
            return None
        except RecoveryApHostError:
            time.sleep(3)
    raise DevelopmentInstallError(
        "resumed activation reached neither healthy station mDNS nor setup AP"
    )


def stage_base_root(work_dir: Path, raw: bytes) -> Path:
    """Place the immutable base root in a new or matching private work directory."""

    destination = work_dir / "base-rootfs.squashfs"
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != raw:
            raise DevelopmentInstallError("preserved base root changed")
        return destination
    atomic_write(destination, raw)
    destination.chmod(0o600)
    return destination
