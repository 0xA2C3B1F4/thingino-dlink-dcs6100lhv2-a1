# Third-party notice

Original material authored for this repository is available under the
top-level MIT license. That license applies only to rights held by the project
authors and does not relicense any third-party material described below.

This foundation contains only the reviewable patch delta under `patches/`; it
does not redistribute complete third-party source trees or binary artifacts.
It records immutable research pins in `sources.lock.json` so the exact files,
revisions, and patch targets can be audited before a build.

The RTSP Basic authentication delta in
`patches/prudynt/0001-enforce-rtsp-basic-auth.patch` originated in upstream
commit `ccd1e633cf1a6146c0e43d645251ce738d819950` by Paul Philippov. The
published patch is normalized as a raw diff without mail-envelope fields.

## Exact pinned-source observations

The WebUI navigation icons under `webui/public/icons/` are unmodified Feather
Icons 4.29.2 SVGs from <https://github.com/feathericons/feather/tree/v4.29.2/icons>.
They are Copyright (c) 2013-2023 Cole Bemis, distributed under the MIT license.
The complete notice is included in `webui/public/icons/LICENSE.txt` and is
copied into the static WebUI output. No remote icon or font service is used.

The following observations were made against the immutable revisions and Git
trees recorded in `sources.lock.json`. They identify license files and a
concrete blocker; they are not a conclusion that every file or generated
firmware component is redistributable.

| Source | Exact-revision observation | Current gate |
|---|---|---|
| Thingino firmware | top-level `LICENSE` contains the MIT license | preserve the notice for any imported Thingino-owned source; audit separately licensed packages and patches |
| Buildroot | `COPYING` states GPL-2.0-or-later for Buildroot except files with their own licenses, and assigns package patches to the patched software's license | retain exact per-file and per-patch license treatment |
| Prudynt | exact pinned tree `c630ae797a86b29e83999fe324323932ec33460c` contains no `LICENSE`, `COPYING`, or `NOTICE` file; repository visibility and historical live555 license files do not grant terms for Prudynt itself | the complete required patch delta is published as source-available material with this unresolved status disclosed; no MIT or open-source license is claimed for Prudynt-derived context, and binary redistribution remains blocked; see `LICENSE_REVIEW.md` |
| Thingino Ingenic SDK | top-level `LICENSE` contains the MIT license | independently audit the prebuilt libraries, modules, firmware, and IQ/tuning inputs before redistribution |
| Camera-local Ingenic runtime | exact public hashes identify three required stock-mtd3 libraries plus one C1 audio-processing library used as a staging/link input and later private runtime dependency | bytes are never committed, downloaded from `ingenic-lib`, placed in a public artifact, or redistributed; all camera-local inputs are validated and marked non-redistributable |
| RTL8188FU | the Thingino package declares GPL-2.0, most driver files carry GPLv2 notices, and the crypto closure carries BSD notices, but the exact pinned tree omits the referenced `COPYING` and README files | this repository carries both referenced license texts; binary release remains blocked until the exact compiled closure, notices, and corresponding source are recorded |
| Ingenic DTRNG register interface | the bounded GPL-2.0 recovery module follows the three-register interface published in Linux commit `406346d2227854bd9a5abdf07ef3d45d0de85a3e`; its T31 applicability remains a physical validation gate | retain GPL-2.0 source and attribution; do not accept entropy readiness until the live health and pool tests pass |
| Thingino Buildroot MIPS toolchain | the guided ARM64 SDK recipe is bound to the exact Thingino and Buildroot source revisions, host platform, configuration, archive name, and expected SHA-256 in `sources.lock.json`; the x86-64 path fails closed until equivalent reproducibility is validated | build the SDK locally from the pinned source closure; do not mirror or redistribute the mutable upstream prebuilt release asset; any binary distribution still requires the corresponding Buildroot and toolchain license closure |
| Rust 1.95.0 compiler and standard library | official source plus x86_64 and AArch64 Linux toolchain archives are pinned by SHA-256; the Rust project top-level terms are MIT OR Apache-2.0 and exact copies are under `third_party/licenses/` | the archives are local build inputs and are not redistributed; retain the notices for statically linked Rust standard-library code and audit the exact vendored standard-library dependency closure before any binary release |
| Raptor, raptor-common, raptor-ipc, and raptor-hal | exact commit pins and upstream `LICENSE` hashes are recorded in `components/raptor-rwd/raptor-lock.json`; each inspected license file contains GPL-3.0 | the D-Link component uses only rwd plus common and IPC code, not raptor-hal; preserve GPL source and notices, review the two local rwd patches, and complete the corresponding-source offer before binary distribution |
| Compy | the exact commit pin and upstream MIT `LICENSE` hash are recorded in `components/raptor-rwd/raptor-lock.json` | preserve the MIT notice for the linked subset and audit the exact source closure before binary distribution |
| Mbed TLS 3.6.6 | the immutable commit behind tag `v3.6.6` and its upstream `LICENSE` hash are recorded in `components/raptor-rwd/raptor-lock.json`; that file offers Apache-2.0 OR GPL-2.0-or-later | retain the chosen license text and notices for the static rwd closure; do not infer compatibility or redistribution closure from a successful link |

License review is still required for every redistributed file and exact pinned
revision, including Thingino, Buildroot, the Ingenic SDK integration, Prudynt,
Raptor, Compy, Mbed TLS, RTL8188FU, kernel and firmware modules, media runtime
components, and OS02G10 IQ/tuning data.

When redistribution rights are uncertain, this project may publish source
locations, required patch deltas, hashes, and a local acquisition or extraction
recipe instead of a binary artifact. That publication records the technical
delta; it does not create or imply a license grant for third-party context.

## Raptor source reconstruction

The source acquisition lock under `components/raptor-rwd/` records public base
commits, patch hashes, exact reconstructed Git trees and upstream license hashes.
The reconstruction patches retain the component's existing license treatment;
their publication does not create new distribution rights. Compy's transitive
slice99, datatype99, interface99 and metalang99 sources and the mbedTLS framework
are pinned with their LICENSE hashes. Their original notices remain in the
acquired source trees. The linked common library's vendored monocypher and cJSON
are bound by its exact Git tree. Review their per-file notices and corresponding
source obligations before distributing binaries. The local component build does
not close that review or any full-firmware distribution gate.
