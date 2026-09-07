from __future__ import annotations

import tempfile
import unittest
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from installer import final_root
from installer.media_closure import MediaClosure, MediaClosureFile
from installer.vendor_bundle import ElfMetadata, VendorArtifact, VendorBundle
from installer.runtime_policy import (
    DROPBEAR_KEY_ONLY_ARGUMENTS,
    EMPTY_RESOLVER_POLICY,
    LOOPBACK_INTERFACE,
    NETWORK_DEFAULT_ROUTE_GUARD,
    NETWORK_INTERFACES,
    UDHCPC_NO_DEFAULT,
    WLAN_DHCP_NO_DEFAULT,
)


def ed25519_public_key() -> bytes:
    algorithm = b"ssh-ed25519"
    key = b"q" * 32
    blob = len(algorithm).to_bytes(4, "big") + algorithm + len(key).to_bytes(4, "big") + key
    return b"ssh-ed25519 " + base64.b64encode(blob) + b" test-comment\n"


def dropbear_host_key() -> bytes:
    return b"\0\0\0\x0bssh-ed25519\0" + b"q" * 80


class FinalRootTests(unittest.TestCase):
    @staticmethod
    def vendor_bundle() -> VendorBundle:
        artifacts = tuple(
            VendorArtifact(
                name=name,
                destination=destination,
                source_path=source_path,
                raw=raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                elf=(
                    None
                    if name == "os02g10-t31.bin"
                    else ElfMetadata(flags=0x70001007, needed=(), soname=name)
                ),
                rootfs=True,
            )
            for name, destination, source_path, raw in (
                ("libimp.so", "usr/lib/libimp.so", "lib/libimp.so", b"\x7fELFcamera-1.1.4\0"),
                ("libalog.so", "usr/lib/libalog.so", "lib/libalog.so", b"\x7fELFcamera-alog"),
                ("libsysutils.so", "usr/lib/libsysutils.so", "lib/libsysutils.so", b"\x7fELFcamera-sysutils"),
                ("libaudioProcess.so", "usr/lib/libaudioProcess.so", "lib/libaudioProcess.so", b"\x7fELFcamera-audio-process"),
                ("tx-isp-t31.ko", final_root.NATIVE_MEDIA_PATHS[0], "lib/modules/tx-isp-t31.ko", b"stock-tx-isp"),
                ("sensor_os02g10_t31.ko", final_root.NATIVE_MEDIA_PATHS[1], "lib/modules/sensor_os02g10_t31.ko", b"stock-os02g10-module"),
                ("os02g10-t31.bin", final_root.NATIVE_MEDIA_PATHS[2], "etc/sensor/os02g10-t31.bin", b"stock-os02g10-iq"),
            )
        )
        return VendorBundle(
            artifacts=artifacts,
            bundle_sha256="b" * 64,
            firmware_version="1.02.02",
            manifest_sha256="c" * 64,
        )

    def test_private_writer_replaces_an_existing_read_only_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            target = Path(directory_name) / "private-input"
            target.write_bytes(b"old")
            target.chmod(0o400)

            final_root._write_private(target, b"new")

            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(target.stat().st_mode & 0o077, 0)

    def test_private_writer_rejects_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            target = root / "target"
            target.write_bytes(b"old")
            link = root / "link"
            link.symlink_to(target)

            with self.assertRaisesRegex(final_root.FinalRootError, "changed type"):
                final_root._write_private(link, b"new")

    def test_legacy_wpa_config_gets_fixed_runtime_policy_without_secret_changes(self) -> None:
        legacy = (
            b'network={\n    ssid="test"\n    psk='
            + b"a" * 64
            + b"\n}\n"
        )

        materialized = final_root._materialize_wpa_runtime_policy(legacy)

        self.assertTrue(
            materialized.startswith(
                b"ctrl_interface=/run/wpa_supplicant\n"
                b"update_config=0\n"
                b"ap_scan=1\n"
            )
        )
        self.assertEqual(materialized.split(b"network=", 1)[1], legacy.split(b"network=", 1)[1])

    def test_canonical_wpa_config_stays_byte_exact(self) -> None:
        canonical = (
            b"ctrl_interface=/run/wpa_supplicant\n"
            b"update_config=0\n"
            b"ap_scan=1\n"
            b'network={\n    ssid="test"\n    psk='
            + b"a" * 64
            + b"\n}\n"
        )

        self.assertEqual(
            final_root._materialize_wpa_runtime_policy(canonical), canonical
        )

    def test_built_station_wifi_must_match_materialized_input(self) -> None:
        expected = b"canonical-private-wpa\n"
        with patch.object(final_root, "_run", return_value=expected) as run:
            final_root._validate_built_station_wifi(
                image=Path("system.private.squashfs"),
                expected=expected,
                unsquashfs=Path("unsquashfs"),
            )
        run.assert_called_once_with(
            [
                "unsquashfs",
                "-cat",
                "system.private.squashfs",
                "etc/wpa_supplicant.conf",
            ],
            "built station Wi-Fi audit",
        )

        with patch.object(final_root, "_run", return_value=b"stale\n"):
            with self.assertRaisesRegex(final_root.FinalRootError, "does not match"):
                final_root._validate_built_station_wifi(
                    image=Path("system.private.squashfs"),
                    expected=expected,
                    unsquashfs=Path("unsquashfs"),
                )

    def test_private_directory_build_rejects_stale_station_wifi_before_build(self) -> None:
        sealed = b'network={\nssid="current"\npsk=' + b"a" * 64 + b"\n}\n"
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            base = root / "base.squashfs"
            base.write_bytes(b"base")
            with (
                patch(
                    "installer.recovery_ap.host.load_host_session",
                    return_value=SimpleNamespace(
                        station_mdns_name="dcs6100-1234abcd.local"
                    ),
                ),
                patch.object(
                    final_root,
                    "load_private_config_for_session",
                    return_value=SimpleNamespace(wpa_config=sealed),
                ),
                patch.object(
                    final_root,
                    "load_private_wpa_config",
                    return_value=sealed.replace(b"current", b"stale__"),
                ),
                patch.object(final_root, "prepare_final_root") as prepare,
            ):
                with self.assertRaisesRegex(
                    final_root.FinalRootError, "expected station Wi-Fi"
                ):
                    final_root.prepare_from_private_directory(
                        base_rootfs_path=base,
                        private_config_dir=root / "private",
                        expected_wpa_config_path=root / "expected-wpa.conf",
                        vendor_bundle_dir=root / "vendor",
                        media_closure_dir=root / "media",
                        session_dir=root / "session",
                        output_dir=root / "output",
                        mksquashfs=Path("mksquashfs"),
                        unsquashfs=Path("unsquashfs"),
                    )
            prepare.assert_not_called()

    def test_wpa_runtime_policy_rejects_conflicts_and_duplicates(self) -> None:
        for payload, expected in (
            (b"ctrl_interface=/var/run/wpa_supplicant\n", "conflicting ctrl_interface"),
            (b"update_config=1\n", "conflicting update_config"),
            (b"ap_scan=1\nap_scan=1\n", "repeats ap_scan"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(final_root.FinalRootError, expected):
                    final_root._materialize_wpa_runtime_policy(payload)

    def test_base_extraction_restores_umask_after_preserving_source_modes(self) -> None:
        with patch.object(final_root.os, "umask", side_effect=(0o077, 0)) as umask:
            with patch.object(final_root, "_run", return_value=b"") as run:
                final_root._extract_base_root(
                    unsquashfs=Path("unsquashfs"),
                    source=Path("base.squashfs"),
                    destination=Path("root"),
                )

        self.assertEqual(umask.call_args_list, [call(0), call(0o077)])
        run.assert_called_once_with(
            ["unsquashfs", "-d", "root", "base.squashfs"],
            "base final-root extraction",
        )

    def test_control_configuration_is_loopback_only_and_credential_bound(self) -> None:
        document = {
            "control": {
                "enabled": True,
                "listen": "localhost",
                "port": 1998,
            }
        }
        final_root._configure_control(document, b"a" * 64 + b"\n")
        control = document["control"]
        self.assertEqual(control["listen"], "127.0.0.1")
        self.assertEqual(control["port"], 1998)
        self.assertEqual(control["backend"], "prudynt")
        self.assertEqual(
            control["token"],
            hashlib.sha256(
                b"dcs6100-control-token\0" + b"a" * 64 + b"\n"
            ).hexdigest(),
        )

    def test_control_configuration_rejects_missing_or_external_listener(self) -> None:
        with self.assertRaisesRegex(final_root.FinalRootError, "lacks Control"):
            final_root._configure_control({}, b"a" * 64 + b"\n")
        with self.assertRaisesRegex(final_root.FinalRootError, "loopback-only"):
            final_root._configure_control(
                {
                    "control": {
                        "enabled": True,
                        "listen": "0.0.0.0",
                        "port": 1998,
                    }
                },
                b"a" * 64 + b"\n",
            )

    def test_final_profile_uses_the_minimum_ha_live_image_interval(self) -> None:
        document = {"ha": {"camera_interval": 60}}

        final_root._configure_ha_live_image(document)

        self.assertEqual(
            document["ha"]["camera_interval"],
            final_root.HA_LIVE_IMAGE_INTERVAL_SECONDS,
        )

    def test_final_profile_requires_home_assistant_configuration(self) -> None:
        with self.assertRaisesRegex(
            final_root.FinalRootError, "lacks Home Assistant configuration"
        ):
            final_root._configure_ha_live_image({})

    def test_requested_output_size_cannot_exceed_fixed_system_region(self) -> None:
        with self.assertRaisesRegex(final_root.FinalRootError, "fixed mtd3 system region"):
            final_root.prepare_final_root(
                base_rootfs=b"hsqs" + b"\0" * 44,
                wpa_config=b'ssid="test"\npsk=' + b"a" * 64 + b"\n",
                credential=b"a" * 64 + b"\n",
                api_key=b"b" * 64 + b"\n",
                authorized_key=ed25519_public_key(),
                dropbear_host_key=dropbear_host_key(),
                station_hostname="dcs6100-1234abcd",
                vendor_bundle=self.vendor_bundle(),
                media_closure=object(),
                output_dir=Path("unused"),
                mksquashfs=Path("mksquashfs"),
                unsquashfs=Path("unsquashfs"),
                output_size=final_root.SYSTEM_FLASH_SPAN + 1,
            )

    def test_final_root_uses_space_efficient_supported_squashfs_blocks(self) -> None:
        with patch.object(final_root, "_run") as run:
            final_root._build_final_root_squashfs(
                mksquashfs=Path("mksquashfs"),
                root=Path("root"),
                output=Path("system.squashfs"),
                label="test final-root build",
            )

        run.assert_called_once_with(
            [
                "mksquashfs",
                "root",
                "system.squashfs",
                "-comp",
                "xz",
                "-b",
                "1048576",
                "-noappend",
                "-no-tailends",
                "-all-root",
                "-no-xattrs",
                "-no-progress",
                "-repro-time",
                str(final_root.SOURCE_DATE_EPOCH),
            ],
            "test final-root build",
        )

    def test_universal_output_size_cannot_exceed_fixed_system_region(self) -> None:
        with self.assertRaisesRegex(final_root.FinalRootError, "output size is invalid"):
            final_root.prepare_universal_final_root(
                base_rootfs=b"",
                vendor_bundle=SimpleNamespace(),
                media_closure=None,
                output_dir=Path("unused"),
                mksquashfs=Path("mksquashfs"),
                unsquashfs=Path("unsquashfs"),
                output_size=final_root.SYSTEM_FLASH_SPAN + 1,
            )

    def test_private_input_must_be_a_bounded_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            bounded = root / "bounded"
            bounded.write_bytes(b"123456789")
            with self.assertRaisesRegex(final_root.FinalRootError, "bounded regular"):
                final_root._read_private(bounded, "test input", limit=8)

            link = root / "link"
            link.symlink_to(bounded)
            with self.assertRaisesRegex(final_root.FinalRootError, "cannot read"):
                final_root._read_private(link, "test input", limit=16)

    def test_cleanup_removes_generic_flash_tools_and_polluted_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            for relative in final_root.FINAL_FORBIDDEN_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"unsafe")
            modules = root / "usr/lib/modules"
            expected = modules / final_root.KERNEL_RELEASE
            (expected / "kernel").mkdir(parents=True)
            polluted = modules / "make[2]: Entering directory"
            polluted.mkdir()
            (polluted / "artifact").write_bytes(b"polluted")

            final_root._remove_unsafe_paths(root)

            self.assertTrue(expected.is_dir())
            self.assertFalse(polluted.exists())
            for relative in final_root.FINAL_FORBIDDEN_PATHS:
                self.assertFalse((root / relative).exists())

    def test_shadow_uses_the_stable_installer_credential_sha512_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            shadow = Path(directory_name) / "shadow"
            shadow.write_text(
                "root:old:1:2:3:4:5:6:7\nnobody:x:1:2:3:4:5:6:7\n",
                encoding="utf-8",
            )
            password_hash = final_root._set_root_password(shadow, b"a" * 64 + b"\n")
            self.assertEqual(
                shadow.read_text(encoding="utf-8"),
                f"root:{password_hash}:1:2:3:4:5:6:7\n"
                "nobody:x:1:2:3:4:5:6:7\n",
            )
            self.assertEqual(
                password_hash,
                "$6$82516685c975117a$ZxCOT6iNeZY5tz.HA4bSpdKgKjIjWP6OYUTYrocm2buFV1MP7WAW9xVlluakMtC3or43jjEijSMhj6DA3c4zX1",
            )
            self.assertEqual(
                final_root._set_root_password(shadow, b"a" * 64 + b"\n"),
                password_hash,
            )
            self.assertEqual(shadow.stat().st_mode & 0o077, 0)

    def test_key_only_ssh_locks_password_and_installs_one_ed25519_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "etc/init.d").mkdir(parents=True)
            (root / "etc/dropbear").mkdir(parents=True)
            (root / "etc/profile.d").mkdir(parents=True)
            (root / "etc/shadow").write_text(
                "root:development:1:2:3:4:5:6:7\nnobody:x:1:2:3:4:5:6:7\n",
                encoding="ascii",
            )
            (root / "etc/profile.d/chpasswd").write_text("unsafe\n", encoding="ascii")
            (root / "etc/init.d/S30dropbear").write_text(
                '#!/bin/sh\nDAEMON_ARGS="-k -K 300 -R"\nstart() {\n\t:\n}\n',
                encoding="ascii",
            )

            final_root._configure_key_only_ssh(
                root,
                ed25519_public_key(),
                dropbear_host_key(),
                b"a" * 64 + b"\n",
            )

            self.assertTrue((root / "etc/shadow").read_text().startswith("root:$6$"))
            self.assertFalse((root / "etc/profile.d/chpasswd").exists())
            authorized = (root / "root/.ssh/authorized_keys").read_bytes()
            self.assertNotIn(b"test-comment", authorized)
            self.assertEqual((root / "root/.ssh").stat().st_mode & 0o077, 0)
            self.assertEqual((root / "root/.ssh/authorized_keys").stat().st_mode & 0o077, 0)
            self.assertEqual(
                (root / "etc/dropbear/dropbear_ed25519_host_key").read_bytes(),
                dropbear_host_key(),
            )
            dropbear = (root / "etc/init.d/S30dropbear").read_text()
            self.assertIn(f'DAEMON_ARGS="{DROPBEAR_KEY_ONLY_ARGUMENTS}"', dropbear)
            self.assertIn("killall dropbear 2>/dev/null || true", dropbear)
            self.assertIn('rm -f "$PIDFILE"', dropbear)

    def test_key_only_ssh_normalizes_the_live_proven_legacy_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "etc/init.d").mkdir(parents=True)
            (root / "etc/dropbear").mkdir(parents=True)
            (root / "etc/profile.d").mkdir(parents=True)
            (root / "etc/shadow").write_text(
                "root:!:1:2:3:4:5:6:7\nnobody:x:1:2:3:4:5:6:7\n",
                encoding="ascii",
            )
            (root / "etc/init.d/S30dropbear").write_text(
                '#!/bin/sh\nDAEMON_ARGS="-s -g -k -K 300"\nstart() {\n\t:\n}\n',
                encoding="ascii",
            )

            final_root._configure_key_only_ssh(
                root,
                ed25519_public_key(),
                dropbear_host_key(),
                b"a" * 64 + b"\n",
            )

            dropbear = (root / "etc/init.d/S30dropbear").read_text()
            self.assertIn(f'DAEMON_ARGS="{DROPBEAR_KEY_ONLY_ARGUMENTS}"', dropbear)
            self.assertNotIn('DAEMON_ARGS="-s -g -k -K 300"', dropbear)

    def test_network_policy_accepts_dhcp_address_without_a_default_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            interfaces = root / "etc/network/interfaces.d"
            interfaces.mkdir(parents=True)
            (interfaces / "lo").write_text("auto lo\niface lo inet loopback\n")
            (interfaces / "eth0").write_text("auto eth0\niface eth0 inet dhcp\n")
            (interfaces / "wlan0").write_text(
                "auto wlan0\niface wlan0 inet dhcp\n   dhcp-v6-enabled true\n"
            )
            (root / "etc/network/interfaces").write_text(
                "source-directory /unexpected\n", encoding="ascii"
            )
            (root / "usr/share/udhcpc").mkdir(parents=True)
            (root / "usr/share/udhcpc/default.script").write_bytes(
                b'#!/bin/sh\ncase "$1" in\nrenew | bound) route add default;;\nesac\n'
            )
            (root / "etc/init.d").mkdir(parents=True)
            (root / "etc/init.d/S40network").write_text(
                "#!/bin/sh\nstart() {\n\tifup -v -a\n}\n", encoding="ascii"
            )

            final_root._configure_no_default_route(root)

            self.assertEqual((interfaces / "wlan0").read_bytes(), WLAN_DHCP_NO_DEFAULT)
            self.assertEqual((interfaces / "lo").read_bytes(), LOOPBACK_INTERFACE)
            self.assertEqual(
                (root / "etc/network/interfaces").read_bytes(), NETWORK_INTERFACES
            )
            self.assertFalse((interfaces / "eth0").exists())
            self.assertEqual(
                (root / "usr/share/udhcpc/default.script").read_bytes(),
                UDHCPC_NO_DEFAULT,
            )
            self.assertIn(
                b'ifconfig "$interface" "$ip" netmask "$subnet"',
                UDHCPC_NO_DEFAULT,
            )
            self.assertNotIn(b'$ip/$subnet', UDHCPC_NO_DEFAULT)
            self.assertEqual(
                (root / "etc/default/resolv.conf").read_bytes(),
                EMPTY_RESOLVER_POLICY,
            )
            self.assertIn(
                NETWORK_DEFAULT_ROUTE_GUARD,
                (root / "etc/init.d/S40network").read_text(),
            )
            self.assertIn("D-Link station association timed out", NETWORK_DEFAULT_ROUTE_GUARD)
            self.assertLess(
                NETWORK_DEFAULT_ROUTE_GUARD.index("/proc/net/wireless"),
                NETWORK_DEFAULT_ROUTE_GUARD.index("ifup -v -f -a"),
            )
            self.assertNotIn("ifup -v -a", NETWORK_DEFAULT_ROUTE_GUARD)

    def test_prudynt_media_profile_matches_live_color_and_hardware_osd(self) -> None:
        document: dict[str, object] = {
            "sensor": {},
            "stream0": {},
            "stream1": {},
            "stream2": {},
            "stream3": {},
            "audio": {},
            "rtsp": {},
            "motion": {},
            "osd": {"burnin": {}, "privacy": {}, "sei": {}},
        }

        final_root._configure_prudynt_media(document)

        self.assertEqual(
            document["sensor"],
            {
                "model": "os02g10", "fps": 25, "gpio_reset": 18,
                "height": 1080, "i2c_address": 0x3C, "i2c_bus": 0,
                "width": 1920,
            },
        )
        # A reused upstream configuration must not retain wiring for another
        # board. Stock TX-ISP has no procfs defaults to correct these values.
        document["sensor"].update(  # type: ignore[union-attr]
            {"i2c_address": 0x37, "i2c_bus": 1, "gpio_reset": -1,
             "width": 0, "height": 0}
        )
        final_root._configure_prudynt_media(document)
        self.assertEqual(document["sensor"]["i2c_address"], 0x3C)  # type: ignore[index]
        self.assertEqual(document["sensor"]["i2c_bus"], 0)  # type: ignore[index]
        self.assertEqual(document["sensor"]["gpio_reset"], 18)  # type: ignore[index]
        self.assertEqual(document["sensor"]["width"], 1920)  # type: ignore[index]
        self.assertEqual(document["sensor"]["height"], 1080)  # type: ignore[index]
        self.assertEqual(
            document["stream0"],
            {
                "enabled": True,
                "width": 1920,
                "height": 1080,
                "fps": 15,
                "buffers": 1,
                "bitrate": 3000,
                "audio_enabled": True,
            },
        )
        self.assertEqual(
            document["stream1"],
            {
                "enabled": True,
                "fps": 15,
                "buffers": 1,
                "audio_enabled": True,
            },
        )
        self.assertFalse(document["stream2"]["enabled"])  # type: ignore[index]
        self.assertFalse(document["stream3"]["enabled"])  # type: ignore[index]
        self.assertEqual(
            document["audio"],
            {
                "mic_enabled": True,
                "mic_format": "AAC",
                "mic_is_digital": True,
                "spk_enabled": False,
                "tap_enabled": False,
            },
        )
        self.assertFalse(document["rtsp"]["audio_only_enabled"])  # type: ignore[index]
        self.assertFalse(document["motion"]["enabled"])  # type: ignore[index]
        self.assertTrue(document["osd"]["burnin"]["enabled"])  # type: ignore[index]
        self.assertEqual(document["osd"]["burnin"]["format"], "%F %T %Z")  # type: ignore[index]
        self.assertFalse(document["osd"]["privacy"]["enabled"])  # type: ignore[index]
        self.assertFalse(document["osd"]["sei"]["enabled"])  # type: ignore[index]

    def test_prudynt_rtsp_uses_the_per_install_management_credential(self) -> None:
        document: dict[str, object] = {
            "rtsp": {"username": "old", "password": "__SET_LOCALLY__"}
        }

        final_root._configure_prudynt_management_credential(
            document, "__SET_LOCALLY__"
        )

        self.assertEqual(
            document["rtsp"],
            {"username": "root", "password": "__SET_LOCALLY__"},
        )

    def test_prudynt_jpeg_is_idle_until_a_snapshot_or_mjpeg_client(self) -> None:
        document: dict[str, object] = {
            "stream2": {
                "enabled": True,
                "fps": 15,
                "jpeg_idle_fps": 1,
                "jpeg_quality": 75,
            },
            "stream3": {
                "enabled": False,
                "fps": 15,
                "jpeg_idle_fps": 1,
                "jpeg_quality": 75,
            },
        }

        final_root._configure_prudynt_jpeg_idle(document)

        self.assertEqual(document["stream2"]["fps"], 5)  # type: ignore[index]
        self.assertEqual(document["stream2"]["jpeg_idle_fps"], 0)  # type: ignore[index]
        self.assertEqual(document["stream2"]["jpeg_quality"], 70)  # type: ignore[index]
        self.assertEqual(document["stream3"]["fps"], 5)  # type: ignore[index]
        self.assertEqual(document["stream3"]["jpeg_idle_fps"], 0)  # type: ignore[index]
        self.assertEqual(document["stream3"]["jpeg_quality"], 70)  # type: ignore[index]
        self.assertTrue(document["stream2"]["enabled"])  # type: ignore[index]

    def test_prudynt_jpeg_idle_policy_fails_closed_without_pinned_fields(self) -> None:
        with self.assertRaisesRegex(final_root.FinalRootError, "stream2.fps"):
            final_root._configure_prudynt_jpeg_idle(
                {
                    "stream2": {},
                    "stream3": {
                        "fps": 15,
                        "jpeg_idle_fps": 1,
                        "jpeg_quality": 75,
                    },
                }
            )

    def test_prudynt_http_media_is_loopback_only(self) -> None:
        document: dict[str, object] = {"http": {"loopback_only": False}}

        final_root._configure_prudynt_http_ingress(document)

        self.assertTrue(document["http"]["loopback_only"])  # type: ignore[index]

    def test_prudynt_http_ingress_materializes_pinned_field(self) -> None:
        document: dict[str, object] = {"http": {}}

        final_root._configure_prudynt_http_ingress(document)

        self.assertTrue(document["http"]["loopback_only"])  # type: ignore[index]

    def test_prudynt_http_ingress_fails_closed_without_http_section(self) -> None:
        with self.assertRaisesRegex(final_root.FinalRootError, "lacks http"):
            final_root._configure_prudynt_http_ingress({})

    def test_media_init_preserves_working_order_and_removes_timing_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            init = root / "etc/init.d"
            init.mkdir(parents=True)
            for name in (
                "S09mmc",
                "S02ssl",
                "F01datetime",
                "S06ircut",
                "S07dusk2dawn",
                "S10daynightd",
                "S11modules",
                "S30mdev",
                "S15thingino-button",
                "S31prudynt",
                "S14volatile-config",
                "S10mdev",
                "S12prudynt",
                "S13dlink-media-ready",
                "S56prudynt",
                "dlink-media-start",
            ):
                (init / name).write_text("#!/bin/sh\n", encoding="ascii")
            modules = root / "etc/modules.d"
            modules.mkdir()
            (modules / "gpio-userkeys").write_text(
                'gpio-userkeys gpio_config="28,60,1"\n', encoding="ascii"
            )
            (root / "etc/thingino-button.conf").write_text(
                "KEY_ENTER TIMED 5 /sbin/reboot\n", encoding="ascii"
            )

            final_root._configure_media_first_init(root)

            for name in (
                "S04loopback",
                "S05dlink-media-preconditions",
                "S14mmc",
                "S55installer-health",
                "S59ssl",
            ):
                self.assertTrue((init / name).is_file(), name)
                self.assertTrue((init / name).stat().st_mode & 0o100, name)
            loopback = (init / "S04loopback").read_bytes()
            self.assertIn(
                b"/sbin/ifconfig lo 127.0.0.1 netmask 255.0.0.0 up",
                loopback,
            )
            self.assertNotIn(b"&", loopback)
            self.assertTrue((init / "S31prudynt").exists())
            self.assertFalse((init / "S12prudynt").exists())
            self.assertFalse((init / "S13dlink-media-ready").exists())
            self.assertFalse((init / "S56prudynt").exists())
            self.assertTrue((init / "S30mdev").exists())
            self.assertFalse((init / "S10mdev").exists())
            self.assertFalse((init / "dlink-media-start").exists())
            self.assertFalse((init / "S14volatile-config").exists())
            self.assertFalse((init / "S15thingino-button").exists())
            self.assertFalse((modules / "gpio-userkeys").exists())
            self.assertFalse((root / "etc/thingino-button.conf").exists())
            preconditions = (init / "S05dlink-media-preconditions").read_bytes()
            self.assertIn(b"/etc/init.d/F01datetime start", preconditions)
            self.assertIn(b"mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /dev/shm", preconditions)
            self.assertIn(
                b"rm -f /run/prudynt-dlink-media.ready /run/prudynt.pid",
                preconditions,
            )
            self.assertNotIn(b"/usr/bin/prudynt", preconditions)
            self.assertNotIn(b"S31prudynt start", preconditions)
            self.assertIn(b"sync", preconditions)
            self.assertIn(b"echo 3 >/proc/sys/vm/drop_caches", preconditions)
            for name in (
                "S06ircut",
                "F01datetime",
                "S07dusk2dawn",
                "S10daynightd",
                "S11modules",
            ):
                self.assertTrue((init / name).is_file(), name)
            acceptance = init / "dlink-media-acceptance"
            self.assertTrue(acceptance.is_file())
            self.assertTrue(acceptance.stat().st_mode & 0o100)
            self.assertIn(b"1080p-started", acceptance.read_bytes())
            self.assertIn(b"prudynt-dlink-media.ready", acceptance.read_bytes())
            verifier = (final_root.TEMPLATE_ROOT / "dlink-media-verify").read_bytes()
            self.assertNotIn(b"dlink-media-start", verifier)
            self.assertIn(b"dlink-media-acceptance", verifier)
            health = (final_root.TEMPLATE_ROOT / "thingino-health").read_bytes()
            self.assertIn(b"/run/mdnsd.pid", health)
            self.assertIn(b"ip -4 addr show dev wlan0", health)
            self.assertIn(b"bs=4097 count=1", health)
            self.assertNotIn(b"/run/prudynt.pid", health)
            self.assertNotIn(b"prudynt-dlink-media.ready", health)
            self.assertNotIn(b"/proc/net/ipv6_route", health)
            local_health = (
                final_root.TEMPLATE_ROOT / "S55installer-health"
            ).read_bytes()
            self.assertIn(b"local IPv4 network ready", local_health)
            self.assertNotIn(b"/proc/net/ipv6_route", local_health)
            self.assertIn(b'"$0" wait &', local_health)
            self.assertIn(b"/proc/$pid/cmdline", local_health)
            self.assertIn(b"WAIT_SECONDS=300", local_health)
            self.assertNotIn(b"dropbear.pid", local_health)
            self.assertNotIn(b"mdnsd.pid", local_health)

    def test_c1_media_config_stays_byte_exact_and_is_not_credential_patched(self) -> None:
        config = b'{"http":{},"rtsp":{},"c1":"byte-exact"}\n'
        iq = b"c1-iq"
        closure = MediaClosure(
            files=(
                MediaClosureFile(
                    "etc/prudynt.json", config, hashlib.sha256(config).hexdigest()
                ),
                MediaClosureFile("sensor/os02g10-t31.bin", iq, hashlib.sha256(iq).hexdigest()),
            ),
            closure_sha256="d" * 64,
            manifest_sha256="e" * 64,
            startup_order=("S06ircut", "S10daynightd", "S11modules", "S31prudynt"),
            runtime_dlopen=("libaudioProcess.so",),
        )
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "etc").mkdir()
            (root / "etc/prudynt.json").write_bytes(b"old")
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/prudynt").write_bytes(b"source-built-prudynt")
            (root / "usr/share/sensor").mkdir(parents=True)
            (root / "usr/share/sensor/os02g10-t31.bin").write_bytes(b"old-iq")

            provenance = final_root._install_media_closure(root, closure)

            self.assertEqual((root / "etc/prudynt.json").read_bytes(), config)
            self.assertEqual((root / "usr/share/sensor/os02g10-t31.bin").read_bytes(), iq)
            self.assertFalse((root / "opt/dlink-media").exists())
            self.assertEqual(
                provenance["runtime_config_sha256"], hashlib.sha256(config).hexdigest()
            )

    def test_glibc_closure_installs_once_in_global_runtime(self) -> None:
        reference_prudynt = b"proven-glibc-prudynt"
        source_built_prudynt = b"source-built-prudynt"
        proven_alog = b"proven-glibc-alog"
        proven_curl = b"proven-glibc-curl"
        proven_audio = b"proven-glibc-audio-process"
        proven_config = b'{"c1":true}\n'

        def media_file(path: str, raw: bytes) -> MediaClosureFile:
            return MediaClosureFile(path, raw, hashlib.sha256(raw).hexdigest())

        closure = MediaClosure(
            files=(
                media_file("bin/prudynt", reference_prudynt),
                media_file("etc/prudynt.json", proven_config),
                media_file("lib/libalog.so", proven_alog),
                media_file("lib/libaudioProcess.so", proven_audio),
                media_file("lib/libcurl.so.4", proven_curl),
            ),
            closure_sha256="a" * 64,
            manifest_sha256="b" * 64,
            startup_order=("S06ircut", "S10daynightd", "S11modules", "S31prudynt"),
            runtime_dlopen=("libaudioProcess.so",),
        )
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/lib").mkdir(parents=True)
            (root / "etc").mkdir()
            (root / "etc/prudynt.json").write_bytes(b"old-config")
            (root / "lib").symlink_to("usr/lib")
            (root / "usr/bin/prudynt").write_bytes(source_built_prudynt)
            (root / "usr/lib/libalog.so").write_bytes(b"old-alog")
            (root / "usr/lib/libcurl.so.4.8.0").write_bytes(b"old-curl")
            (root / "usr/lib/libcurl.so.4").symlink_to("libcurl.so.4.8.0")
            unrelated = root / "usr/bin/unrelated-feature"
            unrelated.write_bytes(b"unchanged")

            provenance = final_root._install_media_closure(root, closure)

            global_prudynt = root / "usr/bin/prudynt"
            self.assertEqual(global_prudynt.read_bytes(), source_built_prudynt)
            self.assertEqual((root / "lib/libalog.so").read_bytes(), proven_alog)
            self.assertEqual((root / "lib/libaudioProcess.so").read_bytes(), proven_audio)
            self.assertTrue((root / "usr/lib/libcurl.so.4").is_symlink())
            self.assertEqual(
                (root / "usr/lib/libcurl.so.4").readlink(),
                Path("libcurl.so.4.8.0"),
            )
            self.assertEqual((root / "usr/lib/libcurl.so.4.8.0").read_bytes(), proven_curl)
            self.assertEqual(unrelated.read_bytes(), b"unchanged")
            self.assertEqual(
                provenance["runtime"],
                "source-built-prudynt-global-glibc-c1-closure",
            )
            self.assertEqual(
                provenance["prudynt"]["sha256"],
                hashlib.sha256(source_built_prudynt).hexdigest(),
            )
            self.assertFalse(
                any(entry["path"] == "bin/prudynt" for entry in provenance["files"])
            )
            self.assertFalse((root / "opt/dlink-media").exists())

    def test_media_closure_keeps_source_built_init_scripts(self) -> None:
        reference_ircut = b"#!/bin/sh\nircut off\n"
        reference_prudynt = b"#!/bin/sh\nstart-stop-daemon\n"
        source_ircut = b"#!/bin/sh\nprintf 1 > /sys/class/gpio/gpio50/value\n"
        source_prudynt = b"#!/bin/sh\nulimit -s 256\nstart-stop-daemon\n"

        def media_file(path: str, raw: bytes) -> MediaClosureFile:
            return MediaClosureFile(path, raw, hashlib.sha256(raw).hexdigest())

        closure = MediaClosure(
            files=(
                media_file("etc/prudynt.json", b'{}\n'),
                media_file("init/S06ircut", reference_ircut),
                media_file("init/S31prudynt", reference_prudynt),
            ),
            closure_sha256="a" * 64,
            manifest_sha256="b" * 64,
            startup_order=("S06ircut", "S10daynightd", "S11modules", "S31prudynt"),
            runtime_dlopen=("libaudioProcess.so",),
        )
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/prudynt").write_bytes(b"source-built-prudynt")
            init = root / "etc/init.d"
            init.mkdir(parents=True)
            (root / "etc/prudynt.json").write_bytes(b"old-config")
            (init / "S06ircut").write_bytes(source_ircut)
            (init / "S31prudynt").write_bytes(source_prudynt)

            provenance = final_root._install_media_closure(root, closure)

            self.assertEqual((init / "S06ircut").read_bytes(), source_ircut)
            self.assertEqual((init / "S31prudynt").read_bytes(), source_prudynt)
            self.assertFalse(
                any(entry["path"].startswith("init/") for entry in provenance["files"])
            )
            self.assertEqual(
                {entry["path"] for entry in provenance["source_built_init"]},
                {"init/S06ircut", "init/S31prudynt"},
            )
            for entry in provenance["source_built_init"]:
                self.assertEqual(entry["origin"], "pinned-source-build")
                self.assertNotEqual(entry["sha256"], entry["reference_sha256"])

    def test_glibc_closure_rejects_dangling_audio_alias(self) -> None:
        raw = b"proven-glibc-audio-process"
        closure = MediaClosure(
            files=(
                MediaClosureFile(
                    "lib/libaudioProcess.so",
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                ),
            ),
            closure_sha256="a" * 64,
            manifest_sha256="b" * 64,
            startup_order=("S06ircut", "S10daynightd", "S11modules", "S31prudynt"),
            runtime_dlopen=("libaudioProcess.so",),
        )
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "lib").mkdir()
            (root / "lib/libaudioProcess.so").symlink_to("missing-audio-library")
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/prudynt").write_bytes(b"source-built-prudynt")

            with self.assertRaisesRegex(final_root.FinalRootError, "unsafe"):
                final_root._install_media_closure(root, closure)

    def test_universal_tree_is_closed_and_contains_no_camera_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "etc/init.d").mkdir(parents=True)
            (root / "etc/dropbear").mkdir(parents=True)
            (root / "root/.ssh").mkdir(parents=True)
            (root / "etc/shadow").write_text(
                "root:$6$old$hash:1:2:3:4:5:6:7\n", encoding="utf-8"
            )
            for relative in final_root.UNIVERSAL_DISABLED_INIT:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"#!/bin/sh\n")
            for relative in (
                "etc/wpa_supplicant.conf",
                "etc/thingino-api.key",
                "etc/dropbear/dropbear_ed25519_host_key",
                "etc/dcs6100-personal-image.json",
                "root/.ssh/authorized_keys",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"private-camera-value")
            final_root._sanitize_universal_secret_paths(root)
            final_root._disable_universal_network_services(root)
            marker = {
                "artifact_scope": "model-universal",
                "contains_device_secrets": False,
                "provisioning_required": True,
                "schema_version": 1,
                "target": "DCS-6100LHV2-A1",
            }
            (root / final_root.UNIVERSAL_MARKER).write_text(
                __import__("json").dumps(
                    marker, sort_keys=True, separators=(",", ":")
                )
                + "\n",
                encoding="ascii",
            )
            final_root._validate_universal_tree(root)
            self.assertIn("root:!:", (root / "etc/shadow").read_text())
            self.assertEqual(
                (root / "etc/hostname").read_text(),
                final_root.UNIVERSAL_HOSTNAME + "\n",
            )
            self.assertFalse((root / "etc/wpa_supplicant.conf").exists())
            for relative in final_root.UNIVERSAL_DISABLED_INIT:
                self.assertTrue((root / relative).is_file())
                self.assertEqual((root / relative).stat().st_mode & 0o111, 0)
            self.assertEqual(
                {
                    entry.name
                    for entry in (
                        root / "usr/share/thingino-provisioning/init"
                    ).iterdir()
                },
                {Path(path).name for path in final_root.UNIVERSAL_DISABLED_INIT},
            )

    def test_matched_media_provenance_binds_stock_media_and_source_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            files = {
                "usr/bin/prudynt": b"source-prudynt",
                "etc/prudynt.json": b'{"rtsp":{}}\n',
                "etc/init.d/F01datetime": b"datetime",
                "etc/init.d/S06ircut": b"ircut",
                "etc/init.d/S10daynightd": b"daynight",
                "etc/init.d/S11modules": b"modules",
                "etc/init.d/S31prudynt": b"prudynt-init",
            }
            vendor_raw = {
                "libimp.so": b"stock-imp",
                "libalog.so": b"stock-alog",
                "libsysutils.so": b"stock-sysutils",
                "libaudioProcess.so": b"stock-audio",
                "tx-isp-t31.ko": b"stock-tx-isp",
                "sensor_os02g10_t31.ko": b"stock-os02g10-module",
                "os02g10-t31.bin": b"stock-os02g10-iq",
            }
            vendor_destinations = {
                "libimp.so": "usr/lib/libimp.so",
                "libalog.so": "usr/lib/libalog.so",
                "libsysutils.so": "usr/lib/libsysutils.so",
                "libaudioProcess.so": "usr/lib/libaudioProcess.so",
                "tx-isp-t31.ko": final_root.NATIVE_MEDIA_PATHS[0],
                "sensor_os02g10_t31.ko": final_root.NATIVE_MEDIA_PATHS[1],
                "os02g10-t31.bin": final_root.NATIVE_MEDIA_PATHS[2],
            }
            for name, raw in vendor_raw.items():
                destination = vendor_destinations[name]
                if name in {"libalog.so", "libsysutils.so", "libaudioProcess.so"}:
                    files[destination] = b"source-" + name.encode("ascii")
                else:
                    files[destination] = raw
            for relative, raw in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            bundle = VendorBundle(
                artifacts=tuple(
                    VendorArtifact(
                        name=name,
                        destination=vendor_destinations[name],
                        source_path=f"lib/{name}",
                        raw=raw,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        elf=(
                            None
                            if name == "os02g10-t31.bin"
                            else ElfMetadata(flags=0x70001007, needed=(), soname=name)
                        ),
                        rootfs=True,
                    )
                    for name, raw in vendor_raw.items()
                ),
                bundle_sha256="b" * 64,
                firmware_version="1.02.02",
                manifest_sha256="c" * 64,
            )

            provenance = final_root._record_source_media(root, bundle)

            self.assertEqual(
                provenance["runtime"],
                "source-built-prudynt-global-glibc-c1-closure",
            )
            self.assertEqual(provenance["vendor_bundle_sha256"], "b" * 64)
            origins = {
                entry["destination"]: entry["origin"]
                for entry in provenance["files"]
            }
            self.assertEqual(
                origins["/usr/lib/libaudioProcess.so"],
                "pinned-public-source-build",
            )
            self.assertEqual(
                origins["/usr/share/sensor/os02g10-t31.bin"],
                "camera-read-only-stock-mtd3",
            )
            self.assertEqual(len(provenance["acquired_stock_inputs"]), 7)
            self.assertEqual(len(provenance["source_built_support"]), 3)
            self.assertEqual(
                provenance["runtime_config_source_sha256"],
                provenance["runtime_config_sha256"],
            )

    def test_runtime_gate_requires_glibc_imp114_and_normal_hardware_osd(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name)
            (root / "etc").mkdir()
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/lib").mkdir(parents=True)
            (root / "etc/os-release").write_text(
                f'LIBC=uclibc\nTOOLCHAIN=uclibc\nIMAGE_ID={final_root.EXPECTED_IMAGE_ID}\n',
                encoding="ascii",
            )
            (root / "usr/bin/prudynt").write_bytes(
                b"/lib/ld.so.1"
                + b"".join(final_root.MEDIA_MARKERS)
                + b"libimp.so\0libalog.so\0libsysutils.so\0"
            )
            (root / "usr/lib/libimp.so").write_bytes(b"\x7fELF" + b"1.1.4\0")

            with self.assertRaisesRegex(final_root.FinalRootError, "glibc"):
                final_root._require_proven_media_runtime(root)

            (root / "etc/os-release").write_text(
                f'LIBC=glibc\nTOOLCHAIN=glibc\nIMAGE_ID={final_root.EXPECTED_IMAGE_ID}\n',
                encoding="ascii",
            )
            final_root._require_proven_media_runtime(root)

            (root / "usr/lib/libimp.so").write_bytes(b"\x7fELF" + b"1.1.6\0")
            with self.assertRaisesRegex(final_root.FinalRootError, "IMP 1.1.4"):
                final_root._require_proven_media_runtime(root)

            (root / "usr/lib/libimp.so").write_bytes(b"\x7fELF" + b"1.1.4\0")
            (root / "usr/bin/prudynt").write_bytes(
                b"/lib/ld.so.1"
                + b"".join(final_root.MEDIA_MARKERS)
                + b"libimp.so\0libalog.so\0libsysutils.so\0"
                + final_root.FORBIDDEN_MEDIA_MARKERS[0]
            )
            with self.assertRaisesRegex(final_root.FinalRootError, "non-normal"):
                final_root._require_proven_media_runtime(root)

    def test_camera_bundle_installs_stock_media_and_preserves_source_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            root = Path(directory_name) / "root"
            for relative in final_root.NATIVE_MEDIA_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            source_support = {}
            for name in ("libalog.so", "libsysutils.so", "libaudioProcess.so"):
                destination = f"usr/lib/{name}"
                path = root / destination
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"public placeholder")
                source_support[destination] = path.read_bytes()
            imp = root / "usr/lib/libimp.so"
            imp.write_bytes(b"public placeholder")
            bundle = self.vendor_bundle()

            final_root._install_vendor_bundle(root, bundle)

            for artifact in bundle.artifacts:
                assert artifact.destination is not None
                if artifact.name in {"libalog.so", "libsysutils.so", "libaudioProcess.so"}:
                    self.assertEqual(
                        (root / artifact.destination).read_bytes(),
                        source_support[artifact.destination],
                    )
                else:
                    self.assertEqual((root / artifact.destination).read_bytes(), artifact.raw)


if __name__ == "__main__":
    unittest.main()
