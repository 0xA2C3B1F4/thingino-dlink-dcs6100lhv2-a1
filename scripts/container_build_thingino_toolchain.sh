#!/bin/sh
set -eu

source_input=/input/source
download_cache=/input/toolchain-download-cache.tar
workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/thingino-toolchain-output
result_dir=/result
board=toolchain_xburst1_glibc_gcc16
source_epoch=1786006608
archive_name=thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz
archive_sha256=9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412
archive_root=mipsel-thingino-linux-gnu_sdk-buildroot
kernel_tarball_url=https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.10.14.tar.xz
kernel_tarball_sha256=36540d5fb15951be64d4c150cf3bc291a8d4d6699fb988173f6be30aa1e41b47

test -d "$source_input/.git"
test -f "$download_cache"
test -d "$workspace"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"
test ! -e "$source_dir"

install -d -o builder -g builder "$source_dir" "$output_dir"
cp -a "$source_input/." "$source_dir/"
tar -xf "$download_cache" -C "$source_dir"
chown -R builder:builder "$source_dir" "$output_dir"
printf '%s  %s\n' "$kernel_tarball_sha256" \
  "$source_dir/dl/linux/linux-3.10.14.tar.xz" | sha256sum -c -

common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch PRISTINE=1 CCACHE_DISABLE=1 BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir WORKFLOW=1"
# Keep host libtool output independent of the amd64 compatibility libraries
# installed for the legacy Ingenic compiler. This is the dlsearch path emitted
# by the pinned Debian arm64 base before that compatibility layer is added.
libtool_dlsearch_path="/lib /usr/lib /usr/local/lib/aarch64-linux-gnu /lib/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu /usr/local/lib "

runuser -u builder -- env $common_env \
  "lt_cv_sys_lib_dlsearch_path_spec=$libtool_dlsearch_path" \
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
  "lt_cv_sys_lib_dlsearch_path_spec=$libtool_dlsearch_path" \
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
  "lt_cv_sys_lib_dlsearch_path_spec=$libtool_dlsearch_path" \
  make -C "$source_dir/buildroot" O="$output_dir" \
  BR2_EXTERNAL="$source_dir" toolchain

host_dir=$output_dir/host
test -d "$host_dir"
install -m 0755 "$source_dir/buildroot/support/misc/relocate-sdk.sh" \
  "$host_dir/relocate-sdk.sh"
install -d "$host_dir/share/buildroot"
(
  export LC_ALL=C
  grep -lr "$host_dir" "$host_dir" | sort | while IFS= read -r file; do
    if file -b --mime-type "$file" | grep -q '^text/' && \
      test "$file" != "$host_dir/share/buildroot/sdk-location" && \
      test "$file" != "$host_dir/share/buildroot/sdk-relocs"; then
      printf '%s\n' "$file"
    fi
  done
) | sed "s|^$host_dir|.|" > "$host_dir/share/buildroot/sdk-relocs"
printf '%s\n' "$host_dir" > "$host_dir/share/buildroot/sdk-location"

# Some toolchain packages leave the archive symbol-table timestamp at the
# wall-clock build time even under BR2_REPRODUCIBLE.  The members themselves
# are reproducible, so rewrite each real ar archive with deterministic index
# metadata before making the SDK tarball.  A few glibc objects use an .a suffix
# without being archives and must not be passed to ranlib.
find "$host_dir" -type f -name '*.a' | sort | while IFS= read -r archive; do
  if test "$(head -c 7 "$archive")" = '!<arch>'; then
    "$host_dir/bin/mipsel-thingino-linux-gnu-ranlib" -D "$archive"
  fi
done

normalized=$workspace/toolchain-normalized
install -d "$normalized/$archive_root"
cp -a "$host_dir/." "$normalized/$archive_root/"
tar --sort=name --mtime="@$source_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - -C "$normalized" "$archive_root" | \
  gzip -n -9 > "$result_dir/$archive_name"
printf '%s  %s\n' "$archive_sha256" "$result_dir/$archive_name" | \
  sha256sum -c -
chmod 0400 "$result_dir/$archive_name"
