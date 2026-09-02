from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer import media_closure


def _config(*, height: int = 1080) -> bytes:
    return (
        json.dumps(
            {
                "sensor": {"fps": 25, "model": "os02g10"},
                "stream0": {
                    "enabled": True,
                    "fps": 15,
                    "height": height,
                    "width": 1920,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


class MediaClosureTests(unittest.TestCase):
    def _write_fixture(
        self,
        root: Path,
        *,
        config: bytes | None = None,
    ) -> dict[str, str]:
        files = {
            "bin/prudynt": b"fake-prudynt",
            "etc/prudynt.json": config or _config(),
            "init/F01datetime": b"#!/bin/sh\n",
            "init/S06ircut": b"#!/bin/sh\n",
            "init/S10daynightd": b"#!/bin/sh\n",
            "init/S11modules": b"#!/bin/sh\n",
            "init/S31prudynt": b"#!/bin/sh\n",
            "lib/libaudioProcess.so": b"fake-audio",
            "lib/libimp.so": b"fake-imp-libaudioProcess.so",
            "modules/sensor_os02g10_t31.ko": b"fake-sensor-module",
            "modules/tx-isp-t31.ko": b"fake-isp-module",
            "sensor/os02g10-t31.bin": b"fake-iq",
        }
        identities: dict[str, str] = {}
        entries: list[dict[str, object]] = []
        for relative, raw in sorted(files.items()):
            path = root / media_closure.FILES_DIRECTORY / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            identities[relative] = digest
            entries.append({"path": relative, "sha256": digest, "size": len(raw)})
        manifest = {
            "schema_version": 2,
            "target": media_closure.TARGET,
            "runtime": "single-glibc-c1-reconstruction",
            "interpreter": "/lib/ld.so.1",
            "startup_order": list(media_closure.STARTUP_ORDER),
            "preconditions": ["F01datetime", "/dev/shm"],
            "runtime_dlopen": ["libaudioProcess.so"],
            "native_warmup": False,
            "chroot": False,
            "two_stage_markers": False,
            "s14_volatile_config": False,
            "files": entries,
        }
        (root / media_closure.MANIFEST_NAME).write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        return identities

    def _load(self, root: Path, identities: dict[str, str]) -> media_closure.MediaClosure:
        with (
            patch.object(media_closure, "PROVEN_IDENTITIES", identities),
            patch.object(media_closure, "_validate_runtime"),
        ):
            return media_closure.load_media_closure(root)

    def test_closed_manifest_loads_and_preserves_config_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root)
            closure = self._load(root, identities)
            self.assertEqual(closure.by_path()["etc/prudynt.json"].raw, _config())
            self.assertEqual(closure.startup_order, media_closure.STARTUP_ORDER)
            self.assertEqual(closure.runtime_dlopen, ("libaudioProcess.so",))

    def test_file_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root)
            (root / "files/etc/prudynt.json").write_bytes(b"changed")
            with self.assertRaisesRegex(media_closure.MediaClosureError, "identity mismatch"):
                self._load(root, identities)

    def test_extra_file_and_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root)
            (root / "files/extra").write_bytes(b"extra")
            with self.assertRaisesRegex(media_closure.MediaClosureError, "extra or missing"):
                self._load(root, identities)
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root)
            (root / "files/link").symlink_to("etc/prudynt.json")
            with self.assertRaisesRegex(media_closure.MediaClosureError, "symlink"):
                self._load(root, identities)

    def test_unsafe_path_and_two_stage_contract_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root)
            path = root / media_closure.MANIFEST_NAME
            document = json.loads(path.read_text(encoding="utf-8"))
            document["files"][0]["path"] = "../escape"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(media_closure.MediaClosureError, "unsafe"):
                self._load(root, identities)
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root)
            path = root / media_closure.MANIFEST_NAME
            document = json.loads(path.read_text(encoding="utf-8"))
            document["native_warmup"] = True
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(media_closure.MediaClosureError, "single-runtime contract"):
                self._load(root, identities)

    def test_config_must_remain_the_1080p_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            identities = self._write_fixture(root, config=_config(height=720))
            with self.assertRaisesRegex(media_closure.MediaClosureError, "proven 1080p"):
                self._load(root, identities)

    def test_exact_c1_identities_include_dlopen_and_exclude_old_prudynts(self) -> None:
        expected = {
            "bin/prudynt": "5e4fafcf1971b4eeaef39ec3ac06ef0cb25f96056427d4b9b1477b0535be49c9",
            "etc/prudynt.json": "e2df0caa0f03b862c888db7b0406cef904c630c184b3468dbd62dfd5a8a119fb",
            "lib/libimp.so": "14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe",
            "lib/libalog.so": "c2dff5a7ab4183c41781e3c2a879ef3aad3fc1feb3412b4c68a22eda6566c3a5",
            "lib/libsysutils.so": "d38e37b9746aebad62cf2ed906b099a2d22f7d44d0972cf516443b542dae6c55",
            "lib/libaudioProcess.so": "f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273",
            "modules/tx-isp-t31.ko": "d13b5654e858155d07ce10dce97477549974227a90d02c37737907221fc2a573",
            "modules/sensor_os02g10_t31.ko": "4b034950cd9450f9c1cfdff19bcf0d92a1cf69fd412218c1841706ef9cdde8be",
            "sensor/os02g10-t31.bin": "dce8af706b8663bcefe47b38418fbec469d7b3704665a45544f8bb1845e5f78e",
        }
        for path, digest in expected.items():
            self.assertEqual(media_closure.PROVEN_IDENTITIES[path], digest)
        values = set(media_closure.PROVEN_IDENTITIES.values())
        self.assertNotIn("709b587d3e257d4c8edc80cd1581a4630ec7628aaca472c1744ec32db377879a", values)
        self.assertNotIn("6fb62914cd1b6cc627e9903a297bb67ae75ec231057d6964d8fc3443278069f2", values)

    def test_libaudio_process_is_required_beyond_dt_needed(self) -> None:
        files = {
            "bin/prudynt": media_closure.MediaClosureFile(
                "bin/prudynt", b"/lib/ld.so.1 /etc/sensor/os02g10-t31.bin", "a" * 64
            ),
            "lib/libimp.so": media_closure.MediaClosureFile(
                "lib/libimp.so", b"no-dlopen-target", "b" * 64
            ),
            "lib/libalog.so": media_closure.MediaClosureFile("lib/libalog.so", b"x", "c" * 64),
            "lib/libsysutils.so": media_closure.MediaClosureFile("lib/libsysutils.so", b"x", "d" * 64),
            "lib/libaudioProcess.so": media_closure.MediaClosureFile("lib/libaudioProcess.so", b"x", "e" * 64),
            "modules/tx-isp-t31.ko": media_closure.MediaClosureFile(
                "modules/tx-isp-t31.ko", media_closure.KERNEL_VERMAGIC, "f" * 64
            ),
            "modules/sensor_os02g10_t31.ko": media_closure.MediaClosureFile(
                "modules/sensor_os02g10_t31.ko", media_closure.KERNEL_VERMAGIC, "0" * 64
            ),
        }
        with patch.object(
            media_closure,
            "parse_elf32_mips",
            return_value=SimpleNamespace(needed=()),
        ):
            with self.assertRaisesRegex(media_closure.MediaClosureError, "runtime dlopen"):
                media_closure._validate_runtime(files)


if __name__ == "__main__":
    unittest.main()
