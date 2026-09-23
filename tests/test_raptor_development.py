from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from installer import raptor_development as profile
from installer.mtd3_split import derive_final_layout, final_kernel_command_line
from installer.stage1.build import _build_install_set, Stage1BuildError
from installer.stage2 import build_stage2, validate_stage2, validate_stage2_for_profile
from test_artifacts import test_squashfs, test_uimage


class RaptorDevelopmentTests(unittest.TestCase):
    def test_stage1_cli_uses_both_exact_profiles(self):
        result = subprocess.run([sys.executable, "-B", "-m", "installer.stage1.build", "--help"],
                                check=True, capture_output=True, text=True)
        self.assertIn("{" + ",".join(profile.PROFILES) + "}", result.stdout)
        self.assertEqual(profile.PROFILES, {
            "full-raptor-development-20260910-256k":
                ("2e3e796f0839ac07a123301fa549ec4e9d6d06c95ac510e78c82711ec1f0c830", 6328320),
            "full-raptor-development-20260911-services":
                ("df7f104157bf80fbbc23e58f1817e012a5dff6482e4cab7ffa9e4d0604ee86d5", 6397952),
        })

    def setUp(self):
        system = test_squashfs(0x18000)
        # Equal sizes deliberately force cross-profile rejection by digest.
        self.systems = {profile.PROFILE: system,
                        profile.SERVICES_PROFILE: system[:-1] + b"x"}
        command = final_kernel_command_line(derive_final_layout(len(system)))
        command = command.replace("mem=42M@0x0 rmem=22M@0x2a00000",
                                  "mem=42M@0x0 rmem=22M@0x2a00000 ipv6.disable=1")
        self.kernel = test_uimage(command.encode("ascii"))

    def fixture_identities(self):
        # Synthetic framing tests never claim physical acceptance of these bytes.
        return patch.multiple(profile,
            PROFILES={name: (hashlib.sha256(system).hexdigest(), len(system))
                      for name, system in self.systems.items()},
            KERNEL_SHA256=hashlib.sha256(self.kernel).hexdigest())

    def test_unreviewed_images_are_never_admitted(self):
        for name, system in self.systems.items():
            with self.subTest(profile=name), self.assertRaises(ValueError):
                profile.validate_candidate(profile=name, kernel=self.kernel,
                                           system=system, data_mode="preserve")

    def test_known_tuples_require_explicit_profile_and_preserve(self):
        with self.fixture_identities():
            for name, system in self.systems.items():
                for invalid in (None, "raptor", "unknown", "default", "universal",
                                "full-raptor-development-20260910"):
                    with self.subTest(profile=name, invalid=invalid), self.assertRaises(ValueError):
                        profile.validate_candidate(profile=invalid, kernel=self.kernel,
                                                   system=system, data_mode="preserve")
                for mode in ("initialize", "factory-reset", "unknown"):
                    with self.subTest(profile=name, mode=mode), self.assertRaises(ValueError):
                        profile.validate_candidate(profile=name, kernel=self.kernel,
                                                   system=system, data_mode=mode)
                    with self.assertRaises(ValueError):
                        build_stage2(final_kernel=self.kernel, system_rootfs=system,
                                     data_mode=mode, development_profile=name)
                command = profile.validate_candidate(profile=name, kernel=self.kernel,
                                                     system=system, data_mode="preserve")
                self.assertIn("mem=42M@0x0 rmem=22M@0x2a00000 ipv6.disable=1", command)

    def test_cross_profile_hash_size_and_kernel_mutations_are_rejected(self):
        with self.fixture_identities():
            for name, system in self.systems.items():
                other = next(value for key, value in self.systems.items() if key != name)
                for kernel, changed in ((self.kernel + b"x", system),
                                        (self.kernel, b"x" + system[1:]),
                                        (self.kernel, system + b"x"), (self.kernel, other)):
                    with self.subTest(profile=name), self.assertRaises(ValueError):
                        profile.validate_candidate(profile=name, kernel=kernel,
                                                   system=changed, data_mode="preserve")

    def test_development_payloads_cannot_pass_default_or_other_profile(self):
        payloads = {}
        with self.fixture_identities():
            for name, system in self.systems.items():
                raw = build_stage2(final_kernel=self.kernel, system_rootfs=system,
                                   data_mode="preserve", development_profile=name)
                payloads[name] = raw
                parsed = validate_stage2_for_profile(raw, name)
                self.assertEqual(parsed.data_mode, "preserve")
                self.assertEqual(parsed.system_flash_span, 6619136)
                self.assertEqual(parsed.data_flash_span, 1507328)
                with self.assertRaises(ValueError): validate_stage2(raw)
                for rejected in (None, "unknown", "full-raptor-development-20260910",
                                 next(key for key in self.systems if key != name)):
                    with self.subTest(profile=name, rejected=rejected), self.assertRaises(ValueError):
                        validate_stage2_for_profile(raw, rejected)
        for name, raw in payloads.items():
            with self.assertRaises(ValueError): validate_stage2_for_profile(raw, name)

    def test_universal_admission_rejects_both_before_building(self):
        for name, system in self.systems.items():
            with patch("installer.stage1.build.os.chmod"), self.assertRaisesRegex(Stage1BuildError, "not a universal"):
                _build_install_set(installer_kernel=b"", final_kernel=self.kernel,
                    final_linux_config=b"", system=system, mmc_module=b"",
                    output_dir=Path("unused"), clang=Path("unused"), lld=Path("unused"),
                    mksquashfs=Path("unused"), unsquashfs=Path("unused"),
                    data_mode="preserve", artifact_scope="model-universal", development_profile=name)

    def test_build_dependencies_are_frozen_individually(self):
        inputs = {"installer_kernel": b"kernel", "linux_config": b"config", "mmc": b"mmc"}
        with patch.multiple(profile,
                INSTALLER_KERNEL_SHA256=hashlib.sha256(inputs["installer_kernel"]).hexdigest(),
                LINUX_CONFIG_SHA256=hashlib.sha256(inputs["linux_config"]).hexdigest(),
                MMC_SHA256=hashlib.sha256(inputs["mmc"]).hexdigest()):
            profile.validate_build_inputs(**inputs)
            for key in inputs:
                with self.subTest(input=key), self.assertRaises(ValueError):
                    profile.validate_build_inputs(**{**inputs, key: b"wrong"})
        with self.assertRaises(ValueError): profile.validate_build_inputs(**inputs)


if __name__ == "__main__":
    unittest.main()
