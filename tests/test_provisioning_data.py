from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import final_root, raptor_provisioning
from installer.private_config import derive_rtsp_viewer_credential
from installer.mtd3_split import DATA_FLASH_SPAN
from installer.provisioning_data import (
    PROVISIONING_RECEIPT,
    _RUNTIME_PATHS,
    ProvisioningDataError,
    build_provisioning_data_image,
)


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class ProvisioningDataTests(unittest.TestCase):
    def _universal_tree(self, root: Path, *, raptor: bool = True) -> None:
        (root / "etc/init.d").mkdir(parents=True)
        (root / "etc/dropbear").mkdir(parents=True)
        (root / "root/.ssh").mkdir(parents=True)
        (root / "etc/shadow").write_text(
            "root:!:1:2:3:4:5:6:7\n", encoding="utf-8"
        )
        (root / "etc/hostname").write_text(
            final_root.UNIVERSAL_HOSTNAME + "\n", encoding="ascii"
        )
        scripts: dict[str, bytes] = {}
        for relative in final_root.UNIVERSAL_DISABLED_INIT:
            if relative.endswith("S30dropbear"):
                raw = b'#!/bin/sh\nDAEMON_ARGS="-k -K 300 -R"\nstart() {\n\t:\n}\n'
            else:
                raw = b"#!/bin/sh\nexit 0\n"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o644)
            scripts[path.name] = raw
        if raptor:
            rwd = root / "etc/init.d/S96rwd"
            rwd.write_bytes(b"#!/bin/sh\nexit 0\n")
            rwd.chmod(0o644)
        archive = root / "usr/share/thingino-provisioning/init"
        archive.mkdir(parents=True)
        for name, raw in scripts.items():
            path = archive / name
            path.write_bytes(raw)
            path.chmod(0o755)
        marker = {
            "artifact_scope": "model-universal",
            "contains_device_secrets": False,
            "provisioning_required": True,
            "schema_version": 1,
            "target": "DCS-6100LHV2-A1",
        }
        (root / final_root.UNIVERSAL_MARKER).write_text(
            json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        (root / "etc/onvif.json").write_text(
            json.dumps({"server": {"username": "", "password": ""},
                        "adv_enable_media2": False,
                        "profiles": {"stream0": {"name": "Profile_0", "width": 1920},
                                     "stream1": {"name": "Profile_1", "width": 640}},
                        "unknown_fixture_field": {"keep": [False, "unchanged", 37]}}),
            encoding="utf-8",
        )
        (root / "etc/thingino.json").write_text(
            json.dumps(
                {
                    "control": {"enabled": False},
                    "daynight": {"controls": {"color": False}},
                    "gpio": {"ircut": ""},
                    "ha": {"camera_interval": 30},
                }
            ),
            encoding="utf-8",
        )
        prudynt = {
            "audio": {},
            "http": {},
            "motion": {},
            "osd": {
                "burnin": {},
                "privacy": {},
                "sei": {},
            },
            "rtsp": {"username": "", "password": ""},
            "sensor": {},
            "stream0": {},
            "stream1": {},
            "stream2": {"fps": 0, "jpeg_idle_fps": 0, "jpeg_quality": 70},
            "stream3": {"fps": 0, "jpeg_idle_fps": 0, "jpeg_quality": 70},
        }
        prudynt_raw = (json.dumps(prudynt, sort_keys=True) + "\n").encode()
        (root / "etc/prudynt.json").write_bytes(prudynt_raw)
        (root / "etc/dlink-media-closure.private.json").write_text(
            json.dumps(
                {
                    "files": [
                        {
                            "path": "etc/prudynt.json",
                            "sha256": hashlib.sha256(prudynt_raw).hexdigest(),
                            "size": len(prudynt_raw),
                        }
                    ],
                    "runtime_config_sha256": hashlib.sha256(prudynt_raw).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _tree_identity(root: Path) -> bytes:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
            relative = str(path.relative_to(root)).encode()
            digest.update(relative + b"\0")
            digest.update((path.stat().st_mode & 0o777).to_bytes(2, "big"))
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.digest()

    def _build(
        self,
        parent: Path,
        *,
        divergent_second: bool = False,
        raptor: bool = True,
        full_raptor: bool = True,
        credential: bytes = b"b" * 64 + b"\n",
        captured_overlay: dict[str, bytes] | None = None,
    ):
        source = parent / "source"
        source.mkdir()
        self._universal_tree(source, raptor=raptor and not full_raptor)
        if full_raptor:
            self._full_raptor_tree(source)
        original_identity = self._tree_identity(source)
        calls: list[bytes] = []

        def extract(*, destination: Path, **_values: object) -> None:
            shutil.copytree(source, destination)

        def mkfs(arguments: list[str], _label: str, **_values: object) -> bytes:
            overlay = Path(arguments[arguments.index("--root") + 1])
            output = Path(arguments[arguments.index("--output") + 1])
            identity = self._tree_identity(overlay)
            self.assertTrue((overlay / "etc/hostname").is_file())
            self.assertTrue((overlay / "etc/wpa_supplicant.conf").is_file())
            self.assertFalse((overlay / "root/etc/hostname").exists())
            self.assertFalse((overlay / "work").exists())
            onvif = json.loads((overlay / "etc/onvif.json").read_bytes())
            self.assertIs(onvif["adv_enable_media2"], True)
            self.assertEqual(onvif["server"]["username"], "root")
            self.assertEqual(onvif["server"]["password"], credential.decode("ascii").strip())
            expected_onvif = json.loads((source / "etc/onvif.json").read_bytes())
            expected_onvif["server"] = {"username": "root", "password": credential.decode("ascii").strip()}
            expected_onvif["adv_enable_media2"] = True
            self.assertEqual(onvif, expected_onvif)
            rwd = overlay / "etc/init.d/S96rwd"
            self.assertEqual(rwd.exists(), raptor and not full_raptor)
            if raptor and not full_raptor:
                self.assertTrue(rwd.stat().st_mode & 0o111)
            if full_raptor:
                self.assertFalse((overlay / "etc/prudynt.json").exists())
                self.assertTrue((overlay / raptor_provisioning.SERVICE).stat().st_mode & 0o111)
                for relative in raptor_provisioning.CONFIGS:
                    self.assertTrue((overlay / relative).is_file())
                self.assertEqual(
                    (overlay / "etc/init.d/S95thingino-control").read_bytes(),
                    raptor_provisioning.CONTROL_INIT,
                )
            if captured_overlay is not None:
                captured_overlay.update({
                    path.relative_to(overlay).as_posix(): path.read_bytes()
                    for path in overlay.rglob("*") if path.is_file()
                })
            calls.append(identity)
            if divergent_second and len(calls) == 2:
                identity = hashlib.sha256(identity).digest()
            raw = b"\x85\x19" + identity
            raw += b"\xff" * (DATA_FLASH_SPAN - len(raw))
            output.write_bytes(raw)
            return b""

        output = parent / "camera.jffs2"
        with (
            mock.patch("installer.provisioning_data.validate_squashfs"),
            mock.patch("installer.provisioning_data._extract_base_root", side_effect=extract),
            mock.patch("installer.provisioning_data._run", side_effect=mkfs),
        ):
            image = build_provisioning_data_image(
                universal_rootfs=b"universal-system",
                wpa_config=b'network={\nssid="camera"\npsk=' + b"a" * 64 + b"\n}\n",
                credential=credential,
                api_key=b"c" * 64 + b"\n",
                authorized_key=b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr38ca5R64nSROxDExscFYDU8yWc42nuvRoOVXZLzCk camera\n",
                dropbear_host_key=b"ssh-ed25519 " + b"k" * 96,
                station_hostname="dcs6100-12345678",
                camera_identity_sha256="1" * 64,
                universal_firmware_sha256="2" * 64,
                recovery_session_sha256="3" * 64,
                provisioning_id="4" * 64,
                credential_set_id="5" * 64,
                unsquashfs=parent / "unsquashfs",
                mkfs_jffs2=parent / "mkfs.jffs2",
                output_path=output,
            )
        self.assertEqual(self._tree_identity(source), original_identity)
        return image, output, calls

    def _full_raptor_tree(self, root: Path) -> None:
        (root / "etc/prudynt.json").unlink()
        service = root / raptor_provisioning.SERVICE
        service.write_bytes(b"#!/bin/sh\nexit 0\n")
        service.chmod(0o644)
        for relative in (
            "etc/init.d/S95thingino-control",
            "usr/share/thingino-provisioning/init/S95thingino-control",
        ):
            (root / relative).write_bytes(raptor_provisioning.CONTROL_INIT)
        entries = []
        repository = Path(__file__).resolve().parents[1]
        for relative in raptor_provisioning.CONFIGS:
            raw = (repository / "components/raptor" / Path(relative).name).read_bytes()
            (root / relative).write_bytes(raw)
            entries.append({
                "path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            })
        (root / "etc/dlink-media-closure.private.json").write_text(json.dumps({
            "runtime": raptor_provisioning.RUNTIME, "files": entries,
        }))

    def test_full_raptor_uses_one_model_with_distinct_camera_overlays(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            overlays = []
            images = []
            for index, token in enumerate((b"b", b"d")):
                camera = parent / str(index)
                camera.mkdir()
                captured = {}
                credential = token * 64 + b"\n"
                image, _, calls = self._build(
                    camera, full_raptor=True, credential=credential,
                    captured_overlay=captured,
                )
                self.assertEqual(calls, [calls[0], calls[0]])
                media = captured[raptor_provisioning.CONFIGS[0]]
                self.assertIn(b"port = 554", media)
                self.assertIn(b"bind_address = 0.0.0.0", media)
                self.assertIn(derive_rtsp_viewer_credential(credential).strip(), media)
                self.assertNotIn(credential.strip(), media)
                overlays.append(captured)
                images.append(image)
            self.assertNotEqual(images[0].sha256, images[1].sha256)
            self.assertEqual(
                self._tree_identity(parent / "0/source"),
                self._tree_identity(parent / "1/source"),
            )
            for relative in raptor_provisioning.CONFIGS[1:]:
                self.assertEqual(overlays[0][relative], overlays[1][relative])

    def test_full_raptor_rejects_an_active_legacy_media_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            self._universal_tree(root, raptor=False)
            self._full_raptor_tree(root)
            final_root._validate_universal_tree(root)
            (root / "etc/init.d/S31prudynt").write_bytes(b"#!/bin/sh\nexit 0\n")
            with self.assertRaisesRegex(final_root.FinalRootError, "legacy media owner"):
                final_root._validate_universal_tree(root)

    def test_full_raptor_rejects_modified_config_and_duplicate_control_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            self._universal_tree(root, raptor=False)
            self._full_raptor_tree(root)
            config = root / raptor_provisioning.CONFIGS[0]
            original = config.read_bytes()
            config.write_bytes(original + b"\n# changed\n")
            with self.assertRaisesRegex(final_root.FinalRootError, "provenance changed"):
                final_root._validate_universal_tree(root)
            config.write_bytes(original)
            (root / "usr/share/thingino-provisioning/init/S95thingino-control").write_bytes(b"start old control\n")
            with self.assertRaisesRegex(final_root.FinalRootError, "only Control startup owner"):
                final_root._validate_universal_tree(root)

    def test_complete_overlay_is_reproducible_and_secret_repr_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            image, output, calls = self._build(Path(directory_name))
            self.assertEqual(len(image.raw), DATA_FLASH_SPAN)
            self.assertEqual(output.read_bytes(), image.raw)
            self.assertEqual(calls, [calls[0], calls[0]])
            self.assertEqual(image.receipt["state"], "committed")
            self.assertNotIn("ssh-ed25519", repr(image))
            self.assertNotIn("bbbbbbbb", repr(image))
            self.assertEqual(image.receipt["provisioning_id"], "4" * 64)

    def test_source_native_media_is_rejected_without_full_raptor_init(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            with self.assertRaisesRegex(
                ProvisioningDataError, "universal root lacks the Raptor provisioning service"
            ):
                self._build(Path(directory_name), raptor=False, full_raptor=False)

    def test_nonreproducible_mkfs_fails_without_published_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            with self.assertRaisesRegex(
                ProvisioningDataError, "not byte-identical"
            ):
                self._build(parent, divergent_second=True)
            self.assertFalse((parent / "camera.jffs2").exists())

    def test_provisioning_enables_full_raptor_service(self) -> None:
        self.assertNotIn("etc/init.d/S96rwd", final_root.UNIVERSAL_DISABLED_INIT)
        self.assertIn(raptor_provisioning.SERVICE, _RUNTIME_PATHS)
        self.assertNotIn("etc/init.d/S96rwd", _RUNTIME_PATHS)

    def test_container_wrapper_is_offline_fixed_geometry_and_crc_checked(self) -> None:
        script = Path("scripts/run_container_mkfs_jffs2.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--network none", script)
        self.assertIn("--eraseblock=0x8000", script)
        self.assertIn("--pagesize=0x100", script)
        self.assertIn("--pad=1507328", script)
        self.assertIn("jffs2dump -c", script)
        self.assertNotIn("curl ", script)
        self.assertNotIn("wget ", script)
        self.assertNotIn("/tmp/jffs2-errors", script)

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell is required")
    def test_container_crc_check_rejects_stderr_and_nonzero_status(self) -> None:
        script = Path("scripts/run_container_mkfs_jffs2.sh").read_text(encoding="utf-8")
        check = next(line.strip()[1:-1] for line in script.splitlines()
                     if line.strip().startswith("'errors=$(jffs2dump"))
        for body, expected in (("return 0", 0),
                               ("printf 'CRC error' >&2; return 0", 1),
                               ("return 7", 7)):
            with self.subTest(body=body):
                result = subprocess.run(
                    [shutil.which("sh"), "-c", "jffs2dump() { " + body + "; }; " + check],
                    check=False, capture_output=True,
                )
                self.assertEqual(result.returncode, expected)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")

    def test_receipt_path_is_inside_overlay_upper(self) -> None:
        self.assertEqual(PROVISIONING_RECEIPT, "etc/dcs6100-provisioning.json")


if __name__ == "__main__":
    unittest.main()
