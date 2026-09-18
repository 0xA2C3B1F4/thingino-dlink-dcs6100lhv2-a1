# License review status

This file records source observations, not legal advice or a new license grant.
The machine-readable release decision is in
[`policy/release-gates.json`](../policy/release-gates.json).

## Full Raptor

`components/raptor/source-build-lock.json` pins the public source trees used by
the full media stack. Each entry records a repository, exact base and final
tree, reconstruction patch digest, and license-file digest. The public tree
contains the reconstruction patches under `patches/raptor-full-source/`, but it
does not redistribute the fetched upstream trees or firmware binaries.

The lock records GPL-3.0 Raptor components, the MIT-licensed Compy subset,
Mbed TLS under Apache-2.0 or GPL-2.0-or-later, libschrift under ISC, and the
Ubuntu Regular font under the Ubuntu Font Licence 1.0. These observations do
not close the per-file review. Transitive sources, vendored code, Ingenic
headers, generated notices, and the exact linked closure still need to be
reviewed together before a firmware binary is distributed.

The full-Raptor build can reconstruct the locked trees and validate the local
patches. That is source and build provenance, not a corresponding-source
delivery package. The `raptor-corresponding-source` firmware gate stays blocked
until the exact binary closure, notices, and corresponding-source delivery are
recorded.

## RTL8188FU

Pinned commit `6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06` has no top-level
`COPYING`, `LICENSE`, `NOTICE`, or README file. Most driver sources carry
Realtek GPL version 2 notices. The `core/crypto` closure also contains Jouni
Malinen and Cozybit files that state BSD terms and refer to a missing README for
the full text.

This repository carries the canonical GPL-2.0-only text and the hostap BSD
license text referenced by those headers. That fixes the missing license-text
delivery in this source repository. It does not yet prove the exact compiled
RTL8188FU file closure or corresponding-source package for a firmware binary.
The firmware gate stays blocked until that generated closure and its notices are
recorded together.

### Linked translation-unit evidence

`rtl8188fu-linked-sources.inventory.json` records 150 C translation units from
the actual Kbuild link and compile records in the completed clean build of
firmware source `14b81c671931b038545b0d9177a4609ad8818e91`. It binds each source,
object and compile record by SHA-256, plus the kernel configuration, generated
module source, link record and built/installed modules. The build workspace was
mounted read-only with journal replay disabled. Regenerate the JSON with:

```sh
python3 scripts/inventory_rtl_linked_sources.py --workspace /path/to/readonly/workspace
```

The complete JSON is byte-identical to the independently captured preceding
`38607f3` build inventory. The installed module hash is
`2ac80b9f1d3b08080cb55ed8011379fc8872caa73502269cb3d0591b9ff2735f`.
Earlier inspection of those same built-module bytes verified that the target
toolchain's `--strip-debug` produces that installed hash. Header marker counts
are observations only, not license classifications. The tool accepts the exact
merged-usr `lib -> usr/lib` alias and rejects other symlink components.

This inventory does not cover transitive driver/kernel/compiler headers, full
per-file notice review or a corresponding-source delivery archive. In particular,
absence of a license marker in a file's first 80 lines is not a license decision.
The RTL8188FU firmware gate remains blocked until those requirements are met.

The follow-up `rtl8188fu-header-dependencies.inventory.json` records the 767
existing header dependencies from all 150 verified compile records: 605 kernel,
161 driver and one compiler header. It separately lists 983 absent optional
configuration headers. Shared `source_sets` preserve the translation-unit
mapping without repeating identical source-name lists for every header.
Regenerate from the same read-only workspace and linked-unit inventory:

```sh
python3 scripts/inventory_rtl_dependencies.py --workspace /path/to/readonly/workspace \\
  --units third_party/rtl8188fu-linked-sources.inventory.json
```

The parser does not evaluate Make expressions. Only literal paths and the
recorded optional configuration-header form are accepted. Required dependencies
must exist, compile-record hashes must match, and resolved paths must remain
inside the workspace. Internal symlinks retain both recorded and resolved paths.
This extends the technical inventory to recorded transitive headers, but does
not establish unrecorded generator/compiler inputs, per-file licensing or a
complete corresponding-source delivery package. No license gate is closed.

The bundled copies were retrieved from the primary sources on 2026-08-29:

- `RTL8188FU-GPL-2.0-only.txt` has SHA-256
  `4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e`; and
- `RTL8188FU-hostap-BSD-3-Clause.txt` has SHA-256
  `2c2b3640a8256edb409356a7e3f3dfe762a489bd2cd9586aa1647b0f1128caf8`.

## Evidence sources

- [Pinned RTL8188FU repository](https://github.com/gtxaspec/rtl8188ftv-wifi/tree/6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06)
- [GNU GPL version 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt)
- [hostap license](https://w1.fi/cgit/hostap/plain/README)
