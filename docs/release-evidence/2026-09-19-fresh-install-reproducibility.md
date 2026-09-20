# September 19 fresh-install build

## September 20 host regression scope

The recovery-gate regression uses two synthetic snapshots with identical model,
layout and boot bytes but changed stock configuration. It verifies that old
recovery rejects new readback, a complete new snapshot produces new identity
and authorization bindings, and the internally consistent old snapshot still
passes offline. This demonstrates why host success cannot establish live
camera freshness. It does not establish the physical HMAC failure's cause.

The following host checks passed after documenting that boundary:

```sh
python3 -m unittest tests.test_recovery_gate tests.test_install_actions tests.test_user_cli tests.test_universal_artifacts tests.test_stage1_build tests.test_capture_reuse -q
python3 scripts/check_docs.py
git diff --check
```

Result: 123 tests and the 108-file documentation check passed. No camera or SD
write was part of these checks. The existing capture archival command leaves
old authorization files in place, while inconsistent-media quarantine accepts
only its specific checkpoint mismatch. Neither command is yet a validated
general recovery route from the observed pre-write HMAC failure.

## September 19 build record

Source commit: `c4cf3aea7d37bca3704f60d851c461ca0afd2029`.
Data mode: `initialize`. This candidate targets new-user installations, not
migration from previous development firmware. The repository remains private;
this record does not authorize firmware redistribution.

The normal `local-build build-universal --build-count 2 --data-mode initialize`
command completed successfully. Two clean base builds and two independent
Raptor component builds produced byte-identical complete firmware. Neither
component build used compiled-component cache entries. Both schema-2 install-set
inspections passed.

An independent verifier rehashed all 32 files in the 16 reported artifact pairs.
It checked the complete-firmware comparison, both inspections, source commit and
data mode against the build receipt and run manifest. The comparison report has
SHA-256 `dac0922647722222a88a7a80827ae678f13917617fff1430490338c92387759f`.
The completed CLI receipt has SHA-256
`dd1b15b0c8cb7db29fa14f13a5a7b70ceaca67f817216ab4488d25439879f857`.
Private build evidence retains those documents and the independent verification.

| Artifact | Size in bytes | SHA-256 |
| --- | --- | --- |
| Signed universal bundle | 7950417 | `c72210570908e6a24a7aa9af0c07b751cc9d26b724542c877f4ca6565432b84f` |
| System SquashFS | 6365184 | `93f0f2b4b15e68012f423a01690cd75bc2376f8482ebcb052c1a10640688e7a1` |

Padding that SquashFS with `0xff` to the fixed 6619136-byte system span produces
SHA-256 `11599ef71c93ae500635497aea00d28b45b19f08a53407cd81becefb69a3c35d`.
This is an expected readback value, not a camera readback result.

Reading `etc/onvif.json` directly from each packed SquashFS confirmed
`adv_enable_media2=true` and two profiles. This establishes the model image's
configuration, not live SOAP behavior or a camera's provisioned configuration.

The new schema-2 Raptor candidate ID is
`e2575f1c1b204c7bacc851c1e30b0ae4a16d062f943eafd0b09ca24e9f2a287c`.
No previous camera acceptance results were imported. No SD or camera write
occurred during this build. Its 20 candidate checks, physical first-install and
recovery requirements, second-camera and host-platform acceptance, licensing
review and complete corresponding-source delivery remain separate requirements.

## Subsequent installation and bounded camera checks

The same candidate was subsequently installed with explicitly authorized data
initialization. Independent kernel and full system-span readback matched the
built artifacts. Protected boot, vendor and factory spans matched the retained
same-camera backups. Nine runtime file hashes matched the packed image, and
running executables were checked separately. The private readback receipt has
SHA-256 `77b965954c500f47e136b1de94e1d8153b7c365540ba910683de9e9a763564fe`.
Mutable data is not claimed to remain byte-identical after boot.

The operator confirmed first-Listen audio on both Main and Substream after
microphone Off/On, without Reload or a second Listen click. This is operator
audibility evidence, not a measured latency or long-running stability result.
It is not stock-state first-installation or power-interruption evidence.

