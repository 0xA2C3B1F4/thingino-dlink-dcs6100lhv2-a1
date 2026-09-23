# September 23 Raptor candidate on the first A1 camera

This record applies only to firmware source
`22989b85e327b090116cf7884a2e616055e24454c` and its 7,958,609-byte
signed universal bundle, SHA-256
`416e94a8125aa4758d8cdea2ee91ab5543fc955ea54f41c35c7121edf125a23c`.
The target was the first DCS-6100LHV2 A1 camera. It is a bounded installation
and browser result, not public firmware-release acceptance.

## Build and installation

Two clean `initialize` builds without Raptor component-cache reuse produced
16 byte-identical complete-firmware artifact pairs. An independent host pass
rehashed all 32 files and inspected both install sets. These build results were
recorded in [release status](../status.md) before the camera work.

The supported universal path staged the camera-specific SD files. On the first
card boot, the operator observed familiar blinking, but no direct UART success
line was captured. A subsequent boot without the card
reached `STAGE1 booted` and `device_verified`, then stopped at the expected
`FAIL sd_partition_missing`. This sequence alone does not prove that the stock
updater completed. The still-active card was booted again; the operator observed
regular red blinking, but the passive UART listener started too late to capture
the stock updater's success line. The active selector was then passivated
through the supported universal handoff. Its camera-bound host plan and full
receipt are retained in private evidence.

The next card boot produced UART stage records for `sd_mounted`,
`stage2_snapshotted_and_verified`,
`camera_authorization_and_provisioning_snapshotted`,
`recovery_checkpoint_committed`, `stock_userdata_backup_committed`,
`final_write_phase`, `provisioning_data_written_and_verified`,
`final_system_written`, `final_kernel_tail_written`,
`preactivation_kernel_verified`, `activation_written_and_verified`,
`camera_install_files_passivated`, `sd_unmounted`, and `rebooting_final`.
The following boot reached `thingino_verified_switch_root`.

The supported universal verifier passed application, Control, health, media,
and WebUI checks on the pinned station. It reported system partition
SHA-256 `3c73cd753eab1be4e9df7a57154833fb601513c76eee8740749c51ec8426a0b4`.
The supported selected-partition verify-readback passed on that boot:

| Readback span | SHA-256 |
| --- | --- |
| Logical mtd1 | `093a7ba72b13545406e59f35fb38bd312e485b3f2e20f55e6c300dafad9c56af` |
| Logical mtd3, system | `3c73cd753eab1be4e9df7a57154833fb601513c76eee8740749c51ec8426a0b4` |

The camera-specific protected physical mtd0, mtd4 and mtd5 also matched the
preserved same-camera readbacks. Their identifiers and digests are retained only
in private evidence.

This is not a full-flash readback. Mutable data and an exact comparison of
physical mtd2 were excluded.

## Browser observation and remaining gates

After the operator handled the new local HTTPS certificate warning and logged
in, Chromium showed live Main and Substream WebRTC. One Listen click reported
`Audio playing` on each stream, and the operator heard audio immediately on
both. On Main stream, with Listen still on, switching the microphone from Off
to On restored audio immediately without Reload. No Safari result is claimed
for these bytes.

The public firmware-release ledger remains 2 of 9 gates closed. The full
content-addressed candidate matrix, interrupted-installation and recovery
tests, wider provisioning acceptance, a second camera, host-platform and hosted
CI acceptance, RTL8188FU rights, and Raptor corresponding-source and legal
review remain open. This record does not authorize firmware distribution.

## Bounded Control acceptance after installation

The host-side camera acceptance script was updated for the Raptor supervisor's
Control PID path and for the documented Raptor API differences; this script
change did not alter the installed firmware bytes. The first unmodified-script
attempt made no API requests because it expected the old PID path. A subsequent
diagnostic run identified the expected 503 for Raptor's unsupported aggregate
`GET /api/v1/config` and the expected 401 for ONVIF snapshot requests carrying
only a WebUI API key. The final script requires the exact unavailable response
for aggregate config and rejects a WebUI-key bypass of ONVIF HTTP Digest.

