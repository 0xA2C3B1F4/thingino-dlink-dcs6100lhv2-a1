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

The pinned `ingenic-headers` README at commit
`f573958ebe2a851a6ba0493288b47bc0122daf36` has SHA-256
`236883ff1d99ad12ad83c64e70a3a332859e330a2c9b510035d80e50f7ab523e`.
It describes the headers as collected from public sources and warns that
licensing restrictions may apply. `headers-input.json` therefore records
`license_status: unspecified` and does not authorize redistribution. Public
availability and development use do not by themselves settle the rights to
deliver these headers as corresponding source. The relevant file-level terms
and provenance still need review.

The exported source repository contains the pinned URL and commit in
`headers-input.json`, not the Ingenic header files. The local build fetches
those files from upstream. The missing header grant is therefore not, by
itself, a reason to keep this text-only source repository private. Shipping a
prebuilt firmware image or a source-delivery archive that includes the headers
is a separate question and needs its own rights review.

The installed `867d1182` build sets `PLATFORM=T31` and
`DLINK_OS02G10_IMP_1_1_4=1`. Its pinned `raptor-hal/Makefile` selects
`T31/1.1.6/en` for the normal HAL objects and `T31/1.1.4/zh` for
`hal_ivs.o`. The run's `ingenic-headers.tar` has SHA-256
`65e1b4c73a5cd59e66f31a34e380ca313e99f9c249b105603883e6af1de6c590`;
direct audio and IVS headers matched the pinned cache by bytes. The two
include roots contain 39 regular headers. A full-text search found an Ingenic
Semiconductor copyright line in 37; only the two
`imp_dmic.h` versions lacked one. None of the 39 contained an SPDX identifier
or the searched GPL, BSD, MIT or permission-grant text. The HAL source directly
includes IMP headers from both roots. This is a review of the selected header
directories, not a compiler-generated dependency list or a legal conclusion
about every header. It identifies Ingenic as the named copyright holder for
the files with notices and leaves the right to redistribute the header source
unresolved. A third-party repository's top-level license cannot silently
relicense those files.

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

For source `aff74396532c133805e7f08549fc7bf6ba80d60b`, build A and build B
produced byte-identical full-Raptor component archives, SHA-256
`49789f25426fed7fb867a846c21467fecd6b26886d3e141d37cc877db4d0e1bf`.
All 13 final source-tree IDs in each component's `component.json` match the
retained private source archive's `manifest.json` and the candidate closure
record. This identifies the Raptor source trees used for this component build.
The private archive omits other firmware sources. This comparison does not
approve notices, corresponding-source delivery or binary redistribution.

For the later installed source `867d1182aa2f3586ce35d6709f51463faff010bb`,
the completed-run validator produced byte-identical technical closure
sidecars in two independent invocations. The candidate closure has SHA-256
`18dd2e39be32c8bbe6ef28416e4d2ec571de38f16ecb9ebf4fb4552012fea16b`;
its notice manifest has SHA-256
`220b8eecb2bc8cf6a34566c1bb1ab5acdd1ac17a568a1de9554b2c1d4c43904f`.
The manifest binds Raptor source-lock SHA-256
`bff85f6d1dd168b9152770f688d9e0a354a9e95d3557a59446ed2cda6f40e769`,
which matches the checked-in 13-entry source-delivery inventory. Both sidecars
still mark legal review `not-assessed` and redistribution
`not-authorized-by-this-repository`. They establish technical identity only,
not a complete firmware corresponding-source delivery or a rights decision.

## RTL8188FU

Pinned commit `6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06` has no top-level
`COPYING`, `LICENSE`, `NOTICE`, or README file. Most driver sources carry
Realtek GPL version 2 notices. The `core/crypto` closure also contains Jouni
Malinen and Cozybit files that state BSD terms and refer to a missing README for
the full text.

This repository carries the canonical GPL-2.0-only text and the hostap BSD
license text referenced by those headers. That fixes the missing license-text
delivery in this source repository. The inventories below record the compiled
RTL8188FU files and dependencies for the a091 build, but they do not constitute
a corresponding-source delivery package for a firmware binary. The firmware
gate stays blocked.

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

