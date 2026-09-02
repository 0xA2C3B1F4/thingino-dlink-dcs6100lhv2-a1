# Evidence levels

Read `docs/testing.md` and `docs/status.md` from the checkout before evaluating
a candidate. Record each result at the level it actually proves.

| Level | Valid claim | Invalid promotion |
| --- | --- | --- |
| Source and policy | The reviewed tree, schemas, manifests, and locks agree | The program runs on the target |
| Host tests or fake NOR | Logic and simulated interruption cases pass | Physical power-loss recovery works |
| MIPS compile | Target code compiles with the named toolchain and mode | The image boots or behaves correctly |
| Built artifact | The named bytes, size, digest, provenance, and reproducibility checks pass | A camera accepted or installed them |
| Removable media | The exact card transaction and storage readback passed | NOR changed or the camera booted |
| Live device | The named camera and exact candidate produced the observed result | Another camera, build, or open matrix row passes |

Report the source HEAD and dirty state, commands and test counts, toolchain or
builder image identity, artifact digests and sizes, and reproducibility result
when applicable. State separately whether removable media, camera access,
reboots, U-Boot commands, or NOR writes occurred. Name the physical MTD write
set only from observed CLI output or readback evidence.

Do not call a host pass a firmware release, a matching artifact size
reproducibility, an API success a physical-control result, or a bootloader
success string readback. Leave every unrun or blocked gate explicit.
