#!/bin/sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
manifest="$repository_root/components/thingino-control/Cargo.toml"
: "${TMPDIR:?TMPDIR must identify task-owned temporary storage}"
: "${CARGO_TARGET_DIR:=$TMPDIR/thingino-control-target}"
export CARGO_TARGET_DIR

version=$(rustc --version)
case "$version" in
	rustc\ 1.95.0\ *) ;;
	*) echo "Rust 1.95.0 is required, found: $version" >&2; exit 1 ;;
esac

cargo fmt --manifest-path "$manifest" --check
cargo clippy --manifest-path "$manifest" --all-targets -- -D warnings
python3 "$repository_root/components/thingino-control/scripts/run_with_mqtt_brokers.py" -- \
	cargo test --manifest-path "$manifest" --locked
cargo build --manifest-path "$manifest" --release --locked
python3 "$repository_root/components/thingino-control/scripts/host_soak.py" \
	--binary "$CARGO_TARGET_DIR/release/thingino-controld"