On 2026-09-23, both inventories were regenerated from the actual build-a and
build-b workspaces for source `a091c826479a206b62f82821e38429273345554c`.
The workspaces were mounted read-only with journal replay disabled; the pinned
build image ran without network access. Both runs produced byte-identical files
that also matched the committed inventories. The linked-source JSON is 109,501
bytes, SHA-256 `1658bfa4fcc4c627b80194815bdb4b34f592e4305b374463812e2db584e0d145`;
the header-dependency JSON is 398,367 bytes, SHA-256
`967927371061ce0bdb6443e73cadd7063f8a3f54509e6fe2f33c6ae9fa9d16bc`.
This binds the recorded 150 translation units and 767 existing dependencies to
the a091 build. It does not resolve the rights and delivery questions above.

The same two inventory commands were run on both clean build workspaces for
the earlier `22989b85e327b090116cf7884a2e616055e24454c` firmware candidate.
The workspaces were mounted read-only without journal replay, using the pinned
builder image `sha256:8abae43028bd1155572c76e268b9b8c5dce4686efe70ff68c0b7c5f9b93b361e`.
Their full-image SHA-256 values remained unchanged after inspection:
`47a7e160aa1101bcd89df4da07451eb23aed3b637322848b8f48918d11816e20`
for build A and `9738e233e25b73ffe04eb66533ffb940331cb0302e62b2a29f4abe5fff2ab621`
for build B. Both produced exactly the committed inventory hashes above,
including 150 translation units, 767 existing dependencies and the installed
module hash. This binds those recorded inputs to that candidate, but
does not close the per-file rights or corresponding-source delivery gate.

That candidate's offline Buildroot collection also retained the pinned
driver source archive, SHA-256
`9705879189d704c274618d550dd19d3f297953f2b8eb4f22fc936345c33b09ba`,
and the two ordered package patches, SHA-256
`c93b9fc9955c4e71e3b5d7884a47a73a05b05e75f40bd776131087c5a193a517`
and `9f2b4ed80caf16517b33f0fd12d559c2d8409cbd9e60f532eb12c58d7ef9cda5`.
After extracting that archive and applying the patches in its recorded order,
all 150 linked driver C files and all 161 recorded driver headers matched the
committed inventories by SHA-256. The patched Makefile had SHA-256
`bb9ddb18062e64108c341cceee88c1c510880ac97ac5caf4db5c74d03e0719d1`,
matching the Makefile in both read-only current-candidate build workspaces.
This verifies the recorded driver-local build inputs. It does not review all
file-level rights or package the kernel and toolchain inputs with the driver.

On 2026-09-23, the same two inventory scripts were run against both clean
workspaces for the host-built MJPEG correction source
`aff74396532c133805e7f08549fc7bf6ba80d60b`. Each ext4 image was mounted
read-only with journal replay disabled under the pinned builder image. The
build-A workspace SHA-256 was
`2d1800c2b0534159dcb19fa7e9730dd495ffe34e616f9b0c5b7bd308e79d1f50`;
build B was
`9a85bd1a5f2b9ef5fd852c901273f45b4d4b48116a2b3bec7da73a81e6513098`.
Both whole-image hashes were unchanged after inspection. Each run produced
byte-identical inventories matching the committed JSON hashes above, including
the same 150 translation units, 767 existing header dependencies and installed
module hash. This binds those recorded RTL8188FU inputs to the new host-built
candidate. A bounded Buildroot `legal-info` collection for this candidate is
recorded below: 327 exported files, with all 326 listed hashes independently
checked. Its receipt says `legal_review_approved: false` and
`publication_authorized: false`. A complete corresponding-source package,
rights review and the firmware gate remain open.

