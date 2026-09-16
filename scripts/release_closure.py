#!/usr/bin/env python3
"""Build a deterministic, technical-only Raptor candidate closure.

This command consumes one completed ``local-build-run.universal.json`` run and
writes two host-side manifests.  It does not publish, sign, install, or alter
any release-policy ledger.  The output contains identities and provenance for
the exact run; it is intentionally not a release approval.

Usage::

    python3 scripts/release_closure.py --run-dir RUN --output-dir OUTPUT
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    # ``python scripts/release_closure.py`` starts with only scripts/ on
    # sys.path.  Keep the module usable both as a script and as ``-m``.
    sys.path.insert(0, str(ROOT))

from installer.local_build_support import _regular, _sha256  # noqa: E402
from installer.final_bundle import validate_universal_final_bundle  # noqa: E402
from installer.media import validate_install_set  # noqa: E402
from installer.raptor_full_component import validate_component  # noqa: E402
from installer.raptor_source import (  # noqa: E402
    FULL_MEDIA_SOURCES,
    recipe_identity,
    source_lock,
)
from installer.stage1.build import BOOTSTRAP_FILENAME  # noqa: E402
from installer.sd_package import atomic_write  # noqa: E402
from scripts.source_checkout import load_lock  # noqa: E402


SCHEMA_VERSION = 1
TECHNICAL_STATUS = (
    "host-built-and-inspected; complete-firmware reproducibility accepted"
)
LEGAL_REVIEW_STATUS = "not-assessed"
REDISTRIBUTION_STATUS = "not-authorized-by-this-repository"
RUN_MANIFEST_NAME = "local-build-run.universal.json"
REPRODUCIBILITY_NAME = "reproducibility.json"
INSPECTION_NAME = "logs/inspect-install-set.json"
INSTALL_SET_NAME = "install-set"
PREPARED_ROOT_MANIFEST = "universal-final-root/final-root.universal.json"
PREPARED_ROOT_SYSTEM = "universal-final-root/system.universal.squashfs"
FINAL_ROOT_MANIFEST = "raptor-final-root/final-root.universal.json"
FINAL_ROOT_SYSTEM = "raptor-final-root/system.universal.squashfs"
RAPTOR_COMPONENT = "raptor-full/raptor-full-component.tar.gz"
GLOBAL_SOURCE_LOCK = "sources.lock.json"
RAPTOR_SOURCE_LOCK = "components/raptor/source-build-lock.json"
HEADERS_INPUT = "components/raptor/headers-input.json"

TARGET = {"hardware_revision": "A1", "model": "DCS-6100LHV2"}
INSTALL_SET_MEMBERS = frozenset(
    {
        BOOTSTRAP_FILENAME,
        "THINGINO2.BIN",
        "install-set.manifest.json",
        "stage1-bootstrap.squashfs",
        "thingino-universal.tgb",
    }
)
BUILD_RESULT_FILES = frozenset(
    {
        "source-preparation.json",
        "thingino-base.manifest.json",
        "thingino-base.squashfs",
        "thingino-linux.config",
    }
)
SPLIT_KERNEL_FILES = frozenset(
    {
        "final-kernel.uimage",
        "final-linux.config",
        "installer-jzmmc_v12.ko",
        "installer-kernel.uimage",
    }
)
REPRODUCTION_FILES = frozenset(
    {f"base/{name}" for name in BUILD_RESULT_FILES}
    | {"raptor/raptor-full-component.tar.gz"}
    | {"final-root/final-root.universal.json", "final-root/system.universal.squashfs"}
    | {f"split-kernels/{name}" for name in SPLIT_KERNEL_FILES}
    | {f"install-set/{name}" for name in INSTALL_SET_MEMBERS}
)
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")


class ReleaseClosureError(ValueError):
    """A completed run does not contain a safe, complete candidate closure."""


def _fail(message: str) -> ReleaseClosureError:
    return ReleaseClosureError(message)


def _regular_file(path: Path, label: str) -> Path:
    try:
        return _regular(path, label)
    except (OSError, ValueError) as exc:
        raise _fail(f"{label} is missing or not a regular file") from exc


def _identity_from_raw(raw: bytes, relative: str) -> dict[str, object]:
    return {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _read_snapshot(
    path: Path, relative: str, label: str
) -> tuple[bytes, dict[str, object]]:
    path = _regular_file(path, label)
    try:
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise _fail(f"cannot read {label}") from exc
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or len(raw) != before.st_size
    ):
        raise _fail(f"{label} changed while being read")
    return raw, _identity_from_raw(raw, relative)


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise _fail(f"{label} must be a JSON object")
    return value


def _read_json(
    path: Path, relative: str, label: str
) -> tuple[bytes, dict[str, Any], dict[str, object]]:
    raw, identity = _read_snapshot(path, relative, label)
    return raw, _parse_json(raw, label), identity


def _digest_identity(path: Path, relative: str, label: str) -> dict[str, object]:
    _, identity = _read_snapshot(path, relative, label)
    return identity


def _require_digest(value: object, label: str, pattern: re.Pattern[str] = HEX64) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _fail(f"{label} is not a lowercase hexadecimal digest")
    return value


def _require_bool(value: object, expected: bool, label: str) -> None:
    if value is not expected:
        raise _fail(f"{label} must be {str(expected).lower()}")


def _require_exact(value: object, expected: object, label: str) -> None:
    if value != expected:
        raise _fail(f"{label} is not the required value")


def _safe_relative(relative: str, label: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != relative
    ):
        raise _fail(f"{label} is not a safe relative path")
    return path


def _validate_reproducibility(
    run_dir: Path,
    run_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, object], dict[str, dict[str, object]]]:
    run_repro = run_manifest.get("reproducibility")
    if not isinstance(run_repro, dict):
        raise _fail("completed run lacks reproducibility evidence")
    _, external_repro, repro_identity = _read_json(
        run_dir / REPRODUCIBILITY_NAME,
        REPRODUCIBILITY_NAME,
        "reproducibility evidence",
    )
    if external_repro != run_repro:
        raise _fail("run manifest and reproducibility evidence differ")
    _require_exact(run_repro.get("builds"), 2, "reproducibility builds")
    _require_bool(run_repro.get("byte_identical"), True, "reproducibility byte_identical")
    _require_bool(
        run_repro.get("component_cache_used"), False,
        "reproducibility component_cache_used",
    )
    _require_bool(
        run_repro.get("inspections_accepted"), True,
        "reproducibility inspections_accepted",
    )
    _require_exact(
        run_repro.get("scope"), "complete-firmware", "reproducibility scope"
    )
    _require_exact(run_repro.get("differences"), [], "reproducibility differences")
    files = run_repro.get("files")
    if not isinstance(files, dict) or set(files) != REPRODUCTION_FILES:
        raise _fail("complete-firmware reproducibility file allowlist changed")

    actual_roots = {
        "base": run_dir / "build-a" / "result",
        "raptor": run_dir / "raptor-full",
        "final-root": run_dir / "raptor-final-root",
        "split-kernels": run_dir / "split-kernels",
        "install-set": run_dir / INSTALL_SET_NAME,
    }
    evidence: dict[str, dict[str, object]] = {}
    for relative, raw_identity in sorted(files.items()):
        if not isinstance(raw_identity, dict):
            raise _fail(f"reproducibility identity is malformed: {relative}")
        expected_keys = {"sha256", "size"}
        if set(raw_identity) != expected_keys:
            raise _fail(f"reproducibility identity fields changed: {relative}")
        expected_sha = _require_digest(raw_identity.get("sha256"), relative)
        size = raw_identity.get("size")
        if not isinstance(size, int) or size < 0:
            raise _fail(f"reproducibility size is invalid: {relative}")
        prefix, name = relative.split("/", 1)
        actual = actual_roots[prefix] / name
        identity = _digest_identity(actual, relative, f"reproducibility artifact {relative}")
        if identity["sha256"] != expected_sha or identity["size"] != size:
            raise _fail(f"reproducibility artifact differs: {relative}")
        evidence[relative] = identity

    return run_repro, repro_identity, evidence


def _validate_run(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, object], dict[str, object], dict[str, Any], dict[str, dict[str, object]]]:
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise _fail("run directory is missing or not a directory")
    try:
        run_dir = run_dir.resolve(strict=True)
    except OSError as exc:
        raise _fail("run directory cannot be resolved") from exc
    manifest_raw, run_manifest, run_identity = _read_json(
        run_dir / RUN_MANIFEST_NAME,
        RUN_MANIFEST_NAME,
        "completed run manifest",
    )
    _require_exact(run_manifest.get("schema_version"), 1, "run schema")
    _require_exact(run_manifest.get("artifact_scope"), "model-universal", "run artifact scope")
    _require_exact(run_manifest.get("media_backend"), "raptor", "run media backend")
    _require_exact(run_manifest.get("install_set"), INSTALL_SET_NAME, "run install set")
    _require_exact(
        run_manifest.get("provisioning"),
        "separate-per-camera-sidecar",
        "run provisioning mode",
    )
    _require_bool(run_manifest.get("private"), False, "run private flag")
    _require_bool(
        run_manifest.get("public_firmware_release_gate_consulted"),
        False,
        "run public release-gate consultation",
    )
    _require_exact(
        run_manifest.get("status"),
        "host-built and inspected; live installation not authorized",
        "run status",
    )
    _require_exact(run_manifest.get("build_count"), 2, "run build count")
    project_head = _require_digest(run_manifest.get("project_head"), "run project HEAD", HEX40)
    source_lock_sha = _require_digest(
        run_manifest.get("sources_lock_sha256"), "run sources.lock.json identity"
    )
    inspection = run_manifest.get("inspection")
    if not isinstance(inspection, dict):
        raise _fail("completed run lacks install-set inspection")
    _require_bool(inspection.get("ok"), True, "install-set inspection")
    _require_exact(inspection.get("schema_version"), 2, "install-set inspection schema")
    _require_exact(
        inspection.get("layout"),
        "dcs6100lhv2-a1-mtd3-split-v1",
        "install-set inspection layout",
    )
    inspection_raw, inspection_document, inspection_identity = _read_json(
        run_dir / INSPECTION_NAME,
        INSPECTION_NAME,
        "install-set inspection evidence",
    )
    if inspection_document != inspection:
        raise _fail("run manifest and install-set inspection differ")
    run_repro, repro_identity, repro_files = _validate_reproducibility(
        run_dir, run_manifest
    )
    # Keep these reads in the validation path.  They make a malformed or
    # truncated manifest fail before any output directory is touched.
    if not manifest_raw or not inspection_raw:
        raise _fail("completed run evidence is empty")
    return (
        run_manifest,
        run_identity,
        inspection_identity,
        {"manifest": run_repro, "identity": repro_identity},
        repro_files,
    )


def _public_key_candidates(run_dir: Path, explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [_regular_file(Path(explicit), "universal final-bundle public key")]
    build_root = run_dir.parent.parent
    workspace_root = build_root.parent
    candidates = [
        run_dir / "model-signing/release-ed25519.pub",
        build_root / "model-signing/release-ed25519.pub",
        workspace_root / f"{build_root.name}-private/model-signing/release-ed25519.pub",
        workspace_root / "model-build-private/model-signing/release-ed25519.pub",
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=True)
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    if not result:
        raise _fail("universal final-bundle public key evidence is missing")
    return result


def _validate_tgb(
    tgb_raw: bytes,
    *,
    run_dir: Path,
    universal_public_key: Path | None,
    final_system_raw: bytes,
    final_system_identity: dict[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    for public_key in _public_key_candidates(run_dir, universal_public_key):
        try:
            bundle = validate_universal_final_bundle(tgb_raw, public_key=public_key)
        except (OSError, TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        embedded_system = bundle.members.get("images/system.squashfs")
        if embedded_system != final_system_raw:
            raise _fail(
                "validated universal TGB system differs from the Raptor final-root system"
            )
        key_raw, key_identity = _read_snapshot(
            public_key,
            "release-ed25519.pub",
            "universal final-bundle public key",
        )
        if not key_raw:
            raise _fail("universal final-bundle public key is empty")
        return {
            "validator": "installer.final_bundle.validate_universal_final_bundle",
            "public_key_sha256": key_identity["sha256"],
            "embedded_system_sha256": final_system_identity["sha256"],
        }
    detail = errors[-1] if errors else "no candidate public key"
    raise _fail(f"existing universal final-bundle validator rejected the TGB: {detail}")


def _validate_install_set(
    run_dir: Path,
    run_manifest: dict[str, Any],
    *,
    repro_files: dict[str, dict[str, object]],
    final_system_raw: bytes,
    final_system_identity: dict[str, object],
    universal_public_key: Path | None,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, dict[str, object]],
    dict[str, object],
]:
    install_dir = run_dir / INSTALL_SET_NAME
    if install_dir.is_symlink() or not install_dir.is_dir():
        raise _fail("completed run install-set directory is missing")
    try:
        names = {entry.name for entry in install_dir.iterdir()}
    except OSError as exc:
        raise _fail("cannot inspect completed run install-set") from exc
    if names != INSTALL_SET_MEMBERS:
        raise _fail("completed run install-set member allowlist changed")
    member_snapshots: dict[str, tuple[bytes, dict[str, object]]] = {
        name: _read_snapshot(
            install_dir / name,
            f"{INSTALL_SET_NAME}/{name}",
            f"install-set member {name}",
        )
        for name in sorted(names)
    }
    member_identities = {
        name: snapshot[1]
        for name, snapshot in member_snapshots.items()
    }
    for name, identity in sorted(member_identities.items()):
        _reproducibility_matches(
            identity,
            repro_files,
            f"{INSTALL_SET_NAME}/{name}",
            f"install-set member {name}",
        )
    manifest_raw, manifest_identity = member_snapshots["install-set.manifest.json"]
    manifest = _parse_json(manifest_raw, "install-set manifest")
    bootstrap_raw = member_snapshots[BOOTSTRAP_FILENAME][0]
    stage2_raw = member_snapshots["THINGINO2.BIN"][0]
    tgb_raw = member_snapshots["thingino-universal.tgb"][0]
    try:
        validate_install_set(
            bootstrap_bytes=bootstrap_raw,
            stage2_bytes=stage2_raw,
            manifest_bytes=manifest_raw,
            bootstrap_name=BOOTSTRAP_FILENAME,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise _fail(f"existing install-set validator rejected the run: {exc}") from exc
    tgb_validation = _validate_tgb(
        tgb_raw,
        run_dir=run_dir,
        universal_public_key=universal_public_key,
        final_system_raw=final_system_raw,
        final_system_identity=final_system_identity,
    )
    _require_exact(manifest.get("schema_version"), 2, "install-set manifest schema")
    _require_exact(manifest.get("artifact_scope"), "model-universal", "install-set artifact scope")
    _require_exact(
        manifest.get("provisioning"),
        "separate-per-camera-audit-and-jffs2",
        "install-set provisioning",
    )
    _require_exact(manifest.get("target"), TARGET, "install-set target")
    _require_exact(
        manifest.get("status"),
        "host-built install set; generation does not authorize live use",
        "install-set status",
    )
    tgb = member_identities["thingino-universal.tgb"]
    _require_exact(
        manifest.get("universal_firmware_sha256"),
        tgb["sha256"],
        "install-set universal firmware identity",
    )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != INSTALL_SET_MEMBERS - {
        "install-set.manifest.json"
    }:
        raise _fail("install-set manifest artifact allowlist changed")
    for name in sorted(artifacts):
        if artifacts[name] != {
            "sha256": member_identities[name]["sha256"],
            "size": member_identities[name]["size"],
        }:
            raise _fail(f"install-set manifest does not bind {name}")
    inspection = run_manifest["inspection"]
    artifact_sizes = inspection.get("artifact_sizes")
    if not isinstance(artifact_sizes, dict) or artifact_sizes != {
        name: member_identities[name]["size"]
        for name in sorted(artifacts)
    }:
        raise _fail("install-set inspection does not bind exact artifact sizes")
    return (
        manifest,
        manifest_identity,
        tgb,
        member_identities,
        tgb_validation,
    )


def _reproducibility_matches(
    identity: dict[str, object],
    repro_files: dict[str, dict[str, object]],
    relative: str,
    label: str,
) -> None:
    expected = repro_files.get(relative)
    if expected is None or identity["sha256"] != expected["sha256"] or identity["size"] != expected["size"]:
        raise _fail(f"{label} does not match reproducibility evidence")


def _validate_prepared_root(
    run_dir: Path,
    repro_files: dict[str, dict[str, object]],
) -> tuple[dict[str, Any], dict[str, object], bytes, dict[str, object]]:
    manifest_raw, manifest, manifest_identity = _read_json(
        run_dir / PREPARED_ROOT_MANIFEST,
        PREPARED_ROOT_MANIFEST,
        "prepared universal final-root manifest",
    )
    system_raw, system_identity = _read_snapshot(
        run_dir / PREPARED_ROOT_SYSTEM,
        PREPARED_ROOT_SYSTEM,
        "prepared universal final-root system",
    )
    _require_exact(manifest.get("schema_version"), 1, "prepared-root schema")
    _require_exact(manifest.get("artifact_scope"), "model-universal", "prepared-root artifact scope")
    _require_exact(
        manifest.get("media_profile"),
        "source-built-camera-support-v1",
        "prepared-root media profile",
    )
    _require_bool(manifest.get("composition_required"), True, "prepared-root composition_required")
    _require_bool(manifest.get("contains_device_secrets"), False, "prepared-root secrets flag")
    _require_bool(manifest.get("provisioning_required"), True, "prepared-root provisioning_required")
    _require_exact(manifest.get("target"), TARGET, "prepared-root target")
    _require_digest(manifest.get("source_sha256"), "prepared-root base source identity")
    system = manifest.get("system")
    if not isinstance(system, dict) or set(system) != {"filename", "sha256", "size"}:
        raise _fail("prepared-root system identity is malformed")
    if system != {
        "filename": "system.universal.squashfs",
        "sha256": system_identity["sha256"],
        "size": system_identity["size"],
    }:
        raise _fail("prepared-root manifest does not bind its system image")
    base_identity = repro_files.get("base/thingino-base.squashfs")
    if base_identity is None or manifest["source_sha256"] != base_identity["sha256"]:
        raise _fail("prepared-root manifest does not bind the clean build base")
    if not manifest_raw:
        raise _fail("prepared-root manifest is empty")
    return manifest, manifest_identity, system_raw, system_identity


def _validate_final_root(
    run_dir: Path,
    repository: Path,
    source_lock_document: dict[str, Any],
    source_lock_sha: str,
    repro_files: dict[str, dict[str, object]],
    prepared_system_raw: bytes,
    prepared_system_identity: dict[str, object],
) -> tuple[
    dict[str, Any],
    dict[str, object],
    bytes,
    dict[str, object],
    dict[str, object],
]:
    manifest_raw, manifest, manifest_identity = _read_json(
        run_dir / FINAL_ROOT_MANIFEST,
        FINAL_ROOT_MANIFEST,
        "Raptor final-root manifest",
    )
    _reproducibility_matches(
        manifest_identity,
        repro_files,
        "final-root/final-root.universal.json",
        "Raptor final-root manifest",
    )
    system_raw, system_identity = _read_snapshot(
        run_dir / FINAL_ROOT_SYSTEM,
        FINAL_ROOT_SYSTEM,
        "Raptor final-root system",
    )
    _reproducibility_matches(
        system_identity, repro_files, "final-root/system.universal.squashfs",
        "Raptor final-root system",
    )
    _require_exact(manifest.get("schema_version"), 1, "final-root schema")
    _require_exact(manifest.get("artifact_scope"), "model-universal", "final-root artifact scope")
    _require_exact(manifest.get("media_profile"), "full-raptor-v1", "final-root media profile")
    _require_bool(manifest.get("composition_required"), False, "final-root composition_required")
    _require_bool(manifest.get("contains_device_secrets"), False, "final-root secrets flag")
    _require_bool(manifest.get("provisioning_required"), True, "final-root provisioning_required")
    _require_exact(manifest.get("target"), TARGET, "final-root target")
    _require_digest(manifest.get("source_sha256"), "final-root prepared source identity")
    _require_digest(manifest.get("vendor_bundle_sha256"), "final-root vendor bundle identity")
    system = manifest.get("system")
    if not isinstance(system, dict) or set(system) != {"filename", "sha256", "size"}:
        raise _fail("final-root system identity is malformed")
    if system != {
        "filename": "system.universal.squashfs",
        "sha256": system_identity["sha256"],
        "size": system_identity["size"],
    }:
        raise _fail("final-root manifest does not bind its system image")
    raptor = manifest.get("raptor_full")
    if not isinstance(raptor, dict) or set(raptor) != {"component_sha256", "source_build"}:
        raise _fail("final-root lacks exact full-Raptor provenance")
    component_raw, component_identity = _read_snapshot(
        run_dir / RAPTOR_COMPONENT,
        RAPTOR_COMPONENT,
        "full Raptor component",
    )
    _reproducibility_matches(
        component_identity, repro_files, "raptor/raptor-full-component.tar.gz",
        "full Raptor component",
    )
    _require_exact(
        raptor.get("component_sha256"), component_identity["sha256"],
        "final-root Raptor component identity",
    )
    source_build = raptor.get("source_build")
    if not isinstance(source_build, dict):
        raise _fail("final-root Raptor source build evidence is missing")
    required_source_build = {
        "build_inputs", "files", "kind", "recipe", "schema_version", "source_trees"
    }
    if set(source_build) != required_source_build:
        raise _fail("final-root Raptor source build fields changed")
    _require_exact(source_build.get("schema_version"), 1, "Raptor source build schema")
    _require_exact(source_build.get("kind"), "raptor-full-media-component", "Raptor source build kind")
    source_trees = source_build.get("source_trees")
    expected_trees = {
        name: spec["tree"] for name, spec in source_lock_document["sources"].items()
    }
    if source_trees != expected_trees:
        raise _fail("final-root Raptor source trees do not match the source lock")
    recipe = source_build.get("recipe")
    if not isinstance(recipe, dict):
        raise _fail("final-root Raptor recipe is missing")
    try:
        expected_recipe = recipe_identity(repository, full_media=True)
    except (OSError, TypeError, ValueError) as exc:
        raise _fail(f"cannot validate current Raptor recipe: {exc}") from exc
    if recipe != expected_recipe:
        raise _fail("final-root Raptor recipe does not match the repository")
    if recipe.get(GLOBAL_SOURCE_LOCK) != source_lock_sha:
        raise _fail("final-root Raptor recipe does not bind sources.lock.json")
    for relative, expected in sorted(recipe.items()):
        _safe_relative(relative, "Raptor recipe path")
        actual = _digest_identity(
            repository / Path(relative), relative, f"Raptor recipe input {relative}"
        )
        if actual["sha256"] != expected:
            raise _fail(f"Raptor recipe input changed: {relative}")
    build_inputs = source_build.get("build_inputs")
    if not isinstance(build_inputs, dict):
        raise _fail("final-root Raptor build inputs are missing")
    base_rootfs_sha = _require_digest(
        build_inputs.get("base_rootfs_sha256"),
        "Raptor prepared-root input identity",
    )
    if manifest["source_sha256"] != base_rootfs_sha or base_rootfs_sha != prepared_system_identity["sha256"]:
        raise _fail("final-root source_sha256 does not bind the prepared universal root")
    files = source_build.get("files")
    if not isinstance(files, dict) or not files:
        raise _fail("final-root Raptor payload identities are missing")
    try:
        _, component_manifest = validate_component(
            run_dir / RAPTOR_COMPONENT,
            expected_sha256=str(component_identity["sha256"]),
            root=repository,
            expected_build_inputs=build_inputs,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise _fail(f"existing Raptor component validator rejected the run: {exc}") from exc
    _, component_after = _read_snapshot(
        run_dir / RAPTOR_COMPONENT,
        RAPTOR_COMPONENT,
        "full Raptor component after validation",
    )
    if component_after != component_identity:
        raise _fail("full Raptor component changed during validation")
    if component_manifest != source_build:
        raise _fail("component and final-root Raptor provenance differ")
    if not component_raw or not manifest_raw:
        raise _fail("final-root evidence is empty")
    return (
        manifest,
        manifest_identity,
        system_raw,
        system_identity,
        component_identity,
    )


def _validate_source_locks(
    repository: Path,
    run_manifest: dict[str, Any],
    final_root: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    lock_path = repository / GLOBAL_SOURCE_LOCK
    _, lock_identity = _read_snapshot(
        lock_path, GLOBAL_SOURCE_LOCK, "global source lock"
    )
    _require_exact(
        run_manifest.get("sources_lock_sha256"), lock_identity["sha256"],
        "run sources.lock.json identity",
    )
    try:
        global_lock = load_lock(lock_path)
        _, lock_after = _read_snapshot(
            lock_path, GLOBAL_SOURCE_LOCK, "global source lock after validation"
        )
        if lock_after != lock_identity:
            raise _fail("global source lock changed during validation")
        _, raptor_before = _read_snapshot(
            repository / RAPTOR_SOURCE_LOCK,
            RAPTOR_SOURCE_LOCK,
            "Raptor source lock",
        )
        _, headers_before = _read_snapshot(
            repository / HEADERS_INPUT,
            HEADERS_INPUT,
            "Raptor headers input",
        )
        raptor_lock = source_lock(repository, full_media=True)
        _, raptor_after = _read_snapshot(
            repository / RAPTOR_SOURCE_LOCK,
            RAPTOR_SOURCE_LOCK,
            "Raptor source lock after validation",
        )
        _, headers_after = _read_snapshot(
            repository / HEADERS_INPUT,
            HEADERS_INPUT,
            "Raptor headers input after validation",
        )
    except (OSError, TypeError, ValueError) as exc:
        raise _fail(f"existing source-lock validator rejected the repository: {exc}") from exc
    if raptor_after != raptor_before or headers_after != headers_before:
        raise _fail("Raptor source-lock inputs changed during validation")
    if not isinstance(global_lock, dict) or not isinstance(raptor_lock, dict):
        raise _fail("source-lock validators did not return objects")
    sources = raptor_lock.get("sources")
    if not isinstance(sources, dict):
        raise _fail("Raptor source lock lacks sources")
    expected_names = FULL_MEDIA_SOURCES
    if set(sources) != expected_names or "ingenic-headers" not in sources:
        raise _fail("Raptor source lock must contain 12 sources plus headers")
    source_build = final_root.get("raptor_full", {}).get("source_build", {})
    recipe = source_build.get("recipe") if isinstance(source_build, dict) else None
    if not isinstance(recipe, dict):
        raise _fail("Raptor source recipe evidence is missing")
    if recipe.get(RAPTOR_SOURCE_LOCK) != raptor_before["sha256"]:
        raise _fail("Raptor recipe does not bind the Raptor source lock")
    if recipe.get(HEADERS_INPUT) != headers_before["sha256"]:
        raise _fail("Raptor recipe does not bind the headers input")
    return global_lock, raptor_lock, lock_identity, raptor_before, headers_before


def _notice_manifest(
    *,
    raptor_lock: dict[str, Any],
    global_source_lock: dict[str, object],
    raptor_source_lock: dict[str, object],
    headers_identity: dict[str, object],
) -> dict[str, object]:
    sources = raptor_lock["sources"]
    locks = []
    for name in sorted(sources):
        if name == "ingenic-headers":
            continue
        spec = copy.deepcopy(sources[name])
        if not isinstance(spec, dict):
            raise _fail(f"Raptor source lock entry is malformed: {name}")
        locks.append(
            {
                "name": name,
                "path": f"{RAPTOR_SOURCE_LOCK}#sources/{name}",
                **spec,
                "legal_review_status": LEGAL_REVIEW_STATUS,
                "redistribution": REDISTRIBUTION_STATUS,
            }
        )
    headers = copy.deepcopy(sources["ingenic-headers"])
    headers = {
        "name": "ingenic-headers",
        "path": HEADERS_INPUT,
        **headers,
        "legal_review_status": LEGAL_REVIEW_STATUS,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "technical_status": TECHNICAL_STATUS,
        "legal_review_status": LEGAL_REVIEW_STATUS,
        "redistribution": REDISTRIBUTION_STATUS,
        "notice": "Raptor source-lock index; legal review and redistribution authorization remain open.",
        "source_lock": {
            "global": global_source_lock,
            "raptor": raptor_source_lock,
            "headers": headers_identity,
        },
        "raptor_source_locks": locks,
        "headers": headers,
    }


def build_closure(
    run_dir: Path,
    output_dir: Path,
    *,
    repository: Path = ROOT,
    universal_public_key: Path | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate one completed run and return the two deterministic manifests."""

    run_dir = Path(run_dir)
    repository = Path(repository)
    if repository.is_symlink() or not repository.is_dir():
        raise _fail("repository is missing or not a directory")
    (
        run_manifest,
        run_identity,
        inspection_identity,
        repro_record,
        repro_files,
    ) = _validate_run(run_dir)
    run_dir = run_dir.resolve(strict=True)
    source_lock_sha = str(run_manifest["sources_lock_sha256"])
    # Final-root validation needs the exact source lock map before notice
    # rendering, while source-lock validation needs the final-root recipe.
    try:
        raptor_lock = source_lock(repository, full_media=True)
    except (OSError, TypeError, ValueError) as exc:
        raise _fail(f"existing source-lock validator rejected the repository: {exc}") from exc
    prepared_root_manifest, prepared_root_identity, prepared_system_raw, prepared_system_identity = _validate_prepared_root(
        run_dir, repro_files
    )
    (
        final_root_manifest,
        final_root_identity,
        final_system_raw,
        final_system_identity,
        component_identity,
    ) = _validate_final_root(
        run_dir,
        repository,
        raptor_lock,
        source_lock_sha,
        repro_files,
        prepared_system_raw,
        prepared_system_identity,
    )
    (
        global_lock,
        raptor_lock,
        global_lock_identity,
        raptor_lock_identity,
        headers_identity,
    ) = _validate_source_locks(
        repository, run_manifest, final_root_manifest
    )
    (
        install_manifest,
        install_manifest_identity,
        tgb_identity,
        install_members,
        tgb_validation,
    ) = _validate_install_set(
        run_dir,
        run_manifest,
        repro_files=repro_files,
        final_system_raw=final_system_raw,
        final_system_identity=final_system_identity,
        universal_public_key=universal_public_key,
    )
    source_build = final_root_manifest["raptor_full"]["source_build"]
    candidate: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "technical-candidate-only",
        "technical_status": TECHNICAL_STATUS,
        "legal_review_status": LEGAL_REVIEW_STATUS,
        "redistribution": REDISTRIBUTION_STATUS,
        "live_install_authorized": False,
        "target": TARGET,
        "project_head": run_manifest["project_head"],
        "sources_lock_sha256": source_lock_sha,
        "project": {"head": run_manifest["project_head"]},
        "run": {
            "id": run_dir.name,
            "manifest": run_identity,
            "inspection": inspection_identity,
        },
        "source_lock": {
            "global": global_lock_identity,
            "raptor": raptor_lock_identity,
            "headers": headers_identity,
            "global_document": global_lock,
            "raptor_document": raptor_lock,
        },
        "artifacts": {
            "tgb": tgb_identity,
            "tgb_validation": tgb_validation,
            "install_set_manifest": install_manifest_identity,
            "install_set": [install_members[name] for name in sorted(install_members)],
            "prepared_root_manifest": prepared_root_identity,
            "prepared_root_system": prepared_system_identity,
            "final_root_manifest": final_root_identity,
            "final_root_system": final_system_identity,
            "raptor_component": component_identity,
        },
        "reproducibility": {
            "manifest": repro_record["identity"],
            "evidence": repro_record["manifest"],
            "files": [repro_files[name] for name in sorted(repro_files)],
        },
        "bindings": {
            "install_set_manifest": install_manifest,
            "final_root_manifest": final_root_manifest,
            "raptor_source_build": source_build,
        },
    }
    notice = _notice_manifest(
        raptor_lock=raptor_lock,
        global_source_lock=global_lock_identity,
        raptor_source_lock=raptor_lock_identity,
        headers_identity=headers_identity,
    )
    return candidate, notice


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode(
        "utf-8"
    )


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename a staged directory without replacing a destination."""

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        try:
            rename = libc.renameatx_np
        except AttributeError as exc:
            raise OSError(
                errno.ENOTSUP, "exclusive directory rename is unavailable"
            ) from exc
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-2, source_bytes, -2, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError:
            syscall_numbers = {
                "aarch64": 276,
                "armv7l": 382,
                "i386": 353,
                "ppc64le": 357,
                "riscv64": 276,
                "s390x": 347,
                "x86_64": 316,
            }
            try:
                syscall_number = syscall_numbers[os.uname().machine]
            except (AttributeError, KeyError) as exc:
                raise OSError(
                    errno.ENOTSUP, "exclusive directory rename is unavailable"
                ) from exc
            rename = libc.syscall
            rename.argtypes = [
                ctypes.c_long,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_long
            result = rename(
                syscall_number,
                -100,
                source_bytes,
                -100,
                destination_bytes,
                0x00000001,
            )
        else:
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            result = rename(-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable")
    if result != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, os.strerror(error), os.fspath(destination))


def _check_new_output_destination(run_dir: Path, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise _fail("output directory already exists")
    parent = output_dir.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise _fail("output directory parent is invalid")
    try:
        run_resolved = Path(run_dir).resolve(strict=True)
        output_resolved = output_dir.resolve(strict=False)
    except OSError as exc:
        raise _fail("cannot resolve output directory") from exc
    if output_resolved == run_resolved or run_resolved in output_resolved.parents:
        raise _fail("output directory must be outside the completed run")
    return output_dir


def _write_outputs(
    output_dir: Path, candidate: dict[str, object], notice: dict[str, object]
) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        raise _fail("output directory already exists")
    parent = output_dir.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise _fail("output directory parent is invalid")
        staged = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
        try:
            atomic_write(
                staged / "candidate-closure.json",
                _canonical_json(candidate),
                mode=0o600,
            )
            atomic_write(
                staged / "notice-manifest.json",
                _canonical_json(notice),
                mode=0o600,
            )
            if output_dir.exists() or output_dir.is_symlink():
                raise _fail("output directory appeared during publication")
            _rename_directory_noreplace(staged, output_dir)
            staged = None
        finally:
            if staged is not None and staged.exists():
                shutil.rmtree(staged)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise _fail("output directory appeared during publication") from exc
        raise _fail(f"cannot write closure outputs: {exc}") from exc


def generate(
    run_dir: Path,
    output_dir: Path,
    *,
    repository: Path = ROOT,
    universal_public_key: Path | None = None,
) -> tuple[Path, Path]:
    """Validate and write candidate-closure.json and notice-manifest.json."""

    output_dir = _check_new_output_destination(Path(run_dir), Path(output_dir))
    candidate, notice = build_closure(
        run_dir,
        output_dir,
        repository=repository,
        universal_public_key=universal_public_key,
    )
    _write_outputs(output_dir, candidate, notice)
    return output_dir / "candidate-closure.json", output_dir / "notice-manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--universal-public-key", type=Path)
    arguments = parser.parse_args(argv)
    try:
        candidate, notice = generate(
            arguments.run_dir,
            arguments.output_dir,
            universal_public_key=arguments.universal_public_key,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "candidate_closure": candidate.name,
                "notice_manifest": notice.name,
                "ok": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
