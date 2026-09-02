#!/bin/sh
set -eu

source_input=/input/source
download_cache=/input/download-cache.tar
workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/output
result_dir=/result
empty_vendor_site=$workspace/ap-only-empty-vendor-site
profile=dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu
source_epoch=1786006608
ap_fragment="\$(BR2_EXTERNAL_THINGINO_PATH)/configs/cameras-exp/$profile/recovery-ap-kernel.fragment"

test -f "$source_input/dcs6100-source-preparation.json"
test -f "$source_input/configs/cameras-exp/$profile/recovery-ap-kernel.fragment"
test -f "$download_cache"
test -d "$workspace"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"
test -d "$source_dir/.git"
cmp "$source_input/dcs6100-source-preparation.json" \
	"$source_dir/dcs6100-source-preparation.json"

install -d -o builder -g builder "$empty_vendor_site"
common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch THINGINO_CONTAINER_BUILD=1 CAMERA=$profile PRISTINE=1 CCACHE_DISABLE=1 BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir DCS6100_VENDOR_BUNDLE_DIR=$empty_vendor_site WORKFLOW=1"

test ! -f "$output_dir/.config" || unlink "$output_dir/.config"
runuser -u builder -- env $common_env \
	make -C "$source_dir" CAMERA="$profile" GROUP=exp defconfig
sed -i "s|^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=.*$|BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$ap_fragment\"|" "$output_dir/.config"
sed -i 's/^BR2_PER_PACKAGE_DIRECTORIES=y$/# BR2_PER_PACKAGE_DIRECTORIES is not set/' "$output_dir/.config"
sed -i 's/^# BR2_PACKAGE_WIFI is not set$/BR2_PACKAGE_WIFI=y/' "$output_dir/.config"
sed -i 's/^# BR2_PACKAGE_WIFI_RTL8188FU is not set$/BR2_PACKAGE_WIFI_RTL8188FU=y/' "$output_dir/.config"
sed -i 's/^BR2_PACKAGE_WIFI_RTW_HOSTAPD=.*/BR2_PACKAGE_WIFI_RTW_HOSTAPD=y/' "$output_dir/.config"
sed -i 's/^# BR2_PACKAGE_THINGINO_KOPT_IPV6 is not set$/BR2_PACKAGE_THINGINO_KOPT_IPV6=y/' "$output_dir/.config"
grep -q '^BR2_PACKAGE_THINGINO_KOPT_IPV6=y$' "$output_dir/.config" ||
	printf '%s\n' 'BR2_PACKAGE_THINGINO_KOPT_IPV6=y' >>"$output_dir/.config"
grep -q '^BR2_PACKAGE_WIFI_RTW_HOSTAPD=y$' "$output_dir/.config" ||
	printf '%s\n' 'BR2_PACKAGE_WIFI_RTW_HOSTAPD=y' >>"$output_dir/.config"
for setting in \
	BR2_PACKAGE_MDNSD \
	BR2_PACKAGE_MTD \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_DRIVER_RTW \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_DRIVER_NL80211; do
	sed -i "s/^# $setting is not set$/$setting=y/" "$output_dir/.config"
	grep -q "^$setting=y$" "$output_dir/.config" ||
		printf '%s\n' "$setting=y" >>"$output_dir/.config"
done
for setting in \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_DRIVER_HOSTAP \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_EAP \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_WPS \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_WPA3 \
	BR2_PACKAGE_WIFI_RTW_HOSTAPD_VLAN; do
	sed -i "s/^$setting=y$/# $setting is not set/" "$output_dir/.config"
done

runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" olddefconfig
grep -Fx "BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$ap_fragment\"" "$output_dir/.config" >/dev/null
grep -Fx 'BR2_PACKAGE_WIFI_RTL8188FU=y' "$output_dir/.config" >/dev/null
grep -Fx 'BR2_PACKAGE_WIFI_RTW_HOSTAPD=y' "$output_dir/.config" >/dev/null
grep -Fx 'BR2_PACKAGE_WIFI_RTW_HOSTAPD_DRIVER_RTW=y' "$output_dir/.config" >/dev/null
grep -Fx 'BR2_PACKAGE_THINGINO_KOPT_IPV6=y' "$output_dir/.config" >/dev/null

runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" linux-reconfigure
# The external RTL8188FU package reads the effective kernel configuration at
# compile time. Buildroot does not automatically invalidate an already-built
# out-of-tree module or hostapd when only their effective options change.
runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" wifi-rtl8188fu-dirclean
runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" wifi-rtw-hostapd-dirclean
runuser -u builder -- env $common_env \
	make -C "$source_dir/buildroot" O="$output_dir" \
	BR2_MAKE_JOBS="-j$(nproc)" \
	linux wifi-rtl8188fu wifi-rtw-hostapd wpa_supplicant dropbear busybox mdnsd mtd

linux_config=$(find "$output_dir/build" -mindepth 2 -maxdepth 2 -type f -path '*/linux-*/.config')
wifi_module=$(find "$output_dir/target/lib/modules" -type f -name '8188fu.ko')
mmc_module=$(find "$output_dir/target/lib/modules" -type f -name 'jzmmc_v12.ko')
test "$(printf '%s\n' "$linux_config" | wc -l)" -eq 1
test "$(printf '%s\n' "$wifi_module" | wc -l)" -eq 1
test "$(printf '%s\n' "$mmc_module" | wc -l)" -eq 1
test -x "$output_dir/target/usr/sbin/hostapd"
test -x "$output_dir/target/usr/sbin/mdnsd"
test -x "$output_dir/target/usr/sbin/wpa_supplicant"
test -x "$output_dir/target/usr/sbin/dropbear"
test -x "$output_dir/target/usr/sbin/flashcp"
test -x "$output_dir/target/bin/busybox"

install -m 0400 "$output_dir/images/uImage" "$result_dir/recovery-ap-kernel.uimage"
install -m 0400 "$linux_config" "$result_dir/recovery-ap-linux.config"
install -m 0400 "$wifi_module" "$result_dir/8188fu.ko"
install -m 0400 "$mmc_module" "$result_dir/jzmmc_v12.ko"
install -m 0400 "$output_dir/build/wifi-rtw-hostapd-"*/hostapd-2.9/hostapd/.config \
	"$result_dir/hostapd.build.config"
grep -q '^CONFIG_IPV6=y$' "$result_dir/hostapd.build.config"
install -m 0400 "$source_input/dcs6100-source-preparation.json" \
	"$result_dir/source-preparation.json"
tar \
	--sort=name \
	--mtime="@$source_epoch" \
	--owner=0 \
	--group=0 \
	--numeric-owner \
	-cf "$result_dir/recovery-ap-target.tar" \
	-C "$output_dir/target" .
