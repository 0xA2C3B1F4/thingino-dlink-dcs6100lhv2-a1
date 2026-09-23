"""Acquire the pinned public checkout and builder image for a local build."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from scripts import source_checkout

from .local_build import (
    LocalBuildError,
    WORKSPACE_NAME,
    local_build_workspace_status,
)
from .sd_package import atomic_write


HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
RECEIPT_SCHEMA_VERSION = 1
MAX_SOURCE_LOCK_BYTES = 1024 * 1024


class LocalBuildBootstrapError(ValueError):
    """Pinned public local-build inputs could not be acquired safely."""


def _project_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "sources.lock.json").is_file():
        raise LocalBuildBootstrapError("project source lock is missing")
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source_lock_snapshot(path: Path) -> tuple[dict[str, object], str]:
    """Read, hash, and validate one regular-file snapshot of the source lock."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LocalBuildBootstrapError("project source lock cannot be opened safely") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise LocalBuildBootstrapError("project source lock is not a regular file")
        if details.st_size < 1 or details.st_size > MAX_SOURCE_LOCK_BYTES:
            raise LocalBuildBootstrapError("project source lock violates its size policy")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(MAX_SOURCE_LOCK_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) != details.st_size:
        raise LocalBuildBootstrapError("project source lock changed while being read")
    digest = hashlib.sha256(payload).hexdigest()
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="dcs6100-source-lock-",
            suffix=".json",
        ) as snapshot:
            snapshot.write(payload)
            snapshot.flush()
            lock = source_checkout.load_lock(Path(snapshot.name))
    except (OSError, source_checkout.SourceError) as exc:
        raise LocalBuildBootstrapError("project source lock is invalid") from exc
    return lock, digest


