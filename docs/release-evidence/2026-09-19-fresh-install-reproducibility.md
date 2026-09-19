# September 19 fresh-install build

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
H.264 frames per stream at 1920x1080 and 640x360. ONVIF privacy and broader
protocol acceptance remain open. RTSP receipt SHA-256:
`148b7d195f6b7285c2871e3236b3aec34a558438dec6b1fcf1df9c379bc49d0f`.

A real Safari 27.0 session returned from Information to Preview without Reload
and switched from Main to Substream WebRTC. Audio packet counters increased
on each connection. These counters do not prove audible playback or decoded
video quality. Network-loss recovery and background suspension remain untested
for this candidate.

A bounded test held one slow MJPEG reader and disconnected one incomplete HTTP
request while issuing three rounds of three concurrent authenticated API reads.
All 15 total baseline, concurrent and recovery reads returned valid HTTP 200
JSON. Maximum measured request/body latency was 0.690 seconds, excluding TLS
establishment. Socket counts returned to baseline after closing the clients;
boot identity and selected daemon PIDs did not change. The test session was
logged out and invalidation checked. Receipt SHA-256:
`b920de2552f55c8195208532cb5166a5e4fbb6d098fd8604654e55db0196adc8`.

The candidate ledger has five passed checks: two snapshots, RTSP, a real browser
session and bounded concurrency. Fifteen checks remain open. No broader pass
is inferred from these observations. Private raw receipts are retained outside
the exported source tree; they are not part of the public source artifact.

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
proof, or physical execution of the correction. The installed firmware remains
the earlier `c4cf3ae` candidate. A matching release build and physical installer
test are still required; the earlier candidate's passes must not be silently
transferred to a different artifact.
