"""Full media archives bind all owners to one source/build generation."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from installer import raptor_full_component as component
from installer.raptor_source import recipe_identity, source_lock
ROOT = Path(__file__).resolve().parents[1]
FONT_FIXTURE = b"\x00\x01\x00\x00" + b"fixture" * 50_545
FONT_FIXTURE = FONT_FIXTURE[:353_824].ljust(353_824, b"x")


def motion_clip_fixture():
    samples = bytearray()
    for index in range(4_000):
        phase = index % 16
        triangle = min(phase, 16 - phase)
        amplitude = (triangle * 2 - 8) * 750
        envelope = min(index, 3_999 - index, 320)
        samples.extend(struct.pack("<h", amplitude * envelope // 320))
    return bytes(samples)


MOTION_CLIP_FIXTURE = motion_clip_fixture()


def elf(dependency="libc.so.6", alignment=4096, extra_tag=0):
    raw = bytearray(1024)
    raw[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<HHI", raw, 16, 3, 8, 1)
    struct.pack_into("<I", raw, 28, 52)
    struct.pack_into("<I", raw, 36, 0x70001000)
    raw[700:713] = b"/lib/ld.so.1\0"
    struct.pack_into("<HHH", raw, 40, 52, 32, 2)
    struct.pack_into("<8I", raw, 52, 1, 0, 0, 0, 1024, 1024, 5, alignment)
    struct.pack_into("<8I", raw, 84, 2, 256, 256, 256, 32, 32, 6, 4)
    struct.pack_into("<8I", raw, 256, 5, 512, 1, 0, extra_tag, 0, 0, 0)
    value = dependency.encode() + b"\0"
    raw[512 : 512 + len(value)] = value
    return bytes(raw)


class FullComponentTests(unittest.TestCase):
    def setUp(self):
        license_fixture = b"fixture font licence\n"
        self.files = {name: elf() for name in component.PAYLOAD - component.DATA_FILES}
        self.files[component.FONT] = FONT_FIXTURE
        self.files[component.FONT_LICENSE] = license_fixture
        self.files[component.LIBSCHRIFT_LICENSE] = b"fixture libschrift licence\n"
        self.files[component.CJSON_NOTICE] = (
            ROOT / "third_party/licenses/raptor-common-cJSON-header.txt"
        ).read_bytes()
        self.files[component.MONOCYPHER_LICENSE] = (
            ROOT / "third_party/licenses/raptor-common-Monocypher-LICENCE.txt"
        ).read_bytes()
        self.files[component.MOTION_CLIP] = MOTION_CLIP_FIXTURE
        for name, value in (
            ("FONT_SHA256", hashlib.sha256(FONT_FIXTURE).hexdigest()),
            ("FONT_LICENSE_SHA256", hashlib.sha256(license_fixture).hexdigest()),
            ("LIBSCHRIFT_LICENSE_SHA256", hashlib.sha256(
                self.files[component.LIBSCHRIFT_LICENSE]
            ).hexdigest()),
        ):
            replacement = patch.object(component, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.inputs = {
            "base_rootfs_sha256": "1" * 64,
            "builder_image_id": "sha256:" + "2" * 64,
            "toolchain_sha256": json.loads((ROOT / "sources.lock.json").read_bytes())[
                "sources"]["thingino_build_toolchain_aarch64"]["sha256"],
        }
        self.provenance = {
            "recipe": recipe_identity(ROOT, full_media=True),
            "source_trees": {name: spec["tree"] for name, spec in source_lock(ROOT, full_media=True)["sources"].items()},
            "build_inputs": self.inputs,
        }

    def validate(self, raw, *, inputs=None):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "component.tar.gz"
            artifact.write_bytes(raw)
            return component.validate_component(
                artifact, expected_sha256=hashlib.sha256(raw).hexdigest(), root=ROOT,
                expected_build_inputs=self.inputs if inputs is None else inputs,
            )

    def test_round_trip_and_deterministic_bytes(self):
        raw = component.pack_component(self.files, self.provenance)
        self.assertEqual(raw, component.pack_component(self.files, self.provenance))
        files, manifest = self.validate(raw)
        self.assertEqual(files, self.files)
        self.assertEqual(manifest["kind"], "raptor-full-media-component")

    def test_missing_daemon_or_unrelated_payload_is_rejected(self):
        for name in component.PAYLOAD:
            with self.subTest(missing=name), self.assertRaises(ValueError):
                component.pack_component({path: raw for path, raw in self.files.items() if path != name}, self.provenance)
        with self.assertRaises(ValueError):
            component.pack_component({**self.files, "etc/credential": b"not a component input"}, self.provenance)

    def test_only_sensor_owners_can_link_vendor_hal(self):
        for name in component.PAYLOAD - component.DATA_FILES:
            files = {**self.files, name: elf(dependency="libimp.so")}
            if name in {"usr/bin/rvd", "usr/bin/rad"}:
                component.audit_payload(files)
            else:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unapproved dynamic"):
                    component.audit_payload(files)

        files = {**self.files, "usr/bin/rod": elf(dependency="libschrift.so")}
        with self.assertRaisesRegex(ValueError, "unapproved dynamic"):
            component.audit_payload(files)

    def test_font_and_licence_are_non_executable_and_identity_bound(self):
        raw = component.pack_component(self.files, self.provenance)
        self.validate(raw)
        for name in (component.FONT, component.FONT_LICENSE, component.LIBSCHRIFT_LICENSE):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "font or licence"):
                changed = {**self.files, name: self.files[name] + b"changed"}
                component.pack_component(changed, self.provenance)
            self.assertEqual(component.payload_mode(name), 0o644)

    def test_vendored_notices_are_required_non_executable_and_identity_bound(self):
        for name, expected in (
            (component.CJSON_NOTICE, component.CJSON_NOTICE_SHA256),
            (component.MONOCYPHER_LICENSE, component.MONOCYPHER_LICENSE_SHA256),
        ):
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256(self.files[name]).hexdigest(), expected)
                self.assertEqual(component.payload_mode(name), 0o644)
                with self.assertRaisesRegex(ValueError, "vendored notice identity"):
                    component.pack_component(
                        {**self.files, name: self.files[name] + b"changed"},
                        self.provenance,
                    )
        files, manifest = self.validate(component.pack_component(self.files, self.provenance))
        self.assertEqual(files[component.CJSON_NOTICE], self.files[component.CJSON_NOTICE])
        self.assertEqual(files[component.MONOCYPHER_LICENSE], self.files[component.MONOCYPHER_LICENSE])
        self.assertEqual(manifest["files"][component.CJSON_NOTICE], component.CJSON_NOTICE_SHA256)
        self.assertEqual(manifest["files"][component.MONOCYPHER_LICENSE], component.MONOCYPHER_LICENSE_SHA256)

    def test_motion_clip_is_required_non_executable_and_identity_bound(self):
        missing = {name: data for name, data in self.files.items() if name != component.MOTION_CLIP}
        with self.assertRaisesRegex(ValueError, "must contain"):
            component.pack_component(missing, self.provenance)
        self.assertEqual(len(MOTION_CLIP_FIXTURE), 8_000)
        self.assertEqual(hashlib.sha256(MOTION_CLIP_FIXTURE).hexdigest(), component.MOTION_CLIP_SHA256)
        self.assertEqual(component.payload_mode(component.MOTION_CLIP), 0o644)
        for changed in (b"", MOTION_CLIP_FIXTURE[:-1], b"\x01" + MOTION_CLIP_FIXTURE[1:]):
            with self.subTest(size=len(changed)), self.assertRaisesRegex(ValueError, "motion clip identity"):
                component.pack_component({**self.files, component.MOTION_CLIP: changed}, self.provenance)
        files, manifest = self.validate(component.pack_component(self.files, self.provenance))
        self.assertEqual(files[component.MOTION_CLIP], MOTION_CLIP_FIXTURE)
        self.assertEqual(manifest["files"][component.MOTION_CLIP], component.MOTION_CLIP_SHA256)

    def test_another_root_or_builder_cannot_reuse_component(self):
        raw = component.pack_component(self.files, self.provenance)
        for name, value in (
            ("base_rootfs_sha256", "3" * 64),
            ("builder_image_id", "sha256:" + "4" * 64),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "provenance"):
                self.validate(raw, inputs={**self.inputs, name: value})

    def test_stale_source_tree_and_overriding_reserved_fields_are_rejected(self):
        stale = {**self.provenance, "source_trees": {}}
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate(component.pack_component(self.files, stale))
        with self.assertRaisesRegex(ValueError, "provenance"):
            component.pack_component(self.files, {**self.provenance, "kind": "other"})


if __name__ == "__main__":
    unittest.main()
