# Third-party notice

Original material authored for this repository is available under the
top-level MIT license. That license applies only to rights held by the project
authors and does not relicense any third-party material described below.

The repository publishes only reviewable patch deltas under `patches/`. It does
not redistribute complete third-party source trees or binary artifacts. The
immutable source pins in `sources.lock.json` and the component locks record the
exact files, revisions, and patch targets used by a build.

## Exact pinned-source observations

The WebUI navigation icons under `webui/public/icons/` are unmodified Feather
Icons 4.29.2 SVGs from <https://github.com/feathericons/feather/tree/v4.29.2/icons>.
They are Copyright (c) 2013-2023 Cole Bemis, distributed under the MIT license.
The complete notice is included in `webui/public/icons/LICENSE.txt` and is
copied into the static WebUI output. No remote icon or font service is used.

The following observations were made against the immutable revisions and Git
trees recorded in `sources.lock.json` and the component locks named below.
They identify license files and concrete blockers. They do not conclude that
every file or generated firmware component is redistributable.

| Source | Exact-revision observation | Current gate |
|---|---|---|
| Thingino firmware | top-level `LICENSE` contains the MIT license | preserve the notice for imported Thingino-owned source; audit separately licensed packages and patches |
| Buildroot | `COPYING` states GPL-2.0-or-later for Buildroot except files with their own licenses, and assigns package patches to the patched software's license | retain exact per-file and per-patch license treatment |
| Thingino Ingenic SDK | top-level `LICENSE` contains the MIT license | independently audit prebuilt libraries, modules, firmware, and IQ/tuning inputs before redistribution |
| Camera-local Ingenic runtime | the vendor catalog pins locally acquired libraries, ISP/sensor modules and IQ data; the final-root builder copies the camera-acquired runtime into the local system image | these camera-acquired bytes are not published in the source repository; a locally composed firmware bundle must not be treated as a redistributable release asset |
| RTL8188FU | the Thingino package declares GPL-2.0, most driver files carry GPLv2 notices, and the crypto closure carries BSD notices, but the exact pinned tree omits the referenced `COPYING` and README files | this repository carries both referenced license texts; binary release remains blocked until the exact compiled closure, notices, and corresponding source are recorded |
| Ingenic DTRNG register interface | the bounded GPL-2.0 recovery module follows the three-register interface published in Linux commit `406346d2227854bd9a5abdf07ef3d45d0de85a3e`; its T31 applicability remains a physical validation gate | retain GPL-2.0 source and attribution; do not accept entropy readiness until live health and pool tests pass |
| Thingino Buildroot MIPS toolchain | the guided ARM64 SDK recipe is bound to the exact Thingino and Buildroot source revisions, host platform, configuration, archive name, and expected SHA-256 in `sources.lock.json`; the x86-64 path fails closed until equivalent reproducibility is validated | build the SDK locally from the pinned source closure; do not mirror or redistribute a mutable upstream prebuilt release asset |
| Rust 1.95.0 compiler and standard library | official source plus x86_64 and AArch64 Linux toolchain archives are pinned by SHA-256; the Rust project terms are MIT OR Apache-2.0 and exact copies are under `third_party/licenses/` | the archives are local build inputs; retain notices for statically linked Rust code and audit the exact vendored standard-library closure before binary release |
| Raptor, raptor-common, raptor-ipc, and raptor-hal | `components/raptor/source-build-lock.json` records the full stack's exact bases, reconstructed trees, patch hashes, and upstream GPL-3.0 license hashes | preserve the applicable source and notices; review the complete corresponding-source delivery before distributing firmware |
| Compy | the full stack's exact tree and upstream MIT `LICENSE` hash are recorded in `components/raptor/source-build-lock.json` | preserve the MIT notice for the linked subset and audit its transitive source closure before binary distribution |
| Mbed TLS 3.6.6 | the exact source tree and upstream `LICENSE` hash are recorded in `components/raptor/source-build-lock.json`; that file offers Apache-2.0 OR GPL-2.0-or-later | retain the chosen license text and notices for linked code; do not infer redistribution closure from a successful link |
| libschrift and Ubuntu Regular font | the full stack pins libschrift's tree and ISC license hash; `components/raptor/README.md` records the font and Ubuntu Font Licence 1.0 inputs | the component installs both license texts beside the font; retain them and their source provenance |

License review is required for every redistributed file and exact pinned
revision, including Thingino, Buildroot, the Ingenic SDK integration, Raptor,
Compy, Mbed TLS, RTL8188FU, kernel and firmware modules, media runtime
components, and OS02G10 IQ/tuning data.

When redistribution rights are uncertain, this project may publish source
locations, required patch deltas, hashes, and a local acquisition or extraction
recipe instead of a binary artifact. That publication records the technical
delta. It does not create or imply a license grant for third-party context.

## Local firmware is not a public binary release

`installer/final_root.py` installs the validated vendor bundle before packing
the model-universal system image. `installer/raptor_full_root.py` composes the
Raptor runtime onto that image, and `installer/final_bundle.py` includes it in
`thingino-universal.tgb`. Camera acquisition happens locally, but the resulting
bundle still contains camera-acquired runtime files. "Model-universal" means
reusable across the supported model without camera-specific credentials. It
does not mean free of third-party binary inputs or approved for redistribution.

A public prebuilt-components package that excludes those files would need a
separate payload definition, exclusion checks, and a local composition step.
That distribution format is not implemented by the current universal bundle.

## Raptor source reconstruction

The full-stack source acquisition lock at `components/raptor/source-build-lock.json`
records twelve public base commits, patch hashes, exact reconstructed Git trees,
and upstream license hashes. Its patches are under
`patches/raptor-full-source/`. The reconstruction patches retain each
component's existing license treatment. Their publication does not create new
distribution rights.

Compy's transitive slice99, datatype99, interface99, and metalang99 sources and
the Mbed TLS framework are pinned with their license hashes. The linked common
library's vendored monocypher and cJSON are bound by its exact Git tree. Review
their per-file notices and corresponding-source obligations before distributing
a binary. The local component build does not close that review or any
full-firmware distribution gate. The Ingenic headers are pinned by
`components/raptor/headers-input.json`, whose recorded license status is
unspecified. Pinning does not grant redistribution rights.

Full-Raptor component validation currently checks payload provenance and the
two delivered font and rendering license files. It is not an audit or delivery
package for every linked component's notices and corresponding source.
