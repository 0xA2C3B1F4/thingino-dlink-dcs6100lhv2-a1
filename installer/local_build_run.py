"""Run the complete private, device-local schema-2 firmware build."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts import source_prepare

from .download_cache import DownloadCacheError, validate_download_cache_archive
from .final_bundle import render_final_kernel_fragment
from .final_root import FinalRootError, prepare_from_private_directory
from .local_build import LocalBuildError, local_build_workspace_status
from .local_build_acquire import LocalBuildAcquireError, acquire_locked_public_inputs
from .media_closure import MediaClosureError, load_media_closure
from .sd_package import atomic_write, read_snapshot
from .stage1.build import (
    Stage1BuildError,
    build_install_set,
    render_installer_kernel_fragment,
)
from .vendor_bundle import (
    VendorBundleError,
    load_vendor_bundle,
    prepare_vendor_build_site,
)


RUN_SCHEMA_VERSION = 1
BUILD_RESULT_FILES = {
    "source-preparation.json",
    "thingino-base.manifest.json",
    "thingino-base.squashfs",
    "thingino-linux.config",
}
MAX_RAPTOR_ARTIFACT_BYTES = 128 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 8 * 60 * 60
TOOLCHAIN_ARCHIVE_MAX_BYTES = 2 * 1024 * 1024 * 1024
TOOLCHAIN_DOWNLOAD_POLICY = (
    Path(__file__).resolve().parents[1]
    / "profiles/dlink-dcs6100lhv2-a1/toolchain-download-cache.json"
)


class LocalBuildRunError(ValueError):
    """The complete guided local build could not be accepted."""


def _project_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    required = (
        root / "scripts/run_macos_thingino_toolchain_download_fetch.sh",
        root / "scripts/run_macos_thingino_toolchain_build.sh",
        root / "scripts/run_macos_thingino_download_fetch.sh",
        root / "scripts/run_macos_thingino_build.sh",
        root / "scripts/run_macos_split_kernel_build.sh",
        root / "components/raptor-rwd/build_persistent.py",
    )
    if not all(path.is_file() and not path.is_symlink() for path in required):
        raise LocalBuildRunError("project local-build implementation is incomplete")
    return root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise LocalBuildRunError(f"{label} is symlinked")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LocalBuildRunError(f"{label} is missing") from exc
    if not resolved.is_dir():
        raise LocalBuildRunError(f"{label} is not a directory")
    return resolved


def _regular(path: Path, label: str, *, limit: int | None = None) -> Path:
    if path.is_symlink():
        raise LocalBuildRunError(f"{label} is symlinked")
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LocalBuildRunError(f"{label} is missing") from exc
    if not resolved.is_file():
        raise LocalBuildRunError(f"{label} is not a regular file")
    if limit is not None and (resolved.stat().st_size < 1 or resolved.stat().st_size > limit):
        raise LocalBuildRunError(f"{label} violates its size limit")
    return resolved


def _tool(name: str) -> Path:
    found = shutil.which(name)
    if found is None:
        raise LocalBuildRunError(f"required host tool is missing: {name}")
    path = Path(found).resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise LocalBuildRunError(f"required host tool is not executable: {name}")
    return path


def _run(
    arguments: list[str], *, label: str, log_path: Path, timeout: int = COMMAND_TIMEOUT_SECONDS
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        try:
            completed = subprocess.run(
                arguments,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalBuildRunError(f"{label} could not complete; see {log_path}") from exc
    if completed.returncode != 0:
        raise LocalBuildRunError(f"{label} failed; see {log_path}")


def _run_id(head: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"build-{timestamp}-{head[:12]}"


def _write_owner(run_dir: Path, *, head: str) -> None:
    run_dir.mkdir(mode=0o700)
    owner = (
        "# Guided local firmware build\n\n"
        "This directory is owned by `thingino-dlink local-build build`.\n"
        f"Project HEAD: `{head}`\n"
        "It may contain private, device-specific build output. Do not publish it.\n"
    )
    atomic_write(run_dir / "OWNER.md", owner.encode(), mode=0o600)


def _prepare_download_cache(
    *,
    root: Path,
    run_dir: Path,
    build_root: Path,
    prepared_source: Path,
    builder_image: str,
    lock_sha256: str,
    thingino_toolchain: Path,
) -> tuple[Path, dict[str, object]]:
    destination = build_root / "cache/downloads" / f"thingino-downloads-{lock_sha256}.tar"
    if destination.exists() or destination.is_symlink():
        try:
            return destination, validate_download_cache_archive(destination)
        except DownloadCacheError as exc:
            raise LocalBuildRunError(str(exc)) from exc
    result = run_dir / "download-fetch-result"
    result.mkdir(mode=0o700)
    workspace = run_dir / "download-fetch.ext4"
    container_name = f"dcs6100-download-{run_dir.name[-25:]}"
    _run(
        [
            str(root / "scripts/run_macos_thingino_download_fetch.sh"),
            "--task-scratch-root",
            str(run_dir),
            "--builder-lock",
            str(run_dir),
            "--builder-image",
            builder_image,
            "--container-name",
            container_name,
            "--prepared-source",
            str(prepared_source),
            "--thingino-toolchain",
            str(thingino_toolchain),
            "--workspace-image",
            str(workspace),
            "--result",
            str(result),
        ],
        label="locked Buildroot download fetch",
        log_path=run_dir / "logs/download-fetch.log",
    )
    candidate = result / "download-cache.tar"
    try:
        identity = validate_download_cache_archive(candidate)
    except DownloadCacheError as exc:
        raise LocalBuildRunError(str(exc)) from exc
    if destination.exists() or destination.is_symlink():
        raise LocalBuildRunError("download cache destination changed during fetch")
    os.replace(candidate, destination)
    destination.chmod(0o400)
    return destination, identity


def _toolchain_archive_identity(
    path: Path, *, expected_sha256: str
) -> dict[str, object]:
    archive = _regular(
        path,
        "source-built Thingino toolchain",
        limit=TOOLCHAIN_ARCHIVE_MAX_BYTES,
    )
    actual_sha256 = _sha256(archive)
    if actual_sha256 != expected_sha256:
        raise LocalBuildRunError("source-built Thingino toolchain digest mismatch")
    return {
        "archive_sha256": actual_sha256,
        "archive_size": archive.stat().st_size,
    }


def _prepare_toolchain_download_cache(
    *,
    root: Path,
    run_dir: Path,
    build_root: Path,
    source_checkout: Path,
    builder_image: str,
    lock_sha256: str,
) -> tuple[Path, dict[str, object]]:
    destination = (
        build_root
        / "cache/downloads"
        / f"thingino-toolchain-downloads-{lock_sha256}.tar"
    )
    if destination.exists() or destination.is_symlink():
        try:
            return destination, validate_download_cache_archive(
                destination,
                policy_path=TOOLCHAIN_DOWNLOAD_POLICY,
                forbid_git_metadata=True,
            )
        except DownloadCacheError as exc:
            raise LocalBuildRunError(str(exc)) from exc
    result = run_dir / "toolchain-download-fetch-result"
    result.mkdir(mode=0o700)
    workspace = run_dir / "toolchain-download-fetch.ext4"
    _run(
        [
            str(root / "scripts/run_macos_thingino_toolchain_download_fetch.sh"),
            "--task-scratch-root",
            str(run_dir),
            "--builder-lock",
            str(run_dir),
            "--builder-image",
            builder_image,
            "--container-name",
            f"dcs6100-toolchain-download-{run_dir.name[-25:]}",
            "--source-checkout",
            str(source_checkout),
            "--workspace-image",
            str(workspace),
            "--result",
            str(result),
        ],
        label="locked Thingino toolchain source fetch",
        log_path=run_dir / "logs/toolchain-download-fetch.log",
    )
    candidate = result / "toolchain-download-cache.tar"
    try:
        identity = validate_download_cache_archive(
            candidate,
            policy_path=TOOLCHAIN_DOWNLOAD_POLICY,
            forbid_git_metadata=True,
        )
    except DownloadCacheError as exc:
        raise LocalBuildRunError(str(exc)) from exc
    if destination.exists() or destination.is_symlink():
        raise LocalBuildRunError("toolchain download cache changed during fetch")
    os.replace(candidate, destination)
    destination.chmod(0o400)
    return destination, identity


def _prepare_thingino_toolchain(
    *,
    root: Path,
    run_dir: Path,
    build_root: Path,
    source_checkout: Path,
    builder_image: str,
    lock_sha256: str,
    metadata: dict[str, object],
) -> tuple[Path, dict[str, object]]:
    archive_name = metadata.get("archive")
    expected_sha256 = metadata.get("sha256")
    if (
        metadata.get("acquisition") != "source-build"
        or archive_name
        != "thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz"
        or metadata.get("archive_format")
        != "gnu-tar-sort-name-source-date-epoch-gzip-n"
        or metadata.get("build_target")
        != "buildroot-toolchain-relocatable-sdk"
        or metadata.get("builder_platform") != "linux/arm64"
        or metadata.get("host") != "aarch64-linux"
        or metadata.get("kernel_headers") != "custom-tarball-3.10"
        or metadata.get("kernel_tarball_sha256")
        != "36540d5fb15951be64d4c150cf3bc291a8d4d6699fb988173f6be30aa1e41b47"
        or metadata.get("kernel_tarball_url")
        != "https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.10.14.tar.xz"
        or metadata.get("linux_kernel") is not False
        or metadata.get("recipe")
        != "configs/github/toolchain_xburst1_glibc_gcc16_defconfig"
        or metadata.get("source") != "thingino_firmware"
        or metadata.get("version") != "gcc16-glibc-xburst1"
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise LocalBuildRunError("Thingino toolchain lock metadata is invalid")
    destination = build_root / "cache/downloads" / f"{lock_sha256}-{archive_name}"
    if destination.exists() or destination.is_symlink():
        return destination, _toolchain_archive_identity(
            destination,
            expected_sha256=expected_sha256,
        )
    download_cache, download_identity = _prepare_toolchain_download_cache(
        root=root,
        run_dir=run_dir,
        build_root=build_root,
        source_checkout=source_checkout,
        builder_image=builder_image,
        lock_sha256=lock_sha256,
    )
    build_dir = run_dir / "toolchain-build"
    result = build_dir / "result"
    build_dir.mkdir(mode=0o700)
    result.mkdir(mode=0o700)
    workspace = build_dir / "workspace.ext4"
    _run(
        [
            str(root / "scripts/run_macos_thingino_toolchain_build.sh"),
            "--task-scratch-root",
            str(run_dir),
            "--builder-lock",
            str(run_dir),
            "--builder-image",
            builder_image,
            "--container-name",
            f"dcs6100-toolchain-build-{run_dir.name[-25:]}",
            "--source-checkout",
            str(source_checkout),
            "--download-cache",
            str(download_cache),
            "--workspace-image",
            str(workspace),
            "--result",
            str(result),
        ],
        label="offline Thingino toolchain build",
        log_path=run_dir / "logs/toolchain-build.log",
    )
    candidate = result / archive_name
    identity = _toolchain_archive_identity(
        candidate,
        expected_sha256=expected_sha256,
    )
    if destination.exists() or destination.is_symlink():
        raise LocalBuildRunError("Thingino toolchain cache changed during build")
    os.replace(candidate, destination)
    destination.chmod(0o400)
    return destination, {**identity, "download_cache": download_identity}


def _run_clean_build(
    *,
    root: Path,
    run_dir: Path,
    label: str,
    builder_image: str,
    prepared_source: Path,
    download_cache: Path,
    vendor_site: Path,
    audio_link: Path,
    rust_source: Path,
    rust_toolchain: Path,
    ingenic_toolchain_archive: Path,
) -> tuple[Path, Path]:
    build_dir = run_dir / label
    result = build_dir / "result"
    build_dir.mkdir(mode=0o700)
    result.mkdir(mode=0o700)
    workspace = build_dir / "workspace.ext4"
    _run(
        [
            str(root / "scripts/run_macos_thingino_build.sh"),
            "--task-scratch-root",
            str(run_dir),
            "--builder-lock",
            str(run_dir),
            "--builder-image",
            builder_image,
            "--container-name",
            f"dcs6100-{label}-{run_dir.name[-25:]}",
            "--prepared-source",
            str(prepared_source),
            "--download-cache",
            str(download_cache),
            "--vendor-site",
            str(vendor_site),
            "--audio-link",
            str(audio_link),
            "--rust-source",
            str(rust_source),
            "--rust-toolchain",
            str(rust_toolchain),
            "--ingenic-toolchain-archive",
            str(ingenic_toolchain_archive),
            "--workspace-image",
            str(workspace),
            "--result",
            str(result),
        ],
        label=f"clean Thingino {label}",
        log_path=run_dir / f"logs/{label}.log",
    )
    if result.is_symlink() or {path.name for path in result.iterdir()} != BUILD_RESULT_FILES:
        raise LocalBuildRunError(f"{label} result allowlist changed")
    for name in BUILD_RESULT_FILES:
        _regular(result / name, f"{label} {name}")
    return result, workspace


def _compare_clean_builds(first: Path, second: Path) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for name in sorted(BUILD_RESULT_FILES):
        first_path = first / name
        second_path = second / name
        if first_path.stat().st_size != second_path.stat().st_size:
            raise LocalBuildRunError(f"clean builds differ in {name} size")
        first_sha256 = _sha256(first_path)
        second_sha256 = _sha256(second_path)
        if first_sha256 != second_sha256:
            raise LocalBuildRunError(f"clean builds are not byte-identical: {name}")
        files[name] = {
            "sha256": first_sha256,
            "size": first_path.stat().st_size,
        }
    return {"byte_identical": True, "files": files}


def _workspace_tool(
    *, name: str, run_dir: Path, workspace: Path, builder_image: str
) -> Path:
    tools = run_dir / "workspace-tools"
    tools.mkdir(mode=0o700, exist_ok=True)
    output = tools / name
    if output.exists() or output.is_symlink():
        raise LocalBuildRunError("workspace tool wrapper already exists")
    quoted_run = shlex.quote(str(run_dir))
    quoted_workspace = shlex.quote(str(workspace))
    quoted_image = shlex.quote(builder_image)
    quoted_name = shlex.quote(name)
    content = f"""#!/bin/sh
