from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildrootLegalInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner_path = ROOT / "scripts/run_macos_buildroot_legal_info.sh"
        self.collector_path = ROOT / "scripts/container_collect_buildroot_legal_info.sh"
        self.runner = self.runner_path.read_text(encoding="utf-8")
        self.collector = self.collector_path.read_text(encoding="utf-8")

    def _preflight(self, mutation: str) -> subprocess.CompletedProcess[str]:
        if sys.platform != "darwin":
            self.skipTest("executable runner guards require macOS stat semantics")
        temp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
        with tempfile.TemporaryDirectory(dir=temp_base) as temporary:
            root = Path(temporary).resolve()
            lock = root / "lock"
            lock.mkdir()
            (lock / "OWNER.md").write_text("test\n", encoding="utf-8")
            original = root / "original.ext4"
            original.write_bytes(b"original")
            copy = root / "copy.ext4"
            copy.write_bytes(b"copy")
            vendor = root / "vendor"
            rust_source = root / "rust-source"
            rust_toolchain = root / "rust-toolchain"
            result = root / "result"
            for directory in (vendor, rust_source, rust_toolchain, result):
                directory.mkdir()
            audio = root / "audio.so"
            audio.write_bytes(b"audio")
            ingenic = root / "ingenic.tar"
            ingenic.write_bytes(b"ingenic")
            if mutation == "same":
                copy = original
            elif mutation == "hardlink":
                copy.unlink()
                copy.hardlink_to(original)
            elif mutation == "symlink":
                copy.unlink()
                copy.symlink_to(original)
            elif mutation == "overlap":
                vendor = result
            vendor.chmod(0o755)
            vendor_mode_before = stat.S_IMODE(vendor.stat().st_mode)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            uname = fake_bin / "uname"
            uname.write_text("#!/bin/sh\necho Darwin\n", encoding="utf-8")
            uname.chmod(0o700)
            command = [
                str(self.runner_path),
                "--task-scratch-root", str(root),
                "--builder-lock", str(lock),
                "--builder-image", "unused",
                "--expected-builder-image-id", "sha256:" + "0" * 64,
                "--container-name", "legal-test",
                "--original-workspace-image", str(original),
                "--workspace-copy-image", str(copy),
                "--expected-original-sha256", "0" * 64,
                "--vendor-site", str(vendor),
                "--audio-link", str(audio),
                "--rust-source", str(rust_source),
                "--rust-toolchain", str(rust_toolchain),
                "--ingenic-toolchain-archive", str(ingenic),
                "--expected-ingenic-toolchain-sha256", "0" * 64,
                "--result", str(result),
            ]
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
            completed = subprocess.run(command, text=True, capture_output=True, env=env)
            self.assertEqual(stat.S_IMODE(vendor.stat().st_mode), vendor_mode_before)
            return completed

    def _logcat_license_preflight(self, mutation: str) -> subprocess.CompletedProcess[str]:
        program = self.collector.split(
            "# BEGIN_LOGCAT_MINI_LICENSE_PREFLIGHT\n", 1
        )[1].split("# END_LOGCAT_MINI_LICENSE_PREFLIGHT", 1)[0]
        version = "29095262840a807d794ecbe4eda9ee2c425b5015"
        archive_root = f"logcat-mini-{version}"
        license_bytes = b"MIT License\n\nCopyright (c) 2024 fixture\n"
        temp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
        with tempfile.TemporaryDirectory(dir=temp_base) as temporary:
            root = Path(temporary)
            source = root / "source"
            download = source / "dl" / "logcat-mini"
            build = root / "output" / "build" / f"logcat-mini-{version}"
            recipe = source / "package" / "logcat-mini" / "logcat-mini.mk"
            download.mkdir(parents=True)
            build.mkdir(parents=True)
            recipe.parent.mkdir(parents=True)
            recipe_version = "0" * 40 if mutation == "version" else version
            recipe_license = "MIT" if mutation == "declaration" else "GPL-2.0"
            recipe.write_text(
                "LOGCAT_MINI_SITE_METHOD = git\n"
                "LOGCAT_MINI_SITE = https://github.com/wltechblog/logcat-mini\n"
                "LOGCAT_MINI_SITE_BRANCH = main\n"
                f"LOGCAT_MINI_VERSION = {recipe_version}\n"
                f"LOGCAT_MINI_LICENSE = {recipe_license}\n"
                "LOGCAT_MINI_LICENSE_FILES = COPYING\n",
                encoding="utf-8",
            )
            archive = download / f"logcat-mini-{version}-git4.tar.gz"
            archive_members = [("LICENSE", license_bytes)]
            if mutation == "archive-copying":
                archive_members.append(("COPYING", b"unexpected\n"))
            with tarfile.open(archive, "w:gz") as bundle:
                for name, payload in archive_members:
                    info = tarfile.TarInfo(f"{archive_root}/{name}")
                    info.size = len(payload)
                    bundle.addfile(info, io.BytesIO(payload))
            build_license = b"different\n" if mutation == "build-license" else license_bytes
            (build / "LICENSE").write_bytes(build_license)
            if mutation == "build-copying":
                (build / "COPYING").write_bytes(b"unexpected\n")
            archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
            if mutation == "archive-hash":
                archive_sha256 = "0" * 64
            env = dict(
                os.environ,
                source_dir=str(source),
                output_dir=str(root / "output"),
                logcat_mini_recipe=str(recipe),
                logcat_mini_version=version,
                logcat_mini_source_filename=archive.name,
                logcat_mini_archive_license_member=f"{archive_root}/LICENSE",
                logcat_mini_archive_copying_member=f"{archive_root}/COPYING",
                logcat_mini_source_sha256_expected=archive_sha256,
                logcat_mini_license_sha256_expected=hashlib.sha256(license_bytes).hexdigest(),
                logcat_mini_archive_copying_absent="false",
                logcat_mini_build_copying_absent="false",
            )
            return subprocess.run(
                ["sh", "-eu", "-c", program],
                text=True,
                capture_output=True,
                env=env,
                timeout=15,
            )

    def _local_package_supplement(self, mutation: str) -> subprocess.CompletedProcess[str]:
        preflight = self.collector.split("# BEGIN_LOCAL_PACKAGE_LICENSE_PREFLIGHT\n", 1)[1].split(
            "# END_LOCAL_PACKAGE_LICENSE_PREFLIGHT", 1
        )[0]
        apply = self.collector.split("# BEGIN_LOCAL_PACKAGE_LICENSE_APPLY\n", 1)[1].split(
            "# END_LOCAL_PACKAGE_LICENSE_APPLY", 1
        )[0]
        temp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
        with tempfile.TemporaryDirectory(dir=temp_base) as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            certgen = source / "package/mbedtls-certgen"
            daynight = source / "package/thingino-daynightd"
            certgen_build = output / "build/mbedtls-certgen-1.0"
            daynight_build = output / "build/thingino-daynightd-2.0.0"
            payloads = {
                "certgen_recipe": (certgen / "mbedtls-certgen.mk", b"MBEDTLS_CERTGEN_LICENSE = GPL-2.0+\n"),
                "certgen_source": (certgen / "files/mbedtls-certgen.c", b"certgen fixture\n"),
                "certgen_build": (certgen_build / "mbedtls-certgen.c", b"certgen fixture\n"),
                "daynight_recipe": (daynight / "thingino-daynightd.mk", b"THINGINO_DAYNIGHTD_LICENSE = GPL-2.0\n"),
                "daynight_source": (daynight / "files/daynightd.c", b"GPL version 2 or later fixture\n"),
                "daynight_build": (daynight_build / "files/daynightd.c", b"GPL version 2 or later fixture\n"),
                "daynight_readme": (daynight / "files/README.md", b"GNU GPL v2.0 fixture\n"),
                "daynight_build_readme": (daynight_build / "files/README.md", b"GNU GPL v2.0 fixture\n"),
                "supplement": (root / "GPL2.txt", b"canonical license text fixture\n"),
            }
            for path, data in payloads.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            env = dict(os.environ, source_dir=str(source), output_dir=str(output),
                       supplemental_license=str(payloads["supplement"][0]))
            for name in ("certgen_recipe", "certgen_source", "daynight_recipe", "daynight_source", "daynight_readme"):
                env[name + "_sha256_expected"] = hashlib.sha256(payloads[name][1]).hexdigest()
            env["supplemental_sha256"] = hashlib.sha256(payloads["supplement"][1]).hexdigest()
            if mutation in payloads:
                payloads[mutation][0].write_bytes(b"changed\n")
            elif mutation == "existing-license":
                (daynight_build / "LICENSE").write_bytes(b"existing license\n")
            elif mutation == "dangling-license":
                (certgen_build / "LICENSE").symlink_to(root / "absent")
            elif mutation == "source-license":
                (certgen / "files/LICENSE").write_bytes(b"new upstream license\n")
            elif mutation == "symlink-source":
                path, data = payloads["certgen_source"]
                target = root / "source-target"
                target.write_bytes(data)
                path.unlink()
                path.symlink_to(target)
            elif mutation == "symlink-build-directory":
                target = root / "moved-build"
                daynight_build.rename(target)
                daynight_build.symlink_to(target, target_is_directory=True)

            def snapshot() -> dict[str, tuple[str, bytes | str]]:
                return {
                    str(path.relative_to(root)): (
                        ("symlink", os.readlink(path)) if path.is_symlink()
                        else ("file", path.read_bytes())
                    )
                    for path in root.rglob("*") if path.is_symlink() or path.is_file()
                }

            before = snapshot()
            # Fixtures execute real install/hash checks; only container ownership
            # is irrelevant on the host. No mocked content or exit status.
            program = "chown() { :; }\n" + preflight + apply
            program += '\nverify_local_package_license_inputs\ntest "$local_package_supplements_applied" = true\n'
            completed = subprocess.run(["sh", "-eu", "-c", program], env=env,
                                       text=True, capture_output=True, timeout=15)
            after = snapshot()
            if mutation == "valid":
                expected = dict(before)
                for directory in (certgen_build, daynight_build):
                    expected[str((directory / "LICENSE").relative_to(root))] = (
                        "file", payloads["supplement"][1]
                    )
                    self.assertEqual(stat.S_IMODE((directory / "LICENSE").stat().st_mode), 0o644)
                self.assertEqual(after, expected)
            else:
                self.assertEqual(after, before, "failed preflight must not write or overwrite text")
            return completed

    def test_local_package_supplement_copies_only_missing_license_text(self) -> None:
        completed = self._local_package_supplement("valid")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_local_package_supplement_rejects_changed_or_unsafe_inputs_before_writes(self) -> None:
        for mutation in (
            "certgen_recipe", "certgen_source", "certgen_build", "daynight_recipe",
            "daynight_source", "daynight_build", "daynight_readme", "daynight_build_readme",
            "supplement", "existing-license", "dangling-license", "source-license",
            "symlink-source", "symlink-build-directory",
        ):
            with self.subTest(mutation=mutation):
                completed = self._local_package_supplement(mutation)
                self.assertNotEqual(completed.returncode, 0)

    def test_local_package_supplement_pins_and_receipt_preserve_existing_declarations(self) -> None:
        for digest in (
            "fb43fe008d9dcad613e6bf948f371fcd5142b08e5a16d346c450959ea00c404b",
            "512ca9723789ee4a703ce9ab4bdf2991988c0d76a0cbb356a6d850293f50d8ad",
            "36b8db2578fa3b07b143e2a43495d24bd1bbdcb339a5f4370db87609f1298302",
            "55cd93cfcb53c783d6868220d00251732f8d9a0142c1f1dff397bc48df8e0217",
            "c9bc43788e95c5abcc949126c7d089853d3dc86ae81479db3af3a380fbc4cb15",
        ):
            self.assertIn(digest, self.collector)
        self.assertIn('"declaration": "GPL-2.0+"', self.collector)
        self.assertIn('"recipe_declaration": "GPL-2.0"', self.collector)
        self.assertIn('"c_header_declaration": "GPL version 2 or later"', self.collector)
        self.assertNotIn('THINGINO_DAYNIGHTD_LICENSE=', self.collector)
        self.assertNotIn('MBEDTLS_CERTGEN_LICENSE=', self.collector)
        self.assertIn('phase=collect\nverify_local_package_license_inputs', self.collector)

    def test_scripts_are_shell_valid_and_runner_is_offline(self) -> None:
        for path in (self.runner_path, self.collector_path):
            subprocess.run(["sh", "-n", str(path)], check=True)
        self.assertTrue(stat.S_IMODE(self.runner_path.stat().st_mode) & 0o111)
        self.assertIn("umask 077", self.runner)
        self.assertIn("umask 077", self.collector)
        self.assertIn("--network none", self.runner)
        self.assertNotIn("--rm", self.runner)
        self.assertIn('"$original_workspace_image:/input/original-workspace.ext4:ro"', self.runner)
        self.assertIn('"$workspace_copy_image:/input/workspace.ext4"', self.runner)
        self.assertNotIn("curl ", self.runner + self.collector)
        self.assertNotIn("wget ", self.runner + self.collector)

    def test_runner_requires_distinct_hash_bound_inputs(self) -> None:
        self.assertIn("workspace copy must not be a hardlink", self.runner)
        self.assertIn("mount path contains a symlink", self.runner)
        self.assertIn("mount paths must be absolute", self.runner)
        self.assertIn("writable mount overlaps a read-only input", self.runner)
        self.assertIn("builder lock escaped the task scratch root", self.runner)
        self.assertIn("^/(build|collect)\\.sh$", self.runner)
        self.assertIn("workspace copy is not byte-identical", self.runner)
        self.assertIn("expected-builder-image-id", self.runner)
        self.assertIn("expected-ingenic-toolchain-sha256", self.runner)
        self.assertIn("accepted original workspace changed", self.runner)
        self.assertIn("original-workspace-postcheck.json", self.runner)
        for mount in (
            "$vendor_site:/input/vendor-site:ro",
            "$audio_link:/input/media-link/libaudioProcess.so:ro",
            "$rust_source:/input/rust-source:ro",
            "$rust_toolchain:/input/rust-toolchain:ro",
            "$ingenic_toolchain_archive:/input/ingenic-glibc216-toolchain.tar:ro",
        ):
            self.assertIn(mount, self.runner)

    def test_preflight_rejects_original_copy_aliases_before_docker(self) -> None:
        for mutation, message in (
            ("same", "workspace copy must differ"),
            ("hardlink", "workspace copy must not be a hardlink"),
            ("symlink", "missing or symlinked"),
        ):
            with self.subTest(mutation=mutation):
                completed = self._preflight(mutation)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(message, completed.stderr)
                self.assertNotIn("docker", completed.stderr)

    def test_preflight_rejects_writable_input_overlap_before_docker(self) -> None:
        completed = self._preflight("overlap")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("writable mount overlaps a read-only input", completed.stderr)
        self.assertNotIn("docker", completed.stderr)

    def test_collection_is_serial_bounded_and_preserves_failures(self) -> None:
        self.assertIn("timeout --signal=TERM --kill-after=60s 7200s", self.collector)
        self.assertIn('make -k -j1 -C "$source_dir/buildroot"', self.collector)
        self.assertIn('"command": "make -k -j1 -C /workspace/source/buildroot', self.collector)
        self.assertIn("BR2_DL_DIR=$source_dir/dl", self.collector)
        self.assertNotIn("HOME=", self.collector)
        self.assertIn('>"$result_dir/buildroot-legal-info.log" 2>&1', self.collector)
        self.assertIn("# BEGIN_BYTE_ONLY_EXPORT", self.collector)
        self.assertIn("os.O_WRONLY | os.O_CREAT | os.O_EXCL", self.collector)
        self.assertIn("destination_manifest != source_manifest", self.collector)
        self.assertIn("destination_directories != source_directories", self.collector)
        self.assertIn("legal_info_export_verified=true", self.collector)
        self.assertNotIn("cp -R", self.collector)
        self.assertNotIn('cp -a ', self.collector)
        self.assertLess(
            self.collector.index('wifi_source_unchanged=true'),
            self.collector.index("# BEGIN_BYTE_ONLY_EXPORT"),
        )
        self.assertIn('write_receipt failed', self.collector)
        self.assertIn('legal_info_exit_code=$?', self.collector)
        self.assertIn('if [ "$legal_info_exit_code" -eq 0 ]; then legal_info_exit_code=1; fi', self.collector)
        self.assertLess(
            self.collector.index("receipt_ready=true"),
            self.collector.index('test -d "$source_dir/.git"'),
        )
        self.assertIn('"legal_review_approved": false', self.collector)
        self.assertIn('"publication_authorized": false', self.collector)

    def test_gnu_make_keep_going_runs_independent_targets_but_fails(self) -> None:
        temp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
        with tempfile.TemporaryDirectory(dir=temp_base) as temporary:
            root = Path(temporary)
            makefile = root / "Makefile"
            makefile.write_text(
                ".PHONY: legal-info missing-license independent-license\n"
                "legal-info: missing-license independent-license\n"
                "missing-license:\n"
                "\t@printf 'failed\\n' >> trace\n"
                "\t@false\n"
                "independent-license:\n"
                "\t@printf 'continued\\n' >> trace\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                ["make", "-k", "-j1", "-f", str(makefile), "legal-info"],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=15,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual((root / "trace").read_text(encoding="utf-8").splitlines(), [
                "failed",
                "continued",
            ])

    def test_byte_only_export_copies_and_verifies_private_regular_files(self) -> None:
        program = self.collector.split("# BEGIN_BYTE_ONLY_EXPORT\n", 1)[1].split(
            "# END_BYTE_ONLY_EXPORT", 1
        )[0]
        temp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
        with tempfile.TemporaryDirectory(dir=temp_base) as temporary:
            root = Path(temporary)
            source = root / "source"
            nested = source / "nested" / "empty"
            nested.mkdir(parents=True)
            payload = source / "nested" / "payload.bin"
            payload.write_bytes(b"legal-info\x00bytes\n")
            source.chmod(0o755)
            (source / "nested").chmod(0o755)
            payload.chmod(0o444)
            destination = root / "destination"
            completed = subprocess.run(
                [sys.executable, "-c", program, str(source), str(destination)],
                text=True,
                capture_output=True,
                check=True,
            )
            fields = completed.stdout.split()
            self.assertEqual(fields[0], "1")
            self.assertEqual(fields[1], str(len(b"legal-info\x00bytes\n")))
            self.assertEqual(len(fields[2]), 64)
            self.assertEqual((destination / "nested" / "payload.bin").read_bytes(), payload.read_bytes())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((destination / "nested").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((destination / "nested" / "payload.bin").stat().st_mode), 0o600)
            self.assertTrue((destination / "nested" / "empty").is_dir())

    def test_byte_only_export_rejects_symlinks(self) -> None:
        program = self.collector.split("# BEGIN_BYTE_ONLY_EXPORT\n", 1)[1].split(
            "# END_BYTE_ONLY_EXPORT", 1
        )[0]
        temp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir())).resolve()
        with tempfile.TemporaryDirectory(dir=temp_base) as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "target").write_bytes(b"target")
            (source / "link").symlink_to("target")
            completed = subprocess.run(
                [sys.executable, "-c", program, str(source), str(root / "destination")],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unsupported source entry", completed.stderr)

    def test_rtl8188fu_fix_is_copy_only_and_does_not_change_source_archive(self) -> None:
        expected = "4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e"
        self.assertIn(expected, self.runner)
        self.assertIn(expected, self.collector)
        self.assertIn('test ! -e "$wifi_build/COPYING"', self.collector)
        self.assertIn('install -m 0644 "$supplemental_license" "$wifi_build/COPYING"', self.collector)
        self.assertIn('test "$wifi_source_sha256_after" = "$wifi_source_sha256_before"', self.collector)
        self.assertIn('"sha256_before": "$wifi_source_sha256_before"', self.collector)
        self.assertIn('"sha256_after": "$wifi_source_sha256_after"', self.collector)
        self.assertIn('"destination": "$supplement_destination"', self.collector)
        self.assertNotIn("tar -xf", self.collector)
        self.assertIn("not an upstream grant or a per-file licensing decision", self.collector)

    def test_mxml_uses_retained_4_0_4_hashes_without_weakening_checks(self) -> None:
        for expected in (
            "c8d1728d6ccf71a862a1538bd5e132daa2181bb42fe14b078baa2ec1510c0150",
            "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
            "528125ed9bea128efa97005f9d1b0656b483508a9777c123c026d1d3058a38ac",
        ):
            self.assertIn(expected, self.collector)
        self.assertIn('MXML_HASH_FILES="$mxml_hash_file" \\', self.collector)
        self.assertIn("$source_dir/package/all-patches/mxml/mxml.hash", self.collector)
        self.assertIn("$source_dir/package/thingino-mxml/mxml-override.mk", self.collector)
        self.assertIn("mxml_source_sha256_observed", self.collector)
        self.assertIn("mxml_license_sha256_observed", self.collector)
        self.assertIn("mxml_notice_sha256_observed", self.collector)
        self.assertIn(
            'test "$mxml_notice_sha256_observed" = "$mxml_notice_hash_declaration"',
            self.collector,
        )
        self.assertIn("does not edit expected hashes or disable verification", self.collector)
        self.assertNotIn(
            "7dedb0043b0bbed7880bcc9724b3a3dae4d5ab2dce98a7904703109572b626e3",
            self.collector,
        )
        self.assertNotIn("BR_NO_CHECK_HASH_FOR", self.collector)
        self.assertNotIn("BR2_DOWNLOAD_FORCE_CHECK_HASHES", self.collector)
        self.assertNotIn("sed -i", self.collector)

    def test_linux_3_10_14_uses_only_its_retained_copying(self) -> None:
        expected = "af8067302947c01fd9eee72befa54c7e3ef8a48fecde7fd71277f2290b2bf0f7"
        self.assertIn(expected, self.collector)
        self.assertIn('BR2_LINUX_KERNEL_LICENSE_FILES="$linux_license_files" \\', self.collector)
        self.assertIn('linux_license_files=COPYING', self.collector)
        self.assertIn('linux_source_filename=linux-3.10.14.tar.xz', self.collector)
        self.assertIn('tar -xOf "$linux_source_archive" "$linux_source_archive_member"', self.collector)
        self.assertIn(
            'test "$linux_build_copying_sha256" = "$linux_archive_copying_sha256"',
            self.collector,
        )
        self.assertIn('test ! -e "$linux_build/LICENSES/preferred/GPL-2.0"', self.collector)
        self.assertIn('test "$linux_source_sha256_after" = "$linux_source_sha256_before"', self.collector)
        self.assertLess(
            self.collector.index("linux_source_unchanged=true"),
            self.collector.index("# BEGIN_BYTE_ONLY_EXPORT"),
        )
        self.assertIn("does not add files, change license text, or hide Buildroot warnings", self.collector)

    def test_logcat_mini_uses_hash_bound_mit_license_only_for_collection(self) -> None:
        for expected in (
            "29095262840a807d794ecbe4eda9ee2c425b5015",
            "3b8633bd44d600ca6f670979ae827e311eaf7464d98f0c6351f8e3e16e09fcd8",
            "865ae85978be3b1da7943fb401412504dd21e92bb83227d5052c9a8c01ede388",
        ):
            self.assertIn(expected, self.collector)
        self.assertIn('LOGCAT_MINI_LICENSE="$logcat_mini_license" \\', self.collector)
        self.assertIn(
            'LOGCAT_MINI_LICENSE_FILES="$logcat_mini_license_files" legal-info',
            self.collector,
        )
        self.assertIn('logcat_mini_license=MIT', self.collector)
        self.assertIn('logcat_mini_license_files=LICENSE', self.collector)
        self.assertIn("original_license_declaration", self.collector)
        self.assertIn("original_license_files_declaration", self.collector)
        self.assertIn("collector legal-info command on the copied workspace only", self.collector)
        self.assertIn('source_sha256_before', self.collector)
        self.assertIn('source_sha256_after', self.collector)
        self.assertIn('archive_copying_absent', self.collector)
        self.assertIn('build_copying_absent', self.collector)
        self.assertIn(
            'test "$logcat_mini_source_sha256_after" = "$logcat_mini_source_sha256_before"',
            self.collector,
        )
        self.assertIn(
            'test "$logcat_mini_build_license_sha256" = "$logcat_mini_archive_license_sha256"',
            self.collector,
        )
        self.assertIn(
            'test "$logcat_mini_archive_license_sha256" = "$logcat_mini_license_sha256_expected"',
            self.collector,
        )
        self.assertLess(
            self.collector.index("logcat_mini_source_unchanged=true"),
            self.collector.index("# BEGIN_BYTE_ONLY_EXPORT"),
        )
        self.assertNotIn("sed -i", self.collector)

    def test_logcat_mini_preflight_accepts_only_matching_fixture(self) -> None:
        self.assertEqual(self._logcat_license_preflight("valid").returncode, 0)
        for mutation in (
            "version",
            "declaration",
            "archive-hash",
            "build-license",
            "archive-copying",
            "build-copying",
        ):
            with self.subTest(mutation=mutation):
                self.assertNotEqual(self._logcat_license_preflight(mutation).returncode, 0)

    def test_receipt_keeps_known_limitations_explicit(self) -> None:
        self.assertIn("THINGINO_CONTROL_LICENSE is Unknown", self.collector)
        self.assertIn("Buildroot legal-info warnings are preserved", self.collector)
        self.assertIn("not a legal approval", self.collector)
        self.assertIn("Other selected packages still have unresolved", self.collector)
        for required in (
            "README",
            "manifest.csv",
            "host-manifest.csv",
            "buildroot.config",
            "legal-info.sha256",
            "licenses",
            "host-licenses",
            "sources",
            "host-sources",
        ):
            self.assertIn(required, self.collector)


if __name__ == "__main__":
    unittest.main()
