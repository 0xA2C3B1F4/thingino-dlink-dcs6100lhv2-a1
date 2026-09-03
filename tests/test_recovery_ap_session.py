from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from installer.recovery_ap.host import (
    RecoveryApHostError,
    load_host_session,
    load_service_credential,
    resolve_recovery_ap_station_candidates,
)
from installer.recovery_ap.session import (
    RecoveryApSessionError,
    create_recovery_ap_session,
    ensure_uartless_provisioning_session,
    materialize_recovery_ap_session,
)


def fake_dropbearkey(path: Path) -> None:
    algorithm = b"ssh-ed25519"
    public = b"h" * 32
    blob = (
        len(algorithm).to_bytes(4, "big")
        + algorithm
        + len(public).to_bytes(4, "big")
        + public
    )
    encoded = base64.b64encode(blob).decode("ascii")
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "if '-y' in sys.argv:\n"
        f"    print('ssh-ed25519 {encoded} generated-host')\n"
        "else:\n"
        "    target = pathlib.Path(sys.argv[sys.argv.index('-f') + 1])\n"
        "    target.write_bytes(b'dropbear-ed25519-private')\n"
        "    pathlib.Path(str(target) + '.pub').write_text('generated')\n",
        encoding="ascii",
    )
    path.chmod(0o700)