On the same installed candidate, a bounded 1,000-request Control/media run
passed. The serial request loop took 28.39 seconds; Control threads were
16→16, file descriptors 5→5, child processes 0→0, and RSS 1616→1604 KiB.
Both API snapshot routes returned HTTP 200, but this script did not validate
JPEG contents. It also did not prove positive ONVIF Digest access, full auth
session lifecycle, concurrent slow-client behavior, or CPU/PSS/socket/shared-
memory stability. Those candidate checks remain open.

## Read-only media and protocol checks

Further checks on the same installed bytes used the camera-bound SSH host key
and made no settings or flash changes. Both authenticated Control snapshot
responses decoded as JPEG at 1920 × 1080 and 640 × 360. Each authenticated
HTTPS MJPEG endpoint produced two decoded frames. This proves the media routes,
not a browser MJPEG fallback or reconnect sequence.

Both `/stream0` and `/stream1` RTSP endpoints rejected an unauthenticated
request, then accepted the separately derived camera-bound Digest credential.
The bounded verifier received a complete H.264 IDR and two RTP timestamps on
each stream, with the expected 1920 × 1080 and 640 × 360 SPS dimensions.
Neither the credential nor camera frames were saved or printed.

Both ONVIF snapshot routes also accepted their own HTTP Digest authentication
and returned decoded JPEGs at those dimensions. WS-Security UsernameToken
authenticated Media1 and Media2; each exposed two profiles whose RTSP and
snapshot URLs matched the endpoints tested above. The HTTPS certificate was
first read over pinned SSH and compared before SOAP credentials were sent.
The reusable, camera-specific SOAP probe and its exact invocation remain with
the private candidate ledger. A running `onvif-httpd` did not establish
WS-Discovery: no UDP 3702 listener or discovery init script was present in
this image. Automatic discovery and ONVIF profile conformance are not claimed.
The installed `wsd_simple_server` also embeds the old
`/var/www/onvif/wsd_files` path, while this image contains those templates only
under `/usr/share/onvif/wsd_files`. Starting the existing binary would not be
a validated discovery fix.

The host clock was on September 23, but the camera UTC clock read August 6
after roughly 84 minutes of uptime. `ntpd` was running without a default route
or DNS resolver. The local-only profile requires an operator-configured
reachable NTP address; the clock was not changed during these checks. SOAP was
tested using the camera's own current time. Time settings, time-based
schedules and timestamp correctness remain unaccepted until synchronization
and persistence are observed.

## Bounded concurrency and runtime logs

At about two hours of uptime, two simultaneous authenticated HTTPS media-state
GETs both returned HTTP 200 in 3.619 and 3.632 seconds of client time. Control
retained 16 threads, five file descriptors and one socket descriptor before
and immediately after. Its RSS fell from 1776 to 1708 KiB, PSS from 1450 to
1385 KiB and private memory from 1380 to 1316 KiB; system shared memory was
unchanged at 5764 KiB. The private ledger retains the read-only probe and
numeric result. TLS setup and uhttpd time are included in the client duration,
so it cannot be compared directly with Control's three-second accepted-request
deadline. Two requests do not close slow-client, disconnect or soak acceptance.

The RVD log contained 21 transient control-request failures, each followed by
a recovery message. Control also logged rate-limited day/night heartbeat
timeouts through at least count 16 and three privacy heartbeat timeouts. A
day/night configuration GET returned HTTP 200, while three sequential
heartbeat GETs reported `unknown`, `day`, `day` for the live mode. The optional
heartbeat reader has a 300 ms RIC budget and preserves `unknown` on failure.
This observation does not prove an encoder failure, but intermittent state
readback and the cause of the timeouts need investigation before acceptance.
The retained kernel ring began at boot but its last entry was at 446 seconds;
it does not prove the later two-hour interval error-free. No camera settings
or firmware were changed for this review.

The private content-addressed candidate ledger now has six passed checks of
twenty. Nine are explicitly incomplete and five have no run observation.
This remains a partial first-camera result, not a firmware release decision.