Live Media1 and Media2 each returned two profiles and valid stream/snapshot
URIs. Both authenticated JPEGs decoded at the expected resolutions; negative
authentication tests rejected access. RTSP over interleaved TCP decoded three
H.264 frames per stream at 1920x1080 and 640x360. The subsequent ONVIF privacy
check is recorded below. RTSP receipt SHA-256:
`148b7d195f6b7285c2871e3236b3aec34a558438dec6b1fcf1df9c379bc49d0f`.

A real Safari 27.0 session returned from Information to Preview without Reload
and switched from Main to Substream WebRTC. Audio packet counters increased
on each connection. These counters do not prove audible playback or decoded
video quality. A subsequent visible-video check is recorded below;
network-loss and audible background recovery remain open.

A bounded test held one slow MJPEG reader and disconnected one incomplete HTTP
request while issuing three rounds of three concurrent authenticated API reads.
All 15 total baseline, concurrent and recovery reads returned valid HTTP 200
JSON. Maximum measured request/body latency was 0.690 seconds, excluding TLS
establishment. Socket counts returned to baseline after closing the clients;
boot identity and selected daemon PIDs did not change. The test session was
logged out and invalidation checked. Receipt SHA-256:
`b920de2552f55c8195208532cb5166a5e4fbb6d098fd8604654e55db0196adc8`.

The candidate ledger now has ten passed checks: two snapshots, RTSP, ONVIF,
a real browser session, bounded concurrency, both MJPEG streams, bounded
resource observations and a configuration round trip. Ten checks remain open.
No broader pass is inferred from these observations. Private raw receipts are retained outside
the exported source tree; they are not part of the public source artifact.

The ONVIF privacy test compared three JPEGs from each endpoint before, during
and after protection. All six protected responses exactly matched the compiled
static privacy JPEG. All twelve unprotected responses decoded at their normal
main/substream dimensions. Original privacy, microphone and speaker states
were restored and read back. Earlier preflights stopped without writes on an
unknown heartbeat state. Direct daemon reads agreed that protection was off;
the transient unknown readings remain a separate diagnostic observation.

The configuration test saved OSD font size from 48 to 47, confirmed both saved
and live values through fresh API and native Raptor reads, then restored 48.
ROD and RVD confirmed matching apply transactions. The entire returned OSD
configuration matched its pre-test value. This is not rendered-text appearance,
reboot persistence or full settings-field coverage.

A later Safari observation showed visible main/sub/main WebRTC video with
advancing camera OSD timestamps. Substream remained live after a short
background-tab interval without Reload. Playback was muted throughout, so this
does not establish audible background recovery. Test tabs were closed afterward.
A separate test session stayed authenticated at 0, 10, 20 and 40 seconds and
was revoked by logout. Cold-boot first login, clock changes and idle expiry
remain separate requirements.

## Installer correction after these checks

Full kernel-log review found an SD FAT unclean-unmount warning. It does not
alone prove filesystem corruption. The installer synced the card but rebooted
without unmounting it. Source commit `f99b4cfd9754e54115092d27a6782a650f6b38f3`
adds checked sync and a normal `/card` unmount before reboot, with a terminal
failure if either fails. The verified NOR image is not rolled back on cleanup
failure. No filesystem repair was performed on the mounted card.

The commit passed 986 host tests with one existing skip. A native C test ran
the actual cleanup statements with syscall doubles for success, sync failure
and unmount failure. Two targeted MIPS installer builds were byte-identical,
with bootstrap SHA-256
`58a195e10432b382bd14f399e890085092e90580b03b2533f7bd377d024097b6`.
These builds are not a new signed release bundle, full-firmware reproducibility
proof, or physical execution of the correction. A subsequent complete build is
recorded below. At that point the installed firmware was still `c4cf3ae`.
The later corrected-package installation is recorded separately below.

## Complete build with the installer correction

Source `cd516efd1b3cf0bf3d581e6561b4c90b17f4e71a` completed two clean full builds
in initialize mode, including two independently compiled Raptor components.
All 16 artifact pairs were byte-identical, both schema-2 inspections passed,
and no compiled-component cache was used. Independent verification rehashed
both this pair and the preceding `c4cf3ae` pair, 64 files in total, before
comparing their artifact identities. Signed-package validation also passed.