set -eu
exec docker run --rm --network none --privileged --platform linux/arm64 \\
  -v {quoted_run}:{quoted_run} \\
  -v {quoted_workspace}:/input/workspace.ext4:ro \\
  --entrypoint /bin/sh {quoted_image} -c '
    set -eu
    mkdir -p /workspace
    mount -o ro,loop /input/workspace.ext4 /workspace
    status=0
    /workspace/thingino-output/host/bin/{quoted_name} "$@" || status=$?
    umount /workspace
    exit "$status"
  ' workspace-{quoted_name} "$@"
"""
    atomic_write(output, content.encode(), mode=0o700)
    return output


def _raptor_module(root: Path):
    path = root / "components/raptor-rwd/build_persistent.py"
    spec = importlib.util.spec_from_file_location("dcs6100_raptor_persistent", path)
    if spec is None or spec.loader is None:
        raise LocalBuildRunError("Raptor persistent builder cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inspect_install_set(root: Path, install_set: Path, log_path: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "installer",
                "inspect-install-set",
                "--install-set-dir",
                str(install_set),
            ],
            cwd=root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalBuildRunError("inspect-install-set could not run") from exc
    atomic_write(
        log_path,
        (completed.stdout + completed.stderr).encode("utf-8", "replace"),
        mode=0o600,
    )
    if completed.returncode != 0:
        raise LocalBuildRunError(f"inspect-install-set failed; see {log_path}")
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LocalBuildRunError("inspect-install-set returned invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or document.get("ok") is not True
        or document.get("schema_version") != 2
        or document.get("layout") != "dcs6100lhv2-a1-mtd3-split-v1"
    ):
        raise LocalBuildRunError("inspect-install-set did not accept schema 2")
    return document


def build_local_install_set(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    private_config_dir: Path,
    expected_wpa_config_path: Path,
    session_dir: Path,
    raptor_rwd_artifact: Path,
    data_mode: str = "initialize",
) -> dict[str, object]:
    """Build and inspect one private install set without consulting release gates."""

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
    if workspace.get("build_count") != 2:
        raise LocalBuildRunError("local-build build requires a two-build workspace")

    root = _project_root().resolve(strict=True)
    build_root = Path(str(workspace["build_root"]))
    vendor_bundle_dir = _directory(vendor_bundle_dir, "private vendor bundle")
    media_closure_dir = _directory(media_closure_dir, "private media closure")
    private_config_dir = _directory(private_config_dir, "private install configuration")
    session_dir = _directory(session_dir, "private recovery session")
    expected_wpa_config_path = _regular(
        expected_wpa_config_path, "independent WPA configuration"
    )
    raptor_rwd_artifact = _regular(
        raptor_rwd_artifact,
        "Raptor RWD artifact",
        limit=MAX_RAPTOR_ARTIFACT_BYTES,
    )
    try:
        load_vendor_bundle(vendor_bundle_dir)
        media_closure = load_media_closure(media_closure_dir)
    except (VendorBundleError, MediaClosureError) as exc:
        raise LocalBuildRunError(str(exc)) from exc
    audio = media_closure.by_path().get("lib/libaudioProcess.so")
    if audio is None:
        raise LocalBuildRunError("private media closure lacks libaudioProcess.so")
    audio_link = _regular(
        media_closure_dir / "files/lib/libaudioProcess.so",
        "private media audio link",
    )

    head = str(workspace["current_head"])
    run_dir = build_root / "runs" / _run_id(head)
    if run_dir.exists() or run_dir.is_symlink():
        raise LocalBuildRunError("local build run directory already exists")
    _write_owner(run_dir, head=head)
    try:
        acquired = acquire_locked_public_inputs(build_root=build_root)
        builder = acquired.get("builder_image")
        if not isinstance(builder, dict) or not isinstance(builder.get("id"), str):
            raise LocalBuildRunError("locked public inputs lack a builder image")
        builder_image = str(builder["id"])
        lock_sha256 = str(acquired["sources_lock_sha256"])
        prepared_source = run_dir / "prepared-source"
        source_prepare.prepare_source(
            Path(str(acquired["source_checkout"]["path"])),
            prepared_source,
            repository_root=root,
        )

        private_inputs = run_dir / "private-inputs"
        private_inputs.mkdir(mode=0o700)
        vendor_site = private_inputs / "vendor-site"
        prepare_vendor_build_site(
            vendor_bundle_dir=vendor_bundle_dir,
            output_dir=vendor_site,
        )
        source_checkout = _directory(
            Path(str(acquired["source_checkout"]["path"])),
            "pinned Thingino source checkout",
        )
        toolchain_metadata = acquired.get("thingino_toolchain")
        if not isinstance(toolchain_metadata, dict):
            raise LocalBuildRunError("locked public inputs lack the Thingino toolchain")
        thingino_toolchain, toolchain_identity = _prepare_thingino_toolchain(
            root=root,
            run_dir=run_dir,
            build_root=build_root,
            source_checkout=source_checkout,
            builder_image=builder_image,
            lock_sha256=lock_sha256,
            metadata=toolchain_metadata,
        )
        download_cache, download_identity = _prepare_download_cache(
            root=root,
            run_dir=run_dir,
            build_root=build_root,
            prepared_source=prepared_source,
            builder_image=builder_image,
            lock_sha256=lock_sha256,
            thingino_toolchain=thingino_toolchain,
        )
        rust_source = _directory(
            Path(str(acquired["rust_source"]["path"])), "locked Rust source"
        )
        rust_toolchain = _directory(
            Path(str(acquired["rust_toolchain"]["path"])), "locked Rust toolchain"
        )
        ingenic_toolchain_archive = _regular(
            Path(str(acquired["ingenic_toolchain"]["archive"])),
            "locked Ingenic toolchain archive",
            limit=TOOLCHAIN_ARCHIVE_MAX_BYTES,
        )
        build_a, workspace_a = _run_clean_build(
            root=root,
            run_dir=run_dir,
            label="build-a",
            builder_image=builder_image,
            prepared_source=prepared_source,
            download_cache=download_cache,
            vendor_site=vendor_site,
            audio_link=audio_link,
            rust_source=rust_source,
            rust_toolchain=rust_toolchain,
            ingenic_toolchain_archive=ingenic_toolchain_archive,
        )
        build_b, _workspace_b = _run_clean_build(
            root=root,
            run_dir=run_dir,
            label="build-b",
            builder_image=builder_image,
            prepared_source=prepared_source,
            download_cache=download_cache,
            vendor_site=vendor_site,
            audio_link=audio_link,
            rust_source=rust_source,
            rust_toolchain=rust_toolchain,
            ingenic_toolchain_archive=ingenic_toolchain_archive,
        )
        reproducibility = _compare_clean_builds(build_a, build_b)
        atomic_write(
            run_dir / "reproducibility.json",
            (json.dumps(reproducibility, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o600,
        )

        mksquashfs = _workspace_tool(
            name="mksquashfs",
            run_dir=run_dir,
            workspace=workspace_a,
            builder_image=builder_image,
        )
        unsquashfs = _workspace_tool(
            name="unsquashfs",
            run_dir=run_dir,
            workspace=workspace_a,
            builder_image=builder_image,
        )
        private_root = run_dir / "private-final-root"
        prepare_from_private_directory(
            base_rootfs_path=build_a / "thingino-base.squashfs",
            private_config_dir=private_config_dir,
            expected_wpa_config_path=expected_wpa_config_path,
            vendor_bundle_dir=vendor_bundle_dir,
            media_closure_dir=media_closure_dir,
            session_dir=session_dir,
            output_dir=private_root,
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
        )

        raptor = _raptor_module(root)
        raptor_root = run_dir / "raptor-final-root"
        raptor_result = raptor.build_persistent_root(
            base_rootfs_path=private_root / "system.private.squashfs",
            base_provenance_path=private_root / "final-root.private.json",
            artifact_path=raptor_rwd_artifact,
            artifact_sha256=_sha256(raptor_rwd_artifact),
            supervisor_path=root / "components/raptor-rwd/S13prudynt-rwd",
            service_path=root / "components/raptor-rwd/S96rwd",
            output_dir=raptor_root,
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
            static_rwd_tls=True,
            split_mtd3=True,
        )
        if not isinstance(raptor_result, dict) or "raptor_rwd" not in raptor_result:
            raise LocalBuildRunError("Raptor overlay lacks raptor_rwd provenance")
        system_rootfs = raptor_root / "system.private.squashfs"
        system = read_snapshot(system_rootfs)
        fragments = run_dir / "kernel-fragments"
        fragments.mkdir(mode=0o700)
        installer_fragment = fragments / "installer.fragment"
        final_fragment = fragments / "final.fragment"
        atomic_write(installer_fragment, render_installer_kernel_fragment(len(system)))
        atomic_write(final_fragment, render_final_kernel_fragment(len(system)))

        split_result = run_dir / "split-kernels"
        split_result.mkdir(mode=0o700)
        _run(
            [
                str(root / "scripts/run_macos_split_kernel_build.sh"),
                "--task-scratch-root",
                str(run_dir),
                "--builder-lock",
                str(run_dir),
                "--builder-image",
                builder_image,
                "--container-name",
                f"dcs6100-split-{run_dir.name[-25:]}",
                "--workspace-image",
                str(workspace_a),
                "--installer-fragment",
                str(installer_fragment),
                "--final-fragment",
                str(final_fragment),
                "--vendor-site",
                str(vendor_site),
                "--audio-link",
                str(audio_link),
                "--rust-source",
                str(rust_source),
                "--rust-toolchain",
                str(rust_toolchain),
                "--ingenic-toolchain-archive",
                str(ingenic_toolchain_archive),
                "--result",
                str(split_result),
            ],
            label="fixed-layout split-kernel build",
            log_path=run_dir / "logs/split-kernels.log",
        )
        install_set = run_dir / "install-set"
        build_install_set(
            installer_kernel=read_snapshot(split_result / "installer-kernel.uimage"),
            final_kernel=read_snapshot(split_result / "final-kernel.uimage"),
            final_linux_config=read_snapshot(split_result / "final-linux.config"),
            system=system,
            mmc_module=read_snapshot(split_result / "installer-jzmmc_v12.ko"),
            output_dir=install_set,
            clang=_tool("clang"),
            lld=_tool("ld.lld"),
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
            data_mode=data_mode,
        )
        inspection = _inspect_install_set(
            root,
            install_set,
            run_dir / "logs/inspect-install-set.json",
        )
        run_manifest = {
            "build_count": 2,
            "data_mode": data_mode,
            "download_cache": download_identity,
            "install_set": "install-set",
            "inspection": inspection,
            "private": True,
            "project_head": head,
            "public_firmware_release_gate_consulted": False,
            "raptor_rwd_overlay": True,
            "reproducibility": reproducibility,
            "schema_version": RUN_SCHEMA_VERSION,
            "sources_lock_sha256": lock_sha256,
            "status": "host-built and inspected; live installation not authorized",
            "thingino_toolchain": toolchain_identity,
        }
        atomic_write(
            run_dir / "local-build-run.private.json",
            (json.dumps(run_manifest, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o600,
        )
    except (
        DownloadCacheError,
        FinalRootError,
        LocalBuildAcquireError,
        MediaClosureError,
        Stage1BuildError,
        VendorBundleError,
        source_prepare.PreparationError,
        OSError,
        ValueError,
    ) as exc:
        if isinstance(exc, LocalBuildRunError):
            raise
        raise LocalBuildRunError(str(exc)) from exc

    return {
        "build_count": 2,
        "build_root": str(build_root),
        "data_mode": data_mode,
        "install_set_dir": str(install_set),
        "inspection": inspection,
        "nor_writes": False,
        "public_firmware_release_gate_consulted": False,
        "raptor_rwd_overlay": True,
        "reproducible": True,
        "run_dir": str(run_dir),
        "schema_version": 2,
        "status": "host-built and inspected; live installation not authorized",
    }
