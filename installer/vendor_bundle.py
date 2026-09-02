"""Validate the exact private vendor-library closure collected from one camera."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .layout import TARGET


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "profiles/dlink-dcs6100lhv2-a1/vendor-closure.json"
MANIFEST_NAME = "vendor-bundle.private.json"
FILES_DIRECTORY = "files"


class VendorBundleError(ValueError):
    """The private camera-local vendor bundle violates its public contract."""


@dataclass(frozen=True, slots=True)
class ElfMetadata:
    flags: int
    needed: tuple[str, ...]
    soname: str | None


@dataclass(frozen=True, slots=True)
class VendorArtifact:
    name: str
    destination: str | None
    source_path: str
    raw: bytes
    sha256: str
    elf: ElfMetadata
    rootfs: bool


@dataclass(frozen=True, slots=True)
class VendorBundle:
    artifacts: tuple[VendorArtifact, ...]
    bundle_sha256: str
    firmware_version: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class VendorBuildSite:
    files: tuple[str, ...]
    source_bundle_sha256: str
    source_manifest_sha256: str


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise VendorBundleError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise VendorBundleError(f"{label} must be a JSON array")
    return value


def _exact_keys(document: dict[str, object], expected: set[str], label: str) -> None:
    if set(document) != expected:
        raise VendorBundleError(f"{label} has unexpected or missing fields")


def _read_regular(path: Path, label: str, *, exact_size: int | None, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise VendorBundleError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise VendorBundleError(f"{label} is not a bounded regular file")
        if exact_size is not None and before.st_size != exact_size:
            raise VendorBundleError(f"{label} has the wrong size")
        raw = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (before.st_dev, before.st_ino, before.st_size)
    if identity != (after.st_dev, after.st_ino, after.st_size):
        raise VendorBundleError(f"{label} changed while being read")
    if len(raw) != before.st_size:
        raise VendorBundleError(f"{label} read was incomplete")
    return raw


def _cstring(table: bytes, offset: int, label: str) -> str:
    if offset < 0 or offset >= len(table):
        raise VendorBundleError(f"{label} has an invalid dynamic string offset")
    end = table.find(b"\0", offset)
    if end < 0:
        raise VendorBundleError(f"{label} has an unterminated dynamic string")
    try:
        return table[offset:end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise VendorBundleError(f"{label} has a non-ASCII dynamic string") from exc


def parse_elf32_mips(raw: bytes, label: str, *, expected_type: int | None = None) -> ElfMetadata:
    """Parse only the ELF fields needed by the fail-closed bundle gate."""

    if len(raw) < 52 or raw[:4] != b"\x7fELF":
        raise VendorBundleError(f"{label} is not ELF")
    identity = raw[:16]
    if identity[4:7] != b"\x01\x01\x01":
        raise VendorBundleError(f"{label} is not ELF32 little-endian version 1")
    try:
        (
            _identity,
            elf_type,
            machine,
            version,
            _entry,
            _program_offset,
            section_offset,
            flags,
            header_size,
            _program_entry_size,
            _program_count,
            section_entry_size,
            section_count,
            _section_names,
        ) = struct.unpack_from("<16sHHIIIIIHHHHHH", raw)
    except struct.error as exc:
        raise VendorBundleError(f"{label} has a truncated ELF header") from exc
    if machine != 8 or version != 1 or header_size != 52:
        raise VendorBundleError(f"{label} is not the expected MIPS ELF ABI")
    if expected_type is not None and elf_type != expected_type:
        raise VendorBundleError(f"{label} has the wrong ELF object type")
    if section_entry_size != 40 or not section_count:
        raise VendorBundleError(f"{label} lacks the required ELF sections")
    sections_end = section_offset + section_entry_size * section_count
    if section_offset < header_size or sections_end > len(raw):
        raise VendorBundleError(f"{label} has an invalid ELF section table")

    sections: list[tuple[int, ...]] = []
    for index in range(section_count):
        offset = section_offset + index * section_entry_size
        sections.append(struct.unpack_from("<IIIIIIIIII", raw, offset))
    dynamic_sections = [section for section in sections if section[1] == 6]
    if len(dynamic_sections) != 1:
        raise VendorBundleError(f"{label} must have exactly one dynamic section")
    dynamic = dynamic_sections[0]
    dynamic_offset, dynamic_size, string_index, entry_size = (
        dynamic[4],
        dynamic[5],
        dynamic[6],
        dynamic[9],
    )
    if entry_size != 8 or dynamic_size % entry_size or string_index >= len(sections):
        raise VendorBundleError(f"{label} has an invalid dynamic section")
    string_section = sections[string_index]
    if string_section[1] != 3:
        raise VendorBundleError(f"{label} dynamic section does not link a string table")
    string_offset, string_size = string_section[4], string_section[5]
    if dynamic_offset + dynamic_size > len(raw) or string_offset + string_size > len(raw):
        raise VendorBundleError(f"{label} has out-of-range dynamic data")
    strings = raw[string_offset:string_offset + string_size]

    needed: list[str] = []
    sonames: list[str] = []
    saw_null = False
    for offset in range(dynamic_offset, dynamic_offset + dynamic_size, entry_size):
        tag, value = struct.unpack_from("<iI", raw, offset)
        if tag == 0:
            saw_null = True
            break
        if tag == 1:
            needed.append(_cstring(strings, value, label))
        elif tag == 14:
            sonames.append(_cstring(strings, value, label))
    if not saw_null or len(sonames) > 1:
        raise VendorBundleError(f"{label} has an invalid dynamic terminator or SONAME")
    return ElfMetadata(flags=flags, needed=tuple(needed), soname=sonames[0] if sonames else None)


def _partition_document() -> list[dict[str, int | str]]:
    return [
        {
            "mtd": partition.mtd,
            "name": partition.name,
            "offset": partition.offset,
            "size": partition.size,
        }
        for partition in TARGET.partitions
    ]


def _load_json(raw: bytes, label: str) -> dict[str, object]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VendorBundleError(f"{label} is not valid UTF-8 JSON") from exc
    return _object(document, label)


def _load_catalog(path: Path) -> dict[str, object]:
    raw = _read_regular(path, "public vendor catalog", exact_size=None, limit=64 * 1024)
    catalog = _load_json(raw, "public vendor catalog")
    _exact_keys(catalog, {"schema_version", "target", "source", "files"}, "public vendor catalog")
    if catalog["schema_version"] != 1:
        raise VendorBundleError("unsupported public vendor catalog schema")
    target = _object(catalog["target"], "catalog target")
    expected_target = {
        "erase_block_size": TARGET.erase_block_size,
        "flash_size": TARGET.nor_size,
        "hardware_revision": TARGET.hardware_revision,
        "model": TARGET.model,
        "partitions": _partition_document(),
    }
    if target != expected_target:
        raise VendorBundleError("public vendor catalog target differs from the flash contract")
    return catalog


def _unescape_mountinfo(value: str) -> str:
    for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(encoded, decoded)
    return value


def require_read_only_mtd3_mount(mount: Path, *, mountinfo_path: Path) -> None:
    """Require one exact read-only JFFS2 mount sourced from physical mtd3."""

    if mount.is_symlink() or not mount.is_dir():
        raise VendorBundleError("stock mtd3 mount is not a regular directory")
    mount_text = _read_regular(
        mountinfo_path,
        "mount information",
        exact_size=None,
        limit=1024 * 1024,
    ).decode("utf-8", "strict")
    expected = str(mount.resolve())
    matches: list[tuple[set[str], str, str]] = []
    for line in mount_text.splitlines():
        fields = line.split()
        if "-" not in fields or len(fields) < 10:
            continue
        separator = fields.index("-")
        if separator < 6 or len(fields) <= separator + 2:
            continue
        if _unescape_mountinfo(fields[4]) != expected:
            continue
        matches.append((set(fields[5].split(",")), fields[separator + 1], fields[separator + 2]))
    if len(matches) != 1:
        raise VendorBundleError("stock mtd3 is not the exact mounted filesystem")
    options, filesystem, source = matches[0]
    if "ro" not in options or "rw" in options:
        raise VendorBundleError("stock mtd3 is not mounted read-only")
    if filesystem != "jffs2" or source not in {"/dev/mtd3", "/dev/mtdblock3"}:
        raise VendorBundleError("stock mtd3 mount has the wrong source or filesystem")


def _safe_source(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != relative:
        raise VendorBundleError("public vendor catalog has an unsafe source path")
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise VendorBundleError(f"vendor source path contains a symlink: {relative}")
    return current


def extract_vendor_bundle(
    *,
    mtd3_mount: Path,
    mountinfo_path: Path,
    output_dir: Path,
    catalog_path: Path = CATALOG_PATH,
) -> VendorBundle:
    """Copy the allowlist from an already mounted read-only stock mtd3."""

    require_read_only_mtd3_mount(mtd3_mount, mountinfo_path=mountinfo_path)
    if output_dir.exists() or output_dir.is_symlink():
        raise VendorBundleError("refusing to reuse a vendor bundle output directory")
    catalog = _load_catalog(catalog_path)
    source = _object(catalog["source"], "catalog source")
    entries = [_object(value, "catalog file") for value in _array(catalog["files"], "catalog files")]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        work.chmod(0o700)
        destination = work / FILES_DIRECTORY
        destination.mkdir(mode=0o700)
        manifest_files: list[dict[str, object]] = []
        for entry in entries:
            name = entry.get("name")
            relative = entry.get("source_path")
            size = entry.get("size")
            digest = entry.get("sha256")
            required = entry.get("required")
            if not isinstance(name, str) or not isinstance(relative, str):
                raise VendorBundleError("public vendor catalog has an invalid file path")
            if not isinstance(size, int) or not isinstance(digest, str) or required not in (True, False):
                raise VendorBundleError("public vendor catalog has invalid file identity")
            source_path = _safe_source(mtd3_mount, relative)
            if not source_path.exists():
                if required:
                    raise VendorBundleError(f"required stock mtd3 file is missing: {relative}")
                continue
            raw = _read_regular(source_path, f"stock mtd3 file {relative}", exact_size=size, limit=size)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise VendorBundleError(f"stock mtd3 file hash mismatch: {relative}")
            output = destination / name
            output.write_bytes(raw)
            output.chmod(0o600)
            manifest_files.append(
                {"name": name, "sha256": digest, "size": size, "source_path": relative}
            )
        manifest = {
            "schema_version": 1,
            "target": catalog["target"],
            "source": {
                "firmware_version": source["firmware_version"],
                "mounted_read_only": True,
                "partition": source["partition"],
            },
            "files": manifest_files,
        }
        manifest_path = work / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)
        bundle = load_vendor_bundle(work, catalog_path=catalog_path)
        os.replace(work, output_dir)
        return bundle
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def load_vendor_bundle(directory: Path, *, catalog_path: Path = CATALOG_PATH) -> VendorBundle:
    """Snapshot and validate one private bundle without following links."""

    if directory.is_symlink() or not directory.is_dir():
        raise VendorBundleError("vendor bundle is not a private directory")
    if {entry.name for entry in directory.iterdir()} != {MANIFEST_NAME, FILES_DIRECTORY}:
        raise VendorBundleError("vendor bundle must contain exactly its manifest and files directory")
    files_directory = directory / FILES_DIRECTORY
    if files_directory.is_symlink() or not files_directory.is_dir():
        raise VendorBundleError("vendor bundle files entry is not a directory")

    catalog = _load_catalog(catalog_path)
    source = _object(catalog["source"], "catalog source")
    catalog_files = [_object(value, "catalog file") for value in _array(catalog["files"], "catalog files")]
    names = [value.get("name") for value in catalog_files]
    if not names or not all(isinstance(name, str) for name in names) or len(names) != len(set(names)):
        raise VendorBundleError("public vendor catalog file names are invalid")
    expected_names = set(names)
    required_names = {str(value["name"]) for value in catalog_files if value.get("required") is True}
    present_names = {entry.name for entry in files_directory.iterdir()}
    if not required_names.issubset(present_names) or not present_names.issubset(expected_names):
        raise VendorBundleError("vendor bundle files are incomplete or contain extra entries")

    manifest_raw = _read_regular(
        directory / MANIFEST_NAME,
        "private vendor manifest",
        exact_size=None,
        limit=64 * 1024,
    )
    manifest = _load_json(manifest_raw, "private vendor manifest")
    _exact_keys(manifest, {"schema_version", "target", "source", "files"}, "private vendor manifest")
    if manifest["schema_version"] != 1 or manifest["target"] != catalog["target"]:
        raise VendorBundleError("private vendor manifest targets the wrong camera or flash layout")
    manifest_source = _object(manifest["source"], "private vendor source")
    _exact_keys(manifest_source, {"firmware_version", "mounted_read_only", "partition"}, "private vendor source")
    if manifest_source.get("firmware_version") != source.get("firmware_version"):
        raise VendorBundleError("private vendor manifest has the wrong stock firmware version")
    if manifest_source.get("mounted_read_only") is not True:
        raise VendorBundleError("private vendor manifest does not attest a read-only mount")
    if manifest_source.get("partition") != source.get("partition"):
        raise VendorBundleError("private vendor manifest has the wrong source partition")

    manifest_files = [_object(value, "private vendor file") for value in _array(manifest["files"], "private vendor files")]
    by_name: dict[str, dict[str, object]] = {}
    for value in manifest_files:
        _exact_keys(value, {"name", "sha256", "size", "source_path"}, "private vendor file")
        name = value.get("name")
        if not isinstance(name, str) or name in by_name:
            raise VendorBundleError("private vendor manifest file names are invalid")
        by_name[name] = value
    if set(by_name) != present_names:
        raise VendorBundleError("private vendor manifest file closure is incomplete")

    artifacts: list[VendorArtifact] = []
    binding: list[dict[str, object]] = []
    for expected in catalog_files:
        _exact_keys(
            expected,
            {"destination", "elf", "name", "required", "rootfs", "sha256", "size", "source_path"},
            "catalog file",
        )
        name = expected["name"]
        assert isinstance(name, str)
        if name not in present_names:
            if expected["required"] is True:
                raise VendorBundleError(f"required vendor file is missing: {name}")
            continue
        declared = by_name[name]
        for field in ("sha256", "size", "source_path"):
            if declared.get(field) != expected.get(field):
                raise VendorBundleError(f"private vendor manifest does not match the catalog: {name}")
        size = expected["size"]
        digest = expected["sha256"]
        destination = expected["destination"]
        source_path = expected["source_path"]
        if not isinstance(size, int) or size <= 0 or not isinstance(digest, str):
            raise VendorBundleError("public vendor catalog has an invalid size or digest")
        rootfs = expected["rootfs"]
        if rootfs not in (True, False) or expected["required"] not in (True, False):
            raise VendorBundleError("public vendor catalog has an invalid disposition")
        if (rootfs and not isinstance(destination, str)) or (not rootfs and destination is not None):
            raise VendorBundleError("public vendor catalog has an invalid destination")
        if not isinstance(source_path, str):
            raise VendorBundleError("public vendor catalog has an invalid path")
        raw = _read_regular(files_directory / name, f"vendor file {name}", exact_size=size, limit=size)
        actual_digest = hashlib.sha256(raw).hexdigest()
        if actual_digest != digest:
            raise VendorBundleError(f"vendor file hash mismatch: {name}")
        metadata = parse_elf32_mips(raw, f"vendor file {name}", expected_type=3)
        elf = _object(expected["elf"], "catalog ELF metadata")
        _exact_keys(elf, {"flags", "needed", "soname"}, "catalog ELF metadata")
        try:
            expected_flags = int(str(elf["flags"]), 0)
        except ValueError as exc:
            raise VendorBundleError("catalog ELF flags are invalid") from exc
        needed = tuple(_array(elf["needed"], "catalog ELF dependencies"))
        if (
            metadata.flags != expected_flags
            or metadata.soname != elf["soname"]
            or metadata.needed != needed
        ):
            raise VendorBundleError(f"vendor ELF ABI or dependency mismatch: {name}")
        artifacts.append(
            VendorArtifact(
                name=name,
                destination=destination,
                source_path=source_path,
                raw=raw,
                sha256=actual_digest,
                elf=metadata,
                rootfs=rootfs,
            )
        )
        binding.append(
            {
                "destination": destination,
                "name": name,
                "rootfs": rootfs,
                "sha256": actual_digest,
                "size": size,
            }
        )

    canonical_binding = json.dumps(
        {
            "firmware_version": manifest_source["firmware_version"],
            "files": binding,
            "target": manifest["target"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return VendorBundle(
        artifacts=tuple(artifacts),
        bundle_sha256=hashlib.sha256(canonical_binding).hexdigest(),
        firmware_version=str(manifest_source["firmware_version"]),
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
    )


def prepare_vendor_build_site(
    *,
    vendor_bundle_dir: Path,
    output_dir: Path,
    catalog_path: Path = CATALOG_PATH,
) -> VendorBuildSite:
    """Materialize only the three validated link/runtime libraries for Buildroot."""

    bundle = load_vendor_bundle(vendor_bundle_dir, catalog_path=catalog_path)
    rootfs_artifacts = tuple(
        artifact
        for artifact in bundle.artifacts
        if artifact.name in {"libimp.so", "libalog.so", "libsysutils.so"}
    )
    expected = ("libimp.so", "libalog.so", "libsysutils.so")
    if tuple(artifact.name for artifact in rootfs_artifacts) != expected:
        raise VendorBundleError("vendor build-site closure is incomplete or reordered")
    if output_dir.exists() or output_dir.is_symlink():
        raise VendorBundleError("refusing to reuse a vendor build-site output directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        work.chmod(0o700)
        files_dir = work / FILES_DIRECTORY
        files_dir.mkdir(mode=0o700)
        files: list[dict[str, object]] = []
        for artifact in rootfs_artifacts:
            path = files_dir / artifact.name
            path.write_bytes(artifact.raw)
            path.chmod(0o600)
            files.append(
                {
                    "name": artifact.name,
                    "sha256": artifact.sha256,
                    "size": len(artifact.raw),
                }
            )
        manifest = {
            "schema_version": 1,
            "source_vendor_bundle_sha256": bundle.bundle_sha256,
            "source_vendor_manifest_sha256": bundle.manifest_sha256,
            "files": files,
            "policy": {
                "archive_only_files_excluded": True,
                "public_ingenic_lib_archive": False,
            },
        }
        manifest_path = work / "build-site.private.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)
        if {path.name for path in files_dir.iterdir()} != set(expected):
            raise VendorBundleError("vendor build-site contains unexpected files")
        os.replace(work, output_dir)
        return VendorBuildSite(
            files=expected,
            source_bundle_sha256=bundle.bundle_sha256,
            source_manifest_sha256=bundle.manifest_sha256,
        )
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