The new universal bundle is 7950417 bytes with SHA-256
`dc02700568df0c0ceb1a78c75fdc91d7c00bd20989a1dd4e7c54272c63e1801a`.
The packed stage1 bootstrap matches the targeted build hash above, and its
`sbin/init` hash is
`ccb0768cb3b77e143e1b8e843df12b585c2fbc5da7e6935bc7c38a0c00e48e05`.
The compiled sync-failure, unmount-failure and successful-unmount markers are
present. Their presence is not physical execution evidence.

The final runtime kernel and complete system image are byte-identical to the
installed `c4cf3ae` artifacts. Only the SD bootstrap container, stage1 SquashFS,
install-set manifest and signed universal bundle changed. Existing runtime
observations therefore concern the same immutable image bytes, but do not
prove new installation, restored mutable configuration or the SD cleanup path.
No candidate passes were automatically transferred. Technical closure manifests
explicitly leave legal review unassessed.

## Corrected-package installation and remaining FAT warning

The operator completed the two-phase `cd516ef` installation with explicitly
approved data initialization. The host verified six staged files again after
remounting the card, both before its first boot and after the phase handoff.
The preceding transaction's recovery checkpoint was validated against its old
authorization and provisioning, archived with its backup and independently
read back before retiring that pair from the card. No signature or checkpoint
validation was bypassed.

After the new boot, pinned management verification passed with an empty write
set. Independent readback matched all 14 expected hashes: kernel, full system,
three protected spans and nine runtime files. Private readback receipt SHA-256:
`e31d88c829a2ad1c9ff07b19f9378720bfe19359b9fd32a37670787bff5a6020`.
The transaction's authorization and provisioning files were absent from the
card afterward. This does not establish stock-state first installation,
audibility on this boot or successful execution of the final unmount syscall.

The new boot still emitted the FAT unclean-unmount warning. Private log receipt
SHA-256: `a93de8bba3a267957700a24187d3e3eee7632672e1230024c1179c07c692da46`.
The retained build's Linux 3.10.14 source explains an important limitation:
`fs/fat/inode.c:504` returns from `fat_set_state` without changing disk state
if `sbi->dirty` was already set when mounting. `fat_put_super` calls that same
function to clear the flag at unmount. Consequently, a successful normal
unmount cannot clear a pre-existing dirty flag. That source file has SHA-256
`b09443e6debe5d9e30cbaf0edbcb6d408215394b6033b54082181b20bbf17fa5`.
It was inspected from the actual build workdisk mounted read-only with
`ro,noload`, with container networking disabled.

The warning alone therefore proves neither failure nor success of the new
unmount path. The origin of the dirty flag is unresolved. A Mac read-only
filesystem check before staging had passed, but no raw FAT state-byte evidence
was captured at each installation boundary. Do not clear the flag blindly or
suppress the warning. Clean-baseline, phase-specific filesystem evidence and
installer completion evidence are still required before accepting this fix.

A subsequent controlled runtime check found recording and timelapse disabled,
verified the SD identity and found no filesystem users. Checked sync and normal
unmount succeeded; a second SSH connection confirmed the volume was unmounted
in the same boot. The FAT32 boot-sector state byte was `0x01` both before and
after unmount, and both 512-byte boot-sector hashes were identical. This directly
confirms persistence of the existing dirty flag across a successful runtime
unmount on this card. It does not prove how the flag first arose or establish
execution of the earlier installer's unmount. No filesystem repair was made.

## Later normal boot and recorder acceptance

After a separately approved, backed-up correction of the card's existing FAT
state flag, a normal runtime boot no longer emitted the FAT warning. Independent
readback confirmed that only the approved state byte changed. This was a
card-specific recovery action, not an installer fix or a general instruction to
clear filesystem flags. It does not replace the phase-specific installer checks
above. The operator subsequently reported that the requested audio test worked;
no latency measurement or new stream-by-stream detail was supplied in that reply.
The earlier explicit first-Listen confirmations on both streams remain retained.

