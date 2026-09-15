from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import tempfile
import tarfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer import cli as installer_cli
from installer.cli import build_parser
from installer.recovery_ap.host import (
    RecoveryNorState,
    RecoveryApHostError,
    _digest,
    _station_payload,
    activate_personal_mtd3,
    collect_runtime_snapshot,
    diagnose_thingino_failure,
    extract_camera_vendor_bundle,
    install_recovery_ap,
    install_personal_mtd3,
    reconcile_recovery_ap_state,
    load_host_session,
    load_service_credential,
    probe_recovery_ap,
    provision_recovery_ap,
    prove_thingino_health,
    resolve_recovery_ap_station,
    ssh_arguments,
)
from installer.mtd3_image import build_personal_mtd3_image
from installer.ram_boot import build_ramdisk_uimage
from installer.raptor_runtime_protocol import command as raptor_runtime_command


def squashfs_stub() -> bytes:
    raw = bytearray(4096)
    raw[:4] = b"hsqs"
    struct.pack_into("<Q", raw, 40, 96)
    return bytes(raw)


def private_session(root: Path) -> Path:
    session = root / "session"
    host = session / "host"
    host.mkdir(parents=True, mode=0o700)
    identity = host / "identity"
    identity.write_text("private-key-placeholder\n", encoding="ascii")
    identity.chmod(0o600)
    known_hosts = host / "known_hosts"
    known_hosts.write_text(
        "192.168.88.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGhoaGhoaGhoaGhoaGhoaGhoaGhoaGhoaGhoaGhoaGho\n",
        encoding="ascii",
    )
    known_hosts.chmod(0o600)
    manifest = host / "session.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "setup_ssid": "DCS6100-1234abcd",
                "station_mdns_name": "dcs6100-1234abcd.local",
            }
        ),
        encoding="ascii",
    )
    manifest.chmod(0o600)
    return session


class RecoveryApHostCompatibilityTests(unittest.TestCase):
    def test_private_digest_validator_keeps_host_exception_identity(self) -> None:
        self.assertEqual(_digest("a" * 64, "mtd3"), "a" * 64)
        with self.assertRaises(RecoveryApHostError):
            _digest("invalid", "mtd3")


def nor_report(*, kind: str, digest: str, payload: str = "-") -> bytes:
    return (
        "schema=1\n"
        "layout=dcs6100lhv2-a1-six-partition\n"
        "mtd1_kind=uimage\n"
        f"mtd1_sha256={'1' * 64}\n"
        "mtd2_kind=squashfs\n"
        f"mtd2_sha256={'2' * 64}\n"
        "mtd3_mounted=no\n"
        f"mtd3_kind={kind}\n"
        f"mtd3_sha256={digest}\n"
        f"mtd3_payload_sha256={payload}\n"
    ).encode("ascii")


