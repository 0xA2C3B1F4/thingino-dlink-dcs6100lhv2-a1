# Build

The repository pins upstream source, toolchains, container bases, package
revisions, profile inputs, and local patches. A device-specific build combines
them with owner-acquired camera files kept in a private workspace.

Start with the [README installation procedure](../README.md#install-from-a-public-checkout).
This page explains build identities, advanced inputs, and the legacy personalized
path. The README is the canonical per-camera command sequence.

## Host gate

Python 3.11 or newer and a running Docker-compatible container runtime are
required. The editable package installs the pinned `pyserial` dependency used
by the UART backup command. The guided builder currently requires Apple Silicon
macOS. Install and start Docker Desktop first. The project installer creates
the build location and manages project images and containers, but it does not
install or start the host container runtime. The recovery root packager also
requires LLVM `clang`/`ld.lld` and `mksquashfs`/`unsquashfs` on the host. On
Apple Silicon, install Homebrew `llvm` and `lld`; the guided build selects the
Homebrew compiler instead of the incompatible Apple Clang MIPS driver. Install
Homebrew `squashfs`, `dropbear`, and `openssl@3` as well; `dropbearkey` creates
the per-camera SSH host key for UARTless provisioning. The model and camera
signers invoke `openssl` from `PATH` and require Ed25519 support.

```bash
brew install llvm lld squashfs dropbear openssl@3
export PATH="$(brew --prefix openssl@3)/bin:$PATH"
openssl version
```

```bash
make check
```

This checks the explicit public tree, source lock, Markdown links, Control API
contract, and host tests. Firmware building and device acceptance have their
own gates.

## Create the local build location

Run the following commands from the repository root. This single non-secret
variable puts the workspace beside the checkout on the same writable volume;
override it once when another volume is required. The path does not need to
exist. The default reserves capacity for one clean build:

```bash
export DCS6100_BUILD_ROOT="$(cd .. && pwd)/dcs6100-build"
thingino-dlink local-build prepare
```

The command creates a mode-0700 workspace with separate public-input caches and
per-run directories and stores a hash-bound pointer to that workspace in the
installer state. Later commands resolve an explicit `--build-root` first, then
`DCS6100_BUILD_ROOT`, then the prepare-recorded pointer. They fail if explicit
and environment paths disagree. The default workspace requires 80 GiB free and
rejects symlinked or unowned nonempty paths. Public inputs use the shared cache;
private inputs use the per-run workspace. Normal installation uses one clean
build. Use `--build-count 2` with both `prepare` and `build-universal` only when collecting
byte-identical reproducibility evidence; that workspace requires 160 GiB. Check
an existing workspace without modifying it:

```bash
thingino-dlink local-build status
```

`ready_to_build` remains false until the checkout is clean, the locked source
identity still matches, the capacity gate passes, and Docker is reachable.
`prepare` creates the workspace. `bootstrap` and `acquire` populate its public
inputs, and `build` compiles the firmware. The complete guided build currently
runs on Apple Silicon macOS and creates task-owned ext4 Buildroot workspaces on
the selected volume.

Acquire the immutable Thingino/Buildroot checkout and build the pinned local
ARM64 builder image:

```bash
thingino-dlink local-build bootstrap
```

This is the networked public-bootstrap phase. The checkout is verified by exact
commit, tree, remote, submodule, package pins, and clean state before reuse. The
built image is recorded by immutable Docker image ID together with the project
HEAD, source-lock digest, Containerfile digest, and platform. A missing image is
built automatically; an existing unowned tag or a tag that changed after its
receipt was recorded is rejected. This phase reads locked public inputs.

Download and validate the remaining Rust archives, use the immutable builder
image to install the pinned Rust 1.95.0 ARM64 Linux toolchain into the external
cache, and fetch the exact Ingenic glibc 2.16 toolchain source:

```bash
thingino-dlink local-build acquire
```

Every downloaded archive is streamed into the mode-0700 public-input cache,
size-bounded, verified by the SHA-256 in `sources.lock.json`, and activated
atomically. Extraction rejects path traversal, escaping links, unexpected
duplicate paths, and unsupported member types; reruns rehash both cached
archives and extracted trees. The Rust source extractor keeps only the
`library/` and `vendor/` roots required by the offline build and normalizes
three explicitly verified, byte-identical README case aliases.

The Ingenic source is bound to its exact commit, tree, and origin in a bare Git
repository. Its case-distinct headers cannot be represented safely in the
default macOS filesystem, so `acquire` exports the exact tree to a validated
tar archive without extracting it on the host. Each clean firmware build
extracts that archive inside its case-sensitive ext4 workspace. This phase
adds locked public inputs to the shared cache. Private camera data enters later
in the per-run workspace. The Ingenic source keeps its upstream licensing
terms when cached locally.

`local-build build` constructs the Thingino MIPS toolchain from pinned source.
Upstream replaces its same-named release assets, so a release URL is not an
immutable input even when an old checksum remains in this repository. Build the
public recovery inputs before asking the camera for any private input:

```bash
thingino-dlink local-build recovery-assets
```

`recovery-assets` fetches the recursive source closure of the Buildroot
`toolchain` target for the exact Thingino and Buildroot commits, validates its
pinned inventory, and builds the ARM64-hosted MIPS SDK with networking disabled.
The recipe makes the SDK relocatable without running the unrelated firmware
package graph or camera-kernel target; the pinned Linux 3.10.14 tarball remains
the source of its matching userspace kernel headers. The generated SDK is
packed with the locked deterministic tar and gzip recipe, then its archive must
match the SHA-256 in `sources.lock.json` before it can enter the firmware
download cache. A changed upstream release asset is never trusted or used as a
fallback. The one networked toolchain-source fetch mounts only the verified
public checkout and its public cache workspace; no private configuration,
camera file, or generated credential enters that container.

The same command prepares the patched source, creates and validates the locked
Buildroot download cache, and performs the bounded offline collector builds.
Its result contains exact paths for `collector-kernel.uimage`,
`collector-linux.config`, and `jzmmc_v12.ko` for the no-write UART transport.
It additionally produces an all-MTD-read-only mtd2-boot kernel/config, minimal
functional collector root, stock-U-Boot package, and package manifest for the
explicit UARTless alternative. Both kernel sets are rebuilt in a second clean
workspace and must be byte-identical before packaging. Building either set never contacts a camera or
stages an SD card, so the command's immediate write set is empty. The UARTless
manifest separately records that a future authorized physical boot writes
mtd1/mtd2 and does not preserve an original complete six-partition backup.
Follow the distinct command sequences in [installation](installation.md).

### Legacy personalized continuation

The following `configure/build` pair is the older device-personalized path.
For the preferred universal flow, skip to the next section and use
`build-universal`, then per-camera provisioning. Do not mix the two paths.

For a legacy build, configure the validated camera-specific inputs in a
terminal and build with the recorded plan:

```bash
thingino-dlink local-build configure
thingino-dlink local-build build
```

`configure` validates all selected non-secret inputs before asking for Wi-Fi.
The SSID and passphrase are requested twice with hidden terminal input. The
command derives the WPA PSK locally, creates the session-bound installation
configuration and a separate confirmation file, generates the management and
WebUI API credentials, and saves paths plus the selected data action in a
mode-0600 settings file. Wi-Fi details enter through the hidden prompts.
`build` loads the recorded plan, so the normal command needs no private path
arguments.

`build` revalidates and reuses the source-built Thingino toolchain and immutable
Buildroot download cache produced by `recovery-assets`; advanced workflows that
already possess accepted private inputs can let `build` create those caches on
demand. Toolchain compilation and each requested clean firmware build run
without networking in separate ext4 workspaces. An explicit two-build run must
export byte-identical base artifacts. The command then creates the private
final-root, applies the source-bound Raptor RWD overlay, builds both fixed-layout
kernels, packages the schema-2 install set, and runs `inspect-install-set`. The
output JSON names the private run and `install_set_dir`. The persistent overlay installs only the
Raptor delta; base-owned Control, WebUI, uhttpd, init, and Prudynt files are
preserved and hash-checked instead of being restored from the artifact.

`build` ends with a locally inspected install set. Public release acceptance,
SD-card staging, and camera installation are separate steps.

The firmware container and `npm run build` use the same WebUI build script.
`webui/firmware-bundle.json` locks every installed asset's size and SHA-256,
including navigation icons and their license. `npm run check` verifies this
manifest against a fresh build and exercises the rootfs installation hook.
When changing WebUI sources, review and update this manifest and the matching
source-profile hashes before publication. Do not update expected hashes from
an unexplained failed build or disable the checks. The same manifest is checked
before and after installation into the firmware rootfs; the base-build report
records the verified inventory. The installation regression uses GNU coreutils,
as the firmware container does. On macOS, put Homebrew's coreutils `gnubin`
directory on PATH when running the WebUI tests.

The same prepared source includes two bounded offline kernel builds. The
collector uses `collector-kernel.fragment` and
`scripts/run_macos_collector_kernel_build.sh`; its exact volatile U-Boot
command line supplies the RAM-disk address and byte length and marks all six
physical partitions read-only. The stock restorer uses
`stock-restore-kernel.fragment` and
`scripts/run_macos_stock_restore_kernel_build.sh`; it marks only mtd1, mtd2,
and mtd3 writable. Their kernels, effective configs, and matching MMC modules
are private recovery inputs, not public firmware artifacts.

## Build WebRTC from public sources

After `local-build prepare`, select the validated vendor bundle and WebRTC:

```bash
export DCS6100_VENDOR_BUNDLE="/path/to/accepted/vendor-bundle"
thingino-dlink local-build build-universal --webrtc \
  --build-root "$DCS6100_BUILD_ROOT" \
  --vendor-bundle-dir "$DCS6100_VENDOR_BUNDLE"
```

To build and validate only the component, without camera inputs or a firmware build:

```bash
thingino-dlink local-build build-raptor --non-interactive --json --events-jsonl \
  --build-root "$DCS6100_BUILD_ROOT"
```

Both commands use the same source acquisition, toolchain and offline build core.
A selected installation project remembers the resulting `raptor_rwd_artifact`
and supplies it to `build-universal`; the explicit `--webrtc` flag always selects
the source-build/cache path even when a project remembers an older artifact.
The explicit alternative remains `--raptor-rwd-artifact /path/to/accepted.tar.gz`.
It cannot be combined with `--webrtc` on the same command line.

`components/raptor-rwd/source-build-lock.json` binds public base commits, Git
base trees, reconstruction patch SHA-256 values, accepted trees, licenses and
transitive dependencies. The three reconstruction patches reproduce the
original Compy, Raptor and IPC trees exactly. Raptor's existing `0001` and `0002`
runtime patches are then applied in order and the final tree is checked.
Compy's slice99, datatype99, interface99 and metalang99 headers and mbedTLS's
framework are acquired before compilation. Vendored monocypher and cJSON are
bound by the common library tree. No source lookup occurs during compilation.

The builder uses the existing source-built, hash-locked Thingino GCC 16 SDK
and the validated ARM64 builder container. SDK acquisition and construction
use the existing installer infrastructure. The Raptor compile container has
networking disabled. It produces rwd, RSS, Compy and static LTO mbedTLS from
sources in a task-owned 2 GiB ext4 image on the build volume, using the same
Docker loop-mount approach as the existing macOS build runners. This preserves
the SDK's Linux links without extracting it through an APFS bind mount.
Docker uses a privileged container to mount that regular image file inside its
Linux VM. Only the task workspace and validated read-only inputs are mounted;
no host camera or SD device is passed. The compile itself runs as the unprivileged
`builder` user, and the wrapper unmounts the image on exit. The retained workspace
path is included in the result. Host-side cleanup checks the invocation's unique
container name and ownership label before removing a container left by an
interrupted Docker client.
The component validator checks every ELF's 4 KiB LOAD alignment,
absence of RPATH/RUNPATH and allowed dynamic dependencies. The archive contains
only rwd, two RSS libraries, configuration and provenance. It contains no old
Prudynt, Control, uhttpd or WebUI binaries. The legacy RAM artifact remains valid
under its original member contract.

Source and artifact caches bind the full recipe, builder image identity,
toolchain SHA-256 and source trees. Changed or incomplete cache entries fail
closed. A failed component build cannot continue into firmware packaging.
JSON results include the artifact, hashes, ELF audit, retained run directory
and build log. `--events-jsonl` emits phase progress on stderr.
Licensing, distribution rights and physical acceptance remain separate gates.

## Build one reusable A1 firmware

The model-universal path is separate from the older personalized configure
path. The default profile accepts public source plus seven catalog-locked
stock media files acquired read-only from the owner's matching camera:

```bash
export DCS6100_VENDOR_BUNDLE="/path/to/accepted/vendor-bundle"
thingino-dlink local-build build-universal \
  --build-root "$DCS6100_BUILD_ROOT" \
  --vendor-bundle-dir "$DCS6100_VENDOR_BUNDLE"
```

Keep the vendor directory local because redistribution is not cleared. The
public catalog fixes the accepted runtime bytes. A second camera acquisition may be used as
compatibility evidence only; it must normalize to the same catalog identities
and must not select different model bytes. When `--signing-key` is omitted, the
command creates or reuses one stable Ed25519 key pair beneath the build root's
adjacent private directory and reports both paths and the public key identity.
Changing that key intentionally changes the signed bundle.

The default `camera-stock-media-open-source-support-v1` profile uses
source-built Prudynt and init, the camera-acquired TX-ISP and OS02G10 modules,
the matching stock IQ file and IMP 1.1.4 library, and the pinned open-source
`libalog`, `libsysutils`, and `libaudioProcess` replacements. The final kernel
keeps the IPv6-enabled T31 ABI used by the validated stock modules. This
provides RTSP and MJPEG without requiring the legacy
private media closure. `--media-closure-dir` selects the older accepted closure,
and `--webrtc` builds and adds the Raptor component from locked public sources.
An existing accepted `--raptor-rwd-artifact` remains supported as an alternative.

The command has no arguments for camera recovery, WPA, recovery session,
management credential, API key, hostname, or SSH identity. It creates a
`model-universal` install set and `thingino-universal.tgb` with an empty data
member. The immutable system has a locked root account, no private Wi-Fi/API/
SSH files, and non-executable network-facing startup scripts. The normal path
runs one clean build; `--build-count 2` adds byte-identical reproducibility
evidence for release work.

## Provision and install the universal result

Follow the complete [README procedure](../README.md#5-create-private-configuration)
with the printed `install_set_dir` and `model_signing.public_key`.
The same per-camera session must be used for every following step:

1. `thingino-dlink universal init-session` validates functional recovery and
   creates a local camera-bound session without a recovery AP.
2. `thingino-dlink universal configure` collects Wi-Fi privately and generates
   the configuration and authorization signer.
3. `thingino-dlink universal provision` builds the signed private audit sidecar
   and exact-span JFFS2 data image.
4. `thingino-dlink universal authorize` binds that camera, session, firmware,
   stage 2, sidecar, and data image.
5. `thingino-dlink universal stage` validates and writes the confirmed SD card.
6. After the stock updater completes, `thingino-dlink universal handoff`
   makes the selector inert before the final Stage-1 boot.
7. `thingino-dlink universal verify` checks the installed camera over the
   network, without UART. Media and physical controls are separate acceptance.

On macOS, provisioning uses `scripts/run_container_mkfs_jffs2.sh` with
`DCS6100_BUILDER_IMAGE` set to the recorded immutable builder image ID.
The [project workflow](installer-projects.md#configure-and-install) shows the
short commands with those tool arguments and session-based verification.
Linux can supply a reviewed regular `mkfs.jffs2` binary instead.
The data image is built twice with little-endian 32 KiB erase/256-byte page
geometry, root ownership, fixed time, 1,507,328-byte padding, and a CRC scan.
Both the image and audit sidecar contain secrets and remain mode 0600.

The universal data action is `initialize`; this is not a preserve-settings
update. Use new output paths for a changed firmware/provisioning set and keep
the old recovery evidence. If an earlier SD backup/checkpoint is present, follow
[card reuse](installation.md#reusing-an-installation-card) before restaging.

The host build does not authorize physical writes or establish a supported
public release. The current single-camera result and remaining gates are in
[status](status.md).

## Legacy personalized configure inputs

Press Enter to accept a displayed default. A custom path may be absolute or
relative to the directory where the command is run; it is resolved and saved
as an absolute path. Do not create empty placeholder files or directories.

| Question | Default | What it must contain and where it comes from |
| --- | --- | --- |
| Private input root | `${DCS6100_BUILD_ROOT}-private` | A directory owned by the current user with mode 0700. Generated credentials, Wi-Fi files, and the saved plan are written here. |
| Vendor bundle directory | `<private-root>/vendor-bundle` | `vendor-bundle.private.json` and the closed library set acquired from this camera's stock mtd3 through the read-only vendor acquisition workflow. `inspect-vendor-bundle` must accept it. |
| Media closure directory | `<private-root>/media-closure` | An accepted private acquisition output containing `media-closure.private.json` and its exact hash-locked C1 runtime files. |
| Recovery session directory | `<private-root>/recovery-session` | The private recovery workflow's session containing `host/identity.pub`, `host/service.credential`, and the validated session metadata. It binds SSH and management access to this build. An empty directory is invalid. |
| Raptor RWD artifact | `<private-root>/raptor-rwd.tar.gz` | A separately prepared, reviewed source-built archive matching `components/raptor-rwd/raptor-lock.json`, its patches, member list, checksums, and checkout supervisor. |
| Data action | `initialize` | `initialize` creates the first private stock-mtd3 backup/checkpoint and erases the new data region; `preserve` requires the complete data region to remain byte-identical; `factory-reset` explicitly erases only that data region. |
| Station Wi-Fi SSID and credential | none | Enter each twice through the hidden prompts. SSID is 1-32 UTF-8 bytes. The WPA credential is either an 8-63-byte UTF-8 passphrase without control characters or an exact 64-character hexadecimal raw PSK. The command derives the PSK from a passphrase and preserves a supplied raw PSK. |

The last two Wi-Fi entries are confirmation, not a request for a PSK. On a
mismatch, nothing is generated. A successful run creates:

- `<private-root>/install-config/`, the sealed configuration bound to the
  selected recovery session;
- `<private-root>/expected-wpa.conf`, a separate mode-0600 confirmation file;
  and
- `<private-root>/local-build-settings.private.json`, which stores the paths
  and selected data action.

`configure` validates the vendor bundle, media closure, recovery session, and
Raptor artifact against their manifests. If one is missing or invalid, the
command identifies it and stops before collecting Wi-Fi. A clean clone supplies
the source and validators; each operator supplies the accepted device-specific
artifacts.

For automation, ordinary non-secret paths and the data action may be supplied
with the documented `local-build configure --help` options. Wi-Fi must still
arrive through an inherited descriptor of at least 3. Its JSON object has
exactly `ssid`, `passphrase`, `confirmation_ssid`, and
`confirmation_passphrase`; do not use a temporary command-line file, process
substitution, environment variable, or argument for this content.

Advanced and existing workflows may bypass the saved plan by supplying all
six private paths together. Partial explicit input is rejected:

```bash
export DCS6100_LEGACY_PRIVATE_ROOT="/path/to/accepted/personalized-inputs"
thingino-dlink local-build build \
  --vendor-bundle-dir "$DCS6100_LEGACY_PRIVATE_ROOT/vendor-bundle" \
  --media-closure-dir "$DCS6100_LEGACY_PRIVATE_ROOT/media-closure" \
  --private-config-dir "$DCS6100_LEGACY_PRIVATE_ROOT/install-config" \
  --expected-wpa-config "$DCS6100_LEGACY_PRIVATE_ROOT/expected-wpa.conf" \
  --session-dir "$DCS6100_LEGACY_PRIVATE_ROOT/recovery-session" \
  --raptor-rwd-artifact "$DCS6100_LEGACY_PRIVATE_ROOT/raptor-rwd.tar.gz" \
  --data-mode initialize
```

In this advanced form, `--expected-wpa-config` is a mode-0600 canonical
`wpa_supplicant.conf`-style file containing one network, a derived 64-hex PSK,
and no plaintext password. It must be a different file/inode from
`install-config/wpa_supplicant.conf` while describing the same SSID, PSK, and
security policy. Prefer `local-build configure`, which creates both correctly.

## Prepare upstream source

The low-level source commands below remain available for development and
diagnosis. Choose empty destinations outside the repository. The guided local
builder owns equivalent paths beneath its generated workspace; do not mix
manually prepared trees into an installer-owned run.

```bash
python3 scripts/source_checkout.py validate-lock
python3 scripts/source_checkout.py fetch \
  --destination "$DCS6100_BUILD_ROOT/thingino-sources"
python3 scripts/source_checkout.py verify \
  --checkout "$DCS6100_BUILD_ROOT/thingino-sources"
python3 scripts/source_prepare.py \
  --checkout "$DCS6100_BUILD_ROOT/thingino-sources" \
  --destination "$DCS6100_BUILD_ROOT/thingino-prepared"
```

Only `fetch` uses the network. It permits the pinned public HTTPS Git sources.
Verification checks the exact commits, trees, submodule identity, remotes, and
clean state. Preparation exports the verified trees without Git metadata,
checks every profile input hash, and applies the ordered patches offline.

## Local private and model inputs

The legacy personalized build also needs:

- `/lib/libimp.so`, `/lib/libalog.so`, `/lib/libsysutils.so`, and
  `/lib/libaudioProcess.so` acquired from the owner's matching DCS-6100LHV2 A1
  stock mtd3;
- the separately validated media closure required by the selected profile;
- unique local WebUI, RTSP, ONVIF, Wi-Fi, API, and SSH credentials;
- the pinned Rust 1.95.0 source and host toolchain;
- the pinned Ingenic glibc toolchain; and
- the architecture-specific builder image and package cache.

Keep every item in ignored private storage. Do not place a credential in argv,
the environment, a public manifest, or a build log.

The vendor bundle validator is available without building:

```bash
python3 -m installer.user_cli inspect-vendor-bundle \
  --vendor-bundle-dir "$DCS6100_VENDOR_BUNDLE"
```

The low-level `python3 -m installer inspect-vendor-bundle` form remains
available for scripts that consume its compact JSON output.

The acquisition step mounts physical mtd3 read-only, copies the seven named
media paths, and validates them against the profile catalog. These exact inputs
prevent a public upstream sensor build from silently replacing the model's
catalog-locked working sensor stack.

## Builder image

The repository has digest-pinned ARM64 and AMD64 container definitions. For an
ARM64 host:

```bash
docker build --platform linux/arm64 \
  -f containers/thingino-builder-arm64.Containerfile \
  -t dcs6100-thingino-builder:arm64 .
```

Container creation is networked and lockfile checked. Firmware compilation is
network-free after the source, toolchains, package cache, and local vendor build
site are ready. The container builds the Rust Control service and the static
WebUI from their locked source inputs.

## Optional Raptor overlay

Select `--webrtc` on `local-build build-universal` to build the component from
locked public sources. An accepted external archive can still be supplied with
`--raptor-rwd-artifact`. Source, library and configuration identities must pass
the component checks; an arbitrary older firmware archive is not a substitute.


`prepare-final-root` produces the matched-media RTSP/MJPEG base root. The
universal builder invokes `components/raptor-rwd/build_persistent.py` before
split-kernel or stage-1 packaging with the accepted base root, provenance,
validated component and `--static-rwd-tls --split-mtd3`. No manual overlay step
is needed.

The overlay preserves and hash-checks current base-owned Control, WebUI,
uhttpd, init, and Prudynt files. It must retain the Prudynt initialization
failure guard when enabling `PRUDYNT_RAPTOR_RING=1`. The universal root keeps
network service startup disabled until the private provisioning overlay enables
it. The final root must still fit the fixed 6,619,136-byte system region.

The overlay must install `usr/bin/rwd`, `etc/init.d/S96rwd`, and
`etc/raptor.conf`; retain the bounded static DTLS-SRTP closure; switch Prudynt
to the lifecycle-safe RSS publisher; and record the source-bound
`raptor_rwd.source_provenance_sha256`. The overlay audit and final stage-1
validator must both pass for the WebRTC profile. Their absence is valid only
when the manifest records the matched-media base profile and no Raptor overlay.

## Output gates

### Source component validation, 2026-09-08

A clean standalone public export of development commit
`2c54c50c2403f738a295130b20ad463aa109b41a` completed the `local-build build-raptor`
command above on an ARM64 macOS host with Docker. All ten source trees were
acquired from their public repositories; the SDK had been built from locked
sources in the same task. Its archive matched
`9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412`.
The component compile ran offline with builder image
`sha256:a3815c8c4fe4f67545a0d0c8b8c70f42f507c849d57bd22e5febd850fb83fbd7`.

The validated component archive was 364,243 bytes, SHA-256
`3fc181ad0f680f48208633960506ef83176f0b53cbf84961326c4a1ac91369a9`.
Stripped rwd, RSS common and RSS IPC were respectively 545,000, 141,508 and
26,180 bytes. All three passed the 4 KiB LOAD, MIPS ABI, dynamic dependency
and no-RPATH/RUNPATH checks. No shared mbedTLS, IMP or HAL dependency remained.
An immediate CLI repeat and a `build-raptor --project` call both returned
`cached: true` with the same archive digest. `project status` reported the
operation completed, and the project linked the artifact to `build-universal`.
The dev check passed 784 tests; the standalone public check passed 769 tests.

This evidence covers component construction, validation, cache reuse and
project linking. It does not establish full universal firmware construction,
two-build reproducibility, target ABI execution or physical camera behavior.
The source-component overlay checks that its required libraries, including
`libatomic.so.1` and `/lib/ld.so.1`, exist inside the resulting image and rejects
a missing dependency. Release and distribution gates below remain open.

A build manifest must bind every input and output hash, size, effective
configuration, source revision, toolchain, and selected media profile. When
Raptor is selected, it must also bind the Raptor source provenance and the image
validators enforce its service, configuration, library isolation, and Prudynt
RSS-publisher closure.

Two isolated clean builds must produce byte-identical release components and
normalized inventories. A successful compile is still not an installable
release. Licensing, artifact closure, browser, camera, removable-media,
readback, interruption, and recovery checks remain separate.
