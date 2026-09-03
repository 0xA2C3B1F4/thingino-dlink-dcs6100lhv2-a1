"""Create one private SD/host session for the recovery-AP experiment."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from installer.private_config import PrivateConfigError, read_authorized_key
from installer.runtime_policy import normalize_ed25519_authorized_key


class RecoveryApSessionError(ValueError):
    """A private recovery-AP session input or generated key is invalid."""


@dataclass(frozen=True, slots=True)
class RecoveryApSession:
    output_dir: Path
    setup_ssid: str


def ensure_uartless_provisioning_session(
    *,
    output_dir: Path,
    ssh_keygen: Path,
    camera_identity_sha256: str,
) -> RecoveryApSession:
    """Create or validate a local-only session for one UARTless recovery."""

    if (
        len(camera_identity_sha256) != 64
        or any(character not in "0123456789abcdef" for character in camera_identity_sha256)
    ):
        raise RecoveryApSessionError("UARTless camera identity is invalid")
    if ssh_keygen.is_symlink() or not ssh_keygen.is_file() or not os.access(
        ssh_keygen, os.X_OK
    ):
        raise RecoveryApSessionError("ssh-keygen is not an executable regular file")
    if output_dir.exists() or output_dir.is_symlink():
        from .host import load_host_session, load_service_credential

        session = load_host_session(output_dir)
        load_service_credential(output_dir)
        if (
            session.session_kind != "uartless-functional-provisioning"
            or session.camera_identity_sha256 != camera_identity_sha256
            or session.transport_enabled
        ):
            raise RecoveryApSessionError(
                "existing UARTless provisioning session is bound elsewhere"
            )
        return RecoveryApSession(
            output_dir=output_dir,
            setup_ssid=f"DCS6100-{camera_identity_sha256[:8]}",
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    temporary.chmod(0o700)
    try:
        host = temporary / "host"
        host.mkdir(mode=0o700)
        identity = host / "identity"
        binding = host / ".binding-host-key"
        for target, comment in (
            (identity, "dcs6100-uartless-provisioning"),
            (binding, "dcs6100-uartless-session-binding"),
        ):
            _run(
                [
                    str(ssh_keygen),
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    comment,
                    "-f",
                    str(target),
                ],
                "ssh-keygen",
            )
        read_authorized_key(identity.with_suffix(".pub"))
        binding_public = read_authorized_key(binding.with_suffix(".pub"))
        binding.unlink()
        binding.with_suffix(".pub").unlink()
        setup_ssid = f"DCS6100-{camera_identity_sha256[:8]}"
        station_mdns_name = f"{setup_ssid.lower()}.local"
        _write(host / "known_hosts", b"192.168.88.1 " + binding_public)
        _write(
            host / "service.credential",
            (secrets.token_hex(32) + "\n").encode("ascii"),
        )
        manifest = {
            "ap_address": None,
            "camera_identity_sha256": camera_identity_sha256,
            "contains_secrets": True,
            "control": "local-provisioning-only",
            "nor_writes": False,
            "schema_version": 1,
            "session_kind": "uartless-functional-provisioning",
            "setup_ssid": setup_ssid,
            "station_mdns_name": station_mdns_name,
            "transport_enabled": False,
        }
        _write(
            host / "session.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("ascii"),
        )
        for path in temporary.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
        os.replace(temporary, output_dir)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return RecoveryApSession(output_dir=output_dir, setup_ssid=setup_ssid)


def _run(arguments: list[str], label: str) -> bytes:
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            check=False,
        )
    except OSError as exc:
        raise RecoveryApSessionError(f"cannot run {label}") from exc
    if result.returncode:
        raise RecoveryApSessionError(f"{label} failed")
    return result.stdout + result.stderr


def _public_key_from_output(raw: bytes) -> bytes:
    candidates = [
        line + b"\n" for line in raw.splitlines() if line.startswith(b"ssh-ed25519 ")
    ]
    if len(candidates) != 1:
        raise RecoveryApSessionError("Dropbear host public key output is invalid")
    try:
        return normalize_ed25519_authorized_key(candidates[0])
    except ValueError as exc:
        raise RecoveryApSessionError(str(exc)) from exc


def _write(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RecoveryApSessionError("private session write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_private(path: Path, label: str, limit: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RecoveryApSessionError(f"cannot read private {label}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not path.is_file()
            or metadata.st_size < 1
            or metadata.st_size > limit
            or metadata.st_mode & 0o077
        ):
            raise RecoveryApSessionError(f"private {label} violates its file policy")
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise RecoveryApSessionError(f"private {label} changed while being read")
    return raw


def create_recovery_ap_session(
    *,
    output_dir: Path,
    ssh_keygen: Path,
    dropbearkey: Path,
) -> RecoveryApSession:
    if output_dir.exists() or output_dir.is_symlink():
        raise RecoveryApSessionError("refusing to overwrite a recovery-AP session")
    for tool, label in ((ssh_keygen, "ssh-keygen"), (dropbearkey, "dropbearkey")):
        if tool.is_symlink() or not tool.is_file() or not os.access(tool, os.X_OK):
            raise RecoveryApSessionError(f"{label} is not an executable regular file")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    temporary.chmod(0o700)
    try:
        media = temporary / "media/RECOVERY"
        host = temporary / "host"
        media.mkdir(parents=True, mode=0o700)
        host.mkdir(mode=0o700)
        identity = host / "identity"
        _run(
            [
                str(ssh_keygen),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "dcs6100-recovery",
                "-f",
                str(identity),
            ],
            "ssh-keygen",
        )
        try:
            authorized_key = read_authorized_key(identity.with_suffix(".pub"))
        except PrivateConfigError as exc:
            raise RecoveryApSessionError(str(exc)) from exc

        host_key = media / "HOST.KEY"
        _run(
            [str(dropbearkey), "-t", "ed25519", "-f", str(host_key)],
            "Dropbear host-key generation",
        )
        host_output = _run(
            [str(dropbearkey), "-y", "-f", str(host_key)],
            "Dropbear host-key inspection",
        )
        host_public_key = _public_key_from_output(host_output)
        companion_public_key = Path(str(host_key) + ".pub")
        if companion_public_key.exists():
            if companion_public_key.is_symlink() or not companion_public_key.is_file():
                raise RecoveryApSessionError("Dropbear companion public key is invalid")
            companion_public_key.unlink()
        ap_psk = (secrets.token_hex(32) + "\n").encode("ascii")
        setup_ssid = f"DCS6100-{ap_psk[:8].decode('ascii')}"
        station_mdns_name = f"dcs6100-{ap_psk[:8].decode('ascii')}.local"
        _write(media / "AP.PSK", ap_psk)
        _write(media / "AUTHORIZED.KEY", authorized_key)
        _write(
            host / "known_hosts",
            b"192.168.88.1 " + host_public_key,
        )
        manifest = {
            "ap_address": "192.168.88.1",
            "contains_secrets": True,
            "control": "forced-command-ed25519-ssh",
            "nor_writes": False,
            "schema_version": 1,
            "setup_ssid": setup_ssid,
            "station_mdns_name": station_mdns_name,
        }
        _write(
            host / "session.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("ascii"),
        )
        for path in temporary.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
        os.replace(temporary, output_dir)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return RecoveryApSession(output_dir=output_dir, setup_ssid=setup_ssid)


def materialize_recovery_ap_session(
    *,
    output_dir: Path,
    session_media_dir: Path,
    identity: Path,
    ssh_keygen: Path,
    dropbearkey: Path,
    service_credential: Path | None = None,
) -> RecoveryApSession:
    """Recreate private host access for one already embedded recovery session."""

    if output_dir.exists() or output_dir.is_symlink():
        raise RecoveryApSessionError("refusing to overwrite a recovery-AP session")
    if session_media_dir.is_symlink() or not session_media_dir.is_dir():
        raise RecoveryApSessionError("recovery session media directory is invalid")
    for tool, label in ((ssh_keygen, "ssh-keygen"), (dropbearkey, "dropbearkey")):
        if tool.is_symlink() or not tool.is_file() or not os.access(tool, os.X_OK):
            raise RecoveryApSessionError(f"{label} is not an executable regular file")

    identity_raw = _read_private(identity, "client identity", 16 * 1024)
    authorized_raw = _read_private(
        session_media_dir / "AUTHORIZED.KEY", "authorized key", 4096
    )
    ap_psk = _read_private(session_media_dir / "AP.PSK", "AP PSK", 128)
    host_key = _read_private(session_media_dir / "HOST.KEY", "host key", 16 * 1024)
    credential_raw = None
    if service_credential is not None:
        credential_raw = _read_private(
            service_credential, "service credential", 128
        )
        if (
            len(credential_raw) != 65
            or credential_raw[-1:] != b"\n"
            or any(
                character not in b"0123456789abcdef"
                for character in credential_raw[:-1]
            )
        ):
            raise RecoveryApSessionError("service credential framing is invalid")
    try:
        authorized_key = normalize_ed25519_authorized_key(authorized_raw)
    except ValueError as exc:
        raise RecoveryApSessionError(str(exc)) from exc
    identity_public = _public_key_from_output(
        _run([str(ssh_keygen), "-y", "-f", str(identity)], "client-key inspection")
    )
    if identity_public.split()[:2] != authorized_key.split()[:2]:
        raise RecoveryApSessionError("client identity is not authorized by this recovery session")
    if len(ap_psk) != 65 or ap_psk[-1:] != b"\n":
        raise RecoveryApSessionError("recovery AP PSK framing is invalid")
    try:
        psk_text = ap_psk[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise RecoveryApSessionError("recovery AP PSK is invalid") from exc
    if len(psk_text) != 64 or any(character not in "0123456789abcdef" for character in psk_text):
        raise RecoveryApSessionError("recovery AP PSK is invalid")
    host_public_key = _public_key_from_output(
        _run(
            [str(dropbearkey), "-y", "-f", str(session_media_dir / "HOST.KEY")],
            "Dropbear host-key inspection",
        )
    )
    setup_ssid = f"DCS6100-{psk_text[:8]}"
    station_mdns_name = f"{setup_ssid.lower()}.local"

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    temporary.chmod(0o700)
    try:
        media = temporary / "media/RECOVERY"
        host = temporary / "host"
        media.mkdir(parents=True, mode=0o700)
        host.mkdir(mode=0o700)
        _write(host / "identity", identity_raw)
        _write(host / "identity.pub", identity_public)
        _write(host / "known_hosts", b"192.168.88.1 " + host_public_key)
        if credential_raw is not None:
            _write(host / "service.credential", credential_raw)
        _write(media / "AUTHORIZED.KEY", authorized_key)
        _write(media / "AP.PSK", ap_psk)
        _write(media / "HOST.KEY", host_key)
        manifest = {
            "ap_address": "192.168.88.1",
            "contains_secrets": True,
            "control": "forced-command-ed25519-ssh",
            "nor_writes": False,
            "schema_version": 1,
            "setup_ssid": setup_ssid,
            "station_mdns_name": station_mdns_name,
        }
        _write(
            host / "session.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("ascii"),
        )
        os.replace(temporary, output_dir)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return RecoveryApSession(output_dir=output_dir, setup_ssid=setup_ssid)
