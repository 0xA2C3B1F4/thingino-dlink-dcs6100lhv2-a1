from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import final_root
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
            json.dumps({"server": {"username": "", "password": ""}}),
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
    ):
        source = parent / "source"
        source.mkdir()
        self._universal_tree(source, raptor=raptor)
        calls: list[bytes] = []

        def extract(*, destination: Path, **_values: object) -> None:
            shutil.copytree(source, destination)

        def mkfs(arguments: list[str], _label: str, **_values: object) -> bytes:
            overlay = Path(arguments[arguments.index("--root") + 1])
            output = Path(arguments[arguments.index("--output") + 1])
            identity = self._tree_identity(overlay)
            rwd = overlay / "root/etc/init.d/S96rwd"
            self.assertEqual(rwd.exists(), raptor)
            if raptor:
                self.assertTrue(rwd.stat().st_mode & 0o111)
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
                credential=b"b" * 64 + b"\n",
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
        return image, output, calls

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

    def test_source_native_media_does_not_require_raptor_init(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            image, output, calls = self._build(
                Path(directory_name),
                raptor=False,
            )
            self.assertEqual(len(image.raw), DATA_FLASH_SPAN)
            self.assertEqual(output.read_bytes(), image.raw)
            self.assertEqual(calls, [calls[0], calls[0]])

    def test_nonreproducible_mkfs_fails_without_published_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            parent = Path(directory_name)
            with self.assertRaisesRegex(
                ProvisioningDataError, "not byte-identical"
            ):
                self._build(parent, divergent_second=True)
            self.assertFalse((parent / "camera.jffs2").exists())

    def test_provisioning_reactivates_raptor_rwd(self) -> None:
        self.assertNotIn("etc/init.d/S96rwd", final_root.UNIVERSAL_DISABLED_INIT)
        self.assertIn("etc/init.d/S96rwd", _RUNTIME_PATHS)

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

    def test_receipt_path_is_inside_overlay_upper(self) -> None:
        self.assertEqual(PROVISIONING_RECEIPT, "etc/dcs6100-provisioning.json")


if __name__ == "__main__":
    unittest.main()
