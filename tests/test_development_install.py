from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer.cli import build_parser
from installer.development_install import (
    DevelopmentInstallError,
    _existing_state,
    _require_expected_health_image,
    build_personal_candidate,
    complete_personal_install,
    stage_base_root,
)


class DevelopmentInstallTests(unittest.TestCase):
    def test_personal_candidate_build_is_host_only_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base.squashfs"
            base.write_bytes(b"base-root")
            output = root / "candidate"

            def prepare(**arguments: object) -> dict[str, object]:
                destination = Path(arguments["output_dir"])
                destination.mkdir()
                (destination / "system.private.squashfs").write_bytes(b"system")
                (destination / "final-root.private.json").write_text(
                    '{"schema_version":1}\n', encoding="utf-8"
                )
                return {"schema_version": 1}

            image = SimpleNamespace(image_sha256="1" * 64, raw=b"personal-image")
            with (
                mock.patch("installer.development_install.load_host_session"),
                mock.patch(
                    "installer.development_install.load_private_config_for_session",
                    return_value=SimpleNamespace(credential_set_id="2" * 64),
                ),
                mock.patch(
                    "installer.development_install.load_vendor_bundle",
                    return_value=SimpleNamespace(bundle_sha256="3" * 64),
                ),
                mock.patch(
                    "installer.development_install.load_media_closure",
                    return_value=SimpleNamespace(closure_sha256="4" * 64),
                ),
                mock.patch(
                    "installer.development_install.prepare_from_private_directory",
                    side_effect=prepare,
                ),
                mock.patch(
                    "installer.development_install.build_personal_mtd3_image",
                    return_value=image,
                ),
            ):
                result = build_personal_candidate(
                    session_dir=root / "session",
                    base_rootfs_path=base,
                    private_config_dir=root / "private-config",
                    expected_wpa_config_path=root / "current-wpa.conf",
                    vendor_bundle_dir=root / "vendor-bundle",
                    media_closure_dir=root / "media-closure",
                    output_dir=output,
                    mksquashfs=Path("mksquashfs"),
                    unsquashfs=Path("unsquashfs"),
                )

            self.assertFalse(result["nor_writes"])
            self.assertEqual(result["image_sha256"], "1" * 64)
            self.assertEqual((output / "personal-mtd3.bin").read_bytes(), b"personal-image")
            self.assertTrue((output / "build-result.private.json").is_file())

    def test_existing_state_is_bound_to_the_recovery_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / "install-state.private.json").write_text(
                '{"schema_version":1,"base_rootfs_sha256":"base",'
                '"session_identity_sha256":"old",'
                '"station_mdns_name":"dcs6100-1234abcd.local"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DevelopmentInstallError, "another recovery session"):
                _existing_state(
                    work,
                    "base",
                    session_identity_sha256="new",
                    station_mdns_name="dcs6100-1234abcd.local",
                )

    def test_station_health_must_bind_complete_expected_mtd3(self) -> None:
        with self.assertRaisesRegex(DevelopmentInstallError, "expected complete"):
            _require_expected_health_image(
                {
                    "mtd3_read_back_verified": True,
                    "mtd3_sha256": "a" * 64,
                },
                "b" * 64,
            )

    def test_base_root_is_immutable_across_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            stage_base_root(work, b"first")
            stage_base_root(work, b"first")
            with self.assertRaisesRegex(DevelopmentInstallError, "base root changed"):
                stage_base_root(work, b"second")

    def test_complete_flow_preserves_personal_image_and_uses_mdns_health(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session"
            (session / "host").mkdir(parents=True)
            (session / "host/identity.pub").write_bytes(b"ssh-ed25519 test\n")
            work = root / "work"

            def extract(**arguments: object) -> dict[str, object]:
                Path(arguments["output_dir"]).mkdir()
                return {}

            def configure(**arguments: object) -> object:
                Path(arguments["output_dir"]).mkdir()
                return object()

            def final_root(**arguments: object) -> dict[str, object]:
                output = Path(arguments["output_dir"])
                output.mkdir()
                (output / "system.private.squashfs").write_bytes(b"squashfs")
                return {}

            image = SimpleNamespace(
                image_sha256="1" * 64,
                payload_sha256="2" * 64,
                raw=b"personal-image",
            )
            with (
                mock.patch(
                    "installer.development_install.load_host_session",
                    return_value=SimpleNamespace(station_mdns_name="dcs6100-1234abcd.local"),
                ),
                mock.patch(
                    "installer.development_install.load_media_closure",
                    return_value=SimpleNamespace(closure_sha256="4" * 64),
                ),
                mock.patch("installer.development_install.probe_recovery_ap"),
                mock.patch(
                    "installer.development_install.extract_camera_vendor_bundle",
                    side_effect=extract,
                ),
                mock.patch(
                    "installer.development_install.load_vendor_bundle",
                    return_value=SimpleNamespace(bundle_sha256="3" * 64),
                ),
                mock.patch(
                    "installer.development_install.generate_private_config",
                    side_effect=configure,
                ) as generate_config,
                mock.patch(
                    "installer.development_install.load_service_credential",
                    return_value=b"a" * 64,
                ) as service_credential,
                mock.patch(
                    "installer.development_install.prepare_from_private_directory",
                    side_effect=final_root,
                ),
                mock.patch(
                    "installer.development_install.build_personal_mtd3_image",
                    return_value=image,
                ),
                mock.patch(
                    "installer.development_install.install_personal_mtd3",
                    return_value={
                        "image_sha256": "1" * 64,
                        "read_back_verified": True,
                        "safe_next_action": "activate_existing",
                        "written_mtd": [3],
                    },
                ) as install,
                mock.patch("installer.development_install.activate_personal_mtd3") as activate,
                mock.patch(
                    "installer.development_install.prove_thingino_health",
                    return_value={
                        "health_gate": "passed",
                        "mtd3_read_back_verified": True,
                        "mtd3_sha256": "1" * 64,
                        "station_mdns_name": "dcs6100-1234abcd.local",
                    },
                ),
            ):
                result = complete_personal_install(
                    session_dir=session,
                    base_rootfs=b"base",
                    media_closure_dir=root / "media-closure",
                    work_dir=work,
                    ssid="Private WiFi",
                    passphrase="private-passphrase",
                    expected_wpa_config_path=None,
                    mksquashfs=Path("mksquashfs"),
                    unsquashfs=Path("unsquashfs"),
                    station_timeout=30,
                )
            self.assertEqual(result["station_mdns_name"], "dcs6100-1234abcd.local")
            self.assertEqual(result["written_mtd"], [3])
            self.assertEqual(
                result["write_history"],
                [
                    {
                        "image_sha256": "1" * 64,
                        "mtd": 3,
                        "read_back_verified": True,
                        "source": "current-run",
                    }
                ],
            )
            self.assertEqual((work / "personal-mtd3.bin").read_bytes(), b"personal-image")
            install.assert_called_once()
            activate.assert_called_once()
            service_credential.assert_called_once_with(session)
            self.assertEqual(
                generate_config.call_args.kwargs["credential"],
                b"a" * 64 + b"\n",
            )

    def test_cli_exposes_resumable_completion(self) -> None:
        arguments = build_parser().parse_args(
            [
                "complete-personal-install",
                "--session-dir",
                "session",
                "--base-rootfs",
                "root.squashfs",
                "--media-closure-dir",
                "media-closure",
                "--work-dir",
                "private/install",
                "--mksquashfs",
                "mksquashfs",
                "--unsquashfs",
                "unsquashfs",
            ]
        )
        self.assertEqual(arguments.host, "192.168.88.1")
        self.assertEqual(arguments.station_timeout, 180)

    def test_resume_after_activation_proves_station_before_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session"
            (session / "host").mkdir(parents=True)
            (session / "host/identity.pub").write_bytes(b"ssh-ed25519 test\n")
            work = root / "work"
            work.mkdir()
            (work / "base-rootfs.squashfs").write_bytes(b"base")
            (work / "vendor-bundle").mkdir()
            (work / "install-config").mkdir()
            (work / "final-root").mkdir()
            (work / "personal-mtd3.bin").write_bytes(b"personal-image")
            (work / "install-state.private.json").write_text(
                '{"schema_version":1,"base_rootfs_sha256":'
                '"cae662172fd450bb0cd710a769079c05bfc5d8e35efa6576edc7d0377afdd4a2",'
                '"phase":"activation-requested","written_mtd":[3],'
                '"image_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}',
                encoding="utf-8",
            )
            image = SimpleNamespace(
                image_sha256="1" * 64,
                payload_sha256="2" * 64,
                raw=b"personal-image",
            )
            with (
                mock.patch(
                    "installer.development_install.load_host_session",
                    return_value=SimpleNamespace(station_mdns_name="dcs6100-1234abcd.local"),
                ),
                mock.patch(
                    "installer.development_install.load_media_closure",
                    return_value=SimpleNamespace(closure_sha256="4" * 64),
                ),
                mock.patch(
                    "installer.development_install.load_vendor_bundle",
                    return_value=SimpleNamespace(bundle_sha256="3" * 64),
                ),
                mock.patch(
                    "installer.development_install.validate_personal_mtd3_image",
                    return_value=image,
                ),
                mock.patch(
                    "installer.development_install.prove_thingino_health",
                    return_value={
                        "health_gate": "passed",
                        "mtd3_read_back_verified": True,
                        "mtd3_sha256": "1" * 64,
                        "station_mdns_name": "dcs6100-1234abcd.local",
                    },
                ),
                mock.patch("installer.development_install.install_personal_mtd3") as install,
                mock.patch("installer.development_install.activate_personal_mtd3") as activate,
            ):
                result = complete_personal_install(
                    session_dir=session,
                    base_rootfs=b"base",
                    media_closure_dir=root / "media-closure",
                    work_dir=work,
                    ssid=None,
                    passphrase=None,
                    expected_wpa_config_path=root / "current-wpa.conf",
                    mksquashfs=Path("mksquashfs"),
                    unsquashfs=Path("unsquashfs"),
                    station_timeout=30,
                )
            self.assertEqual(result["health_gate"], "passed")
            self.assertEqual(result["written_mtd"], [])
            self.assertEqual(
                result["write_history"],
                [
                    {
                        "image_sha256": "1" * 64,
                        "mtd": 3,
                        "read_back_verified": True,
                        "source": "legacy-state",
                    }
                ],
            )
            install.assert_not_called()
            activate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
