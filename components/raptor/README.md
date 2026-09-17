# Full Raptor source build

`source-build-lock.json` pins the public Git inputs for the full Raptor media
stack. Each entry records a public HTTPS repository, an exact base commit and
tree, the final tree expected after applying the listed repository patches, and
the license file digest.

The image contains one built-in Motion alert at
`/usr/share/raptor/audio/motion.pcm`. The build creates the fixed 250 ms,
16 kHz mono PCM16LE asset deterministically. RAD accepts only the `motion`
clip identifier, one to three repeats, and at most ten seconds of total PCM.
Its deterministic SHA-256 is
`507c4135606518c8158e2dcb28fe76170df1f7e9b9ad9aef4b267787b7f22fad`.
This does not expose general file playback.

The closure contains nine media sources, the two unchanged Mbed TLS sources
used by Raptor's RWD support, and unchanged libschrift 0.10.2 for ROD. Five
media trees differ from their public bases;
their flattened binary patches live in `patches/raptor-full-source/`. The other
seven trees have empty patch lists and must match their locked base trees exactly.

The flattened Raptor patch includes the RWD audio-direction correction. It
inherits session-level SDP direction unless audio overrides it and answers
`recvonly` with `sendonly`, `sendonly` with `recvonly`, and `inactive` with
`inactive`. An omitted direction remains `sendrecv`, preserving bidirectional
clients. RWD does not create an outgoing audio transport for send-only or
inactive offers, or an incoming audio backchannel for receive-only or inactive
offers. The native SDP regression fixture exercises the actual parser
and answer generator without requiring the firmware SDK. Browser acceptance,
camera packet delivery and audible playback remain separate checks.
The offline component build runs this native fixture before cross-compilation;
a regression stops the build before producing a deployable component.

`scripts/raptor_full_source_manifest.py` is the host-only preparation and
verification helper. It binds the prepared media inputs by file content,
executable mode, and symlink target; checks every public base tree and license;
rejects local/private provenance in generated patches; and reconstructs each
final Git tree in a separate clean checkout. The verified media input directory
and its matched-inventory lock are preparation evidence, not public build inputs.

`local-build build-universal --webrtc` uses this closure after preparing the
unprovisioned base image. It compiles `rvd`, `rhd`, `rsd`, `ric`, `rad`, `rod`,
`rmr`, `raptorctl`, `rwd` and their two RSS libraries against that build's SDK
and target libraries. ROD statically links libschrift, so it adds no freetype,
libpng or libschrift shared-library dependency. The component also installs the
locked 353824-byte Ubuntu Regular font as `/usr/share/fonts/default.ttf` and its
Ubuntu Font Licence 1.0. The font bytes come from the locked libschrift tree;
the accompanying licence text is the Canonical Ubuntu Sans v1.006
`LICENCE.txt`, committed and hash-bound by the component recipe. The compile
also installs libschrift's locked ISC `LICENSE` beside the font licence. The
container has no network, two CPUs, a 2 GiB memory limit and no additional swap
allowance.

The base build uses the hash-locked `raptor-support.fragment` through Thingino's
existing configuration-fragment mechanism. It retains the required libraries
and common services without compiling Prudynt. A support-only image cannot be
provisioned or packaged for installation before full Raptor composition.

The component cache binds the source recipe, base-image hash, SDK hash and
immutable builder-image ID. Composition checks ELF dependencies, removes the
old Prudynt/daynight/recorder startup paths, and reads the packed image back.
The final kernel uses 42 MiB Linux / 22 MiB ISP. SquashFS uses 256 KiB XZ blocks
with `-no-tailends` and the existing fixed system-region size limit.

The default H.264 profile explicitly retains 16 frames per stream in its
shared-memory ring, about one second at 15 fps. Byte capacity remains tied to
the configured bitrate and GOP. Upstream's 32-slot default reserves about
4 MiB across the two rings at this profile's bitrates; 16 slots halve that
reservation without changing resolution, FPS or the 42/22 memory split.
Slow consumers can fall behind this shorter retention window, so combined
streaming and recording acceptance must include this profile setting.

The universal image contains no camera credentials. Its Raptor service remains
disabled until per-camera provisioning enables it, configures authenticated
RTSP and supplies the camera's other settings. The model image is reusable;
the provisioning image is not. Raptor alone starts the media-aware Control
service, so provisioning must not reactivate a second Control owner.

The initial full-Raptor RTSP account is `root` with the separately derived
RTSP password, not the WebUI password. Default video paths are `/stream0`
and `/stream1`; configured endpoint aliases replace those paths. RSD enforces
Digest authentication. The shared installer RTSP acceptance helper supports
Digest MD5 and the legacy Basic scheme, selects only the observed challenge,
and takes an explicit stream path for the Raptor profile. It still requires
unauthenticated rejection and a complete 1080p H.264 IDR before passing.
Neither authentication scheme encrypts the video transport.

Host tests cover source reconstruction, component caching, composition and
camera-specific provisioning. A clean firmware build and an installation from
this integrated path still need validation. Earlier camera tests of separately
assembled images do not close those checks.

`headers-input.json` separately pins the unchanged public Ingenic headers tree
needed as a build input. That upstream commit has no LICENSE, COPYING, or NOTICE
file. Its README is recorded only as a provenance notice: the pin does not
authorize redistribution of the headers, an SDK, or firmware binaries, and the
headers are not copied into this repository.
