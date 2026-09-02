#!/bin/sh
set -eu

workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/thingino-output
result_dir=/result
vendor_site=/input/vendor-site
audio_link=/input/media-link/libaudioProcess.so
rust_toolchain=/input/rust-toolchain
rust_source=/input/rust-source
ingenic_toolchain_archive=/input/ingenic-glibc216-toolchain.tar
ingenic_toolchain=$workspace/ingenic-glibc216-toolchain
profile=dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu
fragment_dir=$source_dir/configs/cameras-exp/$profile
installer_fragment_rel="\$(BR2_EXTERNAL_THINGINO_PATH)/configs/cameras-exp/$profile/split-installer-kernel.fragment"
final_fragment_rel="\$(BR2_EXTERNAL_THINGINO_PATH)/configs/cameras-exp/$profile/split-final-kernel.fragment"
source_epoch=1786006608

test -d "$source_dir/.git"
test -f "$output_dir/.config"
test -f /input/installer-kernel.fragment
test -f /input/final-kernel.fragment
test -f "$vendor_site/build-site.private.json"
test -f "$audio_link"
test -x "$rust_toolchain/bin/rustc"
test -f "$rust_source/library/Cargo.toml"
test -f "$ingenic_toolchain_archive"
test ! -L "$ingenic_toolchain_archive"
test -d "$ingenic_toolchain"
find "$ingenic_toolchain" -depth -delete
tar -xf "$ingenic_toolchain_archive" -C "$workspace"
test -d "$ingenic_toolchain"
test ! -L "$ingenic_toolchain"
test -x "$ingenic_toolchain/bin/mips-linux-gnu-gcc"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"

install -m 0644 /input/installer-kernel.fragment \
	"$fragment_dir/split-installer-kernel.fragment"
install -m 0644 /input/final-kernel.fragment \
	"$fragment_dir/split-final-kernel.fragment"
chown builder:builder \
	"$fragment_dir/split-installer-kernel.fragment" \
	"$fragment_dir/split-final-kernel.fragment"

common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch THINGINO_CONTAINER_BUILD=1 CAMERA=$profile PRISTINE=1 CCACHE_DISABLE=1 THINGINO_OUTPUT_DIR=$output_dir DCS6100_VENDOR_BUNDLE_DIR=$vendor_site DCS6100_AUDIOPROCESS_LINK_FILE=$audio_link DCS6100_RUST_TOOLCHAIN_DIR=$rust_toolchain DCS6100_RUST_SOURCE_DIR=$rust_source DCS6100_INGENIC_TOOLCHAIN_DIR=$ingenic_toolchain WORKFLOW=1"

build_kernel() {
	role=$1
	fragment_rel=$2
	fragment_file=$3

	sed -i \
		"s|^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=.*$|BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$fragment_rel\"|" \
		"$output_dir/.config"
	grep -Fx "BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$fragment_rel\"" \
		"$output_dir/.config" >/dev/null

	runuser -u builder -- env $common_env \
		make -C "$source_dir/buildroot" O="$output_dir" olddefconfig
	runuser -u builder -- env $common_env \
		make -C "$source_dir/buildroot" O="$output_dir" linux-reconfigure
	runuser -u builder -- env $common_env \
		make -C "$source_dir" CAMERA="$profile" GROUP=exp \
		BR2_MAKE_JOBS="-j$(nproc)" "$output_dir/images/uImage"

	linux_config=$(find "$output_dir/build" -mindepth 2 -maxdepth 2 \
		-type f -path '*/linux-*/.config')
	test "$(printf '%s\n' "$linux_config" | wc -l)" -eq 1
	while IFS= read -r required; do
		case "$required" in
			CONFIG_*=*) grep -Fx "$required" "$linux_config" >/dev/null ;;
		esac
	done <"$fragment_file"

	install -m 0400 "$output_dir/images/uImage" \
		"$result_dir/$role-kernel.uimage"
	install -m 0400 "$linux_config" \
		"$result_dir/$role-linux.config"
}

build_kernel installer "$installer_fragment_rel" /input/installer-kernel.fragment

mmc_module=$(find "$output_dir/target/usr/lib/modules" -type f \
	-name jzmmc_v12.ko)
test "$(printf '%s\n' "$mmc_module" | wc -l)" -eq 1
install -m 0400 "$mmc_module" "$result_dir/installer-jzmmc_v12.ko"

build_kernel final "$final_fragment_rel" /input/final-kernel.fragment

for artifact in \
	installer-kernel.uimage installer-linux.config installer-jzmmc_v12.ko \
	final-kernel.uimage final-linux.config; do
	test -s "$result_dir/$artifact"
done

cat >"$result_dir/split-kernels.manifest.json" <<EOF
{
  "schema_version": 1,
  "status": "host-built split kernels; generation does not authorize live use",
  "target": "DCS-6100LHV2-A1",
  "layout": "dcs6100lhv2-a1-mtd3-split-v1",
  "artifacts": {
    "installer-kernel.uimage": "$(sha256sum "$result_dir/installer-kernel.uimage" | cut -d' ' -f1)",
    "installer-linux.config": "$(sha256sum "$result_dir/installer-linux.config" | cut -d' ' -f1)",
    "installer-jzmmc_v12.ko": "$(sha256sum "$result_dir/installer-jzmmc_v12.ko" | cut -d' ' -f1)",
    "final-kernel.uimage": "$(sha256sum "$result_dir/final-kernel.uimage" | cut -d' ' -f1)",
    "final-linux.config": "$(sha256sum "$result_dir/final-linux.config" | cut -d' ' -f1)"
  }
}
EOF
chmod 0400 "$result_dir/split-kernels.manifest.json"
