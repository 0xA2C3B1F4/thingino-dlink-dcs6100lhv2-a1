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
thingino-dlink workflow-preflight --json \
  --mode production-build --data-volume "$DCS6100_DATA_VOLUME"
thingino-dlink local-build prepare
thingino-dlink local-build bootstrap
thingino-dlink local-build acquire
thingino-dlink local-build recovery-assets
```

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

On macOS, provision with the repository's JFFS2 container wrapper. Set the
immutable builder image ID from the successful bootstrap/acquire result or build
manifest, and ensure that exact image is available locally. An arbitrary Docker
tag is not a substitute. See [builder prerequisites](build.md#builder-image).
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

Pipes are rejected
because a read could block indefinitely. Legacy `install` does not support
`--non-interactive`. Existing JSON callers without this flag retain their
explicit legacy confirmations, bound to a freshly validated plan within the call.

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
input and presentation. Legacy development and restore commands keep their
existing contracts.

`--json` preserves the result schema and adds structured error details and missing
inputs. Interrupted operations report an uncertain outcome. `--events-jsonl`
writes static phase events to stderr and the final result to stdout. Events
contain no credentials, camera identifiers, private paths or percentage claims.

Advanced callers can pass a command-to-path JSON mapping through the `--paths`
option of `project init`. Conflicting project flags, environment selections or build roots stop
initialization. Host fixture tests cover the short and long call chains, signed
artifact validation and stale plans. They do not constitute camera or physical
removable-media acceptance.
