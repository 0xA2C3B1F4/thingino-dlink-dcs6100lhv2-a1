#!/bin/sh
set -eu

source_input=/input/source
workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/thingino-toolchain-download-output
result_dir=/result
board=toolchain_xburst1_glibc_gcc16
source_epoch=1786006608
kernel_tarball_url=https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.10.14.tar.xz
kernel_tarball_sha256=36540d5fb15951be64d4c150cf3bc291a8d4d6699fb988173f6be30aa1e41b47

test -d "$source_input/.git"
test -d "$workspace"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"
test ! -e "$source_dir"

install -d -o builder -g builder "$source_dir" "$output_dir"
cp -a "$source_input/." "$source_dir/"
chown -R builder:builder "$source_dir" "$output_dir"

common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch PRISTINE=1 CCACHE_DISABLE=1 BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir WORKFLOW=1"

runuser -u builder -- env $common_env \
  make -C "$source_dir" BOARD="$board" GROUP=github \
  KERNEL_TARBALL_URL="$kernel_tarball_url" defconfig
runuser -u builder -- \
  "$source_dir/buildroot/utils/config" --file "$output_dir/.config" \
  --enable BR2_REPRODUCIBLE --disable BR2_LINUX_KERNEL \
  --enable BR2_KERNEL_HEADERS_CUSTOM_TARBALL \
  --set-str BR2_KERNEL_HEADERS_CUSTOM_TARBALL_LOCATION "$kernel_tarball_url" \
  --enable BR2_PACKAGE_HOST_LINUX_HEADERS_CUSTOM_3_10
runuser -u builder -- sed -i \
  's|^BR2_PRIMARY_SITE=.*|BR2_PRIMARY_SITE="https://sources.buildroot.net"|' \
  "$output_dir/.config"
runuser -u builder -- env $common_env \
  make -C "$source_dir/buildroot" O="$output_dir" \
  BR2_EXTERNAL="$source_dir" olddefconfig
grep -qx 'BR2_REPRODUCIBLE=y' "$output_dir/.config"
grep -qx '# BR2_LINUX_KERNEL is not set' "$output_dir/.config"
grep -qx 'BR2_KERNEL_HEADERS_CUSTOM_TARBALL=y' "$output_dir/.config"
grep -qx 'BR2_PACKAGE_HOST_LINUX_HEADERS_CUSTOM_3_10=y' "$output_dir/.config"
grep -qx 'BR2_PRIMARY_SITE="https://sources.buildroot.net"' \
  "$output_dir/.config"
grep -qx "BR2_KERNEL_HEADERS_CUSTOM_TARBALL_LOCATION=\"$kernel_tarball_url\"" \
  "$output_dir/.config"
runuser -u builder -- env $common_env \
  make -C "$source_dir/buildroot" O="$output_dir" \
  BR2_EXTERNAL="$source_dir" toolchain-all-source

test -d "$source_dir/dl"
printf '%s  %s\n' "$kernel_tarball_sha256" \
  "$source_dir/dl/linux/linux-3.10.14.tar.xz" | sha256sum -c -
find "$source_dir/dl" -mindepth 2 -maxdepth 2 -type d -name git -print | \
  while IFS= read -r git_cache; do
    test -d "$git_cache/.git"
    rm -rf -- "$git_cache"
  done
test -z "$(find "$source_dir/dl" -name .git -print -quit)"
tar --sort=name --mtime="@$source_epoch" --owner=0 --group=0 \
  --exclude='*/.git' --exclude='*/.git/*' \
  --numeric-owner -cf "$result_dir/toolchain-download-cache.tar" \
  -C "$source_dir" dl
chmod 0400 "$result_dir/toolchain-download-cache.tar"
