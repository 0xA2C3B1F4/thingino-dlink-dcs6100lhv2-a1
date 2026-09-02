# Public patch set

These patches are the smallest reviewed source delta currently admitted to the
first-release build path. They apply only to the immutable Thingino and Prudynt
revisions in sources.lock.json; scripts/source_prepare.py verifies every input
digest before installing or applying them.

## Scope

- thingino/0001-dlink-first-release-baseline.patch adds deterministic build
  timestamps, the D-Link AVPU and 24 MHz MMC0 seams, disables generic BusyBox
  flash applets, locks the non-login ONVIF account, and enables only the
  D-Link-scoped normal-media defines.
- thingino/0002-dlink-glibc-imp114.patch replaces the public `ingenic-lib`
  package site with a validated private same-camera bundle, selects only its
  three required glibc-compatible libraries, admits the exact C1 audio library
  only as a hash-bound staging/link input, excludes it from the generic target
  root, and builds Prudynt against the IMP 1.1.4 `zh` interface.
- thingino/0005-add-thingino-control.patch adds the `thingino-control`
  Buildroot package and installs the pinned Rust `thingino-controld` service.
  The historical Agent package remains available to unrelated profiles but is
  not selected or installed by this board.
- thingino/0009-remove-extension-cgi-and-mqtt-dispatch.patch routes Home
  Assistant settings through Control and prevents extension packages from
  restoring CGI or the generic mqtt-sub UI after the WebUI package runs.
- thingino/0010-persistent-onvif-and-disable-cgi-ingress.patch installs a
  persistent loopback ONVIF service, removes the CGI ONVIF and captive-portal
  roots, disables CGI launch arguments, and starts uhttpd in foreground mode.
- thingino/0011-install-static-dlink-webui.patch replaces the complete final
  `/var/www` tree with the four-file TypeScript bundle after every package
  install hook. It removes the retired WebUI boot generator and command
  helpers, so package ordering cannot restore CGI or legacy assets.
- thingino/0012-minimize-uhttpd-dependencies.patch drops the obsolete jct
  package dependency after the JSON request handler is removed.
- thingino/0014-stop-uhttpd-by-verified-pid.patch stops only the daemon named
  by its validated PID file and waits at most five seconds for TERM. It removes
  the BusyBox `pgrep`/`pkill` fallback which also matched the init command line.
- thingino/0015-route-daynight-actuation-through-control.patch leaves ISP
  photosensing and hysteresis in `daynightd`, but moves Prudynt and GPIO
  actuation into Thingino Control. The daemon publishes a requested mode and
  never runs the former shell/helper chain. The D-Link boot initializer pulses
  the fixed A1 IR-cut pins for the device-verified 100 ms before runtime
  services start. Prudynt startup also drops its background curl/API-key
  resynchronizer; Control owns the bounded post-start mode reconciliation.
- thingino/0019-reproducible-aac-link-order.patch feeds a null-delimited,
  bytewise-sorted Helix AAC object list to the linker so clean build workspaces
  produce the same shared object.
- uhttpd/0007-proxy-thingino-control.patch implements the bounded,
  loopback-only, nonblocking API proxy, direct Prudynt media relay, and
  authenticated native recording file path with byte-range support.
- uhttpd/0008-disable-cgi-and-proxy-onvif.patch removes the CGI dispatcher,
  makes the legacy request-process ABI fail closed, and adds bounded native
  ONVIF SOAP and snapshot ingress.
- uhttpd/0009-remove-request-script-runtime.patch removes CGI, process relay,
  dynamic request plugins, JSON request handlers, their CLI/config inputs, and
  the script queue from the production binary. It also drops the unused JSON
  handler libraries from the binary link closure.
- uhttpd/0010-defer-control-proxy-pump.patch defers every proxy continuation
  through one bounded event-loop pump and removes reentrant client/backend
  callback calls.
- uhttpd/0011-account-tls-socket-backpressure.patch blocks the backend on the
  combined plaintext and encrypted TLS output queues.
- uhttpd/0012-keep-mjpeg-encoder-profile-static.patch prevents media requests
  from changing shared JPEG dimensions or quality. It validates and discards
  browser `q`, `w`, and `h` hints; the fixed device profile supplies those
  values and an optional `f` only paces one client.
- uhttpd/0013-never-cache-static-html-shell.patch marks `.html` and `.htm`
  responses as `no-store` before conditional requests, so a firmware update
  cannot leave the static WebUI shell selecting an older asset bundle.
- onvif/0001-persistent-httpd-no-request-children.patch turns the pinned
  request handler into one bounded loopback daemon and replaces every command
  hook with in-process native or Control-backed behavior.
- prudynt/0001-enforce-rtsp-basic-auth.patch makes the already-configured
  RTSP credentials effective.
- prudynt/0002-dlink-media-baseline.patch contains only four live-path
  changes: JSON fallback for an empty or `unknown` proc sensor name, the
  board-specific IQ/bypass handling, a D-Link-only media-first startup path
  that does not block IMP initialization on wall-clock synchronization, and
  the exact 1080p-started barrier. The normal pipeline and hardware OSD remain
  enabled.
- prudynt/0003 through 0006 share immutable H.264 payloads, bound each RTSP
  client queue by elements and bytes, preserve a zero JPEG idle rate, and add
  host coverage for slow-client isolation and recovery at frame boundaries.
- prudynt/0007 through 0010 expire stale JPEG demand, stop and restart encoder
  receive around real demand, detect disconnected MJPEG peers without waiting
  for another frame, and keep those transitions under a tested state policy.
