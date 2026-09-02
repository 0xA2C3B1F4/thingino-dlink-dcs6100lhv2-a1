# Workflows

The checkout is the authority for commands and current support status. Read only
the documentation needed for the requested operation.

| Request | Read in the checkout | Starting gate |
| --- | --- | --- |
| Inspect or change source | `CONTRIBUTING.md`, `docs/testing.md` | Run the focused check, then `make check` before publication |
| Build local host tools | `README.md`, `docs/build.md` | Use Python 3.11 or newer and verify the source lock |
| Build reusable model firmware | `docs/build.md`, `docs/status.md` | Run `workflow-preflight --mode production-build`, then use `local-build build-universal` only with the required reviewed model inputs |
| Create per-camera provisioning or authorization | `docs/build.md`, `docs/installation.md` | Validate that camera's recovery and preserved readback before running `universal provision` or `universal authorize` |
| Stage removable media or install | `docs/installation.md`, `docs/hardware.md`, `docs/status.md` | Stop if the requested path has an open release gate or no guided staging command |
| Back up or restore stock firmware | `docs/recovery.md`, `docs/installation.md` | Use the `stock-recovery` commands and their current media and camera confirmations |
| Diagnose a running candidate | `docs/testing.md`, command help | Prefer an existing snapshot; live collection requires current camera-access authorization |
| Accept or publish a release | `docs/testing.md`, `docs/status.md` | Run `make release-status` and the matching `make release-ready-*` gate |

Install the host package only when the requested work needs its CLI:

```bash
make check
python3 -m pip install -e .
thingino-dlink --help
python3 -m installer --help
```

Use a project environment when the host has one. Installing this skill does not
install the Python package, Docker, toolchains, private model inputs, or
firmware.

Prefer JSON output for preflight, inspection, diagnosis, candidate, and status
commands. Read the returned phase, checks, physical-action declaration, written
MTD list, readback state, and next command. Do not infer success from exit code
or a bootloader success string alone.

The universal workflow deliberately separates reusable model bytes from each
camera's recovery, provisioning data, and authorization. Never reuse a
per-camera sidecar, data image, authorization, recovery set, or preserved
readback for another camera. A missing or mismatched model input is a stop, not
a reason to use an older private artifact or bypass a digest check.
