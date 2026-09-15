# Installation projects and automation

An installation project remembers input and output paths for one camera. Use
the project commands below, with physical details from the [README](../README.md).
Keep the project's commands and paths when following a link; the linked long-form
examples are a separate workflow and must not override the project's selections.
Existing long commands remain available and use the same validators and writers.

## Select the project once

First complete the [host-tool setup](../README.md#1-install-the-host-tools),
including the active Python environment and Ed25519-capable OpenSSL on PATH.
On macOS this also installs Homebrew Dropbear and squashfs. The container runtime
must be running for builds and container-based provisioning.

Choose private storage on a mounted disk with enough free space. Set these quoted
paths once; keep the quotes when a path contains spaces. The project file must
not already exist. Do not rerun initialization to resume an existing project.

```bash
export DCS6100_DATA_VOLUME="/path/to/mounted-volume"
export DCS6100_CAMERA_ROOT="$DCS6100_DATA_VOLUME/thingino/camera-a"
export DCS6100_BUILD_ROOT="$DCS6100_DATA_VOLUME/thingino/build-a1"
export DCS6100_PROJECT="$DCS6100_CAMERA_ROOT/project.json"
thingino-dlink project init --name camera-a --build-root "$DCS6100_BUILD_ROOT"
thingino-dlink project status
```

The installer creates default private output paths beside the project under
`project-private/`. These cover recovery evidence, session keys, configuration,
provisioning, authorization and evacuated backups. Build commands share the
selected build workspace. Successful operations link accepted outputs to later
commands, so you do not copy paths between stages.

For a new source build, run the local preparation commands in order:

```bash
thingino-dlink local-build prepare --build-root "$DCS6100_BUILD_ROOT"
thingino-dlink local-build bootstrap
thingino-dlink local-build acquire
thingino-dlink local-build recovery-assets
```

## Select the Raptor build

After attaching or acquiring the vendor bundle, the normal command acquires,
builds and includes full Raptor:

```bash
thingino-dlink local-build build-universal --non-interactive --json --events-jsonl
```

`--webrtc` remains a compatible explicit alias for the full-Raptor build. The
project does not store or select a separate media artifact. See the
[source-build details](build.md#build-full-raptor-from-public-sources).

## Capture functional recovery with the project

Use these short commands while `DCS6100_PROJECT` is selected. The README's long
examples use different output paths; do not copy their path overrides into this
project. Links below provide physical details, not replacement command lines.
For an exact-original backup, choose that separate procedure before any updater.
This functional route replaces mtd1/mtd2 first and preserves original mtd0/mtd3/mtd4/mtd5.

After successful `local-build recovery-assets`, the package and manifest are
already linked. Insert a mounted removable FAT32 card and run:

```bash
thingino-dlink stock-recovery uartless-prepare
thingino-dlink stock-recovery uartless-authorize
```

Each command discovers the current card and requires your selection, even if only
one is listed. Review its plan and type the exact target, current device, plan
digest and write phrase. Prepare stages an inert package; authorize requires
`WRITE-MTD1-MTD2` and arms it. Neither command performs the physical boot.

Stop here. Safely eject, insert into the powered-off camera and power on. Confirm
the stock updater's mtd1/mtd2 completion before powering off and returning the card
to the host. A timer or LED color alone is insufficient. See
[boot phases](installation.md#boot-phases-and-failure-handling) for physical details.
Reinsert and identify the current card through the next command's selection:

```bash
thingino-dlink stock-recovery uartless-handoff
```

Review the new plan and confirm the observed `MTD1-MTD2-WRITTEN` result with the
current device and plan digest. Handoff passivates the updater. Stop again:
safely eject, insert into the powered-off camera and power on for the read-only
collector boot. After completed capture, power off and return the card to the
host. Select the newly mounted card and confirm the printed private destination:

```bash
thingino-dlink stock-recovery uartless-validate
```

Validation uses the card's `DCS6100F` collector directory and the project's default
`project-private/recovery` destination. Require `functional_recovery_accepted: true`;
`original_complete_backup_accepted: false` is expected. Successful validation links
the protected readbacks and vendor bundle. Retain a separate private backup off
the card. No output path override or project JSON edit is needed.

These are interactive commands with physical stops. Automation must supply fresh
media selections and the exact confirmations from each media-write operation's own
`--plan-only` result; see [automation](#automation-and-plan-binding). Project status
cannot confirm either boot or trigger the next operation.

## Reuse existing inputs

Select existing material once with `project attach`. For example, a camera with
accepted functional recovery and an inspected universal install set can skip
capture and firmware building:

```bash
export DCS6100_RECOVERY_ROOT="/path/to/accepted-recovery"
export DCS6100_INSTALL_SET="/path/to/inspected-install-set"
export DCS6100_MODEL_PUBLIC_KEY="/path/to/model-public-key.pem"
thingino-dlink project attach \
  --functional-recovery-dir "$DCS6100_RECOVERY_ROOT" \
  --install-set-dir "$DCS6100_INSTALL_SET" \
  --universal-public-key "$DCS6100_MODEL_PUBLIC_KEY"
```

The recovery's `preserved/` directory and the install set's
`thingino-universal.tgb` are selected automatically. Supply
`--preserved-readback-dir` when the readbacks live elsewhere. Attach also accepts
existing sessions, configuration, signed provisioning and authorization. Run
`project attach --help` for the input roles. Exact-original recovery uses
`--recovery-dir` and requires an existing compatible session; it never becomes
functional recovery merely by being attached to a project.

Attachments are path selections, not acceptance certificates. Camera operations
revalidate recovery class, signatures, artifact contents and same-camera binding.
The project rejects inconsistent camera evidence. Explicit operation arguments
that disagree with remembered paths stop the operation; use `project attach`
to deliberately replace an input selection.

## Configure and install

If you are building the universal image locally, run `local-build build-universal`
after accepting recovery. Its install set and model public key are linked to
the next stages. Then run:

```bash
thingino-dlink universal init-session
thingino-dlink universal configure
```

Skip session or configuration creation when you attached existing accepted
inputs. Configuration asks for private Wi-Fi input twice and generates a camera
signer when none is supplied. Secrets stay in protected files and inherited
descriptors; the project stores paths and digests, never raw passwords.

`private-config inspect` reports credential roles without changing or printing
secrets. Supplying `--session-dir` also checks that the saved SSH identity and
service credential match that session. A UARTless provisioning session has no
recovery-AP password; its inspection uses the provisioning host key instead.

On macOS, provision with the repository's JFFS2 container wrapper. Set the
immutable builder image ID from the successful bootstrap/acquire result or build
manifest, and ensure that exact image is available locally. An arbitrary Docker
tag is not a substitute. See the [build host gate](build.md#host-gate).
From the checkout root:

```bash
export DCS6100_BUILDER_IMAGE="sha256:REPLACE_WITH_RECORDED_BUILDER_IMAGE_ID"
export DCS6100_UNSQUASHFS="$(command -v unsquashfs)"
export DCS6100_MKFS_JFFS2="$PWD/scripts/run_container_mkfs_jffs2.sh"
thingino-dlink universal provision \
  --unsquashfs "$DCS6100_UNSQUASHFS" --mkfs-jffs2 "$DCS6100_MKFS_JFFS2"
thingino-dlink universal authorize
thingino-dlink universal stage --plan-only
```

On a host with native `mkfs.jffs2`, set `DCS6100_MKFS_JFFS2` to that executable
instead. Native provisioning does not need the container image variable.

Inspect the stage plan, then run `universal stage` and answer its confirmations.
Follow the returned physical actions. Run `universal handoff` only after observing
the stock updater's mtd1/mtd2 completion. The handoff plan is a separate operation
with its own confirmation. After the final boot, run `thingino-dlink universal verify`.
It discovers the session's mDNS name and uses the retained SSH key and station pin;
there is no `--host` override. `universal evacuate-recovery` also provides
a reviewable plan before copying and removing reserved SD recovery files.

## Reuse one card for camera A and camera B

Keep a separate project for each camera. Reuse the same model-universal install
set and recovery-assets package; a second camera does not require another
firmware build. Its recovery capture, session keys, configuration, provisioning
and authorization must be created for that camera.

After camera A's installation has been independently verified, return its card
to the host and identify the current medium again. If an updater is still
active, complete its explicit handoff/passivation workflow first. Do not remove
an active updater by renaming or deleting it manually to bypass this gate.

On macOS or Linux, archive the old capture using camera A's project. The default
destination is that project's private `archived-capture` directory. The host
destination must be on a different filesystem from the card, with no symlink
components. Windows capture archival is currently unsupported because this
transaction requires private directory permissions and directory fsync; it
stops before copying or removing files there.

```bash
export DCS6100_CAMERA_A_PROJECT="$DCS6100_PROJECT"
thingino-dlink stock-recovery uartless-reuse --project "$DCS6100_CAMERA_A_PROJECT" \
  --whole-device "$DCS6100_SD_DEVICE" --mount-root "$DCS6100_SD_MOUNT" --plan-only
thingino-dlink stock-recovery uartless-reuse --project "$DCS6100_CAMERA_A_PROJECT" \
  --whole-device "$DCS6100_SD_DEVICE" --mount-root "$DCS6100_SD_MOUNT"
```

Review the complete plan and confirm the private destination and write set.
Automation additionally supplies `--non-interactive --json`, `--confirm-plan`,
`--confirm-physical-device`, `--confirm-target`, `--confirm-output-dir` and
`--confirm-write-set COPY-VERIFY-THEN-REMOVE-CAPTURE` from the reviewed plan.
Current card identity, including its filesystem UUID, and capture contents are
checked again before writes. An old plan cannot authorize changed media or data.

The operation copies only `UARTCAP.PSV`, `.uartless-capture-upload.part`,
`DCS6100F`'s fixed collector directories/files and their AppleDouble `._` files.
Known partial files and empty collector directories are preserved too. It checks
all host copies and publishes a private receipt before removing any SD file.
This archive preserves bytes; it does not certify incomplete capture output as
functional recovery. Unknown files inside `DCS6100F` stop the operation.
Recordings and other root files remain untouched and are listed in the result.
An interrupted updater rollback is ambiguous and requires inspection through
its original recovery workflow; capture reuse does not remove it.

If copying stops before a verified receipt exists, all SD source files remain.
Retain the partial host directory and choose a new explicit `--output-dir` for
the next attempt. If removal stops after the receipt exists, inspect the retained
archive, then plan again with `--resume` and the same destination. Confirm that
new plan explicitly. Resume verifies the entire archive and every remaining SD
file before continuing. Existing destinations are never overwritten, and a
changed receipt, copy, source or card stops cleanup.
An SD `.uartless-reuse.pending` marker blocks new capture until cleanup finishes.
The host completion marker is synced before that SD marker is removed. A completed
archive cannot authorize removal of a new capture, even when its package bytes
are identical. Keep the markers and original archive when an operation stops.
For this command only, an explicit new `--output-dir` may select another archive
destination in the same project. It still needs its own plan and destination
confirmation; the previous archive remains intact. Older projects receive the
new default path in memory, without a project write during `--plan-only`.

If `STOCKM3.BIN`/`STOCKM3.OK` remain, use camera A's existing
`universal evacuate-recovery --plan-only` and confirmed evacuation workflow to
preserve that separate checkpoint before new universal staging. Capture reuse
does not delete that checkpoint or touch camera A's validated host recovery.

Select the existing install-set directory, its matching model public key, and
the capture package/manifest from A's completed build outputs. Set these paths
once; they contain no camera-specific recovery or session credentials. Then
switch the selected project explicitly before initializing B:

```bash
export DCS6100_REUSE_INSTALL_SET="/path/from/camera-a/install-set"
export DCS6100_REUSE_PUBLIC_KEY="/path/from/camera-a/model-public-key.pem"
export DCS6100_REUSE_CAPTURE_PACKAGE="/path/from/recovery-assets/uartless-capture-bootstrap.bin"
export DCS6100_REUSE_CAPTURE_MANIFEST="/path/from/recovery-assets/uartless-capture-bootstrap.manifest.json"
export DCS6100_CAMERA_B_PROJECT="$DCS6100_DATA_VOLUME/thingino/camera-b/project.json"
export DCS6100_PROJECT="$DCS6100_CAMERA_B_PROJECT"
thingino-dlink project init --name camera-b --build-root "$DCS6100_BUILD_ROOT"
thingino-dlink project attach \
  --install-set-dir "$DCS6100_REUSE_INSTALL_SET" \
  --universal-public-key "$DCS6100_REUSE_PUBLIC_KEY" \
  --package "$DCS6100_REUSE_CAPTURE_PACKAGE" \
  --package-manifest "$DCS6100_REUSE_CAPTURE_MANIFEST"
```

Follow `uartless-prepare`, `uartless-authorize`, `uartless-handoff` and
`uartless-validate` under camera B's project. Prepare and authorize now reject
stale capture directories and sidecars before reporting a ready plan. Authorize
also checks the expected passive package, and handoff checks the active package.
Handoff does not treat collector output as proof that a physical boot completed.

Continue B's `universal init-session`, `configure`, `provision`, `authorize`,
`stage`, `handoff` and `verify` with its own recovery and credentials. Keep A's
project, private recovery and archive. Do not run `build-universal` again solely
because the card or camera changed. If `DCS6100_PROJECT` is set, ensure it names
the same project as each explicit `--project` selection.

## Automation and plan binding

`--non-interactive` implies JSON output and never prompts. Supply the current
whole-device path and mount root explicitly; they are never recalled from a
project. Identify the card again after every reconnection. Set the following
values from that current inspection, keep them out of saved project settings,
and refresh them before the next media operation. The example uses macOS names;
see the README for other hosts.

```bash
export DCS6100_SD_DEVICE="/dev/diskN"
export DCS6100_SD_MOUNT="/path/to/current-mounted-card"
thingino-dlink universal stage --non-interactive --plan-only \
  --whole-device "$DCS6100_SD_DEVICE" --mount-root "$DCS6100_SD_MOUNT"
```

Review `result.plan` and `result.required_confirmations`. Repeat without
`--plan-only`, providing those exact values. Copy the reviewed digest into the
assignment below; do not obtain confirmations by blindly piping a plan to a write:

```bash
export DCS6100_REVIEWED_PLAN="REVIEWED_PLAN_SHA256"
thingino-dlink universal stage --non-interactive --events-jsonl \
  --whole-device "$DCS6100_SD_DEVICE" --mount-root "$DCS6100_SD_MOUNT" \
  --confirm-target DCS-6100LHV2-A1 --confirm-physical-device "$DCS6100_SD_DEVICE" \
  --confirm-write-set STOCK-MTD1-MTD2-THEN-FINAL-MTD1-MTD3 \
  --confirm-plan "$DCS6100_REVIEWED_PLAN"
```

The shared operation regenerates the plan before writing. A changed operation,
camera binding, artifact, mount identity or reserved installer file rejects the
old confirmation. A plan's empty `write_set` and `writes_performed: false` mean
this invocation wrote nothing; they do not claim the card is disarmed.
Unrelated recordings are excluded from the content digest.
Media identity uses current host evidence, including a volume UUID when
available; it does not prove an immutable hardware serial number.

Automation configuration requires all non-secret selections and an inherited
regular-file `--secrets-fd` containing [confirmed Wi-Fi JSON](installation.md#private-input-through-a-file-descriptor).
Use the project-selected configuration paths rather than the long-form guide's
different output paths. In place of interactive configuration, pass the existing
owner-only file without putting its contents in the command:

```bash
thingino-dlink universal configure --non-interactive \
  --secrets-fd 3 3<"$DCS6100_CAMERA_ROOT/confirmed-wifi.json"
```

Pipes are rejected because a read could block indefinitely. The low-level
`install` command does not support `--non-interactive`. Existing JSON callers
without this flag retain their explicit confirmations, bound to a freshly
validated plan within the call.

## Status and shared operations

`project status` reports the next named action and missing prerequisites. Changed
or missing recorded artifacts produce `needs-review`. A failed or interrupted
operation retains `started`; status asks for inspection and never retries a
write. A stale completed stage also stops the next-action advice at inspection,
even when later input paths are still selected. Completion records describe host operations, never an observed physical
boot. There is no automatic resume or global last-camera selection.

Session tracking covers the camera binding, retained keys and other session
inputs. Verification may create a missing station pin from the retained host key;
it checks and records that derived output separately. This does not invalidate
earlier session-dependent stages. An identity change still requires review.
Records from the older whole-session tracking format may require review once.

To resolve stale host evidence, inspect the named stage and its files first.
Restoring the exact recorded files makes the original record current again.
Alternatively, explicitly attach independently reviewed replacement outputs:

```bash
export DCS6100_REVIEWED_CONFIG="/path/to/reviewed/session-bound-config"
thingino-dlink project attach --private-config-dir "$DCS6100_REVIEWED_CONFIG"
thingino-dlink project status
```

Attach replaces the old advisory producer record for a selected session, private
config, provisioning pair, or authorization directory. It reports those names in
`superseded_host_records`; it does not claim those operations ran again. Supply
both `--provisioning` and `--provisioning-data` together for a replacement pair.
Other producer artifacts, including signing keys, must remain unchanged or also
be explicitly selected. Downstream stale records still require their own review,
and current camera/session/signature validators still run before use.

Attach never clears an interrupted operation or a media-write record. Preserve
that project while inspecting outputs and physical state. If continuing with
reviewed external inputs, initialize a separate project and attach them there;
retain the old project as evidence. No project operation retries a physical write,
recreates configuration or keys, or supplies a media confirmation.

The shared project, recovery, camera setup and media modules accept typed inputs
without a CLI parser or terminal prompts. They return the common installation
result and emit structured phase events. CLI adapters handle choices, private
input and presentation. Recovery and restore commands keep their existing
contracts.

`--json` preserves the result schema and adds structured error details and missing
inputs. Interrupted operations report an uncertain outcome. `--events-jsonl`
writes static phase events to stderr and the final result to stdout. Events
contain no credentials, camera identifiers, private paths or percentage claims.

Advanced callers can pass a command-to-path JSON mapping through the `--paths`
option of `project init`. Conflicting project flags, environment selections or build roots stop
initialization. Host fixture tests cover the short and long call chains, signed
artifact validation and stale plans. They do not constitute camera or physical
removable-media acceptance.
