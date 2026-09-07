# License review status

This file records source observations, not legal advice or a new license grant.
The machine-readable release decision is in
[`policy/release-gates.json`](../policy/release-gates.json).

## Prudynt

The pinned source is public, but its licensing is not established:

- `themactep/prudynt-t` is a public fork of `gtxaspec/prudynt-t`;
- GitHub reports no detected license for either repository;
- pinned commit `c630ae797a86b29e83999fe324323932ec33460c` has no
  `LICENSE`, `COPYING`, or `NOTICE` file;
- the current pinned tree has no project-source license notice or copyright
  header that grants modification and redistribution rights;
- the initial `gtxaspec/prudynt-t` import contained GPL and LGPL files for its
  bundled live555 copy, but commit `57623076d84b044e88aa4095f22f3417eb42ae36`
  removed that third-party tree; and
- the surviving live555 terms applied to live555, not automatically to the
  Prudynt sources.

A public Git repository is source-available. It is open source only when the
copyright holders grant an open-source license. This project cannot infer that
grant from repository visibility, forks, build recipes, or historical
third-party license files.

The source snapshot intentionally publishes the complete required Prudynt patch
series because omitting it would make the documented build incomplete. This is a
source-available publication decision, not a conclusion that the Prudynt
context is open source or covered by this repository's MIT license. Anyone
redistributing that context must assess the unresolved rights independently.

The separate firmware gate closes only when an authorized copyright holder
publishes terms that cover the pinned Prudynt source and derivative patch
context. A useful upstream request is:

> Please add an explicit project-level license covering the Prudynt source and
> clarify whether it applies to commit
> `c630ae797a86b29e83999fe324323932ec33460c` and downstream derivative patches.
> Please retain the copyright notices and state any separately licensed files.

Until then, do not distribute a Prudynt binary or describe the Prudynt-derived
patch context as MIT-licensed or open source.

## RTL8188FU

Pinned commit `6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06` has no
top-level `COPYING`, `LICENSE`, `NOTICE`, or README file. Most driver sources
carry Realtek GPL version 2 notices. The `core/crypto` closure also contains
Jouni Malinen and Cozybit files that state BSD terms and refer to a missing
README for the full text.

This repository now carries the canonical GPL-2.0-only text and the hostap BSD
license text referenced by those headers. That fixes the missing license-text
delivery in this source repository. It does not yet prove the exact compiled
RTL8188FU file closure or corresponding-source package for a firmware binary.
The firmware gate stays blocked until that generated closure and its notices
are recorded together.

The bundled copies were retrieved from the primary sources on 2026-08-29:

- `RTL8188FU-GPL-2.0-only.txt` has SHA-256
  `4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e`;
  and
- `RTL8188FU-hostap-BSD-3-Clause.txt` has SHA-256
  `2c2b3640a8256edb409356a7e3f3dfe762a489bd2cd9586aa1647b0f1128caf8`.

## Evidence sources

- [Pinned Prudynt repository](https://github.com/themactep/prudynt-t/tree/c630ae797a86b29e83999fe324323932ec33460c)
- [Prudynt parent repository](https://github.com/gtxaspec/prudynt-t)
- [Prudynt v3 mirror](https://github.com/gtxaspec/prudynt-v3)
- [Pinned RTL8188FU repository](https://github.com/gtxaspec/rtl8188ftv-wifi/tree/6e3c1c2d244f5056d2a7ade3dbcf9daa3876fc06)
- [GNU GPL version 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt)
- [hostap license](https://w1.fi/cgit/hostap/plain/README)
