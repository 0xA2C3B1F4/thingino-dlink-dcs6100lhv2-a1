from __future__ import annotations

import hashlib
import io
import json
import struct
import tempfile
import tarfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer.cli import build_parser
from installer.recovery_ap.host import (
    RUNTIME_ACTIVATE_COMMAND,
    RUNTIME_RECEIVE_COMMAND,
    RUNTIME_ROLLBACK_COMMAND,
    RUNTIME_STATUS_COMMAND,
    RecoveryNorState,
    RecoveryApHostError,
    _digest,
    _station_payload,
    activate_personal_mtd3,
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
            for command in (
                RUNTIME_RECEIVE_COMMAND,
                RUNTIME_ACTIVATE_COMMAND,
                RUNTIME_STATUS_COMMAND,
                RUNTIME_ROLLBACK_COMMAND,
            ):
                self.assertEqual(
                    ssh_arguments(session, host="192.168.88.1", command=command)[-1],
                    command,
                )
            self.assertIn('awk \'$2 == "/run"', RUNTIME_RECEIVE_COMMAND)
            self.assertIn("|| exit 1", RUNTIME_RECEIVE_COMMAND)

    def test_uartless_session_allows_only_pinned_final_health_transport(self) -> None:
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
                    command=RUNTIME_ACTIVATE_COMMAND + "; reboot",
                )

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

    def test_failure_diagnostics_are_authenticated_bounded_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            log = b"IMP_ISP_Open failed\n"
            report = (
                b"schema=1\n"
                b"thingino_mounts=absent\n"
                b"cleanup=complete\n"
                + f"prudynt_log_size={len(log)}\n".encode("ascii")
                + f"prudynt_log_sha256={hashlib.sha256(log).hexdigest()}\n".encode("ascii")
                + b"prudynt_log_tail_begin\n"
                + log.rstrip(b"\n")
                + b"\nprudynt_log_tail_end\n"
            )

            def run(arguments, **_kwargs):
                self.assertEqual(arguments[-1], "thingino-failure")
                return SimpleNamespace(returncode=0, stdout=report, stderr=b"")

            with patch("installer.recovery_ap.host.subprocess.run", side_effect=run):
                result = diagnose_thingino_failure(
                    session_dir=session_dir,
                    host="192.168.88.1",
                )
            self.assertEqual(result["cleanup"], "complete")
            self.assertEqual(result["thingino_mounts"], "absent")
            self.assertEqual(result["prudynt_log_tail"], "IMP_ISP_Open failed")
            self.assertFalse(result["nor_writes"])

    def test_failure_diagnostics_reject_malformed_framing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            session_dir = private_session(Path(name))
            with patch(
                "installer.recovery_ap.host.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout=b"schema=1\nthingino_mounts=present\n",
                    stderr=b"",
                ),
            ), self.assertRaisesRegex(RecoveryApHostError, "framing"):
                diagnose_thingino_failure(
                    session_dir=session_dir,
                    host="192.168.88.1",
                )

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
