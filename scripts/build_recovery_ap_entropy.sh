#!/bin/sh
set -eu

kernel_dir=${KERNEL_DIR:?set KERNEL_DIR to the prepared Linux 3.10.14 tree}
cross_compile=${CROSS_COMPILE:?set CROSS_COMPILE to the mipsel-linux- prefix}
output_dir=${OUTPUT_DIR:?set OUTPUT_DIR to an empty durable result directory}
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

test -f "$kernel_dir/Makefile"
test ! -e "$output_dir" || test -z "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit)"
mkdir -p "$output_dir"
build_dir=$(mktemp -d "${TMPDIR:?TMPDIR must be set}/dcs6100-dtrng.XXXXXX")
trap 'rm -rf "$build_dir"' EXIT HUP INT TERM

cp "$project_dir/installer/recovery_ap/ingenic_t31_dtrng.c" "$build_dir/"
printf '%s\n' 'obj-m := ingenic_t31_dtrng.o' >"$build_dir/Makefile"
make -C "$kernel_dir" M="$build_dir" ARCH=mips CROSS_COMPILE="$cross_compile" modules
"${cross_compile}gcc" -Os -Wall -Wextra -Werror \
	-o "$build_dir/entropy-seed" \
	"$project_dir/installer/recovery_ap/entropy_seed.c"
"${cross_compile}strip" --strip-debug "$build_dir/ingenic_t31_dtrng.ko"
"${cross_compile}strip" --strip-all "$build_dir/entropy-seed"

install -m 0400 "$build_dir/ingenic_t31_dtrng.ko" "$output_dir/ingenic_t31_dtrng.ko"
install -m 0500 "$build_dir/entropy-seed" "$output_dir/entropy-seed"
