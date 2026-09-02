# Release status

Status date: 2026-09-02.

This reviewed snapshot publishes the source and host tools. A supported public
firmware release still has open gates. Device-specific build inputs stay with
their owner. The exact development source commit used for the export is
recorded in [`source-export.json`](../source-export.json).

## What has been demonstrated

The named private A1 development camera has booted the fixed split-mtd3 layout
and the Prudynt plus Raptor rwd hybrid persistently. The measured checkpoint
included:

- WebRTC main, substream, Preview, and Streamer playback;
- both H.264 encoders at 15 fps in both Day and Night modes;
- HTTP 200 from both snapshot channels and Control health;
- RTSP, ONVIF, OSD, audio, Motion, privacy, recording, and day/night checks;
- WebRTC disconnect cleanup without a growing private or shared-memory leak;
- about 18.6 MiB available-memory proxy after the final run; and
- no second IMP, ISP, frame-source, or encoder owner.

A direct 25 fps trial reached about 24.8 fps on Day stream0, but Day stream1
and both Night streams ran at 12.5 fps. The selected profile remains 15 fps.

These are live-device results for one private candidate. The public evidence is
a summary; raw logs, identifiers, credentials, and device-specific hashes stay
private.

This source snapshot includes all 48 required Prudynt patches. The third-party
notice records their unresolved upstream license status. The project MIT
license covers project-authored work, while the Prudynt-derived patch context
still needs an upstream grant before firmware distribution.

## Source and host gates

The current checkout passes its fail-closed public-tree gate with 493
allowlisted text files, 24 Markdown checks, and 575 Python tests.
The refactored Control previously passed 186 library tests, eight binary and
storage-worker tests, 16 contract tests, a release build, and the 1,000-request
soak. WebUI code did not change in this refactor; its latest retained gate is 98
tests plus typecheck and bundle scan. The most recent 15-test Chromium fixture
run is earlier browser evidence and was not repeated for this checkpoint.

`source-export.json` has 477 provenance entries for the generated base export.
Public-only commits layered on that base carry their own provenance in Git
history; the manifest does not attribute those later files to the older
development commit.

The local install-set stager has native Linux and Windows CI contract coverage
plus the macOS adapter, and it performs common file readback before activation.
One macOS removable-media installation and UART-free restore sequence completed
with storage readback on the private camera workflow. This does not close the
wider card-reader, card-format, Linux, or Windows physical matrix or the clean
reproducible-firmware release gate.

The source now includes a complete duplicate private-backup schema, a
pre-write read-only RAM collector, same-device stock 1.02.02 preparation, an
unarmed one-use live authorization, a protected write-set contract, a bounded
MIPS restorer, and an activation-last fake-NOR interruption matrix. The guided
CLI reports backup, binding, write set, armed/prepared status, physical proof,
and a safe next action without printing private identities or mtd5 data.

The host and MIPS paths are implemented. One private camera completed the
UART-assisted pre-write duplicate capture and the full UART-free same-device
stock restore, including restorer physical readback, activation last, card
passivation, and stable D-Link 1.02.02 setup-mode boot. A stock-U-Boot success
indication alone was not treated as readback evidence. A development UARTless functional-capture source path now keeps the exact
original-backup type separate while declaring a pre-capture mtd1/mtd2 write
set, preserving original mtd0/mtd3/mtd4/mtd5, and requiring duplicate
post-bootstrap reads. One camera completed its mtd1/mtd2 updater pass, inert
handoff, read-only collector boot, duplicate schema-3 reconstruction, vendor
capture, preserved readback, and same-device comparison with the earlier exact
UART backup. It does not preserve original mtd1/mtd2, and its initial
recovery-functional source is not a D-Link stock restoration source. Physical
retry and restoration evidence, equivalent second-camera evidence, and an
accepted stock-functional mtd1/mtd2 source remain release gates. Post-run atomic status-marker and transaction-
rollback hardening has host and MIPS-build evidence but has not been repeated
on physical hardware.

The source also has a model-universal build path whose default media profile
uses source-built Prudynt and camera-acquired, catalog-locked stock vendor
libraries. It no longer requires the legacy private media closure or Raptor;
Raptor is an optional WebRTC profile. The build API has no camera configuration
or recovery arguments, emits an empty data member, locks the unprovisioned root,
and disables network-facing startup. Separate signed
audit sidecars, exact-span JFFS2 overlays, and authorizations bind one camera,
session, provisioning payload, universal bundle, exact stage 2, and data
action. The JFFS2 overlay is built twice with fixed geometry and checked with
`jffs2dump -c`. Universal Stage 1 snapshots both write sources in RAM, verifies
a camera-keyed HMAC before writes, commits backup/checkpoint temporary names by
readback plus rename, writes and verifies provisioning before system/kernel,
and passivates camera files after activation. The authorization key derived
from preserved mtd0/mtd4/mtd5 is never emitted. Host tests prove that two camera
fixtures use identical universal bytes and reject cross-camera swaps. The
fake-NOR matrix injects torn erase/write/readback/activation failures and proves
an exact provisioning retry.

This is not yet physical provisioning acceptance. Slow-card timing, physical
power interruption, one-use cleanup, and successful boot with camera-specific
credentials remain blocked. The guided CLI can build the universal artifact,
create session-bound configuration and provisioning/authorization artifacts,
stage the validated tuple to an exactly confirmed removable device, and perform
the separately confirmed post-stock `universal handoff`. The handoff
revalidates the tuple and makes the stock selector inert before Stage 1. Neither
host command writes NOR, and their availability does not close those physical
gates.

## Open release gates

A public installer or firmware release remains blocked by:

- unresolved Prudynt source and patch-context terms;
- incomplete RTL8188FU license-file provenance;
- a clean reproducible firmware build using only documented public and
  owner-acquired inputs;
- full content-addressed candidate acceptance with a real browser;
- interrupted-write, corrupt-data, reset, and reinstall tests on physical
  hardware;
- a supported read-only pre-write RAM capture path for all six original
  partitions on an unopened camera;
- the documented recovery flow on the final split layout;
- physical JFFS2 provisioning write/readback, slow-card timing, provisioned
  boot, and one-active-card cleanup without changing the universal firmware
  digest;
- equivalent proof on another DCS-6100LHV2 A1 using the identical universal
  firmware SHA-256 and distinct camera artifacts; and
- real removable-media acceptance for the installation workflow on each claimed
  macOS, Linux, and Windows host.

`policy/release-gates.json` is the machine-readable ledger. Run
`make release-status` to inspect it. `make release-ready-source` validates this
disclosed source snapshot. `make release-ready-public-firmware` remains nonzero
while the public firmware scope has any blocked gate. Private local-build
acceptance and public release acceptance remain independent.

Do not publish an installer artifact or describe the source snapshot as an
installable release until every applicable gate has recorded evidence.
