# September 17 full-Raptor audio candidate

Firmware compilation source: `d5cc9cd11ddec9298feafe220e4977e2fce25ce4`.
Host-only packaging fix: `0a64926b688a5a85498236cb078ab9fa15bddf4b`.
This records technical host evidence, not a firmware release or complete
physical acceptance.

## Independent whole-firmware comparison

Two clean base builds and two independently compiled full-Raptor components
produced byte-identical final roots, split kernels and signed install sets.
Neither full-Raptor compilation used the component artifact cache. The
[normalized comparison receipt](2026-09-17-raptor-audio-reproducibility.json)
records all 16 compared files, their sizes and SHA-256 values.

| Artifact | SHA-256 |
| --- | --- |
| Signed universal bundle | `98c764a5a01eac261290e6cb37e1a83c75b2006da01bb200f8e325b91dbbd7fd` |
| System SquashFS, 6,352,896 bytes | `5395ee1285c178b3fc4914e720e73c0e51984d48c9a8bbb717f42d2881a8c57f` |
| Final kernel | `faf28040e59639fb9818b3cc12ead8c1c5a0f8109d01ab693fb579797dd3e383` |
| RWD executable | `4fd3a82152cbd2b59b11c372a160780a69e3981214f31b30d04f58e07533e106` |

The system fits the fixed 6,619,136-byte region. The model build is full
Raptor, not Prudynt. The source-built media payload validator and the native
SDP direction parser/generator regression passed. Both install sets passed
schema-2 inspection. `scripts/release_closure.py` accepted the completed run
and generated technical candidate and notice sidecars in private evidence
storage. The 13 reconstructed source trees also passed normal source
validation and the technical source-delivery inventory check.

## Interrupted host packaging and explicit completion

The original CLI invocation stopped before the second split-kernel container
launched. Its fixed `verification-build-b` name collided with an exited
container from an older run. The old container and its results were not used
or removed. The namespace fix hashes the whole run path, including the parent
of a verification child; its regression and the full 901-test host gate passed.

After revalidating the pinned public inputs, vendor bundle, both new component
payloads, base comparison and first package, an explicit private completion
invoked the normal `package_install_root` for only the missing second kernel
and install set. The normal `_compare_full_builds` and completed-run manifest
writer then recorded the complete result. The initial failure and separate
completion receipt are preserved. This is not claimed as an uninterrupted
successful first CLI invocation or a fresh post-fix CLI end-to-end run.

## Physical acceptance boundary

The operator heard Preview Listen audio on both Mainstream and Substream with
the new RWD, WebUI and audio-reader profile overlaid on the older September 16
whole image. That bounded observation does not establish installation or
acceptance of this new universal bundle. No SD-card or camera write occurred
during this host build.

In a later physical session, the same-camera SD stage and operator-observed
stock-updater completion were followed by the normal host handoff. Independent
SD readbacks passed after unmount/remount. On the final station boot,
`universal verify` passed pinned health, authenticated Control, WebUI and media
readiness. An independent pinned read of the complete system partition produced
`050850f42416e45101e740f5928a242a3c559a23354a99d1b6bd530c41e9538d`,
equal to this candidate's SquashFS padded with `0xff` to 6,619,136 bytes. Live
RWD matched the executable digest above. This establishes this candidate's
persistent system installation, not complete release acceptance. It does not
independently establish kernel or protected-partition equality.

The operator then reproduced the microphone-enable transition error. Audio
was audible after reload and later again after a brief reported silence, with
no further microphone press reported. A later direct RAD owner observation
reported input disabled and unmuted. Do not infer a successful transition, an
operator retry, or one root cause from these observations. Existing logs do
not distinguish the RAD command, its acknowledgement, and independent
post-transition observation. Stage-tagged Control diagnostics are being added
without removing the error or relaxing the readback gate.

On September 18, after a cold boot and an independent disabled-input readback,
one explicit native `raptorctl rad ai-enable` failed with `connection failed`.
The immediately following independent input and level observations nevertheless
reported enabled input. No command retry or service restart occurred. Private
receipts retain the complete command results. This establishes a lost command
acknowledgement despite an applied mutation, not complete browser acceptance.

The locked IPC server reused its one-second receive deadline after synchronous
handler work. A new 1.2-second handler regression reproduced a missing response
against that implementation. Giving response transmission its own one-second
budget made all seven control-socket tests pass with AddressSanitizer and
UndefinedBehaviorSanitizer. Partial-prefix/body receive bounds and the client's
total command timeout remain unchanged. The updated source patch and tree lock
are host-tested only; the installed candidate above does not contain this fix.
Do not attribute the earlier failed RAM deployment to this IPC finding.

The microphone transition/independent-readback acceptance gate remains
open. Complete candidate, provisioning, interruption/recovery, second-camera
and claimed physical host-media acceptance remain open. RTL8188FU and Raptor
corresponding-source delivery/review remain open. Technical sidecars retain
legal review `not-assessed` and redistribution
`not-authorized-by-this-repository`.
