from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "source_checkout.py"
SPEC = importlib.util.spec_from_file_location("source_checkout", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SOURCES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCES)


def git(path: Path, *arguments: str) -> str:
    environment = os.environ.copy()
    for name in (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_WORK_TREE",
    ):
        environment.pop(name, None)
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        env=environment,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def commit(path: Path, message: str) -> None:
    git(path, "add", "-A")
    git(
        path,
        "-c",
        "user.name=Source Test",
        "-c",
        "user.email=source-test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )


class SourceCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = SOURCES.load_lock()

    def test_public_source_lock_is_valid_and_matches_firmware_epoch(self) -> None:
        self.assertEqual(self.lock["schema_version"], 2)
        self.assertEqual(self.lock["source_date_epoch"], 1_786_006_608)
        self.assertEqual(
            self.lock["sources"]["prudynt"]["tree"],
            "d2262cebd653298b2b51893e680866d7be3fa9ad",
        )
        self.assertEqual(
            self.lock["sources"]["rust_source"],
            {
                "acquisition": "archive",
                "sha256": "62b67230754da642a264ca0cb9fc08820c54e2ed7b3baba0289876d4cdb48c08",
                "url": "https://static.rust-lang.org/dist/rustc-1.95.0-src.tar.xz",
                "version": "1.95.0",
            },
        )
        self.assertEqual(
            self.lock["sources"]["rust_builder_container_amd64"]["digest"],
            "sha256:362e64223cc0da95422b3b13c045186fc0a81250e765d31c025fbddf257f6143",
        )
        self.assertEqual(
            self.lock["sources"]["rust_builder_container_arm64"],
            {
                "acquisition": "oci-image",
                "digest": "sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241",
                "image": "docker.io/library/debian",
                "platform": "linux/arm64",
                "version": "bookworm-slim",
            },
        )
        self.assertEqual(
            self.lock["sources"]["rust_toolchain_aarch64_linux"],
            {
                "acquisition": "archive",
                "host": "aarch64-unknown-linux-gnu",
                "sha256": "094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e",
                "url": "https://static.rust-lang.org/dist/rust-1.95.0-aarch64-unknown-linux-gnu.tar.xz",
                "version": "1.95.0",
            },
        )
        self.assertEqual(
            self.lock["sources"]["rust_std_source_component"]["sha256"],
            "67b09138c8db96afc4bbfc69ea771ac9a091fd777698acb43f6dfd9fb7dea363",
        )
        self.assertEqual(
            self.lock["sources"]["thingino_build_toolchain_x86_64"],
            {
                "acquisition": "unsupported",
                "host": "x86_64-linux",
                "reason": "source-built SDK reproducibility is not validated",
                "version": "gcc16-glibc-xburst1",
            },
        )
        self.assertEqual(
            self.lock["sources"]["thingino_build_toolchain_aarch64"],
            {
                "acquisition": "source-build",
                "archive": "thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz",
                "archive_format": "gnu-tar-sort-name-source-date-epoch-gzip-n",
                "build_target": "buildroot-toolchain-relocatable-sdk",
                "builder_platform": "linux/arm64",
                "host": "aarch64-linux",
                "kernel_headers": "custom-tarball-3.10",
                "kernel_tarball_sha256": "36540d5fb15951be64d4c150cf3bc291a8d4d6699fb988173f6be30aa1e41b47",
                "kernel_tarball_url": "https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.10.14.tar.xz",
                "linux_kernel": False,
                "recipe": "configs/github/toolchain_xburst1_glibc_gcc16_defconfig",
                "sha256": "9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412",
                "source": "thingino_firmware",
                "version": "gcc16-glibc-xburst1",
            },
        )
        self.assertEqual(
            self.lock["sources"]["ingenic_glibc216_toolchain"]["tree"],
            "88314e07175ad8f5d398d684a5263b15bc2345d0",
        )
        self.assertEqual(
            self.lock["sources"]["ingenic_glibc216_toolchain"]["redistribution"],
            "not-authorized-by-this-repository",
        )
        self.assertFalse(any(self.lock["constraints"].values()))

    def test_thingino_toolchains_do_not_use_mutable_release_assets(self) -> None:
        arm64 = self.lock["sources"]["thingino_build_toolchain_aarch64"]
        self.assertEqual(arm64["acquisition"], "source-build")
        x86_64 = self.lock["sources"]["thingino_build_toolchain_x86_64"]
        self.assertEqual(x86_64["acquisition"], "unsupported")
        self.assertNotIn("url", arm64)
        self.assertNotIn("url", x86_64)
        lock_text = (ROOT / "sources.lock.json").read_text(encoding="utf-8")
        self.assertNotIn("/releases/download/toolchain-", lock_text)

    def test_guided_toolchain_build_is_offline_and_hash_bound(self) -> None:
        fetch = (
            ROOT / "scripts/container_fetch_thingino_toolchain_downloads.sh"
        ).read_text(encoding="utf-8")
        build = (ROOT / "scripts/container_build_thingino_toolchain.sh").read_text(
            encoding="utf-8"
        )
        runner = (
            ROOT / "scripts/run_macos_thingino_toolchain_build.sh"
        ).read_text(encoding="utf-8")
        firmware_fetch = (
            ROOT / "scripts/container_fetch_thingino_downloads.sh"
        ).read_text(encoding="utf-8")
        for script in (fetch, build):
            self.assertIn(
                "36540d5fb15951be64d4c150cf3bc291a8d4d6699fb988173f6be30aa1e41b47",
                script,
            )
            self.assertIn("linux-3.10.14.tar.xz", script)
        self.assertIn("--network none", runner)
        self.assertIn("make -C \"$source_dir\" BOARD=\"$board\" GROUP=github", build)
        self.assertIn("make -C \"$source_dir/buildroot\" O=\"$output_dir\"", build)
        self.assertIn("BR2_EXTERNAL=\"$source_dir\" toolchain", build)
        self.assertIn("BR2_EXTERNAL=\"$source_dir\" toolchain-all-source", fetch)
        self.assertIn("relocate-sdk.sh", build)
        self.assertIn("--enable BR2_REPRODUCIBLE", build)
        self.assertIn("--disable BR2_LINUX_KERNEL", build)
        self.assertIn("BR2_KERNEL_HEADERS_CUSTOM_TARBALL", build)
        self.assertIn("BR2_PACKAGE_HOST_LINUX_HEADERS_CUSTOM_3_10", build)
        self.assertIn("https://sources.buildroot.net", build)
        self.assertIn("https://sources.buildroot.net", fetch)
        self.assertIn("mipsel-thingino-linux-gnu-ranlib\" -D", build)
        self.assertIn("lt_cv_sys_lib_dlsearch_path_spec", build)
        self.assertNotIn(
            'HOST_GCC_FINAL_MAKE_OPTS += $(HOST_GCC_COMMON_MAKE_OPTS) LDFLAGS=',
            build,
        )
        self.assertNotIn("HOST_GCC_FINAL_INSTALL_OPTS", build)
        self.assertIn("DCS6100_PRESERVE_GCC_FINAL_PROGRAMS", build)
        self.assertIn("DCS6100_RESTORE_GCC_FINAL_PROGRAMS", build)
        self.assertIn(".dcs6100-programs/cc1plus", build)
        self.assertIn("update-index", build)
        self.assertIn("--assume-unchanged", build)
        self.assertIn("support/scripts/setlocalversion", build)
        self.assertIn("!<arch>", build)
        self.assertIn("sha256sum -c -", build)
        self.assertIn("$toolchain_input", firmware_fetch)
        self.assertIn("sha256sum -c -", firmware_fetch)
        self.assertIn("GIT_ASKPASS=/bin/false", firmware_fetch)
        self.assertIn("GIT_CONFIG_COUNT=1", firmware_fetch)
        self.assertIn("GIT_CONFIG_GLOBAL=/dev/null", firmware_fetch)
        self.assertIn("GIT_CONFIG_KEY_0=http.version", firmware_fetch)
        self.assertIn("GIT_CONFIG_NOSYSTEM=1", firmware_fetch)
        self.assertIn("GIT_CONFIG_VALUE_0=HTTP/1.1", firmware_fetch)
        self.assertIn("GIT_TERMINAL_PROMPT=0", firmware_fetch)
        self.assertEqual(firmware_fetch.count('retry_network_fetch "locked'), 2)
        self.assertIn('if [ "$attempt" -ge 3 ]', firmware_fetch)
        self.assertIn('echo "$label failed after $attempt attempts"', firmware_fetch)

    def test_builder_containerfiles_match_locked_platform_digests(self) -> None:
        for architecture in ("amd64", "arm64"):
            source = self.lock["sources"][
                f"rust_builder_container_{architecture}"
            ]
            containerfile = (
                ROOT / "containers" / f"thingino-builder-{architecture}.Containerfile"
            ).read_text(encoding="utf-8")
            first_line = containerfile.splitlines()[0]
            self.assertEqual(
                first_line,
                f"FROM debian@{source['digest']}",
            )
            self.assertIn("nodejs npm", containerfile)
            self.assertIn(
                "COPY webui/package.json webui/package-lock.json /opt/dcs6100-webui/",
                containerfile,
            )
            self.assertIn(
                "npm ci --ignore-scripts --no-audit --no-fund",
                containerfile,
            )
            self.assertIn('require("esbuild").version', containerfile)
            if architecture == "arm64":
                self.assertIn("dpkg --add-architecture amd64", containerfile)
                self.assertIn("libc6:amd64", containerfile)
                self.assertIn("libstdc++6:amd64", containerfile)

    def test_lock_rejects_mutable_or_authenticated_urls(self) -> None:
        for url in (
            "http://github.com/themactep/thingino-firmware",
            "https://token@github.com/themactep/thingino-firmware",
            "https://github.com/themactep/thingino-firmware?ref=stable",
            "file:///private/source",
        ):
            changed = deepcopy(self.lock)
            changed["sources"]["thingino_firmware"]["url"] = url
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "sources.lock.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(SOURCES.SourceError):
                    SOURCES.load_lock(path)

    def _fixture(self, directory: Path) -> tuple[Path, dict]:
        buildroot_source = directory / "buildroot-source"
        buildroot_source.mkdir()
        git(buildroot_source, "init", "-q")
        (buildroot_source / "COPYING").write_text("test\n", encoding="utf-8")
        commit(buildroot_source, "buildroot")
        buildroot_revision = git(buildroot_source, "rev-parse", "HEAD")
        buildroot_tree = git(buildroot_source, "rev-parse", "HEAD^{tree}")

        checkout = directory / "firmware"
        checkout.mkdir()
        git(checkout, "init", "-q")
        (checkout / "package/ingenic-sdk").mkdir(parents=True)
        (checkout / "package/prudynt-t").mkdir(parents=True)
        (checkout / "package/wifi-rtw-hostapd").mkdir(parents=True)
        (checkout / "package/wifi-rtl8188fu").mkdir(parents=True)
        (checkout / "package/ingenic-sdk/ingenic-sdk.mk").write_text(
            "INGENIC_SDK_SITE_METHOD = git\n"
            "INGENIC_SDK_SITE = https://github.com/themactep/ingenic-sdk\n"
            f"INGENIC_SDK_VERSION = {self.lock['sources']['thingino_ingenic_sdk']['revision']}\n",
            encoding="utf-8",
        )
        (checkout / "package/prudynt-t/prudynt-t.mk").write_text(
            "PRUDYNT_T_SITE_METHOD = git\n"
            "PRUDYNT_T_SITE = https://github.com/themactep/prudynt-t\n"
            f"PRUDYNT_T_VERSION = {self.lock['sources']['prudynt']['revision']}\n",
            encoding="utf-8",
        )
        (checkout / "package/wifi-rtl8188fu/wifi-rtl8188fu.mk").write_text(
            "WIFI_RTL8188FU_SITE_METHOD = git\n"
            "WIFI_RTL8188FU_SITE = https://github.com/gtxaspec/rtl8188ftv-wifi\n"
            f"WIFI_RTL8188FU_VERSION = {self.lock['sources']['rtl8188fu']['revision']}\n",
            encoding="utf-8",
        )
        (checkout / "package/wifi-rtw-hostapd/wifi-rtw-hostapd.mk").write_text(
            "WIFI_RTW_HOSTAPD_SITE_METHOD = git\n"
            "WIFI_RTW_HOSTAPD_SITE = https://github.com/lwfinger/rtl8188eu\n"
            f"WIFI_RTW_HOSTAPD_VERSION = {self.lock['sources']['realtek_hostapd']['revision']}\n",
            encoding="utf-8",
        )
        git(
            checkout,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(buildroot_source),
            "buildroot",
        )
        (checkout / ".gitmodules").write_text(
            '[submodule "buildroot"]\n'
            "\tpath = buildroot\n"
            "\turl = https://github.com/buildroot/buildroot\n"
            "\tbranch = master\n",
            encoding="utf-8",
        )
        git(
            checkout / "buildroot",
            "remote",
            "set-url",
            "origin",
            "https://github.com/buildroot/buildroot",
        )
        commit(checkout, "firmware")
        git(
            checkout,
            "remote",
            "add",
            "origin",
            "https://github.com/themactep/thingino-firmware",
        )

        fixture_lock = deepcopy(self.lock)
        firmware = fixture_lock["sources"]["thingino_firmware"]
        firmware["revision"] = git(checkout, "rev-parse", "HEAD")
        firmware["tree"] = git(checkout, "rev-parse", "HEAD^{tree}")
        fixture_lock["source_date_epoch"] = int(
            git(checkout, "show", "-s", "--format=%ct", "HEAD")
        )
        buildroot = fixture_lock["sources"]["buildroot"]
        buildroot["revision"] = buildroot_revision
        buildroot["tree"] = buildroot_tree
        return checkout, fixture_lock

    def test_checkout_verifies_exact_gitlink_recipes_and_clean_trees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout, lock = self._fixture(Path(directory))
            result = SOURCES.verify_checkout(checkout, lock)
            self.assertTrue(result["verified"])
            self.assertEqual(
                result["sources"]["buildroot"]["revision"],
                lock["sources"]["buildroot"]["revision"],
            )

    def test_checkout_ignores_callers_repository_local_git_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout, lock = self._fixture(Path(directory))
            with patch.dict(
                os.environ,
                {
                    "GIT_DIR": "/nonexistent/foreign-git-dir",
                    "GIT_INDEX_FILE": "/nonexistent/foreign-index",
                    "GIT_WORK_TREE": "/nonexistent/foreign-worktree",
                },
            ):
                self.assertTrue(SOURCES.verify_checkout(checkout, lock)["verified"])

    def test_checkout_rejects_dirty_tree_and_recipe_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout, lock = self._fixture(Path(directory))
            (checkout / "untracked.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(SOURCES.SourceError, "dirty"):
                SOURCES.verify_checkout(checkout, lock)
            (checkout / "untracked.txt").unlink()
            recipe = checkout / "package/ingenic-sdk/ingenic-sdk.mk"
            recipe.write_text(
                recipe.read_text(encoding="utf-8").replace(
                    lock["sources"]["thingino_ingenic_sdk"]["revision"],
                    "0" * 40,
                ),
                encoding="utf-8",
            )
            commit(checkout, "recipe drift")
            changed_lock = deepcopy(lock)
            firmware = changed_lock["sources"]["thingino_firmware"]
            firmware["revision"] = git(checkout, "rev-parse", "HEAD")
            firmware["tree"] = git(checkout, "rev-parse", "HEAD^{tree}")
            changed_lock["source_date_epoch"] = int(
                git(checkout, "show", "-s", "--format=%ct", "HEAD")
            )
            with self.assertRaisesRegex(
                SOURCES.SourceError, "package revision mismatch"
            ):
                SOURCES.verify_checkout(checkout, changed_lock)

    def test_fetch_refuses_repository_and_existing_destinations(self) -> None:
        with self.assertRaisesRegex(SOURCES.SourceError, "outside the repository"):
            SOURCES._outside_repository(ROOT / "private" / "source")
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(SOURCES.SourceError, "refusing to reuse"):
                SOURCES._outside_repository(existing)


if __name__ == "__main__":
    unittest.main()
