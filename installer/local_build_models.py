"""Explicit data passed between the local install-set build phases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .vendor_bundle import VendorBundle


@dataclass(frozen=True, repr=False)
class ValidatedBuildInputs:
    root: Path
    build_root: Path
    head: str
    vendor_bundle_dir: Path
    vendor_bundle: VendorBundle
    signing_key: Path | None
    audio_link: Path
    artifact_scope: str
    data_mode: str
    build_count: int


@dataclass(frozen=True)
class BuildEnvironment:
    builder_image: str
    lock_sha256: str
    prepared_source: Path
    vendor_site: Path
    thingino_toolchain_identity: dict[str, object]
    download_cache: Path
    download_identity: dict[str, object]
    rust_source: Path
    rust_toolchain: Path
    ingenic_toolchain_archive: Path
    thingino_toolchain_archive: Path


@dataclass(frozen=True)
class CleanBuildResult:
    result: Path
    workspace: Path
    reproducibility: dict[str, object]
    verification_result: Path | None = None
    verification_workspace: Path | None = None


@dataclass(frozen=True, repr=False)
class PreparedFinalRoot:
    directory: Path
    system: bytes
    mksquashfs: Path
    unsquashfs: Path
    component_artifact: Path


@dataclass(frozen=True)
class PackagedInstallSet:
    directory: Path
    inspection: dict[str, object]
    split_directory: Path