For installed source `867d1182aa2f3586ce35d6709f51463faff010bb`, both
clean ext4 build workspaces were mounted `ro,noload` under the pinned offline
builder image. Each regenerated linked-source inventory matched the committed
150-unit JSON byte for byte, SHA-256
`1658bfa4fcc4c627b80194815bdb4b34f592e4305b374463812e2db584e0d145`.
Each regenerated dependency inventory likewise matched the committed JSON,
SHA-256 `967927371061ce0bdb6443e73cadd7063f8a3f54509e6fe2f33c6ae9fa9d16bc`,
including 767 existing dependencies and 983 absent optional config headers.
The installed `8188fu.ko` hash remained
`2ac80b9f1d3b08080cb55ed8011379fc8872caa73502269cb3d0591b9ff2735f`.
Full workspace hashes were unchanged before and after inspection: build A
`4f87e6360c85a3d6ad55df6e6b7f8407c0a6d1806e7cef7fb4fa18b0b9f97c52`
and build B
`0d2324a8e20cce5fb3413971b1ff11e79ba5374e2e0d9cf71a8b95bb837f0066`.
This binds the recorded driver inputs to the installed candidate. It does not
complete the kernel or toolchain source delivery, per-file rights review, or
firmware redistribution authorization.

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
text and records the exact installed asset comparison below. Control's `Cargo.toml`
declares `MIT`. Its Buildroot recipe now declares `MIT` and
`LICENSE_FILES = LICENSE`, and the source profile copies the root MIT `LICENSE`
to the prepared package. The `22989b85` prepared source contains that recipe
and license file, with SHA-256 `441975d4935e4d47a14b7052cfeb23bd5f5288b3f67332688925809665ffee75`.
Buildroot legal-info does not list this local package, so review its delivery
separately. The installed `a091c826` image and its old legal-info receipt
were not rebuilt or changed.
Overall legal and source-delivery closure remains open.

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

### Installed rejected candidate collection

For source `22989b85e327b090116cf7884a2e616055e24454c`, the offline
collector completed against a copy of the finished build-A workspace. It
exported 327 files totaling 547,326,204 bytes, with tree SHA-256
`10333d2250f30b79e21fbd0487ad04a9ebd5292db8526d25f53824557da7ad6f`.
An independent readback verified all 326 listed file hashes. The accepted
original workspace had SHA-256
`47a7e160aa1101bcd89df4da07451eb23aed3b637322848b8f48918d11816e20`
before and after the run. The collection receipt itself has SHA-256
`5175f29cac57b66adfdd041c65a0a3919be64f3e5c5e63000d76108de26ab4c3`.
The receipt marks collection complete but legal review and publication
authorization false.

Compared with the retained `a091c826` collection, only `buildroot.config`,
the Buildroot version in `host-manifest.csv`, and their checksum index differ.
The selected-package manifest and saved source and license files are otherwise
byte-identical. This is a technical comparison, not a rights determination.
The same eight base warning categories listed below remain.

A separate private Buildroot source supplement for `22989b85` includes the
pinned upstream tree, four overrides, the final config and build receipts.
Its reconstruction matched all 20,000 prepared entries by content, type,
executable bit and symlink target, allowing for private staging permission
changes. Its SHA-256 is
`6a3c697be6ea945893e0500ef6d015b880a5a82474ae69697d4d36b80c532d00`.
The locked 13-source Raptor archive has SHA-256
`3b37cc50f9f1b676bfb3643fe1fd7a6f86505314ddf0fd307ab95fdcd2fc4d8c`;
its source-lock and headers-input digests match this candidate's source. Both
archives remain private technical evidence. They exclude other firmware
sources and do not approve redistribution.

### Host-built MJPEG correction collection

For source `aff74396532c133805e7f08549fc7bf6ba80d60b`, the offline
Buildroot collector completed against an APFS-cloned copy of the clean build-A
workspace from `build-20260923T145048233909Z-aff74396532c`. The original
20 GiB ext4 image had SHA-256
`2d1800c2b0534159dcb19fa7e9730dd495ffe34e616f9b0c5b7bd308e79d1f50`
before and after the run. The collector exported 327 files totaling
547,326,204 bytes, with tree SHA-256
`e78477c36aa2473c69e315e8bea1b99c13829aa2028f6a8713616d3dab5d4e66`.
An independent host pass checked all 326 listed file hashes. The collection
receipt has SHA-256
`417b221075f188d09f4a9f70fe4c6cb7498c2d1ad6257279d74ce413a180be8d`.

Compared byte-for-byte with the preceding `22989b85` collection, only
`buildroot.config`, the Buildroot version field in `host-manifest.csv`, and
their checksum index differ. The recorded Buildroot version changed from
`-g200c9bf6` to `-gac0f7208`; all saved source and license files match.
The same eight base warning categories below remain. The receipt says
`complete: true`, `legal_review_approved: false`, and
`publication_authorized: false`. This is candidate-specific technical source
collection, not a complete corresponding-source package or permission to
distribute firmware. The RTL8188FU and Raptor rights gates remain open.

### Installed Raptor candidate collection

