from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer.camera_authorization import (
    CameraAuthorizationError,
    create_camera_authorization,
    validate_camera_authorization,
)
from installer.final_bundle import ValidatedFinalBundle
from installer.private_config import PrivateInstallConfig
from installer.mtd3_split import DATA_FLASH_SPAN
from installer.install_policy import universal_physical_write_policy
from installer.provisioning import (
    ProvisioningError,
    create_provisioning_sidecar,
    read_private_provisioning_data,
    require_provisioning_signing_key,
    validate_provisioning_sidecar,
)
from installer.recovery_gate import RecoveryDecision
from installer.provisioning_data import ProvisioningDataImage
from installer.universal_install import (
    AUTHORIZATION_BINARY_CARD_NAME,
    AUTHORIZATION_CARD_NAME,
    AUTHORIZATION_SIGNATURE_CARD_NAME,
    PROVISIONING_CARD_NAME,
    handoff_camera_bound_universal_install,
    stage_camera_bound_universal_install,
)


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class UniversalArtifactTests(unittest.TestCase):
    def _keys(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        private_key = directory / "private.pem"
        public_key = directory / "public.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", private_key],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.chmod(private_key, 0o600)
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                private_key,
                "-pubout",
                "-out",
                public_key,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return private_key, public_key

    @staticmethod
    def _private(seed: bytes) -> PrivateInstallConfig:
        token = seed.hex().encode("ascii").ljust(64, b"0")[:64] + b"\n"
        api = bytes(reversed(seed)).hex().encode("ascii").ljust(64, b"1")[:64] + b"\n"
        return PrivateInstallConfig(
            authorized_key=(
                b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr38ca5R64nSROxDExscFYDU8yWc42nuvRoOVXZLzCk camera\n"
            ),
            credential=token,
            api_key=api,
            wpa_config=(
                b"network={\nssid=\"camera-" + seed[:2].hex().encode() + b"\"\npsk="
                + b"a" * 64
                + b"\n}\n"
            ),
            credential_set_id=hashlib.sha256(
                b"credential-set-id\0" + seed
            ).hexdigest(),
        )

    @staticmethod
    def _bundle() -> ValidatedFinalBundle:
        return ValidatedFinalBundle(
            raw=b"one-byte-identical-model-universal-firmware",
            manifest={"artifact_scope": "model-universal"},
            members={"images/data.jffs2": b""},
        )

    @staticmethod
    def _recovery(camera: str) -> RecoveryDecision:
        return RecoveryDecision(
            mode="uartless-functional-same-device-pair",
            preserved_mtd=(0, 4, 5),
            recovery_images=2,
            camera_identity_sha256=camera,
            camera_authorization_key_sha256=hashlib.sha256(
                b"test-camera-authorization-key\0" + camera.encode("ascii")
            ).hexdigest(),
            functional_recovery_accepted=True,
            original_complete_backup_accepted=False,
            original_preserved_mtd=(0, 3, 4, 5),
        )

    def test_one_universal_firmware_has_distinct_camera_sidecars_and_authorizations(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            private_key, public_key = self._keys(root)
            bundle = self._bundle()
            camera_a = "a" * 64
            camera_b = "b" * 64
            session_a = "c" * 64
            session_b = "d" * 64
            sidecars = []
            for name, camera, session, private in (
                ("a", camera_a, session_a, self._private(b"camera-a")),
                ("b", camera_b, session_b, self._private(b"camera-b")),
            ):
                path = root / f"{name}.tps"
                with (
                    mock.patch(
                        "installer.provisioning.load_private_config_for_session",
                        return_value=private,
                    ),
                    mock.patch(
                        "installer.provisioning.recovery_session_identity",
                        return_value=session,
                    ),
                    mock.patch(
                        "installer.provisioning.load_host_session",
                        return_value=SimpleNamespace(station_mdns_name=f"dcs6100-{name}1234567.local"),
                    ),
                    mock.patch(
                        "installer.provisioning._read_private",
                        return_value=b"ssh-ed25519 fake-private-host-key",
                    ),
                    mock.patch(
                        "installer.provisioning.build_provisioning_data_image",
                        return_value=ProvisioningDataImage(
                            b"\x85\x19" + name.encode("ascii") * (DATA_FLASH_SPAN - 2),
                            {"state": "committed"},
                        ),
                    ),
                ):
                    sidecar, data_image = create_provisioning_sidecar(
                        private_config_dir=root,
                        session_dir=root,
                        camera_identity_sha256=camera,
                        universal_firmware_sha256=bundle.sha256,
                        universal_system_rootfs=b"universal-system",
                        signing_key=private_key,
                        unsquashfs=root / "unsquashfs",
                        mkfs_jffs2=root / "mkfs.jffs2",
                        output_path=path,
                        data_output_path=root / f"{name}.jffs2",
                    )
                validated = validate_provisioning_sidecar(
                    path,
                    public_key=public_key,
                    expected_camera_identity_sha256=camera,
                    expected_universal_firmware_sha256=bundle.sha256,
                    expected_recovery_session_sha256=session,
                )
                self.assertEqual(validated.sha256, sidecar.sha256)
                self.assertEqual(validated.provisioning_data_sha256, data_image.sha256)
                self.assertNotIn(private.credential.decode().strip(), repr(validated))
                self.assertNotIn(private.wpa_config.decode(), repr(validated))
                sidecars.append((path, validated, camera, session))
            self.assertNotEqual(sidecars[0][1].raw, sidecars[1][1].raw)
            require_provisioning_signing_key(sidecars[0][1], private_key)
            other_private, _other_public = self._keys(root / "other")
            with self.assertRaisesRegex(ProvisioningError, "signing key differs"):
                require_provisioning_signing_key(sidecars[0][1], other_private)
            self.assertEqual(bundle.raw, self._bundle().raw)

            authorizations = []
            for name, (_path, sidecar, camera, session) in zip(
                ("a", "b"), sidecars, strict=True
            ):
                auth_dir = root / f"authorization-{name}"
                created = create_camera_authorization(
                    recovery=self._recovery(camera),
                    universal_bundle=bundle,
                    universal_stage2_sha256="f" * 64,
                    provisioning_sidecar_sha256=sidecar.sha256,
                    provisioning_data_sha256=sidecar.provisioning_data_sha256,
                    provisioning_data_size=sidecar.provisioning_data_size,
                    provisioning_id=sidecar.provisioning_id,
                    recovery_session_sha256=session,
                    data_action="initialize",
                    signing_key=private_key,
                    output_dir=auth_dir,
                )
                validated = validate_camera_authorization(
                    auth_dir,
                    public_key=public_key,
                    expected_camera_identity_sha256=camera,
                    expected_camera_authorization_key_sha256=(
                        self._recovery(camera).camera_authorization_key_sha256
                    ),
                    expected_universal_firmware_sha256=bundle.sha256,
                    expected_universal_stage2_sha256="f" * 64,
                    expected_provisioning_sidecar_sha256=sidecar.sha256,
                    expected_provisioning_data_sha256=sidecar.provisioning_data_sha256,
                    expected_provisioning_data_size=sidecar.provisioning_data_size,
                    expected_provisioning_id=sidecar.provisioning_id,
                    expected_recovery_session_sha256=session,
                    expected_data_action="initialize",
                )
                self.assertEqual(
                    validated.authorization_sha256,
                    created.authorization_sha256,
                )
                self.assertEqual(len(validated.binary), 256)
                self.assertEqual(
                    validated.document["write_policy"],
                    universal_physical_write_policy(),
                )
                self.assertEqual(validated.binary[24:56], bytes.fromhex(camera))
                self.assertEqual(validated.binary[88:120], bytes.fromhex("f" * 64))
                self.assertEqual(
                    int.from_bytes(validated.binary[152:156], "big"),
                    sidecar.provisioning_data_size,
                )
                self.assertEqual(
                    int.from_bytes(validated.binary[156:160], "big"), 0
                )
                authorizations.append((auth_dir, validated))
            self.assertNotEqual(
                authorizations[0][1].authorization_sha256,
                authorizations[1][1].authorization_sha256,
            )
            with self.assertRaisesRegex(
                CameraAuthorizationError, "binary differs"
            ):
                validate_camera_authorization(
                    authorizations[0][0],
                    public_key=public_key,
                    expected_camera_identity_sha256=camera_a,
                    expected_camera_authorization_key_sha256="2" * 64,
                    expected_universal_firmware_sha256=bundle.sha256,
                    expected_universal_stage2_sha256="f" * 64,
                    expected_provisioning_sidecar_sha256=sidecars[0][1].sha256,
                    expected_provisioning_data_sha256=sidecars[0][1].provisioning_data_sha256,
                    expected_provisioning_data_size=sidecars[0][1].provisioning_data_size,
                    expected_provisioning_id=sidecars[0][1].provisioning_id,
                    expected_recovery_session_sha256=session_a,
                    expected_data_action="initialize",
                )
            with self.assertRaisesRegex(
                CameraAuthorizationError, "provisioning_data_sha256 differs"
            ):
                validate_camera_authorization(
                    authorizations[0][0],
                    public_key=public_key,
                    expected_camera_identity_sha256=camera_a,
                    expected_camera_authorization_key_sha256=(
                        self._recovery(camera_a).camera_authorization_key_sha256
                    ),
                    expected_universal_firmware_sha256=bundle.sha256,
                    expected_universal_stage2_sha256="f" * 64,
                    expected_provisioning_sidecar_sha256=sidecars[0][1].sha256,
                    expected_provisioning_data_sha256="9" * 64,
                    expected_provisioning_data_size=sidecars[0][1].provisioning_data_size,
                    expected_provisioning_id=sidecars[0][1].provisioning_id,
                    expected_recovery_session_sha256=session_a,
                    expected_data_action="initialize",
                )
            with self.assertRaisesRegex(
                CameraAuthorizationError, "camera_identity_sha256 differs"
            ):
                validate_camera_authorization(
                    authorizations[0][0],
                    public_key=public_key,
                    expected_camera_identity_sha256=camera_b,
                    expected_camera_authorization_key_sha256=(
                        self._recovery(camera_b).camera_authorization_key_sha256
                    ),
                    expected_universal_firmware_sha256=bundle.sha256,
                    expected_universal_stage2_sha256="f" * 64,
                    expected_provisioning_sidecar_sha256=sidecars[0][1].sha256,
                    expected_provisioning_data_sha256=sidecars[0][1].provisioning_data_sha256,
                    expected_provisioning_data_size=sidecars[0][1].provisioning_data_size,
                    expected_provisioning_id=sidecars[0][1].provisioning_id,
                    expected_recovery_session_sha256=session_a,
                    expected_data_action="initialize",
                )

    def test_universal_sidecars_are_read_back_before_bootstrap_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            authorization = SimpleNamespace(
                raw_manifest=b"authorization",
                signature=b"signature",
                binary=b"binary-authorization",
            )
            validated = SimpleNamespace(
                authorization=authorization,
                provisioning=SimpleNamespace(raw=b"private-audit-sidecar"),
                provisioning_data=b"private-provisioning-data",
                bootstrap=b"bootstrap",
                stage2=SimpleNamespace(raw=b"stage2"),
                manifest=b"manifest",
            )
            preflight = SimpleNamespace(
                physical_device="/dev/test-media",
                mount_root=root.resolve(),
            )

            def activate(**_values: object) -> dict[str, str]:
                self.assertEqual(
                    (root / PROVISIONING_CARD_NAME).read_bytes(),
                    b"private-provisioning-data",
                )
                self.assertEqual(
                    (root / AUTHORIZATION_CARD_NAME).read_bytes(),
                    b"authorization",
                )
                self.assertEqual(
                    (root / AUTHORIZATION_SIGNATURE_CARD_NAME).read_bytes(),
                    b"signature",
                )
                self.assertEqual(
                    (root / AUTHORIZATION_BINARY_CARD_NAME).read_bytes(),
                    b"binary-authorization",
                )
                return {"bootstrap": "accepted"}

            with (
                mock.patch("installer.universal_install.media.validate_sd_root"),
                mock.patch(
                    "installer.universal_install.media._write_verified_temporary",
                    side_effect=lambda path, raw: path.write_bytes(raw),
                ),
                mock.patch("installer.universal_install.media._sync_directory"),
                mock.patch(
                    "installer.universal_install.media.stage_verified_install_set",
                    side_effect=activate,
                ) as stage,
            ):
                result = stage_camera_bound_universal_install(
                    validated,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-media",
                )
            self.assertEqual(stage.call_count, 1)
            self.assertEqual(result["bootstrap"], "accepted")

    def test_universal_staging_rejects_changed_live_mount_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            actual = root.stat()
            validated = SimpleNamespace()
            preflight = SimpleNamespace(
                physical_device="/dev/test-media",
                mount_root=root.resolve(),
                mount_device_id=actual.st_dev,
                mount_inode=actual.st_ino + 1,
            )
            with self.assertRaisesRegex(
                ValueError, "media identity changed"
            ):
                stage_camera_bound_universal_install(
                    validated,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-media",
                )

    def test_failed_universal_bootstrap_activation_removes_camera_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            validated = SimpleNamespace(
                authorization=SimpleNamespace(
                    raw_manifest=b"authorization",
                    signature=b"signature",
                    binary=b"binary-authorization",
                ),
                provisioning=SimpleNamespace(raw=b"audit-sidecar"),
                provisioning_data=b"provisioning-data",
                bootstrap=b"bootstrap",
                stage2=SimpleNamespace(raw=b"stage2"),
                manifest=b"manifest",
            )
            preflight = SimpleNamespace(
                physical_device="/dev/test-media",
                mount_root=root.resolve(),
            )
            with (
                mock.patch("installer.universal_install.media.validate_sd_root"),
                mock.patch(
                    "installer.universal_install.media._write_verified_temporary",
                    side_effect=lambda path, raw: path.write_bytes(raw),
                ),
                mock.patch("installer.universal_install.media._sync_directory"),
                mock.patch(
                    "installer.universal_install.media.stage_verified_install_set",
                    side_effect=ValueError("injected activation failure"),
                ),
                self.assertRaisesRegex(ValueError, "injected"),
            ):
                stage_camera_bound_universal_install(
                    validated,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-media",
                )
            for name in (
                PROVISIONING_CARD_NAME,
                AUTHORIZATION_CARD_NAME,
                AUTHORIZATION_SIGNATURE_CARD_NAME,
                AUTHORIZATION_BINARY_CARD_NAME,
            ):
                self.assertFalse((root / name).exists())

    def test_universal_handoff_verifies_sidecars_and_passivates_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            authorization = SimpleNamespace(
                raw_manifest=b"authorization",
                signature=b"signature",
                binary=b"binary-authorization",
            )
            validated = SimpleNamespace(
                authorization=authorization,
                provisioning_data=b"private-provisioning-data",
                bootstrap=b"bootstrap",
                stage2=SimpleNamespace(raw=b"stage2"),
                manifest=b"manifest",
            )
            payloads = {
                PROVISIONING_CARD_NAME: validated.provisioning_data,
                AUTHORIZATION_CARD_NAME: authorization.raw_manifest,
                AUTHORIZATION_SIGNATURE_CARD_NAME: authorization.signature,
                AUTHORIZATION_BINARY_CARD_NAME: authorization.binary,
            }
            for name, raw in payloads.items():
                (root / name).write_bytes(raw)
            preflight = SimpleNamespace(
                physical_device="/dev/test-media",
                mount_root=root.resolve(),
            )
            with (
                mock.patch(
                    "installer.universal_install.media.validate_sd_root"
                ) as validate_root,
                mock.patch(
                    "installer.universal_install.media.deactivate_staged_install_set",
                    return_value={"STAGE1.PKG": "a" * 64},
                ) as deactivate,
            ):
                result = handoff_camera_bound_universal_install(
                    validated,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-media",
                )
            validate_root.assert_called_once_with(
                root,
                allowed_matching_filename="DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin",
            )
            deactivate.assert_called_once_with(
                bootstrap_bytes=b"bootstrap",
                stage2_bytes=b"stage2",
                manifest_bytes=b"manifest",
                root=root,
                bootstrap_name="DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin",
                preflight=preflight,
                confirmed_physical_device="/dev/test-media",
            )
            self.assertEqual(result["STAGE1.PKG"], "a" * 64)
            for name, raw in payloads.items():
                self.assertEqual(result[name], hashlib.sha256(raw).hexdigest())

    def test_universal_handoff_rejects_tampered_camera_sidecar_before_passivation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            authorization = SimpleNamespace(
                raw_manifest=b"authorization",
                signature=b"signature",
                binary=b"binary-authorization",
            )
            validated = SimpleNamespace(
                authorization=authorization,
                provisioning_data=b"private-provisioning-data",
                bootstrap=b"bootstrap",
                stage2=SimpleNamespace(raw=b"stage2"),
                manifest=b"manifest",
            )
            (root / PROVISIONING_CARD_NAME).write_bytes(b"tampered")
            for name, raw in {
                AUTHORIZATION_CARD_NAME: authorization.raw_manifest,
                AUTHORIZATION_SIGNATURE_CARD_NAME: authorization.signature,
                AUTHORIZATION_BINARY_CARD_NAME: authorization.binary,
            }.items():
                (root / name).write_bytes(raw)
            preflight = SimpleNamespace(
                physical_device="/dev/test-media",
                mount_root=root.resolve(),
            )
            with (
                mock.patch("installer.universal_install.media.validate_sd_root"),
                mock.patch(
                    "installer.universal_install.media.deactivate_staged_install_set"
                ) as deactivate,
                self.assertRaisesRegex(ValueError, "sidecar differs at handoff"),
            ):
                handoff_camera_bound_universal_install(
                    validated,
                    root=root,
                    preflight=preflight,
                    confirmed_physical_device="/dev/test-media",
                )
            deactivate.assert_not_called()

    def test_provisioning_data_requires_private_single_link_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            path = root / "camera.jffs2"
            path.write_bytes(b"\x85\x19" + b"x" * (DATA_FLASH_SPAN - 2))
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ProvisioningError, "private file policy"):
                read_private_provisioning_data(path)
            os.chmod(path, 0o600)
            self.assertEqual(len(read_private_provisioning_data(path)), DATA_FLASH_SPAN)
            linked = root / "linked.jffs2"
            os.link(path, linked)
            with self.assertRaisesRegex(ProvisioningError, "private file policy"):
                read_private_provisioning_data(path)

    def test_provisioning_swap_and_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            private_key, public_key = self._keys(root)
            path = root / "camera.tps"
            with (
                mock.patch(
                    "installer.provisioning.load_private_config_for_session",
                    return_value=self._private(b"camera-a"),
                ),
                mock.patch(
                    "installer.provisioning.recovery_session_identity",
                    return_value="c" * 64,
                ),
                mock.patch(
                    "installer.provisioning.load_host_session",
                    return_value=SimpleNamespace(station_mdns_name="dcs6100-a1234567.local"),
                ),
                mock.patch(
                    "installer.provisioning._read_private",
                    return_value=b"ssh-ed25519 fake-private-host-key",
                ),
                mock.patch(
                    "installer.provisioning.build_provisioning_data_image",
                    return_value=ProvisioningDataImage(
                        b"\x85\x19" + b"a" * (DATA_FLASH_SPAN - 2),
                        {"state": "committed"},
                    ),
                ),
            ):
                create_provisioning_sidecar(
                    private_config_dir=root,
                    session_dir=root,
                    camera_identity_sha256="a" * 64,
                    universal_firmware_sha256="e" * 64,
                    universal_system_rootfs=b"universal-system",
                    signing_key=private_key,
                    unsquashfs=root / "unsquashfs",
                    mkfs_jffs2=root / "mkfs.jffs2",
                    output_path=path,
                    data_output_path=root / "camera.jffs2",
                )
            with self.assertRaisesRegex(ProvisioningError, "camera_identity_sha256 differs"):
                validate_provisioning_sidecar(
                    path,
                    public_key=public_key,
                    expected_camera_identity_sha256="b" * 64,
                    expected_universal_firmware_sha256="e" * 64,
                    expected_recovery_session_sha256="c" * 64,
                )
            raw = bytearray(path.read_bytes())
            raw[-1] ^= 1
            path.write_bytes(bytes(raw))
            os.chmod(path, 0o600)
            with self.assertRaises(ProvisioningError):
                validate_provisioning_sidecar(
                    path,
                    public_key=public_key,
                    expected_camera_identity_sha256="a" * 64,
                    expected_universal_firmware_sha256="e" * 64,
                    expected_recovery_session_sha256="c" * 64,
                )

    def test_uartless_provisioning_uses_its_local_dropbear_host_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            private_key, _public_key = self._keys(root)
            session_dir = root / "session"
            session = SimpleNamespace(
                camera_identity_sha256="a" * 64,
                session_kind="uartless-functional-provisioning",
                station_mdns_name="dcs6100-a1234567.local",
            )
            with (
                mock.patch(
                    "installer.provisioning.load_private_config_for_session",
                    return_value=self._private(b"camera-a"),
                ),
                mock.patch(
                    "installer.provisioning.recovery_session_identity",
                    return_value="c" * 64,
                ),
                mock.patch(
                    "installer.provisioning.load_host_session",
                    return_value=session,
                ),
                mock.patch(
                    "installer.provisioning._read_private",
                    return_value=b"ssh-ed25519 fake-private-host-key",
                ) as read_private,
                mock.patch(
                    "installer.provisioning.build_provisioning_data_image",
                    return_value=ProvisioningDataImage(
                        b"\x85\x19" + b"a" * (DATA_FLASH_SPAN - 2),
                        {"state": "committed"},
                    ),
                ),
            ):
                create_provisioning_sidecar(
                    private_config_dir=root,
                    session_dir=session_dir,
                    camera_identity_sha256="a" * 64,
                    universal_firmware_sha256="e" * 64,
                    universal_system_rootfs=b"universal-system",
                    signing_key=private_key,
                    unsquashfs=root / "unsquashfs",
                    mkfs_jffs2=root / "mkfs.jffs2",
                    output_path=root / "camera.tps",
                    data_output_path=root / "camera.jffs2",
                )
            self.assertEqual(
                read_private.call_args.args[0],
                session_dir / "host/dropbear_ed25519_host_key",
            )


if __name__ == "__main__":
    unittest.main()
