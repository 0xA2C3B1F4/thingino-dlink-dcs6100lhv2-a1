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

### Private source archive tooling

`scripts/raptor_private_source_archive.py` packages the 13 locally reconstructed
Raptor source trees, their exact source lock, headers input and reconstruction
patches. It requires an existing verified cache and does not fetch sources.
The command uses `TMPDIR` for temporary data and refuses an existing output.
For example, with task-specific paths on the external build volume:

```sh
python3 scripts/raptor_private_source_archive.py \
  --verified-cache-root "$RAPTOR_VERIFIED_CACHE" \
  --output "$RAPTOR_PRIVATE_EVIDENCE/raptor-sources.tar"
```

The archive is private technical evidence. Its manifest records source-file
digests and gitlinks, including omitted formatter submodules. It does not include
the full Thingino, kernel or RTL source closure, nor does it bind those sources
to a particular firmware binary. It does not settle the Ingenic headers license
or the remaining notice review. Do not publish the generated archive or treat
successful packaging as closure of the firmware corresponding-source gate.

The 2026-09-18 private verification produced two byte-identical archives from
the current source lock. Both were 75,622,400 bytes, with SHA-256
`3b37cc50f9f1b676bfb3643fe1fd7a6f86505314ddf0fd307ab95fdcd2fc4d8c`.
An independent archive reader compared all 3,440 source files and symlinks,
including their modes and contents, against the retained canonical source tars.
That comparison passed. These are source-package integrity results, not license
clearance, binary-to-source correspondence or physical camera acceptance.

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

### Remaining driver notice questions

A full-file marker check against the hash-verified 150 translation units and
161 driver headers found GNU GPL references in 289 files and BSD references in
19 files. The following three files contained none of the searched GPL, BSD,
SPDX or permission-grant markers:

- `core/crypto/rtw_crypto_wrap.c`
- `core/crypto/rtw_crypto_wrap.h`
- `include/rtw_version.h`

