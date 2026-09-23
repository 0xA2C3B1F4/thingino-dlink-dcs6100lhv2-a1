# Features

The table separates implemented features from their device validation.
The September 6 baseline passed main/substream WebRTC, Motion, Privacy,
Day/Night and Information checks. Those results do not validate later full
Raptor source changes. The full-Raptor profile owns the sensor, encoder, audio,
RTSP and recording services. See
[historical acceptance](status.md#historical-tested-installation) for measured results and limits.

The default installation is local-network-only: DHCP supplies the camera address
and subnet, but not a default route or DNS resolver. The inherited Internet NTP
hostname therefore cannot synchronize the clock in this profile. In Settings >
Time, configure the numeric address of an NTP server reachable on the camera's
directly connected subnet, save it, then use Sync time now and check the returned
camera time. Do not assume that the router provides NTP, or that a running NTP
process means the clock is synchronized. Timezone selection does not set the
date. Until synchronization is verified, timestamps and time-based schedules
must be treated as unverified. Do not add Internet routing merely to bypass
this local-network policy.

| Feature | Source state | Validation boundary |
| --- | --- | --- |
| H.264 stream0 and stream1 | Raptor RVD | Both ran at the selected 15 fps on the September 6 baseline |
| Stream settings | Raptor has checked FPS, GOP, codec, H.264 profile, bitrate-mode and target-bitrate operations. Codec and profile changes restart the stream. H.264 profiles are Baseline, Main and High. RTSP path changes use the separate access service and preserve viewer credentials and port. A single form save preserves all edited fields and stops on an unconfirmed operation. | Host tests cover sequencing, SDK/configuration readback and persistence failures. Browser tests cover profile save/reload, H.265 profile exclusion, simultaneous codec/bitrate/GOP edits, RTSP path-only saves and explicit persistence retry after reload. Emitted codec/profile, RTSP client reconnection, H.265 client compatibility, bitrate and reboot persistence still need matching-firmware acceptance |
| WebRTC preview | Raptor RWD consumes the full-stack stream rings | Main/substream Preview passed on September 6; later full-Raptor acceptance is separate |
| MJPEG fallback | Implemented for both streams | Browser and camera checks passed; remains available if WebRTC is unavailable |
| Snapshots | Separate stream0 and stream1 JPEG paths | Both returned complete JPEG responses on the tested candidate |
| Image quality | Raptor supports brightness, contrast, saturation, sharpness, backlight compensation, dynamic range, highlight tone, defog, noise reduction, hue, defective-pixel correction, exposure compensation and horizontal/vertical flips. Thirteen controls use SDK readback; T31 noise reduction uses an acknowledged setter plus checked configuration because its SDK has no getter. White balance supports automatic, manual and preset modes, with retained manual red/blue gains. | Host tests cover range validation, startup restoration, failed setters and configuration persistence. Browser tests save and reload all fourteen scalar controls and switch white balance between manual and automatic/preset modes without losing manual gains. White balance requires matching SDK, disk and final live readback. Visual effects and reboot persistence need matching-firmware acceptance |
| RTSP | Authenticated Raptor RSD | H.264 main stream and concurrent preview passed on the baseline |
| ONVIF | Persistent loopback service behind uhttpd | The current candidate passed authenticated Media1/Media2 profile and URI reads plus HTTP Digest snapshots on both streams. Automatic WS-Discovery was not running. Manual endpoint use is tested; ONVIF profile conformance is not claimed. |
| Day and night | Auto, Day, Night, IR-cut and 850 nm IR. Raptor supports detector thresholds, a 50–10000 ms sampling interval and a 1–300 second transition delay for non-Photo detection. Fixed-time schedules use camera local time, allow midnight crossing and treat equal start/end as Day all day. Sunrise/sunset schedules use locally stored coordinates and separate minute offsets, with defined polar-day/night behavior. Choose one schedule; forced Day/Night overrides either. Photo detection has its own state machine. | Earlier physical transitions passed. New timing and schedules have host tests for elapsed time, clock changes, failed clock observations, manual override and save/reload. Solar tests also cover leap years, polar transitions, date boundaries and coordinate precision. Updated-firmware effects and reboot restoration still need camera acceptance |
| Audio | Raptor settings cover microphone/speaker enable, volume/gain, ALC, built codecs and built processing effects. The A1 profile uses an analog microphone and Raptor publishes mono audio. Queue and microphone-tap controls are not implemented by Raptor. | Host tests cover saved and live values, mute protection and partial-save retry. Browser tests cover speaker toggle, levels and unavailable controls. Updated-firmware sound, continuity and reboot persistence still require camera acceptance; WebRTC audio and talkback are separate |
| OSD and privacy | ROD rasterizes text and RVD uses hardware bitmap composition. Checked text settings include format, RGBA fill/outline/background colors, 16–48 pixel main-stream size and text visibility. Text visibility does not change Privacy. | September 12 full-Raptor checks confirmed visible main/substream OSD, format save/readback and selected Privacy controls. New size, visibility and RGBA controls have host coverage, including overlap-safe outline opacity and saved/live readback. Physical effects and reboot checks remain open |
| Motion | RVD supplies checked detector results to Control. Settings include skip count, debounce samples, initialization, cooldown, minimum event and post-event timing. Event storage adds a clip duration and starts only an idle recorder; it does not stop manually owned recordings. | On/off controls have camera evidence. New timing and storage settings have host and browser save/reload coverage. Their physical effects, emitted clips, reboot restore and ROI edges still require acceptance. The bounded built-in speaker alert has host coverage; other event destinations remain separate integrations |
| Home Assistant | Native MQTT worker in Control | Host broker and earlier live-device checks passed; not repeated in the September 6 acceptance run |
| Recording and timelapse | Per-stream RMR writers and native Control timelapse | Earlier start, stop, IDR-safe segment and SD-backed checks exist; current-candidate acceptance remains open |
| SD formatting | Confirmed partition-only FAT32 worker; recorder closure and maintenance acknowledgements, paused timelapse and checked restoration of prior recording | Host tests cover card/device revalidation, refused recorder pause, terminal worker refusal and missing completion. Physical formatting and recorder recovery remain unverified on the current candidate |
| Time and timezone | Shared native NTP sync; Raptor verifies timezone reload on every required daemon. Sync refreshes the displayed camera clock without discarding unsaved form edits. | Host failure/retry checks and browser clock-readback tests exist; updated firmware needs device acceptance |
| Network and remote logging | Save configuration, then restart the camera to apply network and logging changes. Saving does not restart these services. Local logs use the memory buffer, not a persistent log file. | Host and browser checks cover saved values and the apply instructions. Changed connectivity and log delivery require camera verification after restart |
| Management password and API key | Shared persistent authentication for both backends, with checked management-credential rollback | Host checks exist; password/API-key persistence tests on the updated camera remain pending |
| Sensor history | Bounded checked RVD exposure and RIC policy observations | Host decoder and missing-measurement checks exist; updated firmware needs device acceptance |
| Static WebUI | Framework-free TypeScript bundle with the classic layout | Host tests and selected controls in a real browser passed on September 6 |
| Persistent settings | Fixed JFFS2 data region and OverlayFS | Preserve update passed on one camera; corruption and interruption matrix remains open |
| UART-free install | Universal stage, stock updater, host handoff, and fixed Stage 1 | Clean source build and one installation passed without UART on September 6, using existing recovery inputs; broader physical acceptance remains open |

Saved stream width and height have a checked configuration-only operation.
The admitted main sizes are 1920 × 1080 and 1280 × 720; substream sizes are
640 × 360, 480 × 270 and 320 × 180, with substream dimensions no greater than
one third of main. The UI distinguishes saved and active dimensions and asks
for a full camera restart to apply changes. Native tests cover preserved live
geometry, Motion ROI handling and OSD canvas sizes. Physical restart and image
acceptance for these combinations remain open.

Substream enable is also a saved, restart-only setting. Disabling it requires
Motion and recorder1 autostart to be off and RMR1 to be confirmed idle. Saving
does not restart the camera. While disable is pending, new substream recording
and Motion starts are rejected. Timezone changes and SD-format coordination use
the active stream owners, including RMR1 until a restart actually removes it.
Host tests cover these checks and failed-save retry. Matching-firmware restart
and recording acceptance remain open. The main stream stays required by the
current software pipeline, and FrameSource buffers remain fixed at 1/1.

FIXQP has a saved mode-plus-QP operation for H.264/H.265. It preserves the
previous bitrate and applies only after a full camera-stack restart. T31 GOP
structure has the same saved-versus-active behavior for DEFAULT, PYRAMIDAL and
SMARTP; GOP length retains its checked live operation. SMARTP is not SMART
rate control. Native startup verifies the SDK's mode and required QP/GOP
values before registering the encoder. Host tests cover input, persistence,
readback and init-error cleanup; matching-firmware restart, encoded output and
42/22 runtime-memory acceptance remain open.

## Remaining settings

Anti-flicker exposes Off, 50 Hz and 60 Hz through the matched T31 SDK getter
and setter. Live, configured and disk-saved values remain separate. Host tests
cover all three save paths, mixed imaging changes and failed readback; physical
flicker reduction and reboot persistence need matching-firmware acceptance.

Motion webhook delivery uses one write-only administrator-configured HTTP or
verified HTTPS endpoint and a separate bounded delivery worker. Saved/live
comparison uses the actual internal endpoint, while status never returns it.
Host tests cover redaction, queue saturation and storage-worker independence.
Delivery from the installed camera remains unverified.

Motion also supports one ntfy topic URL with a separate two-event delivery
queue. It sends a fixed text notification, without images or device identifiers.
The URL and optional bearer token are write-only; a token requires HTTPS.
Changing a webhook or ntfy destination discards events queued for the previous
configuration. A request already accepted by its worker may still finish.
Host tests cover these boundaries and saved-URL reuse. Delivery and memory use
on the matching firmware still need camera acceptance.

Gotify and Telegram have separate opt-in text notification workers. Email has
a verified-TLS SMTP worker. FTP delivery uses explicit FTPS only and sends one
bounded substream JPEG; it does not provide plaintext FTP, video upload or a
persistent spool. Configuration, acknowledgement, queue and cancellation paths
have local host tests. These tests do not establish delivery from the camera,
the target TLS/protocol features, or camera memory use. Notification credentials
remain write-only. Privacy cancellation is best effort once upload has started:
bytes already sent cannot be recalled.

The implemented operations above do not cover every form field:

- Main-stream disable and buffer-count changes remain unimplemented. Substream
  enable uses the checked restart-only operation described above. Buffer status
  separates the SDK, loaded configuration and saved file. The current 42/22
  profile fixes both FrameSource counts at one; larger counts need memory
  measurements, not an assumption that hardware cannot support them.
  T31 has no distinct SMART rate-control mode; use the explicit CAPPED_VBR
  mode instead. SMARTP GOP structure is a different setting.
  Per-stream audio inclusion has a checked shared policy for RTSP and managed
  recordings, with an explicit selection and saved/active distinction.
  Changes apply after a full camera-stack restart. Host and browser tests pass;
  SDP, recorded audio and restart effects still need camera acceptance. This
  does not enable WebRTC audio or browser talkback.
- Day/Night has both fixed-time and sunrise/sunset scheduling. Its separate
  startup policy selects Day, Night or the existing Day default before
  automation takes over. Saving it does not change live outputs; configured,
  saved and process-start values remain separate. Host tests pass; startup
  behavior still needs matching-firmware camera acceptance. Automation pause
  retains manual control. Independent confirmation counts, per-output
  color/filter participation and RIC log-level settings are integrated with
  host tests; their effects and persistence still need camera acceptance.
  The existing Auto/Day/Night mode
  already covers forced operation. Detector-specific thresholds and elapsed
  transition delay are separate from the generic thresholds and
  confirmation counts.
  The night-time 850 nm illumination policy now has checked saved and live
  values; current-candidate camera acceptance remains open.
- Named OSD metadata has checked name, type, format and position settings,
  a native producer and RTSP/recording transport. Saved configuration and fresh
  published state remain separate. Gain observations share the Day/Night
  service's detector read, or use a demand-driven read at most once per second
  without changing its policy. Stale or missing gain is unavailable, not zero.
  Disabling Day/Night control retains this passive producer without initializing
  GPIOs or applying a Day/Night policy. Host tests cover disabled-to-enabled
  lifecycle, render bounds, persistence and readback failures. Stream and
  recording metadata, restart persistence and resource use need camera
  acceptance. Metadata is separate from the visible text overlay: the text
  size setting changes hardware-composited bitmaps, not browser text.
- Motion has one bounded built-in speaker alert and the notification workers
  described above. Their source integration does not replace delivery and
  resource tests on the matching firmware. Microphone diagnostic taps remain
  unimplemented.

Missing integration does not establish a hardware limitation. The A1 control
scope excludes IR940, white light, generic GPIO remapping, generic firmware
flashing, and whole-card repartitioning.

The full settings inventory is not yet accepted. The gain producer and other
integrated changes need a matching firmware
build and field-level camera checks. WebRTC receive audio, browser talkback,
native RMR video timelapse, IQ upload and raw configuration restore are separate
follow-up work; the existing JPEG timelapse and camera audio controls are not
substitutes for them.
