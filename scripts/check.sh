#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

# macOS exposes its temporary directory through /var -> /private/var.
# Tests exercise strict path and symlink checks, so create their temporary
# fixtures under the canonical directory without weakening installer checks.
TMPDIR=$(python3 -c 'import pathlib, tempfile; print(pathlib.Path(tempfile.gettempdir()).resolve(strict=True))')
export TMPDIR

python3 scripts/check_public_tree.py
python3 scripts/check_release_gates.py
python3 scripts/source_checkout.py validate-lock >/dev/null
python3 scripts/check_docs.py
python3 scripts/check_thingino_control_contract.py
python3 -m unittest discover -s tests -p 'test_*.py' -q -b
