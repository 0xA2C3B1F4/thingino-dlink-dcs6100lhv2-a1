from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.final_bundle import derive_final_layout, final_kernel_command_line
from installer.layout import (
    ERASE_BLOCK_SIZE,
    MTD_PHYSICAL_ERASE_SIZE,
    MTD_WRITE_SIZE,
)
from installer.stage1 import build
from installer.stage1.build import (
    Stage1BuildError,
    installer_kernel_command_line,
    render_installer_kernel_fragment,
)
from installer.runtime_policy import (
    DROPBEAR_KEY_ONLY_ARGUMENTS,
    EMPTY_RESOLVER_POLICY,
    LOOPBACK_INTERFACE,
    NETWORK_DEFAULT_ROUTE_GUARD,
    NETWORK_INTERFACES,
    UDHCPC_NO_DEFAULT,
    WLAN_DHCP_NO_DEFAULT,
)
from installer.stage2 import build_stage2
from tests.test_artifacts import test_squashfs, test_uimage


class Stage1BuildTests(unittest.TestCase):
    def test_matched_public_media_identity_contract_is_complete(self) -> None:
        self.assertEqual(
            set(build.MATCHED_STOCK_MEDIA_IDENTITIES),
            {
                "usr/lib/libimp.so",
                "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/tx-isp-t31.ko",
                "usr/lib/modules/3.10.14__isvp_swan_1.0__/ingenic/sensor_os02g10_t31.ko",
                "usr/share/sensor/os02g10-t31.bin",
            },
        )
        self.assertEqual(
            set(build.SOURCE_BUILT_SUPPORT_IDENTITIES),
            {
                "usr/lib/libalog.so",
                "usr/lib/libsysutils.so",
                "usr/lib/libaudioProcess.so",
            },
        )
        self.assertTrue(
            set(build.MATCHED_STOCK_MEDIA_IDENTITIES).isdisjoint(
                build.SOURCE_BUILT_SUPPORT_IDENTITIES
            )
        )
        self.assertEqual(
            build.MATCHED_STOCK_MEDIA_IDENTITIES["usr/lib/libimp.so"],
            build.PROVEN_IDENTITIES["lib/libimp.so"],
        )
        self.assertEqual(
            build.SOURCE_BUILT_SUPPORT_IDENTITIES["usr/lib/libaudioProcess.so"],
            build.PROVEN_IDENTITIES["lib/libaudioProcess.so"],
        )

    def test_raptor_provenance_accepts_legacy_and_source_bound_shapes(self) -> None:
        legacy = dict(build.RAPTOR_RWD_PROVENANCE)
        source_bound = {
            **legacy,
            "source_provenance_sha256": "a" * 64,
        }

        self.assertTrue(build._valid_raptor_rwd_provenance(legacy))
        self.assertTrue(build._valid_raptor_rwd_provenance(source_bound))

    def test_raptor_provenance_rejects_unbound_extensions_and_bad_digests(self) -> None:
        valid = {
            **build.RAPTOR_RWD_PROVENANCE,
            "source_provenance_sha256": "a" * 64,
        }
        invalid = (
            {**valid, "unexpected": True},
            {**valid, "source_provenance_sha256": "A" * 64},
            {**valid, "source_provenance_sha256": "a" * 63},
            {**valid, "service": "S95rwd"},
        )

        for provenance in invalid:
            with self.subTest(provenance=provenance):
                self.assertFalse(build._valid_raptor_rwd_provenance(provenance))

    def test_squashfs_listing_modes_are_parsed_without_following_symlinks(self) -> None:
        listing = "\n".join(
            (
                "drwxr-xr-x 0/0 0 2026-08-06 11:56 squashfs-root/var/www",
                "-rw-r--r-- 0/0 789 2026-08-06 11:56 squashfs-root/var/www/index.html",
                "lrwxrwxrwx 0/0 3 2026-08-06 11:56 squashfs-root/lib -> usr/lib",
            )
        )

        self.assertEqual(
            build._listing_modes(listing),
            {
                "var/www": "drwxr-xr-x",
                "var/www/index.html": "-rw-r--r--",
                "lib": "lrwxrwxrwx",
            },
        )

    def test_final_root_policy_requires_private_credentials_and_clean_paths(self) -> None:
        paths = set(build.FINAL_REQUIRED_PATHS)
        algorithm = b"ssh-ed25519"
        key = b"s" * 32
        blob = len(algorithm).to_bytes(4, "big") + algorithm + len(key).to_bytes(4, "big") + key
        authorized_keys = b"ssh-ed25519 " + base64.b64encode(blob) + b"\n"
        password_key = "pass" + "word"
        prudynt = json.dumps(
            {
                "http": {"auth_required": True, "username": "probe", password_key: "h" * 24},
                "rtsp": {"auth_required": True, "username": "root", password_key: "r" * 24},
                "stream0": {"enabled": True, "fps": 15, "buffers": 1},
                "stream1": {"enabled": True, "fps": 15, "buffers": 1},
            }
        ).encode()
        onvif = json.dumps(
            {"server": {"username": "root", password_key: "r" * 24}}
        ).encode()
        thingino = json.dumps(
            {"daynight": {"controls": {"color": True}}}
        ).encode()
        valid = {
            "paths": paths,
            "shadow": (
                b"root:$6$82516685c975117a$"
                b"ZxCOT6iNeZY5tz.HA4bSpdKgKjIjWP6OYUTYrocm2buFV1MP7WAW9xVlluakMtC3or43jjEijSMhj6DA3c4zX1:::::::\n"
            ),
            "authorized_keys": authorized_keys,
            "dropbear_init": f'#!/bin/sh\nDAEMON_ARGS="{DROPBEAR_KEY_ONLY_ARGUMENTS}"\n'.encode(),
            "network_init": ("#!/bin/sh\nstart() {\n" + NETWORK_DEFAULT_ROUTE_GUARD + "}\n").encode(),
            "interfaces_config": NETWORK_INTERFACES,
            "loopback_config": LOOPBACK_INTERFACE,
            "wlan_config": WLAN_DHCP_NO_DEFAULT,
            "dhcp_script": UDHCPC_NO_DEFAULT,
            "resolver_policy": EMPTY_RESOLVER_POLICY,
            "wpa_config": b'ssid="private"\npsk=' + b"a" * 64 + b"\n",
            "prudynt_config": prudynt,
            "thingino_config": thingino,
            "onvif_config": onvif,
        }
        build.validate_final_root_contents(**valid)
        with self.assertRaisesRegex(Stage1BuildError, "Web UI password"):
            build.validate_final_root_contents(
                **{
                    **valid,
                    "shadow": b"root:!:::::::\n",
                }
            )
        with self.assertRaisesRegex(Stage1BuildError, "polluted module"):
            build.validate_final_root_contents(
                **{
                    **valid,
                    "paths": paths | {"usr/lib/modules/make-output"},
                }
            )
        with self.assertRaisesRegex(Stage1BuildError, "DHCP policy"):
            build.validate_final_root_contents(
                **{
                    **valid,
                    "dhcp_script": UDHCPC_NO_DEFAULT + b"route add default\n",
                }
            )
        with self.assertRaisesRegex(Stage1BuildError, "Day color control"):
            build.validate_final_root_contents(
                **{
                    **valid,
                    "thingino_config": json.dumps(
                        {"daynight": {"controls": {"color": False}}}
                    ).encode(),
                }
            )
        for legacy in (
            "etc/init.d/S10mdev",
            "etc/init.d/S12prudynt",
            "etc/init.d/S13dlink-media-ready",
            "etc/init.d/S56prudynt",
            "etc/init.d/dlink-media-start",
        ):
            with self.subTest(legacy=legacy), self.assertRaisesRegex(
                Stage1BuildError, "forbidden"
            ):
                build.validate_final_root_contents(
                    **{**valid, "paths": paths | {legacy}}
                )
        with self.assertRaisesRegex(Stage1BuildError, "credentials differ"):
            build.validate_final_root_contents(
                **{
                    **valid,
                    "onvif_config": json.dumps(
                        {"server": {"username": "root", password_key: "o" * 24}}
                    ).encode(),
                }
            )
        for stream1, message in (
            ({"enabled": True, "fps": 0, "buffers": 1}, "effective stream state"),
            ({"enabled": True, "fps": 15, "buffers": -1}, "effective stream state"),
            ({"enabled": False, "fps": 15, "buffers": 1}, "not enabled"),
        ):
            with self.subTest(stream1=stream1), self.assertRaisesRegex(
                Stage1BuildError, message
            ):
                invalid_prudynt = json.loads(prudynt)
                invalid_prudynt["stream1"] = stream1
                build.validate_final_root_contents(
                    **{
                        **valid,
                        "prudynt_config": json.dumps(invalid_prudynt).encode(),
                    }
                )
        for legacy, message in (
            ("var/www/x/unknown-handler", "CGI request tree"),
            ("var/www/onvif/device_service", "legacy ONVIF request tree"),
            ("var/www-portal/index.html", "CGI captive portal"),
            ("var/www/cgi-bin/control.cgi", "CGI code"),
            ("usr/share/thingino-agent-state", "retired Agent name"),
            ("usr/sbin/recordmgr", "forbidden"),
            (
                "usr/libexec/thingino-webui/heartbeat-lib.sh",
                "legacy WebUI shell helper",
            ),
            ("etc/init.d/S48webui-config", "forbidden"),
            ("usr/sbin/mqtt-sub-dispatcher", "forbidden"),
            ("var/www/assets/legacy.js", "exact static closure"),
        ):
            with self.subTest(legacy=legacy), self.assertRaisesRegex(
                Stage1BuildError, message
            ):
                build.validate_final_root_contents(
                    **{**valid, "paths": paths | {legacy}}
                )

    def test_installer_and_final_maps_differ_only_at_write_gates(self) -> None:
        size = 6_443_008
        installer = installer_kernel_command_line(size)
        final = final_kernel_command_line(derive_final_layout(size))
        self.assertIn("1792k(kernel),", installer)
        self.assertIn("6464k(system),", installer)
        self.assertIn("mem=42M@0x0", installer)
        self.assertIn("rmem=22M@0x2a00000", installer)
        self.assertIn("1792k(kernel)ro,", final)
        self.assertIn("6464k(system)ro,", final)
        self.assertIn("mem=39M@0x0", final)
        self.assertIn("rmem=25M@0x2700000", final)
        self.assertIn("ipv6.disable=1", installer)
        self.assertNotIn("ipv6.disable=1", final)
        for command_line in (installer, final):
            self.assertIn("root=/dev/mtdblock2", command_line)
            self.assertIn("1472k(data)", command_line)
            self.assertIn("1536k(vendor)ro,256k(factory)ro", command_line)

    def test_installer_kernel_fragment_binds_writable_split(self) -> None:
        fragment = render_installer_kernel_fragment(len(test_squashfs())).decode()
        self.assertIn("CONFIG_OVERLAYFS_FS=y", fragment)
        self.assertIn("1792k(kernel),4608k(bootstrap)ro", fragment)
        self.assertIn("6464k(system),1472k(data)", fragment)
        self.assertNotIn("1792k(kernel)ro", fragment)
        self.assertNotIn("6464k(system)ro", fragment)

    def test_contract_binds_complete_stage2_and_each_consumed_input(self) -> None:
        system = test_squashfs()
        final_kernel = test_uimage(
            final_kernel_command_line(derive_final_layout(len(system))).encode()
        )
        stage2 = build_stage2(final_kernel=final_kernel, system_rootfs=system)
        module = b"module"
        contract = build.render_contract(
            stage2=stage2,
            final_kernel=final_kernel,
            system=system,
            mmc_module=module,
        ).decode("ascii")
        self.assertIn('#define STAGE2_PATH "/card/THINGINO2.BIN"', contract)
        self.assertIn("#define REQUIRE_CAMERA_AUTHORIZATION 0", contract)
        self.assertIn('#define CAMERA_AUTHORIZATION_PATH "/card/INSTALL.AUTH.BIN"', contract)
        self.assertIn('#define PROVISIONING_PATH "/card/THINGINO.PROVISION"', contract)
        self.assertIn(
            '#define BOOTSTRAP_PATH "/card/DCS6100LHV2Ax_FW000B00_THINGINO_SD.bin"',
            contract,
        )
        for digest in (
            hashlib.sha256(stage2).digest(),
            hashlib.sha256(final_kernel).digest(),
            hashlib.sha256(system).digest(),
            hashlib.sha256(module).digest(),
        ):
            self.assertIn(f"0x{digest[0]:02x}", contract)

    def test_contract_binds_each_data_action(self) -> None:
        system = test_squashfs()
        final_kernel = test_uimage(
            final_kernel_command_line(derive_final_layout(len(system))).encode()
        )
        for data_mode, expected_action in (
            ("initialize", 0),
            ("preserve", 1),
            ("factory-reset", 2),
        ):
            with self.subTest(data_mode=data_mode):
                stage2 = build_stage2(
                    final_kernel=final_kernel,
                    system_rootfs=system,
                    data_mode=data_mode,
                )
                contract = build.render_contract(
                    stage2=stage2,
                    final_kernel=final_kernel,
                    system=system,
                    mmc_module=b"module",
                ).decode("ascii")
                self.assertIn(
                    f"#define DATA_ACTION {expected_action}U",
                    contract,
                )

    def test_universal_contract_requires_camera_authorization_before_writes(self) -> None:
        system = test_squashfs()
        final_kernel = test_uimage(
            final_kernel_command_line(derive_final_layout(len(system))).encode()
        )
        stage2 = build_stage2(final_kernel=final_kernel, system_rootfs=system)
        contract = build.render_contract(
            stage2=stage2,
            final_kernel=final_kernel,
            system=system,
            mmc_module=b"module",
            require_camera_authorization=True,
        ).decode("ascii")
        self.assertIn("#define REQUIRE_CAMERA_AUTHORIZATION 1", contract)
        source = build.SOURCE.read_text(encoding="utf-8")
        install = source[source.index("static void install_stage2(void)") :]
        authorization = install.index("verify_camera_authorization();")
        bootstrap_remove = install.index("remove_consumed_bootstrap();")
        first_write = install.index('SYSCALL_OPEN, (long)INSTALLER_LOGICAL_DATA_MTD, O_RDWR')
        self.assertLess(authorization, bootstrap_remove)
        self.assertLess(bootstrap_remove, first_write)
        self.assertIn("STAGE1 FAIL camera_authorization_hmac", source)
        self.assertIn("STAGE1 FAIL camera_authorization_device", source)
        self.assertIn("STAGE1 FAIL camera_authorization_stage2", source)
        self.assertIn("STAGE1 FAIL camera_authorization_provisioning", source)
        self.assertIn("stage2_buffer[STAGE2_EXPECTED_SIZE]", source)
        self.assertIn("provisioning_buffer[DATA_FLASH_SPAN]", source)
        self.assertIn("STAGE1 provisioning_data_written_and_verified", source)

    def test_universal_contract_rejects_noninitializing_data_action(self) -> None:
        system = test_squashfs()
        final_kernel = test_uimage(
            final_kernel_command_line(derive_final_layout(len(system))).encode()
        )
        for mode in ("preserve", "factory-reset"):
            stage2 = build_stage2(
                final_kernel=final_kernel,
                system_rootfs=system,
                data_mode=mode,
            )
            with self.subTest(mode=mode), self.assertRaisesRegex(
                Stage1BuildError, "requires initialize"
            ):
                build.render_contract(
                    stage2=stage2,
                    final_kernel=final_kernel,
                    system=system,
                    mmc_module=b"module",
                    require_camera_authorization=True,
                )

    def test_final_writes_use_prevalidated_ram_snapshots_not_reopened_card_paths(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        copy = source[
            source.index("static void copy_stage2_to_mtd(") :
            source.index("static void verify_stage2_matches_mtd(")
        ]
        self.assertIn("stage2_buffer + source_offset", copy)
        self.assertNotIn("STAGE2_PATH", copy)
        provisioning = source[
            source.index("static void write_provisioning_to_data(") :
            source.index("static void append_mtd_to_backup(")
        ]
        self.assertIn("provisioning_buffer", provisioning)
        self.assertNotIn("PROVISIONING_PATH", provisioning)
        install = source[source.index("static void install_stage2(void)") :]
        self.assertNotIn("STAGE2_PATH", install)
        self.assertIn("stage2_buffer + STAGE2_KERNEL_OFFSET", install)

    def test_source_hashes_preserved_data_around_system_write(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        before = source.index(
            "INSTALLER_LOGICAL_DATA_MTD, DATA_FLASH_SPAN, preserved_data_digest"
        )
        system_write = source.index(
            "STAGE2_SYSTEM_OFFSET, INSTALLER_LOGICAL_SYSTEM_MTD"
        )
        after = source.index(
            "INSTALLER_LOGICAL_DATA_MTD, DATA_FLASH_SPAN, readback_data_digest"
        )
        self.assertLess(before, system_write)
        self.assertLess(system_write, after)
        self.assertIn("STAGE1 FAIL preserved_data_changed", source)
        self.assertIn("if (DATA_ACTION != DATA_ACTION_PRESERVE)", source)

    def test_stage1_accepts_host_deactivated_bootstrap(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn("result != 0 && result != -ENOENT", source)
        self.assertIn('EMIT("STAGE1 bootstrap_absent\\n")', source)
        self.assertIn('FAIL("STAGE1 FAIL bootstrap_remove\\n")', source)

    def test_verified_stage1_handoff_enables_persistent_overlay_init(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        marker = '"THINGINO_DLINK_VERIFIED_MTD_ROOT=1"'
        self.assertIn(marker, source)
        self.assertIn("environment[3] = (char *)verified_mtd_environment;", source)
        self.assertIn("environment[4] = 0;", source)
        self.assertLess(source.index(marker), source.index("call3(SYSCALL_EXECVE"))

    def test_failed_atomic_build_leaves_no_output_or_work_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / "install-set"
            with mock.patch.object(
                build,
                "_build_install_set",
                side_effect=Stage1BuildError("injected"),
            ):
                with self.assertRaises(Stage1BuildError):
                    build.build_install_set(
                        installer_kernel=b"",
                        final_kernel=b"",
                        final_linux_config=b"",
                        system=b"",
                        mmc_module=b"",
                        output_dir=output,
                        clang=parent / "clang",
                        lld=parent / "lld",
                        mksquashfs=parent / "mksquashfs",
                        unsquashfs=parent / "unsquashfs",
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(parent.iterdir()), [])

    def test_universal_install_set_has_no_private_configuration_argument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / "universal-install-set"
            universal_manifest = {
                "artifact_scope": "model-universal",
                "contains_device_secrets": False,
                "provisioning_required": True,
                "system": {"sha256": "0" * 64, "size": 0},
            }
            with mock.patch.object(
                build,
                "_build_install_set",
                return_value={"artifact_scope": "model-universal"},
            ) as inner:
                result = build.build_universal_install_set(
                    installer_kernel=b"",
                    final_kernel=b"",
                    final_linux_config=b"",
                    system=b"",
                    universal_root_manifest=universal_manifest,
                    signing_key=parent / "signing-key.pem",
                    mmc_module=b"",
                    output_dir=output,
                    clang=parent / "clang",
                    lld=parent / "lld",
                    mksquashfs=parent / "mksquashfs",
                    unsquashfs=parent / "unsquashfs",
                )
            self.assertEqual(result["artifact_scope"], "model-universal")
            values = inner.call_args.kwargs
            self.assertEqual(values["artifact_scope"], "model-universal")
            self.assertEqual(values["data_mode"], "initialize")
            self.assertIs(values["universal_root_manifest"], universal_manifest)
            self.assertEqual(
                values["universal_signing_key"], parent / "signing-key.pem"
            )
            self.assertFalse(
                {
                    "private_config_dir",
                    "expected_wpa_config_path",
                    "session_dir",
                }
                & set(values)
            )

    def test_source_exports_backup_before_first_final_erase(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        stage2 = source.index("verify_file();")
        remove_bootstrap = source.index("remove_consumed_bootstrap();")
        backup = source.index("export_stock_userdata_backup();")
        first_erase = source.index('erase_range(descriptor, 0, DATA_FLASH_SPAN);')
        self.assertLess(stage2, remove_bootstrap)
        self.assertLess(remove_bootstrap, backup)
        self.assertLess(backup, first_erase)
        self.assertIn("SYSCALL_UNLINK = 4010", source)
        self.assertIn("STAGE1 bootstrap_removed", source)
        self.assertIn('"/card/STOCKM3.BIN"', source)
        self.assertIn('"/dev/mtd7"', source)

    def test_source_reuses_or_recovers_only_verified_backup(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn("STOCK_USERDATA_BACKUP_PART_PATH", source)
        self.assertIn("existing != -ENOENT", source)
        self.assertIn("stock_userdata_backup_matches_current_nor(\n                STOCK_USERDATA_BACKUP_PATH)", source)
        self.assertIn('#define INSTALLER_LOGICAL_SYSTEM_MTD "/dev/mtd3"', source)
        self.assertIn('#define INSTALLER_LOGICAL_DATA_MTD "/dev/mtd4"', source)
        self.assertIn("INSTALLER_LOGICAL_SYSTEM_MTD, backup", source)
        self.assertIn("INSTALLER_LOGICAL_DATA_MTD, backup", source)
        self.assertIn("digest_stock_userdata_backup(STOCK_USERDATA_BACKUP_PATH, backup_digest);", source)
        self.assertIn("STAGE1 FAIL backup_size", source)
        self.assertIn("STAGE1 stock_userdata_backup_reused", source)
        self.assertIn("STAGE1 FAIL backup_create", source)

    def test_source_binds_fixed_recovery_checkpoint_before_writes(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn('#define RECOVERY_CHECKPOINT_PATH "/card/STOCKM3.OK"', source)
        self.assertIn("#define RECOVERY_CHECKPOINT_SIZE 144", source)
        self.assertIn("#define RECOVERY_CHECKPOINT_SIZE 80", source)
        self.assertIn("'D', 'C', 'S', '6', 'R', 'C', '0', '2'", source)
        self.assertIn("'D', 'C', 'S', '6', 'R', 'C', '0', '1'", source)
        self.assertIn('#define RECOVERY_CHECKPOINT_PART_PATH "/card/STOCKM3.OK.PART"', source)
        self.assertIn("O_WRONLY | O_CREAT | O_EXCL", source)
        self.assertIn("SYSCALL_RENAME", source)
        self.assertNotIn("O_WRONLY | O_CREAT | O_TRUNC", source)
        self.assertIn("checkpoint[16 + index] = backup_digest[index]", source)
        self.assertIn("checkpoint[48 + index] = STAGE2_EXPECTED_SHA256[index]", source)
        self.assertIn("checkpoint[80 + index] = authorization_digest[index]", source)
        self.assertIn(
            "checkpoint[112 + index] = camera_authorization[120 + index]",
            source,
        )
        self.assertIn("validate_recovery_checkpoint(backup_digest);", source)
        self.assertIn("STAGE1 interrupted_install_recovery", source)
        self.assertIn("STAGE1 FAIL recovery_checkpoint_invalid", source)
        self.assertLess(
            source.index("export_stock_userdata_backup();"),
            source.index("set_status_leds_checked(1, 1);"),
        )

    def test_source_exposes_kernel_led_class_status(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        self.assertIn('"/sys/class/leds/led_g/brightness"', source)
        self.assertIn('"/sys/class/leds/led_r/brightness"', source)
        self.assertNotIn('"/sys/class/gpio/gpio52/value"', source)
        self.assertNotIn('"/sys/class/gpio/gpio54/value"', source)
        self.assertNotIn("GPIO_EXPORT_PATH", source)
        self.assertIn(
            "read_status_value(GREEN_LED_BRIGHTNESS_PATH, green_value)", source
        )
        self.assertIn(
            "read_status_value(RED_LED_BRIGHTNESS_PATH, red_value)", source
        )
        self.assertIn("set_status_leds_best_effort(0, 1);", source)
        self.assertIn("set_status_leds_checked(1, 1);", source)
        self.assertIn("STAGE1 final_write_phase", source)
        self.assertLess(
            source.index("initialize_status_leds();"),
            source.index("installer_mode = verify_layout();"),
        )
        self.assertLess(
            source.index("export_stock_userdata_backup();"),
            source.index("set_status_leds_checked(1, 1);"),
        )
        self.assertLess(
            source.index("set_status_leds_checked(1, 1);"),
            source.index('erase_range(descriptor, 0, DATA_FLASH_SPAN);'),
        )

    def test_source_distinguishes_physical_erase_and_activation_spans(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        contract = json.loads(
            build.SOURCE.parent.joinpath("stage1-contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(contract["mtd_erase_bytes"], MTD_PHYSICAL_ERASE_SIZE)
        self.assertEqual(contract["mtd_write_bytes"], MTD_WRITE_SIZE)
        self.assertEqual(contract["activation_block_bytes"], ERASE_BLOCK_SIZE)
        self.assertIn(f"MTD_ERASE_SIZE = 0x{MTD_PHYSICAL_ERASE_SIZE:x}", source)
        self.assertIn(f"MTD_WRITE_SIZE = 0x{MTD_WRITE_SIZE:x}", source)
        self.assertIn(f"ACTIVATION_SIZE = 0x{ERASE_BLOCK_SIZE:x}", source)
        self.assertIn("info.erasesize != MTD_ERASE_SIZE", source)
        self.assertIn("info.writesize != MTD_WRITE_SIZE", source)
        self.assertIn("start % MTD_ERASE_SIZE", source)
        self.assertIn("#define MEMERASE ((long)0x80084d02UL)", source)
        self.assertIn(
            "for (padding = amount; padding < write_amount; padding++)", source
        )
        self.assertIn("remaining -= amount;", source)
        self.assertNotIn("io_buffer[amount++] = 0xff", source)
        self.assertIn("write_mtd_pages(destination, io_buffer, write_amount);", source)
        self.assertIn(
            "write_mtd_pages(descriptor, activation_buffer, ACTIVATION_SIZE);", source
        )
        self.assertNotIn("info.erasesize != ERASE_SIZE", source)
        self.assertNotIn("info.writesize != 1", source)
        self.assertNotIn("MEMERASE = 0x40084d02", source)

    def test_source_binds_activation_buffer_to_flash_tail_before_erasing_mtd1_head(self) -> None:
        source = build.SOURCE.read_text(encoding="utf-8")
        install = source[source.index("static void install_stage2(void)") :]
        read_activation = install.index(
            "stage2_buffer + STAGE2_KERNEL_OFFSET"
        )
        preactivation = install.index("verify_kernel_before_activation();")
        erase_activation = install.index("erase_range(descriptor, 0, ACTIVATION_SIZE);")
        write_activation = install.index(
            "write_mtd_pages(descriptor, activation_buffer, ACTIVATION_SIZE);"
        )
        self.assertLess(read_activation, preactivation)
        self.assertLess(preactivation, erase_activation)
        self.assertLess(erase_activation, write_activation)
        self.assertIn(
            "sha256_update(&context, activation_buffer, ACTIVATION_SIZE);",
            source,
        )
        self.assertIn("FINAL_KERNEL_SHA256", source)

    def test_install_manifest_binds_every_consumed_binary_input(self) -> None:
        source = build.SOURCE.parent.joinpath("build.py").read_text(encoding="utf-8")
        self.assertIn(
            '"status": "host-built install set; generation does not authorize live use"',
            source,
        )
        self.assertIn('"terminal_failure": "red"', source)
        for name in (
            "installer_kernel",
            "final_kernel",
            "final_linux_config",
            "system_rootfs",
            "mmc_module",
        ):
            self.assertIn(f'("{name}",', source)


if __name__ == "__main__":
    unittest.main()
