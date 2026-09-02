#!/usr/bin/env python3
"""Build a private persistent Prudynt plus Raptor rwd root."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from installer.artifacts import SOURCE_DATE_EPOCH, validate_squashfs  # noqa: E402
from installer.layout import TARGET  # noqa: E402
from installer.mtd3_image import FOOTER_SIZE  # noqa: E402
from installer.mtd3_split import SYSTEM_FLASH_SPAN  # noqa: E402
from installer.runtime_candidate import (  # noqa: E402
    _validate_mips_elf,
    _validate_provenance,
)
from installer.stage1.build import validate_final_root  # noqa: E402
from scripts.raptor_rwd_runtime import validate_artifact  # noqa: E402


MAX_BASE_BYTES = 8 * 1024 * 1024
INSTALL_REGULAR = {
    "etc/raptor.conf",
    "usr/bin/prudynt",
    "usr/bin/rwd",
    "usr/bin/uhttpd",
    "usr/lib/libmbedcrypto.so.3.6.6",
    "usr/lib/libmbedtls.so.3.6.6",
    "usr/lib/libmbedx509.so.3.6.6",
    "usr/lib/librss_common.so",
    "usr/lib/librss_ipc.so",
    "usr/sbin/thingino-controld",
    "var/www/assets/app.css",
    "var/www/assets/app.js",
    "var/www/index.html",
    "var/www/manifest.webmanifest",
}
INSTALL_SYMLINKS = {
    "usr/lib/libmbedcrypto.so",
    "usr/lib/libmbedcrypto.so.16",
    "usr/lib/libmbedtls.so",
    "usr/lib/libmbedtls.so.21",
    "usr/lib/libmbedx509.so",
    "usr/lib/libmbedx509.so.7",
}
BASE_OWNED_RUNTIME = {
    "S95thingino-control": "etc/init.d/S95thingino-control",
    "usr/bin/prudynt": "usr/bin/prudynt",
    "usr/bin/uhttpd": "usr/bin/uhttpd",
    "usr/sbin/thingino-controld": "usr/sbin/thingino-controld",
    "var/www/assets/app.css": "var/www/assets/app.css",
    "var/www/assets/app.js": "var/www/assets/app.js",
    "var/www/index.html": "var/www/index.html",
    "var/www/manifest.webmanifest": "var/www/manifest.webmanifest",
}
RAPTOR_TLS_REGULAR = {
    "usr/lib/libmbedcrypto.so.3.6.6",
    "usr/lib/libmbedtls.so.3.6.6",
    "usr/lib/libmbedx509.so.3.6.6",
}
RAPTOR_TLS_SYMLINKS = set(INSTALL_SYMLINKS)
RAPTOR_TLS_PREFIX = "usr/lib/raptor"
PRUDYNT_START = b'start-stop-daemon -S -b -m -p "$PIDFILE" -x "$DAEMON"\n'
PRUDYNT_RING_START = (
    b'PRUDYNT_RAPTOR_RING=1 start-stop-daemon -S -b -m -p "$PIDFILE" '
    b'-x "$DAEMON"\n'
)
RESET_CONFLICTS = (
    "etc/init.d/S15thingino-button",
    "etc/modules.d/gpio-userkeys",
    "etc/thingino-button.conf",
)
RAPTOR_PATCHES = (
    "0001-rwd-video-only-whip-cleanup.patch",
    "0002-rwd-ingenic-runtime-and-ring-reader.patch",
)


class PersistentCandidateError(ValueError):
    """A private persistent rwd input or output violated its fixed contract."""


def _regular(path: Path, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PersistentCandidateError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > limit:
            raise PersistentCandidateError(f"{label} violates its size policy")
        raw = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ) or len(raw) != before.st_size:
        raise PersistentCandidateError(f"{label} changed while being read")
    return raw


def patch_prudynt_init(raw: bytes) -> bytes:
    if raw.count(PRUDYNT_START) != 1 or PRUDYNT_RING_START in raw:
        raise PersistentCandidateError("Prudynt init does not match the proven baseline")
    return raw.replace(PRUDYNT_START, PRUDYNT_RING_START)


def _run(arguments: list[str], label: str) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PersistentCandidateError(f"{label} failed") from exc
    return completed.stdout


def _pack(
    *,
    tool: Path,
    root: Path,
    output: Path,
    block_size: int = 262144,
    exportable: bool = True,
    pack_tailends: bool = False,
    partition_limit: int = TARGET.partition(3).size - FOOTER_SIZE,
) -> bytes:
    arguments = [
        str(tool),
        str(root),
        str(output),
        "-comp",
        "xz",
        "-b",
        str(block_size),
        "-noappend",
        "-tailends" if pack_tailends else "-no-tailends",
    ]
    if not exportable:
        arguments.append("-no-exports")
    arguments.extend(
        [
            "-all-root",
            "-no-xattrs",
            "-no-progress",
            "-repro-time",
            str(SOURCE_DATE_EPOCH),
        ]
    )
    _run(arguments, "private WebRTC rootfs build")
    raw = output.read_bytes()
    validate_squashfs(raw, partition_limit=partition_limit)
    return raw


def _destination(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PersistentCandidateError("artifact path escapes the final root")
    destination = root.joinpath(*path.parts)
    try:
        destination.parent.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise PersistentCandidateError("artifact parent escapes the final root") from exc
    return destination


def artifact_destination(relative: str) -> str:
    """Keep rwd's mbedTLS build out of uhttpd's global ABI namespace."""

    if relative.startswith("usr/lib/libmbed"):
        return f"{RAPTOR_TLS_PREFIX}/{PurePosixPath(relative).name}"
    return relative


def _validate_component_provenance(normalized_tar: bytes) -> dict[str, object]:
    """Bind the accepted artifact to this checkout's RWD sources and policy."""

    try:
        with tarfile.open(fileobj=io.BytesIO(normalized_tar), mode="r:") as archive:
            lock_member = archive.getmember("raptor-lock.json")
            config_member = archive.getmember("etc/raptor.conf")
            lock_source = archive.extractfile(lock_member)
            config_source = archive.extractfile(config_member)
            if lock_source is None or config_source is None:
                raise PersistentCandidateError("rwd provenance members cannot be read")
            artifact_lock = lock_source.read()
            artifact_config = config_source.read()
    except (KeyError, tarfile.TarError) as exc:
        raise PersistentCandidateError("rwd provenance members are invalid") from exc

    lock_path = ROOT / "components/raptor-rwd/raptor-lock.json"
    config_path = ROOT / "components/raptor-rwd/raptor.conf"
    checkout_lock = _regular(lock_path, "checkout rwd source lock", 64 * 1024)
    checkout_config = _regular(config_path, "checkout rwd configuration", 16 * 1024)
    if artifact_lock != checkout_lock:
        raise PersistentCandidateError("artifact rwd source lock does not match the checkout")
    if artifact_config != checkout_config:
        raise PersistentCandidateError("artifact rwd configuration does not match the checkout")

    try:
        lock = json.loads(checkout_lock.decode("utf-8"))
        build_closure = lock["build_closure"]
        sources = lock["sources"]
        excluded_sources = lock["excluded_sources"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PersistentCandidateError("checkout rwd source lock is invalid") from exc
    if (
        lock.get("schema_version") != 1
        or not isinstance(build_closure, list)
        or not build_closure
        or not all(isinstance(name, str) for name in build_closure)
        or not isinstance(sources, dict)
        or not isinstance(excluded_sources, dict)
        or not all(
            isinstance(sources.get(name), str) and len(sources[name]) == 40
            for name in build_closure
        )
    ):
        raise PersistentCandidateError("checkout rwd source lock is invalid")

    patch_root = ROOT / "patches/raptor"
    patches: list[dict[str, str]] = []
    for name in RAPTOR_PATCHES:
        raw = _regular(patch_root / name, f"checkout rwd patch {name}", 256 * 1024)
        if not raw.startswith(b"diff --git ") or b"\nFrom: " in raw or b"\nSubject: " in raw:
            raise PersistentCandidateError("checkout rwd patch format is invalid")
        patches.append(
            {
                "path": f"patches/raptor/{name}",
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return {
        "config_sha256": hashlib.sha256(checkout_config).hexdigest(),
        "excluded_sources": sorted(excluded_sources),
        "lock_sha256": hashlib.sha256(checkout_lock).hexdigest(),
        "patches": patches,
        "sources": {name: sources[name] for name in build_closure},
    }


def _install_artifact(
    root: Path,
    normalized_tar: bytes,
    *,
    static_rwd_tls: bool = False,
) -> dict[str, str]:
    installed: dict[str, str] = {}
    library_root = _destination(root, "usr/lib")
    if not library_root.is_dir() or library_root.is_symlink():
        raise PersistentCandidateError("global library directory changed type")
    raptor_tls_root = library_root / "raptor"
    if raptor_tls_root.exists() and not raptor_tls_root.is_dir():
        raise PersistentCandidateError("rwd TLS library directory changed type")
    if not static_rwd_tls:
        raptor_tls_root.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(normalized_tar), mode="r:") as archive:
        members = {member.name: member for member in archive.getmembers()}
        regular = (set(INSTALL_REGULAR) | {"S95thingino-control"}) - set(
            BASE_OWNED_RUNTIME
        )
        symlinks = set(INSTALL_SYMLINKS)
        if static_rwd_tls:
            regular -= RAPTOR_TLS_REGULAR
            symlinks -= RAPTOR_TLS_SYMLINKS
        for name in sorted(regular):
            member = members.get(name)
            if member is None or not member.isfile():
                raise PersistentCandidateError(f"artifact lacks persistent member: {name}")
            source = archive.extractfile(member)
            if source is None:
                raise PersistentCandidateError(f"cannot read artifact member: {name}")
            payload = source.read()
            relative = "etc/init.d/S95thingino-control" if name == "S95thingino-control" else artifact_destination(name)
            destination = _destination(root, relative)
            if destination.is_dir():
                raise PersistentCandidateError(f"artifact destination changed type: {relative}")
            if destination.is_symlink():
                destination.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            destination.chmod(member.mode & 0o777)
            installed[relative] = hashlib.sha256(payload).hexdigest()
        for name in sorted(symlinks):
            member = members.get(name)
            if member is None or not member.issym() or "/" in member.linkname:
                raise PersistentCandidateError(f"artifact symlink is invalid: {name}")
            relative = artifact_destination(name)
            destination = _destination(root, relative)
            if destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink():
                    raise PersistentCandidateError(f"artifact destination changed type: {relative}")
                destination.unlink()
            destination.symlink_to(member.linkname)
            installed[relative] = f"symlink:{member.linkname}"
    return installed


def _base_owned_runtime_identities(root: Path) -> dict[str, str]:
    """Bind runtime files that the persistent Raptor delta must never replace."""

    identities: dict[str, str] = {}
    for relative in sorted(BASE_OWNED_RUNTIME.values()):
        payload = _regular(
            _destination(root, relative),
            f"accepted base runtime {relative}",
            MAX_BASE_BYTES,
        )
        identities[relative] = hashlib.sha256(payload).hexdigest()
    return identities


def _remove_reset_conflicts(root: Path) -> None:
    for relative in RESET_CONFLICTS:
        path = root / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise PersistentCandidateError(f"reset conflict changed type: {relative}")


def _update_embedded_media_provenance(
    root: Path,
    prudynt: bytes,
    init: bytes,
    *,
    static_rwd_tls: bool,
    source_provenance: dict[str, object],
) -> None:
    path = root / "etc/dlink-media-closure.private.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        prudynt_entry = document["prudynt"]
        init_entries = document["source_built_init"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PersistentCandidateError("embedded media provenance is invalid") from exc
    if not isinstance(prudynt_entry, dict) or not isinstance(init_entries, list):
        raise PersistentCandidateError("embedded media provenance shape changed")
    prudynt_entry.update(
        {
            "origin": "pinned-source-build-with-rss-publisher",
            "sha256": hashlib.sha256(prudynt).hexdigest(),
            "size": len(prudynt),
        }
    )
    matching = [
        entry
        for entry in init_entries
        if isinstance(entry, dict) and entry.get("destination") == "/etc/init.d/S31prudynt"
    ]
    if len(matching) != 1:
        raise PersistentCandidateError("embedded Prudynt init provenance is missing")
    matching[0].update(
        {
            "origin": "raptor-rwd-overlay",
            "sha256": hashlib.sha256(init).hexdigest(),
            "size": len(init),
        }
    )
    document["runtime"] = "source-built-prudynt-with-rss-publisher"
    source_provenance_sha256 = hashlib.sha256(
        json.dumps(source_provenance, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    document["raptor_rwd"] = {
        "consumer": "raptor-rwd",
        "media_owner": "prudynt-only",
        "service": "S96rwd",
        "streams": [0, 1],
        "tls": "static-rwd" if static_rwd_tls else "isolated-shared",
        "source_provenance_sha256": source_provenance_sha256,
    }
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _audit_persistent_root(
    root: Path,
    *,
    installed: dict[str, str],
    base_owned_runtime: dict[str, str],
    service: bytes,
    static_rwd_tls: bool = False,
) -> None:
    for relative in RESET_CONFLICTS:
        if (root / relative).exists() or (root / relative).is_symlink():
            raise PersistentCandidateError(f"reset input remains claimed by Thingino: {relative}")
    init = (root / "etc/init.d/S31prudynt").read_bytes()
    lines = [line.lstrip(b"\t") for line in init.splitlines(keepends=True)]
    if lines.count(PRUDYNT_RING_START) != 1 or lines.count(PRUDYNT_START) != 0:
        raise PersistentCandidateError("Prudynt ring publisher is not lifecycle-stable")
    if (root / "etc/init.d/S96rwd").read_bytes() != service:
        raise PersistentCandidateError("persistent rwd service changed")
    if b"LD_LIBRARY_PATH=/usr/lib/raptor:/usr/lib" not in service:
        raise PersistentCandidateError("persistent rwd TLS closure is not isolated")
    for relative, identity in installed.items():
        path = root / relative
        if identity.startswith("symlink:"):
            if not path.is_symlink() or os.readlink(path) != identity.split(":", 1)[1]:
                raise PersistentCandidateError(f"installed symlink changed: {relative}")
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != identity:
            raise PersistentCandidateError(f"installed artifact member changed: {relative}")
    if _base_owned_runtime_identities(root) != base_owned_runtime:
        raise PersistentCandidateError("persistent overlay changed base-owned runtime")
    prudynt = (root / "usr/bin/prudynt").read_bytes()
    rwd = (root / "usr/bin/rwd").read_bytes()
    for label, payload in (("prudynt", prudynt), ("rwd", rwd)):
        _validate_mips_elf(payload, label)
    if b"libimp.so" in rwd or b"raptor_hal" in rwd:
        raise PersistentCandidateError("rwd links another media or ISP owner")
    bundled_tls = (b"libmbedtls.so", b"libmbedx509.so", b"libmbedcrypto.so")
    if static_rwd_tls and any(name in rwd for name in bundled_tls):
        raise PersistentCandidateError("static rwd still depends on shared mbedTLS")
    if not static_rwd_tls and not all(name in rwd for name in bundled_tls):
        raise PersistentCandidateError("dynamic rwd lost its bundled mbedTLS closure")
    config = (root / "etc/raptor.conf").read_text(encoding="ascii")
    for required in (
        "video_only = true",
        "signaling_loopback = true",
        "max_clients = 1",
        "http_port = 8554",
        "udp_port = 8443",
    ):
        if config.count(required) != 1:
            raise PersistentCandidateError(f"rwd configuration changed: {required}")


def build_persistent_root(
    *,
    base_rootfs_path: Path,
    base_provenance_path: Path,
    artifact_path: Path,
    artifact_sha256: str,
    supervisor_path: Path,
    service_path: Path,
    output_dir: Path,
    mksquashfs: Path,
    unsquashfs: Path,
    static_rwd_tls: bool = False,
    split_mtd3: bool = False,
) -> dict[str, object]:
    if output_dir.exists():
        raise PersistentCandidateError("refusing to reuse a private output directory")
    base = _regular(base_rootfs_path, "base rootfs", MAX_BASE_BYTES)
    validate_squashfs(base)
    base_provenance_sha256 = _validate_provenance(base, base_provenance_path)
    normalized_tar, artifact_files = validate_artifact(
        artifact_path,
        expected_sha256=artifact_sha256,
        supervisor=supervisor_path,
    )
    source_provenance = _validate_component_provenance(normalized_tar)
    service = _regular(service_path, "persistent rwd service", 32 * 1024)
    if not service.startswith(b"#!/bin/sh\n"):
        raise PersistentCandidateError("persistent rwd service is not a shell script")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        root = temporary / "root"
        base_snapshot = temporary / "base.squashfs"
        base_snapshot.write_bytes(base)
        _run([str(unsquashfs), "-d", str(root), str(base_snapshot)], "base extraction")
        _remove_reset_conflicts(root)

        # Prove the accepted private root still satisfies every normal final-root
        # invariant after releasing the reset GPIO, before applying the rwd delta.
        sanitized = temporary / "sanitized-base.squashfs"
        sanitized_raw = _pack(tool=mksquashfs, root=root, output=sanitized)
        validate_final_root(
            sanitized_raw,
            unsquashfs=unsquashfs,
            temporary_parent=temporary,
        )

        base_owned_runtime = _base_owned_runtime_identities(root)
        installed = _install_artifact(root, normalized_tar, static_rwd_tls=static_rwd_tls)
        init_path = root / "etc/init.d/S31prudynt"
        patched_init = patch_prudynt_init(init_path.read_bytes())
        init_path.write_bytes(patched_init)
        init_path.chmod(0o755)
        service_target = root / "etc/init.d/S96rwd"
        service_target.write_bytes(service)
        service_target.chmod(0o755)
        _update_embedded_media_provenance(
            root,
            (root / "usr/bin/prudynt").read_bytes(),
            patched_init,
            static_rwd_tls=static_rwd_tls,
            source_provenance=source_provenance,
        )
        _audit_persistent_root(
            root,
            installed=installed,
            base_owned_runtime=base_owned_runtime,
            service=service,
            static_rwd_tls=static_rwd_tls,
        )

        output = temporary / "system.private.squashfs"
        final_limit = SYSTEM_FLASH_SPAN if split_mtd3 else TARGET.partition(3).size - FOOTER_SIZE
        raw = _pack(
            tool=mksquashfs,
            root=root,
            output=output,
            block_size=1048576 if split_mtd3 else 262144,
            exportable=not split_mtd3,
            pack_tailends=split_mtd3,
            partition_limit=final_limit,
        )
        if len(raw) > final_limit:
            raise PersistentCandidateError("persistent WebRTC rootfs exceeds its system region")

        try:
            base_document = json.loads(base_provenance_path.read_text(encoding="utf-8"))
            policies = dict(base_document["policies"])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise PersistentCandidateError("base provenance policies are invalid") from exc
        policies.update(
            {
                "media_runtime": "source-built-prudynt-with-rss-publisher",
                "media_start": "automatic-S31prudynt-plus-S96rwd",
                "overlay_base_runtime": "preserved-from-accepted-base",
                "output_size": len(raw),
                "reset_gpio_owner": "recovery-supervisor-only",
                "webrtc": "raptor-rwd-video-only-loopback-signaling",
                "persistent_layout": "mtd3-split-v1" if split_mtd3 else "whole-mtd3",
                "webrtc_tls_library_scope": "static-rwd" if static_rwd_tls else "/usr/lib/raptor",
                "webrtc_max_clients": 1,
                "webrtc_streams": [0, 1],
            }
        )
        manifest = {
            "schema_version": 1,
            "status": "private final-root input; not an install authorization",
            "source_sha256": hashlib.sha256(base).hexdigest(),
            "system": {
                "filename": "system.private.squashfs",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            },
            "policies": policies,
            "raptor_rwd": {
                "artifact_sha256": artifact_sha256,
                "artifact_files": artifact_files,
                "base_owned_runtime": base_owned_runtime,
                "base_provenance_sha256": base_provenance_sha256,
                "media_owner": "prudynt-only",
                "rwd_service_sha256": hashlib.sha256(service).hexdigest(),
                "source_provenance": source_provenance,
            },
        }
        provenance = temporary / "final-root.private.json"
        provenance.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        shutil.rmtree(root)
        base_snapshot.unlink()
        sanitized.unlink()
        os.chmod(temporary, 0o700)
        output.chmod(0o600)
        provenance.chmod(0o600)
        os.replace(temporary, output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {**manifest, "output_dir": str(output_dir)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-rootfs", type=Path, required=True)
    parser.add_argument("--base-provenance", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--supervisor", type=Path, required=True)
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mksquashfs", type=Path, required=True)
    parser.add_argument("--unsquashfs", type=Path, required=True)
    parser.add_argument("--static-rwd-tls", action="store_true")
    parser.add_argument("--split-mtd3", action="store_true")
    arguments = parser.parse_args()
    try:
        result = build_persistent_root(
            base_rootfs_path=arguments.base_rootfs,
            base_provenance_path=arguments.base_provenance,
            artifact_path=arguments.artifact,
            artifact_sha256=arguments.artifact_sha256,
            supervisor_path=arguments.supervisor,
            service_path=arguments.service,
            output_dir=arguments.output_dir,
            mksquashfs=arguments.mksquashfs,
            unsquashfs=arguments.unsquashfs,
            static_rwd_tls=arguments.static_rwd_tls,
            split_mtd3=arguments.split_mtd3,
        )
    except (PersistentCandidateError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
