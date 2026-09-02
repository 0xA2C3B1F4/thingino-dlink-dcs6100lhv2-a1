from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from installer import vendor_bundle


def elf32_shared(*, soname: str, needed: list[str], machine: int = 8, flags: int = 0x70001007) -> bytes:
    strings = bytearray(b"\0")
    offsets: dict[str, int] = {}
    for value in [*needed, soname]:
        offsets[value] = len(strings)
        strings.extend(value.encode("ascii") + b"\0")
    string_offset = 0x80
    dynamic_offset = (string_offset + len(strings) + 7) & ~7
    dynamic = bytearray()
    for value in needed:
        dynamic.extend(struct.pack("<iI", 1, offsets[value]))
    dynamic.extend(struct.pack("<iI", 14, offsets[soname]))
    dynamic.extend(struct.pack("<iI", 0, 0))
    section_offset = (dynamic_offset + len(dynamic) + 3) & ~3
    raw = bytearray(section_offset + 3 * 40)
    identity = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into(
        "<16sHHIIIIIHHHHHH",
        raw,
        0,
        identity,
        3,
        machine,
        1,
        0,
        0,
        section_offset,
        flags,
        52,
        0,
        0,
        40,
        3,
        0,
    )
    raw[string_offset:string_offset + len(strings)] = strings
    raw[dynamic_offset:dynamic_offset + len(dynamic)] = dynamic
    struct.pack_into("<IIIIIIIIII", raw, section_offset + 40, 0, 3, 0, 0, string_offset, len(strings), 0, 0, 1, 0)
    struct.pack_into("<IIIIIIIIII", raw, section_offset + 80, 0, 6, 0, 0, dynamic_offset, len(dynamic), 1, 0, 4, 8)
    return bytes(raw)


def make_bundle(root: Path) -> tuple[Path, Path, dict[str, bytes]]:
    catalog = json.loads(vendor_bundle.CATALOG_PATH.read_text(encoding="utf-8"))
    bundle = root / "bundle"
    files = bundle / vendor_bundle.FILES_DIRECTORY
    files.mkdir(parents=True)
    raws: dict[str, bytes] = {}
    for entry in catalog["files"]:
        raw = elf32_shared(soname=entry["elf"]["soname"], needed=entry["elf"]["needed"])
        entry["size"] = len(raw)
        entry["sha256"] = hashlib.sha256(raw).hexdigest()
        raws[entry["name"]] = raw
        (files / entry["name"]).write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "target": catalog["target"],
        "source": {
            "firmware_version": catalog["source"]["firmware_version"],
            "mounted_read_only": True,
            "partition": catalog["source"]["partition"],
        },
        "files": [
            {
                "name": entry["name"],
                "sha256": entry["sha256"],
                "size": entry["size"],
                "source_path": entry["source_path"],
            }
            for entry in catalog["files"]
        ],
    }
    (bundle / vendor_bundle.MANIFEST_NAME).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    catalog_path = root / "catalog.json"
    catalog_path.write_text(json.dumps(catalog) + "\n", encoding="utf-8")
    return bundle, catalog_path, raws


