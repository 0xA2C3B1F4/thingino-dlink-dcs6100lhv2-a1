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
