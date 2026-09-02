#!/bin/sh
set -eu

source_input=/input/source
download_cache=/input/download-cache.tar
workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/output
result_dir=/result
empty_vendor_site=$workspace/kernel-only-empty-vendor-site
profile=dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu
source_epoch=1786006608
collector_fragment="\$(BR2_EXTERNAL_THINGINO_PATH)/configs/cameras-exp/$profile/collector-kernel.fragment"
uartless_fragment="\$(BR2_EXTERNAL_THINGINO_PATH)/configs/cameras-exp/$profile/uartless-collector-kernel.fragment"
uartless_fragment_path="$source_dir/configs/cameras-exp/$profile/uartless-collector-kernel.fragment"
uartless_command_line='console=ttyS1,115200n8 mem=42M@0x0 rmem=22M@0x2a00000 init=/sbin/init root=/dev/mtdblock2 rootfstype=squashfs ro panic=10 mtdparts=jz_sfc:256k(boot)ro,1792k(kernel)ro,4608k(rootfs)ro,7936k(userdata)ro,1536k(userdata2)ro,256k(userdata3)ro'

test -f "$source_input/dcs6100-source-preparation.json"
test -f "$source_input/configs/cameras-exp/$profile/collector-kernel.fragment"
test -f "$download_cache"
test -d "$workspace"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"

if test -e "$workspace/lost+found"; then
	test -d "$workspace/lost+found"
	test ! -L "$workspace/lost+found"
	test -z "$(find "$workspace/lost+found" -mindepth 1 -print -quit)"
fi
if test -z "$(find "$workspace" -mindepth 1 -maxdepth 1 ! -name lost+found -print -quit)"; then
	install -d -o builder -g builder "$source_dir" "$output_dir"
	cp -a "$source_input/." "$source_dir/"
	chown -R builder:builder "$workspace"

	runuser -u builder -- git -C "$source_dir" init -q
	runuser -u builder -- git -C "$source_dir" config user.name "DCS6100 source builder"
	runuser -u builder -- git -C "$source_dir" config user.email "builder@invalid"
	runuser -u builder -- git -C "$source_dir" add -A
	runuser -u builder -- env \
		GIT_AUTHOR_DATE="@$source_epoch +0000" \
		GIT_COMMITTER_DATE="@$source_epoch +0000" \
		git -C "$source_dir" commit -q -m "Prepared public source"

	tar -xf "$download_cache" -C "$source_dir"
	chown -R builder:builder "$source_dir/dl"
fi

test -d "$source_dir/.git"
test -f "$output_dir/.config" || initial_config=1
cmp "$source_input/dcs6100-source-preparation.json" \
	"$source_dir/dcs6100-source-preparation.json"
install -d -o builder -g builder "$output_dir"
install -d -o builder -g builder "$empty_vendor_site"

common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch THINGINO_CONTAINER_BUILD=1 CAMERA=$profile PRISTINE=1 CCACHE_DISABLE=1 BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir DCS6100_VENDOR_BUNDLE_DIR=$empty_vendor_site WORKFLOW=1"

if test "${initial_config:-0}" = 1; then
	runuser -u builder -- env $common_env \
		make -C "$source_dir" CAMERA="$profile" GROUP=exp defconfig

	old_fragment=$(sed -n 's/^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES="\(.*\)"$/\1/p' "$output_dir/.config")
	test -n "$old_fragment"
	test "$(printf '%s\n' "$old_fragment" | wc -l)" -eq 1
	sed -i "s|^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=.*$|BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$collector_fragment\"|" "$output_dir/.config"
	sed -i 's/^BR2_PER_PACKAGE_DIRECTORIES=y$/# BR2_PER_PACKAGE_DIRECTORIES is not set/' "$output_dir/.config"