class VendorBundleTests(unittest.TestCase):
    def test_exact_camera_local_closure_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            bundle_path, catalog_path, raws = make_bundle(Path(directory_name))

            bundle = vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)

            self.assertEqual([artifact.name for artifact in bundle.artifacts], list(raws))
            self.assertEqual(bundle.firmware_version, "1.02.02")
            self.assertEqual(len(bundle.bundle_sha256), 64)
            self.assertEqual(bundle.artifacts[0].elf.flags, 0x70001007)

    def test_extra_missing_or_linked_files_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, _raws = make_bundle(root)
            (bundle_path / "files/unexpected.so").write_bytes(b"no")
            with self.assertRaisesRegex(vendor_bundle.VendorBundleError, "extra"):
                vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)

            (bundle_path / "files/unexpected.so").unlink()
            target = bundle_path / "files/libimp.so"
            saved = target.read_bytes()
            target.unlink()
            target.symlink_to(bundle_path / "files/libalog.so")
            with self.assertRaisesRegex(vendor_bundle.VendorBundleError, "cannot read"):
                vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)
            target.unlink()
            target.write_bytes(saved)

    def test_hash_target_mount_and_elf_abi_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, _raws = make_bundle(root)
            library = bundle_path / "files/libimp.so"
            changed = bytearray(library.read_bytes())
            changed[-1] ^= 1
            library.write_bytes(changed)
            with self.assertRaisesRegex(vendor_bundle.VendorBundleError, "hash mismatch"):
                vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, _raws = make_bundle(root)
            manifest_path = bundle_path / vendor_bundle.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text())
            manifest["source"]["mounted_read_only"] = False
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(vendor_bundle.VendorBundleError, "read-only"):
                vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)

        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, _raws = make_bundle(root)
            catalog = json.loads(catalog_path.read_text())
            bad = elf32_shared(
                soname=catalog["files"][0]["elf"]["soname"],
                needed=catalog["files"][0]["elf"]["needed"],
                machine=3,
            )
            catalog["files"][0]["size"] = len(bad)
            catalog["files"][0]["sha256"] = hashlib.sha256(bad).hexdigest()
            catalog_path.write_text(json.dumps(catalog))
            target = bundle_path / "files/libimp.so"
            target.write_bytes(bad)
            manifest_path = bundle_path / vendor_bundle.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text())
            manifest["files"][0]["size"] = len(bad)
            manifest["files"][0]["sha256"] = hashlib.sha256(bad).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(vendor_bundle.VendorBundleError, "MIPS ELF ABI"):
                vendor_bundle.load_vendor_bundle(bundle_path, catalog_path=catalog_path)

    def test_extractor_requires_exact_read_only_mtd3_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            source_bundle, catalog_path, raws = make_bundle(root)
            stock = root / "stock-mtd3"
            (stock / "lib").mkdir(parents=True)
            for name, raw in raws.items():
                (stock / "lib" / name).write_bytes(raw)
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"31 22 31:3 / {stock.resolve()} ro,nosuid,nodev - jffs2 /dev/mtdblock3 ro\n",
                encoding="utf-8",
            )
            output = root / "extracted"

            extracted = vendor_bundle.extract_vendor_bundle(
                mtd3_mount=stock,
                mountinfo_path=mountinfo,
                output_dir=output,
                catalog_path=catalog_path,
            )

            self.assertEqual({artifact.name for artifact in extracted.artifacts}, set(raws))
            self.assertTrue((output / vendor_bundle.MANIFEST_NAME).is_file())
            self.assertEqual((output / "files/libaudioProcess.so").read_bytes(), raws["libaudioProcess.so"])
            self.assertTrue(source_bundle.is_dir())

            mountinfo.write_text(
                f"31 22 31:3 / {stock.resolve()} rw,nosuid,nodev - jffs2 /dev/mtdblock3 rw\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vendor_bundle.VendorBundleError, "read-only"):
                vendor_bundle.extract_vendor_bundle(
                    mtd3_mount=stock,
                    mountinfo_path=mountinfo,
                    output_dir=root / "rejected",
                    catalog_path=catalog_path,
                )

    def test_build_site_contains_only_three_required_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bundle_path, catalog_path, raws = make_bundle(root)
            output = root / "build-site"

            site = vendor_bundle.prepare_vendor_build_site(
                vendor_bundle_dir=bundle_path,
                output_dir=output,
                catalog_path=catalog_path,
            )

            self.assertEqual(site.files, ("libimp.so", "libalog.so", "libsysutils.so"))
            self.assertEqual(
                {path.name for path in (output / "files").iterdir()},
                set(site.files),
            )
            self.assertFalse((output / "files/libaudioProcess.so").exists())
            self.assertEqual((output / "files/libimp.so").read_bytes(), raws["libimp.so"])
            manifest = json.loads((output / "build-site.private.json").read_text())
            self.assertTrue(manifest["policy"]["archive_only_files_excluded"])
            self.assertFalse(manifest["policy"]["public_ingenic_lib_archive"])


if __name__ == "__main__":
    unittest.main()