These are text observations, not determinations that the files lack a license.
The first two are small compatibility wrappers; the version header contains
one version-definition line. The pinned upstream history for
[`rtw_crypto_wrap.c`](https://github.com/gtxaspec/rtl8188ftv-wifi/blob/6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06/core/crypto/rtw_crypto_wrap.c)
leads to import commit `252ab91e666918c52541891519cf029641e31fec`, rather than
providing an earlier file-level license explanation. Package-wide GPL metadata
alone is not recorded here as a resolution of those provenance questions.

The exact Thingino package declares `WIFI_RTL8188FU_LICENSE = GPL-2.0` and
`WIFI_RTL8188FU_LICENSE_FILES = COPYING`, while the pinned driver tree lacks
that file. A source-delivery process must explicitly reconcile the missing
upstream license file and the BSD notices instead of assuming that invoking
Buildroot legal-info completes the review. No legal-info success or complete
source-delivery archive is claimed by these inventories.

The bundled copies were retrieved from the primary sources on 2026-08-29:

- `RTL8188FU-GPL-2.0-only.txt` has SHA-256
  `4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e`; and
- `RTL8188FU-hostap-BSD-3-Clause.txt` has SHA-256
  `2c2b3640a8256edb409356a7e3f3dfe762a489bd2cd9586aa1647b0f1128caf8`.

## Selected local-package gaps

Three selected local packages name license files that are absent from their
retained package directories, but they have existing package-level license
declarations. `mbedtls-certgen` declares `GPL-2.0+`; its dependency on mbedTLS is
separate from that declaration. `thingino-daynightd` declares `GPL-2.0`, and its
`files/README.md` identifies the project as GNU GPL v2.0. Its C header additionally
permits GPL version 2 or later. Preserve that file-level permission without
silently changing the entire package to an or-later declaration.

`thingino-sounds` declares `CC0`. That is existing package-level evidence, not
an absence of rights information. The source repository now supplies the CC0
text and records the exact installed asset comparison below. Control's `Cargo.toml` declares `MIT`,
while its local Buildroot recipe incorrectly reports `Unknown`. The project's
existing MIT text and Cargo declaration are the starting point for correcting
that metadata, rather than seeking a new grant.

A missing per-file header or LICENSE file alone does not establish that new
author permissions are needed. Retain package declarations and existing
exceptions, supply their referenced texts, and review actual conflicts or
imported material where evidence requires it. Root MIT does not replace a
different component's terms or authorize camera-vendor binary redistribution.
The exact source/text delivery and firmware distribution gates remain open.

### Installed Thingino sounds and supplied text

[`thingino-sounds.inventory.json`](thingino-sounds.inventory.json) records the
12 Opus files, totaling 99,287 bytes, under `usr/share/sounds` in the completed
`a091c826479a206b62f82821e38429273345554c` system image. Each installed file was
read directly with `unsquashfs -cat` and compared byte for byte with its retained
prepared-package input. All 12 matched. The inventory binds the system image,
preparation receipt, package recipe and individual asset hashes. Its reproduction
instructions do not require extracting or changing the image.

The selected configuration uses Opus and Wi-Fi messages, with startup and
chime sounds disabled. The inventory describes this image, not every optional
upstream asset. Raptor's separately installed `motion.pcm` is outside its scope.

The package declares CC0 but omits its referenced `LICENSE`. The complete
[CC0 1.0 Universal text](licenses/thingino-sounds-CC0-1.0.txt) was retrieved from
[Creative Commons](https://creativecommons.org/publicdomain/zero/1.0/legalcode.txt)
on 2026-09-22. Its 7,048 bytes have SHA-256
`a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499`.
A second fetch independently matched the committed text's digest. This supplies
the text corresponding to the package's existing declaration. It does not add
a grant, edit upstream sources, place the text in the already-built firmware,
or assert the provenance of rights beyond that declaration.

The installed assets are now mapped to their prepared sources. A corresponding
package-source delivery and the rest of the firmware review remain open. The
inventory does not treat source-byte equality as legal approval or permission
to publish firmware binaries.

## Buildroot legal-info collection

The offline collector passes the retained Thingino `mxml` 4.0.4 hash file to
Buildroot as `MXML_HASH_FILES`. Before running `legal-info`, it checks the
Thingino version override, the pinned source archive, and the extracted
`LICENSE` and `NOTICE` against that file. The receipt records this selection and
its scope. For Linux 3.10.14, the collector selects its top-level `COPYING`
instead of newer kernel `LICENSES/` paths that do not exist in that release. It
checks the retained archive and built-tree copies against the same pinned hash.
The APFS export copies file bytes into private, exclusive paths and verifies the
exported path set, sizes, and hashes without applying source metadata. These
collector-only corrections do not revise failed prior receipts, approve the
license review, or establish a complete corresponding-source delivery.
GNU make keep-going mode gathers independent package failures in one attempt;
any failure still leaves the collection nonzero and incomplete and closes no
review gate.

The retained `logcat-mini` commit contains an MIT `LICENSE` (Copyright 2024
wltechblog), although its package recipe declares `GPL-2.0` and names an absent
`COPYING`. The collector binds a command-line-only metadata correction to the
exact recipe declarations, source archive hash, and matching archive/build
`LICENSE` hashes, then verifies the archive is unchanged. This does not create
a grant, alter package sources, or resolve other packages' missing license
files or provenance.

For the exact retained certgen 1.0 and daynightd 2.0.0 inputs, the collector
supplements only the missing build-directory `LICENSE` files with the retained
canonical GPL version 2 text. It first checks the package recipes, source and
build copies of the C files, and daynightd's package README against pinned
hashes. Existing or symlinked destinations are rejected. All original inputs
and installed license bytes are checked again after collection. The receipt
records the existing declarations, checked hashes and copied-workspace scope;
the source recipes and their GPL version declarations are not changed.

This repairs text collection for two targets, not the whole firmware source
delivery. The base Buildroot collection does not cover the separately composed
full-Raptor source closure, and some local packages are omitted from its
manifest. A successful command does not prove that all required sources and
notices have been delivered. Buildroot describes both overcollection and
omissions in its [legal-info guidance](https://buildroot.org/downloads/manual/manual.html#legal-info);
review the actual delivered material rather than treating the exit code as
legal approval.

## Evidence sources

- [Pinned RTL8188FU repository](https://github.com/gtxaspec/rtl8188ftv-wifi/tree/6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06)
- [GNU GPL version 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt)
- [hostap license](https://w1.fi/cgit/hostap/plain/README)
