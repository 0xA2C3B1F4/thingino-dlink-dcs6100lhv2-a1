---
name: dcs6100-thingino
description: Build, provision, stage, recover, diagnose, or validate Thingino for the D-Link DCS-6100LHV2 A1 through this repository's machine-checked workflows. Use only for this exact model and revision; do not use for generic Thingino boards or treat a host result as permission to touch a camera.
---

# DCS-6100 Thingino

Work from a reviewed checkout of
`0xA2C3B1F4/thingino-dlink-dcs6100lhv2-a1`. Before changing files or starting a
build, inspect the repository root, branch, worktrees, status, HEAD, remotes,
and competing build or device processes. Preserve unrelated changes and use an
isolated worktree when another writer is active.

Use the repository's `thingino-dlink` and `dcs6100-thingino` commands. Do not
reimplement their validation, artifact construction, media staging, recovery,
or write logic with ad hoc shell commands. Run `make release-status` before
claiming that an operation is supported, and run `make check` before a
production build or source publication.

Choose the workflow in [references/workflows.md](references/workflows.md). For
a supported preflight mode, start with:

```bash
thingino-dlink workflow-preflight --json \
  --mode MODE \
  --data-volume /path/to/exact-mounted-volume
```

Supply the mode-specific inputs documented by the checkout. Treat `ok: false`,
`phase: workflow-stopped`, a missing `next_command`, or
`safe_next_action: stop` as a stop decision. Follow a returned `next_command`
only when it remains inside the user's authorized scope.

Before any camera access, removable-media change, power action, reboot, UART or
U-Boot interaction, or persistent write, read
[references/authorization-boundaries.md](references/authorization-boundaries.md).
These actions need current, explicit authorization even when the user asked to
finish the surrounding build or investigation.

Keep private inputs and generated secrets outside the checkout. Do not print
credentials, Wi-Fi values, private keys, camera-derived secret material, or
provisioning contents. Report roles, paths, sizes, public identifiers, and
non-secret digests only when they help verify the result.

Use [references/evidence-levels.md](references/evidence-levels.md) when
reporting results. Source, host, MIPS, artifact, removable-media, and live-device
evidence are separate. End with the exact checks run, artifact identities when
created, physical actions and MTD writes actually performed, open gates, and
the next authorized action.
