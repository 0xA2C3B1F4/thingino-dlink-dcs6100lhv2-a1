"""Generate per-install private inputs without command-line secret values."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from .runtime_policy import normalize_ed25519_authorized_key
from .sd_package import atomic_write


class PrivateConfigError(ValueError):
    """Private bootstrap input or output violates the local-only contract."""


@dataclass(frozen=True, slots=True)
class PrivateConfigResult:
    output_dir: Path
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrivateInstallConfig:
    """One validated, stable set of device-private installation inputs."""

    authorized_key: bytes
    credential: bytes
    api_key: bytes
    wpa_config: bytes
    credential_set_id: str


_ROLE_MAP = {
    "management_credential": {
        "consumers": ["onvif", "root_webui"],
        "derives": [
            "rtsp_viewer_credential",
            "thingino_control_internal_token",
        ],
        "file": "installer.credential",
        "rotation": "explicit-with-session-binding",
    },
    "ssh_authorized_key": {
        "consumers": ["recovery_ap", "thingino_ssh"],
        "file": "authorized_keys",
        "rotation": "rebuild-recovery-session",
    },
    "station_wifi": {
        "consumers": ["wpa_supplicant"],
        "file": "wpa_supplicant.conf",
        "rotation": "explicit-with-new-wpa-input",
    },
    "webui_api_key": {
        "consumers": ["thingino_control_external_api"],
        "file": "webui-api.key",
        "rotation": "explicit",
    },
}


_HEX_PSK = re.compile(r"[0-9a-fA-F]{64}")
_HEX_SSID = re.compile(r"[0-9a-fA-F]{2,64}")
_TOKEN = re.compile(rb"[0-9a-f]{64}\n")
_PRIVATE_FILES = (
    "authorized_keys",
    "installer.credential",
    "webui-api.key",
    "wpa_supplicant.conf",
)
_TARGET = {
    "hardware_revision": "A1",
    "model": "DCS-6100LHV2",
}


def derive_rtsp_viewer_credential(credential: bytes) -> bytes:
    """Derive a domain-separated RTSP-only credential from a management secret."""

    if _TOKEN.fullmatch(credential) is None:
        raise PrivateConfigError("management credential framing is invalid")
    value = hmac.new(
        credential.rstrip(b"\n"),
        b"dcs6100-rtsp-viewer-credential-v1\0",
        hashlib.sha256,
    ).hexdigest()
    return (value + "\n").encode("ascii")


def _validate_wifi(ssid: str, passphrase: str) -> tuple[bytes, bytes]:
    try:
        ssid_bytes = ssid.encode("utf-8")
        passphrase_bytes = passphrase.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PrivateConfigError("Wi-Fi input is not valid UTF-8") from exc
    if not 1 <= len(ssid_bytes) <= 32:
        raise PrivateConfigError("SSID must contain 1 to 32 UTF-8 bytes")
    raw_psk = len(passphrase_bytes) == 64 and all(
        byte in b"0123456789abcdefABCDEF" for byte in passphrase_bytes
    )
    if not 8 <= len(passphrase_bytes) <= 63 and not raw_psk:
        raise PrivateConfigError(
            "WPA credential must contain 8 to 63 UTF-8 bytes or exactly "
            "64 hexadecimal ASCII characters"
        )
    if any(byte < 0x20 or byte == 0x7F for byte in ssid_bytes + passphrase_bytes):
        raise PrivateConfigError("Wi-Fi input contains a control character")
    return ssid_bytes, passphrase_bytes


def _quoted_wpa(value: bytes) -> str:
    escaped = []
    for byte in value:
        if byte in (0x22, 0x5C):
            escaped.append("\\" + chr(byte))
        elif 0x20 <= byte <= 0x7E:
            escaped.append(chr(byte))
        else:
            escaped.append(f"\\x{byte:02x}")
    return '"' + "".join(escaped) + '"'


def _read_bounded_regular(path: Path, *, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PrivateConfigError(f"cannot read {label}") from exc
    try:
        first = os.fstat(descriptor)
        if not stat.S_ISREG(first.st_mode) or first.st_size > limit:
            raise PrivateConfigError(f"{label} is not a bounded regular file")
        raw = os.read(descriptor, limit + 1)
        second = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        first.st_dev != second.st_dev
        or first.st_ino != second.st_ino
        or first.st_size != second.st_size
        or len(raw) != first.st_size
    ):
        raise PrivateConfigError(f"{label} changed while being read")
    return raw


def _validate_imported_wpa(payload: bytes) -> bytes:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PrivateConfigError("private WPA input is not ASCII") from exc
    if any(character not in "\n\r\t" and ord(character) < 0x20 for character in text):
        raise PrivateConfigError("private WPA input contains a control character")
    lines = [line.strip() for line in text.splitlines()]
    ssids = [line.removeprefix("ssid=") for line in lines if line.startswith("ssid=")]
    psks = [line.removeprefix("psk=") for line in lines if line.startswith("psk=")]
    if text.count("network={") != 1 or lines.count("}") != 1:
        raise PrivateConfigError("private WPA input must contain exactly one network")
    if len(ssids) != 1 or len(psks) != 1:
        raise PrivateConfigError("private WPA input must contain one SSID and PSK")
    ssid = ssids[0]
    if ssid.startswith('"') and ssid.endswith('"'):
        if not 2 < len(ssid) <= 130:
            raise PrivateConfigError("private WPA SSID is outside its size policy")
    elif _HEX_SSID.fullmatch(ssid) is None or len(ssid) % 2:
        raise PrivateConfigError("private WPA SSID encoding is invalid")
    if _HEX_PSK.fullmatch(psks[0]) is None:
        raise PrivateConfigError("private WPA input must use a derived 64-hex PSK")
    forbidden = ("password=", "sae_password=", "update_config=1")
    if any(marker in text for marker in forbidden):
        raise PrivateConfigError("private WPA input contains a mutable or plaintext secret")
    return payload if payload.endswith(b"\n") else payload + b"\n"


def _read_private_file(path: Path, *, label: str, limit: int) -> bytes:
    raw = _read_bounded_regular(path, label=label, limit=limit)
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise PrivateConfigError(f"cannot inspect {label}") from exc
    if mode & 0o077:
        raise PrivateConfigError(f"{label} is accessible outside its owner")
    return raw


def _validate_private_files(output_dir: Path) -> dict[str, bytes]:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise PrivateConfigError("private configuration directory is invalid")
    if output_dir.stat(follow_symlinks=False).st_mode & 0o077:
        raise PrivateConfigError(
            "private configuration directory is accessible outside its owner"
        )
    raw = {
        "authorized_keys": _read_private_file(
            output_dir / "authorized_keys", label="SSH authorized key", limit=2048
        ),
        "installer.credential": _read_private_file(
            output_dir / "installer.credential",
            label="management credential",
            limit=128,
        ),
        "webui-api.key": _read_private_file(
            output_dir / "webui-api.key", label="WebUI API key", limit=128
        ),
        "wpa_supplicant.conf": _read_private_file(
            output_dir / "wpa_supplicant.conf",
            label="private WPA configuration",
            limit=4096,
        ),
    }
    try:
        authorized_key = normalize_ed25519_authorized_key(raw["authorized_keys"])
    except ValueError as exc:
        raise PrivateConfigError(str(exc)) from exc
    if authorized_key != raw["authorized_keys"]:
        raise PrivateConfigError("SSH authorized key is not in canonical form")
    for name, label in (
        ("installer.credential", "management credential"),
        ("webui-api.key", "WebUI API key"),
    ):
        if _TOKEN.fullmatch(raw[name]) is None:
            raise PrivateConfigError(f"{label} framing is invalid")
    if _validate_imported_wpa(raw["wpa_supplicant.conf"]) != raw["wpa_supplicant.conf"]:
        raise PrivateConfigError("private WPA configuration is not canonical")
    return raw


def _manifest_v2_for(raw: dict[str, bytes]) -> dict[str, object]:
    digests = {name: hashlib.sha256(raw[name]).hexdigest() for name in _PRIVATE_FILES}
    set_hash = hashlib.sha256()
    set_hash.update(b"thingino-private-install-config-v2\0")
    for name in _PRIVATE_FILES:
        set_hash.update(name.encode("ascii") + b"\0" + bytes.fromhex(digests[name]))
    return {
        "bindings": {
            "management_credential": {
                "consumers": [
                    "onvif",
                    "root_webui",
                    "rtsp",
                    "thingino_control_internal_token_derivation",
                ],
                "file": "installer.credential",
                "sha256": digests["installer.credential"],
            },
            "ssh_authorized_key": {
                "consumers": ["recovery_ap", "thingino_ssh"],
                "file": "authorized_keys",
                "sha256": digests["authorized_keys"],
            },
            "station_wifi": {
                "consumers": ["wpa_supplicant"],
                "file": "wpa_supplicant.conf",
                "sha256": digests["wpa_supplicant.conf"],
            },
            "webui_api_key": {
                "consumers": ["thingino_control_external_api"],
                "file": "webui-api.key",
                "sha256": digests["webui-api.key"],
            },
        },
        "credential_set_id": set_hash.hexdigest(),
        "files": list(_PRIVATE_FILES),
        "rotation_policy": "explicit-only-no-build-regeneration",
        "schema_version": 2,
        "target": _TARGET,
    }


def _manifest_for(raw: dict[str, bytes]) -> dict[str, object]:
    digests = {name: hashlib.sha256(raw[name]).hexdigest() for name in _PRIVATE_FILES}
    set_hash = hashlib.sha256()
    # The set identity is bound only to the private files, not to the manifest
    # schema, so sealing an existing v2 set does not detach its recovery session.
    set_hash.update(b"thingino-private-install-config-v2\0")
    for name in _PRIVATE_FILES:
        set_hash.update(name.encode("ascii") + b"\0" + bytes.fromhex(digests[name]))
    return {
        "bindings": {
            "management_credential": {
                "consumers": ["onvif", "root_webui"],
                "derives": [
                    "rtsp_viewer_credential",
                    "thingino_control_internal_token",
                ],
                "file": "installer.credential",
                "sha256": digests["installer.credential"],
            },
            "rtsp_viewer_credential": {
                "consumers": ["rtsp"],
                "derived_from": "management_credential",
                "derivation": "hmac-sha256-dcs6100-rtsp-viewer-credential-v1",
            },
            "ssh_authorized_key": {
                "consumers": ["recovery_ap", "thingino_ssh"],
                "file": "authorized_keys",
                "sha256": digests["authorized_keys"],
            },
            "station_wifi": {
                "consumers": ["wpa_supplicant"],
                "file": "wpa_supplicant.conf",
                "sha256": digests["wpa_supplicant.conf"],
            },
            "webui_api_key": {
                "consumers": ["thingino_control_external_api"],
                "file": "webui-api.key",
                "sha256": digests["webui-api.key"],
            },
        },
        "credential_set_id": set_hash.hexdigest(),
        "files": list(_PRIVATE_FILES),
        "rotation_policy": "explicit-only-no-build-regeneration",
        "schema_version": 3,
        "target": _TARGET,
    }


def _read_manifest(path: Path) -> dict[str, object]:
    raw = _read_private_file(
        path, label="private configuration manifest", limit=16 * 1024
    )
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateConfigError("private configuration manifest is invalid") from exc
    if not isinstance(document, dict):
        raise PrivateConfigError("private configuration manifest is invalid")
    return document


def seal_private_config(
    *, output_dir: Path, authorized_key: bytes | None = None
) -> PrivateConfigResult:
    """Upgrade or verify one existing set without rotating any secret value."""

    raw = _validate_private_files(output_dir)
    manifest_path = output_dir / "private-config.json"
    current = _read_manifest(manifest_path)
    schema_version = current.get("schema_version")
    if schema_version not in (1, 2, 3):
        raise PrivateConfigError("private configuration schema is unsupported")
    if current.get("target") != _TARGET or current.get("files") != list(_PRIVATE_FILES):
        raise PrivateConfigError(
            "private configuration manifest does not match its target"
        )
    if schema_version == 2 and current != _manifest_v2_for(raw):
        raise PrivateConfigError(
            "private configuration binding manifest does not match its files"
        )
    if schema_version == 3 and authorized_key is None:
        if current != _manifest_for(raw):
            raise PrivateConfigError(
                "private configuration binding manifest does not match its files"
            )
        return PrivateConfigResult(output_dir=output_dir, files=_PRIVATE_FILES)
    if authorized_key is not None:
        try:
            normalized = normalize_ed25519_authorized_key(authorized_key)
        except ValueError as exc:
            raise PrivateConfigError(str(exc)) from exc
        atomic_write(output_dir / "authorized_keys", normalized)
        raw["authorized_keys"] = normalized
    atomic_write(
        manifest_path,
        (json.dumps(_manifest_for(raw), indent=2, sort_keys=True) + "\n").encode(),
    )
    return PrivateConfigResult(output_dir=output_dir, files=_PRIVATE_FILES)


def load_private_config(output_dir: Path) -> PrivateInstallConfig:
    raw = _validate_private_files(output_dir)
    manifest = _read_manifest(output_dir / "private-config.json")
    if manifest.get("schema_version") != 3:
        raise PrivateConfigError(
            "private configuration is not sealed; run seal-private-install-config"
        )
    if manifest != _manifest_for(raw):
        raise PrivateConfigError(
            "private configuration binding manifest does not match its files"
        )
    return PrivateInstallConfig(
        authorized_key=raw["authorized_keys"],
        credential=raw["installer.credential"],
        api_key=raw["webui-api.key"],
        wpa_config=raw["wpa_supplicant.conf"],
        credential_set_id=str(manifest["credential_set_id"]),
    )


def load_private_wpa_config(
    path: Path, *, independent_from: Path | None = None
) -> bytes:
    """Read one private WPA source without exposing or weakening its policy."""

    raw = _read_private_file(
        path,
        label="expected station Wi-Fi configuration",
        limit=4096,
    )
    if independent_from is not None:
        try:
            same_file = os.path.samefile(path, independent_from)
        except OSError as exc:
            raise PrivateConfigError(
                "cannot prove station Wi-Fi confirmation independence"
            ) from exc
        if same_file:
            raise PrivateConfigError(
                "station Wi-Fi confirmation must be independent of sealed config"
            )
    canonical = _validate_imported_wpa(raw)
    if canonical != raw:
        raise PrivateConfigError(
            "expected station Wi-Fi configuration is not canonical"
        )
    return canonical


def station_wifi_binding(payload: bytes) -> bytes:
    """Return a secret-safe semantic identity for one validated WPA network."""

    canonical = _validate_imported_wpa(payload)
    text = canonical.decode("ascii")
    inside = False
    fields: dict[str, bytes] = {}
    token_fields = {"group", "key_mgmt", "pairwise", "proto"}
    binding_fields = {"ssid", "psk", *token_fields}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "network={":
            inside = True
            continue
        if line == "}":
            inside = False
            continue
        if not inside:
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise PrivateConfigError(
                "private WPA network contains an invalid field"
            )
        if key not in binding_fields:
            continue
        if key in fields:
            raise PrivateConfigError(
                "private WPA network contains a repeated binding field"
            )
        normalized = value.strip().encode("ascii")
        if key == "ssid":
            if value.startswith('"') and value.endswith('"'):
                body = value[1:-1]
                decoded = bytearray()
                index = 0
                while index < len(body):
                    if body[index] != "\\":
                        decoded.append(ord(body[index]))
                        index += 1
                    elif body[index : index + 2] in {r'\"', r"\\"}:
                        decoded.append(ord(body[index + 1]))
                        index += 2
                    elif (
                        body[index : index + 2] == r"\x"
                        and index + 4 <= len(body)
                        and re.fullmatch(r"[0-9a-fA-F]{2}", body[index + 2 : index + 4])
                    ):
                        decoded.append(int(body[index + 2 : index + 4], 16))
                        index += 4
                    else:
                        raise PrivateConfigError(
                            "private WPA SSID escape is invalid"
                        )
                normalized = bytes(decoded)
            else:
                normalized = bytes.fromhex(value)
        elif key == "psk":
            normalized = value.lower().encode("ascii")
        elif key in token_fields:
            normalized = b" ".join(
                sorted(token.encode("ascii") for token in value.split())
            )
        fields[key] = normalized
    digest = hashlib.sha256()
    digest.update(b"thingino-station-wifi-binding-v1\0")
    for key in sorted(fields):
        digest.update(key.encode("ascii") + b"\0" + fields[key] + b"\0")
    return digest.digest()


def _fingerprint_key(raw: dict[str, bytes]) -> bytes:
    digest = hashlib.sha256()
    digest.update(b"thingino-private-config-fingerprint-v1\0")
    digest.update(raw["installer.credential"])
    digest.update(raw["webui-api.key"])
    return digest.digest()


def _safe_fingerprint(key: bytes, *, role: str, value: bytes) -> str:
    return hmac.new(key, role.encode("ascii") + b"\0" + value, hashlib.sha256).hexdigest()[:16]


def inspect_private_config(
    *, output_dir: Path, session_dir: Path | None = None
) -> dict[str, object]:
    """Return role bindings and keyed fingerprints without exposing secret values."""

    private = load_private_config(output_dir)
    raw = _validate_private_files(output_dir)
    key = _fingerprint_key(raw)
    roles: dict[str, object] = {}
    for role, policy in _ROLE_MAP.items():
        filename = str(policy["file"])
        roles[role] = {
            "consumers": list(policy["consumers"]),
            "derives": list(policy.get("derives", [])),
            "exists": True,
            "fingerprint": _safe_fingerprint(key, role=role, value=raw[filename]),
            "rotation": policy["rotation"],
            "storage": "private-install-config",
        }
    roles["thingino_control_internal_token"] = {
        "consumers": ["thingino_control_loopback_backend"],
        "derived_from": "management_credential",
        "exists": True,
        "fingerprint": None,
        "rotation": "with-management-credential",
        "storage": "derived-during-final-root-build",
    }
    roles["rtsp_viewer_credential"] = {
        "consumers": ["rtsp"],
        "derived_from": "management_credential",
        "exists": True,
        "fingerprint": _safe_fingerprint(
            key,
            role="rtsp_viewer_credential",
            value=derive_rtsp_viewer_credential(private.credential),
        ),
        "rotation": "with-management-credential-during-image-build",
        "storage": "derived-during-final-root-build",
    }
    roles["webui_session_id"] = {
        "consumers": ["thingino_control_web_session"],
        "exists": False,
        "existence_scope": "persistent-private-config",
        "fingerprint": None,
        "rotation": "new-random-value-per-login-and-process-lifetime",
        "runtime_state": "not-inspected",
        "storage": "runtime-memory-only",
    }
    if session_dir is not None:
        from .recovery_ap.host import load_host_session

        load_host_session(session_dir)
        session_files = {
            "recovery_ap_psk": ("media/RECOVERY/AP.PSK", "recovery_ap_hostapd"),
            "recovery_service_credential": (
                "host/service.credential",
                "recovery_ap_fixed_protocol",
            ),
            "recovery_ssh_host_key": ("media/RECOVERY/HOST.KEY", "recovery_ap_dropbear"),
        }
        for role, (relative, consumer) in session_files.items():
            value = _read_private_file(
                session_dir / relative,
                label=role.replace("_", " "),
                limit=16 * 1024,
            )
            roles[role] = {
                "consumers": [consumer],
                "exists": True,
                "fingerprint": _safe_fingerprint(key, role=role, value=value),
                "rotation": "rebuild-recovery-session",
                "storage": "private-recovery-session",
            }
    return {
        "credential_set_id": private.credential_set_id,
        "roles": roles,
        "rotation_policy": "explicit-only-no-build-regeneration",
        "schema_version": 1,
        "target": _TARGET,
    }


def rotate_private_config_role(
    *,
    output_dir: Path,
    role: str,
    session_dir: Path | None = None,
    wpa_config: bytes | None = None,
) -> dict[str, object]:
    """Rotate one supported role and reseal the binding manifest."""

    load_private_config(output_dir)
    raw = _validate_private_files(output_dir)
    before = str(_read_manifest(output_dir / "private-config.json")["credential_set_id"])
    if role == "management_credential":
        if session_dir is None:
            raise PrivateConfigError(
                "management credential rotation requires the bound recovery session"
            )
        load_private_config_for_session(output_dir=output_dir, session_dir=session_dir)
        replacement = (secrets.token_hex(32) + "\n").encode("ascii")
        atomic_write(session_dir / "host/service.credential", replacement)
        raw["installer.credential"] = replacement
    elif role == "webui_api_key":
        raw["webui-api.key"] = (secrets.token_hex(32) + "\n").encode("ascii")
    elif role == "station_wifi":
        if wpa_config is None:
            raise PrivateConfigError("station Wi-Fi rotation requires a new WPA input")
        raw["wpa_supplicant.conf"] = _validate_imported_wpa(wpa_config)
    elif role in {"ssh_authorized_key", "recovery_ap_psk", "recovery_ssh_host_key"}:
        raise PrivateConfigError(f"{role} rotation requires rebuilding the recovery session")
    else:
        raise PrivateConfigError("private configuration role is not rotatable")
    filename = str(_ROLE_MAP[role]["file"])
    atomic_write(output_dir / filename, raw[filename])
    manifest = _manifest_for(raw)
    atomic_write(
        output_dir / "private-config.json",
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
    )
    load_private_config(output_dir)
    if role == "management_credential":
        assert session_dir is not None
        load_private_config_for_session(output_dir=output_dir, session_dir=session_dir)
    return {
        "credential_set_id_after": manifest["credential_set_id"],
        "credential_set_id_before": before,
        "role": role,
        "rotated": True,
        "schema_version": 1,
    }


def load_private_config_for_session(
    *, output_dir: Path, session_dir: Path
) -> PrivateInstallConfig:
    from .recovery_ap.host import load_host_session

    load_host_session(session_dir)
    private = load_private_config(output_dir)
    session_authorized_key = _read_private_file(
        session_dir / "host/identity.pub",
        label="recovery-session SSH authorized key",
        limit=2048,
    )
    try:
        session_authorized_key = normalize_ed25519_authorized_key(
            session_authorized_key
        )
    except ValueError as exc:
        raise PrivateConfigError(str(exc)) from exc
    if session_authorized_key != private.authorized_key:
        raise PrivateConfigError(
            "private configuration is bound to a different recovery SSH identity"
        )
    service_credential = _read_private_file(
        session_dir / "host/service.credential",
        label="recovery-session service credential",
        limit=128,
    )
    if service_credential != private.credential:
        raise PrivateConfigError(
            "private configuration is bound to a different recovery service credential"
        )
    return private


def seal_private_config_for_session(
    *, output_dir: Path, session_dir: Path
) -> PrivateConfigResult:
    """Bind an existing set to one session without regenerating secret values."""

    current = _validate_private_files(output_dir)
    service_credential = _read_private_file(
        session_dir / "host/service.credential",
        label="recovery-session service credential",
        limit=128,
    )
    if service_credential != current["installer.credential"]:
        raise PrivateConfigError(
            "private configuration is bound to a different recovery service credential"
        )
    authorized_key = read_authorized_key(session_dir / "host/identity.pub")
    result = seal_private_config(
        output_dir=output_dir,
        authorized_key=authorized_key,
    )
    load_private_config_for_session(
        output_dir=output_dir,
        session_dir=session_dir,
    )
    return result


def _write_private_config(
    *,
    output_dir: Path,
    wpa_config: bytes,
    authorized_key: bytes,
    credential: bytes | None = None,
) -> PrivateConfigResult:
    if output_dir.exists():
        raise PrivateConfigError("private output directory already exists")
    try:
        normalized_authorized_key = normalize_ed25519_authorized_key(authorized_key)
    except ValueError as exc:
        raise PrivateConfigError(str(exc)) from exc
    output_dir.mkdir(parents=True, mode=0o700)
    os.chmod(output_dir, 0o700)

    if credential is None:
        credential = (secrets.token_hex(32) + "\n").encode("ascii")
    elif _TOKEN.fullmatch(credential) is None:
        raise PrivateConfigError("management credential framing is invalid")
    api_key = (secrets.token_hex(32) + "\n").encode("ascii")
    wpa_path = output_dir / "wpa_supplicant.conf"
    authorized_keys_path = output_dir / "authorized_keys"
    credential_path = output_dir / "installer.credential"
    api_key_path = output_dir / "webui-api.key"
    manifest_path = output_dir / "private-config.json"
    private_metadata = _manifest_for(
        {
            "authorized_keys": normalized_authorized_key,
            "installer.credential": credential,
            "webui-api.key": api_key,
            "wpa_supplicant.conf": wpa_config,
        }
    )

    try:
        atomic_write(wpa_path, wpa_config)
        atomic_write(authorized_keys_path, normalized_authorized_key)
        atomic_write(credential_path, credential)
        atomic_write(api_key_path, api_key)
        atomic_write(
            manifest_path,
            (json.dumps(private_metadata, indent=2, sort_keys=True) + "\n").encode(),
        )
    except BaseException:
        for path in (
            manifest_path,
            api_key_path,
            credential_path,
            authorized_keys_path,
            wpa_path,
        ):
            path.unlink(missing_ok=True)
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise
    return PrivateConfigResult(
        output_dir=output_dir,
        files=_PRIVATE_FILES,
    )


def generate_private_config(
    *,
    output_dir: Path,
    ssid: str,
    passphrase: str,
    authorized_key: bytes,
    credential: bytes | None = None,
) -> PrivateConfigResult:
    wpa_config = render_private_wpa_config(ssid=ssid, passphrase=passphrase)
    return _write_private_config(
        output_dir=output_dir,
        wpa_config=wpa_config,
        authorized_key=authorized_key,
        credential=credential,
    )


def render_private_wpa_config(*, ssid: str, passphrase: str) -> bytes:
    """Derive one canonical WPA configuration without exposing the passphrase."""

    ssid_bytes, passphrase_bytes = _validate_wifi(ssid, passphrase)
    if len(passphrase_bytes) == 64:
        wpa_psk = passphrase_bytes.decode("ascii").lower()
    else:
        wpa_psk = hashlib.pbkdf2_hmac(
            "sha1", passphrase_bytes, ssid_bytes, 4096, dklen=32
        ).hex()
    wpa_config = "\n".join(
        (
            "ctrl_interface=/run/wpa_supplicant",
            "update_config=0",
            "ap_scan=1",
            "network={",
            f"    ssid={_quoted_wpa(ssid_bytes)}",
            f"    psk={wpa_psk}",
            "    key_mgmt=WPA-PSK",
            "    proto=RSN WPA",
            "}",
            "",
        )
    ).encode("utf-8")
    return wpa_config


def import_private_wpa_config(
    *, output_dir: Path, source: Path, authorized_key: bytes
) -> PrivateConfigResult:
    payload = _read_bounded_regular(
        source, label="private WPA input", limit=4096
    )
    return _write_private_config(
        output_dir=output_dir,
        wpa_config=_validate_imported_wpa(payload),
        authorized_key=authorized_key,
    )


def read_authorized_key(source: Path) -> bytes:
    payload = _read_bounded_regular(
        source, label="SSH authorized key", limit=2048
    )
    try:
        return normalize_ed25519_authorized_key(payload)
    except ValueError as exc:
        raise PrivateConfigError(str(exc)) from exc


def _read_inherited_secrets(
    *, secrets_fd: int, fields: set[str], label: str
) -> dict[str, str]:
    if secrets_fd < 3:
        raise PrivateConfigError("secrets file descriptor must be inherited and at least 3")
    try:
        with os.fdopen(os.dup(secrets_fd), "r", encoding="utf-8") as source:
            raw = source.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            raise PrivateConfigError("inherited secrets JSON exceeds its size limit")
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateConfigError("cannot read inherited secrets JSON") from exc
    if not isinstance(document, dict) or set(document) != fields or not all(
        isinstance(document[key], str) for key in fields
    ):
        raise PrivateConfigError(f"inherited {label} JSON has the wrong schema")
    return document


def read_private_input(*, secrets_fd: int | None) -> tuple[str, str]:
    if secrets_fd is None:
        ssid = getpass.getpass("Wi-Fi SSID (hidden): ")
        passphrase = getpass.getpass("Wi-Fi passphrase (hidden): ")
        return ssid, passphrase
    document = _read_inherited_secrets(
        secrets_fd=secrets_fd,
        fields={"ssid", "passphrase"},
        label="secrets",
    )
    return document["ssid"], document["passphrase"]


def read_confirmed_private_input(
    *, secrets_fd: int | None
) -> tuple[str, str, str, str]:
    """Read station Wi-Fi twice without accepting either secret through argv."""

    if secrets_fd is None:
        ssid = getpass.getpass("Station Wi-Fi SSID (hidden): ")
        passphrase = getpass.getpass("Station Wi-Fi passphrase (hidden): ")
        confirmation_ssid = getpass.getpass("Confirm station Wi-Fi SSID (hidden): ")
        confirmation_passphrase = getpass.getpass(
            "Confirm station Wi-Fi passphrase (hidden): "
        )
        return ssid, passphrase, confirmation_ssid, confirmation_passphrase
    fields = {
        "ssid",
        "passphrase",
        "confirmation_ssid",
        "confirmation_passphrase",
    }
    document = _read_inherited_secrets(
        secrets_fd=secrets_fd,
        fields=fields,
        label="confirmed-secrets",
    )
    return (
        document["ssid"],
        document["passphrase"],
        document["confirmation_ssid"],
        document["confirmation_passphrase"],
    )
