"""Compile full Raptor against the root and SDK produced by the same build."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

from .local_build_acquire import _directory, _private_child_directory, _load_json_object
from .local_build_support import LocalBuildRunError, _regular, _run, _sha256
from .raptor_full_component import (
    CJSON_NOTICE_SHA256, MONOCYPHER_LICENSE_SHA256, PAYLOAD, audit_payload,
    pack_component, validate_component,
)
from .raptor_source import Progress, acquire_sources, recipe_identity
from .sd_package import atomic_write


COMPILE = """set -euo pipefail
mkdir -p /linux /work /target /base-workspace
mount -o loop /workspace.ext4 /linux
cleanup() {
    mountpoint -q /target && umount /target || true
    mountpoint -q /work && umount /work || true
    mountpoint -q /base-workspace && umount /base-workspace || true
    umount /linux
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
mount -o ro,loop /base-workspace.ext4 /base-workspace
mkdir /linux/work
mount --bind /linux/work /work
/base-workspace/thingino-output/host/bin/unsquashfs -no-progress -d /linux/target /base.squashfs
mount --bind /linux/target /target
mount -o remount,bind,ro /target
mkdir /linux/headers
tar --no-same-owner -xf /inputs/ingenic-headers.tar -C /linux/headers
ln -s /linux/headers/ingenic-headers /headers
bash /build.sh
"""


def run_container(arguments: list[str], *, label: str, log_path: Path) -> None:
    """Remove only this invocation's labeled container if its client is interrupted."""
    token = uuid.uuid4().hex
    name = f"dcs6100-raptor-{token}"
    owner_label = "org.thingino.raptor.owner"
    command = (
        arguments[:2]
        + ["--name", name, "--label", f"{owner_label}={token}"]
        + arguments[2:]
    )
    try:
        _run(command, label=f"{label} ({name})", log_path=log_path)
    finally:
        # The client may have died after create but before cleanup. A random name
        # plus an independently checked label binds cleanup to this invocation.
        try:
            inspection = subprocess.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    '{{.Id}} {{index .Config.Labels "org.thingino.raptor.owner"}}',
                    name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=15,
                check=False,
            )
            identity = inspection.stdout.strip().split()
            if (
                inspection.returncode == 0
                and len(identity) == 2
                and re.fullmatch(r"[0-9a-f]{64}", identity[0])
                and identity[1] == token
            ):
                subprocess.run(
                    ["docker", "container", "rm", "--force", identity[0]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError):
            # Keep the original compiler/client error for subsequent host inspection.
            pass


def build_full_component(
    *, root: Path, build_root: Path, run_dir: Path, builder_image: str,
    base_rootfs: Path, base_workspace: Path, toolchain: Path,
    progress: Progress | None = None, artifact_cache: bool = True,
) -> dict[str, object]:
    """Build/cache a model component; never read camera credentials or write a device."""
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", builder_image):
        raise LocalBuildRunError("full Raptor builder must be an immutable image ID")
    base_rootfs = _regular(base_rootfs, "fresh final root", limit=8 * 1024 * 1024)
    base_workspace = _regular(base_workspace, "base build workspace")
    toolchain = _regular(toolchain, "source-built Raptor SDK")
    lock = _load_json_object(root / "sources.lock.json", "source lock")
    expected_toolchain = lock["sources"]["thingino_build_toolchain_aarch64"]["sha256"]
    if _sha256(toolchain) != expected_toolchain:
        raise LocalBuildRunError("full Raptor SDK identity changed")
    recipe = recipe_identity(root, full_media=True)
    notices = (
        (root / "third_party/licenses/raptor-common-cJSON-header.txt",
         "cJSON notice", CJSON_NOTICE_SHA256),
        (root / "third_party/licenses/raptor-common-Monocypher-LICENCE.txt",
         "Monocypher licence", MONOCYPHER_LICENSE_SHA256),
    )
    for path, label, expected in notices:
        if _sha256(_regular(path, label, limit=8 * 1024 * 1024)) != expected:
            raise LocalBuildRunError(f"full Raptor {label} identity changed")
    sources, receipt = acquire_sources(
        root=root, cache_root=build_root / "cache", progress=progress, full_media=True
    )
    build_inputs = {
        "base_rootfs_sha256": _sha256(base_rootfs),
        "builder_image_id": builder_image,
        "toolchain_sha256": expected_toolchain,
    }
    identity = {"recipe": recipe, "build_inputs": build_inputs}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = _private_child_directory(build_root / "cache", "downloads", "raptor-full-artifacts")
    destination = cache / key
    artifact = destination / "raptor-full-component.tar.gz"
    if artifact_cache and (destination.exists() or destination.is_symlink()):
        _directory(destination, "full Raptor cache")
        cached = _load_json_object(destination / "build.json", "full Raptor build receipt")
        if cached.get("identity") != identity:
            raise LocalBuildRunError("full Raptor cache identity changed")
        validate_component(artifact, expected_sha256=cached["sha256"], root=root,
                           expected_build_inputs=build_inputs)
        return {**cached, "artifact": str(artifact), "cached": True}

    run = run_dir / "raptor-full"
    run.mkdir(mode=0o700)
    inputs, result = run / "inputs", run / "result"
    inputs.mkdir(mode=0o700)
    result.mkdir(mode=0o700)
    if shutil.disk_usage(run).free < 3 * 1024**3:
        raise LocalBuildRunError("full Raptor build needs 3 GiB free on the build volume")
    try:
        for name, record in receipt["sources"].items():
            subprocess.run(
                ["git", "--no-replace-objects", "-C", str(sources / name), "archive",
                 "--format=tar", f"--prefix={name}/", "--output=" + str(inputs / (name + ".tar")),
                 record["tree"]],
                stdin=subprocess.DEVNULL, check=True, timeout=60,
            )
        workspace = run / "workspace.ext4"
        run_container(
            ["docker", "run", "--rm", "--network=none", "--platform=linux/arm64",
             "--mount", f"type=bind,src={run},dst=/host", builder_image, "sh", "-c",
             "test ! -e /host/workspace.ext4 && truncate -s 2G /host/workspace.ext4 && "
             "mkfs.ext4 -q -F -L raptor-full /host/workspace.ext4"],
            label="full Raptor Linux filesystem", log_path=run_dir / "logs/raptor-full-workspace.log",
        )
        mounts = (
            (inputs, "/inputs", True), (toolchain, "/toolchain.tar.gz", True),
            (root / "scripts/container_build_raptor_full.sh", "/build.sh", True),
            (root / "components/raptor/Ubuntu-Font-Licence-1.0.txt", "/font-license", True),
            (notices[0][0], "/cjson-notice", True),
            (notices[1][0], "/monocypher-license", True),
            (base_rootfs, "/base.squashfs", True),
            (base_workspace, "/base-workspace.ext4", True),
            (workspace, "/workspace.ext4", False), (result, "/result", False),
        )
        command = ["docker", "run", "--rm", "--network=none", "--platform=linux/arm64",
                   "--privileged", "--pids-limit=256", "--cpus=2", "--memory=2g",
                   "--memory-swap=2g", "--env",
                   "RAPTOR_SOURCE_ID=tree-" + receipt["sources"]["raptor"]["tree"][:12]]
        for path, target, readonly in mounts:
            if any(character in str(path) for character in (",", "\n", "\r")):
                raise LocalBuildRunError("unsupported character in Raptor build path")
            command += ["--mount", f"type=bind,src={path},dst={target}" + (",readonly" if readonly else "")]
        command += [builder_image, "bash", "-c", COMPILE]
        if progress:
            progress({"phase": "raptor-full-compile", "log": str(run_dir / "logs/raptor-full.log")})
        run_container(command, label="offline full Raptor build", log_path=run_dir / "logs/raptor-full.log")
        payload_root = result / "root"
        paths = {path.relative_to(payload_root).as_posix(): path
                 for path in payload_root.rglob("*") if not path.is_dir() or path.is_symlink()}
        if set(paths) != PAYLOAD or any(path.is_symlink() or not path.is_file() for path in paths.values()):
            raise LocalBuildRunError("full Raptor build output differs from its payload allowlist")
        files = {name: path.read_bytes() for name, path in paths.items()}
        audits = audit_payload(files)
        if _sha256(base_rootfs) != build_inputs["base_rootfs_sha256"] or _sha256(toolchain) != expected_toolchain:
            raise LocalBuildRunError("full Raptor build inputs changed during compilation")
        provenance = {
            "recipe": recipe,
            "source_trees": {name: item["tree"] for name, item in receipt["sources"].items()},
            "build_inputs": build_inputs,
        }
        candidate = run / artifact.name
        atomic_write(candidate, pack_component(files, provenance), mode=0o400)
        digest = _sha256(candidate)
        validate_component(candidate, expected_sha256=digest, root=root, expected_build_inputs=build_inputs)
        report = {"identity": identity, "sha256": digest, "size": candidate.stat().st_size,
                  "elf": audits, "run_dir": str(run), "status": "host-built"}
        if artifact_cache:
            publication = Path(tempfile.mkdtemp(prefix=".publish-", dir=cache))
            try:
                atomic_write(publication / artifact.name, candidate.read_bytes(), mode=0o400)
                atomic_write(publication / "build.json", (json.dumps(report, sort_keys=True) + "\n").encode(), mode=0o400)
                if destination.exists() or destination.is_symlink():
                    raise LocalBuildRunError("full Raptor cache generation already exists")
                os.rename(publication, destination)
            finally:
                if publication.exists():
                    shutil.rmtree(publication)
            output_artifact = artifact
        else:
            # Independent reproducibility builds retain their own compile output
            # and may not satisfy the compile step from a shared artifact cache.
            output_artifact = candidate
            atomic_write(
                run / "build.json",
                (json.dumps(report, sort_keys=True) + "\n").encode(),
                mode=0o400,
            )
        return {**report, "artifact": str(output_artifact), "cached": False}
    except BaseException:
        atomic_write(run / "FAILED", b"No full Raptor component selected\n", mode=0o600)
        raise
