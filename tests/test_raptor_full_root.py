"""Full Raptor composition must prune old owners and survive packed read-back."""

import configparser
import hashlib
import json
import re
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from installer import final_root, raptor_full_component, raptor_full_root
from installer.raptor_full_component import PAYLOAD, pack_component
from installer.raptor_provisioning import CONFIGS, SERVICE
from installer.raptor_source import recipe_identity, source_lock
import test_provisioning_data as provisioning_tests
from test_raptor_full_component import FONT_FIXTURE, MOTION_CLIP_FIXTURE, elf


ROOT = Path(__file__).resolve().parents[1]


def squashfs(label):
    raw = bytearray(128)
    raw[:4] = b"hsqs"
    struct.pack_into("<Q", raw, 40, len(raw))
    raw[64:64 + len(label)] = label
    return bytes(raw)


class FullRootTests(unittest.TestCase):
    def test_fresh_stream_encoding_defaults_have_explicit_saved_state(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "components/raptor/raptor-media.conf")
        # Match load_stream_config's existing RVD defaults without inferring
        # persisted state from an active encoder observation in Control.
        for stream, bitrate in (("stream0", 1500000), ("stream1", 400000)):
            with self.subTest(stream=stream):
                self.assertEqual(config.get(stream, "rc_mode"), "cbr")
                self.assertEqual(config.getint(stream, "profile"), 2)
                self.assertEqual(config.getint(stream, "bitrate"), bitrate)
                # CBR does not require FIXQP's explicit startup QP. Retain
                # the encoder's unset sentinel rather than forcing a QP.
                self.assertNotIn("init_qp", config[stream])

    def test_required_main_stream_has_explicit_enabled_saved_state(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "components/raptor/raptor-media.conf")
        self.assertTrue(config.getboolean("stream0", "enabled"))

    def test_fresh_antiflicker_default_is_explicit_for_saved_readback(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "components/raptor/raptor-media.conf")
        # RVD's absent-key default is 2; Control independently reads the saved
        # sensor key and must not infer successful persistence from live state.
        self.assertEqual(config.getint("sensor", "antiflicker"), 2)

    def test_default_stream_rings_have_explicit_retention_budget(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "components/raptor/raptor-media.conf")
        self.assertFalse(config.getboolean("ring", "refmode"))
        for stream, slots in (("stream0", "main_slots"), ("stream1", "sub_slots")):
            with self.subTest(stream=stream):
                self.assertEqual(config.getint("ring", slots), 16)
                self.assertEqual(config.getint(stream, "fps"), 15)
        # Do not replace bitrate-aware sizing with a fixed multi-megabyte ring.
        self.assertNotIn("main_data_mb", config["ring"])
        self.assertNotIn("sub_data_mb", config["ring"])

    def test_structured_osd_metadata_has_a_real_disabled_saved_state(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "components/raptor/raptor-media.conf")
        self.assertFalse(config.getboolean("osd_metadata", "enabled"))
        self.assertEqual(config.getint("osd_metadata", "entry_count"), 0)

    def test_all_startup_and_acceptance_probes_use_flat_raptorctl_json(self):
        for relative in (
            "components/raptor/S96raptor",
            "components/raptor/media-acceptance.sh",
            "installer/templates/dlink-application-verify",
        ):
            with self.subTest(script=relative):
                source = (ROOT / relative).read_text()
                requests = re.findall(r"'({[^'\n]*get-stream-enabled[^'\n]*})'", source)
                self.assertEqual(len(requests), 1)
                self.assertEqual(json.loads(requests[0]), {
                    "daemon": "rvd", "cmd": "get-stream-enabled", "stream_id": 1,
                })

    def test_timezone_inventory_matches_supervised_media_owners(self):
        config = configparser.ConfigParser()
        config.read(ROOT / "components/raptor/raptor-media.conf")
        expected = {"rvd", "rsd", "rhd", "rad", "ric", "rod", "rmr0", "rmr1", "rwd"}
        required = {name for name in config["timezone"]
                    if config.getboolean("timezone", name)}
        self.assertEqual(required, expected)
        self.assertEqual(set(config["timezone"]), expected | {"rod", "rmr"})
        supervisor = (ROOT / "components/raptor/S96raptor").read_text()
        for name in required:
            self.assertIn(f"start_owner {name} /usr/bin/", supervisor)
        self.assertNotIn("start_owner rmr ", supervisor)
        self.assertLess(
            supervisor.index("start_owner rvd /usr/bin/rvd"),
            supervisor.index("start_owner rod /usr/bin/rod"),
        )
        self.assertLess(
            supervisor.index("/dev/shm/rss_ring_sub"),
            supervisor.index("start_owner rod /usr/bin/rod"),
        )
        self.assertIn('"cmd":"get-stream-enabled","stream_id":1', supervisor)
        self.assertIn('if [ "$sub_enabled" = true ]; then', supervisor)
        self.assertIn("start_owner rmr1 /usr/bin/rmr --channel 1", supervisor)
        self.assertNotIn("config_bool()", supervisor)
        self.assertEqual(config["osd"]["font"], "/usr/share/fonts/default.ttf")
        self.assertEqual(config.get("osd.timestamp", "template", raw=True), "%time%")
        contract = json.loads(
            (ROOT / "contracts/thingino-control-api-v1.json").read_text()
        )
        time_contract = next(route for route in contract["routes"] if route["id"] == "config.time")
        self.assertIn(
            "full-stack profile starts and requires ROD",
            time_contract["secret_semantics"],
        )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        fixture = provisioning_tests.ProvisioningDataTests()
        fixture._universal_tree(self.source, raptor=False)
        for relative in ("lib/ld.so.1", "lib/libc.so.6", "usr/bin/prudynt", "usr/bin/daynightd"):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(elf())
        self.base = self.root / "base.squashfs"
        self.base.write_bytes(squashfs(b"base"))
        self.manifest = self.root / "base.json"
        self.manifest.write_text(json.dumps({
            "artifact_scope": "model-universal", "schema_version": 1,
            "contains_device_secrets": False, "provisioning_required": True,
            "system": {"filename": final_root.UNIVERSAL_OUTPUT_NAME,
                       "sha256": hashlib.sha256(self.base.read_bytes()).hexdigest(),
                       "size": self.base.stat().st_size},
        }))
        self.inputs = {
            "base_rootfs_sha256": hashlib.sha256(self.base.read_bytes()).hexdigest(),
            "builder_image_id": "sha256:" + "2" * 64,
            "toolchain_sha256": json.loads((ROOT / "sources.lock.json").read_bytes())[
                "sources"]["thingino_build_toolchain_aarch64"]["sha256"],
        }
        license_fixture = b"fixture font licence\n"
        self.files = {name: elf() for name in PAYLOAD - raptor_full_component.DATA_FILES}
        self.files[raptor_full_component.FONT] = FONT_FIXTURE
        self.files[raptor_full_component.FONT_LICENSE] = license_fixture
        self.files[raptor_full_component.LIBSCHRIFT_LICENSE] = b"fixture libschrift licence\n"
        self.files[raptor_full_component.CJSON_NOTICE] = (
            ROOT / "third_party/licenses/raptor-common-cJSON-header.txt"
        ).read_bytes()
        self.files[raptor_full_component.MONOCYPHER_LICENSE] = (
            ROOT / "third_party/licenses/raptor-common-Monocypher-LICENCE.txt"
        ).read_bytes()
        self.files[raptor_full_component.MOTION_CLIP] = MOTION_CLIP_FIXTURE
        for name, value in (
            ("FONT_SHA256", hashlib.sha256(FONT_FIXTURE).hexdigest()),
            ("FONT_LICENSE_SHA256", hashlib.sha256(license_fixture).hexdigest()),
            ("LIBSCHRIFT_LICENSE_SHA256", hashlib.sha256(
                self.files[raptor_full_component.LIBSCHRIFT_LICENSE]
            ).hexdigest()),
        ):
            replacement = patch.object(raptor_full_component, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.component = self.root / "component.tar.gz"
        self.component.write_bytes(pack_component(self.files, {
            "recipe": recipe_identity(ROOT, full_media=True),
            "source_trees": {
                name: item["tree"] for name, item in
                source_lock(ROOT, full_media=True)["sources"].items()
            },
            "build_inputs": self.inputs,
        }))
        self.output = self.root / "output"
        self.packed = self.root / "packed"

    def compose(self, *, corrupt_readback=None):
        def extract(*, source, destination, **kwargs):
            original = self.source if source.name == "base.squashfs" else self.packed
            shutil.copytree(original, destination, symlinks=True)
            if corrupt_readback and original == self.packed:
                (destination / corrupt_readback).write_bytes(b"changed")

        def pack(*, root, output, **kwargs):
            shutil.copytree(root, self.packed, symlinks=True)
            output.write_bytes(squashfs(b"full-raptor"))

        with (
            patch.object(final_root, "_extract_base_root", side_effect=extract),
            patch.object(final_root, "_build_final_root_squashfs", side_effect=pack),
        ):
            return raptor_full_root.compose_universal_root(
                repository=ROOT, base_rootfs=self.base, base_manifest=self.manifest,
                component_artifact=self.component,
                component_sha256=hashlib.sha256(self.component.read_bytes()).hexdigest(),
                build_inputs=self.inputs, output_dir=self.output,
                mksquashfs=Path("mksquashfs"), unsquashfs=Path("unsquashfs"),
            )

    def test_composition_removes_prudynt_and_leaves_raptor_unprovisioned(self):
        result = self.compose()
        self.assertEqual(result["media_profile"], "full-raptor-v1")
        self.assertFalse(result["contains_device_secrets"])
        self.assertTrue((self.output / "system.universal.squashfs").is_file())
        for relative in raptor_full_root.RETIRED_PATHS:
            self.assertFalse((self.packed / relative).exists())
        self.assertFalse((self.packed / SERVICE).stat().st_mode & 0o111)
        source_onvif = json.loads((self.source / "etc/onvif.json").read_bytes())
        packed_onvif = json.loads((self.packed / "etc/onvif.json").read_bytes())
        self.assertEqual(packed_onvif, {**source_onvif, "adv_enable_media2": True})
        for relative in CONFIGS:
            self.assertTrue((self.packed / relative).is_file())
        packed_media = configparser.ConfigParser()
        packed_media.read(self.packed / "etc/raptor-media.conf")
        self.assertEqual(packed_media.getint("sensor", "antiflicker"), 2)
        self.assertTrue(packed_media.getboolean("stream0", "enabled"))
        for stream in ("stream0", "stream1"):
            self.assertEqual(packed_media.get(stream, "rc_mode"), "cbr")
            self.assertEqual(packed_media.getint(stream, "profile"), 2)
        for relative in (
            "usr/sbin/dlink-media-verify",
            "usr/sbin/dlink-runtime-snapshot",
            "usr/sbin/dlink-application-verify",
        ):
            helper = self.packed / relative
            self.assertTrue(helper.is_file())
            self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.source / "usr/bin/prudynt").is_file())
        clip = self.packed / raptor_full_component.MOTION_CLIP
        self.assertEqual(clip.read_bytes(), MOTION_CLIP_FIXTURE)
        self.assertEqual(clip.stat().st_mode & 0o777, 0o644)
        for relative in (raptor_full_component.CJSON_NOTICE,
                         raptor_full_component.MONOCYPHER_LICENSE):
            notice = self.packed / relative
            self.assertEqual(notice.read_bytes(), self.files[relative])
            self.assertEqual(notice.stat().st_mode & 0o777, 0o644)
        final_root._validate_universal_tree(self.packed)

    def test_modified_packed_binary_does_not_publish_output(self):
        with self.assertRaisesRegex(ValueError, "component changed"):
            self.compose(corrupt_readback="usr/bin/rvd")
        self.assertFalse(self.output.exists())

    def test_modified_packed_notice_does_not_publish_output(self):
        with self.assertRaisesRegex(ValueError, "component changed"):
            self.compose(corrupt_readback=raptor_full_component.CJSON_NOTICE)
        self.assertFalse(self.output.exists())

    def test_support_only_intermediate_must_be_composed_before_provisioning(self):
        raptor_full_root.remove_retired_runtime(self.source)
        closure = self.source / "etc/dlink-media-closure.private.json"
        closure.write_text(json.dumps({"runtime": "source-built-camera-support-v1", "files": []}))
        with self.assertRaisesRegex(ValueError, "not an installable image"):
            final_root._validate_universal_tree(self.source)
        final_root._validate_universal_tree(self.source, allow_support_base=True)
        result = self.compose()
        self.assertFalse(result["composition_required"])
        final_root._validate_universal_tree(self.packed)

    def test_component_from_another_base_is_rejected_before_unpacking(self):
        self.inputs["base_rootfs_sha256"] = "3" * 64
        with self.assertRaisesRegex(ValueError, "base provenance"):
            self.compose()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.packed.exists())

    def test_missing_runtime_library_is_rejected(self):
        (self.source / "lib/libc.so.6").unlink()
        with self.assertRaisesRegex(ValueError, "runtime dependency missing"):
            self.compose()
        self.assertFalse(self.output.exists())

    def test_symlink_destination_cannot_write_outside_the_image(self):
        outside = self.root / "outside"
        outside.write_bytes(b"keep")
        (self.source / "usr/bin/rvd").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.compose()
        self.assertEqual(outside.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
