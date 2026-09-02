from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "dlink-dcs6100lhv2-a1"


class ProfileTests(unittest.TestCase):
    def test_feature_profile_and_kernel_inputs_are_byte_exact(self) -> None:
        def sha256(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        self.assertEqual(
            sha256(PROFILE / "defconfig"),
            "c2b76246ca0f162c18e4e7fb30b40e14461adddbb960aa760f2987bb64a7bdb3",
        )
        self.assertEqual(
            sha256(PROFILE / "kernel.fragment"),
            "7b8923008d034f2f8e7c3d0e1bf61f4e6c70da3e789724baf678575f42b5dda3",
        )

    def test_exact_board_identity_and_media_memory_baseline(self) -> None:
        source = (PROFILE / "defconfig").read_text(encoding="utf-8")
        required = (
            'BR2_INGENIC_SOC_MODEL="t31n"',
            'BR2_SENSOR_1_NAME="os02g10"',
            "BR2_PACKAGE_WIFI_RTL8188FU=y",
            "BR2_THINGINO_RMEM_MB=22",
            'BR2_THINGINO_GPIO_LIST="49o,50o,52O,54O,57o,61o,63o"',
            "# BR2_THINGINO_TOOLCHAIN_LIBC_UCLIBC is not set",
            "BR2_THINGINO_TOOLCHAIN_LIBC_GLIBC=y",
            "# BR2_THINGINO_TOOLCHAIN_LIBC_MUSL is not set",
            "# BR2_PACKAGE_THINGINO_SYSUPGRADE is not set",
            "BR2_PACKAGE_PRUDYNT_T_DCS6100_CAMERA_LOCAL_VENDOR=y",
            'BR2_STRIP_EXCLUDE_FILES="libimp.so libalog.so libsysutils.so"',
            "# BR2_PACKAGE_THINGINO_KOPT_IPV6 is not set",
            "# BR2_PACKAGE_THINGINO_ODHCP6C is not set",
            "BR2_PACKAGE_THINGINO_HA=y",
            "BR2_PACKAGE_THINGINO_MOSQUITTO_20X=y",
            "# BR2_PACKAGE_THINGINO_MOSQUITTO_20X_BROKER is not set",
            "# BR2_TARGET_ENABLE_ROOT_LOGIN is not set",
        )
        for line in required:
            self.assertEqual(source.count(line), 1)
        self.assertNotIn("ROOT_PASSWD", source)

    def test_gpio_and_ircut_contract(self) -> None:
        config = json.loads((PROFILE / "thingino.json").read_text(encoding="utf-8"))
        gpio = config["gpio"]
        self.assertEqual(gpio["button_reset"], 60)
        self.assertEqual(gpio["ir850"], 61)
        self.assertEqual(gpio["ircut"], "50 49")
        self.assertEqual(gpio["mmc_cd"], 59)
        self.assertEqual(gpio["wlan"], {"pin": 57, "active_low": False})

    def test_kernel_fragment_has_mmc0_but_no_final_static_layout(self) -> None:
        source = (PROFILE / "kernel.fragment").read_text(encoding="utf-8")
        self.assertIn("CONFIG_JZMMC_V12_MMC0_PB_4BIT=y", source)
        self.assertIn("CONFIG_MMC0_MAX_FREQ=24000000", source)
        self.assertNotIn("CONFIG_CMDLINE=", source)


if __name__ == "__main__":
    unittest.main()
