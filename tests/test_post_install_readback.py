import argparse
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer import post_install_readback, user_cli
from installer.camera_setup import OperationError
from installer.final_bundle import (
    build_universal_final_bundle,
    ensure_ed25519_keypair,
    render_final_kernel_fragment,
)
from installer.install_policy import universal_physical_write_policy
from installer.install_project import ProjectError
from installer.layout import TARGET
from installer.mtd3_split import final_kernel_command_line, derive_final_layout
from installer.recovery_ap.host import RecoveryApHostError, RecoveryApHostSession, ssh_arguments
from installer.sd_package import generate_bootstrap
from installer.stage1.build import BOOTSTRAP_FILENAME
from installer.stage2 import FILENAME as STAGE2_FILENAME, build_stage2, validate_stage2
from tests.test_artifacts import test_squashfs, test_uimage


BOOT_ID = "12345678-1234-5678-9abc-123456789abc"
HASHES = {0: "0" * 64, 1: "1" * 64, 3: "3" * 64, 5: "5" * 64, 6: "6" * 64}


def actual_inputs(root: Path):
    system = test_squashfs(64)
    kernel = test_uimage(final_kernel_command_line(derive_final_layout(len(system))).encode("ascii"))
    bootstrap_root = test_squashfs()
    keys = ensure_ed25519_keypair(root / "keys/key.pem")
    public_key = Path(keys["public_key"])
    bundle = build_universal_final_bundle(
        kernel=kernel, bootstrap_rootfs=bootstrap_root, system_rootfs=system,
        linux_config=render_final_kernel_fragment(len(system)),
        signing_key=Path(keys["private_key"]),
    )
    bootstrap = generate_bootstrap(kernel, bootstrap_root)
    stage2 = build_stage2(final_kernel=kernel, system_rootfs=system)
    payload = validate_stage2(stage2)
    install = root / "install-set"
    install.mkdir()
    manifest = {
        "schema_version": 2,
        "artifact_scope": "model-universal",
        "provisioning": "separate-per-camera-audit-and-jffs2",
        "universal_firmware_sha256": hashlib.sha256(bundle).hexdigest(),
        "physical_write_policy": universal_physical_write_policy("initialize"),
        "target": {"hardware_revision": "A1", "model": "DCS-6100LHV2"},
        "layout": {
            "abi": "dcs6100lhv2-a1-mtd3-split-v1", "parent_physical_mtd": 3,
            "parent_offset": TARGET.partition(3).offset,
            "parent_span": TARGET.partition(3).size,
            "system_offset": payload.system_flash_offset,
            "system_span": payload.system_flash_span,
            "data_offset": payload.data_flash_offset, "data_span": payload.data_flash_span,
            "preserved_physical_mtd": [0, 4, 5], "data_mode": "initialize",
        },
        "region_policy": {
            "system": {"filesystem": "squashfs", "write": "erase-write-readback",
                       "sha256": hashlib.sha256(system).hexdigest(), "payload_size": len(system)},
            "data": {"filesystem": "jffs2",
                     "initialize": "camera-authorized-jffs2-erase-write-readback",
                     "preserve": "before-and-after-complete-region-sha256",
                     "factory_reset": "explicit-data-only-erase",
                     "corrupt": "preserve-and-require-explicit-recovery"},
            "activation": {"region": "kernel-first-64KiB", "written_last": True},
        },
        "artifacts": {
            BOOTSTRAP_FILENAME: {"size": len(bootstrap),
                                 "sha256": hashlib.sha256(bootstrap).hexdigest()},
            STAGE2_FILENAME: {"size": len(stage2),
                              "sha256": hashlib.sha256(stage2).hexdigest()},
            "stage1-bootstrap.squashfs": {"size": len(bootstrap_root),
                                           "sha256": hashlib.sha256(bootstrap_root).hexdigest()},
            "thingino-universal.tgb": {"size": len(bundle),
                                        "sha256": hashlib.sha256(bundle).hexdigest()},
        },
    }
    files = {
        BOOTSTRAP_FILENAME: bootstrap, STAGE2_FILENAME: stage2,
        "install-set.manifest.json": json.dumps(manifest).encode(),
        "stage1-bootstrap.squashfs": bootstrap_root, "thingino-universal.tgb": bundle,
    }
    for name, raw in files.items():
        (install / name).write_bytes(raw)

    preserved = root / "preserved"
    preserved.mkdir()
    identity = hashlib.sha256(b"thingino-dcs6100-camera-identity-v1\0")
    identity.update(TARGET.model.encode("ascii") + b"\0")
    identity.update(TARGET.hardware_revision.encode("ascii") + b"\0")
    identity.update(TARGET.nor_size.to_bytes(8, "big"))
    for index in (0, 4, 5):
        raw = bytes((index + 1,)) * TARGET.partition(index).size
        (preserved / f"mtd{index}.bin").write_bytes(raw)
        identity.update(bytes((index,)))
        identity.update(len(raw).to_bytes(8, "big"))
        identity.update(raw)
    camera_identity = identity.hexdigest()

    session = root / "session"
    host = session / "host"
    host.mkdir(parents=True, mode=0o700)
    setup = f"DCS6100-{camera_identity[:8]}"
    private_files = {
        "identity": b"private-key-placeholder\n",
        "known_hosts": b"192.168.88.1 ssh-ed25519 " + b"A" * 68 + b"\n",
        "service.credential": b"a" * 64 + b"\n",
        "station_known_hosts": setup.lower().encode() + b".local ssh-ed25519 " + b"B" * 68 + b"\n",
        "session.json": json.dumps({
            "schema_version": 1, "setup_ssid": setup,
            "station_mdns_name": setup.lower() + ".local",
            "session_kind": "uartless-functional-provisioning",
            "camera_identity_sha256": camera_identity, "transport_enabled": False,
        }).encode(),
    }
    for name, raw in private_files.items():
        path = host / name
        path.write_bytes(raw)
        path.chmod(0o600)
    recovery = root / "recovery"
    recovery.mkdir()
    request = post_install_readback.PostInstallReadbackInputs(
        install_set_dir=install, universal_public_key=public_key,
        preserved_readback_dir=preserved, session_dir=session,
        functional_recovery_dir=recovery,
    )
    return request, camera_identity


