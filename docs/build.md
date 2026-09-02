# Build

The repository pins upstream source, toolchains, container bases, package
revisions, profile inputs, and local patches. A device-specific build combines
them with owner-acquired camera files kept in a private workspace.

## Host gate

Python 3.11 or newer and a running Docker-compatible container runtime are
required. The guided builder currently requires Apple Silicon macOS. Install
and start Docker Desktop first. The project installer then creates the build
location and manages project images and containers.

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
exist. The default reserves capacity for two independent clean builds:

```bash
export DCS6100_BUILD_ROOT="$(cd .. && pwd)/dcs6100-build"
thingino-dlink local-build prepare
```

The command creates a mode-0700 workspace with separate public-input caches and
per-run directories and stores a hash-bound pointer to that workspace in the
installer state. Later commands resolve an explicit `--build-root` first, then
`DCS6100_BUILD_ROOT`, then the prepare-recorded pointer. They fail if explicit
and environment paths disagree. The workspace requires 160 GiB free and
rejects symlinked or unowned nonempty paths. Public inputs use the shared cache;
private inputs use the per-run workspace. `local-build build` requires the
default two-build plan; a
`--build-count 1` workspace is only for a bounded investigation and still
requires 80 GiB. Check an existing workspace without modifying it:

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
immutable input even when an old checksum remains in this repository. The
builder fetches the recursive source closure of the Buildroot `toolchain`
target for the exact Thingino and Buildroot
commits, validates its pinned inventory, and builds the ARM64-hosted MIPS SDK
with networking disabled. The recipe makes the SDK relocatable without running
the unrelated firmware package graph or camera-kernel target; the pinned Linux
3.10.14 tarball remains the source of its matching userspace kernel headers.
The generated SDK is packed with the locked deterministic tar and gzip recipe,
then its archive must match the SHA-256 in `sources.lock.json` before it can
enter the firmware download cache. A changed upstream release asset is never
trusted or used as a fallback. The networked toolchain-source fetch mounts the
verified public checkout and its public cache workspace.

Configure the private build inputs in a terminal, then build with the recorded
plan:

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

`build` prepares the locked source, creates the source-built Thingino toolchain,
then generates and validates the immutable Buildroot download cache. Toolchain
compilation and both clean firmware builds run without networking in separate
ext4 workspaces. The two firmware builds must export byte-identical base
artifacts. The command then creates the private final-root, applies the
source-bound Raptor RWD overlay, builds both fixed-layout kernels, packages the
schema-2 install set, and runs `inspect-install-set`. The output JSON names the
private run and `install_set_dir`. The persistent overlay installs only the
Raptor delta; base-owned Control, WebUI, uhttpd, init, and Prudynt files are
preserved and hash-checked instead of being restored from the artifact.

`build` ends with a locally inspected install set. Public release acceptance,
SD-card staging, and camera installation are separate steps.

The same prepared source includes two bounded offline kernel builds. The
collector uses `collector-kernel.fragment` and
`scripts/run_macos_collector_kernel_build.sh`; its exact volatile U-Boot
command line supplies the RAM-disk address and byte length and marks all six
physical partitions read-only. The stock restorer uses
`stock-restore-kernel.fragment` and
`scripts/run_macos_stock_restore_kernel_build.sh`; it marks only mtd1, mtd2,
and mtd3 writable. Their kernels, effective configs, and matching MMC modules
are private recovery inputs, not public firmware artifacts.

## What configure asks

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
| Station Wi-Fi SSID and passphrase | none | Enter each twice through the hidden prompts. SSID is 1-32 UTF-8 bytes and a WPA-PSK passphrase is 8-63 UTF-8 bytes without control characters. The command derives the 64-hex PSK with the standard WPA PBKDF2 rule. |

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
thingino-dlink local-build build \
  --vendor-bundle-dir /private/device/vendor-bundle \
  --media-closure-dir /private/device/media-closure \
  --private-config-dir /private/device/install-config \
  --expected-wpa-config /private/device/expected-wpa.conf \
  --session-dir /private/device/recovery-session \
  --raptor-rwd-artifact /private/device/raptor-rwd.tar.gz \
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
export DCS6100_BUILD_ROOT="$(cd .. && pwd)/dcs6100-build"

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

## Local device-specific inputs

The normal build also needs:

- `/lib/libimp.so`, `/lib/libalog.so`, and `/lib/libsysutils.so` acquired from
  the owner's matching DCS-6100LHV2 A1 stock mtd3;
- optional `/lib/libaudioProcess.so` when its catalog identity matches;
- the separately validated media closure required by the selected profile;
- unique local WebUI, RTSP, ONVIF, Wi-Fi, API, and SSH credentials;
- the pinned Rust 1.95.0 source and host toolchain;
- the pinned Ingenic glibc toolchain; and
- the architecture-specific builder image and package cache.

Keep every item in ignored private storage. Do not place a credential in argv,
the environment, a public manifest, or a build log.

The vendor bundle validator is available without building:

```bash
python3 -m installer inspect-vendor-bundle \
  --vendor-bundle-dir /path/to/private/vendor-bundle
```

The acquisition step mounts physical mtd3 read-only, copies the four named
library paths, and validates them against the profile catalog.

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

## Final Raptor overlay

`prepare-final-root` produces the accepted private base root. It is not the
final firmware candidate for the current WebRTC profile. Before split-kernel
or stage-1 packaging, run `components/raptor-rwd/build_persistent.py` with the
accepted base root and provenance, the source-built Raptor artifact, its exact
digest, and `--static-rwd-tls --split-mtd3`.

The overlay must install `usr/bin/rwd`, `etc/init.d/S96rwd`, and
`etc/raptor.conf`; retain the bounded static DTLS-SRTP closure; switch Prudynt
to the lifecycle-safe RSS publisher; and record the source-bound
`raptor_rwd.source_provenance_sha256`. The overlay audit and final stage-1
validator must both pass. A base root without these files and provenance is
not a firmware or final-root candidate.

## Output gates

A build manifest must bind every input and output hash, size, effective
configuration, source revision, toolchain, and Raptor source provenance. The
image validators enforce the fixed kernel, bootstrap, system, data, and
partition limits plus the `rwd` service, configuration, library isolation, and
Prudynt RSS-publisher closure.

Two isolated clean builds must produce byte-identical release components and
normalized inventories. A successful compile is still not an installable
release. Licensing, artifact closure, browser, camera, removable-media,
readback, interruption, and recovery checks remain separate.
