"""Validate one private, hash-locked C1 glibc media closure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .vendor_bundle import VendorBundleError, parse_elf32_mips


MANIFEST_NAME = "media-closure.private.json"
FILES_DIRECTORY = "files"
TARGET = "DCS-6100LHV2-A1"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_FILE_SIZE = 4 * 1024 * 1024
MAX_TOTAL_SIZE = 24 * 1024 * 1024
KERNEL_VERMAGIC = b"3.10.14__isvp_swan_1.0__ preempt mod_unload MIPS32_R1 32BIT "
STARTUP_ORDER = ("S06ircut", "S10daynightd", "S11modules", "S31prudynt")
RUNTIME_DLOPEN = ("libaudioProcess.so",)
PROVEN_IDENTITIES = {
    "bin/prudynt": "5e4fafcf1971b4eeaef39ec3ac06ef0cb25f96056427d4b9b1477b0535be49c9",
    "etc/prudynt.json": "e2df0caa0f03b862c888db7b0406cef904c630c184b3468dbd62dfd5a8a119fb",
    "init/F01datetime": "f812fa67cb012aa754f5e4b7e4df00157403c049d4f853f0a0f0da77551d4a56",
    "init/S06ircut": "f6f68ecc932341a178b1be1183dcca4c9b35afd9df804c24ca9f983f8e343d2f",
    "init/S10daynightd": "bb23cfcc0de379bfa1124301d57833925970a9b0add1636a901957727846ce91",
    "init/S11modules": "8c8abfde296e546c262cc4bbfb179d14f75bd321a14f99e68140f7f7d9129bb4",
    "init/S31prudynt": "c26ebe316dde9bf14187c22d926af39490a4a6b9ff5c114b9f14875e1491e623",
    "lib/ld.so.1": "dbb9ac65e090d5a4a701eaad21c170e0bb45abfd8b112de55952fa7a8248c67c",
    "lib/libalog.so": "c2dff5a7ab4183c41781e3c2a879ef3aad3fc1feb3412b4c68a22eda6566c3a5",
    "lib/libatomic.so.1": "5c60cc3df8b0a591b5d1111b7a1c8a09d291fea7d4fc233f575639a80dba5103",
    "lib/libaudioProcess.so": "f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273",
    "lib/libc.so.6": "9bdbf7af960dbefcd06f7d6a57e34330519e7732a38f7de214f8047a0e56ddaa",
    "lib/libcurl.so.4": "e89005104ec334edac4dd5c450129568dacaf69c6a9c044398dcb1903542b05d",
    "lib/libdl.so.2": "507b11006c5ece39e1610d16de0c4222d0e481f9f8674098c7bafc604f43d382",
    "lib/libeverest.so": "71606f68aba8a674450dffe5c802940d059f81f31c26c2aa98b1a44d8b2125b6",
    "lib/libfaac.so.1": "fae33bd4492377882ec10ad96fcdaaa9aae70f672ef656f66226ec382d458aae",
    "lib/libgcc_s.so.1": "5e92c416817f90b5f86cf4aa57f3b5c5e6ed569a0ed735403a4609625e213723",
    "lib/libhelix-aac.so": "a4afd3d9e1adbceca37eff4e482afdc9ddd89b721c6d34d7a2b9aaa6ee4db071",
    "lib/libimp.so": "14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe",
    "lib/libjct.so.1": "ebdb6e1f0549c860acf9bc196c24f6eff857b2238b27e08c821fee746c7c3098",
    "lib/libm.so.6": "50fb702ed2b25ce3bbd3318e198318c962543e57dd80ff84511933e4f871db84",
    "lib/libmbedcrypto.so.16": "b9d4b969d51b80d0c5fbe024521382348a8902cab13e84bb9ed9960236e3a42a",
    "lib/libmbedtls.so.21": "b4ec79123e8bb2b44e4604162ac1c2dedf606e6f539c20e2de6e48f6f4733e66",
    "lib/libmbedx509.so.7": "5badad03698d3ebaa6180a528c24e0410bc5f542aa3095acccae3e821630eb28",
    "lib/libopus.so.0": "d76db55e821e093ee081c812a4c28ff7791882a5928419e172689deb778aefa8",
    "lib/libp256m.so": "6eca10db0e5789a2ec3c64ea2131ea4d8493b3a99382efca115907b6284481ad",
    "lib/libpthread.so.0": "9bdbf7af960dbefcd06f7d6a57e34330519e7732a38f7de214f8047a0e56ddaa",
    "lib/libstdc++.so.6": "f365f78d9d84810051d0d978f23bd67253c4bb792ff5168259cb828c7ec7d432",
    "lib/libsysutils.so": "d38e37b9746aebad62cf2ed906b099a2d22f7d44d0972cf516443b542dae6c55",
    "modules/sensor_os02g10_t31.ko": "4b034950cd9450f9c1cfdff19bcf0d92a1cf69fd412218c1841706ef9cdde8be",
    "modules/tx-isp-t31.ko": "d13b5654e858155d07ce10dce97477549974227a90d02c37737907221fc2a573",
    "sensor/os02g10-t31.bin": "dce8af706b8663bcefe47b38418fbec469d7b3704665a45544f8bb1845e5f78e",
}


class MediaClosureError(ValueError):
    """The private single-runtime media closure violates its closed manifest."""


@dataclass(frozen=True, slots=True)
class MediaClosureFile:
    path: str
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class MediaClosure:
    files: tuple[MediaClosureFile, ...]
    closure_sha256: str
    manifest_sha256: str
    startup_order: tuple[str, ...]
    runtime_dlopen: tuple[str, ...]

    def by_path(self) -> dict[str, MediaClosureFile]:
        return {item.path: item for item in self.files}


def _read_regular(path: Path, label: str, *, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise MediaClosureError(f"cannot read {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise MediaClosureError(f"{label} is not a bounded regular file")
        raw = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise MediaClosureError(f"{label} changed while being read")
    if len(raw) != before.st_size:
        raise MediaClosureError(f"{label} read was incomplete")
    return raw


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MediaClosureError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise MediaClosureError(f"{label} has unexpected or missing fields")


def _safe_relative(value: object) -> str:
    if not isinstance(value, str):
        raise MediaClosureError("media file path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise MediaClosureError("media file path is unsafe")
    if path.parts[0] not in ("bin", "etc", "init", "lib", "modules", "sensor"):
        raise MediaClosureError("media file is outside the single-runtime allowlist")
    return str(path)


def _config(raw: bytes) -> None:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaClosureError("C1 Prudynt config is not valid JSON") from exc
    document = _object(document, "C1 Prudynt config")
    sensor = _object(document.get("sensor"), "C1 Prudynt sensor")
    stream = _object(document.get("stream0"), "C1 Prudynt stream0")
    if sensor.get("model") != "os02g10" or sensor.get("fps") != 25:
        raise MediaClosureError("C1 config is not the proven OS02G10 profile")
    if (
        stream.get("enabled") is not True
        or stream.get("width") != 1920
        or stream.get("height") != 1080
        or stream.get("fps") != 15
    ):
        raise MediaClosureError("C1 config is not the proven 1080p profile")


def _validate_runtime(files: dict[str, MediaClosureFile]) -> None:
    prudynt = files["bin/prudynt"].raw
    if b"/lib/ld.so.1" not in prudynt:
        raise MediaClosureError("C1 Prudynt uses the wrong interpreter")
    if b"/etc/sensor/os02g10-t31.bin" not in prudynt:
        raise MediaClosureError("C1 Prudynt uses the wrong IQ path")
    libraries = {
        path.removeprefix("lib/"): item
        for path, item in files.items()
        if path.startswith("lib/")
    }
    for item in (files["bin/prudynt"], *libraries.values()):
        try:
            metadata = parse_elf32_mips(item.raw, item.path)
        except VendorBundleError as exc:
            raise MediaClosureError(str(exc)) from exc
        missing = sorted(name for name in metadata.needed if name not in libraries)
        if missing:
            raise MediaClosureError(
                f"C1 runtime lacks dependencies for {item.path}: {', '.join(missing)}"
            )
    for name in ("libimp.so", "libalog.so", "libsysutils.so"):
        if name not in libraries:
            raise MediaClosureError(f"C1 runtime lacks {name}")
    if b"libaudioProcess.so" not in files["lib/libimp.so"].raw:
        raise MediaClosureError("C1 libimp no longer declares its runtime dlopen")
    if "libaudioProcess.so" not in libraries:
        raise MediaClosureError("C1 runtime lacks its libaudioProcess dlopen target")
    for path in ("modules/tx-isp-t31.ko", "modules/sensor_os02g10_t31.ko"):
        if KERNEL_VERMAGIC not in files[path].raw:
            raise MediaClosureError(f"C1 module uses the wrong vermagic: {path}")


def load_media_closure(directory: Path) -> MediaClosure:
    directory = directory.expanduser()
    if directory.is_symlink():
        raise MediaClosureError("media closure is not a real directory")
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise MediaClosureError("media closure is not a real directory")
    manifest_raw = _read_regular(
        directory / MANIFEST_NAME,
        "media closure manifest",
        limit=256 * 1024,
    )
    try:
        document = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaClosureError("media closure manifest is not valid JSON") from exc
    document = _object(document, "media closure manifest")
    _exact_keys(
        document,
        {
            "schema_version",
            "target",
            "runtime",
            "interpreter",
            "startup_order",
            "preconditions",
            "runtime_dlopen",
            "native_warmup",
            "chroot",
            "two_stage_markers",
            "s14_volatile_config",
            "files",
        },
        "media closure manifest",
    )
    if (
        document["schema_version"] != 2
        or document["target"] != TARGET
        or document["runtime"] != "single-glibc-c1-reconstruction"
        or document["interpreter"] != "/lib/ld.so.1"
        or document["startup_order"] != list(STARTUP_ORDER)
        or document["preconditions"] != ["F01datetime", "/dev/shm"]
        or document["runtime_dlopen"] != list(RUNTIME_DLOPEN)
        or document["native_warmup"] is not False
        or document["chroot"] is not False
        or document["two_stage_markers"] is not False
        or document["s14_volatile_config"] is not False
    ):
        raise MediaClosureError("media closure is not the exact single-runtime contract")
    entries = document["files"]
    if not isinstance(entries, list) or not entries:
        raise MediaClosureError("media closure file list is invalid")
    files_root = directory / FILES_DIRECTORY
    if files_root.is_symlink() or not files_root.is_dir():
        raise MediaClosureError("media closure files directory is invalid")
    loaded: list[MediaClosureFile] = []
    declared: set[str] = set()
    total = 0
    for value in entries:
        entry = _object(value, "media closure file")
        _exact_keys(entry, {"path", "sha256", "size"}, "media closure file")
        relative = _safe_relative(entry["path"])
        if relative in declared:
            raise MediaClosureError("media closure declares a duplicate path")
        size = entry["size"]
        sha256 = entry["sha256"]
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or size > MAX_FILE_SIZE
            or not isinstance(sha256, str)
            or SHA256_PATTERN.fullmatch(sha256) is None
        ):
            raise MediaClosureError("media closure file metadata is invalid")
        raw = _read_regular(files_root / relative, relative, limit=MAX_FILE_SIZE)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
            raise MediaClosureError(f"media closure file identity mismatch: {relative}")
        declared.add(relative)
        loaded.append(MediaClosureFile(relative, raw, sha256))
        total += len(raw)
        if total > MAX_TOTAL_SIZE:
            raise MediaClosureError("media closure exceeds its total size limit")
    actual: set[str] = set()
    for path in files_root.rglob("*"):
        if path.is_symlink():
            raise MediaClosureError("media closure contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(files_root).as_posix())
        elif not path.is_dir():
            raise MediaClosureError("media closure contains an unsupported entry")
    if actual != declared:
        raise MediaClosureError("media closure has extra or missing files")
    if declared != set(PROVEN_IDENTITIES):
        raise MediaClosureError("media closure path set differs from the proven closure")
    by_path = {item.path: item for item in loaded}
    for path, expected_sha256 in PROVEN_IDENTITIES.items():
        if by_path[path].sha256 != expected_sha256:
            raise MediaClosureError(f"media closure changed proven component: {path}")
    _config(by_path["etc/prudynt.json"].raw)
    _validate_runtime(by_path)
    digest = hashlib.sha256()
    for item in sorted(loaded, key=lambda value: value.path):
        digest.update(item.path.encode("ascii") + b"\0")
        digest.update(len(item.raw).to_bytes(8, "big"))
        digest.update(item.raw)
    return MediaClosure(
        files=tuple(sorted(loaded, key=lambda value: value.path)),
        closure_sha256=digest.hexdigest(),
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        startup_order=STARTUP_ORDER,
        runtime_dlopen=RUNTIME_DLOPEN,
    )
