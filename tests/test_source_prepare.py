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
        self.assertEqual(len(profile["thingino_patches"]), 19)
        self.assertEqual(len(profile["installed_files"]), 149)
        self.assertEqual(
            profile["installed_files"][0]["destination"],
            "configs/cameras-exp/"
            "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu/"
            "collector-kernel.fragment",
        )
        self.assertEqual(
            profile["installed_files"][3]["destination"],
            "configs/cameras-exp/"
            "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu/"
            "recovery-ap-kernel.fragment",
        )
        self.assertEqual(
            profile["installed_files"][4]["destination"],
            "configs/cameras-exp/"
            "dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu/"
            "stock-restore-kernel.fragment",
        )
        installed_destinations = {
            item["destination"] for item in profile["installed_files"]
        }
        expected_webui = {
            "dcs6100-webui/index.html",
            "dcs6100-webui/public/manifest.webmanifest",
            "dcs6100-webui/tsconfig.json",
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
                "package/thingino-control/rust/src/camera/api.rs",
                "package/thingino-control/rust/src/camera/config/access.rs",
                "package/thingino-control/rust/src/camera/config/crontab.rs",
                "package/thingino-control/rust/src/camera/config/daynight.rs",
                "package/thingino-control/rust/src/camera/config/domains.rs",
                "package/thingino-control/rust/src/camera/config/gpio.rs",
                "package/thingino-control/rust/src/camera/config/imaging.rs",
                "package/thingino-control/rust/src/camera/config/mod.rs",
                "package/thingino-control/rust/src/camera/config/network.rs",
                "package/thingino-control/rust/src/camera/config/recorder.rs",
                "package/thingino-control/rust/src/camera/config/schema.rs",
                "package/thingino-control/rust/src/camera/config/send.rs",
                "package/thingino-control/rust/src/camera/config/time.rs",
                "package/thingino-control/rust/src/camera/diagnostics.rs",
                "package/thingino-control/rust/src/camera/files.rs",
                "package/thingino-control/rust/src/camera/maintenance.rs",
                "package/thingino-control/rust/src/camera/motion.rs",
                "package/thingino-control/rust/src/camera/motion_events.rs",
                "package/thingino-control/rust/src/camera/network.rs",
                "package/thingino-control/rust/src/camera/platform.rs",
                "package/thingino-control/rust/src/camera/prudynt.rs",
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
                "package/prudynt-t/0010-media-bound-JPEG-demand-and-receive-lifecycle.patch",
                "package/prudynt-t/0011-http-keep-shared-JPEG-static-and-loopback-only.patch",
                "package/prudynt-t/0012-media-replace-unsafe-dlink-video-restarts.patch",
                "package/prudynt-t/0013-http-bound-mjpeg-client-threads.patch",
                "package/prudynt-t/0014-media-keep-dlink-live-controls-in-process.patch",
                "package/prudynt-t/0015-reload-camera-timezone.patch",
                "package/prudynt-t/0016-scale-burnin-osd-per-stream.patch",
                "package/prudynt-t/0017-motion-add-nonblocking-Control-observation-output.patch",
                "package/prudynt-t/0018-motion-remove-legacy-shell-event-path.patch",
                "package/prudynt-t/0019-motion-clear-stale-state-on-process-start.patch",
                "package/prudynt-t/0020-config-reload-after-complete-write.patch",
                "package/prudynt-t/0021-media-stabilize-IPC-clients-and-expose-queue-metrics.patch",
                "package/prudynt-t/0022-recorder-require-keyframe-safe-durable-segments.patch",
                "package/prudynt-t/0023-ipc-add-bounded-versioned-JSON-framing.patch",
                "package/prudynt-t/0024-jpeg-recover-failed-starts-and-isolate-snapshot-files.patch",
                "package/prudynt-t/0025-rtsp-bound-socket-and-audio-queues-by-bytes-and-elements.patch",
                "package/prudynt-t/0026-motion-clear-active-marker-before-thread-exit.patch",
                "package/prudynt-t/0027-jpeg-expose-per-channel-lifecycle-metrics.patch",
                "package/prudynt-t/0028-privacy-ack-and-recorder-segment-timestamps.patch",
                "package/prudynt-t/0029-privacy-and-recorder-failure-transactions.patch",
                "package/prudynt-t/0030-osd-centralize-group-lifecycle.patch",
                "package/prudynt-t/0031-video-ready-startup-barrier.patch",
                "package/prudynt-t/0032-recorder-reject-volatile-and-root-mounts.patch",
                "package/prudynt-t/0033-recorder-close-segments-before-process-exit.patch",
                "package/prudynt-t/0034-dlink-disable-unsupported-defog.patch",
                "package/prudynt-t/0035-release-error-handling-and-imp-init-rollback.patch",
                "package/prudynt-t/0036-explicit-release-debug-logging.patch",
                "package/prudynt-t/0037-recorder-direct-framed-ipc.patch",
                "package/prudynt-t/0038-lifecycle-preserve-retry-safe-teardown-state.patch",
                "package/prudynt-t/0039-recorder-include-framed-control-socket-declaration.patch",
                "package/prudynt-t/0040-logging-use-the-declared-critical-level.patch",
                "package/prudynt-t/0041-media-retire-ready-marker-across-process-restart.patch",
                "package/prudynt-t/0042-motion-snapshot-config-before-ivs-restart.patch",
                "package/prudynt-t/0043-worker-startup-and-join-ownership.patch",
                "package/prudynt-t/0044-bound-repeated-worker-error-logs.patch",
                "package/prudynt-t/0045-refuse-motion-init-with-stale-ivs-state.patch",
                "package/prudynt-t/0046-jpeg-publish-and-send-complete-frames.patch",
                "package/prudynt-t/0047-media-publish-stream0-to-raptor-ring.patch",
                "package/prudynt-t/0048-dlink-preserve-encoder-fps-across-daynight.patch",
                "dcs6100-webui/src/app/fullscreen-preview.ts",
                "package/thingino-uhttpd/0007-proxy-thingino-control.patch",
                "package/thingino-uhttpd/0008-disable-cgi-and-proxy-onvif.patch",
                "package/thingino-uhttpd/0009-remove-request-script-runtime.patch",
                "package/thingino-uhttpd/0010-defer-control-proxy-pump.patch",
                "package/thingino-uhttpd/0011-account-tls-socket-backpressure.patch",
                "package/thingino-uhttpd/0012-keep-mjpeg-encoder-profile-static.patch",
                "package/thingino-uhttpd/0013-never-cache-static-html-shell.patch",
                "package/thingino-uhttpd/0014-allow-prometheus-control-responses.patch",
            }
            <= installed_destinations
        )

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
                "package/prudynt-t/prudynt-t.mk",
                "package/thingino-kopt/thingino-kopt.mk",
            ],
        )
        for forbidden in (
            "package/thingino-agent/",
            "package/thingino-daynightd/",
            "incremental-package-",
            "DCS_DLINK_MINIMAL_PIPELINE",
            "DCS_DLINK_SKIP_HARDWARE_OSD",
        ):
            self.assertNotIn(forbidden, thingino)
        for required in (
            "SOURCE_DATE_EPOCH",
            "DCS_DLINK_OS02G10_IQ",
            "DCS_DLINK_MEDIA_READY",
            "DCS_DLINK_SKIP_ISP_BYPASS",
            "THINGINO_MMC0_MAX_FREQ := 24000000",
        ):
            self.assertIn(required, thingino)

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
                "package/prudynt-t/Config.in",
                "package/prudynt-t/prudynt-t.mk",
            ],
        )
        self.assertIn("IMP_SDK_VERSION := 1.1.4", imp114)
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
            "package/prudynt-t/files/S31prudynt",
        ):
            self.assertIn(required, daynight_control)
        self.assertNotIn("-THINGINO_DAYNIGHTD_DEPENDENCIES", daynight_control)
        daynight_removed = "\n".join(
            line[1:]
            for line in daynight_control.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        self.assertIn('API_URL="http://127.0.0.1:8080/api/v1/config"', daynight_removed)
        self.assertIn('if curl -s "$API_URL"', daynight_removed)
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

        prudynt_media = (
            ROOT / "patches/prudynt/0002-dlink-media-baseline.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'path == "/snapshot"',
            'qs == "ch=0"',
            'qs == "ch=1"',
            "get_snapshot_ch_local_http(snapshot_ch, image)",
            "INADDR_LOOPBACK",
            'Content-Type: image/jpeg\\r\\nContent-Disposition: ',
            "SO_SNDTIMEO",
        ):
            self.assertIn(required, prudynt_media)
        self.assertNotIn("/tmp/snapshot", prudynt_media)

        prudynt_shared = (
            ROOT / "patches/prudynt/0003-rtsp-share-immutable-video-payloads.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("class SharedPayload", prudynt_shared)
        self.assertIn("std::shared_ptr<const Storage>", prudynt_shared)
        self.assertIn("nalu.data = shared_payload", prudynt_shared)
        self.assertNotIn("\n+  NaluPool naluPool", prudynt_shared)

        prudynt_queues = (
            ROOT / "patches/prudynt/0004-rtsp-bound-slow-client-video-queues.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "RTSP_VIDEO_TAP_MAX_BYTES = 512 * 1024",
            "FrameRecoveryGate",
            "queued_bytes",
            "eviction_count",
            "waiting_for_random_access_",
            "sendQueueBytes -= static_cast<size_t>(n)",
        ):
            self.assertIn(required, prudynt_queues)
        self.assertIn("if (!backpressure && s->videoTap)", prudynt_queues)

        prudynt_jpeg_idle = (
            ROOT / "patches/prudynt/0005-jpeg-preserve-zero-idle-frame-rate.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("jpegIdleFpsPolicy", prudynt_jpeg_idle)
        self.assertIn("jpeg_refresh > 0 ? 1", prudynt_jpeg_idle)
        self.assertNotIn("StopRecvPic", prudynt_jpeg_idle)
        self.assertNotIn("DestroyChn", prudynt_jpeg_idle)

        prudynt_queue_tests = (
            ROOT
            / "patches/prudynt/0006-test-cover-queue-element-and-client-isolation-limits.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("test_exact_element_boundary", prudynt_queue_tests)
        self.assertIn("test_slow_client_does_not_block_fast_client", prudynt_queue_tests)

        prudynt_jpeg_lifecycle = (
            ROOT
            / "patches/prudynt/0007-jpeg-synchronize-video-demand-lifecycle.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("std::atomic<bool> run_for_jpeg", prudynt_jpeg_lifecycle)
        self.assertIn("std::memory_order_release", prudynt_jpeg_lifecycle)
        self.assertIn("std::memory_order_acquire", prudynt_jpeg_lifecycle)
        self.assertNotIn("IMP_Encoder_StopRecvPic", prudynt_jpeg_lifecycle)

        prudynt_jpeg_deadline = (
            ROOT
            / "patches/prudynt/0008-jpeg-expire-source-demand-after-disconnect.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("subscriber_deadline_ms", prudynt_jpeg_deadline)
        self.assertIn("jpegRequestDeadlineActive", prudynt_jpeg_deadline)
        self.assertIn("jpegSourceDemandActive", prudynt_jpeg_deadline)
        self.assertNotIn("std::atomic<int64_t>", prudynt_jpeg_deadline)

        prudynt_jpeg_standby = (
            ROOT
            / "patches/prudynt/0009-jpeg-apply-expiry-to-the-video-standby-gate.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("jpegVideoDemandActive", prudynt_jpeg_standby)
        self.assertIn("!run_for_jpeg", prudynt_jpeg_standby)
        self.assertIn("!jpeg_video_demand_active()", prudynt_jpeg_standby)
        self.assertNotIn("IMP_Encoder_StopRecvPic", prudynt_jpeg_standby)

        prudynt_receive_lifecycle = (
            ROOT
            / "patches/prudynt/0010-media-bound-JPEG-demand-and-receive-lifecycle.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "EncoderReceiveLifecycle",
            "socketPeerClosed",
            "mjpegSharedEncoderMutationRequested",
            "IMP_Encoder_StopRecvPic",
            "IMP_Encoder_StartRecvPic",
        ):
            self.assertIn(required, prudynt_receive_lifecycle)

        prudynt_http_ingress = (
            ROOT
            / "patches/prudynt/0011-http-keep-shared-JPEG-static-and-loopback-only.patch"
        ).read_text(encoding="utf-8")
        prudynt_http_added = "\n".join(
            line[1:]
            for line in prudynt_http_ingress.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for required in (
            "http.loopback_only",
            '"loopback_only": false',
            "httpBindHostAddress(loopback_only_)",
            "per-request JPEG quality and size are unsupported",
            "MSG_NOSIGNAL",
        ):
            self.assertIn(required, prudynt_http_added)
        self.assertNotIn("global_jpeg[ch]->quality_override = q", prudynt_http_added)

        prudynt_dlink_restart = (
            ROOT
            / "patches/prudynt/0012-media-replace-unsafe-dlink-video-restarts.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "DLinkMediaProcessAction",
            "FD_CLOEXEC",
            'execv("/proc/self/exe"',
            "normalizeVideoFps",
            "restart_video || restart_audio",
            "global_restart_audio",
            "D-Link shutdown: exiting without unsafe SDK teardown",
        ):
            self.assertIn(required, prudynt_dlink_restart)
        self.assertNotIn("fork(", prudynt_dlink_restart)
        self.assertNotIn("system(", prudynt_dlink_restart)

        prudynt_live_controls = (
            ROOT
            / "patches/prudynt/0014-media-keep-dlink-live-controls-in-process.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "DLinkLiveControlPolicy",
            "dlinkAudioWorkerRequired",
            "dlinkLiveAudioRestartRequired",
            "AudioOutputWorker::applyMute(!enabled)",
            "global_restart_motion",
            "dlinkMotionVideoRestartRequired",
        ):
            self.assertIn(required, prudynt_live_controls)
        self.assertNotIn("fork(", prudynt_live_controls)
        self.assertNotIn("system(", prudynt_live_controls)

        prudynt_timezone = (
            ROOT / "patches/prudynt/0015-reload-camera-timezone.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'std::fopen("/etc/TZ", "r")',
            "strcspn(value, \"\\r\\n\")",
            "reload_dlink_timezone()",
            "dlink_timezone_environment",
            'std::strncmp(*entry, "TZ=", 3)',
            'environment.emplace_back("TZ=" + timezone)',
            'execve("/proc/self/exe"',
        ):
            self.assertIn(required, prudynt_timezone)
        prudynt_timezone_added = "\n".join(
            line[1:]
            for line in prudynt_timezone.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("src/OSD.cpp", prudynt_timezone)
        self.assertNotIn("setenv(", prudynt_timezone_added)
        self.assertNotIn("unsetenv(", prudynt_timezone_added)
        self.assertNotIn("tzset(", prudynt_timezone_added)
        self.assertNotIn('execv("/proc/self/exe"', prudynt_timezone_added)
        self.assertNotIn("fork(", prudynt_timezone_added)
        self.assertNotIn("system(", prudynt_timezone_added)
        self.assertNotIn("popen(", prudynt_timezone_added)

        prudynt_osd_scale = (
            ROOT / "patches/prudynt/0016-scale-burnin-osd-per-stream.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "automaticBurninScale",
            "kAutoScaleWidth = 480",
            "streamWidth) + kAutoScaleWidth - 1",
            "std::clamp(roundedUp, 1, kBurninMaxScale)",
            "new_scale = automaticBurninScale(stream_width)",
            "ts_scale_ = automaticBurninScale(stream_width)",
        ):
            self.assertIn(required, prudynt_osd_scale)

        prudynt_motion = (
            ROOT
            / "patches/prudynt/0017-motion-add-nonblocking-Control-observation-output.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC",
            "errno == EINVAL || errno == EPROTONOSUPPORT",
            "::socket(AF_UNIX, SOCK_DGRAM, 0)",
            "::fcntl(socketFd_, F_GETFD, 0)",
            "descriptorFlags | FD_CLOEXEC",
            "MSG_DONTWAIT",
            r'\"event\":\"motion\"',
            'return "suppressed"',
            "initialGrace_",
            "motion event output tests passed",
        ):
            self.assertIn(required, prudynt_motion)
        prudynt_motion_removal = (
            ROOT / "patches/prudynt/0018-motion-remove-legacy-shell-event-path.patch"
        ).read_text(encoding="utf-8")
        prudynt_motion_added = "\n".join(
            line[1:]
            for line in prudynt_motion_removal.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("system(", prudynt_motion_added)
        self.assertNotIn("popen(", prudynt_motion_added)
        self.assertNotIn("fork(", prudynt_motion_added)
        self.assertIn("-              ret = system(cmd);", prudynt_motion_removal)
        self.assertIn("-        ret = system(cmd);", prudynt_motion_removal)
        prudynt_motion_restart = (
            ROOT
            / "patches/prudynt/0019-motion-clear-stale-state-on-process-start.patch"
        ).read_text(encoding="utf-8")
        for required in (
            '@@ -88,7 +88,15 @@ constexpr const char *kPrudyntRunDir',
            'kPrudyntMotionStatePath = "/run/prudynt/motion.active"',
            "clear_stale_motion_state();",
            "errno != ENOENT",
        ):
            self.assertIn(required, prudynt_motion_restart)

        prudynt_motion_stop = (
            ROOT
            / "patches/prudynt/0026-motion-clear-active-marker-before-thread-exit.patch"
        ).read_text(encoding="utf-8")
        prudynt_motion_stop_result = [
            line[1:]
            for line in prudynt_motion_stop.splitlines()
            if (line.startswith(" ") or line.startswith("+"))
            and not line.startswith("+++")
        ]
        self.assertLess(
            prudynt_motion_stop_result.index("  remove_motion_detection_state_file();"),
            prudynt_motion_stop_result.index("  exit();"),
        )

        prudynt_config_reload = (
            ROOT / "patches/prudynt/0020-config-reload-after-complete-write.patch"
        ).read_text(encoding="utf-8")
        prudynt_config_reload_added = "\n".join(
            line[1:]
            for line in prudynt_config_reload.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        for required in (
            "inotify_add_watch(inotifyFd, cfg->filePath.c_str(), IN_CLOSE_WRITE)",
            "if (event->mask & IN_CLOSE_WRITE)",
            "truncate/write intermediate state",
        ):
            self.assertIn(required, prudynt_config_reload_added)
        self.assertNotIn("IN_MODIFY", prudynt_config_reload_added)
        self.assertNotIn("IN_ALL_EVENTS", prudynt_config_reload_added)

        prudynt_media_metrics = (
            ROOT
            / "patches/prudynt/0021-media-stabilize-IPC-clients-and-expose-queue-metrics.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "ClientCounterLease::acquire(",
            "prudynt_rtsp_queue_bytes",
            "prudynt_rtsp_queue_drops_total",
            "prudynt_rtsp_recoveries_total",
            "prudynt_ipc_mjpeg_clients",
        ):
            self.assertIn(required, prudynt_media_metrics)

        prudynt_recorder_safety = (
            ROOT
            / "patches/prudynt/0022-recorder-require-keyframe-safe-durable-segments.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "firstKeyframeIndex(prebuffer_frames)",
            "firstKeyframeIndex(pending_frames_during_flush)",
            "bool writeFully(",
            "O_NOFOLLOW",
            "::fsync(fd_)",
        ):
            self.assertIn(required, prudynt_recorder_safety)

        prudynt_recorder_mount_policy = (
            ROOT
            / "patches/prudynt/0032-recorder-reject-volatile-and-root-mounts.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'recorderMountAllowed("/mnt/sdcard", "vfat", true)',
            'recorderMountAllowed("/run", "tmpfs", true)',
            'recorderMountAllowed("/mnt/ram", "tmpfs", true)',
            'recorderMountAllowed("/mnt/thingino", "jffs2", true)',
            "recorderMountFilesystemAllowed(entry.fsType)",
        ):
            self.assertIn(required, prudynt_recorder_mount_policy)

        prudynt_recorder_teardown = (
            ROOT
            / "patches/prudynt/0033-recorder-close-segments-before-process-exit.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "void MP4ControlSocket::stopAll()",
            "if (!write_channel_state_file(channel, path, duration_seconds))",
            "O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC | O_NOFOLLOW",
            "::rename(temporary_path.c_str(), state_path.c_str())",
            "MP4ControlSocket::stopAll();",
        ):
            self.assertIn(required, prudynt_recorder_teardown)

        prudynt_dlink_isp_capability = (
            ROOT / "patches/prudynt/0034-dlink-disable-unsupported-defog.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "DCS_DLINK_OS02G10_IQ",
            "supportsDefog(true, true)",
            ".has_isp_defog = DLinkISPCapabilityPolicy::supportsDefog(",
            "D-Link ISP capability policy tests passed",
        ):
            self.assertIn(required, prudynt_dlink_isp_capability)

        prudynt_release_error_handling = (
            ROOT
            / "patches/prudynt/0035-release-error-handling-and-imp-init-rollback.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "Error handling must never disappear from a production build",
            "return log_condition_",
            "class IMPSystemLifecycle",
            "lifecycle_.teardown",
            "refusing to start media workers",
            "test_release_error_handling.cpp",
        ):
            self.assertIn(required, prudynt_release_error_handling)

        prudynt_release_debug = (
            ROOT / "patches/prudynt/0036-explicit-release-debug-logging.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "ENABLE_LOG_DEBUG is supplied by build.sh",
            "PRUDYNT_LOGGER_STANDALONE",
            "-DENABLE_LOG_DEBUG",
        ):
            self.assertIn(required, prudynt_release_debug)

        prudynt_recorder_ipc = (
            ROOT / "patches/prudynt/0037-recorder-direct-framed-ipc.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "MP4ControlSocket::startLoop(channel, duration_seconds)",
            "bool MP4ControlSocket::stop(int channel)",
            "request_ok = handle_mp4(v, out, sep) && request_ok",
            '! grep -q \'open("/run/prudynt/mp4ctl"\'',
        ):
            self.assertIn(required, prudynt_recorder_ipc)

        prudynt_retry_safe_teardown = (
            ROOT
            / "patches/prudynt/0038-lifecycle-preserve-retry-safe-teardown-state.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "class MotionLifecycle",
            "Keep this stage and all of its prerequisites active",
            "Motion cleanup failed; retrying while dependencies are intact",
            "IMPSystem cleanup failed; retrying while dependencies are intact",
            "IMP_IVS_CreateMoveInterface() returned null",
        ):
            self.assertIn(required, prudynt_retry_safe_teardown)

        prudynt_recorder_ipc_compile = (
            ROOT
            / "patches/prudynt/0039-recorder-include-framed-control-socket-declaration.patch"
        ).read_text(encoding="utf-8")
        self.assertIn('#include "MP4ControlSocket.hpp"', prudynt_recorder_ipc_compile)

        prudynt_critical_logging = (
            ROOT / "patches/prudynt/0040-logging-use-the-declared-critical-level.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "Logger::CRIT",
            'LOG_CRIT("critical startup failure")',
        ):
            self.assertIn(required, prudynt_critical_logging)

        prudynt_media_ready_lifecycle = (
            ROOT
            / "patches/prudynt/0041-media-retire-ready-marker-across-process-restart.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "clear_stale_dlink_media_ready();",
            "unlink(kDLinkMediaReadyPath)",
            "process_action != DLinkMediaProcessAction::Continue",
        ):
            self.assertIn(required, prudynt_media_ready_lifecycle)

        prudynt_motion_snapshot = (
            ROOT
            / "patches/prudynt/0042-motion-snapshot-config-before-ivs-restart.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "MotionRuntimeConfig CFG::motionRuntimeConfig() const",
            "normalizeMotionGeometry",
            "runtime_config_ = cfg->motionRuntimeConfig();",
            "test_motion_geometry.cpp",
            "partial_reload{1920, 0",
        ):
            self.assertIn(required, prudynt_motion_snapshot)

        prudynt_thread_ownership = (
            ROOT
            / "patches/prudynt/0043-worker-startup-and-join-ownership.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "reported.compare_exchange_strong",
            "if (ret == 0)",
            "audio_thread_joinable = true",
            "motion_thread_joinable = ret == 0",
            "rtsp_thread_joinable = ret == 0",
            "if (motion_thread_joinable)",
            "if (rtsp_thread_joinable)",
            "BackchannelWorker thread caught exception",
        ):
            self.assertIn(required, prudynt_thread_ownership)

        prudynt_bounded_error_logs = (
            ROOT / "patches/prudynt/0044-bound-repeated-worker-error-logs.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "class RepeatedFailureGate",
            "FirstFailure",
            "Recovered",
            "std::numeric_limits<uint16_t>::max()",
            "test_repeated_failure_gate.cpp",
            "IMP_IVS_PollingResult recovered after",
            "std::chrono::milliseconds(10)",
            "IPC: accept recovered after",
        ):
            self.assertIn(required, prudynt_bounded_error_logs)

        prudynt_motion_reinit_guard = (
            ROOT
            / "patches/prudynt/0045-refuse-motion-init-with-stale-ivs-state.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "if (!lifecycle_.empty())",
            "const int cleanup_ret = exit()",
            "cleanup_ret != 0 || !lifecycle_.empty()",
            "Motion initialization refused while prior IVS resources",
        ):
            self.assertIn(required, prudynt_motion_reinit_guard)

        prudynt_complete_jpeg = (
            ROOT
            / "patches/prudynt/0046-jpeg-publish-and-send-complete-frames.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "jpegFrameMatches",
            "prudynt_jpeg_invalid_frames_total",
            "write_full(cfd, img.data(), img.size())",
            "test_jpeg_frame_policy_rejects_partial_and_wrong_frames",
            "-bool write_chunked_paced",
        ):
            self.assertIn(required, prudynt_complete_jpeg)

        prudynt_raptor_ring = (
            ROOT
            / "patches/prudynt/0047-media-publish-stream0-to-raptor-ring.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'const char *ring_name = channel == 0 ? "main" : "sub"',
            "channel == 0 ? 2U * 1024U * 1024U : 512U * 1024U",
            "demand.external = raptor_ring.hasReaders()",
            "IMP_Encoder_RequestIDR(encChn)",
            "raptor_ring.publish(raptor_frame.data()",
            "PRUDYNT_RAPTOR_IPC_LIBRARY",
        ):
            self.assertIn(required, prudynt_raptor_ring)

        prudynt_daynight_fps = (
            ROOT
            / "patches/prudynt/0048-dlink-preserve-encoder-fps-across-daynight.patch"
        ).read_text(encoding="utf-8")
        dlink_guard = prudynt_daynight_fps.index(
            "+#if !defined(DCS_DLINK_MEDIA_READY)"
        )
        generic_fps_change = prudynt_daynight_fps.index(
            "hal::encoder::set_framerate(ch, effective_fps, 1);", dlink_guard
        )
        guard_end = prudynt_daynight_fps.index("+#endif", generic_fps_change)
        self.assertLess(dlink_guard, generic_fps_change)
        self.assertLess(generic_fps_change, guard_end)

        prudynt_ipc_frame = (
            ROOT / "patches/prudynt/0023-ipc-add-bounded-versioned-JSON-framing.patch"
        ).read_text(encoding="utf-8")
        for required in (
            'prefix = "PRUDYNT/1 JSON "',
            "kMaxRequestBytes = 16 * 1024",
            '"ERR request_too_large\\n"',
            "decodeFramedJson(req, kMaxRequestBytes)",
        ):
            self.assertIn(required, prudynt_ipc_frame)

        prudynt_jpeg_recovery = (
            ROOT
            / "patches/prudynt/0024-jpeg-recover-failed-starts-and-isolate-snapshot-files.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "sh.success.load(std::memory_order_acquire)",
            "imp_encoder->deinit(false)",
            '".tmp.ch"',
            "O_NOFOLLOW",
        ):
            self.assertIn(required, prudynt_jpeg_recovery)

        prudynt_rtsp_queue = (
            ROOT
            / "patches/prudynt/0025-rtsp-bound-socket-and-audio-queues-by-bytes-and-elements.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "kRtspSocketQueueMaxElements = 1024",
            "kRtspSocketQueueMaxBytes = 1024 * 1024",
            "kRtspAudioTapMaxBytes = 256 * 1024",
            "rtspSocketQueueCanAppend(",
        ):
            self.assertIn(required, prudynt_rtsp_queue)

        prudynt_jpeg_metrics = (
            ROOT
            / "patches/prudynt/0027-jpeg-expose-per-channel-lifecycle-metrics.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "prudynt_jpeg_worker_running",
            "prudynt_jpeg_receiving",
            "prudynt_jpeg_receive_start_failures_total",
            "prudynt_jpeg_receive_stop_failures_total",
            "prudynt_jpeg_source_timeouts_total",
            "test_media_metrics_track_jpeg_lifecycle_per_channel",
        ):
            self.assertIn(required, prudynt_jpeg_metrics)

        startup_timezone = (
            ROOT / "patches/thingino/0017-pass-camera-timezone-to-prudynt.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "load_timezone()",
            "IFS= read -r TZ_VALUE < /etc/TZ",
            "export TZ",
            "unset TZ",
            "load_timezone",
            "start_daemon",
        ):
            self.assertIn(required, startup_timezone)
        startup_timezone_added = "\n".join(
            line[1:]
            for line in startup_timezone.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn("curl", startup_timezone_added)
        self.assertNotIn("cat /etc/TZ", startup_timezone_added)

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

        prudynt_http_clients = (
            ROOT / "patches/prudynt/0013-http-bound-mjpeg-client-threads.patch"
        ).read_text(encoding="utf-8")
        for required in (
            "HTTPClientAdmission",
            "256U * 1024U",
            "PTHREAD_CREATE_DETACHED",
            "pthread_create failed",
            "client handler failed",
        ):
            self.assertIn(required, prudynt_http_clients)
        prudynt_http_client_added = "\n".join(
            line[1:]
            for line in prudynt_http_clients.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        self.assertNotIn(
            "std::thread(&HTTPMJPEG::handle_client", prudynt_http_client_added
        )

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
            "X-Thingino-Proxy: 2",
            "control_proxy_is_media_url(url)",
            "uh_dispatch_add(&control_proxy_dispatch)",
            "CGI execution is disabled",
            "request-process relay is excluded",
        ):
            self.assertIn(required, no_cgi_proxy)
        self.assertNotIn("+\tpid = fork();", no_cgi_proxy)
        self.assertNotIn("+\t\t\texecl(", no_cgi_proxy)
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
            '"${TARGET_DIR}/var/www/assets/app.js"',
            '"${TARGET_DIR}/var/www/assets/app.css"',
            '"${TARGET_DIR}/var/www/index.html"',
            '"${TARGET_DIR}/var/www/manifest.webmanifest"',
            '"${TARGET_DIR}/etc/init.d/S48webui-config"',
            '"${TARGET_DIR}/usr/sbin/mqtt-sub-dispatcher"',
        ):
            self.assertIn(required, static_webui)
        container_build = (ROOT / "scripts/container_build_thingino.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('GIT_AUTHOR_DATE="@$source_epoch +0000"', container_build)
        self.assertIn('GIT_COMMITTER_DATE="@$source_epoch +0000"', container_build)
        self.assertIn("symbolic-ref HEAD refs/heads/master", container_build)
        for required in (
            'make -C "$source_dir" CAMERA="$profile" GROUP=exp \\\n\t\tbuild',
            "error while loading shared libraries:",
            "Buildroot host tool failed to load a shared library",
            'find "$output_dir/target" -print0 | tr -cd',
            'find "$output_dir/target/usr/lib/modules"',
            "DCS6100_RUST_TOOLCHAIN_DIR=$rust_toolchain",
            "DCS6100_RUST_SOURCE_DIR=$rust_source",
            "DCS6100_INGENIC_TOOLCHAIN_DIR=$ingenic_toolchain",
            "DCS6100_AUDIOPROCESS_LINK_FILE=$audio_link",
            "/opt/dcs6100-webui",
            "--entry-names=app",
            "--tsconfig=",
            "--target=es2020",
            "5c5c9b40289f0f52cb9bab571c41519cf3c6e8a82d06bd66ae3852be96d5ea9e",
            "7180124793f453ef542d654c0b8e8899fe05e7754349bf9870face07985f3798",
            "be4dc858466807072af04b972470e504938a577cd77aa12177dd8c0156480bb9",
            'mipsel-linux-objcopy"',
            "--remove-section=.comment",
            'cmp "$prudynt_normalized" "$prudynt_merged"',
            "per-request JPEG quality and size are unsupported",
            "http.loopback_only",
            "f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273",
            "40fd7eb9237772f705a92e9792325f07f0fe022479923f8dc67653cc11450ea1",
            "befca6166d2e25b749cc9fff4798332f42a2d997b00d4aee713358d803353b79",
            'test -x "$output_dir/target/usr/sbin/thingino-controld"',
            'test -x "$output_dir/target/etc/init.d/S95thingino-control"',
            'test -x "$output_dir/target/sbin/mkfs.vfat"',
            'test ! -e "$output_dir/target/usr/sbin/formatsd"',
            'test ! -e "$output_dir/target/usr/sbin/envfromcard"',
            "d8da684a19b1eac7b5f69844495cfd1c4d04f28db792b9eb605b6deb3978f271",
            'test -x "$output_dir/target/etc/init.d/S06ircut"',
            'test -x "$output_dir/target/etc/init.d/S31prudynt"',
            "491761453de5c8dc9eee17d1cf5775530f683ab5df4ca8894bd910882c51a861",
            'timezone_catalog=$output_dir/target/usr/share/tz.json',
            '"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"',
            "grep -aFq '/etc/TZ'",
            "IFS= read -r TZ_VALUE < /etc/TZ",
            "grep -c 'usleep 100000'",
            "rm -f /run/transfer.bin /run/transfer.footer",
            "grep -E 'curl|thingino-api\\.key|API_URL'",
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
            '"total_size": 188716',
            "x86_64 Thingino firmware build is unavailable",
            "thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz",
            "9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412",
        ):
            self.assertIn(required, container_build)
        self.assertNotIn("LD_LIBRARY_PATH=$output_dir/host", container_build)
        self.assertIn("PRUDYNT_IMP_LANG := LIBIMP_LANG=zh", imp114)
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

        media = (
            ROOT / "patches/prudynt/0002-dlink-media-baseline.patch"
        ).read_text(encoding="utf-8")
        media_paths = [
            line.split(" b/", 1)[1]
            for line in media.splitlines()
            if line.startswith("diff --git a/")
        ]
        self.assertEqual(
            media_paths,
            [
                "src/HTTPMJPEG.cpp",
                "src/Config.cpp",
                "src/IMPSystem.cpp",
                "src/main.cpp",
                "src/VideoWorker.cpp",
            ],
        )
        self.assertNotIn("src/simple-rtsp/", media)
        self.assertNotIn("X-DCS-RTSP-Stats", media)
        self.assertIn("1080p-started", media)
        self.assertIn("DCS_DLINK_ALLOW_UNSYNCED_STARTUP", media)
        self.assertIn("continuing before wall-clock", media)
        self.assertIn('line == "unknown"', media)
        self.assertNotIn("DCS_DLINK_MINIMAL_PIPELINE", media)
        self.assertNotIn("DCS_DLINK_SKIP_HARDWARE_OSD", media)

        auth = (
            ROOT / "patches/prudynt/0001-enforce-rtsp-basic-auth.patch"
        ).read_text(encoding="utf-8")
        self.assertEqual(auth.count("diff --git a/src/simple-rtsp/"), 2)
        self.assertIn("WWW-Authenticate: Basic", auth)
        self.assertNotIn("X-DCS-RTSP-Stats", auth)

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
