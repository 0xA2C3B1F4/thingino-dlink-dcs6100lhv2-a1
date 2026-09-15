from __future__ import annotations

import inspect
import subprocess
import unittest

from installer.cli import build_parser
from installer.layout import TARGET
from installer.recovery_ap.build import (
    DTRNG_SOURCE,
    ENTROPY_SOURCE,
    INIT,
    MAX_ROOT_SIZE,
    MDNS_SERVICE,
    RECOVERYCTL,
    RUNTIME_LIBRARIES,
    THINGINO_ENTER_SOURCE,
    build_recovery_ap_root,
)


class RecoveryApRootTests(unittest.TestCase):
    def test_root_contract_is_bounded_to_physical_mtd2(self) -> None:
        self.assertEqual(MAX_ROOT_SIZE, TARGET.partition(2).size)
        self.assertEqual(MAX_ROOT_SIZE, 0x480000)
        self.assertIn("libmdnsd.so.2.1.0", RUNTIME_LIBRARIES)
        self.assertIn("libnl-genl-3.so.200.26.0", RUNTIME_LIBRARIES)
        self.assertNotIn("libnl-route-3.so.200.26.0", RUNTIME_LIBRARIES)
        self.assertNotIn("libstdc++.so.6.0.35", RUNTIME_LIBRARIES)

    def test_scripts_are_posix_shell_and_write_only_physical_mtd3(self) -> None:
        for source in (INIT, RECOVERYCTL, MDNS_SERVICE):
            subprocess.run(
                ["sh", "-n", str(source)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raw = source.read_bytes()
            for forbidden in (
                b"flash_erase",
                b"MEMERASE",
                b"nandwrite",
            ):
                self.assertNotIn(forbidden, raw)
        control = RECOVERYCTL.read_text(encoding="utf-8")
        self.assertEqual(control.count("flashcp -v"), 1)
        self.assertIn("flashcp -v /run/transfer.bin /dev/mtd3", control)
        for protected in ("mtd0", "mtd1", "mtd2", "mtd4", "mtd5"):
            self.assertNotIn(f"flashcp -v /run/transfer.bin /dev/{protected}", control)
            self.assertNotIn(f"of=/dev/{protected}", control)

    def test_station_secret_is_received_on_stdin_and_kept_in_ram(self) -> None:
        control = RECOVERYCTL.read_text(encoding="utf-8")
        provision = control.split("\nprovision)\n", 1)[1].split("\n\t;;", 1)[0]
        self.assertIn("IFS= read -r ssid_hex", provision)
        self.assertIn("IFS= read -r psk_hex", provision)
        self.assertNotIn("${2:-}", provision)
        self.assertNotIn("${3:-}", provision)
        self.assertIn("/run/station.conf", provision)
        self.assertNotIn("/media/", provision)

    def test_authenticated_transfer_is_bounded_hash_checked_and_sd_install_is_scoped(self) -> None:
        control = RECOVERYCTL.read_text(encoding="utf-8")
        self.assertIn("expected_size\" -le 8126464", control)
        self.assertIn("ulimit -f", control)
        self.assertIn("sha256sum", control)
        self.assertIn("/run/transfer.bin", control)
        self.assertIn("SSH_ORIGINAL_COMMAND", control)
        install = control.split("\ninstall-recovery)\n", 1)[1].split("\n\t;;", 1)[0]
        self.assertIn("/dev/mmcblk0p1", install)
        self.assertIn("T4DEV.NEW", install)
        self.assertIn("T4DEV.UIM", install)
        self.assertIn("4538432", install)
        self.assertIn("installed_sha256", install)
        self.assertIn("sync", install)
        self.assertNotIn("/dev/mtd", install)
        mtd3 = control.split("\ninstall-mtd3)\n", 1)[1].split("\n\t;;", 1)[0]
        self.assertIn("8126464", mtd3)
        self.assertIn("DCS6100-MTD3-V1", mtd3)
        self.assertIn("led amber", mtd3)
        self.assertIn("led green", mtd3)
        led = control.split("led() {\n", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("/sys/class/leds/led_g/brightness", led)
        self.assertIn("/sys/class/leds/led_r/brightness", led)
        self.assertIn("flashcp -v /run/transfer.bin /dev/mtd3", mtd3)
        self.assertIn("sha256sum /dev/mtd3", mtd3)
        self.assertNotIn("/dev/mtd0", mtd3)
        self.assertNotIn("/dev/mtd1", mtd3)
        self.assertNotIn("/dev/mtd2", mtd3)
        self.assertNotIn("/dev/mtd4", mtd3)
        self.assertNotIn("/dev/mtd5", mtd3)

    def test_nor_inspection_is_exact_read_only_and_activation_is_reconciled(self) -> None:
        control = RECOVERYCTL.read_text(encoding="utf-8")
        inspect_nor = control.split("\ninspect-nor)\n", 1)[1].split("\n\t;;", 1)[0]
        layout_check = control.split("exact_layout() {\n", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("dcs6100lhv2-a1-six-partition", inspect_nor)
        self.assertEqual(layout_check.count('00008000 "'), 6)
        self.assertNotIn('00010000 "', layout_check)
        self.assertIn("sha256sum /dev/mtd1", inspect_nor)
        self.assertIn("sha256sum /dev/mtd2", inspect_nor)
        self.assertIn("mtd3_mounted=yes", inspect_nor)
        self.assertIn("personal_mtd3_identity", inspect_nor)
        self.assertIn("stock_mtd3_matches", inspect_nor)
        self.assertNotIn("flashcp", inspect_nor)
        self.assertNotIn("flash_erase", inspect_nor)
        install = control.split("\ninstall-mtd3)\n", 1)[1].split("\n\t;;", 1)[0]
        self.assertIn("grep -q '^/dev/mtdblock3 ' /proc/mounts && exit 2", install)
        self.assertLess(install.index("/proc/mounts"), install.index("flashcp -v"))
        activate = control.split("\nactivate-mtd3)\n", 1)[1].split("\n\t;;", 1)[0]
        self.assertIn("personal_mtd3_identity || exit 2", activate)
        self.assertNotIn('cat /run/installed-mtd3.sha256', activate)
        self.assertIn("rm -f /run/transfer.bin /run/transfer.footer", activate)
        self.assertLess(
            activate.index("personal_mtd3_identity || exit 2"),
            activate.index("rm -f /run/transfer.bin /run/transfer.footer"),
        )
        self.assertLess(
            activate.index("rm -f /run/transfer.bin /run/transfer.footer"),
            activate.index(": >/run/transition.activate"),
        )

        self.assertNotIn("thingino-failure", control)
        self.assertNotIn("/run/prudynt.log", control)

    def test_ap_station_fallback_and_reset_paths_are_explicit(self) -> None:
        init = INIT.read_text(encoding="utf-8")
        self.assertIn('[ -c /dev/null ] || fail dev', init)
        self.assertIn('tmpfs /root || fail root_tmpfs', init)
        self.assertIn('[ "$mmc_wait" -lt 10 ]', init)
        self.assertIn('fail setup_media_device', init)
        self.assertIn("driver=nl80211", init)
        self.assertNotIn("driver=rtl871xdrv", init)
        self.assertIn('/usr/sbin/mdnsd -H "$setup_host" -i wlan0 -s', init)
        self.assertIn('/etc/mdns.d/recovery.service', init)
        self.assertIn('STATION_READY ${setup_host}.local', init)
        self.assertIn("wpa_supplicant -B -D nl80211", init)
        self.assertNotIn("-D wext", init)
        self.assertIn("-s /run/recovery-udhcpc", init)
        self.assertIn("cat >/run/recovery-udhcpc <<'EOF'", init)
        self.assertIn("[ -e /run/station.bound ]", init)
        self.assertIn("STATION_FAILED_RETURNED_TO_AP", init)
        self.assertIn("THINGINO_FAILED_RETURNED_TO_AP", init)
        self.assertIn("THINGINO_EXITED_RETURNED_TO_AP", init)
        self.assertIn("gpio60", init)
        self.assertIn("RESET_RETURNED_TO_AP", init)
        self.assertIn("-r /run/setup/HOST.KEY", init)
        self.assertIn('kill -0 "$dropbear_pid"', init)
        self.assertNotIn("dropbear -R", init)
        self.assertIn('command="/usr/sbin/recoveryctl dispatch"', init)
        self.assertIn("no-port-forwarding", init)
        self.assertIn("insmod /modules/ingenic_t31_dtrng.ko", init)
        self.assertIn("/usr/sbin/entropy-seed || fail entropy_seed", init)
        self.assertIn("/usr/sbin/entropy-seed || fail entropy_reseed", init)
        self.assertGreaterEqual(init.count("/usr/sbin/entropy-seed"), 2)
        start_ap = init[init.index("start_ap() {") : init.index("start_station() {")]
        self.assertLess(start_ap.index("entropy-seed"), start_ap.index("hostapd -B"))
        self.assertLess(
            init.index("/usr/sbin/entropy-seed"),
            init.index("if ! reset_pressed && run_thingino"),
        )
        self.assertIn("start 192.168.88.2\nend 192.168.88.2", init)
        self.assertIn("/etc/recovery-session", init)
        self.assertIn("THINGINO_ROOT=/mnt/thingino", init)
        self.assertIn('/proc/[0-9]*', init)
        self.assertIn('/bin/readlink "$process_dir/root"', init)
        self.assertIn('/bin/readlink "$process_dir/cwd"', init)
        self.assertIn('thingino_mounts_present && return 1', init)
        self.assertIn(': >/run/thingino-cleanup.failed', init)
        self.assertIn('mount -o bind "/$path" "$THINGINO_ROOT/$path"', init)
        self.assertIn('THINGINO_ETC=/run/thingino-etc', init)
        self.assertIn('cp -a "$THINGINO_ROOT/etc/." "$THINGINO_ETC/"', init)
        self.assertIn('mount -o bind "$THINGINO_ETC" "$THINGINO_ROOT/etc"', init)
        self.assertIn('for path in dev/shm etc run tmp sys proc dev', init)
        self.assertIn('/usr/sbin/thingino-enter &', init)
        self.assertNotIn('chroot "$THINGINO_ROOT"', init)
        self.assertNotIn("/run/thingino/$path", init)
        self.assertIn('blink_failure "$1"', init)
        self.assertIn('wifi_power*|wifi_module) code=5', init)
        self.assertIn('wlan0_missing|hostapd) code=6', init)
        self.assertIn('[ "$wlan_attempt" -lt 15 ]', init)
        self.assertIn('[ "$hostapd_attempt" -lt 3 ]', init)
        self.assertIn('killall hostapd 2>/dev/null || true', init)
        self.assertIn('led amber\n\t\t\tsleep 1\n\t\t\tled off', init)
        led = init[init.index("led() {") : init.index("blink_failure() {")]
        self.assertIn("return 0", led)

    def test_entropy_path_is_hardware_backed_health_checked_and_fail_closed(self) -> None:
        module = DTRNG_SOURCE.read_text(encoding="utf-8")
        loader = ENTROPY_SOURCE.read_text(encoding="utf-8")
        self.assertIn("0x10072000", module)
        self.assertIn('clk_get(NULL, "dtrng")', module)
        self.assertIn("DTRNG_DATA_READY", module)
        self.assertIn('open("/dev/dtrng"', loader)
        self.assertIn("sample_is_sane", loader)
        self.assertIn("RNDADDENTROPY", loader)
        self.assertIn("CREDITED_BITS 384", loader)
        self.assertNotIn("/dev/mtd", module + loader)

    def test_fixed_thingino_launcher_has_only_the_supervised_chroot_contract(self) -> None:
        launcher = THINGINO_ENTER_SOURCE.read_text(encoding="utf-8")
        self.assertIn("SYSCALL_CHROOT = 4061", launcher)
        self.assertIn('static const char root[] = "/mnt/thingino"', launcher)
        self.assertIn('static const char shell[] = "/bin/sh"', launcher)
        self.assertIn("/etc/init.d/rcS; exec sleep 2147483647", launcher)
        self.assertIn("SYSCALL_EXECVE", launcher)
        self.assertNotIn("/dev/mtd", launcher)

    def test_read_only_root_contains_the_sd_mountpoint(self) -> None:
        source = inspect.getsource(build_recovery_ap_root)
        self.assertIn('"media/setup"', source)
        self.assertIn('"mnt/thingino"', source)
        self.assertIn('"usr/sbin/mdnsd"', source)
        self.assertIn('"usr/sbin/thingino-enter"', source)
        self.assertIn('"etc/mdns.d/recovery.service"', source)
        self.assertIn('(root / "var/run").symlink_to("../run")', source)

    def test_cli_exposes_the_root_builder(self) -> None:
        arguments = build_parser().parse_args(
            [
                "build-recovery-ap-root",
                "--target-archive",
                "target.tar",
                "--wifi-module",
                "8188fu.ko",
                "--mmc-module",
                "jzmmc_v12.ko",
                "--dtrng-module",
                "ingenic_t31_dtrng.ko",
                "--entropy-seed",
                "entropy-seed",
                "--output",
                "root.squashfs",
            ]
        )
        self.assertEqual(arguments.command, "build-recovery-ap-root")

        bootstrap = build_parser().parse_args(
            [
                "build-recovery-ap-bootstrap",
                "--kernel",
                "uImage",
                "--linux-config",
                "linux.config",
                "--rootfs",
                "bootstrap.squashfs",
                "--output-dir",
                "out",
            ]
        )
        self.assertEqual(bootstrap.command, "build-recovery-ap-bootstrap")


if __name__ == "__main__":
    unittest.main()
