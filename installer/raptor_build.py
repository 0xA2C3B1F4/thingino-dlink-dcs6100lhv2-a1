"""Headless public Raptor acquisition, offline compilation and component packaging."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import shutil
import re
import uuid
from pathlib import Path

from .local_build_bootstrap import (
    bootstrap_public_build_inputs,
    load_source_lock_snapshot,
)
from .local_build_acquire import _private_child_directory, _directory, _load_json_object
from .local_build import _git as project_git
from .local_build_run import _prepare_thingino_toolchain, _run_id, _write_owner
from .local_build_support import _run, _sha256
from .raptor_source import Progress, acquire_sources, recipe_identity
from .raptor_component import audit_elf, validate_component
from .sd_package import atomic_write


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
        # The client may have died after create but before --cidfile could be
        # written. A random name plus an independently checked label binds cleanup.
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
            # Keep the original compiler/client error. Its message and log name
            # include the unique container name for subsequent host inspection.
            pass


def publish_artifact(
    *, cache: Path, destination: Path, candidate: Path, receipt: dict[str, object]
) -> Path:
    """Publish a complete generation atomically; never replace an existing one."""
    publication = Path(tempfile.mkdtemp(prefix=".publish-", dir=cache))
    artifact = destination / "raptor-rwd-component.tar.gz"
    try:
        atomic_write(publication / artifact.name, candidate.read_bytes(), mode=0o400)
        atomic_write(
            publication / "build.json",
            (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode(),
            mode=0o400,
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("Raptor cache generation was published by another build")
        # A concurrent publisher's nonempty directory also makes rename fail.
        os.rename(publication, destination)
    finally:
        if publication.exists():
            shutil.rmtree(publication)
    return artifact


def _build_raptor_component(
    *, build_root: Path, progress: Progress | None = None
) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    recipe = recipe_identity(root)
    if progress:
        progress({"phase": "raptor-build-bootstrap"})
    boot = bootstrap_public_build_inputs(build_root=build_root)
    build_root = Path(boot["build_root"])
    image = boot["builder_image"]["id"]
    lock, lock_sha = load_source_lock_snapshot(root / "sources.lock.json")
    metadata = lock["sources"]["thingino_build_toolchain_aarch64"]
    sources, source_receipt = acquire_sources(
        root=root, cache_root=build_root / "cache", progress=progress
    )
    identity = {
        "recipe": recipe,
        "builder_image_id": image,
        "toolchain_sha256": metadata["sha256"],
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = _private_child_directory(
        build_root / "cache", "downloads", "raptor-artifacts"
    )
    destination = cache / key
    artifact = destination / "raptor-rwd-component.tar.gz"
    if destination.exists() or destination.is_symlink():
        _directory(destination, "Raptor artifact cache")
        receipt = _load_json_object(destination / "build.json", "Raptor build receipt")
        if receipt.get("identity") != identity:
            raise ValueError("Raptor artifact cache identity changed")
        validate_component(artifact, expected_sha256=receipt["sha256"], root=root)
        if (
            _sha256(artifact) != receipt["sha256"]
            or artifact.stat().st_size != receipt["size"]
        ):
            raise ValueError("Raptor artifact cache bytes changed")
        return {**receipt, "raptor_rwd_artifact": str(artifact), "cached": True}
    run = build_root / "runs" / ("raptor-" + _run_id(key))
    _write_owner(run, head=project_git(root, "rev-parse", "HEAD"))
    try:
        if progress:
            progress({"phase": "raptor-toolchain-prepare", "run_dir": str(run)})
        toolchain, toolchain_receipt = _prepare_thingino_toolchain(
            root=root,
            run_dir=run,
            build_root=build_root,
            source_checkout=Path(boot["source_checkout"]),
            builder_image=image,
            lock_sha256=lock_sha,
            metadata=metadata,
        )
        inputs = run / "inputs"
        inputs.mkdir(mode=0o700)
        for name, record in source_receipt["sources"].items():
            subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-C",
                    str(sources / name),
                    "archive",
                    "--format=tar",
                    f"--prefix={name}/",
                    "--output=" + str(inputs / (name + ".tar")),
                    record["tree"],
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        result = run / "result"
        result.mkdir(mode=0o700)
        workspace = run / "raptor-workspace.ext4"
        if shutil.disk_usage(run).free < 2 * 1024 * 1024 * 1024:
            raise ValueError(
                "Raptor build volume needs 2 GiB free for its Linux workspace"
            )
        run_container(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--platform=linux/arm64",
                "--mount",
                f"type=bind,src={run},dst=/host",
                image,
                "sh",
                "-c",
                "test ! -e /host/raptor-workspace.ext4 && "
                "truncate -s 2G /host/raptor-workspace.ext4 && "
                "mkfs.ext4 -q -F -L raptor-build /host/raptor-workspace.ext4",
            ],
            label="Raptor Linux build filesystem",
            log_path=run / "logs/raptor-workspace.log",
        )
        if progress:
            progress(
                {
                    "phase": "raptor-offline-compile",
                    "log": str(run / "logs/raptor-build.log"),
                }
            )
        run_container(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--platform=linux/arm64",
                "--privileged",
                "--pids-limit=256",
                "--mount",
                f"type=bind,src={inputs},dst=/inputs,readonly",
                "--mount",
                f"type=bind,src={toolchain},dst=/toolchain.tar.gz,readonly",
                "--mount",
                f"type=bind,src={root/'scripts/container_build_raptor.sh'},dst=/build.sh,readonly",
                "--mount",
                f"type=bind,src={workspace},dst=/workspace.ext4",
                "--mount",
                f"type=bind,src={result},dst=/result",
                image,
                "bash",
                "-c",
                "set -e; mkdir -p /work; mount -o loop /workspace.ext4 /work; "
                "trap 'umount /work' EXIT; trap 'exit 130' HUP INT TERM; bash /build.sh; "
                "cp /work/result/* /result/",
            ],
            label="offline Raptor component build",
            log_path=run / "logs/raptor-build.log",
        )
        files = {
            "raptor-lock.json": (
                root / "components/raptor-rwd/raptor-lock.json"
            ).read_bytes(),
            "etc/raptor.conf": (
                root / "components/raptor-rwd/raptor.conf"
            ).read_bytes(),
        }
        audits = {}
        for name, relative in (
            ("rwd", "usr/bin/rwd"),
            ("librss_common.so", "usr/lib/librss_common.so"),
            ("librss_ipc.so", "usr/lib/librss_ipc.so"),
        ):
            data = (result / name).read_bytes()
            audits[relative] = audit_elf(data, relative)
            files[relative] = data
        hashes = {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        }
        files["component.json"] = (
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "raptor-rwd-static-component",
                    "files": hashes,
                    "recipe": recipe,
                    "builder_image_id": image,
                    "toolchain_sha256": metadata["sha256"],
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
        buffer = io.BytesIO()
        with tarfile.open(
            fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT
        ) as archive:
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755 if name.startswith("usr/") else 0o644
                member.mtime = 1786006608
                archive.addfile(member, io.BytesIO(data))
        candidate = run / "raptor-rwd-component.tar.gz"
        atomic_write(candidate, gzip.compress(buffer.getvalue(), mtime=0), mode=0o400)
        sha = _sha256(candidate)
        validate_component(candidate, expected_sha256=sha, root=root)
        receipt = {
            "identity": identity,
            "sha256": sha,
            "size": candidate.stat().st_size,
            "run_dir": str(run),
            "log": str(run / "logs/raptor-build.log"),
            "elf": audits,
            "toolchain": toolchain_receipt,
            "workspace": str(workspace),
            "status": "host-built and validated; physical and distribution gates remain open",
        }
        publish_artifact(
            cache=cache,
            destination=destination,
            candidate=candidate,
            receipt=receipt,
        )
        return {**receipt, "raptor_rwd_artifact": str(artifact), "cached": False}
    except BaseException:
        atomic_write(
            run / "FAILED", b"Raptor build failed; no component selected\n", mode=0o600
        )
        raise


def build_raptor_component(
    *, build_root: Path, progress: Progress | None = None
) -> dict[str, object]:
    from .local_build_support import LocalBuildRunError

    try:
        return _build_raptor_component(build_root=build_root, progress=progress)
    except (ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise LocalBuildRunError(f"Raptor source build: {exc}") from exc
