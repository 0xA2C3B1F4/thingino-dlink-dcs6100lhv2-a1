"""Machine-readable host and artifact gates for the DCS-6100 workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .mtd3_image import Mtd3ImageError, validate_personal_mtd3_image
from .private_config import (
    PrivateConfigError,
    load_private_config_for_session,
    load_private_wpa_config,
    station_wifi_binding,
)
from .recovery_ap.host import (
    RecoveryApHostError,
    load_host_session,
    probe_recovery_ap,
    resolve_recovery_ap_station_candidates,
)
from .runtime_candidate import RuntimeCandidateError, build_runtime_candidate_package
from .vendor_bundle import VendorBundleError, load_vendor_bundle


class WorkflowPreflightError(ValueError):
    """The requested workflow cannot start from the observed inputs."""


MODES = (
    "production-build",
    "collector-build",
    "ram-candidate",
    "runtime-candidate",
    "recovery-preflight",
    "build-personal-mtd3",
    "install-mtd3",
    "post-boot-health",
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _git(project_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise WorkflowPreflightError("project root is not a readable Git worktree") from exc
    return completed.stdout.strip()


def _regular(path: Path | None, label: str) -> Path:
    if path is None:
        raise WorkflowPreflightError(f"{label} is required")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise WorkflowPreflightError(f"{label} is missing") from exc
    if path.is_symlink() or not resolved.is_file():
        raise WorkflowPreflightError(f"{label} is not a regular file")
    return resolved


def _provenance(
    path: Path,
    *,
    image_payload_sha256: str,
    image_payload_size: int,
    vendor_bundle_sha256: str | None,
) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        policies = document["policies"]
        system = document["system"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise WorkflowPreflightError("image provenance is invalid") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(policies, dict)
        or not isinstance(system, dict)
        or system.get("sha256") != image_payload_sha256
        or system.get("size") != image_payload_size
    ):
        raise WorkflowPreflightError("image provenance does not bind the supplied image")
    recorded_bundle = policies.get("vendor_bundle_sha256")
    if vendor_bundle_sha256 is not None and recorded_bundle != vendor_bundle_sha256:
        raise WorkflowPreflightError("image provenance does not bind the supplied vendor bundle")
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "vendor_bundle_sha256": recorded_bundle,
    }


def observe_camera_state(
    *,
    session_dir: Path,
    recovery_host: str = "192.168.88.1",
    station_ipv4: str | None = None,
) -> dict[str, object]:
    """Classify the live camera through authenticated, read-only protocol probes."""

    session = load_host_session(session_dir)
    observations: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    try:
        result = probe_recovery_ap(
            session_dir=session_dir,
            host=recovery_host,
            expected_state="ap",
        )
        observations.append(
            {"host": recovery_host, "state": "recovery-ap", "proof": result}
        )
        attempts.append(
            {
                "host": recovery_host,
                "result": "authenticated",
                "state": "recovery-ap",
            }
        )
    except RecoveryApHostError as exc:
        attempts.append(
            {
                "error": str(exc),
                "host": recovery_host,
                "result": "rejected",
                "state": "recovery-ap",
            }
        )
    if station_ipv4 is None:
        try:
            mdns_name, hosts = resolve_recovery_ap_station_candidates(session_dir)
            discovery = "session-pinned-mdns"
        except RecoveryApHostError as exc:
            mdns_name, hosts = session.station_mdns_name, ()
            discovery = "session-pinned-mdns"
            attempts.append(
                {
                    "error": str(exc),
                    "mdns_name": mdns_name,
                    "result": "rejected",
                    "state": "station-discovery",
                }
            )
    else:
        mdns_name, hosts = session.station_mdns_name, (station_ipv4,)
        discovery = "explicit-private-ipv4"
    for host in hosts:
        try:
            result = probe_recovery_ap(
                session_dir=session_dir,
                host=host,
                expected_state="station",
            )
            observations.append(
                {
                    "discovery": discovery,
                    "host": host,
                    "mdns_name": mdns_name,
                    "state": "station",
                    "proof": result,
                }
            )
            attempts.append(
                {
                    "discovery": discovery,
                    "host": host,
                    "result": "authenticated",
                    "state": "station",
                }
            )
        except RecoveryApHostError as exc:
            attempts.append(
                {
                    "discovery": discovery,
                    "error": str(exc),
                    "host": host,
                    "result": "rejected",
                    "state": "station",
                }
            )
    states = {str(item["state"]) for item in observations}
    if not observations:
        classification = "unreachable"
    elif len(observations) != 1 or len(states) != 1:
        classification = "ambiguous"
    else:
        classification = str(observations[0]["state"])
    return {
        "classification": classification,
        "attempts": attempts,
        "nor_writes": False,
        "observations": observations,
        "observed_at": _now(),
        "schema_version": 1,
    }


def run_workflow_preflight(
    *,
    project_root: Path,
    mode: str,
    data_volume: Path,
    session_dir: Path | None = None,
    vendor_bundle_dir: Path | None = None,
    private_config_dir: Path | None = None,
    expected_wpa_config_path: Path | None = None,
    image_path: Path | None = None,
    provenance_path: Path | None = None,
    rootfs_path: Path | None = None,
    output_dir: Path | None = None,
    work_dir: Path | None = None,
    station_ipv4: str | None = None,
    camera_observation: dict[str, object] | None = None,
) -> dict[str, object]:
    if mode not in MODES:
        raise WorkflowPreflightError("workflow mode is unsupported")
    root = project_root.expanduser().resolve(strict=True)
    expected_markers = (
        root / "installer/user_cli.py",
        root / "profiles/dlink-dcs6100lhv2-a1/artifact-limits.json",
    )
    if not all(path.is_file() for path in expected_markers):
        raise WorkflowPreflightError("worktree is not the DCS-6100LHV2 project root")
    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise WorkflowPreflightError("command must run from the project worktree root")
    head = _git(root, "rev-parse", "HEAD")
    changed = [line for line in _git(root, "status", "--short").splitlines() if line]
    volume_ready = data_volume.is_mount() and os.access(root, os.W_OK)
    checks: dict[str, object] = {
        "data_volume": {
            "mounted": data_volume.is_mount(),
            "project_writable": os.access(root, os.W_OK),
            "ready": volume_ready,
        },
        "worktree": {
            "dirty": bool(changed),
            "head": head,
            "root": str(root),
            "status": changed,
        },
    }
    failures: list[str] = []
    if not volume_ready:
        failures.append("data-volume-unavailable")
    if mode == "production-build" and changed:
        failures.append("production-build-requires-clean-worktree")

    needs_session = mode in {
        "ram-candidate",
        "runtime-candidate",
        "recovery-preflight",
        "build-personal-mtd3",
        "install-mtd3",
        "post-boot-health",
    }
    if needs_session:
        if session_dir is None:
            failures.append("session-required")
        else:
            try:
                session = load_host_session(session_dir)
                checks["session"] = {
                    "ready": True,
                    "station_mdns_name": session.station_mdns_name,
                }
            except RecoveryApHostError as exc:
                failures.append("session-invalid")
                checks["session"] = {"error": str(exc), "ready": False}

    vendor_sha: str | None = None
    if mode in {"build-personal-mtd3", "install-mtd3"}:
        if vendor_bundle_dir is None:
            failures.append("vendor-bundle-required")
        else:
            try:
                bundle = load_vendor_bundle(vendor_bundle_dir)
                vendor_sha = bundle.bundle_sha256
                checks["vendor_bundle"] = {"ready": True, "sha256": vendor_sha}
            except VendorBundleError as exc:
                failures.append("vendor-bundle-invalid")
                checks["vendor_bundle"] = {"error": str(exc), "ready": False}
        if private_config_dir is None or session_dir is None:
            failures.append("private-config-required")
        else:
            try:
                private = load_private_config_for_session(
                    output_dir=private_config_dir,
                    session_dir=session_dir,
                )
                checks["private_config"] = {
                    "credential_set_id": private.credential_set_id,
                    "ready": True,
                }
                if mode == "build-personal-mtd3":
                    if expected_wpa_config_path is None:
                        failures.append("station-wifi-confirmation-required")
                        checks["station_wifi"] = {"ready": False}
                    else:
                        expected_wpa = load_private_wpa_config(
                            expected_wpa_config_path,
                            independent_from=(
                                private_config_dir / "wpa_supplicant.conf"
                            ),
                        )
                        bound = hmac.compare_digest(
                            station_wifi_binding(private.wpa_config),
                            station_wifi_binding(expected_wpa),
                        )
                        checks["station_wifi"] = {
                            "bound": bound,
                            "ready": bound,
                        }
                        if not bound:
                            failures.append("station-wifi-confirmation-mismatch")
            except PrivateConfigError as exc:
                failures.append("private-config-invalid")
                checks["private_config"] = {"error": str(exc), "ready": False}

    if mode == "build-personal-mtd3":
        if output_dir is None:
            failures.append("output-directory-required")
        elif output_dir.exists():
            failures.append("output-directory-already-exists")
        if work_dir is None:
            failures.append("work-directory-required")

    if mode in {"install-mtd3", "post-boot-health"}:
        try:
            image_file = _regular(image_path, "personal mtd3 image")
            image = validate_personal_mtd3_image(image_file.read_bytes())
            checks["image"] = {
                "payload_sha256": image.payload_sha256,
                "sha256": image.image_sha256,
                "size": len(image.raw),
            }
            provenance_file = _regular(provenance_path, "image provenance")
            checks["provenance"] = _provenance(
                provenance_file,
                image_payload_sha256=image.payload_sha256,
                image_payload_size=image.payload_size,
                vendor_bundle_sha256=vendor_sha,
            )
        except (Mtd3ImageError, WorkflowPreflightError) as exc:
            failures.append("image-or-provenance-invalid")
            checks["image"] = {"error": str(exc), "ready": False}

    if mode == "runtime-candidate":
        if rootfs_path is None or provenance_path is None:
            failures.append("runtime-candidate-inputs-required")
        else:
            try:
                _, candidate = build_runtime_candidate_package(
                    rootfs_path=rootfs_path,
                    provenance_path=provenance_path,
                )
                checks["runtime_candidate"] = {
                    **candidate,
                    "ready": True,
                }
            except RuntimeCandidateError as exc:
                failures.append("runtime-candidate-invalid")
                checks["runtime_candidate"] = {"error": str(exc), "ready": False}

    if camera_observation is not None:
        checks["camera"] = camera_observation
        classification = camera_observation.get("classification")
        if classification in {"ambiguous", "unreachable"}:
            failures.append(f"camera-{classification}")
        if mode == "runtime-candidate" and classification != "station":
            failures.append("runtime-candidate-requires-station")

    next_argv: list[str] | None
    if mode in {"production-build", "collector-build"}:
        next_argv = ["python3", "scripts/source_checkout.py", "validate-lock"]
    elif mode == "recovery-preflight":
        next_argv = [
            "thingino-dlink",
            "workflow-preflight",
            "--json",
            "--mode",
            "recovery-preflight",
            "--project-root",
            str(root),
            "--data-volume",
            str(data_volume),
            "--session-dir",
            str(session_dir),
            "--observe-camera",
        ]
        if station_ipv4 is not None:
            next_argv.extend(["--station-ipv4", station_ipv4])
        if camera_observation is not None or session_dir is None:
            next_argv = [
                "thingino-dlink",
                "status",
                "--json",
            ]
    elif mode == "build-personal-mtd3":
        next_argv = [
            "thingino-dlink",
            "build-personal-mtd3",
            "--json",
            "--work-dir",
            str(work_dir),
            "--session-dir",
            str(session_dir),
            "--private-config-dir",
            str(private_config_dir),
            "--expected-wpa-config",
            str(expected_wpa_config_path),
            "--vendor-bundle-dir",
            str(vendor_bundle_dir),
            "--output-dir",
            str(output_dir),
        ]
    elif mode == "install-mtd3":
        next_argv = [
            "dcs6100-thingino",
            "install-personal-mtd3",
            "--session-dir",
            str(session_dir),
            "--image",
            str(image_path),
            "--vendor-bundle-dir",
            str(vendor_bundle_dir),
            "--image-provenance",
            str(provenance_path),
        ]
    elif mode == "post-boot-health":
        image_identity = checks.get("image")
        expected = image_identity.get("sha256") if isinstance(image_identity, dict) else None
        next_argv = [
            "dcs6100-thingino",
            "prove-thingino-health",
            "--session-dir",
            str(session_dir),
            "--expected-mtd3-sha256",
            str(expected),
        ]
    elif mode == "runtime-candidate":
        if camera_observation is None:
            next_argv = [
                "python3",
                "-m",
                "installer.user_cli",
                "workflow-preflight",
                "--json",
                "--mode",
                "runtime-candidate",
                "--project-root",
                str(root),
                "--data-volume",
                str(data_volume),
                "--session-dir",
                str(session_dir),
                "--rootfs",
                str(rootfs_path),
                "--image-provenance",
                str(provenance_path),
                "--observe-camera",
            ]
            if station_ipv4 is not None:
                next_argv.extend(["--station-ipv4", station_ipv4])
        else:
            observations = camera_observation.get("observations")
            host = None
            expected_mtd3_sha256 = None
            if isinstance(observations, list) and len(observations) == 1:
                observation = observations[0]
                if isinstance(observation, dict) and isinstance(observation.get("host"), str):
                    host = observation["host"]
                    proof = observation.get("proof")
                    if isinstance(proof, dict) and isinstance(
                        proof.get("mtd3_sha256"), str
                    ):
                        expected_mtd3_sha256 = proof["mtd3_sha256"]
            if (
                host is None
                or expected_mtd3_sha256 is None
                or re.fullmatch(r"[0-9a-f]{64}", expected_mtd3_sha256) is None
            ):
                failures.append("runtime-candidate-station-host-invalid")
                next_argv = None
            else:
                next_argv = [
                    "python3",
                    "-m",
                    "installer.user_cli",
                    "runtime-candidate",
                    "stage",
                    "--json",
                    "--session-dir",
                    str(session_dir),
                    "--host",
                    host,
                    "--rootfs",
                    str(rootfs_path),
                    "--image-provenance",
                    str(provenance_path),
                    "--expected-mtd3-sha256",
                    expected_mtd3_sha256,
                    "--approve-volatile-runtime-restart",
                ]
    else:
        next_argv = None
    if mode == "ram-candidate":
        failures.append("live-ram-command-not-implemented")
    authorization_required = mode in {"install-mtd3", "runtime-candidate"} and not failures
    return {
        "authorization_required_before_next_command": authorization_required,
        "checks": checks,
        "failures": sorted(set(failures)),
        "mode": mode,
        "next_argv": next_argv if not failures else None,
        "next_command": shlex.join(next_argv) if not failures and next_argv else None,
        "observed_at": _now(),
        "ok": not failures,
        "safe_next_action": "run-next-command" if not failures else "stop",
        "schema_version": 1,
    }