def _run(arguments: list[str], *, label: str, timeout: int) -> str:
    try:
        completed = subprocess.run(
            arguments,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalBuildBootstrapError(f"{label} could not run") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise LocalBuildBootstrapError(f"{label} failed{suffix}")
    return completed.stdout.strip()


def _docker_image_id(image: str) -> str | None:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalBuildBootstrapError("Docker image inspection could not run") from exc
    if completed.returncode != 0:
        return None
    image_id = completed.stdout.strip()
    if IMAGE_ID.fullmatch(image_id) is None:
        raise LocalBuildBootstrapError("Docker returned an invalid builder image ID")
    return image_id


def _load_receipt(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise LocalBuildBootstrapError("builder image receipt is not a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBuildBootstrapError("builder image receipt is invalid") from exc
    if not isinstance(document, dict):
        raise LocalBuildBootstrapError("builder image receipt is invalid")
    expected = {
        "containerfile_sha256",
        "image_id",
        "image_tag",
        "platform",
        "project_head",
        "schema_version",
        "sources_lock_sha256",
    }
    if set(document) != expected or document.get("schema_version") != 1:
        raise LocalBuildBootstrapError("builder image receipt fields are invalid")
    for field, pattern in (
        ("containerfile_sha256", HEX64),
        ("image_id", IMAGE_ID),
        ("project_head", HEX40),
        ("sources_lock_sha256", HEX64),
    ):
        value = document.get(field)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise LocalBuildBootstrapError(f"builder image receipt {field} is invalid")
    if document.get("platform") != "linux/arm64":
        raise LocalBuildBootstrapError("builder image receipt platform is invalid")
    tag = document.get("image_tag")
    if not isinstance(tag, str) or not tag.startswith("dcs6100-thingino-builder:arm64-"):
        raise LocalBuildBootstrapError("builder image receipt tag is invalid")
    return document


def _builder_image(
    *, root: Path, build_root: Path, head: str, lock_sha256: str
) -> dict[str, str]:
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        raise LocalBuildBootstrapError(
            "the guided macOS builder currently requires an Apple Silicon host"
        )
    containerfile = root / "containers/thingino-builder-arm64.Containerfile"
    if containerfile.is_symlink() or not containerfile.is_file():
        raise LocalBuildBootstrapError("ARM64 builder Containerfile is missing")
    containerfile_sha256 = _sha256(containerfile)
    tag = f"dcs6100-thingino-builder:arm64-{head[:12]}"
    receipt_path = build_root / "cache/images" / f"builder-arm64-{head}.json"
    image_id = _docker_image_id(tag)
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = _load_receipt(receipt_path)
        expected = {
            "containerfile_sha256": containerfile_sha256,
            "image_tag": tag,
            "platform": "linux/arm64",
            "project_head": head,
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "sources_lock_sha256": lock_sha256,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise LocalBuildBootstrapError(
                "builder image receipt does not match the current checkout"
            )
        if image_id != receipt["image_id"]:
            raise LocalBuildBootstrapError(
                "builder image tag changed after its receipt was recorded"
            )
        return {"id": str(image_id), "receipt": str(receipt_path), "tag": tag}
    if image_id is not None:
        raise LocalBuildBootstrapError(
            "unowned builder image tag already exists without a receipt"
        )
    _run(
        [
            "docker",
            "build",
            "--platform",
            "linux/arm64",
            "--file",
            str(containerfile),
            "--tag",
            tag,
            str(root),
        ],
        label="builder image construction",
        timeout=3600,
    )
    image_id = _docker_image_id(tag)
    if image_id is None:
        raise LocalBuildBootstrapError("builder image was not published by Docker")
    receipt = {
        "containerfile_sha256": containerfile_sha256,
        "image_id": image_id,
        "image_tag": tag,
        "platform": "linux/arm64",
        "project_head": head,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "sources_lock_sha256": lock_sha256,
    }
    atomic_write(
        receipt_path,
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
    )
    receipt_path.chmod(0o600)
    return {"id": image_id, "receipt": str(receipt_path), "tag": tag}


def bootstrap_public_build_inputs(*, build_root: Path) -> dict[str, object]:
    """Acquire or revalidate only the public source checkout and builder image."""

    try:
        workspace = local_build_workspace_status(build_root=build_root)
    except LocalBuildError as exc:
        raise LocalBuildBootstrapError(str(exc)) from exc
    if workspace["ready_to_build"] is not True:
        raise LocalBuildBootstrapError(
            f"local build workspace is not ready: {workspace['safe_next_action']}"
        )
    root = _project_root().resolve(strict=True)
    head = str(workspace["current_head"])
    if HEX40.fullmatch(head) is None:
        raise LocalBuildBootstrapError("project HEAD is invalid")
    build_root = Path(str(workspace["build_root"]))
    workspace_manifest = build_root / WORKSPACE_NAME
    lock, lock_sha256 = load_source_lock_snapshot(root / "sources.lock.json")
    if lock_sha256 != json.loads(
        workspace_manifest.read_text(encoding="utf-8")
    ).get("sources_lock_sha256"):
        raise LocalBuildBootstrapError("workspace source-lock binding changed")
    try:
        checkout = build_root / "cache/sources" / f"thingino-{lock_sha256}"
        if checkout.exists() or checkout.is_symlink():
            source_checkout.verify_checkout(checkout, lock)
            checkout_created = False
        else:
            source_checkout.fetch_checkout(checkout, lock)
            checkout_created = True
        verification = source_checkout.verify_checkout(checkout, lock)
    except (OSError, source_checkout.SourceError) as exc:
        raise LocalBuildBootstrapError(
            "pinned Thingino source checkout acquisition failed"
        ) from exc
    image = _builder_image(
        root=root,
        build_root=build_root,
        head=head,
        lock_sha256=lock_sha256,
    )
    return {
        "build_root": str(build_root),
        "builder_image": image,
        "checkout_created": checkout_created,
        "next_action": "acquire-remaining-locked-public-inputs",
        "source_checkout": str(checkout),
        "source_date_epoch": verification["source_date_epoch"],
        "sources_lock_sha256": lock_sha256,
    }
