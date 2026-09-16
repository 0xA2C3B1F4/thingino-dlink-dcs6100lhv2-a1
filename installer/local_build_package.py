"""Final-root composition and inspected local install-set packaging."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .final_bundle import render_final_kernel_fragment
from .final_root import prepare_universal_final_root
from .local_build_models import (
    BuildEnvironment,
    CleanBuildResult,
    PackagedInstallSet,
    PreparedFinalRoot,
    ValidatedBuildInputs,
)
from .local_build_support import LocalBuildRunError, _llvm_tools, _run
from .sd_package import atomic_write, read_snapshot
from .stage1.build import build_universal_install_set, render_installer_kernel_fragment


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


def prepare_install_root(
    inputs: ValidatedBuildInputs,
    environment: BuildEnvironment,
    clean: CleanBuildResult,
    run_dir: Path,
    *,
    progress: Callable[[dict[str, object]], None] | None = None,
    independent_component_build: bool = False,
) -> PreparedFinalRoot:
    """Prepare and compose the source-built Raptor universal image."""

    if inputs.artifact_scope != "model-universal":
        raise LocalBuildRunError(
            "only model-universal full Raptor builds are supported"
        )

    mksquashfs = _workspace_tool(
        name="mksquashfs",
        run_dir=run_dir,
        workspace=clean.workspace,
        builder_image=environment.builder_image,
    )
    unsquashfs = _workspace_tool(
        name="unsquashfs",
        run_dir=run_dir,
        workspace=clean.workspace,
        builder_image=environment.builder_image,
    )
    prepared_root = run_dir / "universal-final-root"
    prepare_universal_final_root(
        base_rootfs=(clean.result / "thingino-base.squashfs").read_bytes(),
        vendor_bundle=inputs.vendor_bundle,
        media_closure=None,
        output_dir=prepared_root,
        mksquashfs=mksquashfs,
        unsquashfs=unsquashfs,
        support_only=True,
    )
    prepared_system_name = "system.universal.squashfs"
    prepared_manifest_name = "final-root.universal.json"

    from .raptor_full_build import build_full_component
    from .raptor_full_root import compose_universal_root

    component = build_full_component(
        root=inputs.root, build_root=inputs.build_root, run_dir=run_dir,
        builder_image=environment.builder_image,
        base_rootfs=prepared_root / prepared_system_name,
        base_workspace=clean.workspace,
        toolchain=environment.thingino_toolchain_archive,
        progress=progress,
        artifact_cache=not independent_component_build,
    )
    raptor_root = run_dir / "raptor-final-root"
    compose_universal_root(
        repository=inputs.root,
        base_rootfs=prepared_root / prepared_system_name,
        base_manifest=prepared_root / prepared_manifest_name,
        component_artifact=Path(component["artifact"]),
        component_sha256=component["sha256"],
        build_inputs=component["identity"]["build_inputs"],
        output_dir=raptor_root, mksquashfs=mksquashfs, unsquashfs=unsquashfs,
    )
    system_rootfs = raptor_root / "system.universal.squashfs"
    system = read_snapshot(system_rootfs)
    return PreparedFinalRoot(
        directory=raptor_root,
        system=system,
        mksquashfs=mksquashfs,
        unsquashfs=unsquashfs,
        component_artifact=Path(component["artifact"]),
    )


def package_install_root(
    inputs: ValidatedBuildInputs,
    environment: BuildEnvironment,
    clean: CleanBuildResult,
    final_root: PreparedFinalRoot,
    run_dir: Path,
    *,
    task_scratch_root: Path | None = None,
) -> PackagedInstallSet:
    """Build split kernels from build A, package, then require schema-2 inspection."""

    if inputs.artifact_scope != "model-universal" or inputs.signing_key is None:
        raise LocalBuildRunError(
            "only signed model-universal full Raptor install sets are supported"
        )

    task_scratch_root = task_scratch_root or run_dir
    fragments = run_dir / "kernel-fragments"
    fragments.mkdir(mode=0o700)
    installer_fragment = fragments / "installer.fragment"
    final_fragment = fragments / "final.fragment"
    atomic_write(
        installer_fragment, render_installer_kernel_fragment(len(final_root.system))
    )
    atomic_write(final_fragment, render_final_kernel_fragment(len(final_root.system)))

    split_result = run_dir / "split-kernels"
    split_result.mkdir(mode=0o700)
    _run(
        [
            str(inputs.root / "scripts/run_macos_split_kernel_build.sh"),
            "--task-scratch-root",
            str(task_scratch_root),
            "--builder-lock",
            str(task_scratch_root),
            "--builder-image",
            environment.builder_image,
            "--container-name",
            f"dcs6100-split-{run_dir.name[-25:]}",
            "--workspace-image",
            str(clean.workspace),
            "--installer-fragment",
            str(installer_fragment),
            "--final-fragment",
            str(final_fragment),
            "--vendor-site",
            str(environment.vendor_site),
            "--audio-link",
            str(inputs.audio_link),
            "--rust-source",
            str(environment.rust_source),
            "--rust-toolchain",
            str(environment.rust_toolchain),
            "--ingenic-toolchain-archive",
            str(environment.ingenic_toolchain_archive),
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
        "system": final_root.system,
        "mmc_module": read_snapshot(split_result / "installer-jzmmc_v12.ko"),
        "output_dir": install_set,
        "clang": stage1_clang,
        "lld": stage1_lld,
        "mksquashfs": final_root.mksquashfs,
        "unsquashfs": final_root.unsquashfs,
    }
    try:
        universal_root_manifest = json.loads(
            (final_root.directory / "final-root.universal.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBuildRunError(
            "universal final-root provenance is invalid"
        ) from exc
    build_universal_install_set(
        **install_arguments,
        universal_root_manifest=universal_root_manifest,
        signing_key=inputs.signing_key,
        data_mode=inputs.data_mode,
    )
    inspection = _inspect_install_set(
        inputs.root,
        install_set,
        run_dir / "logs/inspect-install-set.json",
    )
    return PackagedInstallSet(
        directory=install_set,
        inspection=inspection,
        split_directory=split_result,
    )