For installed source `867d1182aa2f3586ce35d6709f51463faff010bb`, the
offline collector ran against an APFS copy of the clean build-A workspace.
It checked the original and copy against SHA-256
`4f87e6360c85a3d6ad55df6e6b7f8407c0a6d1806e7cef7fb4fa18b0b9f97c52`
before the run, and the original had the same hash afterwards. The exported
legal-info tree has 327 files totaling 547,326,204 bytes and tree SHA-256
`144bec17fb013dd79b948ae2d6c759958099c4378c789150005598bebdfae99c`.
An independent host `shasum -c` pass accepted all 326 indexed files. Its
`buildroot.config` has SHA-256
`5ed397f53c6c97c51c535c4264402b6409295d5b2f27059f902ba9b5b5ec94f5`,
matching the separately recovered Buildroot config for both clean builds.
The collection receipt has SHA-256
`7dca6d79fe1f2ee48b1a79daf5dc7c0d0017e8f4774712c33bfe65346d54d158`.
It records `complete: true`, `legal_review_approved: false`, and
`publication_authorized: false`. The log retains 18 warning lines, including
the eight base warning categories reviewed below. This is exact-candidate
source collection, not full corresponding-source delivery, a per-file rights
decision, or approval to distribute a firmware image.

The target manifest has 33 package rows and the export has 32 matching
package directories. The one omitted target is `ingenic-lib`, whose recipe
marks its camera-local vendor inputs non-redistributable. The host manifest
has 46 rows and 45 matching directories; the omitted host entry is Buildroot,
covered separately by the private exact-candidate source supplement below.
This directory count is not a source-completeness count: one of the saved
target entries is a prebuilt external toolchain SDK archive, which does not
by itself establish the toolchain's corresponding source. The manifests have
SHA-256
`54d21b058df16f147ecc73159ef602818ddc54dc1ded7d8c0a8cb27495615748`
and `99b8754d690d4b3b4284e92801414ab24399e2018456f7a96dc3c4608b5242cd`
respectively. The 13-source Raptor archive and other local firmware sources
are outside these Buildroot manifest counts. The public tree pins the
`ingenic-headers` upstream URL and commit but contains no copy of those
headers.

## Remaining base-collection warnings

The completed collection for source `a091c826479a206b62f82821e38429273345554c`
retains eight warnings. A bounded review compared its package recipes and
retained archives with the final system image on 2026-09-22. Missing recipe
metadata does not necessarily mean missing license declarations in the source.

| Warning | Observed material and remaining work |
| --- | --- |
| Buildroot source | The collector omits Buildroot and its Libtool patches. A private `a091c826` supplement contains the pinned Buildroot Git archive, four exact overrides, final config and receipts. Its reconstructed tree matched all 20,000 prepared entries by bytes, modes and symlink targets; two runs produced the same archive, SHA-256 `cf5ba3b25a50898ba3b44917a91deb236a33200089985757a027a254b9d3abb8`. It has not been published. |
| External toolchain | The manifest labels the external SDK `unknown` and saves no license files. A private source-input collection now holds the pinned Thingino and Buildroot trees plus the 60-entry GCC 16 / glibc toolchain download cache. That collection has not been approved as a notice or corresponding-source delivery. |
| `ingenic-diag-tools` | `gpio-diag` is installed. The retained source declares AGPL-3.0-or-later and includes the complete license. Both the original source notice and license text are now supplied here. |
| `ingenic-lib` | This profile explicitly uses camera-local inputs with `REDISTRIBUTE = NO`. Their omission from the collected source archives is intentional, not an open-source license-text substitution to make. Firmware redistribution remains unapproved. |
| `ingenic-pwm` | `pwm` and its control script are installed. The four-file pinned utility archive and recipe contain no located license declaration. This remains an unresolved component-specific rights question; another package's MIT license must not be assigned to it. |
| `ingenic-system-libs-neo` | The configuration selects both replacement libraries and the image contains `libalog.so` and `libsysutils.so`. The recipe, source SPDX markers and README identify MIT. This repository supplies the original declaration and standard MIT permission and warranty text in `licenses/thingino-declared-MIT.txt`. The pinned archive has no copyright-holder notice; attribution remains unresolved. |
| `thingino-button` | The executable is installed. Its README identifies the project as MIT, despite the recipe's `unknown` result. This repository supplies that declaration and the standard MIT text in `licenses/thingino-declared-MIT.txt`. The pinned archive has no copyright-holder notice; attribution remains unresolved. |
| `thingino-libubox` | `libubox.so`, `libblobmsg_json.so` and `jshn` are installed. The recipe declares ISC and BSD-3-Clause but names no `LICENSE_FILES`, so the collector saves no license text. This repository now supplies `debian/copyright` and 43 root-level source notices from the pinned archive, including terms beyond the recipe's two labels. The exact linked-file and corresponding-source review remains open. |

