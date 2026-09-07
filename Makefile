.PHONY: contract control check docs policy release-status release-ready-source release-ready-public-firmware sources test webui webui-build webui-size

contract:
	python3 scripts/check_thingino_control_contract.py

control:
	./scripts/check_thingino_control.sh

check:
	./scripts/check.sh

docs:
	python3 scripts/check_docs.py

policy:
	python3 scripts/check_public_tree.py

release-status:
	python3 scripts/check_release_gates.py

release-ready-source:
	python3 scripts/check_release_gates.py --require source-publication

release-ready-public-firmware:
	python3 scripts/check_release_gates.py --require firmware-release

sources:
	python3 scripts/source_checkout.py validate-lock

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -q

webui:
	cd webui && npm run check

webui-build:
	cd webui && npm ci --ignore-scripts --no-audit --no-fund && npm run build

webui-size:
	cd webui && npm run size