class RecoveryApSessionTests(unittest.TestCase):
    def test_uartless_provisioning_session_is_local_only_and_idempotent(self) -> None:
        ssh_keygen = shutil.which("ssh-keygen")
        if ssh_keygen is None:
            self.skipTest("ssh-keygen is unavailable")
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "session"
            camera_identity = "a" * 64
            first = ensure_uartless_provisioning_session(
                output_dir=output,
                ssh_keygen=Path(ssh_keygen),
                camera_identity_sha256=camera_identity,
            )
            second = ensure_uartless_provisioning_session(
                output_dir=output,
                ssh_keygen=Path(ssh_keygen),
                camera_identity_sha256=camera_identity,
            )
            self.assertEqual(first, second)
            session = load_host_session(output)
            self.assertEqual(
                session.session_kind, "uartless-functional-provisioning"
            )
            self.assertEqual(session.camera_identity_sha256, camera_identity)
            self.assertFalse(session.transport_enabled)
            self.assertEqual(len(load_service_credential(output)), 64)
            self.assertFalse((output / "media").exists())
            with self.assertRaisesRegex(
                RecoveryApHostError, "no recovery-AP transport"
            ):
                resolve_recovery_ap_station_candidates(output)
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o077, 0)
            with self.assertRaisesRegex(
                RecoveryApSessionError, "bound elsewhere"
            ):
                ensure_uartless_provisioning_session(
                    output_dir=output,
                    ssh_keygen=Path(ssh_keygen),
                    camera_identity_sha256="b" * 64,
                )

    def test_private_session_has_unique_ap_and_pinned_key_material(self) -> None:
        ssh_keygen = shutil.which("ssh-keygen")
        if ssh_keygen is None:
            self.skipTest("ssh-keygen is unavailable")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            dropbearkey = root / "dropbearkey"
            fake_dropbearkey(dropbearkey)
            output = root / "session"
            result = create_recovery_ap_session(
                output_dir=output,
                ssh_keygen=Path(ssh_keygen),
                dropbearkey=dropbearkey,
            )
            self.assertRegex(result.setup_ssid, r"^DCS6100-[0-9a-f]{8}$")
            self.assertEqual(output.stat().st_mode & 0o077, 0)
            expected = {
                "host/identity",
                "host/identity.pub",
                "host/known_hosts",
                "host/session.json",
                "media/RECOVERY/AP.PSK",
                "media/RECOVERY/AUTHORIZED.KEY",
                "media/RECOVERY/HOST.KEY",
            }
            actual = {
                str(path.relative_to(output))
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)
            self.assertTrue(
                (output / "host/known_hosts")
                .read_text(encoding="ascii")
                .startswith("192.168.88.1 ssh-ed25519 ")
            )
            ap_psk = (output / "media/RECOVERY/AP.PSK").read_text(
                encoding="ascii"
            )
            self.assertRegex(ap_psk, r"^[0-9a-f]{64}\n$")
            manifest = json.loads((output / "host/session.json").read_text())
            self.assertTrue(manifest["contains_secrets"])
            self.assertNotIn(ap_psk.strip(), json.dumps(manifest))
            self.assertEqual(
                manifest["station_mdns_name"],
                result.setup_ssid.lower() + ".local",
            )
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_existing_output_and_symlinked_tool_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "existing"
            output.mkdir()
            linked = root / "linked"
            linked.symlink_to("/usr/bin/true")
            with self.assertRaises(RecoveryApSessionError):
                create_recovery_ap_session(
                    output_dir=output,
                    ssh_keygen=linked,
                    dropbearkey=Path("/usr/bin/true"),
                )

    def test_materializes_host_session_from_embedded_media_and_matching_identity(self) -> None:
        ssh_keygen = shutil.which("ssh-keygen")
        if ssh_keygen is None:
            self.skipTest("ssh-keygen is unavailable")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            dropbearkey = root / "dropbearkey"
            fake_dropbearkey(dropbearkey)
            original = create_recovery_ap_session(
                output_dir=root / "original",
                ssh_keygen=Path(ssh_keygen),
                dropbearkey=dropbearkey,
            )
            output = root / "materialized"
            credential = root / "service.credential"
            credential.write_text("a" * 64 + "\n")
            credential.chmod(0o600)
            result = materialize_recovery_ap_session(
                output_dir=output,
                session_media_dir=original.output_dir / "media/RECOVERY",
                identity=original.output_dir / "host/identity",
                ssh_keygen=Path(ssh_keygen),
                dropbearkey=dropbearkey,
                service_credential=credential,
            )
            self.assertEqual(result.setup_ssid, original.setup_ssid)
            self.assertEqual(
                (output / "host/known_hosts").read_bytes(),
                (original.output_dir / "host/known_hosts").read_bytes(),
            )
            self.assertEqual(
                (output / "media/RECOVERY/HOST.KEY").read_bytes(),
                (original.output_dir / "media/RECOVERY/HOST.KEY").read_bytes(),
            )
            self.assertEqual(
                (output / "host/service.credential").read_bytes(),
                b"a" * 64 + b"\n",
            )
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_materialization_rejects_wrong_identity(self) -> None:
        ssh_keygen = shutil.which("ssh-keygen")
        if ssh_keygen is None:
            self.skipTest("ssh-keygen is unavailable")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            dropbearkey = root / "dropbearkey"
            fake_dropbearkey(dropbearkey)
            first = create_recovery_ap_session(
                output_dir=root / "first",
                ssh_keygen=Path(ssh_keygen),
                dropbearkey=dropbearkey,
            )
            second = create_recovery_ap_session(
                output_dir=root / "second",
                ssh_keygen=Path(ssh_keygen),
                dropbearkey=dropbearkey,
            )
            with self.assertRaisesRegex(RecoveryApSessionError, "not authorized"):
                materialize_recovery_ap_session(
                    output_dir=root / "rejected",
                    session_media_dir=first.output_dir / "media/RECOVERY",
                    identity=second.output_dir / "host/identity",
                    ssh_keygen=Path(ssh_keygen),
                    dropbearkey=dropbearkey,
                )

    def test_materialization_rejects_malformed_service_credential(self) -> None:
        ssh_keygen = shutil.which("ssh-keygen")
        if ssh_keygen is None:
            self.skipTest("ssh-keygen is unavailable")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            dropbearkey = root / "dropbearkey"
            fake_dropbearkey(dropbearkey)
            original = create_recovery_ap_session(
                output_dir=root / "original",
                ssh_keygen=Path(ssh_keygen),
                dropbearkey=dropbearkey,
            )
            credential = root / "service.credential"
            credential.write_text("not-a-service-credential\n")
            credential.chmod(0o600)
            with self.assertRaisesRegex(RecoveryApSessionError, "framing"):
                materialize_recovery_ap_session(
                    output_dir=root / "rejected",
                    session_media_dir=original.output_dir / "media/RECOVERY",
                    identity=original.output_dir / "host/identity",
                    ssh_keygen=Path(ssh_keygen),
                    dropbearkey=dropbearkey,
                    service_credential=credential,
                )


if __name__ == "__main__":
    unittest.main()
