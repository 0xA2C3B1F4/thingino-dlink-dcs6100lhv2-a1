# September 24 corrected Raptor candidate on the first A1 camera

This record applies to source `aff74396532c133805e7f08549fc7bf6ba80d60b`
and the signed universal bundle SHA-256
`d97e3b8857cbfe167411fefb90fb3015476bbbd425824ec0871ecb5855ff3cc1`.
The target was the first DCS-6100LHV2 A1 camera. The [build record](2026-09-23-raptor-mjpeg-correction-build.md)
covers the two clean builds and the MJPEG query correction in these bytes.

The stock updater's first card boot was not captured over UART. A subsequent
boot without the SD card reached `STAGE1 booted`, `device_verified` and
`install_started`, then the expected `FAIL sd_partition_missing`. This showed
that the new installer kernel and bootstrap could boot; it did not capture the
stock updater's success line or prove byte-for-byte physical mtd1/mtd2 readback.

After the supported universal host handoff, the card boot reached
`stage2_snapshotted_and_verified`,
`camera_authorization_and_provisioning_snapshotted`,
`recovery_checkpoint_committed`, `stock_userdata_backup_committed` and
`final_write_phase`. Passive UART then recorded
`provisioning_data_written_and_verified`, `final_system_written`,
`final_kernel_tail_written`, `preactivation_kernel_verified`,
`activation_written_and_verified`, `camera_install_files_passivated`,
`sd_unmounted` and `rebooting_final`. The next boot reached
`thingino_verified_switch_root`.

The supported universal verifier returned
`camera-bound-universal-management-verified` with its media gate passed and
logical mtd3 SHA-256
`8c380cb6813f219dae739231de1b03f857adf98d38d329b990a64aaf3db41d2a`.
The separate selected-partition verifier returned
`camera-bound-split-layout-readback-verified`. These checks establish the
specified camera-bound layout and selected readback. They do not establish
whole-flash equality; mutable data and exact physical mtd2 comparison remain
outside this record. Detailed camera-bound receipts and UART output remain in
private evidence.

At one paired read-only probe, the host UTC clock was
`2026-09-24T06:29:31Z`, while the camera's HTTPS Date header was
`Thu, 06 Aug 2026 09:02:59 GMT`. Time synchronization and timestamp
correctness remain unaccepted. No NTP setting was changed for this probe.

In authenticated Chromium 147.0.7691.0, Preview first reached Live WebRTC on
Main stream with the microphone Off. With the microphone still Off, the operator
switched to Substream and waited for Live WebRTC. Turning the microphone On and
pressing Listen once made the UI show "Audio playing"; the operator confirmed
that camera audio was immediately audible. The operator then turned the
microphone Off, switched back to Main stream, waited for Live WebRTC, turned the
microphone On and pressed Listen once. The UI again showed "Audio playing", and
the operator confirmed immediate audible camera audio. Neither stream needed
Preview Reload or a second Listen click. These tests followed the installation
boot.

A separate authenticated Chromium tab opened each direct MJPEG route,
`/media/v1/mjpeg?stream=0&q=51` and `/media/v1/mjpeg?stream=1&q=51`.
Both rendered an image initially and after that browser tab's Reload.

A second Preview tab temporarily blocked only the camera's
`/api/v1/media/webrtc/whip*` requests in Chromium DevTools. The Main stream
WHIP request was blocked, and Preview reached Live MJPEG with Listen disabled.
Preview's own Reload went through Connecting and returned to Live MJPEG, rather
than Offline. Switching to Substream reached Live MJPEG with an HTTP 200
`stream=1&q=52` request. Preview Reload again reached Live MJPEG, with an
HTTP 200 `stream=1&q=53` request. The DevTools block was then removed and the
test tab closed. The original Main stream resumed Live WebRTC with Listen On
and "Audio playing".

The operator later reported a separate power cycle of the first camera with
the SD card installed. A new passive UART capture recorded `STAGE1 booted`,
`device_verified`, `thingino_verified_switch_root`, local IPv4 network readiness,
uhttpd startup and a login prompt. Its SHA-256 is
`39e878d9d4e945b40cc8926e33e0f30b900253d628f2898a59adcccd9acd028d`;
the capture remains in private evidence. Fresh HTTPS returned HTTP 200. The
previous browser session had expired across the reboot, and the operator signed
in again. Chromium 147 Preview reached Live WebRTC on Main stream. With the
microphone Off, the operator turned it On and pressed Listen once without
Reload. The UI showed "Audio playing", and the operator reported immediate
audible camera audio. After turning the microphone Off, the operator switched
to Substream and waited for stable Live WebRTC. Turning the microphone On and
pressing Listen once, again without Reload, showed "Audio playing"; the
operator again reported immediate audible camera audio. No new selected-partition
readback followed this power cycle, and this test does not establish clock
correctness.

The forced-WHIP test covers the browser fallback and retry path that failed on
the preceding candidate, with WHIP deliberately blocked here. It does not cover
every real network failure, MJPEG frame quality, or the full candidate matrix.
The public firmware-release decision remains open. The firmware-release ledger
still has 2 of 9 gates closed; this result does not authorize binary
distribution.
