# Build

The repository pins the upstream source, toolchains, container bases, package
revisions, profile inputs, and local patches. A model-universal build combines
those inputs with the catalog-locked media files acquired from a matching A1
camera. The public build path always composes the full Raptor media stack.

Start with the [README installation procedure](../README.md#install-from-a-public-checkout).
It is the canonical per-camera command sequence. This page explains the build
inputs, identity checks, and release boundaries.

## Host gate

Python 3.11 or newer and a running Docker-compatible container runtime are
required. The editable package installs the pinned `pyserial` dependency used
by the UART backup command. The guided builder currently requires Apple Silicon
macOS. Install and start Docker Desktop first. The recovery root packager also
requires LLVM `clang` and `ld.lld`, `mksquashfs`, and `unsquashfs` on the host.
On Apple Silicon, install Homebrew `llvm`, `lld`, `squashfs`, `dropbear`, and
`openssl@3`. The signers invoke `openssl` from `PATH` and require Ed25519.

```bash
brew install llvm lld squashfs dropbear openssl@3
export PATH="$(brew --prefix openssl@3)/bin:$PATH"
openssl version
make check
```

`make check` validates the public tree, source lock, Markdown links, Control API
contract, release ledger, and host tests. Firmware building and device
acceptance have their own gates.

## Create the local build location

Run these commands from the repository root. Put the build workspace beside the
checkout on a mounted writable volume, not inside the repository. The default
reserves capacity for one clean build. A two-build reproducibility run needs
twice that space.

```bash
export DCS6100_DATA_VOLUME="/path/to/mounted-volume"
export DCS6100_BUILD_ROOT="$DCS6100_DATA_VOLUME/thingino/build-a1"
export DCS6100_CAMERA_ROOT="$DCS6100_DATA_VOLUME/thingino/camera-a"
export DCS6100_RECOVERY_ROOT="$DCS6100_CAMERA_ROOT/functional-recovery"
thingino-dlink local-build prepare --build-root "$DCS6100_BUILD_ROOT"
thingino-dlink local-build status
```

The commands create mode-0700 private workspaces with separate public-input
caches and per-run directories. They reject a symlinked or unowned non-empty
build root. Later commands use the explicit build root first, then the
`DCS6100_BUILD_ROOT` value, then the recorded prepare location. Conflicting
paths stop before a build starts.

## Acquire public inputs

Bootstrap verifies the exact Thingino and Buildroot checkout, source tree,
submodules, package pins, remote, and clean state. It records the immutable
container image ID together with the checkout and lockfile identities.

```bash
thingino-dlink local-build bootstrap
thingino-dlink local-build acquire
thingino-dlink local-build recovery-assets
```

Every downloaded archive is size-bounded and checked against the SHA-256 in
`sources.lock.json`. Extraction rejects path traversal, escaping links,
unexpected duplicate paths, and unsupported member types. A changed upstream
release asset is never accepted as a substitute for a pinned source.

`recovery-assets` builds the collector packages and both fixed kernel sets in
network-disabled workspaces. The two collector builds must be byte-identical
before packaging. This phase does not contact a camera or write an SD card.
Keep the printed capture package and manifest paths for the separately
authorized recovery procedure.

## Build full Raptor from public sources

After recovery assets and a matching camera vendor bundle are available, run:

```bash
export DCS6100_VENDOR_BUNDLE="$DCS6100_RECOVERY_ROOT/vendor"
thingino-dlink inspect-vendor-bundle \
  --vendor-bundle-dir "$DCS6100_VENDOR_BUNDLE"
thingino-dlink local-build build-universal \
  --build-root "$DCS6100_BUILD_ROOT" \
  --vendor-bundle-dir "$DCS6100_VENDOR_BUNDLE"
```

`build-universal` prepares the closed model image and then compiles the full
Raptor source closure against that image's target libraries and the pinned
Thingino GCC 16 SDK. `--webrtc` is an explicit alias for this same full build.
There is no separate media archive or prebuilt WebRTC input.

The `components/raptor/source-build-lock.json` lock records each public URL,
base commit, base tree, reconstructed tree, patch digest, and license-file
digest. The five reconstruction patches under `patches/raptor-full-source/`
are applied offline after all base trees pass verification. `headers-input.json`
pins the unchanged public Ingenic headers required by the compile. Its pin does
not grant a license for an SDK, firmware binary, or headers distribution.

The source build produces `rvd`, `rhd`, `rsd`, `ric`, `rad`, `rod`, `rmr`,
`raptorctl`, `rwd`, and the two RSS libraries. ROD statically links the locked
libschrift source. The component also installs the locked Ubuntu Regular font
and the applicable Ubuntu Font Licence and libschrift ISC texts. Compilation
runs without network access as the unprivileged builder user in a task-owned
ext4 workspace. No camera or SD device is mounted in that container.

Composition checks every ELF for the target ABI, 4 KiB LOAD alignment, allowed
dynamic dependencies, and absent RPATH/RUNPATH. It checks the service files,
configuration, font license files, source provenance, packed image size, and
read-back inventory. A source, dependency, or read-back failure stops before
install-set packaging.

The model image contains no camera credentials. Its Raptor services remain
disabled until camera-bound provisioning supplies the local network and
authenticated RTSP settings. Provisioning changes the private data image, not
the shared universal firmware bytes.

## Build identity and reproducibility

The build result records every input and output hash, size, effective
configuration, source revision, toolchain, builder image, selected media
profile, and Raptor source provenance. Keep the install set, logs, manifests,
and model signing key pair together in private storage.

The normal build runs once. Use `--build-count 2` with a fresh prepared workspace
when collecting reproducibility evidence:

```bash
thingino-dlink local-build prepare --build-count 2
thingino-dlink local-build build-universal \
  --build-root "$DCS6100_BUILD_ROOT" \
  --vendor-bundle-dir "$DCS6100_VENDOR_BUNDLE" \
  --build-count 2
```

Both complete install sets, firmware members, normalized inventories, and
provenance manifests must match byte for byte. Matching Control binaries or
matching base images alone do not close this gate.

## Provision the model build for one camera

The universal firmware has no station credentials. Use the same camera-bound
session for each step below:

```bash
thingino-dlink universal init-session \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --output-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --config-output-dir "$DCS6100_CAMERA_ROOT/install-config"
thingino-dlink universal configure \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --output-dir "$DCS6100_CAMERA_ROOT/install-config"
```

`configure` collects Wi-Fi through hidden prompts or the documented inherited
file descriptor. It creates the management credential, API key, and camera
authorization signer in private storage. Do not put secrets in command
arguments, environment variables, manifests, logs, or Git.

Create the private provisioning pair and bind it to the exact firmware:

```bash
thingino-dlink universal provision \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --universal-bundle "$DCS6100_INSTALL_SET/thingino-universal.tgb" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY" \
  --private-config-dir "$DCS6100_CAMERA_ROOT/install-config" \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --signing-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pem" \
  --unsquashfs "$(command -v unsquashfs)" \
  --mkfs-jffs2 "./scripts/run_container_mkfs_jffs2.sh" \
  --output "$DCS6100_CAMERA_ROOT/provisioning.private.zip" \
  --data-output "$DCS6100_CAMERA_ROOT/provisioning.data.jffs2"
thingino-dlink universal authorize \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --preserved-readback-dir "$DCS6100_RECOVERY_ROOT/preserved" \
  --universal-bundle "$DCS6100_INSTALL_SET/thingino-universal.tgb" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY" \
  --provisioning "$DCS6100_CAMERA_ROOT/provisioning.private.zip" \
  --provisioning-data "$DCS6100_CAMERA_ROOT/provisioning.data.jffs2" \
  --provisioning-public-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pub" \
  --session-dir "$DCS6100_CAMERA_ROOT/provisioning-session" \
  --signing-key "$DCS6100_CAMERA_ROOT/authorization-signing/ed25519.pem" \
  --output-dir "$DCS6100_CAMERA_ROOT/authorization"
```

The resulting JFFS2 image is exactly 1,507,328 bytes. `initialize` creates the
first private data state. `preserve` requires the complete existing data region
to match before and after the system update and does not write the provisioning
image. Inspect the install set and require the matching data action before SD
staging.

## Keep build, source, and device gates separate

`make check` and a successful source build prove only host and source
properties. They do not authorize an SD write, prove an installed image, or
close a firmware distribution gate. The public release still requires:

- two byte-identical complete builds from the documented inputs;
- the full Raptor corresponding-source and notice review;
- the exact candidate management, media, browser, and resource matrix;
- physical write interruption, corruption, reinstall, recovery, and same-camera
  stock-restoration tests;
- power-loss and slow-card provisioning acceptance;
- a second independently recovered A1 camera; and
- physical removable-media acceptance for every claimed host platform.

There is no supported public firmware download. Keep the generated install set
private until the release ledger says the firmware scope is ready. See
[status](status.md), [testing](testing.md), and the [third-party notices](../third_party/NOTICE.md).
