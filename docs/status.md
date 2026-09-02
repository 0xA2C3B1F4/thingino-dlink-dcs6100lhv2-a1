# Release status

Status date: 2026-09-01.

This is a reviewed source snapshot, not a supported public firmware release.
It contains no firmware binaries or device-private inputs. The exact
development source commit used for the export is recorded in
[`source-export.json`](../source-export.json).

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

These are live-device results for one private candidate. The raw private logs,
identifiers, credentials, and device-specific hashes are not part of this
repository.

This source snapshot intentionally includes all 48 required Prudynt patches.
Their unresolved upstream license status is disclosed in the third-party
notice; the snapshot does not claim that Prudynt-derived context is MIT-licensed
or open source. The unresolved grant remains a firmware-distribution blocker.

## Source and host gates

Before export, the current development source passed its 542-file fail-closed
public-tree gate, 71 Markdown checks, and 510 Python tests. The refactored
Control passed 186 library tests, eight binary and storage-worker tests, 16
contract tests, a release build, and the 1,000-request soak. WebUI code did not
change in this refactor; its latest retained gate is 98 tests plus typecheck and
bundle scan. The production export contains 474 allowlisted text files and 472
`source-export.json` provenance entries. Its canonical `make check` passed 20
Markdown checks and 502 Python tests. The most recent 15-test Chromium fixture
run is earlier browser evidence and was not repeated for this checkpoint.

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
post-bootstrap reads. It is not physical acceptance, does not preserve original
mtd1/mtd2, and its initial recovery-functional source is not a D-Link stock
restoration source. A supported unopened-camera capture path, UARTless physical
write/handoff/retry/restore evidence, equivalent second-camera evidence, and an
accepted stock-functional mtd1/mtd2 source remain release gates. Post-run atomic status-marker and transaction-
rollback hardening has host and MIPS-build evidence but has not been repeated
on physical hardware.

## Open release gates

A public installer or firmware release remains blocked by:

- unresolved Prudynt source and patch-context terms;
- incomplete RTL8188FU license-file provenance;
- exact corresponding-source and notice review for Raptor rwd and its closure;
- a clean reproducible firmware build using only documented public and
  owner-acquired inputs;
- full content-addressed candidate acceptance with a real browser;
- interrupted-write, corrupt-data, reset, and reinstall tests on physical
  hardware;
- a supported read-only pre-write RAM capture path for all six original
  partitions on an unopened camera;
- the documented recovery flow on the final split layout;
- equivalent proof on another DCS-6100LHV2 A1; and
- real removable-media acceptance for the installation workflow on each claimed
  macOS, Linux, and Windows host.

`policy/release-gates.json` is the machine-readable ledger. Run
`make release-status` to inspect it. `make release-ready-source` validates this
disclosed source snapshot. `make release-ready-public-firmware` remains nonzero
while the public firmware scope has any blocked gate. A private local build is
validated independently and does not change those publication gates.

Do not publish an installer artifact or describe the source snapshot as an
installable release until every applicable gate has recorded evidence.
