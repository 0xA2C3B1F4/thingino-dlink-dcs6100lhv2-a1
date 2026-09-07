# Contributing

Maintainers review pull requests and suggestions against the project's scope,
release gates, and licensing requirements.

Any accepted change must keep the DCS-6100LHV2 A1 source, installer, recovery,
and licensing boundaries explicit.

## Before changing code

1. Read `README.md`, `docs/status.md`, and the document for the affected area.
2. Keep vendor inputs, credentials, builds, logs, and device evidence outside
   the repository.
3. Pin imported source to an immutable revision. Preserve authorship and the
   applicable license.
4. Do not contact a camera, write removable media, or change NOR without
   separate authorization for the exact device and operation.

Before editing, inspect the checkout path, branch, worktrees, dirty files,
HEAD, remotes, and competing writers. Preserve unrelated changes. Use an
isolated worktree if another process is changing the same source.

The README is the shared installation guide for people and automation.
Keep its command examples consistent with the current parsers and distinguish
model-universal from legacy personalized commands. Update the focused reference
page with implementation changes. Keep one shared installation procedure.
A stopped operation permits read-only diagnosis, not bypassing the
identity, recovery, signature, or media gate.

Use the repository CLI for media preparation and installation. Never replace
its validation or bounded writes with ad hoc flash commands. Preserve stock
physical mtd0/mtd4/mtd5. Build success, host tests, card readback, and live-device
acceptance are separate results; report only the evidence actually collected.

## Checks

Python 3.11 or newer is required. Run:

```bash
make check
```

For the Rust service and WebUI, also run:

```bash
make control
make webui-build
make webui
```

Enable the repository hook once per clone:

```bash
git config core.hooksPath .githooks
```

Keep commits focused. Before committing, run `git diff --cached --check` and
review every staged file for identifiers, secrets, binaries, private data, and
licensing problems.

## Keep private and generated files out

Do not submit firmware images, dumps, device backups, credentials, private
keys, certificates, packet captures, raw UART logs, vendor payloads, extracted
proprietary files, generated installer sets, or per-device manifests.

`policy/public-tree.json` is the fail-closed source allowlist. Every tracked
release-source file must appear in it.