The pinned `ingenic-system-libs-neo` commit
`3e1189021e4f273b701b5d8bb8a07b19699333d6` has 17 regular files.
All 14 C and header files contain `SPDX-License-Identifier: MIT`, and its
README identifies MIT. A full-tree text search found no copyright-holder
line or full permission text; there is no standalone license file. This is a
missing text/attribution-delivery question, not the same as the unspecified
`ingenic-headers` grant. Do not invent a copyright holder or borrow one from
another package when preparing notices.

The September 25 text supplement retains both packages' exact README license
declarations, source identities and README hashes. A scan of all 17 regular
files in the replacement-library archive and all four in the button archive
found no copyright-holder notice. The replacement library's 14 C and header
files all contain `SPDX-License-Identifier: MIT`. The supplement supplies
the standard MIT permission and warranty text with its SPDX reference;
it does not fill in a copyright holder or close attribution review. The
Buildroot collection and its historical warnings remain unchanged.

On 2026-09-23, the [upstream `ingenic-pwm` repository](https://github.com/gtxaspec/ingenic-pwm)
still had the exact pinned commit `8d45ebdb97600c7559f5b7eac8e42a9d8c38426b`
at `master`. Its four-file tree showed no license file, and the
[GitHub repository metadata](https://api.github.com/repos/gtxaspec/ingenic-pwm)
reported `license: null`. This is a bounded search for a grant, not a legal
determination or evidence that another project's MIT declaration applies.
The component-specific rights question remains open.

For the installed `867d1182` candidate, the exact final SquashFS image has
SHA-256 `498c68d706df40c2590f467818137980e87897e94dc062587f0d32b02b205325`.
A byte-oriented search of all 481 regular files in its extracted root found
`pwm` or `pwm-ctrl` only in `/usr/sbin/pwm`, `/usr/sbin/pwm-ctrl`, the two
separate kernel PWM modules, and `modules.alias`. No other installed file
contained a literal reference to either command. This bounds a possible
future experiment to omit the userland utility, but cannot rule out dynamic
invocation or establish that omitting it preserves camera behavior. The
installed candidate still contains the utility, so this observation does not
close its rights question or authorize firmware redistribution.

This table is a scoped warning review, not an exhaustive linked-file inventory
or a corresponding-source acceptance decision. The two replacement libraries
must not be classified by their filenames alone as the originally acquired
vendor copies; final-root composition deliberately preserves source-built
support libraries. The actual vendor files retained in the image remain subject
to the separate distribution restriction.

The private Buildroot supplement covers build-system source preparation only.
External toolchain source and notices, other firmware components and package
sources, per-file rights review, and complete source delivery remain open.

For the current candidate's external SDK, a private technical collection now
retains the source-built toolchain inputs: Thingino commit `94d140dc0a458a23eb48a598e633324ea533f97c`
as a Git archive, SHA-256 `c15882cb4b3ce7dc7d0fd4724adcdac4eeb2417f65f2fa531294540fd6898538`;
its Buildroot submodule commit `3c323d714afbc772050fbf665d67700f9a869d79`
as a Git archive, SHA-256 `b875fbbd6ad9d91a7b17d6faa1b59f81aa7775ef498684590b6e1eb34ed8ca13`;
and the validated 60-entry source download cache, SHA-256
`f7317d824370e7c5bfb8eb28a5cccec72d3ce9817f30ae9a867389a050641340`.
The private source-input manifest has SHA-256
`fe2285ce688df1407e9b92cb92bb6cce9d01fdb148a4b63681e49d3ec44f21d4`
and binds these to the locked SDK SHA-256
`9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412`.
This resolves where the exact toolchain build inputs are held, not which notices
must accompany a firmware release or whether distribution is authorized.

For installed source `867d1182aa2f3586ce35d6709f51463faff010bb`, the
Buildroot `.config` was read without journal replay from each clean build's
read-only ext4 workspace. Both files were 143,498 bytes and matched byte for
byte, SHA-256 `5ed397f53c6c97c51c535c4264402b6409295d5b2f27059f902ba9b5b5ec94f5`.
Using those two independently read configs, the pinned Thingino and Buildroot
checkouts, the exact preparation and run receipts, and
`scripts/package_buildroot_source.py`, two offline private source supplements
were produced. Each reconstructed Buildroot tree matched all 20,000 entries
of this run's prepared tree. The two 10-member archives were byte-identical,
7,122,835 bytes with SHA-256
`af7bf292dcb165947ca1bde9cb01cdcbecbf4781cb2089a9664516c77efffa6e`.
An independent archive read confirmed the embedded config and preparation
receipt hashes. The archive contains the pinned Buildroot Git tree, four
ordered overrides, config and receipts; it remains private. Its receipt marks
`legal_review_approved: false` and `publication_authorized: false`. This
ties one build-system source supplement to the installed candidate, not the
full firmware source closure, notice review or redistribution rights.

For the current candidate, the pinned `thingino-libubox` source archive has
SHA-256 `78254b8a4f2b38ca9e7d7aae0b8d5faa2f2cb88ae13ff886577e5aaea401c146`.
Its `debian/copyright` contains an ISC grant and has SHA-256
`f957700a3f105a45e6b34ae4268f63eb9f825d9c22656b95fb5d159419804626`.
The upstream `CMakeLists.txt` includes `base64.c` and `md5.c` in the library.
Their SHA-256 values are
`3378f8210b5bd02d19e9c73caa658b5206e61efef8d2fdedf879b13f5f0ef966`
and `a9cb115e58a1f1a249fa13653223439e8add19f4a23d0e1bb701f233c87c98a6`.
`base64.c` carries ISC, Internet Software Consortium and IBM notices;
`md5.c` carries its author's public-domain statement with fallback terms.
These are source observations, not a completed linked-file or rights review.

On September 25, the source repository added
[`libubox-source-notices.txt`](licenses/libubox-source-notices.txt), copied
from that verified archive. Its 44 sections retain `debian/copyright` and
the consecutive opening comments containing copyright or permission text
from 43 root-level `.c` and `.h` files. Each section records the complete
source file's SHA-256. The collection includes the AVL BSD notices and the
base64 and MD5 terms described above, rather than assigning the package-wide
ISC label to every file. Its 50,122 bytes have SHA-256
`17aaeb022253e9fd8a87b9962403e99548457ad81e5412c06f761ea351e4b035`.
This supplies the source notice texts separately from Buildroot's unchanged
collection. It does not identify the exact linked subset, include external
dependency notices, or complete firmware corresponding-source delivery.

For `ingenic-diag-tools`, the retained six-file source archive has SHA-256
`483394340e74015f2a55761c838f6597df56b4b224803d9f46c888c8e1331ebe`.
Its `LICENSE` was copied byte for byte to
[`jz-diag-tools-AGPL-3.0.txt`](licenses/jz-diag-tools-AGPL-3.0.txt), 34,523 bytes,
SHA-256 `8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef`.
The complete opening comment of `jz_gpio.c` is retained as
[`jz-diag-tools-source-notice.txt`](licenses/jz-diag-tools-source-notice.txt),
SHA-256 `7824dff674b1ff99a28c66c8041b735fcb603a732b14fd7f2ac369dd71b71f40`.
The full C source has SHA-256
`cdaa6a0651b86a1cd70446b5e79ea3a15e8b318d62fb0e367eb502dce74475d2`.
The final image's `usr/bin/gpio-diag` is 9,936 bytes with SHA-256
`180a8f480b0277e91b38df4add97d0d10568b58e4f6a825e9dc8c4ff1dc7748c`.
Its source archive is already present in the private base collection. This
supplement adds the omitted notice texts to the source repository without
changing that collection's manifest, warning history, firmware or release gates.

## Evidence sources

- [Pinned Ingenic headers README](https://github.com/gtxaspec/ingenic-headers/blob/f573958ebe2a851a6ba0493288b47bc0122daf36/README.md)
- [Pinned Ingenic replacement-libraries repository](https://github.com/gtxaspec/ingenic-system-libs-neo/tree/3e1189021e4f273b701b5d8bb8a07b19699333d6)
- [Pinned RTL8188FU repository](https://github.com/gtxaspec/rtl8188ftv-wifi/tree/6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06)
- [GNU GPL version 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt)
- [hostap license](https://w1.fi/cgit/hostap/plain/README)