class RecoveryApHostTests(unittest.TestCase):
    def test_service_credential_is_private_and_exactly_framed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            credential = session_dir / "host/service.credential"
            credential.write_bytes(b"a" * 64 + b"\n")
            credential.chmod(0o600)
            self.assertEqual(load_service_credential(session_dir), b"a" * 64)
            credential.write_bytes(b"a" * 64)
            with self.assertRaisesRegex(RecoveryApHostError, "credential is invalid"):
                load_service_credential(session_dir)

    def test_ssh_is_key_pinned_noninteractive_and_has_no_forwarding(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session = load_host_session(private_session(Path(name)))
            arguments = ssh_arguments(
                session,
                host="192.168.88.1",
                command="status",
            )
            joined = " ".join(arguments)
            self.assertIn("BatchMode=yes", joined)
            self.assertIn("StrictHostKeyChecking=yes", joined)
            self.assertIn("HostKeyAlias=192.168.88.1", joined)
            self.assertIn("ClearAllForwardings=yes", joined)
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(session, host="8.8.8.8", command="status")
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(session, host="192.168.88.1", command="sh")
            from installer.raptor_runtime_protocol import command as runtime_command

            raptor_command = runtime_command(
                "invoke", "a" * 32, "b" * 64, "status"
            )
            self.assertEqual(
                ssh_arguments(
                    session, host="192.168.88.1", command=raptor_command
                )[-1],
                raptor_command,
            )
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(
                    session,
                    host="192.168.88.1",
                    command="/bin/sh /run/thingino-runtime-candidate/activate.sh activate",
                )

    def test_uartless_session_allows_only_pinned_health_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            station_pin = Path(name) / "station_known_hosts"
            station_pin.write_text("pinned\n", encoding="ascii")
            session = replace(
                load_host_session(private_session(Path(name))),
                session_kind="uartless-functional-provisioning",
                camera_identity_sha256="a" * 64,
                transport_enabled=False,
                station_known_hosts=station_pin,
            )
            command = "thingino-health; sha256sum /dev/mtd3"
            arguments = ssh_arguments(
                session,
                host="198.51.100.23",
                command=command,
                allow_uartless_station_health=True,
            )
            self.assertEqual(arguments[-1], command)
            self.assertIn("StrictHostKeyChecking=yes", arguments)
            application = ssh_arguments(
                session, host="198.51.100.23", command="dlink-application-verify",
                allow_uartless_station_health=True,
            )
            self.assertEqual(application[-1], "dlink-application-verify")
            self.assertIn("StrictHostKeyChecking=yes", application)
            diagnostic = ssh_arguments(
                session, host="198.51.100.23", command="dlink-runtime-snapshot",
                allow_uartless_station_health=True,
            )
            self.assertEqual(diagnostic[-1], "dlink-runtime-snapshot")
            self.assertIn(f"UserKnownHostsFile={station_pin}", diagnostic)
            self.assertIn(f"HostKeyAlias={session.station_mdns_name}", diagnostic)
            self.assertIn("StrictHostKeyChecking=yes", diagnostic)
            for command in ("dlink-runtime-snapshot; reboot", "reboot", "sh"):
                with self.assertRaises(RecoveryApHostError):
                    ssh_arguments(session, host="198.51.100.23", command=command,
                                  allow_uartless_station_health=True)
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(session, host="198.51.100.23", command="dlink-runtime-snapshot")
            with self.assertRaisesRegex(RecoveryApHostError, "pin is missing"):
                ssh_arguments(replace(session, station_known_hosts=None),
                              host="198.51.100.23", command="dlink-runtime-snapshot",
                              allow_uartless_station_health=True)
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(
                    session, host="198.51.100.23", command="dlink-application-verify; reboot",
                    allow_uartless_station_health=True,
                )
            with self.assertRaisesRegex(
                RecoveryApHostError, "no recovery-AP transport"
            ):
                ssh_arguments(
                    session,
                    host="198.51.100.23",
                    command="status",
                    allow_uartless_station_health=True,
                )
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(
                    session,
                    host="192.168.88.1",
                    command=raptor_runtime_command(
                        "invoke", "a" * 32, "b" * 64, "status"
                    ) + "; reboot",
                )

    def test_runtime_snapshot_uses_session_specific_station_transport(self) -> None:
        for uartless in (False, True):
            with self.subTest(uartless=uartless), tempfile.TemporaryDirectory() as name:
                root = private_session(Path(name))
                session = replace(load_host_session(root),
                                  session_kind="uartless-functional-provisioning" if uartless else "recovery-ap",
                                  transport_enabled=not uartless)
                with patch("installer.recovery_ap.host.load_host_session", return_value=session), \
                     patch("installer.recovery_ap.host.resolve_recovery_ap_station", return_value=("camera.local", "198.51.100.23")) as resolve, \
                     patch("installer.recovery_ap.host._exchange", return_value=b'{"schema_version":1}') as exchange:
                    self.assertEqual(collect_runtime_snapshot(session_dir=root), {"schema_version": 1})
                    resolve.assert_called_once_with(root, allow_uartless_station=uartless)
                    exchange.assert_called_once_with(
                        session, host="198.51.100.23", command="dlink-runtime-snapshot",
                        timeout=90.0, allow_uartless_station_health=uartless,
                    )

    def test_uartless_raptor_transport_is_explicit_and_station_pinned(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"]).resolve(strict=True)) as name:
            pin = Path(name) / "station_known_hosts"
            pin.write_text("pinned\n", encoding="ascii")
            session = replace(load_host_session(private_session(Path(name))),
                              session_kind="uartless-functional-provisioning",
                              transport_enabled=False, station_known_hosts=pin)
            command = raptor_runtime_command("invoke", "a" * 32, "b" * 64, "status")
            arguments = ssh_arguments(session, host="198.51.100.23", command=command,
                                      allow_uartless_raptor_runtime=True)
            self.assertIn(f"UserKnownHostsFile={pin}", arguments)
            self.assertIn(f"HostKeyAlias={session.station_mdns_name}", arguments)
            self.assertIn("StrictHostKeyChecking=yes", arguments)
            for flags in ({}, {"allow_uartless_station_health": True}):
                with self.assertRaises(RecoveryApHostError):
                    ssh_arguments(session, host="198.51.100.23", command=command, **flags)
            for rejected in ("status", "reboot", "sh", "receive 1 " + "a" * 64,
                             "install-mtd3 " + "a" * 64,
                             "thingino-health; sha256sum /dev/mtd3", command + "; true"):
                with self.subTest(command=rejected), self.assertRaises(RecoveryApHostError):
                    ssh_arguments(session, host="198.51.100.23", command=rejected,
                                  allow_uartless_raptor_runtime=True)
            pin.unlink()
            with self.assertRaisesRegex(RecoveryApHostError, "pin is missing"):
                ssh_arguments(session, host="198.51.100.23", command=command,
                              allow_uartless_raptor_runtime=True)

    def test_raptor_exchange_errors_expose_only_safe_operation_metadata(self) -> None:
        from installer.recovery_ap.host import _exchange
        import subprocess
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"]).resolve(strict=True)) as name:
            session = load_host_session(private_session(Path(name)))
            secret = "private-credential-and-path"
            for values, label in (
                (("initialize", "a" * 32, "b" * 64, "c" * 64), "operation=initialize"),
                (("receive", "a" * 32, "baseline.sha256", "100", "b" * 64),
                 "operation=receive member=baseline.sha256"),
            ):
                command = raptor_runtime_command(*values)
                failures = (
                    (SimpleNamespace(returncode=2, stdout=secret.encode(), stderr=secret.encode()),
                     f"rejected ssh_returncode=2 command_bytes={len(command.encode())}"
                     f" stderr_bytes={len(secret.encode())} ssh_diagnostic=other"),
                    (subprocess.TimeoutExpired(secret, 20, output=secret, stderr=secret), "timeout"),
                    (OSError(secret), "transport-error"),
                )
                for failure, reason in failures:
                    options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                    with self.subTest(operation=values[0], reason=reason), \
                         patch("installer.recovery_ap.host.subprocess.run", **options), \
                         self.assertRaises(RecoveryApHostError) as raised:
                        _exchange(session, host="198.51.100.23", command=command, payload=secret.encode())
                    self.assertEqual(str(raised.exception), f"Raptor runtime {label} {reason}")
                    self.assertNotIn(secret, str(raised.exception))
                    self.assertNotIn("a" * 32, str(raised.exception))
                    if isinstance(failure, Exception):
                        self.assertTrue(raised.exception.__suppress_context__)
            with patch("installer.recovery_ap.host.subprocess.run", return_value=SimpleNamespace(
                    returncode=2, stdout=b"", stderr=secret.encode())), \
                 self.assertRaisesRegex(RecoveryApHostError, "^authenticated recovery-AP exchange was rejected$"):
                _exchange(session, host="198.51.100.23", command="status")

    def test_runtime_stderr_categories_are_bounded_and_never_echo_remote_text(self) -> None:
        from installer.recovery_ap.transport import _runtime_stderr_category
        for raw, expected in (
            (b"", "empty"),
            (b"Received disconnect from private-address: String too long", "ssh-string-too-long"),
            (b"Received disconnect from private-address: command too long private-secret", "command-too-long"),
            (b"exec request failed on channel 0 private-secret", "exec-request-failed"),
            (b"Connection closed by private-address port 22", "connection-closed"),
            (b"raptor_error=quarantine-rejected\n", "receiver-quarantine-rejected"),
            (b"private-secret" + b"x" * 4096 + b"command too long", "other"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(_runtime_stderr_category(raw), expected)

    def test_uartless_station_probe_is_one_shot_and_ap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(os.environ["TMPDIR"]).resolve(strict=True)) as name:
            session_dir = private_session(Path(name))
            pin = Path(name) / "station_known_hosts"
            pin.write_text("pinned\n", encoding="ascii")
            session = replace(load_host_session(session_dir),
                              session_kind="uartless-functional-provisioning",
                              transport_enabled=False, station_known_hosts=pin)
            def run(arguments, **kwargs):
                self.assertEqual(kwargs["timeout"], 20.0)
                self.assertEqual(len(kwargs["input"]), 4096)
                self.assertIn(f"UserKnownHostsFile={pin}", arguments)
                self.assertEqual(arguments[-1], "thingino-health; sha256sum /dev/mtd3")
                return SimpleNamespace(returncode=0, stderr=b"", stdout=(
                    f"healthy {session.station_mdns_name}\n".encode("ascii")
                    + kwargs["input"] + b"a" * 64 + b"  /dev/mtd3\n"))
            with patch("installer.recovery_ap.host.load_host_session", return_value=session), \
                 patch("installer.recovery_ap.host.subprocess.run", side_effect=run) as mocked:
                result = probe_recovery_ap(session_dir=session_dir, host="198.51.100.23",
                                           expected_state="station", allow_uartless_station=True)
                self.assertEqual(result["mtd3_sha256"], "a" * 64)
                mocked.assert_called_once()
                with self.assertRaises(RecoveryApHostError):
                    probe_recovery_ap(session_dir=session_dir, host="198.51.100.23",
                                      expected_state="ap", allow_uartless_station=True)
                mocked.assert_called_once()

    def test_raptor_ram_commands_preserve_pins_and_reject_modified_receiver(self) -> None:
        from installer.raptor_runtime_protocol import command as runtime_command
        with tempfile.TemporaryDirectory() as name:
            session = load_host_session(private_session(Path(name)))
            for values in [("initialize", "a" * 32, "b" * 64, "c" * 64),
                           ("receive", "a" * 32, "usr/bin/rvd", "1024", "b" * 64),
                           ("seal", "a" * 32, "b" * 64),
                           ("invoke", "a" * 32, "b" * 64, "start")]:
                command = runtime_command(*values)
                arguments = ssh_arguments(session, host="192.168.88.2", command=command)
                self.assertEqual(arguments[-1], command)
                for option in ("StrictHostKeyChecking=yes", "HostKeyAlias=192.168.88.1",
                               "ClearAllForwardings=yes", "PasswordAuthentication=no",
                               f"UserKnownHostsFile={session.known_hosts}"):
                    self.assertIn(option, arguments)
                with self.assertRaises(RecoveryApHostError):
                    ssh_arguments(session, host="192.168.88.2", command=command + "; true")
                with self.assertRaises(RecoveryApHostError):
                    ssh_arguments(replace(session, transport_enabled=False),
                                  host="192.168.88.2", command=command)

    def test_recovery_image_install_is_validated_uploaded_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session_dir = private_session(root)
            image = build_ramdisk_uimage(squashfs_stub())
            image_path = root / "recovery.uimage"
            image_path.write_bytes(image)
            commands: list[str] = []

            def run(arguments, **kwargs):
                command = arguments[-1]
                commands.append(command)
                if command.startswith("receive "):
                    self.assertEqual(kwargs["input"], image)
                    output = b"accepted\n"
                elif command.startswith("install-recovery "):
                    self.assertEqual(kwargs["input"], b"")
                    output = b"installed\n"
                else:
                    raise AssertionError(command)
                return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = install_recovery_ap(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_path=image_path,
                )
            digest = hashlib.sha256(image).hexdigest()
            self.assertEqual(
                commands,
                [f"receive {len(image)} {digest}", f"install-recovery {digest}"],
            )
            self.assertTrue(result["read_back_verified"])
            self.assertFalse(result["nor_writes"])

    def test_probe_proves_control_and_hash_checked_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            transferred = b""

            def run(arguments, **kwargs):
                nonlocal transferred
                command = arguments[-1]
                if command == "status":
                    output = b"ap\n"
                elif command.startswith("receive "):
                    transferred = kwargs["input"]
                    size, digest = command.split()[1:]
                    self.assertEqual(int(size), len(transferred))
                    self.assertEqual(digest, hashlib.sha256(transferred).hexdigest())
                    output = b"accepted\n"
                elif command.startswith("send "):
                    output = transferred
                else:
                    raise AssertionError(command)
                return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = probe_recovery_ap(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    expected_state="ap",
                )
            self.assertTrue(result["authenticated_control"])
            self.assertEqual(result["file_transfer_bytes"], 4096)

    def test_station_probe_uses_the_installed_health_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            mtd3_sha256 = "a" * 64

            def run(arguments, **kwargs):
                self.assertEqual(
                    arguments[-1], "thingino-health; sha256sum /dev/mtd3"
                )
                payload = kwargs["input"]
                self.assertEqual(len(payload), 4096)
                output = (
                    b"healthy dcs6100-1234abcd.local\n"
                    + payload
                    + f"{mtd3_sha256}  /dev/mtd3\n".encode("ascii")
                )
                return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = probe_recovery_ap(
                    session_dir=session_dir,
                    host="203.0.113.103",
                    expected_state="station",
                )
            self.assertEqual(result["state"], "station")
            self.assertTrue(result["mtd3_read_back_verified"])
            self.assertEqual(result["mtd3_sha256"], mtd3_sha256)
            self.assertFalse(result["nor_writes"])

    def test_retired_failure_diagnostics_fail_closed_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            with patch("installer.recovery_ap.host.subprocess.run") as run:
                with self.assertRaisesRegex(RecoveryApHostError, "retired"):
                    diagnose_thingino_failure(
                        session_dir=session_dir,
                        host="192.168.88.1",
                    )
            run.assert_not_called()

    def test_provisioning_derives_psk_and_uses_stdin_only(self) -> None:
        ssid = "Private Camera Lab"
        passphrase = "not-in-command-line"
        payload = _station_payload(ssid, passphrase)
        self.assertNotIn(ssid.encode(), payload)
        self.assertNotIn(passphrase.encode(), payload)
        lines = payload.splitlines()
        self.assertEqual(bytes.fromhex(lines[0].decode()), ssid.encode())
        self.assertEqual(len(lines[1]), 64)
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))

            def run(arguments, **kwargs):
                self.assertEqual(arguments[-1], "provision")
                self.assertNotIn(ssid, " ".join(arguments))
                self.assertNotIn(passphrase, " ".join(arguments))
                self.assertEqual(kwargs["input"], payload)
                return SimpleNamespace(
                    returncode=0,
                    stdout=b"accepted\n",
                    stderr=b"",
                )

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = provision_recovery_ap(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    ssid=ssid,
                    passphrase=passphrase,
                )
            self.assertFalse(result["credentials_logged"])

    def test_group_readable_private_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            (session_dir / "host/identity").chmod(0o640)
            with self.assertRaisesRegex(RecoveryApHostError, "file policy"):
                load_host_session(session_dir)

    def test_station_discovery_is_session_pinned_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            answers = [
                (
                    2,
                    1,
                    6,
                    "",
                    ("198.51.100.23", 22),
                )
            ]
            with patch(
                "installer.recovery_ap.host.socket.getaddrinfo",
                return_value=answers,
            ) as resolver:
                mdns_name, address = resolve_recovery_ap_station(session_dir)
            self.assertEqual(mdns_name, "dcs6100-1234abcd.local")
            self.assertEqual(address, "198.51.100.23")
            self.assertEqual(resolver.call_args.args[0], mdns_name)

    def test_station_discovery_rejects_public_or_ambiguous_answers(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            public = [(2, 1, 6, "", ("8.8.8.8", 22))]
            with patch(
                "installer.recovery_ap.host.socket.getaddrinfo",
                return_value=public,
            ), self.assertRaises(RecoveryApHostError):
                resolve_recovery_ap_station(session_dir)
            ambiguous = [
                (2, 1, 6, "", ("192.0.2.20", 22)),
                (2, 1, 6, "", ("192.0.2.21", 22)),
            ]
            with patch(
                "installer.recovery_ap.host.socket.getaddrinfo",
                return_value=ambiguous,
            ), self.assertRaises(RecoveryApHostError):
                resolve_recovery_ap_station(session_dir)

    def test_personal_mtd3_is_validated_uploaded_written_and_activated(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session_dir = private_session(root)
            image = build_personal_mtd3_image(squashfs_stub())
            image_path = root / "personal-mtd3.bin"
            image_path.write_bytes(image.raw)
            commands: list[str] = []

            def run(arguments, **kwargs):
                command = arguments[-1]
                commands.append(command)
                if command == "inspect-nor":
                    output = nor_report(kind="unknown", digest="4" * 64)
                elif command == "status":
                    output = b"ap\n"
                elif command.startswith("receive "):
                    self.assertEqual(kwargs["input"], image.raw)
                    output = b"accepted\n"
                elif command.startswith("install-mtd3 "):
                    output = b"installed\n"
                elif command.startswith("activate-mtd3 "):
                    output = b"activating\n"
                else:
                    raise AssertionError(command)
                return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run), patch(
                "installer.recovery_ap.host._validate_image_vendor_binding",
                return_value="3" * 64,
            ):
                result = install_personal_mtd3(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_path=image_path,
                )
                activate = activate_personal_mtd3(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_sha256=image.image_sha256,
                )
            self.assertEqual(result["written_mtd"], [3])
            self.assertTrue(result["read_back_verified"])
            self.assertTrue(activate["activation_requested"])
            self.assertEqual(
                commands,
                [
                    "inspect-nor",
                    "status",
                    f"receive {len(image.raw)} {image.image_sha256}",
                    f"install-mtd3 {image.image_sha256}",
                    f"activate-mtd3 {image.image_sha256}",
                ],
            )

    def test_matching_personal_mtd3_is_not_uploaded_or_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session_dir = private_session(root)
            image = build_personal_mtd3_image(squashfs_stub())
            image_path = root / "personal-mtd3.bin"
            image_path.write_bytes(image.raw)
            commands: list[str] = []

            def run(arguments, **kwargs):
                command = arguments[-1]
                commands.append(command)
                if command == "inspect-nor":
                    output = nor_report(
                        kind="personal",
                        digest=image.image_sha256,
                        payload=image.payload_sha256,
                    )
                elif command == "status":
                    output = b"ap\n"
                else:
                    raise AssertionError(command)
                return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = install_personal_mtd3(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_path=image_path,
                )
            self.assertTrue(result["already_installed"])
            self.assertEqual(result["written_mtd"], [])
            self.assertEqual(result["safe_next_action"], "activate_existing")
            self.assertEqual(commands, ["inspect-nor", "status"])

    def test_reconcile_actions_fail_closed_from_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session_dir = private_session(root)
            image = build_personal_mtd3_image(squashfs_stub())
            image_path = root / "personal-mtd3.bin"
            image_path.write_bytes(image.raw)
            base = RecoveryNorState(
                layout="dcs6100lhv2-a1-six-partition",
                mtd1_kind="uimage",
                mtd1_sha256="1" * 64,
                mtd2_kind="squashfs",
                mtd2_sha256="2" * 64,
                mtd3_mounted=False,
                mtd3_kind="stock",
                mtd3_sha256="3" * 64,
                mtd3_payload_sha256=None,
            )
            with patch(
                "installer.recovery_ap.host.inspect_recovery_ap_nor", return_value=base
            ), patch("installer.recovery_ap.host._exchange", return_value=b"ap\n"):
                stock = reconcile_recovery_ap_state(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_path=image_path,
                )
            self.assertEqual(stock["safe_next_action"], "extract_vendor")

            damaged = RecoveryNorState(
                base.layout, base.mtd1_kind, base.mtd1_sha256,
                base.mtd2_kind, base.mtd2_sha256, False,
                "unknown", base.mtd3_sha256, None
            )
            with patch(
                "installer.recovery_ap.host.inspect_recovery_ap_nor", return_value=damaged
            ), patch("installer.recovery_ap.host._exchange", return_value=b"ap\n"), patch(
                "installer.recovery_ap.host._validate_image_vendor_binding",
                return_value="4" * 64,
            ):
                retry = reconcile_recovery_ap_state(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_path=image_path,
                )
            self.assertEqual(retry["safe_next_action"], "install_mtd3")

            unknown_layout = RecoveryNorState(
                "unknown", None, None, None, None, None, None, None, None
            )
            with patch(
                "installer.recovery_ap.host.inspect_recovery_ap_nor",
                return_value=unknown_layout,
            ), patch("installer.recovery_ap.host._exchange", return_value=b"ap\n"):
                stopped = reconcile_recovery_ap_state(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    image_path=image_path,
                )
            self.assertEqual(stopped["safe_next_action"], "stop_unknown_layout")

    def test_reconcile_bootstrap_and_runtime_actions(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session_dir = private_session(root)
            image = build_personal_mtd3_image(squashfs_stub())
            image_path = root / "personal-mtd3.bin"
            image_path.write_bytes(image.raw)
            exact = RecoveryNorState(
                "dcs6100lhv2-a1-six-partition",
                "uimage",
                "1" * 64,
                "squashfs",
                "2" * 64,
                False,
                "personal",
                image.image_sha256,
                image.payload_sha256,
            )
            cases = (
                (replace(exact, mtd1_kind="unknown"), b"ap\n", "reinstall_bootstrap"),
                (replace(exact, mtd3_mounted=True), b"ap\n", "stop_mtd3_busy"),
                (replace(exact, mtd3_sha256="3" * 64), b"ap\n", "stop_unknown_mtd3"),
                (exact, b"thingino\n", "boot_and_verify"),
            )
            for state, runtime, expected in cases:
                with self.subTest(expected=expected), patch(
                    "installer.recovery_ap.host.inspect_recovery_ap_nor",
                    return_value=state,
                ), patch("installer.recovery_ap.host._exchange", return_value=runtime):
                    decision = reconcile_recovery_ap_state(
                        session_dir=session_dir,
                        host="192.168.88.1",
                        image_path=image_path,
                    )
                self.assertEqual(decision["safe_next_action"], expected)

    def test_cli_exposes_read_only_reconciliation(self) -> None:
        if not installer_cli.LEGACY_INSTALLER_AVAILABLE:
            self.skipTest("legacy personal installer is not exported")
        arguments = build_parser().parse_args(
            [
                "reconcile-camera-state",
                "--session-dir",
                "session",
                "--image",
                "personal-mtd3.bin",
                "--image-provenance",
                "final-root.private.json",
            ]
        )
        self.assertEqual(arguments.host, "192.168.88.1")

    def test_cli_exposes_read_only_recovery_state_inspection(self) -> None:
        arguments = build_parser().parse_args(
            ["inspect-recovery-state", "--session-dir", "session"]
        )
        self.assertEqual(arguments.host, "192.168.88.1")

    def test_camera_vendor_export_is_hash_checked_and_materialized_privately(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session_dir = private_session(root)
            payload = b"camera-local-vendor"
            archive_buffer = io.BytesIO()
            with tarfile.open(fileobj=archive_buffer, mode="w:") as archive:
                info = tarfile.TarInfo("./libimp.so")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            archive = archive_buffer.getvalue()
            digest = hashlib.sha256(archive).hexdigest()
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "target": {"model": "test"},
                        "source": {
                            "firmware_version": "1.02.02",
                            "partition": {"mtd": 3},
                        },
                        "files": [
                            {
                                "name": "libimp.so",
                                "required": True,
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "size": len(payload),
                                "source_path": "lib/libimp.so",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            artifact = SimpleNamespace(name="libimp.so")
            bundle = SimpleNamespace(bundle_sha256="b" * 64, artifacts=[artifact])
            output = root / "vendor-bundle"
            with patch(
                "installer.recovery_ap.host._exchange",
                side_effect=[
                    f"vendor {len(archive)} {digest}\n".encode(),
                    archive,
                ],
            ), patch("installer.recovery_ap.host.CATALOG_PATH", catalog), patch(
                "installer.recovery_ap.host.load_vendor_bundle", return_value=bundle
            ):
                result = extract_camera_vendor_bundle(
                    session_dir=session_dir,
                    host="192.168.88.1",
                    output_dir=output,
                )
            self.assertEqual((output / "files/libimp.so").read_bytes(), payload)
            self.assertEqual(result["bundle_sha256"], "b" * 64)
            self.assertTrue(result["mounted_read_only"])
            self.assertFalse(result["nor_writes"])

    def test_final_health_uses_same_session_pin_and_unique_mdns_identity(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))

            def run(arguments, **kwargs):
                self.assertEqual(
                    arguments[-1], "thingino-health; sha256sum /dev/mtd3"
                )
                self.assertEqual(len(kwargs["input"]), 4096)
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        b"healthy dcs6100-1234abcd.local\n"
                        + kwargs["input"]
                        + b"a" * 64
                        + b"  /dev/mtd3\n"
                    ),
                    stderr=b"",
                )

            with patch(
                "installer.recovery_ap.host.resolve_recovery_ap_station",
                return_value=("dcs6100-1234abcd.local", "198.51.100.23"),
            ), patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = prove_thingino_health(session_dir=session_dir)
            self.assertEqual(result["health_gate"], "passed")
            self.assertEqual(result["station_ipv4"], "198.51.100.23")
            self.assertEqual(result["mdns_resolution"], "session-pinned")
            self.assertEqual(result["ssh_authentication"], "pinned-key-only")
            self.assertEqual(result["file_transfer_bytes"], 4096)
            self.assertTrue(result["mtd3_read_back_verified"])
            self.assertEqual(result["mtd3_sha256"], "a" * 64)

    def test_final_health_selects_one_mdns_candidate_by_expected_mtd3(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            payload = b"R" * 4096

            def exchange(_session, *, host, **_kwargs):
                digest = b"a" * 64 if host.endswith(".23") else b"b" * 64
                return (
                    b"healthy dcs6100-1234abcd.local\n"
                    + payload
                    + digest
                    + b"  /dev/mtd3\n"
                )

            with patch(
                "installer.recovery_ap.host.resolve_recovery_ap_station_candidates",
                return_value=(
                    "dcs6100-1234abcd.local",
                    ("198.51.100.23", "198.51.100.24"),
                ),
            ), patch(
                "installer.recovery_ap.host.secrets.token_bytes",
                return_value=payload,
            ), patch(
                "installer.recovery_ap.host._exchange",
                side_effect=exchange,
            ):
                result = prove_thingino_health(
                    session_dir=session_dir,
                    expected_mtd3_sha256="b" * 64,
                )
            self.assertEqual(result["station_ipv4"], "198.51.100.24")
            self.assertEqual(result["mtd3_sha256"], "b" * 64)

    def test_final_health_retries_until_management_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            expected = (
                b"healthy dcs6100-1234abcd.local\n"
                + b"R" * 4096
                + b"a" * 64
                + b"  /dev/mtd3\n"
            )
            with patch(
                "installer.recovery_ap.host.resolve_recovery_ap_station",
                return_value=("dcs6100-1234abcd.local", "198.51.100.23"),
            ), patch(
                "installer.recovery_ap.host.secrets.token_bytes",
                return_value=b"R" * 4096,
            ), patch(
                "installer.recovery_ap.host._exchange",
                side_effect=[RecoveryApHostError("not ready"), expected],
            ) as exchange, patch("installer.recovery_ap.host.time.sleep") as sleep:
                result = prove_thingino_health(session_dir=session_dir)
            self.assertEqual(exchange.call_count, 2)
            sleep.assert_called_once_with(1.0)
            self.assertEqual(result["health_gate"], "passed")

    def test_final_health_allows_cold_install_to_exceed_ninety_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            payload = b"R" * 4096
            expected = (
                b"healthy dcs6100-1234abcd.local\n"
                + payload + b"a" * 64 + b"  /dev/mtd3\n"
            )
            with patch(
                "installer.recovery_ap.host.resolve_recovery_ap_station",
                return_value=("dcs6100-1234abcd.local", "198.51.100.23"),
            ), patch(
                "installer.recovery_ap.host.secrets.token_bytes", return_value=payload,
            ), patch(
                "installer.recovery_ap.host._exchange",
                side_effect=[RecoveryApHostError("booting"), expected],
            ) as exchange, patch(
                "installer.recovery_ap.host.time.monotonic", side_effect=[0.0, 100.0],
            ), patch("installer.recovery_ap.host.time.sleep"):
                result = prove_thingino_health(session_dir=session_dir)
            self.assertEqual(exchange.call_count, 2)
            self.assertEqual(result["health_gate"], "passed")

    def test_final_health_still_stops_after_five_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            with patch(
                "installer.recovery_ap.host.resolve_recovery_ap_station",
                return_value=("dcs6100-1234abcd.local", "198.51.100.23"),
            ), patch(
                "installer.recovery_ap.host._exchange",
                side_effect=RecoveryApHostError("not ready"),
            ) as exchange, patch(
                "installer.recovery_ap.host.time.monotonic", side_effect=[0.0, 300.0],
            ), patch("installer.recovery_ap.host.time.sleep") as sleep:
                with self.assertRaisesRegex(RecoveryApHostError, "health timed out"):
                    prove_thingino_health(session_dir=session_dir)
            self.assertEqual(exchange.call_count, 1)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
