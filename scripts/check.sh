#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"

python3 scripts/check_public_tree.py
python3 scripts/check_release_gates.py
python3 scripts/source_checkout.py validate-lock >/dev/null
python3 scripts/check_docs.py
python3 scripts/check_thingino_control_contract.py
python3 -m unittest discover -s tests -p 'test_*.py' -q -b
