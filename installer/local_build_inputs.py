"""Validate the complete build request before creating a run directory."""

from __future__ import annotations

from pathlib import Path

from .local_build import LocalBuildError, local_build_workspace_status
from .local_build_models import ValidatedBuildInputs
from .local_build_support import LocalBuildRunError, _directory, _project_root, _regular
from .media_closure import MediaClosureError, load_media_closure
from .vendor_bundle import VendorBundleError, load_vendor_bundle

MAX_RAPTOR_ARTIFACT_BYTES = 128 * 1024 * 1024


def validate_build_inputs(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path | None,
    private_config_dir: Path | None,
    expected_wpa_config_path: Path | None,
    session_dir: Path | None,
    raptor_rwd_artifact: Path | None,
    data_mode: str,
    artifact_scope: str,
    signing_key: Path | None,
    build_count: int,
) -> ValidatedBuildInputs:
    if artifact_scope not in {"device-personalized", "model-universal"}:
        raise LocalBuildRunError("local build artifact scope is invalid")
    if artifact_scope == "model-universal" and data_mode != "initialize":
        raise LocalBuildRunError("model-universal build requires initialize data mode")
    if data_mode not in {"initialize", "preserve", "factory-reset"}:
        raise LocalBuildRunError("data mode is invalid")
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
    if artifact_scope == "device-personalized":
        if (
            private_config_dir is None
            or expected_wpa_config_path is None
            or session_dir is None
            or media_closure_dir is None
            or raptor_rwd_artifact is None
            or signing_key is not None
        ):
            raise LocalBuildRunError("personalized build inputs are incomplete")
        media_closure_dir = _directory(media_closure_dir, "model media closure")
        private_config_dir = _directory(
            private_config_dir, "private install configuration"
        )
        session_dir = _directory(session_dir, "private recovery session")
        expected_wpa_config_path = _regular(
            expected_wpa_config_path, "independent WPA configuration"
        )
    else:
        if any(
            value is not None
            for value in (
                private_config_dir,
                expected_wpa_config_path,
                session_dir,
            )
        ) or signing_key is None:
            raise LocalBuildRunError(
                "model-universal build accepts no camera inputs and requires signing"
            )
        signing_key = _regular(signing_key, "model-universal signing key", limit=64 * 1024)
    try:
        vendor_bundle = load_vendor_bundle(vendor_bundle_dir)
        media_closure = (
            load_media_closure(_directory(media_closure_dir, "model media closure"))
            if media_closure_dir is not None
            else None
        )
    except (VendorBundleError, MediaClosureError) as exc:
        raise LocalBuildRunError(str(exc)) from exc
    if raptor_rwd_artifact is not None:
        raptor_rwd_artifact = _regular(
            raptor_rwd_artifact,
            "Raptor RWD artifact",
            limit=MAX_RAPTOR_ARTIFACT_BYTES,
        )
    if media_closure is not None:
        audio = media_closure.by_path().get("lib/libaudioProcess.so")
        if audio is None:
            raise LocalBuildRunError("private media closure lacks libaudioProcess.so")
        assert media_closure_dir is not None
        audio_link = _regular(
            media_closure_dir / "files/lib/libaudioProcess.so",
            "private media audio link",
        )
    else:
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
        media_closure_dir=media_closure_dir,
        media_closure=media_closure,
        private_config_dir=private_config_dir,
        expected_wpa_config_path=expected_wpa_config_path,
        session_dir=session_dir,
        raptor_rwd_artifact=raptor_rwd_artifact,
        signing_key=signing_key,
        audio_link=audio_link,
        artifact_scope=artifact_scope,
        data_mode=data_mode,
        build_count=build_count,
    )
