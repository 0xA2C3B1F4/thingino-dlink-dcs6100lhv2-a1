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

from .collector.build import CollectorBuildError, build_collector_root
from .collector.kernel import (
    CollectorKernelError,
    validate_collector_kernel,
    validate_uartless_collector_kernel,
)
from .download_cache import DownloadCacheError, validate_download_cache_archive
from .final_bundle import render_final_kernel_fragment
from .final_root import (
    FinalRootError,
    prepare_from_private_directory,
    prepare_universal_final_root,
)
from .local_build import LocalBuildError, local_build_workspace_status
from .local_build_acquire import LocalBuildAcquireError, acquire_locked_public_inputs
from .media_closure import MediaClosureError, load_media_closure
from .sd_package import (
    atomic_write,
    generate_bootstrap,
    package_manifest,
    parse_package,
    read_snapshot,
    validate_bootstrap,
)
from .stage1.build import (
    Stage1BuildError,
    build_install_set,
    build_universal_install_set,
    render_installer_kernel_fragment,
    validate_mmc_module,
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
        root / "scripts/run_macos_collector_kernel_build.sh",
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


def _llvm_tools() -> tuple[Path, Path]:
    """Use Homebrew LLVM on supported Apple Silicon hosts, not Apple Clang."""

    homebrew = Path("/opt/homebrew/opt/llvm/bin")
    clang = homebrew / "clang"
    if clang.is_file() and os.access(clang, os.X_OK):
        return clang.resolve(), _tool("ld.lld")
    clang = _tool("clang")
    sibling_lld = clang.parent / "ld.lld"
    if sibling_lld.is_file() and os.access(sibling_lld, os.X_OK):
        return clang, sibling_lld.resolve(strict=True)
    return clang, _tool("ld.lld")


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


def build_local_recovery_assets(*, build_root: Path) -> dict[str, object]:
    """Build the public read-only collector inputs before private configuration."""

    try:
        workspace = local_build_workspace_status(build_root=build_root)
    except LocalBuildError as exc:
        raise LocalBuildRunError(str(exc)) from exc
    if workspace.get("ready_to_build") is not True:
        raise LocalBuildRunError(
            f"local build workspace is not ready: {workspace.get('safe_next_action')}"
        )

    root = _project_root().resolve(strict=True)
    build_root = Path(str(workspace["build_root"]))
    head = str(workspace["current_head"])
    run_dir = build_root / "runs" / _run_id(head)
    if run_dir.exists() or run_dir.is_symlink():
        raise LocalBuildRunError("local recovery build directory already exists")
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

        result = run_dir / "collector-kernel"
        result.mkdir(mode=0o700)
        collector_workspace = run_dir / "collector-kernel.ext4"
        _run(
            [
                str(root / "scripts/run_macos_collector_kernel_build.sh"),
                "--task-scratch-root",
                str(run_dir),
                "--builder-lock",
                str(run_dir),
                "--builder-image",
                builder_image,
                "--container-name",
                f"dcs6100-collector-{run_dir.name[-25:]}",
                "--prepared-source",
                str(prepared_source),
                "--download-cache",
                str(download_cache),
                "--workspace-image",
                str(collector_workspace),
                "--result",
                str(result),
            ],
            label="read-only collector kernel build",
            log_path=run_dir / "logs/collector-kernel.log",
        )
        verification_result = run_dir / "collector-kernel-verification"
        verification_result.mkdir(mode=0o700)
        verification_workspace = run_dir / "collector-kernel-verification.ext4"
        _run(
            [
                str(root / "scripts/run_macos_collector_kernel_build.sh"),
                "--task-scratch-root",
                str(run_dir),
                "--builder-lock",
                str(run_dir),
                "--builder-image",
                builder_image,
                "--container-name",
                f"dcs6100-collector-verify-{run_dir.name[-18:]}",
                "--prepared-source",
                str(prepared_source),
                "--download-cache",
                str(download_cache),
                "--workspace-image",
                str(verification_workspace),
                "--result",
                str(verification_result),
            ],
            label="read-only collector reproducibility build",
            log_path=run_dir / "logs/collector-kernel-verification.log",
        )
        expected_files = {
            "collector-kernel.uimage",
            "collector-linux.config",
            "jzmmc_v12.ko",
            "source-preparation.json",
            "uartless-collector-kernel.uimage",
            "uartless-collector-linux.config",
        }
        if (
            result.is_symlink()
            or verification_result.is_symlink()
            or {path.name for path in result.iterdir()} != expected_files
            or {path.name for path in verification_result.iterdir()} != expected_files
        ):
            raise LocalBuildRunError("collector recovery asset allowlist changed")
        for name in sorted(expected_files):
            if (result / name).read_bytes() != (verification_result / name).read_bytes():
                raise LocalBuildRunError(
                    f"collector recovery reproducibility differs: {name}"
                )
        kernel = _regular(result / "collector-kernel.uimage", "collector kernel")
        linux_config = _regular(
            result / "collector-linux.config", "collector Linux configuration"
        )
        mmc_module = _regular(result / "jzmmc_v12.ko", "collector MMC module")
        source_manifest = _regular(
            result / "source-preparation.json", "collector source preparation"
        )
        uartless_kernel = _regular(
            result / "uartless-collector-kernel.uimage",
            "UARTless collector kernel",
        )
        uartless_linux_config = _regular(
            result / "uartless-collector-linux.config",
            "UARTless collector Linux configuration",
        )
        try:
            validate_collector_kernel(
                kernel=kernel.read_bytes(), linux_config=linux_config.read_bytes()
            )
            validate_uartless_collector_kernel(
                kernel=uartless_kernel.read_bytes(),
                linux_config=uartless_linux_config.read_bytes(),
            )
            validate_mmc_module(mmc_module.read_bytes())
        except (CollectorKernelError, Stage1BuildError, ValueError) as exc:
            raise LocalBuildRunError(str(exc)) from exc
        if source_manifest.read_bytes() != (
            prepared_source / "dcs6100-source-preparation.json"
        ).read_bytes():
            raise LocalBuildRunError("collector source preparation binding changed")

        uartless = run_dir / "uartless-capture"
        uartless.mkdir(mode=0o700)
        uartless_rootfs = uartless / "uartless-collector.squashfs"
        uartless_rootfs_verification = (
            uartless / "uartless-collector-verification.squashfs"
        )
        try:
            for output in (uartless_rootfs, uartless_rootfs_verification):
                build_collector_root(
                    mmc_module=mmc_module.read_bytes(),
                    output=output,
                    capture_mode="functional-uartless",
                )
            if uartless_rootfs.read_bytes() != uartless_rootfs_verification.read_bytes():
                raise LocalBuildRunError(
                    "UARTless collector root reproducibility differs"
                )
            package_raw = generate_bootstrap(
                uartless_kernel.read_bytes(), uartless_rootfs.read_bytes()
            )
            verification_package_raw = generate_bootstrap(
                (verification_result / "uartless-collector-kernel.uimage").read_bytes(),
                uartless_rootfs_verification.read_bytes(),
            )
            if package_raw != verification_package_raw:
                raise LocalBuildRunError(
                    "UARTless capture package reproducibility differs"
                )
            package = parse_package(package_raw, require_project_header=True)
            validate_bootstrap(package)
        except (CollectorBuildError, ValueError) as exc:
            raise LocalBuildRunError(str(exc)) from exc
        uartless_rootfs_verification.unlink()
        uartless_package = uartless / "uartless-capture-bootstrap.bin"
        atomic_write(uartless_package, package_raw, mode=0o400)
        package_document = json.loads(
            package_manifest(package, purpose="uartless-functional-capture")
        )
        package_document.update(
            {
                "future_physical_boot_writes_mtd": [1, 2],
                "original_complete_backup": False,
                "original_preserved_mtd": [0, 3, 4, 5],
                "restoration_class": "recovery-functional",
            }
        )
        uartless_manifest = uartless / "uartless-capture-bootstrap.manifest.json"
        atomic_write(
            uartless_manifest,
            (json.dumps(package_document, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o400,
        )
        files = {
            "kernel": str(kernel),
            "linux_config": str(linux_config),
            "mmc_module": str(mmc_module),
            "source_preparation": str(source_manifest),
            "uartless_kernel": str(uartless_kernel),
            "uartless_linux_config": str(uartless_linux_config),
            "uartless_rootfs": str(uartless_rootfs),
            "uartless_package": str(uartless_package),
            "uartless_package_manifest": str(uartless_manifest),
        }
        identities = {
            key: {"sha256": _sha256(Path(path)), "size": Path(path).stat().st_size}
            for key, path in files.items()
        }
        manifest = {
            "builder_image_id": builder_image,
            "download_cache": download_identity,
            "files": identities,
            "project_head": head,
            "reproducibility": {
                "builds": 2,
                "byte_identical": True,
            },
            "schema_version": 1,
            "sources_lock_sha256": lock_sha256,
            "thingino_toolchain": toolchain_identity,
        }
        manifest_path = run_dir / "local-recovery-assets.json"
        atomic_write(
            manifest_path,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o600,
        )
        return {
            "collector_assets_dir": str(result),
            "files": files,
            "manifest": str(manifest_path),
            "next_action": "choose-uart-read-only-or-uartless-functional-capture",
            "run_dir": str(run_dir),
            "schema_version": 1,
            "transports": {
                "uart": {
                    "original_complete_backup": True,
                    "write_set": [],
                },
                "uartless": {
                    "future_physical_boot_writes_mtd": [1, 2],
                    "original_complete_backup": False,
                    "original_preserved_mtd": [0, 3, 4, 5],
                    "restoration_class": "recovery-functional",
                },
            },
            "write_set": [],
        }
    except Exception:
        atomic_write(
            run_dir / "FAILED",
            b"local recovery asset build failed; inspect mode-restricted logs\n",
            mode=0o600,
        )
        raise


def _build_local_install_set(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    private_config_dir: Path | None,
    expected_wpa_config_path: Path | None,
    session_dir: Path | None,
    raptor_rwd_artifact: Path,
    data_mode: str,
    artifact_scope: str,
    signing_key: Path | None,
) -> dict[str, object]:
    """Build one explicitly personalized or model-universal install set."""

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
    if workspace.get("build_count") != 2:
        raise LocalBuildRunError("local-build build requires a two-build workspace")

    root = _project_root().resolve(strict=True)
    build_root = Path(str(workspace["build_root"]))
    vendor_bundle_dir = _directory(vendor_bundle_dir, "model vendor bundle")
    media_closure_dir = _directory(media_closure_dir, "model media closure")
    if artifact_scope == "device-personalized":
        if (
            private_config_dir is None
            or expected_wpa_config_path is None
            or session_dir is None
            or signing_key is not None
        ):
            raise LocalBuildRunError("personalized build inputs are incomplete")
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
        if artifact_scope == "model-universal":
            prepared_root = run_dir / "universal-final-root"
            prepare_universal_final_root(
                base_rootfs=(build_a / "thingino-base.squashfs").read_bytes(),
                vendor_bundle=load_vendor_bundle(vendor_bundle_dir),
                media_closure=media_closure,
                output_dir=prepared_root,
                mksquashfs=mksquashfs,
                unsquashfs=unsquashfs,
            )
            prepared_system_name = "system.universal.squashfs"
            prepared_manifest_name = "final-root.universal.json"
        else:
            assert private_config_dir is not None
            assert expected_wpa_config_path is not None
            assert session_dir is not None
            prepared_root = run_dir / "private-final-root"
            prepare_from_private_directory(
                base_rootfs_path=build_a / "thingino-base.squashfs",
                private_config_dir=private_config_dir,
                expected_wpa_config_path=expected_wpa_config_path,
                vendor_bundle_dir=vendor_bundle_dir,
                media_closure_dir=media_closure_dir,
                session_dir=session_dir,
                output_dir=prepared_root,
                mksquashfs=mksquashfs,
                unsquashfs=unsquashfs,
            )
            prepared_system_name = "system.private.squashfs"
            prepared_manifest_name = "final-root.private.json"

        raptor = _raptor_module(root)
        raptor_root = run_dir / "raptor-final-root"
        raptor_result = raptor.build_persistent_root(
            base_rootfs_path=prepared_root / prepared_system_name,
            base_provenance_path=prepared_root / prepared_manifest_name,
            artifact_path=raptor_rwd_artifact,
            artifact_sha256=_sha256(raptor_rwd_artifact),
            supervisor_path=root / "components/raptor-rwd/S13prudynt-rwd",
            service_path=root / "components/raptor-rwd/S96rwd",
            output_dir=raptor_root,
            mksquashfs=mksquashfs,
            unsquashfs=unsquashfs,
            static_rwd_tls=True,
            split_mtd3=True,
            artifact_scope=artifact_scope,
        )
        if not isinstance(raptor_result, dict) or "raptor_rwd" not in raptor_result:
            raise LocalBuildRunError("Raptor overlay lacks raptor_rwd provenance")
        system_rootfs = raptor_root / (
            "system.universal.squashfs"
            if artifact_scope == "model-universal"
            else "system.private.squashfs"
        )
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
        stage1_clang, stage1_lld = _llvm_tools()
        install_arguments = {
            "installer_kernel": read_snapshot(
                split_result / "installer-kernel.uimage"
            ),
            "final_kernel": read_snapshot(split_result / "final-kernel.uimage"),
            "final_linux_config": read_snapshot(
                split_result / "final-linux.config"
            ),
            "system": system,
            "mmc_module": read_snapshot(split_result / "installer-jzmmc_v12.ko"),
            "output_dir": install_set,
            "clang": stage1_clang,
            "lld": stage1_lld,
            "mksquashfs": mksquashfs,
            "unsquashfs": unsquashfs,
        }
        if artifact_scope == "model-universal":
            assert signing_key is not None
            try:
                universal_root_manifest = json.loads(
                    (raptor_root / "final-root.universal.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LocalBuildRunError(
                    "universal Raptor provenance is invalid"
                ) from exc
            build_universal_install_set(
                **install_arguments,
                universal_root_manifest=universal_root_manifest,
                signing_key=signing_key,
            )
        else:
            build_install_set(**install_arguments, data_mode=data_mode)
        inspection = _inspect_install_set(
            root,
            install_set,
            run_dir / "logs/inspect-install-set.json",
        )
        run_manifest = {
            "artifact_scope": artifact_scope,
            "build_count": 2,
            "data_mode": data_mode,
            "download_cache": download_identity,
            "install_set": "install-set",
            "inspection": inspection,
            "private": artifact_scope == "device-personalized",
            "project_head": head,
            "provisioning": (
                "separate-per-camera-sidecar"
                if artifact_scope == "model-universal"
                else "embedded-device-personalization"
            ),
            "public_firmware_release_gate_consulted": False,
            "raptor_rwd_overlay": True,
            "reproducibility": reproducibility,
            "schema_version": RUN_SCHEMA_VERSION,
            "sources_lock_sha256": lock_sha256,
            "status": "host-built and inspected; live installation not authorized",
            "thingino_toolchain": toolchain_identity,
        }
        atomic_write(
            run_dir
            / (
                "local-build-run.universal.json"
                if artifact_scope == "model-universal"
                else "local-build-run.private.json"
            ),
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
        "artifact_scope": artifact_scope,
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
        "universal_firmware": (
            str(install_set / "thingino-universal.tgb")
            if artifact_scope == "model-universal"
            else None
        ),
    }


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
    """Legacy personalized build; its bytes must not be shared as universal."""

    return _build_local_install_set(
        build_root=build_root,
        vendor_bundle_dir=vendor_bundle_dir,
        media_closure_dir=media_closure_dir,
        private_config_dir=private_config_dir,
        expected_wpa_config_path=expected_wpa_config_path,
        session_dir=session_dir,
        raptor_rwd_artifact=raptor_rwd_artifact,
        data_mode=data_mode,
        artifact_scope="device-personalized",
        signing_key=None,
    )


def build_local_universal_install_set(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    raptor_rwd_artifact: Path,
    signing_key: Path,
) -> dict[str, object]:
    """Build once for A1 cameras; authorization and provisioning stay separate."""

    return _build_local_install_set(
        build_root=build_root,
        vendor_bundle_dir=vendor_bundle_dir,
        media_closure_dir=media_closure_dir,
        private_config_dir=None,
        expected_wpa_config_path=None,
        session_dir=None,
        raptor_rwd_artifact=raptor_rwd_artifact,
        data_mode="initialize",
        artifact_scope="model-universal",
        signing_key=signing_key,
    )
