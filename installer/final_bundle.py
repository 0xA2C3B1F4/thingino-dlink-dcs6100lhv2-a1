"""Signed final Thingino bundle using the fixed physical-mtd3 split."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import (
    ArtifactError,
    validate_squashfs,
    validate_uimage_command_line,
)
from .install_policy import universal_physical_write_policy
from .layout import ERASE_BLOCK_SIZE, TARGET
from .fake_mtd import FinalBundleImages
from .mtd3_split import (
    DATA_FLASH_SPAN,
    SYSTEM_FLASH_SPAN,
    FinalLayout,
    Mtd3SplitError,
    derive_final_layout,
    final_kernel_command_line,
)
from .sd_package import atomic_write


MAX_FINAL_BUNDLE_SIZE = (
    TARGET.partition(1).size
    + TARGET.partition(2).size
    + TARGET.partition(3).size
    + 1024 * 1024
)
EXPECTED_MEMBERS = {
    "manifest.json",
    "manifest.ed25519",
    "images/kernel.uimage",
    "images/bootstrap.squashfs",
    "images/system.squashfs",
    "images/data.jffs2",
    "metadata/data.empty",
    "metadata/linux.config",
}


class BundleError(ValueError):
    """A final bundle or signing input violates the fixed contract."""


@dataclass(frozen=True, slots=True)
class ValidatedFinalBundle:
    raw: bytes
    manifest: dict[str, object]
    members: dict[str, bytes]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def validate_final_kernel_config(raw: bytes, expected_command_line: str) -> None:
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BundleError("effective Linux config is not ASCII") from exc
    values: dict[str, str] = {}
    for line in source.splitlines():
        if not line.startswith("CONFIG_") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            raise BundleError(f"duplicate effective Linux config key: {key}")
        values[key] = value
    required = {
        "CONFIG_CMDLINE_BOOL": "y",
        "CONFIG_CMDLINE_OVERRIDE": "y",
        "CONFIG_CMDLINE": json.dumps(expected_command_line),
        "CONFIG_MTD": "y",
        "CONFIG_MTD_CMDLINE_PARTS": "y",
        "CONFIG_MTD_BLOCK": "y",
        "CONFIG_MTD_JZ_SFC": "y",
        "CONFIG_MTD_JZ_SFC_NOR": "y",
        "CONFIG_SQUASHFS": "y",
        "CONFIG_JFFS2_FS": "y",
        "CONFIG_OVERLAYFS_FS": "y",
    }
    for key, value in required.items():
        if values.get(key) != value:
            raise BundleError(f"effective Linux config does not enforce {key}={value}")


def render_final_kernel_fragment(system_rootfs_size: int) -> bytes:
    try:
        command_line = final_kernel_command_line(
            derive_final_layout(system_rootfs_size)
        )
    except Mtd3SplitError as exc:
        raise BundleError(str(exc)) from exc
    fragment = "\n".join(
        (
            "# Generated from the validated final system SquashFS size.",
            "CONFIG_CMDLINE_BOOL=y",
            f"CONFIG_CMDLINE={json.dumps(command_line)}",
            "CONFIG_CMDLINE_OVERRIDE=y",
            "CONFIG_MTD=y",
            "CONFIG_MTD_CMDLINE_PARTS=y",
            "CONFIG_MTD_BLOCK=y",
            "CONFIG_MTD_JZ_SFC=y",
            "CONFIG_MTD_JZ_SFC_NOR=y",
            "CONFIG_SQUASHFS=y",
            # The system image uses 1 MiB blocks. Linux 3.10 allocates every
            # fragment-cache buffer at mount time, so three entries pin 3 MiB.
            # Keep one supported entry and leave the saved 2 MiB for page cache.
            "CONFIG_SQUASHFS_EMBEDDED=y",
            "CONFIG_SQUASHFS_FRAGMENT_CACHE_SIZE=1",
            "CONFIG_JFFS2_FS=y",
            "CONFIG_OVERLAYFS_FS=y",
            "",
        )
    ).encode("ascii")
    validate_final_kernel_config(fragment, command_line)
    return fragment


def _regular_private_key(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise BundleError("signing key is not a regular file")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise BundleError("signing key permissions must exclude group and other access")


def _openssl(arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["openssl", *arguments],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise BundleError("OpenSSL is required for Ed25519 bundle signatures") from exc
    if result.returncode != 0:
        raise BundleError("OpenSSL rejected the Ed25519 signing or verification input")
    return result.stdout


def _public_key_id_from_private(private_key: Path) -> str:
    public_der = _openssl(
        ["pkey", "-in", str(private_key), "-pubout", "-outform", "DER"]
    )
    return hashlib.sha256(public_der).hexdigest()


def _public_key_id(public_key: Path) -> str:
    if public_key.is_symlink() or not public_key.is_file():
        raise BundleError("verification key is not a regular file")
    public_der = _openssl(
        ["pkey", "-pubin", "-in", str(public_key), "-outform", "DER"]
    )
    return hashlib.sha256(public_der).hexdigest()


def ensure_ed25519_keypair(
    private_key: Path,
    public_key: Path | None = None,
) -> dict[str, object]:
    """Create or validate one stable private Ed25519 signer and public key."""

    private_key = private_key.expanduser()
    if not private_key.is_absolute():
        private_key = Path.cwd() / private_key
    private_key = private_key.resolve(strict=False)
    public_key = (
        public_key.expanduser().resolve(strict=False)
        if public_key is not None
        else private_key.with_suffix(".pub")
    )
    if private_key == public_key:
        raise BundleError("signing private and public key paths must differ")
    parent = private_key.parent
    if parent.exists():
        if parent.is_symlink() or not parent.is_dir() or parent.stat().st_mode & 0o077:
            raise BundleError("signing key directory must be private mode 0700")
    else:
        parent.mkdir(parents=True, mode=0o700)
        parent.chmod(0o700)
    if public_key.parent != parent:
        raise BundleError("signing public key must share the private key directory")

    created = False
    if private_key.exists() or private_key.is_symlink():
        _regular_private_key(private_key)
    else:
        if public_key.exists() or public_key.is_symlink():
            raise BundleError("signing public key exists without its private key")
        with tempfile.TemporaryDirectory(prefix=".keygen-", dir=parent) as name:
            temporary = Path(name) / "private.pem"
            _openssl(
                ["genpkey", "-algorithm", "ED25519", "-out", str(temporary)]
            )
            temporary.chmod(0o600)
            _regular_private_key(temporary)
            os.replace(temporary, private_key)
        created = True
    private_key.chmod(0o600)
    public_pem = _openssl(
        ["pkey", "-in", str(private_key), "-pubout"]
    )
    if public_key.exists() or public_key.is_symlink():
        if public_key.is_symlink() or not public_key.is_file():
            raise BundleError("signing public key is not a regular file")
        if public_key.read_bytes() != public_pem:
            raise BundleError("signing public key differs from its private key")
    else:
        atomic_write(public_key, public_pem, mode=0o644)
    key_id = _public_key_id_from_private(private_key)
    if _public_key_id(public_key) != key_id:
        raise BundleError("signing key pair identity differs")
    return {
        "created": created,
        "key_id": key_id,
        "private_key": str(private_key),
        "public_key": str(public_key),
    }


def _sign(manifest: bytes, private_key: Path) -> bytes:
    _regular_private_key(private_key)
    with tempfile.TemporaryDirectory(prefix="dcs6100-sign-") as directory_name:
        directory = Path(directory_name)
        manifest_path = directory / "manifest.json"
        signature_path = directory / "manifest.ed25519"
        manifest_path.write_bytes(manifest)
        _openssl(
            [
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(private_key),
                "-in",
                str(manifest_path),
                "-out",
                str(signature_path),
            ]
        )
        return signature_path.read_bytes()


def _verify(manifest: bytes, signature: bytes, public_key: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="dcs6100-verify-") as directory_name:
        directory = Path(directory_name)
        manifest_path = directory / "manifest.json"
        signature_path = directory / "manifest.ed25519"
        manifest_path.write_bytes(manifest)
        signature_path.write_bytes(signature)
        _openssl(
            [
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(public_key),
                "-in",
                str(manifest_path),
                "-sigfile",
                str(signature_path),
            ]
        )


def _component(path: str, payload: bytes, *, offset: int, span: int) -> dict[str, object]:
    return {
        "erase_span": span,
        "offset": offset,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _zip_member(path: str, payload: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100400 << 16
    return info, payload


def build_final_bundle(
    *,
    kernel: bytes,
    bootstrap_rootfs: bytes,
    system_rootfs: bytes,
    data_jffs2: bytes,
    linux_config: bytes,
    signing_key: Path,
    artifact_scope: str = "device-personalized",
) -> bytes:
    if artifact_scope not in {"device-personalized", "model-universal"}:
        raise BundleError("final bundle artifact scope is invalid")
    if artifact_scope == "model-universal" and data_jffs2:
        raise BundleError("model-universal bundle must leave data provisioning empty")
    try:
        layout = derive_final_layout(len(system_rootfs))
    except Mtd3SplitError as exc:
        raise BundleError(str(exc)) from exc
    command_line = final_kernel_command_line(layout)
    try:
        kernel_info = validate_uimage_command_line(
            kernel,
            command_line,
            partition_limit=TARGET.partition(1).size,
            expected_entry=None,
        )
        bootstrap_used = validate_squashfs(
            bootstrap_rootfs, partition_limit=TARGET.partition(2).size
        )
        system_used = validate_squashfs(system_rootfs)
    except ArtifactError as exc:
        raise BundleError(str(exc)) from exc
    if data_jffs2 and (
        len(data_jffs2) != layout.data_span
        or data_jffs2[:2] != b"\x85\x19"
    ):
        raise BundleError(
            "data image is neither empty nor an exact-span little-endian JFFS2"
        )
    validate_final_kernel_config(linux_config, command_line)
    if len(data_jffs2) > layout.data_span:
        raise BundleError("JFFS2 data image exceeds the derived data region")

    components = [
        _component(
            "images/data.jffs2",
            data_jffs2,
            offset=layout.data_offset,
            span=layout.data_span,
        ),
        _component(
            "images/system.squashfs",
            system_rootfs,
            offset=layout.system_offset,
            span=layout.system_span,
        ),
        _component(
            "images/bootstrap.squashfs",
            bootstrap_rootfs,
            offset=TARGET.partition(2).offset,
            span=TARGET.partition(2).size,
        ),
        _component(
            "images/kernel.uimage",
            kernel,
            offset=TARGET.partition(1).offset,
            span=TARGET.partition(1).size,
        ),
    ]
    manifest_document = {
        "artifact_scope": artifact_scope,
        "activation": {
            "kind": "kernel-first-eraseblock",
            "offset": TARGET.partition(1).offset,
            "size": ERASE_BLOCK_SIZE,
            "written_last": True,
        },
        "components": components,
        "image_kind": "dcs6100-mtd3-split-v1",
        "image_metadata": {
            "bootstrap_squashfs_bytes_used": bootstrap_used,
            "kernel_entry": kernel_info.entry_point,
            "kernel_expanded_size": kernel_info.expanded_size,
            "system_squashfs_bytes_used": system_used,
        },
        "kernel_command_line": command_line,
        "kernel_config": {
            "path": "metadata/linux.config",
            "sha256": hashlib.sha256(linux_config).hexdigest(),
            "size": len(linux_config),
        },
        "data_initialization": {
            "empty_erased_region": not data_jffs2,
            "metadata_path": "metadata/data.empty",
        },
        "mtd3_layout": {
            "data": {
                "filesystem": "jffs2",
                "offset": layout.data_offset,
                "span": layout.data_span,
            },
            "parent_mtd": 3,
            "physical_offset": TARGET.partition(3).offset,
            "physical_size": TARGET.partition(3).size,
            "system": {
                "filesystem": "squashfs",
                "offset": layout.system_offset,
                "span": layout.system_span,
            },
        },
        "persistence_policy": {
            "corrupt_data": "recovery-no-autoformat",
            "factory_reset": "explicit-data-only",
            "overlay_mount": "/overlay",
            "preserve_data_on_update": True,
        },
        **(
            {"physical_write_policy": universal_physical_write_policy()}
            if artifact_scope == "model-universal"
            else {}
        ),
        "preserved_mtd": [0, 4, 5],
        "schema_version": 2,
        "signing_key_sha256": _public_key_id_from_private(signing_key),
        "target": {
            "hardware_revision": TARGET.hardware_revision,
            "model": TARGET.model,
            "nor_size": TARGET.nor_size,
        },
        "write_order": {
            "factory_reset": ["images/data.jffs2"],
            "firmware_update": [
                "images/system.squashfs",
                "images/bootstrap.squashfs",
                "images/kernel.uimage:tail",
                "images/kernel.uimage:activation",
            ],
            "initial_install": [
                "images/data.jffs2",
                "images/system.squashfs",
                "images/bootstrap.squashfs",
                "images/kernel.uimage:tail",
                "images/kernel.uimage:activation",
            ],
        },
    }
    manifest = (
        json.dumps(manifest_document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    signature = _sign(manifest, signing_key)
    members = {
        "images/bootstrap.squashfs": bootstrap_rootfs,
        "images/data.jffs2": data_jffs2,
        "images/kernel.uimage": kernel,
        "images/system.squashfs": system_rootfs,
        "metadata/linux.config": linux_config,
        "metadata/data.empty": (
            b"The data region is intentionally left erased for first-boot JFFS2 initialization.\n"
            if not data_jffs2
            else b"The data region contains the validated JFFS2 image in images/data.jffs2.\n"
        ),
        "manifest.ed25519": signature,
        "manifest.json": manifest,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        for path in sorted(members):
            info, payload = _zip_member(path, members[path])
            archive.writestr(info, payload)
    raw = output.getvalue()
    if len(raw) > MAX_FINAL_BUNDLE_SIZE:
        raise BundleError("final bundle exceeds its stage-1 RAM upload cap")
    return raw


def validate_final_bundle(raw: bytes, *, public_key: Path) -> ValidatedFinalBundle:
    if not isinstance(raw, bytes):
        raise TypeError("bundle input must be an immutable bytes snapshot")
    if len(raw) > MAX_FINAL_BUNDLE_SIZE:
        raise BundleError("final bundle exceeds its stage-1 RAM upload cap")
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != EXPECTED_MEMBERS:
                raise BundleError("bundle member allowlist or uniqueness check failed")
            members: dict[str, bytes] = {}
            for info in infos:
                if info.compress_type != zipfile.ZIP_STORED:
                    raise BundleError("bundle members must use stored ZIP encoding")
                if info.file_size > MAX_FINAL_BUNDLE_SIZE:
                    raise BundleError("bundle member exceeds its size cap")
                members[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise BundleError("invalid final bundle ZIP") from exc

    manifest_bytes = members["manifest.json"]
    _verify(manifest_bytes, members["manifest.ed25519"], public_key)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("bundle manifest is not canonical UTF-8 JSON") from exc
    canonical = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if canonical != manifest_bytes:
        raise BundleError("bundle manifest is not in canonical form")
    if manifest.get("schema_version") != 2:
        raise BundleError("unsupported final-bundle schema")
    if manifest.get("image_kind") != "dcs6100-mtd3-split-v1":
        raise BundleError("final bundle image kind changed")
    if manifest.get("artifact_scope") not in {
        "device-personalized",
        "model-universal",
    }:
        raise BundleError("final bundle artifact scope changed")
    if manifest.get("target") != {
        "hardware_revision": TARGET.hardware_revision,
        "model": TARGET.model,
        "nor_size": TARGET.nor_size,
    }:
        raise BundleError("final bundle targets the wrong device")
    if manifest.get("preserved_mtd") != [0, 4, 5]:
        raise BundleError("final bundle does not preserve mtd0/mtd4/mtd5")
    expected_physical_policy = (
        universal_physical_write_policy()
        if manifest.get("artifact_scope") == "model-universal"
        else None
    )
    if manifest.get("physical_write_policy") != expected_physical_policy:
        raise BundleError("final bundle physical write policy changed")
    if manifest.get("signing_key_sha256") != _public_key_id(public_key):
        raise BundleError("bundle signer identity does not match the trusted key")

    activation = manifest.get("activation")
    if activation != {
        "kind": "kernel-first-eraseblock",
        "offset": TARGET.partition(1).offset,
        "size": ERASE_BLOCK_SIZE,
        "written_last": True,
    }:
        raise BundleError("final bundle activation contract changed")
    expected_write_order = {
        "factory_reset": ["images/data.jffs2"],
        "firmware_update": [
            "images/system.squashfs",
            "images/bootstrap.squashfs",
            "images/kernel.uimage:tail",
            "images/kernel.uimage:activation",
        ],
        "initial_install": [
            "images/data.jffs2",
            "images/system.squashfs",
            "images/bootstrap.squashfs",
            "images/kernel.uimage:tail",
            "images/kernel.uimage:activation",
        ],
    }
    if manifest.get("write_order") != expected_write_order:
        raise BundleError("final bundle write order changed")

    components = manifest.get("components")
    if not isinstance(components, list) or len(components) != 4:
        raise BundleError("final bundle has the wrong component count")
    by_path: dict[str, dict[str, object]] = {}
    for component in components:
        if not isinstance(component, dict) or not isinstance(component.get("path"), str):
            raise BundleError("malformed final-bundle component")
        path = str(component["path"])
        if path in by_path or path not in members:
            raise BundleError("duplicate or unknown final-bundle component")
        payload = members[path]
        if component.get("size") != len(payload):
            raise BundleError("component size does not match manifest")
        if component.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise BundleError("component digest does not match manifest")
        by_path[path] = component
    if set(by_path) != EXPECTED_MEMBERS - {
        "manifest.json",
        "manifest.ed25519",
        "metadata/data.empty",
        "metadata/linux.config",
    }:
        raise BundleError("manifest component allowlist is incomplete")

    try:
        derived = derive_final_layout(len(members["images/system.squashfs"]))
    except Mtd3SplitError as exc:
        raise BundleError(str(exc)) from exc
    if manifest.get("mtd3_layout") != {
        "data": {
            "filesystem": "jffs2",
            "offset": derived.data_offset,
            "span": derived.data_span,
        },
        "parent_mtd": 3,
        "physical_offset": TARGET.partition(3).offset,
        "physical_size": TARGET.partition(3).size,
        "system": {
            "filesystem": "squashfs",
            "offset": derived.system_offset,
            "span": derived.system_span,
        },
    }:
        raise BundleError("manifest mtd3 layout changed")
    if manifest.get("persistence_policy") != {
        "corrupt_data": "recovery-no-autoformat",
        "factory_reset": "explicit-data-only",
        "overlay_mount": "/overlay",
        "preserve_data_on_update": True,
    }:
        raise BundleError("manifest persistence policy changed")
    expected_command_line = final_kernel_command_line(derived)
    if manifest.get("kernel_command_line") != expected_command_line:
        raise BundleError("manifest final kernel command line does not match layout")
    kernel_config = manifest.get("kernel_config")
    if kernel_config != {
        "path": "metadata/linux.config",
        "sha256": hashlib.sha256(members["metadata/linux.config"]).hexdigest(),
        "size": len(members["metadata/linux.config"]),
    }:
        raise BundleError("effective Linux config identity does not match manifest")
    validate_final_kernel_config(
        members["metadata/linux.config"], expected_command_line
    )
    try:
        validate_uimage_command_line(
            members["images/kernel.uimage"],
            expected_command_line,
            partition_limit=TARGET.partition(1).size,
            expected_entry=None,
        )
        validate_squashfs(
            members["images/bootstrap.squashfs"],
            partition_limit=TARGET.partition(2).size,
        )
        validate_squashfs(members["images/system.squashfs"])
    except ArtifactError as exc:
        raise BundleError(str(exc)) from exc
    data_payload = members["images/data.jffs2"]
    data_initialization = manifest.get("data_initialization")
    expected_data_metadata = (
        b"The data region is intentionally left erased for first-boot JFFS2 initialization.\n"
        if not data_payload
        else b"The data region contains the validated JFFS2 image in images/data.jffs2.\n"
    )
    if data_initialization != {
        "empty_erased_region": not data_payload,
        "metadata_path": "metadata/data.empty",
    } or members["metadata/data.empty"] != expected_data_metadata:
        raise BundleError("bundle data initialization contract changed")
    if data_payload and (
        len(data_payload) != derived.data_span
        or data_payload[:2] != b"\x85\x19"
    ):
        raise BundleError("bundle data image is not exact-span JFFS2")
    if manifest["artifact_scope"] == "model-universal" and data_payload:
        raise BundleError("model-universal bundle contains per-camera data")
    expected_ranges = {
        "images/kernel.uimage": (
            TARGET.partition(1).offset,
            TARGET.partition(1).size,
        ),
        "images/bootstrap.squashfs": (
            TARGET.partition(2).offset,
            TARGET.partition(2).size,
        ),
        "images/system.squashfs": (derived.system_offset, derived.system_span),
        "images/data.jffs2": (derived.data_offset, derived.data_span),
    }
    for path, (offset, span) in expected_ranges.items():
        if by_path[path].get("offset") != offset or by_path[path].get("erase_span") != span:
            raise BundleError("component physical range does not match derived layout")
        if len(members[path]) > span:
            raise BundleError("component exceeds its derived physical range")
    return ValidatedFinalBundle(raw=raw, manifest=manifest, members=members)


def build_universal_final_bundle(
    *,
    kernel: bytes,
    bootstrap_rootfs: bytes,
    system_rootfs: bytes,
    linux_config: bytes,
    signing_key: Path,
) -> bytes:
    """Build one secret-free model artifact; provisioning is always separate."""

    return build_final_bundle(
        kernel=kernel,
        bootstrap_rootfs=bootstrap_rootfs,
        system_rootfs=system_rootfs,
        data_jffs2=b"",
        linux_config=linux_config,
        signing_key=signing_key,
        artifact_scope="model-universal",
    )


def validate_universal_final_bundle(
    raw: bytes, *, public_key: Path
) -> ValidatedFinalBundle:
    bundle = validate_final_bundle(raw, public_key=public_key)
    if bundle.manifest.get("artifact_scope") != "model-universal":
        raise BundleError("final bundle is not model-universal")
    if bundle.members["images/data.jffs2"]:
        raise BundleError("model-universal bundle contains provisioning data")
    return bundle


def final_bundle_images(bundle: ValidatedFinalBundle) -> FinalBundleImages:
    system = bundle.members["images/system.squashfs"]
    layout = derive_final_layout(len(system))
    images = FinalBundleImages(
        system_offset=layout.system_offset,
        system_erase_span=layout.system_span,
        system=system,
        data_offset=layout.data_offset,
        data_erase_span=layout.data_span,
        data=bundle.members["images/data.jffs2"],
        rootfs=bundle.members["images/bootstrap.squashfs"],
        kernel=bundle.members["images/kernel.uimage"],
    )
    images.validate()
    return images
