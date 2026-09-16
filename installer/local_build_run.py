"""Run the complete model-universal full-Raptor schema-2 firmware build."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
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
from .final_root import FinalRootError
from .local_build import LocalBuildError, local_build_workspace_status
from .local_build_acquire import LocalBuildAcquireError, acquire_locked_public_inputs
from .local_build_inputs import validate_build_inputs
from .local_build_models import (
    BuildEnvironment,
    CleanBuildResult,
    PackagedInstallSet,
    PreparedFinalRoot,
    ValidatedBuildInputs,
)
from .local_build_package import (
    package_install_root,
    prepare_install_root,
)
from .local_build_support import (
    LocalBuildRunError,
    _directory,
    _project_root,
    _regular,
    _run,
    _sha256,
)
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
    validate_mmc_module,
)
from .vendor_bundle import (
    VendorBundleError,
    prepare_vendor_build_site,
)


RUN_SCHEMA_VERSION = 1
BUILD_RESULT_FILES = {
    "source-preparation.json",
    "thingino-base.manifest.json",
    "thingino-base.squashfs",
    "thingino-linux.config",
}
TOOLCHAIN_ARCHIVE_MAX_BYTES = 2 * 1024 * 1024 * 1024
TOOLCHAIN_DOWNLOAD_POLICY = (
    Path(__file__).resolve().parents[1]
    / "profiles/dlink-dcs6100lhv2-a1/toolchain-download-cache.json"
)


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
    source_manifest = _regular(
        prepared_source / "dcs6100-source-preparation.json",
        "prepared Thingino source manifest",
    )
    source_manifest_sha256 = _sha256(source_manifest)
    fetch_contract_sha256 = _sha256(
        _regular(
            root / "scripts/container_fetch_thingino_downloads.sh",
            "Thingino download fetch contract",
        )
    )
    download_policy_sha256 = _sha256(
        _regular(
            root / "profiles/dlink-dcs6100lhv2-a1/download-cache.json",
            "Thingino download-cache policy",
        )
    )
    cache_key_sha256 = hashlib.sha256(
        (
            f"{lock_sha256}\n{source_manifest_sha256}\n"
            f"{fetch_contract_sha256}\n{download_policy_sha256}\n"
        ).encode()
    ).hexdigest()
    destination = (
        build_root
        / "cache/downloads"
        / f"thingino-downloads-{cache_key_sha256}.tar"
    )
    if destination.exists() or destination.is_symlink():
        try:
            identity = validate_download_cache_archive(destination)
            return destination, {
                **identity,
                "prepared_source_manifest_sha256": source_manifest_sha256,
                "fetch_contract_sha256": fetch_contract_sha256,
                "download_policy_sha256": download_policy_sha256,
                "cache_key_sha256": cache_key_sha256,
            }
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
    return destination, {
        **identity,
        "prepared_source_manifest_sha256": source_manifest_sha256,
        "fetch_contract_sha256": fetch_contract_sha256,
        "download_policy_sha256": download_policy_sha256,
        "cache_key_sha256": cache_key_sha256,
    }


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
    return {"builds": 2, "byte_identical": True, "files": files}


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


def _acquire_build_environment(
    inputs: ValidatedBuildInputs, run_dir: Path
) -> BuildEnvironment:
    """Acquire locked inputs and prepare the source and private build site."""

    acquired = acquire_locked_public_inputs(build_root=inputs.build_root)
    builder = acquired.get("builder_image")
    if not isinstance(builder, dict) or not isinstance(builder.get("id"), str):
        raise LocalBuildRunError("locked public inputs lack a builder image")
    builder_image = str(builder["id"])
    lock_sha256 = str(acquired["sources_lock_sha256"])
    prepared_source = run_dir / "prepared-source"
    source_prepare.prepare_source(
        Path(str(acquired["source_checkout"]["path"])),
        prepared_source,
        repository_root=inputs.root,
    )

    private_inputs = run_dir / "private-inputs"
    private_inputs.mkdir(mode=0o700)
    vendor_site = private_inputs / "vendor-site"
    prepare_vendor_build_site(
        vendor_bundle_dir=inputs.vendor_bundle_dir,
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
        root=inputs.root,
        run_dir=run_dir,
        build_root=inputs.build_root,
        source_checkout=source_checkout,
        builder_image=builder_image,
        lock_sha256=lock_sha256,
        metadata=toolchain_metadata,
    )
    download_cache, download_identity = _prepare_download_cache(
        root=inputs.root,
        run_dir=run_dir,
        build_root=inputs.build_root,
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
    return BuildEnvironment(
        builder_image=builder_image,
        lock_sha256=lock_sha256,
        prepared_source=prepared_source,
        vendor_site=vendor_site,
        thingino_toolchain_identity=toolchain_identity,
        download_cache=download_cache,
        download_identity=download_identity,
        rust_source=rust_source,
        rust_toolchain=rust_toolchain,
        ingenic_toolchain_archive=ingenic_toolchain_archive,
        thingino_toolchain_archive=thingino_toolchain,
    )


def _build_clean_roots(
    inputs: ValidatedBuildInputs, environment: BuildEnvironment, run_dir: Path,
) -> CleanBuildResult:
    """Run exactly the reserved build count and retain the comparison evidence."""

    build_a, workspace_a = _run_clean_build(
        root=inputs.root,
        run_dir=run_dir,
        label="build-a",
        builder_image=environment.builder_image,
        prepared_source=environment.prepared_source,
        download_cache=environment.download_cache,
        vendor_site=environment.vendor_site,
        audio_link=inputs.audio_link,
        rust_source=environment.rust_source,
        rust_toolchain=environment.rust_toolchain,
        ingenic_toolchain_archive=environment.ingenic_toolchain_archive,
    )
    reproducibility: dict[str, object] = {
        "builds": 1,
        "byte_identical": None,
    }
    if inputs.build_count == 2:
        build_b, workspace_b = _run_clean_build(
            root=inputs.root,
            run_dir=run_dir,
            label="build-b",
            builder_image=environment.builder_image,
            prepared_source=environment.prepared_source,
            download_cache=environment.download_cache,
            vendor_site=environment.vendor_site,
            audio_link=inputs.audio_link,
            rust_source=environment.rust_source,
            rust_toolchain=environment.rust_toolchain,
            ingenic_toolchain_archive=environment.ingenic_toolchain_archive,
        )
        reproducibility = _compare_clean_builds(build_a, build_b)
    atomic_write(
        run_dir / "reproducibility.json",
        (json.dumps(reproducibility, indent=2, sort_keys=True) + "\n").encode(),
        mode=0o600,
    )

    return CleanBuildResult(
        result=build_a,
        workspace=workspace_a,
        reproducibility=reproducibility,
        verification_result=build_b if inputs.build_count == 2 else None,
        verification_workspace=workspace_b if inputs.build_count == 2 else None,
    )


def _compare_full_builds(
    first_clean: CleanBuildResult,
    first_root: PreparedFinalRoot,
    first_package: PackagedInstallSet,
    second_clean: CleanBuildResult,
    second_root: PreparedFinalRoot,
    second_package: PackagedInstallSet,
) -> dict[str, object]:
    """Compare the complete independently compiled and signed firmware outputs."""

    first_paths = {
        **{f"base/{name}": first_clean.result / name for name in BUILD_RESULT_FILES},
        "raptor/raptor-full-component.tar.gz": first_root.component_artifact,
        "final-root/final-root.universal.json": first_root.directory / "final-root.universal.json",
        "final-root/system.universal.squashfs": first_root.directory / "system.universal.squashfs",
        **{
            f"split-kernels/{name}": first_package.split_directory / name
            for name in (
                "final-kernel.uimage", "final-linux.config",
                "installer-jzmmc_v12.ko", "installer-kernel.uimage",
            )
        },
        **{
            f"install-set/{name}": first_package.directory / name
            for name in (
                "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin", "THINGINO2.BIN",
                "install-set.manifest.json", "stage1-bootstrap.squashfs",
                "thingino-universal.tgb",
            )
        },
    }
    second_paths = {
        **{f"base/{name}": second_clean.result / name for name in BUILD_RESULT_FILES},
        "raptor/raptor-full-component.tar.gz": second_root.component_artifact,
        "final-root/final-root.universal.json": second_root.directory / "final-root.universal.json",
        "final-root/system.universal.squashfs": second_root.directory / "system.universal.squashfs",
        **{
            f"split-kernels/{name}": second_package.split_directory / name
            for name in (
                "final-kernel.uimage", "final-linux.config",
                "installer-jzmmc_v12.ko", "installer-kernel.uimage",
            )
        },
        **{
            f"install-set/{name}": second_package.directory / name
            for name in (
                "DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin", "THINGINO2.BIN",
                "install-set.manifest.json", "stage1-bootstrap.squashfs",
                "thingino-universal.tgb",
            )
        },
    }
    if set(first_paths) != set(second_paths):
        raise LocalBuildRunError("complete firmware artifact allowlist changed")
    files: dict[str, dict[str, object]] = {}
    differences: list[str] = []
    for name in sorted(first_paths):
        first = _regular(first_paths[name], f"build A {name}")
        second = _regular(second_paths[name], f"build B {name}")
        first_sha256, second_sha256 = _sha256(first), _sha256(second)
        if first.stat().st_size != second.stat().st_size or first_sha256 != second_sha256:
            differences.append(name)
            files[name] = {
                "build_a_sha256": first_sha256,
                "build_a_size": first.stat().st_size,
                "build_b_sha256": second_sha256,
                "build_b_size": second.stat().st_size,
            }
        else:
            files[name] = {"sha256": first_sha256, "size": first.stat().st_size}
    return {
        "builds": 2,
        "byte_identical": not differences,
        "component_cache_used": False,
        "differences": differences,
        "files": files,
        "inspections_accepted": True,
        "scope": "complete-firmware",
    }


def _write_install_build_manifest(
    inputs: ValidatedBuildInputs,
    environment: BuildEnvironment,
    clean: CleanBuildResult,
    packaged: PackagedInstallSet,
    run_dir: Path,
) -> None:
    """Record successful host inspection without camera inputs or signing material."""

    run_manifest = {
        "artifact_scope": "model-universal",
        "media_backend": "raptor",
        "build_count": inputs.build_count,
        "data_mode": inputs.data_mode,
        "download_cache": environment.download_identity,
        "install_set": "install-set",
        "inspection": packaged.inspection,
        "private": False,
        "project_head": inputs.head,
        "provisioning": "separate-per-camera-sidecar",
        "public_firmware_release_gate_consulted": False,
        "reproducibility": clean.reproducibility,
        "schema_version": RUN_SCHEMA_VERSION,
        "sources_lock_sha256": environment.lock_sha256,
        "status": "host-built and inspected; live installation not authorized",
        "thingino_toolchain": environment.thingino_toolchain_identity,
    }
    atomic_write(
        run_dir / "local-build-run.universal.json",
        (json.dumps(run_manifest, indent=2, sort_keys=True) + "\n").encode(),
        mode=0o600,
    )


def _build_local_install_set(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    data_mode: str,
    artifact_scope: str,
    signing_key: Path,
    build_count: int,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Build one signed model-universal full-Raptor install set."""

    if artifact_scope != "model-universal":
        raise LocalBuildRunError(
            "personalized local builds are retired; use model-universal full Raptor"
        )

    inputs = validate_build_inputs(
        build_root=build_root,
        vendor_bundle_dir=vendor_bundle_dir,
        data_mode=data_mode,
        artifact_scope=artifact_scope,
        signing_key=signing_key,
        build_count=build_count,
    )
    run_dir = inputs.build_root / "runs" / _run_id(inputs.head)
    if run_dir.exists() or run_dir.is_symlink():
        raise LocalBuildRunError("local build run directory already exists")
    _write_owner(run_dir, head=inputs.head)
    try:
        environment = _acquire_build_environment(inputs, run_dir)
        clean = _build_clean_roots(inputs, environment, run_dir)
        final_root = prepare_install_root(
            inputs, environment, clean, run_dir,
            progress=progress,
            independent_component_build=inputs.build_count == 2,
        )
        packaged = package_install_root(inputs, environment, clean, final_root, run_dir)
        if inputs.build_count == 2:
            if clean.verification_result is None or clean.verification_workspace is None:
                raise LocalBuildRunError("second clean build was not retained")
            verification_dir = run_dir / "verification-build-b"
            verification_dir.mkdir(mode=0o700)
            verification_clean = CleanBuildResult(
                result=clean.verification_result,
                workspace=clean.verification_workspace,
                reproducibility=clean.reproducibility,
            )
            verification_root = prepare_install_root(
                inputs,
                environment,
                verification_clean,
                verification_dir,
                progress=progress,
                independent_component_build=True,
            )
            verification_package = package_install_root(
                inputs,
                environment,
                verification_clean,
                verification_root,
                verification_dir,
                task_scratch_root=run_dir,
            )
            reproducibility = _compare_full_builds(
                clean,
                final_root,
                packaged,
                verification_clean,
                verification_root,
                verification_package,
            )
        else:
            reproducibility = {
                "builds": 1,
                "byte_identical": None,
                "component_cache_used": None,
                "inspections_accepted": True,
                "scope": "complete-firmware",
            }
        atomic_write(
            run_dir / "reproducibility.json",
            (json.dumps(reproducibility, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o600,
        )
        clean = CleanBuildResult(
            result=clean.result,
            workspace=clean.workspace,
            reproducibility=reproducibility,
            verification_result=clean.verification_result,
            verification_workspace=clean.verification_workspace,
        )
        if reproducibility["byte_identical"] is False:
            differing = ", ".join(reproducibility["differences"])
            raise LocalBuildRunError(
                f"complete firmware builds are not byte-identical: {differing}"
            )
        _write_install_build_manifest(
            inputs, environment, clean, packaged, run_dir,
        )
    except (
        DownloadCacheError,
        FinalRootError,
        LocalBuildAcquireError,
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
        "artifact_scope": "model-universal",
        "media_backend": "raptor",
        "build_count": inputs.build_count,
        "build_root": str(inputs.build_root),
        "data_mode": inputs.data_mode,
        "install_set_dir": str(packaged.directory),
        "inspection": packaged.inspection,
        "nor_writes": False,
        "public_firmware_release_gate_consulted": False,
        "reproducible": clean.reproducibility["byte_identical"] is True,
        "run_dir": str(run_dir),
        "schema_version": 2,
        "status": "host-built and inspected; live installation not authorized",
        "universal_firmware": str(packaged.directory / "thingino-universal.tgb"),
    }


def build_local_install_set(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    media_closure_dir: Path,
    private_config_dir: Path,
    expected_wpa_config_path: Path,
    session_dir: Path,
    data_mode: str = "initialize",
    build_count: int = 1,
) -> dict[str, object]:
    """Retired personalized build entrypoint.

    Keep the symbol for callers that can report a structured migration error,
    but reject it before resolving or creating any build workspace.
    """

    raise LocalBuildRunError(
        "personalized local builds are retired; use local-build build-universal"
    )


def build_local_universal_install_set(
    *,
    build_root: Path,
    vendor_bundle_dir: Path,
    signing_key: Path,
    data_mode: str = "initialize",
    build_count: int = 1,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Build the source-complete universal Raptor image for A1 cameras."""

    result = _build_local_install_set(
        build_root=build_root,
        vendor_bundle_dir=vendor_bundle_dir,
        data_mode=data_mode,
        artifact_scope="model-universal",
        signing_key=signing_key,
        build_count=build_count,
        progress=progress,
    )
    result["raptor_full_source_build"] = True
    return result