def readback(*, boot_after=BOOT_ID, hashes=HASHES):
    lines = [
        f"BOOT_BEFORE={BOOT_ID}",
        "MTD_BEGIN",
        "dev:    size   erasesize  name",
        'mtd0: 00040000 00008000 "boot"',
        'mtd1: 001c0000 00008000 "kernel"',
        'mtd2: 00480000 00008000 "bootstrap"',
        'mtd3: 00650000 00008000 "system"',
        'mtd4: 00170000 00008000 "data"',
        'mtd5: 00180000 00008000 "vendor"',
        'mtd6: 00040000 00008000 "factory"',
        "MTD_END",
        *(f"{hashes[index]}  /dev/mtd{index}" for index in (0, 1, 3, 5, 6)),
        f"BOOT_AFTER={boot_after}",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


class PostInstallReadbackTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_actual_signed_inputs_validate_and_bind_protected_identity(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
            request, camera_identity = actual_inputs(Path(name))
            recovery = SimpleNamespace(camera_identity_sha256=camera_identity)
            with mock.patch.object(post_install_readback,
                                   "validate_functional_recovery_boundary",
                                   return_value=recovery):
                validated = post_install_readback._validate_inputs(request)
            self.assertEqual(validated.camera_identity_sha256, camera_identity)
            self.assertEqual(set(validated.expected_sha256), {0, 1, 3, 5, 6})
            self.assertEqual(len(validated.transport_identity), 64)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_bad_signature_and_changed_protected_input_fail_before_resolution(self):
        for mutation in ("signature", "protected"):
            with self.subTest(mutation=mutation), \
                 tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
                request, camera_identity = actual_inputs(Path(name))
                if mutation == "signature":
                    path = request.install_set_dir / "thingino-universal.tgb"
                    raw = bytearray(path.read_bytes())
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        signature = archive.read("manifest.ed25519")
                    offset = raw.find(signature)
                    self.assertGreaterEqual(offset, 0)
                    raw[offset] ^= 1
                    path.write_bytes(raw)
                else:
                    path = request.preserved_readback_dir / "mtd0.bin"
                    raw = bytearray(path.read_bytes())
                    raw[0] ^= 1
                    path.write_bytes(raw)
                recovery = SimpleNamespace(camera_identity_sha256=camera_identity)
                with mock.patch.object(post_install_readback,
                                       "validate_functional_recovery_boundary",
                                       return_value=recovery), \
                     mock.patch.object(post_install_readback,
                                       "resolve_recovery_ap_station") as resolve:
                    with self.assertRaises((OperationError, ProjectError)):
                        post_install_readback.verify_post_install_readback(request)
                resolve.assert_not_called()

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_resolver_pin_change_stops_before_transport_with_actual_inputs(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as name:
            request, camera_identity = actual_inputs(Path(name))
            recovery = SimpleNamespace(camera_identity_sha256=camera_identity)
            pin = request.session_dir / "host/station_known_hosts"

            def mutate_pin(*_args, **_kwargs):
                pin.write_bytes(pin.read_bytes().replace(b"B", b"C"))
                return (f"dcs6100-{camera_identity[:8]}.local", "192.0.2.23")

            with mock.patch.object(post_install_readback,
                                   "validate_functional_recovery_boundary",
                                   return_value=recovery), \
                 mock.patch.object(post_install_readback, "resolve_recovery_ap_station",
                                   side_effect=mutate_pin), \
                 mock.patch.object(post_install_readback, "_exchange") as exchange:
                with self.assertRaisesRegex(ProjectError, "station pin changed"):
                    post_install_readback.verify_post_install_readback(request)
            exchange.assert_not_called()

    def test_fixture_happy_path_is_fixed_read_only_and_selected_partitions_only(self):
        session = RecoveryApHostSession(
            identity=Path("identity"),
            known_hosts=Path("known_hosts"),
            station_mdns_name="dcs6100-1234abcd.local",
            session_kind="uartless-functional-provisioning",
            camera_identity_sha256="a" * 64,
            transport_enabled=False,
            station_known_hosts=Path("station_known_hosts"),
        )
        validated = post_install_readback._ValidatedInputs(
            session=session,
            session_identity="b" * 64,
            transport_identity="d" * 64,
            expected_sha256=HASHES,
            bundle_sha256="c" * 64,
            camera_identity_sha256="a" * 64,
            data_mode="initialize",
        )
        request = post_install_readback.PostInstallReadbackInputs(
            install_set_dir=Path("install-set"),
            universal_public_key=Path("release.pub"),
            preserved_readback_dir=Path("preserved"),
            session_dir=Path("session"),
            functional_recovery_dir=Path("recovery"),
        )
        with mock.patch.object(post_install_readback, "_validate_inputs", return_value=validated), \
             mock.patch.object(post_install_readback, "resolve_recovery_ap_station",
                               return_value=(session.station_mdns_name, "192.0.2.23")), \
             mock.patch.object(post_install_readback, "_exchange", return_value=readback()) as exchange, \
             mock.patch.object(post_install_readback, "session_fingerprint", return_value="b" * 64), \
             mock.patch.object(post_install_readback, "_transport_fingerprint",
                               return_value="d" * 64):
            result = post_install_readback.verify_post_install_readback(request)
        exchange.assert_called_once_with(
            session,
            host="192.0.2.23",
            command=post_install_readback.POST_INSTALL_READBACK_COMMAND,
            timeout=90.0,
            allow_uartless_post_install_readback=True,
        )
        self.assertFalse(result["nor"]["full_physical_readback_verified"])
        self.assertEqual(result["nor"]["written_mtd"], [])
        self.assertEqual(
            [item["logical_mtd"] for item in result["result"]["checked_partitions"]],
            [0, 1, 3, 5, 6],
        )
        self.assertEqual(result["result"]["mutable_data_exact_comparison"], "out-of-scope")
        self.assertEqual(result["result"]["physical_mtd2_exact_comparison"], "out-of-scope")

    def test_host_inputs_fail_before_resolution_or_exchange(self):
        request = post_install_readback.PostInstallReadbackInputs(
            install_set_dir=Path("bad"), universal_public_key=Path("bad"),
            preserved_readback_dir=Path("bad"), session_dir=Path("bad"),
            recovery_dir=Path("bad"),
        )
        with mock.patch.object(post_install_readback, "_validate_inputs",
                               side_effect=OperationError("invalid host inputs")), \
             mock.patch.object(post_install_readback, "resolve_recovery_ap_station") as resolve, \
             mock.patch.object(post_install_readback, "_exchange") as exchange:
            with self.assertRaisesRegex(OperationError, "invalid host inputs"):
                post_install_readback.verify_post_install_readback(request)
        resolve.assert_not_called()
        exchange.assert_not_called()

    def test_fixed_read_timeout_fails_without_a_result(self):
        session = RecoveryApHostSession(
            identity=Path("identity"), known_hosts=Path("known_hosts"),
            station_mdns_name="dcs6100-1234abcd.local",
            session_kind="uartless-functional-provisioning",
            camera_identity_sha256="a" * 64, transport_enabled=False,
            station_known_hosts=Path("station_known_hosts"),
        )
        validated = post_install_readback._ValidatedInputs(
            session=session, session_identity="b" * 64,
            transport_identity="d" * 64,
            expected_sha256=HASHES, bundle_sha256="c" * 64,
            camera_identity_sha256="a" * 64, data_mode="initialize",
        )
        request = post_install_readback.PostInstallReadbackInputs(
            install_set_dir=Path("install-set"), universal_public_key=Path("release.pub"),
            preserved_readback_dir=Path("preserved"), session_dir=Path("session"),
            functional_recovery_dir=Path("recovery"),
        )
        with mock.patch.object(post_install_readback, "_validate_inputs", return_value=validated), \
             mock.patch.object(post_install_readback, "resolve_recovery_ap_station",
                               return_value=(session.station_mdns_name, "192.0.2.23")), \
             mock.patch.object(post_install_readback, "session_fingerprint",
                               return_value="b" * 64), \
             mock.patch.object(post_install_readback, "_transport_fingerprint",
                               return_value="d" * 64), \
             mock.patch.object(post_install_readback, "_exchange",
                               side_effect=RecoveryApHostError("SSH command timed out")):
            with self.assertRaisesRegex(RecoveryApHostError, "timed out"):
                post_install_readback.verify_post_install_readback(request)

    def test_resolver_session_change_stops_before_transport(self):
        session = RecoveryApHostSession(
            identity=Path("identity"), known_hosts=Path("known_hosts"),
            station_mdns_name="dcs6100-1234abcd.local",
            session_kind="uartless-functional-provisioning",
            camera_identity_sha256="a" * 64, transport_enabled=False,
            station_known_hosts=Path("station_known_hosts"),
        )
        validated = post_install_readback._ValidatedInputs(
            session=session, session_identity="b" * 64,
            transport_identity="d" * 64,
            expected_sha256=HASHES, bundle_sha256="c" * 64,
            camera_identity_sha256="a" * 64, data_mode="initialize",
        )
        request = post_install_readback.PostInstallReadbackInputs(
            install_set_dir=Path("install-set"), universal_public_key=Path("release.pub"),
            preserved_readback_dir=Path("preserved"), session_dir=Path("session"),
            functional_recovery_dir=Path("recovery"),
        )
        with mock.patch.object(post_install_readback, "_validate_inputs", return_value=validated), \
             mock.patch.object(post_install_readback, "resolve_recovery_ap_station",
                               return_value=(session.station_mdns_name, "192.0.2.23")), \
             mock.patch.object(post_install_readback, "session_fingerprint",
                               return_value="changed"), \
             mock.patch.object(post_install_readback, "_exchange") as exchange:
            with self.assertRaisesRegex(ProjectError, "changed before verification"):
                post_install_readback.verify_post_install_readback(request)
        exchange.assert_not_called()

    def test_parser_exposes_separate_operation_without_transport_overrides(self):
        parser = user_cli.build_parser()
        parsed = parser.parse_args([
            "universal", "verify-readback",
            "--functional-recovery-dir", "recovery",
            "--preserved-readback-dir", "preserved",
            "--install-set-dir", "install-set",
            "--universal-public-key", "release.pub",
            "--session-dir", "session",
        ])
        self.assertIs(parsed.handler, user_cli._universal_verify_readback)
        commands = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        universal = commands.choices["universal"]
        self.assertIn("verify-readback", universal.format_help())
        universal_commands = next(
            action for action in universal._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        readback_parser = universal_commands.choices["verify-readback"]
        options = {
            option
            for action in readback_parser._actions
            for option in action.option_strings
        }
        self.assertTrue({"--host", "--command", "--timeout"}.isdisjoint(options))

    def test_readback_parser_rejects_malformed_or_ambiguous_evidence(self):
        boot_changed = readback(boot_after="11111111-1111-1111-1111-111111111111")
        bad_prefix = readback().replace(b"BOOT_BEFORE=", b"", 1)
        bad_layout = readback().replace(b'mtd6: 00040000', b'mtd5: 00040000', 1)
        duplicate_hash = readback().replace(b"/dev/mtd6", b"/dev/mtd5", 1)
        wrong_hash = readback().replace(b"0" * 64 + b"  /dev/mtd0", b"f" * 64 + b"  /dev/mtd0", 1)
        for raw in (boot_changed, bad_prefix, bad_layout, duplicate_hash, wrong_hash,
                    readback()[:-1] + b"EXTRA\n", readback()[:80]):
            with self.subTest(raw=raw[-80:]):
                with self.assertRaises(OperationError):
                    post_install_readback._parse_readback(raw, HASHES)

    def test_symlink_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(OperationError, "symlink path component"):
                post_install_readback._reject_symlink_path(linked / "input", "input")

    def test_transport_allows_only_the_fixed_readback_command(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            pin = root / "station_known_hosts"
            pin.write_text("pin")
            session = RecoveryApHostSession(
                identity=root / "identity",
                known_hosts=root / "known_hosts",
                station_mdns_name="dcs6100-1234abcd.local",
                session_kind="uartless-functional-provisioning",
                camera_identity_sha256="a" * 64,
                transport_enabled=False,
                station_known_hosts=pin,
            )
            arguments = ssh_arguments(
                session,
                host="192.0.2.23",
                command=post_install_readback.POST_INSTALL_READBACK_COMMAND,
                allow_uartless_post_install_readback=True,
            )
            self.assertEqual(arguments[-1], post_install_readback.POST_INSTALL_READBACK_COMMAND)
            with self.assertRaises(RecoveryApHostError):
                ssh_arguments(
                    session,
                    host="192.0.2.23",
                    command=post_install_readback.POST_INSTALL_READBACK_COMMAND + "; true",
                    allow_uartless_post_install_readback=True,
                )


if __name__ == "__main__":
    unittest.main()
