"""Compose source-built Raptor into a closed, unprovisioned system image."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from . import final_root
from .artifacts import validate_squashfs
from .mtd3_split import SYSTEM_FLASH_SPAN
from .raptor_component import _root_file
from .raptor_full_component import audit_payload, payload_mode, validate_component
from .raptor_provisioning import CONFIGS, CONTROL_INIT, RUNTIME, SERVICE
from .sd_package import atomic_write, read_snapshot


RETIRED_PATHS = (
    "usr/bin/prudynt", "usr/bin/prudyntctl", "usr/bin/daynightd",
    "etc/prudynt.json", "etc/init.d/S10daynightd", "etc/init.d/S31prudynt",
    "etc/init.d/S13prudynt-rwd", "etc/init.d/S96rwd", "etc/init.d/S98recorder",
)


def _path(root: Path, relative: str) -> Path:
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise final_root.FinalRootError(f"symlink in full Raptor destination: {relative}")
    return path


def _write(root: Path, relative: str, raw: bytes, mode: int) -> None:
    path = _path(root, relative)
    if path.exists() and not path.is_file():
        raise final_root.FinalRootError(f"invalid full Raptor destination: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, raw, mode=mode)


def remove_retired_runtime(root: Path) -> None:
    """Remove only known retired owners from a task-owned extracted image."""
    for relative in RETIRED_PATHS:
        path = _path(root, relative)
        if path.exists():
            if not path.is_file():
                raise final_root.FinalRootError(f"legacy media input changed type: {relative}")
            path.unlink()


def _verify_payload(root: Path, files: dict[str, bytes]) -> None:
    audits = audit_payload(files)
    required = set()
    for relative, raw in files.items():
        path = _path(root, relative)
        if (
            not path.is_file()
            or path.read_bytes() != raw
            or path.stat().st_mode & 0o777 != payload_mode(relative)
        ):
            raise final_root.FinalRootError(f"installed Raptor component changed: {relative}")
        if relative in audits:
            required.update(audits[relative]["needed"])
    _root_file(root, "lib/ld.so.1")
    for name in sorted(required):
        for directory in ("usr/lib", "lib"):
            try:
                _root_file(root, f"{directory}/{name}")
                break
            except ValueError:
                continue
        else:
            raise final_root.FinalRootError(f"Raptor runtime dependency missing: {name}")


def _install_runtime(
    root: Path, *, repository: Path, files: dict[str, bytes],
    component: dict[str, object],
) -> dict[str, object]:
    """Operate only on the task-owned extracted root, with explicit file targets."""
    audit_payload(files)
    remove_retired_runtime(root)
    for relative, raw in files.items():
        _write(root, relative, raw, payload_mode(relative))
    config_files = {
        relative: (repository / "components/raptor" / Path(relative).name).read_bytes()
        for relative in CONFIGS
    }
    for relative, raw in config_files.items():
        _write(root, relative, raw, 0o600)
    service = (repository / "components/raptor/S96raptor").read_bytes()
    _write(root, SERVICE, service, 0o644)
    helper_sources = {
        "etc/init.d/S05dlink-media-preconditions": repository / "components/raptor/S05raptor-preconditions",
        "etc/init.d/dlink-media-acceptance": repository / "components/raptor/media-acceptance.sh",
        "usr/sbin/dlink-media-verify": repository / "installer/templates/dlink-media-verify",
        "usr/sbin/dlink-runtime-snapshot": repository / "installer/templates/dlink-runtime-snapshot",
        "usr/sbin/dlink-application-verify": repository / "installer/templates/dlink-application-verify",
    }
    helper_files = {
        destination: source.read_bytes()
        for destination, source in helper_sources.items()
    }
    for destination, raw in helper_files.items():
        _write(root, destination, raw, 0o755)
    for relative in (
        "etc/init.d/S95thingino-control",
        "usr/share/thingino-provisioning/init/S95thingino-control",
    ):
        _write(root, relative, CONTROL_INIT, 0o644)

    closure_path = _path(root, "etc/dlink-media-closure.private.json")
    closure = json.loads(closure_path.read_bytes())
    if not isinstance(closure, dict) or not isinstance(closure.get("files"), list):
        raise final_root.FinalRootError("base media provenance is invalid")
    installed = {**files, **config_files, **helper_files, SERVICE: service}
    replaced = {*RETIRED_PATHS, *installed, "etc/init.d/S95thingino-control"}
    preserved = [
        entry for entry in closure["files"]
        if isinstance(entry, dict)
        and str(entry.get("destination", entry.get("path", ""))).lstrip("/") not in replaced
    ]
    source_init = [
        entry for entry in closure.get("source_built_init", [])
        if isinstance(entry, dict)
        and str(entry.get("destination", "")).lstrip("/") not in replaced
    ]
    for key in ("prudynt", "runtime_dlopen"):
        closure.pop(key, None)
    config_digest = hashlib.sha256(config_files[CONFIGS[0]]).hexdigest()
    closure.update({
        "runtime": RUNTIME,
        "component": component,
        "runtime_config_sha256": config_digest,
        "runtime_config_source_sha256": config_digest,
        "source_built_init": source_init + [{
            "path": SERVICE, "destination": "/" + SERVICE,
            "sha256": hashlib.sha256(service).hexdigest(), "size": len(service),
        }],
        "startup_order": [
            name for name in closure.get("startup_order", [])
            if "etc/init.d/" + name not in replaced
        ] + [Path(SERVICE).name],
        "files": preserved + [
            {"path": name, "destination": "/" + name,
             "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
            for name, raw in sorted(installed.items())
        ],
    })
    _write(root, "etc/dlink-media-closure.private.json",
           (json.dumps(closure, sort_keys=True) + "\n").encode(), 0o600)
    _verify_payload(root, files)
    final_root._validate_universal_tree(root)
    return closure


def compose_universal_root(
    *, repository: Path, base_rootfs: Path, base_manifest: Path,
    component_artifact: Path, component_sha256: str,
    build_inputs: dict[str, object], output_dir: Path,
    mksquashfs: Path, unsquashfs: Path,
) -> dict[str, object]:
    """Bind the new image to its fresh base and independently audited component."""
    base = read_snapshot(base_rootfs)
    validate_squashfs(base)
    document = json.loads(read_snapshot(base_manifest))
    digest = hashlib.sha256(base).hexdigest()
    if (
        not isinstance(document, dict)
        or document.get("artifact_scope") != "model-universal"
        or document.get("schema_version") != 1
        or document.get("contains_device_secrets") is not False
        or document.get("provisioning_required") is not True
        or document.get("system") != {
            "filename": final_root.UNIVERSAL_OUTPUT_NAME,
            "sha256": digest, "size": len(base),
        }
        or build_inputs.get("base_rootfs_sha256") != digest
    ):
        raise final_root.FinalRootError("full Raptor base provenance does not bind this image")
    files, component = validate_component(
        component_artifact, expected_sha256=component_sha256,
        root=repository, expected_build_inputs=build_inputs,
    )
    if output_dir.exists() or output_dir.is_symlink():
        raise final_root.FinalRootError("refusing to reuse a full Raptor output directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        root = work / "root"
        source = work / "base.squashfs"
        source.write_bytes(base)
        final_root._extract_base_root(unsquashfs=unsquashfs, source=source, destination=root)
        final_root._validate_universal_tree(root, allow_support_base=True)
        _install_runtime(root, repository=repository, files=files, component=component)
        output = work / final_root.UNIVERSAL_OUTPUT_NAME
        final_root._build_final_root_squashfs(
            mksquashfs=mksquashfs, root=root, output=output,
            label="source-built full Raptor universal root",
        )
        raw = output.read_bytes()
        validate_squashfs(raw, partition_limit=SYSTEM_FLASH_SPAN)
        audit = work / "audit"
        final_root._extract_base_root(unsquashfs=unsquashfs, source=output, destination=audit)
        final_root._validate_universal_tree(audit)
        _verify_payload(audit, files)
        for relative in (
            *CONFIGS, SERVICE, "etc/init.d/S05dlink-media-preconditions",
            "etc/init.d/dlink-media-acceptance",
        ):
            if _path(root, relative).read_bytes() != _path(audit, relative).read_bytes():
                raise final_root.FinalRootError(f"packed Raptor runtime changed: {relative}")
        manifest = {
            **document,
            "composition_required": False,
            "media_profile": RUNTIME,
            "source_sha256": digest,
            "raptor_full": {
                "component_sha256": component_sha256,
                "source_build": component,
            },
            "system": {
                "filename": final_root.UNIVERSAL_OUTPUT_NAME,
                "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            },
        }
        atomic_write(work / "final-root.universal.json",
                     (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), mode=0o600)
        source.unlink()
        shutil.rmtree(root)
        shutil.rmtree(audit)
        output.chmod(0o600)
        os.rename(work, output_dir)
        return manifest
    finally:
        if work.exists():
            shutil.rmtree(work)