On this boot, both recording channels started and stopped through Control.
Fresh writer-state reads confirmed recording during each test and closed files
after stopping. The two resulting H.264 MP4s decoded fully without FFmpeg errors:
142 main-stream frames at 1920x1080 and 147 substream frames at 640x360. Their
downloaded bytes matched camera-side SHA-256 values. Host-side authenticated
HTTPS listed both closed files, and final readback confirmed both recorders off.
This is start/stop acceptance, not retention, deletion, timelapse, recording
audio, power-loss recovery or long-running stability acceptance.

Initial file-list checks using curl inside the camera timed out. A separate
read-only probe of the actual list implementation completed in about 13 ms,
and host-side HTTPS checks subsequently passed without running camera-local curl.
The diagnostic process is a possible load confounder; the exact timeout cause
was not established. No production timeout or firmware change was made on this
evidence. Prefer host-side requests for device acceptance measurements.

The retained kernel log still contained the complete earlier boot log. Only five
codec setup/stop messages had been added, with no new media, SD I/O, OOM or oops
errors. Baseline CPU reset-PC and USB clock-gate diagnostics were retained, not
claimed repaired. This observation covers the completed tests, not endurance.

The `cd516ef` candidate ledger records four directly observed checks: both
snapshots, recorder start/stop and kernel media-error review. It remains
incomplete. Rehashing the actual artifacts confirmed unchanged system bytes and
final-kernel SHA-256
`faf28040e59639fb9818b3cc12ead8c1c5a0f8109d01ab693fb579797dd3e383`.
All 14 shared identities in the two installed-readback records also match.
Earlier runtime evidence remains applicable to those immutable bytes without
being relabelled as a new installation or mutable-configuration test. Private
receipts, diagnostic sources and operator reports are retained outside the export.

## September 20 restored-stock reinstall stopped before final writes

A separate restored-stock run reached D-Link V1.02.02 startup and answered an
unauthenticated RTSP OPTIONS request with 200 OK. This establishes service
responsiveness, not stock video/audio acceptance or an explanation of the
operator's earlier repeated-click report.

The unchanged `cd516ef` install set passed host validation. Six staged SD files
and six post-handoff files passed independent unmount/remount readback. Passive
UART confirmed the stock updater's first-phase completion. On the second boot,
Stage 1 verified the stage-2 snapshot but stopped at
`STAGE1 FAIL camera_authorization_hmac`, before `final_write_phase`. The first
phase's mtd1/mtd2 writes had completed; final system/data/kernel writes had not
started. This run is not successful installation evidence.

The host plan used earlier recovery and authorization inputs. Its validation
does not observe current camera flash. Authorization binds full protected
partition bytes, including writable stock configuration. At this point, changes
after stock boot were only a hypothesis; the subsequent capture below tests it. The public
reuse instructions now exclude a subsequent return to stock, and stage/handoff
plans explicitly report `live_camera_verified: false` within
`camera_binding_evidence`. These changes do not bypass device HMAC validation
or establish a successful retry. The camera recovery and fresh-install gate
remain open. A FAT unclean-unmount warning was also observed during this boot
despite successful host eject; its cause is unresolved.

### Fresh capture confirms stale authorization binding

The existing UARTless capture package passed the current package and manifest
validator. Its first updater boot completed, the host performed the supported
handoff, and the second boot reported `COLLECT COMPLETE host_validation_required`.
The host then validated both partition copies, preserved readback, package
binding and vendor material into a new private recovery directory. It accepted
functional recovery, not an exact-original backup. The earlier recovery remains
retained separately.

Comparing the two validated preserved sets found identical stock mtd0 bytes,
510 changed bytes in stock mtd4 and 78,022 changed bytes in stock mtd5, with
unchanged partition sizes. Both camera identity and authorization-key derivation
changed. Independently calculating the archived authorization binary's HMAC
with its digest field zeroed matched the earlier capture's key and rejected
the fresh capture's key. This establishes that the archived authorization is
stale for the newly captured protected bytes. It does not identify which stock
process changed each byte or establish successful reinstallation.

The old UARTless provisioning session is also bound to the earlier identity;
`universal provision` rejects using it with the fresh capture. The supported
new-user route requires a fresh session and newly generated private setup,
followed by provisioning and authorization against that capture. This evidence
does not approve reusing or manually editing old session bindings. Physical
installation and recovery acceptance remain open.
