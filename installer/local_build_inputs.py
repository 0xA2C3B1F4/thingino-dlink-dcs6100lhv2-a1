"""Validate the complete build request before creating a run directory."""

from __future__ import annotations

from pathlib import Path

from .local_build import LocalBuildError, local_build_workspace_status
from .local_build_models import ValidatedBuildInputs
from .local_build_support import LocalBuildRunError, _directory, _project_root, _regular
from .vendor_bundle import VendorBundleError, load_vendor_bundle


def validate_build_inputs(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    data_mode: str,
    artifact_scope: str,
    signing_key: Path | None,
    build_count: int,
) -> ValidatedBuildInputs:
    if artifact_scope != "model-universal":
        raise LocalBuildRunError(
            "only model-universal full Raptor builds are supported"
        )
    if data_mode not in {
        "initialize",
        "preserve",
    }:
        raise LocalBuildRunError(
            "model-universal build requires initialize or preserve data mode"
        )
    try:
        workspace = local_build_workspace_status(build_root=build_root)
    except LocalBuildError as exc:
        raise LocalBuildRunError(str(exc)) from exc
    if workspace.get("ready_to_build") is not True:
        raise LocalBuildRunError(
            f"local build workspace is not ready: {workspace.get('safe_next_action')}"
        )
    if build_count not in {1, 2}:
        raise LocalBuildRunError("local-build build count must be one or two")
    workspace_build_count = workspace.get("build_count")
    if not isinstance(workspace_build_count, int) or workspace_build_count < build_count:
        raise LocalBuildRunError(
            "local-build workspace does not reserve capacity for the requested build count"
        )

    root = _project_root().resolve(strict=True)
    build_root = Path(str(workspace["build_root"]))
    vendor_bundle_dir = _directory(vendor_bundle_dir, "model vendor bundle")
    if signing_key is None:
        raise LocalBuildRunError("model-universal build requires signing")
    signing_key = _regular(signing_key, "model-universal signing key", limit=64 * 1024)
    try:
        vendor_bundle = load_vendor_bundle(vendor_bundle_dir)
    except VendorBundleError as exc:
        raise LocalBuildRunError(str(exc)) from exc
    audio_link = _regular(
        vendor_bundle_dir / "files/libaudioProcess.so",
        "stock vendor audio link",
    )

    return ValidatedBuildInputs(
        root=root,
        build_root=build_root,
        head=str(workspace["current_head"]),
        vendor_bundle_dir=vendor_bundle_dir,
        vendor_bundle=vendor_bundle,
        signing_key=signing_key,
        audio_link=audio_link,
        artifact_scope=artifact_scope,
        data_mode=data_mode,
        build_count=build_count,
    )