fi
sed -i 's/^BR2_PACKAGE_WIFI_RTL8188FU=y$/# BR2_PACKAGE_WIFI_RTL8188FU is not set/' "$output_dir/.config"
sed -i 's/^BR2_PACKAGE_WIFI=y$/# BR2_PACKAGE_WIFI is not set/' "$output_dir/.config"
grep -Fx "BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$collector_fragment\"" "$output_dir/.config" >/dev/null
grep -Fx '# BR2_PER_PACKAGE_DIRECTORIES is not set' "$output_dir/.config" >/dev/null
grep -Fx '# BR2_PACKAGE_WIFI_RTL8188FU is not set' "$output_dir/.config" >/dev/null
grep -Fx '# BR2_PACKAGE_WIFI is not set' "$output_dir/.config" >/dev/null

runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" olddefconfig
runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" linux-reconfigure
runuser -u builder -- env $common_env \
	make -C "$source_dir" CAMERA="$profile" GROUP=exp \
	BR2_MAKE_JOBS="-j$(nproc)" "$output_dir/images/uImage"

linux_config=$(find "$output_dir/build" -mindepth 2 -maxdepth 2 -type f -path '*/linux-*/.config')
test -n "$linux_config"
test "$(printf '%s\n' "$linux_config" | wc -l)" -eq 1
mmc_module="$output_dir/target/lib/modules/3.10.14__isvp_swan_1.0__/kernel/drivers/mmc/host/jzmmc_v12.ko"
test -f "$mmc_module"

install -m 0400 "$output_dir/images/uImage" "$result_dir/collector-kernel.uimage"
install -m 0400 "$linux_config" "$result_dir/collector-linux.config"
install -m 0400 "$mmc_module" "$result_dir/jzmmc_v12.ko"

# Build a separate stock-mtd2-boot kernel for the explicitly write-capable
# UARTless onboarding alternative. The package transport will replace mtd1 and
# mtd2 before this kernel runs, but the effective capture kernel marks every
# physical partition read-only and exposes no network stack.
test ! -e "$uartless_fragment_path"
sed "s|^# CONFIG_CMDLINE_BOOL is not set$|CONFIG_CMDLINE_BOOL=y\\
CONFIG_CMDLINE=\"$uartless_command_line\"\\
CONFIG_CMDLINE_OVERRIDE=y|" \
	"$source_dir/configs/cameras-exp/$profile/collector-kernel.fragment" \
	>"$uartless_fragment_path"
chown builder:builder "$uartless_fragment_path"
grep -Fx 'CONFIG_CMDLINE_BOOL=y' "$uartless_fragment_path" >/dev/null
grep -Fx "CONFIG_CMDLINE=\"$uartless_command_line\"" "$uartless_fragment_path" >/dev/null
grep -Fx 'CONFIG_CMDLINE_OVERRIDE=y' "$uartless_fragment_path" >/dev/null
sed -i "s|^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=.*$|BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$uartless_fragment\"|" "$output_dir/.config"
runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" olddefconfig
runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" linux-reconfigure
runuser -u builder -- env $common_env \
	make -C "$source_dir" CAMERA="$profile" GROUP=exp \
	BR2_MAKE_JOBS="-j$(nproc)" "$output_dir/images/uImage"
linux_config=$(find "$output_dir/build" -mindepth 2 -maxdepth 2 -type f -path '*/linux-*/.config')
test -n "$linux_config"
test "$(printf '%s\n' "$linux_config" | wc -l)" -eq 1
grep -Fx 'CONFIG_CMDLINE_BOOL=y' "$linux_config" >/dev/null
grep -Fx "CONFIG_CMDLINE=\"$uartless_command_line\"" "$linux_config" >/dev/null
grep -Fx 'CONFIG_CMDLINE_OVERRIDE=y' "$linux_config" >/dev/null
install -m 0400 "$output_dir/images/uImage" \
	"$result_dir/uartless-collector-kernel.uimage"
install -m 0400 "$linux_config" \
	"$result_dir/uartless-collector-linux.config"
install -m 0400 "$source_input/dcs6100-source-preparation.json" \
	"$result_dir/source-preparation.json"
