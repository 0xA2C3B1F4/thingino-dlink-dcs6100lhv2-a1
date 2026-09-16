from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "source_prepare.py"
SPEC = importlib.util.spec_from_file_location("source_prepare", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PREP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREP)
TMP_ROOT = Path(tempfile.gettempdir())


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
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
        "user.name=Source Preparation Test",
        "-c",
        "user.email=source-preparation@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class SourceProfileTests(unittest.TestCase):
    def test_raptor_motion_workers_use_mips32_supported_atomics(self) -> None:
        for name in ("motion_email.rs", "motion_ftp.rs", "motion_gotify.rs", "motion_ntfy.rs", "motion_telegram.rs", "motion_webhook.rs"):
            source = (
                ROOT / "components/thingino-control/src/raptor_backend" / name
            ).read_text(encoding="utf-8")
            self.assertNotIn("AtomicU64", source, name)
            self.assertIn("AtomicU32", source, name)

    def test_project_patches_are_raw_diffs(self) -> None:
        for patch_path in sorted((ROOT / "patches").rglob("*.patch")):
            text = patch_path.read_text(encoding="utf-8")
            first_line = text.splitlines()[0]
            self.assertTrue(
                first_line.startswith(("diff --git ", "--- a/")),
                f"project patch has a mail envelope: {patch_path.relative_to(ROOT)}",
            )
            for header in ("From: ", "Date: ", "Subject: ", "Signed-off-by: "):
                self.assertNotIn(
                    header,
                    text,
                    f"project patch has identity metadata: {patch_path.relative_to(ROOT)}",
                )

    def test_uhttpd_onvif_patch_preserves_control_proxy_headers(self) -> None:
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as raw_directory:
            checkout = Path(raw_directory)
            git(checkout, "init", "-q")
            git(
                checkout,
                "apply",
                "--include=control_proxy.c",
                str(
                    ROOT
                    / "patches"
                    / "uhttpd"
                    / "0007-proxy-thingino-control.patch"
                ),
            )
            onvif_patch = (
                ROOT
                / "patches"
                / "uhttpd"
                / "0008-disable-cgi-and-proxy-onvif.patch"
            ).read_text(encoding="utf-8")
            control_start = onvif_patch.index(
                "diff --git a/control_proxy.c b/control_proxy.c\n"
            )
            control_end = onvif_patch.index(
                "diff --git a/main.c b/main.c\n", control_start
            )
            strict_patch = subprocess.run(
                ["patch", "-F0", "-p1", "--batch"],
                cwd=checkout,
                check=False,
                input=onvif_patch[control_start:control_end],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(strict_patch.returncode, 0, strict_patch.stdout)

            control_proxy = (checkout / "control_proxy.c").read_text(
                encoding="utf-8"
            )
            for required in (
                "const char *requested_with;",
                'requested_with = control_proxy_header(cl, "x-requested-with");',
                '"X-Requested-With: Thingino-WebUI\\r\\n"',
                "CONTROL_ONVIF_PORT 1999",
                "onvif_snapshot",
                "struct dispatch_handler control_proxy_dispatch = {",
                ".check_url = control_proxy_url,",
                ".handle_request = control_proxy_request,",
            ):
                self.assertIn(required, control_proxy)

            for patch_name in (
                "0010-defer-control-proxy-pump.patch",
                "0011-account-tls-socket-backpressure.patch",
                "0012-keep-mjpeg-encoder-profile-static.patch",
                "0014-allow-prometheus-control-responses.patch",
                "0015-require-authenticated-tls-ingress.patch",
                "0016-buffer-request-before-backend.patch",
                "0017-bind-recording-file-identity.patch",
            ):
                patch_text = (
                    ROOT / "patches" / "uhttpd" / patch_name
                ).read_text(encoding="utf-8")
                marker = "diff --git a/control_proxy.c b/control_proxy.c\n"
                control_start = patch_text.index(marker)
                control_end = patch_text.find(
                    "diff --git ", control_start + len(marker)
                )
                if control_end < 0:
                    control_end = len(patch_text)
                strict_patch = subprocess.run(
                    ["patch", "-F0", "-p1", "--batch"],
                    cwd=checkout,
                    check=False,
                    input=patch_text[control_start:control_end],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                self.assertEqual(
                    strict_patch.returncode,
                    0,
                    f"{patch_name}:\n{strict_patch.stdout}",
                )

            hardened_proxy = (checkout / "control_proxy.c").read_text(
                encoding="utf-8"
            )
            self.assertIn("\tconst char *requested_with;", hardened_proxy)
            self.assertIn("\tconst char *host, *origin;", hardened_proxy)

        static_html_numstat = git(
            ROOT,
            "apply",
            "--numstat",
            str(
                ROOT
                / "patches"
                / "uhttpd"
                / "0013-never-cache-static-html-shell.patch"
            ),
        )
        self.assertEqual(static_html_numstat, "34\t0\tfile.c")

    def test_static_webui_cleanup_requires_all_legacy_helpers(self) -> None:
        source = b'''rm -rf "${TARGET_DIR}/var/www"\nrm -rf "${TARGET_DIR}/usr/libexec/thingino-webui"\nrm -f "${TARGET_DIR}/usr/sbin/formatsd" \\\n+    "${TARGET_DIR}/usr/sbin/envfromcard" \\\n+    "${TARGET_DIR}/usr/sbin/telegram-cam-register" \\\n+    "${TARGET_DIR}/usr/sbin/telegram-cam-agent"\n'''
        PREP.validate_static_webui_cleanup(source)

        with self.assertRaisesRegex(
            PREP.PreparationError, "telegram-cam-agent"
        ):
            PREP.validate_static_webui_cleanup(
                source.replace(
                    b'    "${TARGET_DIR}/usr/sbin/telegram-cam-agent"\n', b""
                )
            )

    def test_profile_replacement_is_bound_to_the_existing_bytes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as directory:
            root = Path(directory)
            target = root / "existing"
            target.write_bytes(b"old")
            PREP._write_snapshot(
                root,
                target,
                b"new",
                source_date_epoch=1_786_006_608,
                replaces_sha256=digest(b"old"),
            )
            self.assertEqual(target.read_bytes(), b"new")

            with self.assertRaisesRegex(PREP.PreparationError, "changed"):
                PREP._write_snapshot(
                    root,
                    target,
                    b"newer",
                    source_date_epoch=1_786_006_608,
                    replaces_sha256=digest(b"unexpected"),
                )

    def test_public_profile_and_patch_scope_are_exact(self) -> None:
        profile = PREP.load_profile()
        self.assertEqual(profile["model"], "DCS-6100LHV2")
        self.assertEqual(profile["hardware_revision"], "A1")
        self.assertEqual(len(profile["thingino_patches"]), 20)
        self.assertEqual(len(profile["installed_files"]), 163)
        installed_sources = {entry["source"] for entry in profile["installed_files"]}
        self.assertTrue({
            "components/thingino-control/src/raptor.rs",
            "components/thingino-control/src/raptor_backend.rs",
            "components/thingino-control/src/raptor_backend/access.rs",
            "components/thingino-control/src/raptor_backend/imaging.rs",
            "components/thingino-control/src/raptor_backend/motion.rs",
            "components/thingino-control/src/raptor_backend/motion_actions.rs",
            "components/thingino-control/src/raptor_backend/motion_lifecycle.rs",
            "components/thingino-control/src/raptor_backend/motion_gotify.rs",
            "components/thingino-control/src/raptor_backend/motion_email.rs",
            "components/thingino-control/src/raptor_backend/motion_email_tests.rs",
            "components/thingino-control/src/raptor_backend/motion_ftp.rs",
            "components/thingino-control/src/raptor_backend/motion_ftp_tests.rs",
            "components/thingino-control/src/raptor_backend/motion_ntfy.rs",
            "components/thingino-control/src/raptor_backend/motion_roi.rs",
            "components/thingino-control/src/raptor_backend/motion_telegram.rs",
            "components/thingino-control/src/raptor_backend/motion_webhook.rs",
            "components/thingino-control/src/raptor_backend/privacy.rs",
            "webui/firmware-bundle.json",
            "webui/scripts/build.mjs",
            "webui/scripts/firmware-bundle.mjs",
        }.issubset(installed_sources))
        icon_sources = {
            entry["source"] for entry in profile["installed_files"]
            if entry["destination"].startswith("dcs6100-webui/public/icons/")
        }
        self.assertEqual(icon_sources, {
            "webui/public/icons/LICENSE.txt", "webui/public/icons/grid.svg",
            "webui/public/icons/help-circle.svg", "webui/public/icons/info.svg",
            "webui/public/icons/radio.svg", "webui/public/icons/settings.svg",
            "webui/public/icons/tool.svg", "webui/public/icons/video.svg",
        })
        self.assertEqual(
            profile["installed_files"][0]["destination"],
            "configs/cameras-exp/"
            "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu/"
            "collector-kernel.fragment",
        )
        self.assertEqual(
            profile["installed_files"][4]["destination"],
            "configs/cameras-exp/"
            "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu/"
            "recovery-ap-kernel.fragment",
        )
        self.assertEqual(
            profile["installed_files"][5]["destination"],
            "configs/cameras-exp/"
            "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu/"
            "stock-restore-kernel.fragment",
        )
        installed_destinations = {
            item["destination"] for item in profile["installed_files"]
        }
        expected_webui = {
            "dcs6100-webui/firmware-bundle.json",
            "dcs6100-webui/scripts/build.mjs",
            "dcs6100-webui/scripts/firmware-bundle.mjs",
            "dcs6100-webui/index.html",
            "dcs6100-webui/public/manifest.webmanifest",
            "dcs6100-webui/tsconfig.json",
            *{"dcs6100-" + source for source in icon_sources},
            *{
                "dcs6100-webui/" + path.relative_to(ROOT / "webui").as_posix()
                for path in (ROOT / "webui/src").rglob("*")
                if path.is_file()
            },
        }
        self.assertEqual(
            {path for path in installed_destinations if path.startswith("dcs6100-webui/")},
            expected_webui,
        )
        self.assertTrue(
            {
                "package/thingino-control/build-rust.py",
                "package/thingino-control/files/S95thingino-control",
                "package/thingino-control/files/thingino-control.json",
                "package/thingino-control/rust/Cargo.lock",
                "package/thingino-control/rust/Cargo.toml",
                "package/thingino-control/rust/fixtures/motion-state-machine-v1.json",
                "package/thingino-control/rust/storage-worker/mod.rs",
                "package/thingino-control/rust/src/camera.rs",
                "package/thingino-control/rust/src/camera/actions.rs",
                "package/thingino-control/rust/src/camera/config/access.rs",
                "package/thingino-control/rust/src/camera/config/crontab.rs",
                "package/thingino-control/rust/src/camera/config/domains.rs",
                "package/thingino-control/rust/src/camera/config/gpio.rs",
                "package/thingino-control/rust/src/camera/config/mod.rs",
                "package/thingino-control/rust/src/camera/config/network.rs",
                "package/thingino-control/rust/src/camera/config/time.rs",
                "package/thingino-control/rust/src/camera/diagnostics.rs",
                "package/thingino-control/rust/src/camera/host.rs",
                "package/thingino-control/rust/src/camera/motion_events.rs",
                "package/thingino-control/rust/src/camera/network.rs",
                "package/thingino-control/rust/src/camera/platform.rs",
                "package/thingino-control/rust/src/camera/runtime.rs",
                "package/thingino-control/rust/src/camera/storage.rs",
                "package/thingino-control/rust/src/json.rs",
                "package/thingino-control/rust/src/lib.rs",
                "package/thingino-control/rust/src/main.rs",
                "package/thingino-control/rust/src/protocol.rs",
                "package/thingino-control/rust/src/request.rs",
                "package/thingino-control/rust/src/response.rs",
                "package/thingino-control/rust/src/router.rs",
                "package/thingino-control/rust/src/server.rs",
                "package/thingino-control/rust/src/web_auth.rs",
                "package/thingino-control/rust/src/whip.rs",
                "package/thingino-onvif/0001-persistent-httpd-no-request-children.patch",
                "dcs6100-webui/src/app/fullscreen-preview.ts",
                "package/thingino-uhttpd/0007-proxy-thingino-control.patch",
                "package/thingino-uhttpd/0008-disable-cgi-and-proxy-onvif.patch",
                "package/thingino-uhttpd/0009-remove-request-script-runtime.patch",
                "package/thingino-uhttpd/0010-defer-control-proxy-pump.patch",
                "package/thingino-uhttpd/0011-account-tls-socket-backpressure.patch",
                "package/thingino-uhttpd/0012-keep-mjpeg-encoder-profile-static.patch",
                "package/thingino-uhttpd/0013-never-cache-static-html-shell.patch",
                "package/thingino-uhttpd/0014-allow-prometheus-control-responses.patch",
                "package/thingino-uhttpd/0015-require-authenticated-tls-ingress.patch",
                "package/thingino-uhttpd/0016-buffer-request-before-backend.patch",
                "package/thingino-uhttpd/0017-bind-recording-file-identity.patch",
            }
            <= installed_destinations
        )

    def test_thingino_baseline_patch_scope(self) -> None:
        thingino = (
            ROOT / "patches/thingino/0001-dlink-first-release-baseline.patch"
        ).read_text(encoding="utf-8")
        paths = [
            line.split(" b/", 1)[1]
            for line in thingino.splitlines()
            if line.startswith("diff --git a/")
        ]
        self.assertEqual(
            paths,
            [
                "Config.soc.in",
                "Makefile",
                "package/thingino-onvif/thingino-onvif.mk",
                "package/thingino-webui/thingino-webui.mk",
                "package/busybox/busybox.config",
                "package/ingenic-sdk/ingenic-sdk.mk",
                "scripts/rootfs_script.sh",
                "thingino.mk",
                "package/thingino-kopt/thingino-kopt.mk",
            ],
        )
        for forbidden in (
            "package/thingino-agent/",
            "package/thingino-daynightd/",
            "incremental-package-",
            "DCS_DLINK_MINIMAL_PIPELINE",
            "DCS_DLINK_SKIP_HARDWARE_OSD",
            "DCS_DLINK_ALLOW_UNSYNCED_STARTUP",
            "DCS_DLINK_OS02G10_IQ",
            "DCS_DLINK_MEDIA_READY",
            "DCS_DLINK_SKIP_ISP_BYPASS",
        ):
            self.assertNotIn(forbidden, thingino)
        for required in (
            "SOURCE_DATE_EPOCH",
            "THINGINO_MMC0_MAX_FREQ := 24000000",
        ):
            self.assertIn(required, thingino)

    def test_vendor_and_startup_build_patches(self) -> None:
        imp114 = (
            ROOT / "patches/thingino/0002-dlink-glibc-imp114.patch"
        ).read_text(encoding="utf-8")
        imp114_paths = [
            line.split(" b/", 1)[1]
            for line in imp114.splitlines()
            if line.startswith("diff --git a/")
        ]
        self.assertEqual(
            imp114_paths,
            [
                "package/ingenic-lib/ingenic-lib.mk",
                "package/ingenic-sdk/ingenic-sdk.mk",
            ],
        )
        self.assertIn("SDK_VERSION := 1.1.4", imp114)
        self.assertIn("INGENIC_SDK_MODULE_MAKE_OPTS += ISP_FW_VER=1.1.4", imp114)

        kernel_release = (
            ROOT / "patches/thingino/0013-sanitize-kernel-release-capture.patch"
        ).read_text(encoding="utf-8")
        self.assertEqual(kernel_release.count("--no-print-directory -s -C"), 2)
        self.assertIn("package/exfat-nofuse/exfat-nofuse.mk", kernel_release)
        self.assertIn("package/ingenic-sdk/ingenic-sdk.mk", kernel_release)

        exfat_source = (
            ROOT / "patches/thingino/0023-pin-exfat-nofuse-source-archive.patch"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "EXFAT_NOFUSE_SITE = $(call github,dorimanx,exfat-nofuse,",
            exfat_source,
        )
        self.assertIn(
            "b88a98f0a7e1b987465f5ccfcafb384b293506c7fec9d3b91b803e0fe5b16e0a",
            exfat_source,
        )
        self.assertIn("EXFAT_NOFUSE_LICENSE_FILES = LICENSE", exfat_source)
        self.assertNotIn("+EXFAT_NOFUSE_SITE_METHOD = git", exfat_source)

        normalized_neo = (
            ROOT / "patches/thingino/0025-normalize-neo-library-comments.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("package/ingenic-system-libs-neo/ingenic-system-libs-neo.mk", normalized_neo)
        self.assertEqual(normalized_neo.count("--remove-section=.comment"), 2)
        self.assertIn("$(TARGET_DIR)/usr/lib/libalog.so", normalized_neo)
        self.assertIn("$(TARGET_DIR)/usr/lib/libsysutils.so", normalized_neo)

    def test_buildroot_stamp_floor_patch_is_complete(self) -> None:
        outer = ROOT / "patches/thingino/0026-floor-buildroot-package-stamp-mtimes.patch"
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(
                ["git", "apply", "--whitespace=error-all", str(outer)],
                cwd=temporary,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            inner = (
                Path(temporary)
                / "package/all-patches/buildroot/0004-floor-package-stamp-mtimes.patch"
            ).read_text(encoding="utf-8")
            self.assertIn("define stampfile-touch", inner)
            self.assertIn("for prerequisite in $^", inner)
            self.assertIn(
                'test -e "$$prerequisite" || { rm -f "$@"; exit 1; }',
                inner,
            )
            self.assertIn('touch -r "$$prerequisite" "$@"', inner)
            self.assertIn('rm -f "$@"', inner)
            self.assertEqual(inner.count("-\t$(Q)touch $@"), 12)
            self.assertEqual(inner.count("+\t$(Q)$(call stampfile-touch)"), 12)

            added = [
                line[1:]
                for line in inner.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ]
            macro_start = added.index("define stampfile-touch")
            macro_end = added.index("endef", macro_start) + 1
            root = Path(temporary)
            makefile = root / "stamp-floor.mk"
            makefile.write_text(
                "\n".join(
                    [
                        *added[macro_start:macro_end],
                        "Q = @",
                        "target: first second | order-only",
                        "\t@printf 'run\\n' >> runs",
                        "\t$(Q)$(call stampfile-touch)",
                        "missing:",
                        "\t@:",
                        "failed-target: missing",
                        "\t$(Q)$(call stampfile-touch)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            shim = root / "touch"
            shim.write_text(
                "#!/bin/sh\n"
                "if [ \"${1-}\" = -r ]; then exec /usr/bin/touch \"$@\"; fi\n"
                "exec /usr/bin/touch -t 202001010000 \"$@\"\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
            timestamps = {
                "first": 1_609_459_200_111_111_111,
                "second": 1_640_995_200_222_222_222,
                "order-only": 1_672_531_200_333_333_333,
            }
            for name, timestamp in timestamps.items():
                path = root / name
                path.touch()
                os.utime(path, ns=(timestamp, timestamp))
            environment = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}"}
            command = ["make", "-f", str(makefile), "target"]
            subprocess.run(command, cwd=root, env=environment, check=True)
            self.assertEqual((root / "target").stat().st_mtime_ns, timestamps["second"])
            subprocess.run(command, cwd=root, env=environment, check=True)
            self.assertEqual((root / "runs").read_text().splitlines(), ["run"])
            timestamps["first"] = 1_704_067_200_444_444_444
            os.utime(root / "first", ns=(timestamps["first"], timestamps["first"]))
            subprocess.run(command, cwd=root, env=environment, check=True)
            self.assertEqual((root / "target").stat().st_mtime_ns, timestamps["first"])
            self.assertEqual((root / "runs").read_text().splitlines(), ["run", "run"])
            failed = subprocess.run(
                ["make", "-f", str(makefile), "failed-target"],
                cwd=root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((root / "failed-target").exists())

    def test_verified_service_stop_and_daynight_routing(self) -> None:
        uhttpd_stop = (
            ROOT / "patches/thingino/0014-stop-uhttpd-by-verified-pid.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'readlink "/proc/$pid/exe"',
            'kill -TERM "$pid"',
            '[ "$waits" -lt 50 ]',
            'rm -f "$PIDFILE"',
        ):
            self.assertIn(required, uhttpd_stop)
        uhttpd_stop_added = "\n".join(
            line[1:]
            for line in uhttpd_stop.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("pgrep uhttpd", uhttpd_stop_added)
        self.assertNotIn("pkill uhttpd", uhttpd_stop_added)

        daynight_control = (
            ROOT / "patches/thingino/0015-route-daynight-actuation-through-control.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "Thingino Control observes this canonical state file",
            "printf 1 > /sys/class/gpio/gpio50/value",
            "usleep 100000",
        ):
            self.assertIn(required, daynight_control)
        self.assertNotIn("package/prudynt-t/", daynight_control)
        self.assertNotIn("-THINGINO_DAYNIGHTD_DEPENDENCIES", daynight_control)
        daynight_removed = "\n".join(
            line[1:]
            for line in daynight_control.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        daynight_added = "\n".join(
            line[1:]
            for line in daynight_control.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for forbidden in (
            "popen(",
            "/sbin/daynight",
            "$(TARGET_DIR)/usr/sbin/light",
            "$(TARGET_DIR)/usr/sbin/ircut",
            "curl ",
        ):
            self.assertNotIn(forbidden, daynight_added)

    def test_timezone_kernel_and_aac_build_patches(self) -> None:
        linux_patch_directories = (
            ROOT / "patches/thingino/0018-create-linux-patch-directories.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "LINUX_CREATE_THINGINO_PATCH_DIRECTORIES",
            "mkdir -p $(LINUX_DIR)/android/configs",
            "LINUX_PRE_PATCH_HOOKS += LINUX_CREATE_THINGINO_PATCH_DIRECTORIES",
        ):
            self.assertIn(required, linux_patch_directories)

        reproducible_aac = (
            ROOT / "patches/thingino/0019-reproducible-aac-link-order.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "-print0",
            "LC_ALL=C sort -z",
            "xargs -0",
        ):
            self.assertIn(required, reproducible_aac)

    def test_persistent_overlay_and_sd_automount_patches(self) -> None:
        persistent_overlay = (
            ROOT
            / "patches/thingino/0020-overlay-refuse-implicit-data-format.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("explicit recovery required", persistent_overlay)
        self.assertIn(".thingino-factory-reset", persistent_overlay)
        self.assertIn("reset_overlay_if_requested", persistent_overlay)
        self.assertIn("! -name .thingino-factory-reset", persistent_overlay)
        self.assertIn("Failed to commit persistent overlay reset", persistent_overlay)
        added_overlay = "\n".join(
            line[1:]
            for line in persistent_overlay.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("flash_eraseall", added_overlay)

        verified_handoff = (
            ROOT
            / "patches/thingino/0022-accept-verified-stage1-mtd-handoff.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("THINGINO_DLINK_VERIFIED_MTD_ROOT", verified_handoff)
        self.assertIn("unset THINGINO_DLINK_VERIFIED_MTD_ROOT", verified_handoff)
        self.assertIn("Not an MTD RootFS!", verified_handoff)

        sd_automount = (
            ROOT / "patches/thingino/0021-harden-dlink-sd-automount.patch"
        ).read_text(encoding="utf-8")
        sd_automount_added = "\n".join(
            line[1:]
            for line in sd_automount.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for required in (
            "mmcblk0p1",
            "/dev/mmcblk0p1",
            "/mnt/mmcblk0p1",
            "nosuid,nodev,noexec",
        ):
            self.assertIn(required, sd_automount_added)
        for forbidden in (
            "run.sh",
            "runonce.sh",
            "kill -9",
            "umount -l",
            "fsck -V -y",
        ):
            self.assertNotIn(forbidden, sd_automount_added)

    def test_hostapd_and_control_build_integration(self) -> None:
        hostapd = (
            ROOT / "patches/thingino/0003-register-realtek-recovery-hostapd.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("package/wifi-rtw-hostapd/Config.in", hostapd)

        control = (
            ROOT / "patches/thingino/0005-add-thingino-control.patch"
        ).read_text(encoding="utf-8")
        control_paths = [
            line.split(" b/", 1)[1]
            for line in control.splitlines()
            if line.startswith("diff --git a/")
        ]
        self.assertEqual(
            control_paths,
            [
                "Config.in",
                "package/thingino-control/Config.in",
                "package/thingino-control/thingino-control.mk",
            ],
        )
        for required in (
            "BR2_PACKAGE_THINGINO_CONTROL",
            "--rust-toolchain-root",
            "--rustc-source-root",
            "--ingenic-toolchain-root",
            "/usr/sbin/thingino-controld",
            "/usr/share/thingino-defaults/20-control.json",
        ):
            self.assertIn(required, control)
        self.assertNotIn("THINGINO_AGENT_INSTALL_TARGET_CMDS", control)

    def test_authenticated_http_proxy_contract(self) -> None:
        direct_proxy = (
            ROOT / "patches/uhttpd/0007-proxy-thingino-control.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "control_proxy.c",
            "CONTROL_PROXY_MAX_BODY 4096",
            "CONTROL_PROXY_MAX_RESPONSE (2 * 1024 * 1024)",
            "X-Thingino-Proxy: 1",
            "X-Thingino-Remote-Addr: %s",
            'blobmsg_add_string(&proxy->hdr, "Content-Disposition", value)',
            'attachment; filename=\\"snapshot-ch0.jpg\\"',
            'attachment; filename=\\"snapshot-ch1.jpg\\"',
            '"/api/v1/actions/snapshot?stream_id=0"',
            '"/api/v1/actions/snapshot?stream_id=1"',
            'snprintf(output, len, "/snapshot?ch=0")',
            'snprintf(output, len, "/snapshot?ch=1")',
            "control_proxy_is_media_url",
            "control_proxy_session_cookie",
            'static const char prefix[] = "thingino_session="',
            "sizeof(prefix) - 1",
            '"; Path=/; Max-Age=86400; HttpOnly; SameSite=Strict"',
            '"thingino_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"',
        ):
            self.assertIn(required, direct_proxy)
        self.assertNotIn('strncmp(value, "thingino_session=", 18)', direct_proxy)
        self.assertNotIn("fork(", direct_proxy)
        self.assertNotIn("system(", direct_proxy)
        no_cgi_proxy = (
            ROOT / "patches/uhttpd/0008-disable-cgi-and-proxy-onvif.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "CONTROL_ONVIF_PORT 1999",
            "control_proxy_is_media_url(url)",
            "uh_dispatch_add(&control_proxy_dispatch)",
            "CGI execution is disabled",
            "request-process relay is excluded",
        ):
            self.assertIn(required, no_cgi_proxy)
        self.assertNotIn("+\tpid = fork();", no_cgi_proxy)
        self.assertNotIn("+\t\t\texecl(", no_cgi_proxy)
        hardened_ingress = (
            ROOT / "patches/uhttpd/0015-require-authenticated-tls-ingress.patch"
        ).read_text(encoding="utf-8")
        for required in (
            '"; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Strict"',
            '"thingino_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"',
            "control_proxy_host_allowed",
            "control_proxy_origin_matches",
            "if (!control_proxy_tls(cl) && !proxy->onvif)",
            'control_proxy_fail(cl, 426, "Upgrade Required", "https_required")',
            'control_proxy_fail(cl, 421, "Misdirected Request", "invalid_origin")',
            'uh_http_header(cl, 308, "Permanent Redirect")',
            '"Location: https://%s/\\r\\nContent-Length: 0\\r\\n\\r\\n"',
            'if (!control_proxy_host_allowed(host))',
            '"Strict-Transport-Security"',
            '"X-Thingino-Proxy: 1\\r\\nX-Thingino-Remote-Addr: %s\\r\\n"',
        ):
            self.assertIn(required, hardened_ingress)
        self.assertNotIn('uh_http_header(cl, 426, "Upgrade Required")', hardened_ingress)
        self.assertIn('-\t\t\t\t"X-Thingino-Proxy: 2\\r\\n");', hardened_ingress)
        self.assertNotIn('+\t\t\t\t"X-Thingino-Proxy: 2\\r\\n");', hardened_ingress)
        buffered_admission = (
            ROOT / "patches/uhttpd/0016-buffer-request-before-backend.patch"
        ).read_text(encoding="utf-8")
        buffered_added = "\n".join(
            line[1:]
            for line in buffered_admission.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for required in (
            "char *request_body;",
            "proxy->request_body = malloc(proxy->request_body_size);",
            "cl->dispatch.data_done = control_proxy_data_done;",
            "proxy->request_body_used != proxy->request_body_size",
            "control_proxy_start_backend(cl, proxy->backend_port)",
            'control_proxy_fail(cl, 408, "Request Timeout", "request_timeout")',
            "free(proxy->request_body);",
        ):
            self.assertIn(required, buffered_added)
        self.assertIn(
            "-\tif (control_proxy_start_backend(cl, backend_port))",
            buffered_admission,
        )

    def test_http_proxy_runtime_and_backpressure(self) -> None:
        no_script_runtime = (
            ROOT / "patches/uhttpd/0009-remove-request-script-runtime.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "D-Link production uhttpd does not support request script plugins",
            "SET(SOURCES main.c listen.c client.c utils.c file.c auth.c control_proxy.c)",
            "TARGET_LINK_LIBRARIES(uhttpd ${ubox} dl ${LIBS})",
            'getopt(argc, argv, "A:ab:C:c:Dd:E:e:fh:I:K:k:L:l:m:N:O:o:P:p:qRr:Ss:T:U:u:X")',
        ):
            self.assertIn(required, no_script_runtime)
        self.assertNotIn(
            "+TARGET_LINK_LIBRARIES(uhttpd ${ubox} dl ${json_script}",
            no_script_runtime,
        )
        deferred_proxy = (
            ROOT / "patches/uhttpd/0010-defer-control-proxy-pump.patch"
        ).read_text(encoding="utf-8")
        deferred_added = "\n".join(
            line[1:]
            for line in deferred_proxy.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for required in (
            "CONTROL_PROXY_PUMP_BYTES (16 * 1024)",
            "CONTROL_DEFER_START_MEDIA",
            "CONTROL_DEFER_START_LOCAL_FILE",
            "control_proxy_deferred",
            "accepted = ustream_write(cl->us, buf, count, true)",
            "uloop_timeout_set(&proxy->deferred, 1)",
        ):
            self.assertIn(required, deferred_added)
        for forbidden in (
            "ustream_poll(cl->us)",
            "backend->notify_read(backend, 0)",
            "uloop_timeout_set(&proxy->finish, 0)",
        ):
            self.assertNotIn(forbidden, deferred_added)
        tls_backpressure = (
            ROOT / "patches/uhttpd/0011-account-tls-socket-backpressure.patch"
        ).read_text(encoding="utf-8")
        tls_backpressure_added = "\n".join(
            line[1:]
            for line in tls_backpressure.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("control_proxy_client_pending", tls_backpressure_added)
        self.assertIn(
            "ustream_pending_data(cl->ssl.conn, true)", tls_backpressure_added
        )
        self.assertEqual(
            tls_backpressure_added.count(
                "control_proxy_client_pending(cl) >= 4096"
            ),
            2,
        )

    def test_http_static_mjpeg_and_security_headers(self) -> None:
        static_mjpeg = (
            ROOT / "patches/uhttpd/0012-keep-mjpeg-encoder-profile-static.patch"
        ).read_text(encoding="utf-8")
        static_mjpeg_added = "\n".join(
            line[1:]
            for line in static_mjpeg.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        proxy_base = (
            ROOT / "patches/uhttpd/0007-proxy-thingino-control.patch"
        ).read_text(encoding="utf-8")
        for accepted in ('!strcmp(token, "q")', '!strcmp(token, "w")', '!strcmp(token, "h")'):
            self.assertIn(accepted, proxy_base)
        for discarded in (
            'MEDIA_PARAM("q", quality);',
            'MEDIA_PARAM("w", width);',
            'MEDIA_PARAM("h", height);',
        ):
            self.assertIn(f"-\t{discarded}", static_mjpeg)
            self.assertNotIn(discarded, static_mjpeg_added)
        html_cache = (
            ROOT / "patches/uhttpd/0013-never-cache-static-html-shell.patch"
        ).read_text(encoding="utf-8")
        html_cache_added = "\n".join(
            line[1:]
            for line in html_cache.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertIn("cl->dispatch.no_cache = true;", html_cache_added)
        self.assertIn(
            'blobmsg_add_string(&cl->hdr_response, "Cache-Control"',
            html_cache_added,
        )
        self.assertIn('"no-store, max-age=0"', html_cache_added)
        for header in (
            "Content-Security-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ):
            self.assertIn(header, html_cache_added)
        self.assertIn("frame-ancestors 'none'", html_cache_added)
        self.assertIn("script-src 'self'", html_cache_added)

    def test_request_script_runtime_removal(self) -> None:
        no_script_runtime = (
            ROOT / "patches/uhttpd/0009-remove-request-script-runtime.patch"
        ).read_text(encoding="utf-8")
        builder = (ROOT / "scripts/container_build_thingino.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("libjct|libjson_script|libblobmsg_json|libjson-c", builder)
        for removed_source in ("cgi.c", "proc.c", "plugin.c", "handler.c", "relay.c"):
            self.assertIn(removed_source, no_script_runtime)
        onvif_httpd = (
            ROOT / "patches/onvif/0001-persistent-httpd-no-request-children.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'RESOURCE_DIRECTORY "/usr/share/onvif"',
            "ONVIF_LISTEN_PORT 1999",
            "--wrap=system,--wrap=popen,--wrap=pclose",
            "__wrap_system",
            "__wrap_popen",
            'X-Thingino-Remote-Addr',
        ):
            self.assertIn(required, onvif_httpd)
        for forbidden in ("+    fork(", "+    vfork(", "+    execl("):
            self.assertNotIn(forbidden, onvif_httpd)
        extensions = (
            ROOT / "patches/thingino/0009-remove-extension-cgi-and-mqtt-dispatch.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("/api/v1/config/ha", extensions)
        self.assertIn("rm -f $(TARGET_DIR)/var/www/x/json-config-ha.cgi", extensions)
        self.assertNotIn("+\t$(INSTALL)", extensions)
        no_cgi_root = (
            ROOT
            / "patches/thingino/0010-persistent-onvif-and-disable-cgi-ingress.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "/usr/sbin/onvif-httpd",
            "/usr/share/onvif",
            "S94onvif-httpd",
            'UHTTPD_COMMON_ARGS="-f -T 15 -k 10 -N 100 -E /index.html"',
            "rm -rf $(TARGET_DIR)/var/www-portal",
        ):
            self.assertIn(required, no_cgi_root)

    def test_static_webui_installation_contract(self) -> None:
        static_webui = (
            ROOT / "patches/thingino/0011-install-static-dlink-webui.patch"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            [
                line.split(" b/", 1)[1]
                for line in static_webui.splitlines()
                if line.startswith("diff --git a/")
            ],
            ["scripts/rootfs_script.sh"],
        )
        for required in (
            'DCS6100_WEBUI_DIST="${BR2_EXTERNAL}/dcs6100-webui/dist"',
            'rm -rf "${TARGET_DIR}/var/www"',
            'rm -rf "${TARGET_DIR}/usr/libexec/thingino-webui"',
            'node "${BR2_EXTERNAL}/dcs6100-webui/scripts/firmware-bundle.mjs"',
            'cp -R "${DCS6100_WEBUI_DIST}/." "${TARGET_DIR}/var/www/"',
            '-type f -exec chmod 0644 {} +',
            '"${TARGET_DIR}/etc/init.d/S48webui-config"',
            '"${TARGET_DIR}/usr/sbin/mqtt-sub-dispatcher"',
        ):
            self.assertIn(required, static_webui)

    def test_container_build_failure_and_reproducibility_contract(self) -> None:
        container_build = (ROOT / "scripts/container_build_thingino.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('GIT_AUTHOR_DATE="@$source_epoch +0000"', container_build)
        self.assertIn('GIT_COMMITTER_DATE="@$source_epoch +0000"', container_build)
        self.assertIn("symbolic-ref HEAD refs/heads/master", container_build)
        for required in (
            'make -C "$source_dir" CAMERA="$profile" GROUP=exp "$@" \\\n\t\tbuild',
            "error while loading shared libraries:",
            "Buildroot host tool failed to load a shared library",
            'find "$output_dir/target" -print0 | tr -cd',
            'find "$output_dir/target/usr/lib/modules"',
            "DCS6100_RUST_TOOLCHAIN_DIR=$rust_toolchain",
            "DCS6100_RUST_SOURCE_DIR=$rust_source",
            "DCS6100_INGENIC_TOOLCHAIN_DIR=$ingenic_toolchain",
            "DCS6100_AUDIOPROCESS_LINK_FILE=$audio_link",
            "/opt/dcs6100-webui",
            'runuser -u builder -- node scripts/build.mjs',
            'node "$webui_source/scripts/firmware-bundle.mjs" "$webui_dist"',
            'webui_manifest=$(node "$webui_source/scripts/firmware-bundle.mjs"',
            '"webui": $webui_manifest',
            "f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273",
            "10a133f02022bab9c3e0d765c7acb4fd049758cf49cb3affc8e7618951288139",
            "d01fea85b5aa9e08bb3d97d3f8b592a3cafe2e1e84d5c19ba4999d2123f70c4b",
            "grep -qx 'CONFIG_IPV6=y'",
            'test -x "$output_dir/target/usr/sbin/thingino-controld"',
            'test -x "$output_dir/target/etc/init.d/S95thingino-control"',
            'test -x "$output_dir/target/sbin/mkfs.vfat"',
            'test ! -e "$output_dir/target/usr/sbin/formatsd"',
            'test ! -e "$output_dir/target/usr/sbin/envfromcard"',
            "d8da684a19b1eac7b5f69844495cfd1c4d04f28db792b9eb605b6deb3978f271",
            'test -x "$output_dir/target/etc/init.d/S06ircut"',
            'timezone_catalog=$output_dir/target/usr/share/tz.json',
            '"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"',
            "grep -c 'usleep 100000'",
            "rm -f /run/transfer.bin /run/transfer.footer",
            'test -x "$output_dir/target/usr/sbin/onvif-httpd"',
            'test -x "$output_dir/target/etc/init.d/S94onvif-httpd"',
            'test ! -e "$output_dir/target/usr/sbin/thingino-agentd"',
            'test ! -e "$output_dir/target/usr/sbin/thingino-agentctl"',
            'test ! -e "$output_dir/target/usr/libexec/thingino-agent/lib.sh"',
            'test ! -e "$output_dir/target/var/www/x"',
            'test ! -e "$output_dir/target/var/www/onvif"',
            'test ! -e "$output_dir/target/var/www-portal"',
            'test ! -e "$output_dir/target/usr/libexec/thingino-webui"',
            'test ! -e "$output_dir/target/etc/init.d/S48webui-config"',
            'test ! -e "$output_dir/target/usr/sbin/mqtt-sub-dispatcher"',
            'test -z "$(find "$output_dir/target" -type f -name \'*.cgi\' -print -quit)"',
            'test -z "$(find "$output_dir/target" -path \'*thingino-agent*\' -print -quit)"',
            "awk '$7 == \"UND\" && $8 ~ /^fork@/ { count++ } END { print count + 0 }'",
            "-exec sed -E 's@/\\*.*\\*/@@g; s@\".*\"@@g; s@//.*@@g'",
            "grep -Eo '(^|[^[:alnum:]_])fork[[:space:]]*\\('",
            "grep -Fq 'if (!nofork)'",
            "grep -Fq 'switch (fork())'",
            "x86_64 Thingino firmware build is unavailable",
            "thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz",
            "9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412",
        ):
            self.assertIn(required, container_build)
        self.assertNotIn("LD_LIBRARY_PATH=$output_dir/host", container_build)

    def test_vendor_build_site_and_audio_link_contract(self) -> None:
        imp114 = (
            ROOT / "patches/thingino/0002-dlink-glibc-imp114.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("INGENIC_LIB_SITE_METHOD = local", imp114)
        self.assertIn("INGENIC_LIB_SITE = $(DCS6100_VENDOR_BUNDLE_DIR)", imp114)
        self.assertIn("INGENIC_LIB_REDISTRIBUTE = NO", imp114)
        self.assertIn("DCS6100_VALIDATE_CAMERA_VENDOR_SITE", imp114)
        self.assertIn("14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe", imp114)
        self.assertIn("DCS6100_AUDIOPROCESS_LINK_FILE", imp114)
        self.assertIn("f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273", imp114)
        self.assertIn("$(STAGING_DIR)/usr/lib/libaudioProcess.so", imp114)
        self.assertEqual(imp114.count("$(DCS6100_AUDIOPROCESS_LINK_FILE)"), 5)
        self.assertIn("archive is not fetched or packaged", imp114)
        self.assertNotIn("IMP_SDK_LIB_DIR", imp114)
        self.assertNotIn("package/prudynt-t/", imp114)
        self.assertNotIn("PRUDYNT_IMP_", imp114)

    def _fixture(self, directory: Path) -> tuple[Path, Path, Path, dict]:
        public_root = directory / "public"
        public_root.mkdir()
        (public_root / "patches").mkdir()
        (public_root / "profiles").mkdir()

        buildroot = directory / "buildroot"
        buildroot.mkdir()
        git(buildroot, "init", "-q")
        (buildroot / "COPYING").write_text("buildroot\n", encoding="ascii")
        commit(buildroot, "buildroot")

        checkout = directory / "checkout"
        checkout.mkdir()
        git(checkout, "init", "-q")
        (checkout / "base.txt").write_text("before\n", encoding="ascii")
        (checkout / "overlay").mkdir()
        (checkout / "overlay/init").write_bytes(
            b"\n".join(
                (
                    b"THINGINO_DLINK_VERIFIED_MTD_ROOT",
                    b"unset THINGINO_DLINK_VERIFIED_MTD_ROOT",
                    b"mount_jffs2 data /overlay",
                    b"reset_overlay_if_requested() { /overlay/.thingino-factory-reset; }",
                    b"reset_overlay_if_requested\nmount_overlay placeholder",
                    b"mount -t overlayfs -o noatime,lowerdir=/,upperdir=/overlay overlay /mnt",
                    b'mount_overlay || die "Failed to mount overlay!"',
                    b'pivot_root /mnt /mnt/rom || die "Failed to pivot_root!"',
                    b"mount -o noatime,move /rom/overlay /overlay",
                )
            )
        )
        override_raw = (
            b"diff --git a/COPYING b/COPYING\n"
            b"index 6745e6a..1ac6f94 100644\n"
            b"--- a/COPYING\n"
            b"+++ b/COPYING\n"
            b"@@ -1 +1 @@\n"
            b"-buildroot\n"
            b"+buildroot patched\n"
        )
        override_dir = checkout / "package/all-patches/buildroot"
        override_dir.mkdir(parents=True)
        (override_dir / "0001-test.patch").write_bytes(override_raw)
        git(
            checkout,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(buildroot),
            "buildroot",
        )
        commit(checkout, "source")

        patch_raw = (
            b"diff --git a/base.txt b/base.txt\n"
            b"index 90be1bd..2c15216 100644\n"
            b"--- a/base.txt\n"
            b"+++ b/base.txt\n"
            b"@@ -1 +1 @@\n"
            b"-before\n"
            b"+after\n"
        )
        installed_raw = b"profile input\n"
        (public_root / "patches/base.patch").write_bytes(patch_raw)
        (public_root / "profiles/input.txt").write_bytes(installed_raw)
        manifest = {
            "schema_version": 1,
            "hardware_revision": "A1",
            "installed_files": [
                {
                    "destination": "profile/copied.txt",
                    "sha256": digest(installed_raw),
                    "source": "profiles/input.txt",
                }
            ],
            "model": "DCS-6100LHV2",
            "profile_name": (
                "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu"
            ),
            "thingino_patches": [
                {
                    "sha256": digest(patch_raw),
                    "source": "patches/base.patch",
                }
            ],
        }
        manifest_path = public_root / "profiles/source-profile.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True), encoding="utf-8"
        )
        lock = {
            "source_date_epoch": 1_700_000_000,
            "sources": {"buildroot": {"path": "buildroot"}},
        }
        return public_root, checkout, manifest_path, lock

    def test_persistent_overlay_init_contract_fails_closed(self) -> None:
        valid = b"\n".join(
            (
                b"THINGINO_DLINK_VERIFIED_MTD_ROOT",
                b"unset THINGINO_DLINK_VERIFIED_MTD_ROOT",
                b"mount_jffs2 data /overlay",
                b"reset_overlay_if_requested() { /overlay/.thingino-factory-reset; }",
                b"reset_overlay_if_requested\nmount_overlay placeholder",
                b"mount -t overlayfs -o noatime,lowerdir=/,upperdir=/overlay overlay /mnt",
                b'mount_overlay || die "Failed to mount overlay!"',
                b'pivot_root /mnt /mnt/rom || die "Failed to pivot_root!"',
                b"mount -o noatime,move /rom/overlay /overlay",
            )
        )
        PREP.validate_persistent_overlay_init(valid)
        with self.assertRaisesRegex(PREP.PreparationError, "erase"):
            PREP.validate_persistent_overlay_init(
                valid + b"\nflash_eraseall -j /dev/mtd4\n"
            )
        with self.assertRaisesRegex(PREP.PreparationError, "boot order"):
            PREP.validate_persistent_overlay_init(
                valid.replace(b"mount_jffs2 data /overlay\n", b"")
                + b"\nmount_jffs2 data /overlay\n"
            )
        with self.assertRaisesRegex(PREP.PreparationError, "required operation"):
            PREP.validate_persistent_overlay_init(
                valid.replace(b"unset THINGINO_DLINK_VERIFIED_MTD_ROOT\n", b"")
            )

    def test_preparation_exports_exact_trees_and_applies_profile_atomically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as raw_directory:
            directory = Path(raw_directory)
            public_root, checkout, manifest_path, lock = self._fixture(directory)
            destination = directory / "prepared"
            verification = {
                "sources": {"thingino_firmware": {"revision": "a" * 40}},
                "constraints": {"camera_operation_allowed": False},
            }
            with patch.object(
                PREP.sources,
                "verify_checkout",
                return_value=verification,
            ):
                result = PREP.prepare_source(
                    checkout,
                    destination,
                    lock=lock,
                    profile_path=manifest_path,
                    repository_root=public_root,
                )

            self.assertTrue(result["prepared"])
            self.assertEqual(
                (destination / "base.txt").read_text(encoding="ascii"),
                "after\n",
            )
            self.assertEqual(
                (destination / "buildroot/COPYING").read_text(encoding="ascii"),
                "buildroot patched\n",
            )
            self.assertEqual(
                (destination / "profile/copied.txt").read_bytes(),
                b"profile input\n",
            )
            self.assertFalse((destination / ".git").exists())
            generated = json.loads(
                (
                    destination / "dcs6100-source-preparation.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn(str(checkout), json.dumps(generated))
            self.assertEqual(generated["source_date_epoch"], 1_700_000_000)
            self.assertEqual(
                generated["buildroot_override_patches"],
                [
                    {
                        "source": "package/all-patches/buildroot/0001-test.patch",
                        "sha256": digest(
                            (checkout / "package/all-patches/buildroot/0001-test.patch").read_bytes()
                        ),
                    }
                ],
            )
            self.assertEqual(
                int((destination / "base.txt").stat().st_mtime),
                1_700_000_000,
            )
            self.assertEqual(int(destination.stat().st_mtime), 1_700_000_000)

    def test_profile_rejects_digest_drift_and_symlink_inputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as raw_directory:
            directory = Path(raw_directory)
            public_root, _checkout, manifest_path, _lock = self._fixture(directory)
            source = public_root / "profiles/input.txt"
            source.write_bytes(b"changed\n")
            with self.assertRaisesRegex(PREP.PreparationError, "digest mismatch"):
                PREP.load_profile(
                    manifest_path, repository_root=public_root
                )

            source.unlink()
            source.symlink_to(public_root / "patches/base.patch")
            with self.assertRaisesRegex(PREP.PreparationError, "cannot open"):
                PREP.load_profile(
                    manifest_path, repository_root=public_root
                )

    def test_profile_rejects_patch_path_escape(self) -> None:
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as raw_directory:
            directory = Path(raw_directory)
            public_root, _checkout, manifest_path, _lock = self._fixture(directory)
            patch_path = public_root / "patches/base.patch"
            raw = patch_path.read_bytes().replace(
                b"a/base.txt b/base.txt",
                b"a/../escape b/../escape",
            )
            patch_path.write_bytes(raw)
            profile = json.loads(manifest_path.read_text(encoding="utf-8"))
            profile["thingino_patches"][0]["sha256"] = digest(raw)
            manifest_path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(
                PREP.PreparationError, "safe relative path"
            ):
                PREP.load_profile(
                    manifest_path, repository_root=public_root
                )

    def test_preparation_refuses_reuse_and_cleans_failed_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as raw_directory:
            directory = Path(raw_directory)
            public_root, checkout, manifest_path, lock = self._fixture(directory)
            existing = directory / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(PREP.PreparationError, "refusing to reuse"):
                PREP._destination(
                    existing,
                    repository_root=public_root,
                    checkout=checkout,
                )

            bad_patch = public_root / "patches/base.patch"
            raw = bad_patch.read_bytes().replace(b"-before", b"-absent")
            bad_patch.write_bytes(raw)
            profile = json.loads(manifest_path.read_text(encoding="utf-8"))
            profile["thingino_patches"][0]["sha256"] = digest(raw)
            manifest_path.write_text(json.dumps(profile), encoding="utf-8")
            destination = directory / "failed"
            with patch.object(
                PREP.sources,
                "verify_checkout",
                return_value={"sources": {}, "constraints": {}},
            ):
                with self.assertRaisesRegex(
                    PREP.PreparationError, "patch preflight"
                ):
                    PREP.prepare_source(
                        checkout,
                        destination,
                        lock=lock,
                        profile_path=manifest_path,
                        repository_root=public_root,
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(
                list(directory.glob(".failed.prepare-*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