- prudynt/0011 keeps the shared JPEG profile immutable per request and makes the
  HTTP media/API listener optionally loopback-only. The D-Link final root sets
  that option, leaving authenticated uhttpd as the only network media ingress.
- prudynt/0012 replaces D-Link video reconfiguration with an in-place process
  image replacement after marking runtime descriptors close-on-exec. The T31
  SDK can block indefinitely while unbinding the live OSD/encoder graph. The
  childless replacement preserves the daemon PID, closes media resources in
  the kernel, normalizes reloaded stream FPS values, and restarts from the
  persisted configuration.
- prudynt/0013 replaces the unbounded detached MJPEG client threads with an
  eight-client admission limit and 256 KiB stacks. Thread creation and client
  handler failures reject only that connection instead of aborting Prudynt.
- prudynt/0014 keeps D-Link audio, motion, OSD, privacy, and day/night live
  control inside the existing process. prudynt/0015 reloads `/etc/TZ` during
  the childless media-process replacement. prudynt/0016 keeps automatic OSD
  burn-in sizing proportional enough for both 1920x1080 CH0 and 640x360 CH1;
  explicit scale values retain their existing meaning.
- prudynt/0017 through 0020 send versioned Motion observations to the neutral
  Control sink without shell dispatch, clear stale state after exec restart,
  and reload only a completely replaced camera configuration.
- prudynt/0021 makes IPC MJPEG admission and disconnect cleanup deterministic,
  rejects per-client mutation of shared JPEG profiles, keeps snapshots on the
  requested channel, and exposes RTSP queue-byte, drop, recovery, and client
  metrics.
- prudynt/0022 requires an IDR before every prebuffer or pending recorder start,
  handles partial writes and write failures, and syncs a segment before close.
- prudynt/0023 bounds inbound IPC requests and adds an exact, versioned,
  length-framed JSON transaction used directly by Thingino Control.
- prudynt/0024 makes a failed JPEG encoder start retryable and gives both
  snapshot channels separate, no-follow temporary files.
- prudynt/0025 adds an element cap to the byte-bounded RTSP socket queue and a
  byte cap to the audio tap, with overflow accounting through the same metrics.
- prudynt/0026 removes the Motion producer marker before the worker thread exits
  normally. Startup cleanup remains as recovery for abrupt termination and
  process-image replacement.
- prudynt/0027 through 0031 expose per-channel JPEG lifecycle metrics, make
  privacy and recorder transitions transactional, centralize OSD ownership,
  and delay media-ready until encoder receive is active.
- prudynt/0032 rejects `/run`, root/flash, tmpfs, and other non-storage recorder
  mounts even when a caller reaches Prudynt's direct control socket.
- prudynt/0033 publishes active-file markers atomically and closes recorder
  segments before the D-Link exit or process-replacement transition.
- prudynt/0034 through 0041 retain the D-Link-safe defog policy, make release
  error handling and IMP/IVS teardown explicit, route recorder control through
  framed IPC, preserve retryable teardown state, and retire stale readiness
  before process replacement.
- prudynt/0042 snapshots and validates Motion geometry before IVS setup.
  prudynt/0043 owns worker startup reports and joins exactly once.
  prudynt/0044 bounds known repeated failure loops and adds retry backoff.
  prudynt/0045 refuses a new Motion graph while retained IVS teardown state
  remains active.
- prudynt/0046 publishes and serves only complete JPEG frames.
  prudynt/0047 optionally publishes stream0 and stream1 Annex-B access units
  to Raptor's 2 MiB main and 512 KiB sub RSS rings, relays IDR requests, and
  leaves Prudynt as the sole IMP/ISP owner.
- prudynt/0048 keeps the configured D-Link encoder frame rate across day/night
  changes while retaining generic Prudynt's half-rate Night behavior.
- raptor/0001 keeps the hybrid `rwd` video-only and binds WHIP signaling to
  loopback. raptor/0002 removes the target RPATH, selects nonblocking kernel
  entropy for the Linux 3.10 camera runtime, and balances RSS reader ownership.
  Neither patch introduces Raptor HAL, libimp, or another media owner.

## Provenance and validation

The D-Link-specific hunks were decomposed from the reviewed private research
workspace. The earlier selected glibc/IMP 1.1.4 normal-media closure restored
color in a live checkpoint; the new Thingino-module/native-IQ candidate still
requires its bound live rerun. Single-variable IQ and IR-cut experiments are
not treated as equivalent evidence.

All eighteen Thingino patches have been checked in order against firmware commit
94d140dc0a458a23eb48a598e633324ea533f97c. The static TypeScript frontend has
its own host and browser fixture coverage; the final-root replacement, Control
package, and native ingress patches are covered by source-preparation and host
contract tests. The earlier
Agent cutover has live camera evidence; the full r5 Control cutover requires
the separate production acceptance recorded for that build. All forty-eight
Prudynt patches have been applied in order to a clean
c630ae797a86b29e83999fe324323932ec33460c worktree. Both Raptor patches have
been applied in order to a clean 6bc7f44be0c3f94a56b4296bc035386b320d2b5a
worktree. Patch application checks are host-only; runtime evidence is recorded
separately and must not be inferred from a clean application result.

## License gate

Patch context remains subject to the license of the source it modifies. The
exact pinned Prudynt tree contains no LICENSE, COPYING, or NOTICE file, so
publication and binary redistribution remain blocked until its terms are
established. Publishing the patch set does not close that license gate.
