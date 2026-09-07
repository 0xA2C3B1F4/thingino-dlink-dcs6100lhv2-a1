#!/bin/sh
set -eu

source_input=/input/source
toolchain_input=/input/thingino-toolchain.tar.gz
workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/thingino-download-output
result_dir=/result
profile=dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu
source_epoch=1786006608
toolchain_archive=thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz
toolchain_sha256=9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412

test -f "$source_input/dcs6100-source-preparation.json"
test -f "$toolchain_input"
test ! -L "$toolchain_input"
printf '%s  %s\n' "$toolchain_sha256" "$toolchain_input" | sha256sum -c -
test -d "$workspace"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"
test ! -e "$source_dir"

install -d -o builder -g builder "$source_dir" "$output_dir" "$workspace/empty"
cp -a "$source_input/." "$source_dir/"
install -d "$source_dir/dl/toolchain-external-custom"
cp "$toolchain_input" \
  "$source_dir/dl/toolchain-external-custom/$toolchain_archive"
chown -R builder:builder "$source_dir" "$output_dir" "$workspace/empty"
runuser -u builder -- git -C "$source_dir" init -q
runuser -u builder -- git -C "$source_dir" symbolic-ref HEAD refs/heads/master
runuser -u builder -- git -C "$source_dir" add -A
runuser -u builder -- env \
  GIT_AUTHOR_DATE="@$source_epoch +0000" \
  GIT_COMMITTER_DATE="@$source_epoch +0000" \
  git -C "$source_dir" \
  -c user.name=installer-builder -c user.email=installer@invalid \
  commit -q -m prepared-source

common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch THINGINO_CONTAINER_BUILD=1 CAMERA=$profile PRISTINE=1 CCACHE_DISABLE=1 BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir DCS6100_VENDOR_BUNDLE_DIR=$workspace/empty DCS6100_AUDIOPROCESS_LINK_FILE=$workspace/empty/audio-link DCS6100_RUST_TOOLCHAIN_DIR=$workspace/empty DCS6100_RUST_SOURCE_DIR=$workspace/empty DCS6100_INGENIC_TOOLCHAIN_DIR=$workspace/empty GIT_ASKPASS=/bin/false GIT_CONFIG_COUNT=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_KEY_0=http.version GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_VALUE_0=HTTP/1.1 GIT_TERMINAL_PROMPT=0 WORKFLOW=1"

retry_network_fetch() {
  label=$1
  shift
  attempt=1
  while ! "$@"; do
    if [ "$attempt" -ge 3 ]; then
      echo "$label failed after $attempt attempts" >&2
      return 1
    fi
    delay=$((attempt * 5))
    echo "$label attempt $attempt failed; retrying in ${delay}s" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
  done
}

runuser -u builder -- env $common_env \
  make -C "$source_dir" CAMERA="$profile" GROUP=exp defconfig
retry_network_fetch "locked Buildroot source fetch" \
  runuser -u builder -- env $common_env \
    make -C "$source_dir" CAMERA="$profile" GROUP=exp source
# Thingino invokes this host dependency directly during the full build, so the
# generic source target does not discover it.
retry_network_fetch "locked host-libyaml source fetch" \
  runuser -u builder -- env $common_env \
    make -C "$source_dir/buildroot" O="$output_dir" \
      BR2_EXTERNAL="$source_dir" host-libyaml-source

test -d "$source_dir/dl"
tar --sort=name --mtime="@$source_epoch" --owner=0 --group=0 \
  --numeric-owner -cf "$result_dir/download-cache.tar" \
  -C "$source_dir" dl
chmod 0400 "$result_dir/download-cache.tar"
