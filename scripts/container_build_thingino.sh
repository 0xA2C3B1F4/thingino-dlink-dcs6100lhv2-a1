#!/bin/sh
set -eu

source_input=/input/source
download_cache=/input/download-cache.tar
vendor_site=/input/vendor-site
audio_link=/input/media-link/libaudioProcess.so
rust_toolchain=/input/rust-toolchain
rust_source=/input/rust-source
ingenic_toolchain_archive=/input/ingenic-glibc216-toolchain.tar
webui_toolchain=/opt/dcs6100-webui
workspace=/workspace
ingenic_toolchain=$workspace/ingenic-glibc216-toolchain
source_dir=$workspace/source
webui_source=$source_dir/dcs6100-webui
webui_dist=$webui_source/dist
output_dir=$workspace/thingino-output
result_dir=/result
profile=dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu
source_epoch=1786006608
mtd3_payload_limit=8125952
case "$(uname -m)" in
	x86_64)
		echo "x86_64 Thingino firmware build is unavailable: source-built SDK reproducibility is not validated" >&2
		exit 1
		;;
	aarch64)
		build_toolchain_archive=thingino-toolchain-aarch64_xburst1_glibc_gcc16-linux-mipsel.tar.gz
		build_toolchain_sha256=9871abf2b79138fdfa2b684cf0f142bfbc3a37a4553e4af3b56804ea6a1b3412
		;;
	*)
		echo "unsupported builder architecture" >&2
		exit 1
		;;
esac

test -f "$source_input/dcs6100-source-preparation.json"
test -f "$source_input/configs/cameras-exp/$profile/${profile}_defconfig"
test -f "$download_cache"
test -f "$vendor_site/build-site.private.json"
test -f "$audio_link"
test ! -L "$audio_link"
test "$(wc -c <"$audio_link")" -eq 75168
printf '%s  %s\n' \
	f892759f47e0296ea175bf4247f661a11381037bafec7800326298d73d0a7273 "$audio_link" \
	| sha256sum -c -
test -x "$rust_toolchain/bin/rustc"
test -f "$rust_source/library/Cargo.toml"
test -f "$ingenic_toolchain_archive"
test ! -L "$ingenic_toolchain_archive"
test ! -e "$ingenic_toolchain"
tar -xf "$ingenic_toolchain_archive" -C "$workspace"
test -d "$ingenic_toolchain"
test ! -L "$ingenic_toolchain"
test -x "$ingenic_toolchain/bin/mips-linux-gnu-gcc"
test -x "$webui_toolchain/node_modules/.bin/esbuild"
test "$(node -e 'process.stdout.write(require("/opt/dcs6100-webui/node_modules/esbuild").version)')" = 0.25.9
test "$(find "$vendor_site/files" -mindepth 1 -maxdepth 1 -type f | wc -l)" -eq 3
test -d "$workspace"
test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"

if [ ! -d "$source_dir/.git" ]; then
	install -d -o builder -g builder "$source_dir"
	cp -a "$source_input/." "$source_dir/"
	tar -xf "$download_cache" -C "$source_dir"
	chown -R builder:builder "$source_dir"
	runuser -u builder -- git -C "$source_dir" init -q
	runuser -u builder -- git -C "$source_dir" \
		symbolic-ref HEAD refs/heads/master
	runuser -u builder -- git -C "$source_dir" add -A
	runuser -u builder -- env \
		GIT_AUTHOR_DATE="@$source_epoch +0000" \
		GIT_COMMITTER_DATE="@$source_epoch +0000" \
		git -C "$source_dir" \
		-c user.name=installer-builder -c user.email=installer@invalid \
		commit -q -m prepared-source
fi
cmp "$source_input/dcs6100-source-preparation.json" \
	"$source_dir/dcs6100-source-preparation.json"
printf '%s  %s\n' "$build_toolchain_sha256" \
	"$source_dir/dl/toolchain-external-custom/$build_toolchain_archive" \
	| sha256sum -c -

test -f "$webui_source/index.html"
test -f "$webui_source/public/manifest.webmanifest"
test -f "$webui_source/src/main.ts"
test -f "$webui_source/src/styles.css"
test -f "$webui_source/tsconfig.json"
runuser -u builder -- rm -rf "$webui_dist"
runuser -u builder -- mkdir -p "$webui_dist/assets"
runuser -u builder -- "$webui_toolchain/node_modules/.bin/esbuild" \
	"$webui_source/src/main.ts" \
	--bundle \
	--entry-names=app \
	--format=esm \
	--legal-comments=none \
	--minify \
	--outdir="$webui_dist/assets" \
	--tsconfig="$webui_source/tsconfig.json" \
	--target=es2020
runuser -u builder -- cp "$webui_source/index.html" "$webui_dist/index.html"
runuser -u builder -- cp "$webui_source/public/manifest.webmanifest" \
	"$webui_dist/manifest.webmanifest"
webui_asset_version=$(cat "$webui_dist/assets/app.js" \
	"$webui_dist/assets/app.css" | sha256sum | cut -c1-16)
test "${#webui_asset_version}" -eq 16
runuser -u builder -- sed -i \
	-e "s|/assets/app.css|/assets/app.css?v=$webui_asset_version|" \
	-e "s|/assets/app.js|/assets/app.js?v=$webui_asset_version|" \
	"$webui_dist/index.html"
test "$(cd "$webui_dist" && find . -type f -print | LC_ALL=C sort)" = "./assets/app.css
./assets/app.js
./index.html
./manifest.webmanifest"
printf '%s  %s\n' \
	5c5c9b40289f0f52cb9bab571c41519cf3c6e8a82d06bd66ae3852be96d5ea9e "$webui_dist/assets/app.css" \
	7180124793f453ef542d654c0b8e8899fe05e7754349bf9870face07985f3798 "$webui_dist/assets/app.js" \
	be4dc858466807072af04b972470e504938a577cd77aa12177dd8c0156480bb9 "$webui_dist/index.html" \
	38bda3647c9bb6976180108beedeceb0dfde4068a3f157a18ae8527e8d553ce5 "$webui_dist/manifest.webmanifest" \
	| sha256sum -c -
test "$(wc -c <"$webui_dist/assets/app.css")" -eq 26213
test "$(wc -c <"$webui_dist/assets/app.js")" -eq 161712
test "$(wc -c <"$webui_dist/index.html")" -eq 603
test "$(wc -c <"$webui_dist/manifest.webmanifest")" -eq 188
test -z "$(grep -RIlE '/x/[^[:space:]]*\.cgi|agent\.cgi|thingino[-_ ]agent|/bin/(ba)?sh|child_process' "$webui_dist" | head -n 1)"

install -d -o builder -g builder "$output_dir"
common_env="HOME=/home/builder LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch THINGINO_CONTAINER_BUILD=1 CAMERA=$profile PRISTINE=1 CCACHE_DISABLE=1 BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir DCS6100_VENDOR_BUNDLE_DIR=$vendor_site DCS6100_AUDIOPROCESS_LINK_FILE=$audio_link DCS6100_RUST_TOOLCHAIN_DIR=$rust_toolchain DCS6100_RUST_SOURCE_DIR=$rust_source DCS6100_INGENIC_TOOLCHAIN_DIR=$ingenic_toolchain WORKFLOW=1"

test ! -f "$output_dir/.config" || unlink "$output_dir/.config"
runuser -u builder -- env $common_env \
	make -C "$source_dir" CAMERA="$profile" GROUP=exp defconfig
runuser -u builder -- env $common_env \
	make -C "$source_dir" CAMERA="$profile" GROUP=exp \
		build
if grep -R -Fq 'error while loading shared libraries:' "$output_dir/logs" 2>/dev/null; then
	echo "Buildroot host tool failed to load a shared library" >&2
	exit 1
fi

rootfs=$output_dir/images/rootfs.squashfs
linux_config=$(find "$output_dir/build" -mindepth 2 -maxdepth 2 -type f -path '*/linux-*/.config')
tx_isp=$(find "$output_dir/target/usr/lib/modules" -type f -name 'tx-isp-t31.ko')
sensor=$(find "$output_dir/target/usr/lib/modules" -type f -name 'sensor_os02g10_t31.ko')
iq=$output_dir/target/usr/share/sensor/os02g10-t31.bin
timezone_catalog=$output_dir/target/usr/share/tz.json
test -f "$rootfs"
test "$(wc -c <"$rootfs")" -le "$mtd3_payload_limit"
test "$(printf '%s\n' "$linux_config" | wc -l)" -eq 1
test "$(printf '%s\n' "$tx_isp" | wc -l)" -eq 1
test "$(printf '%s\n' "$sensor" | wc -l)" -eq 1
test -f "$iq"
test -s "$timezone_catalog"
grep -Fq '"n":"Europe/Helsinki","v":"EET-2EEST,M3.5.0/3,M10.5.0/4"' \
	"$timezone_catalog"
prudynt_package=$output_dir/per-package/prudynt-t/target/usr/bin/prudynt
prudynt_merged=$output_dir/target/usr/bin/prudynt
prudynt_normalized=$output_dir/build/.dlink-prudynt-final-gate
test -x "$prudynt_merged"
test -x "$prudynt_package"
rm -f "$prudynt_normalized"
trap 'rm -f "$prudynt_normalized"' EXIT HUP INT TERM
"$output_dir/per-package/prudynt-t/host/bin/mipsel-linux-objcopy" \
	--remove-section=.comment "$prudynt_package" "$prudynt_normalized"
cmp "$prudynt_normalized" "$prudynt_merged"
rm -f "$prudynt_normalized"
trap - EXIT HUP INT TERM
grep -aFq '/snapshot' "$output_dir/target/usr/bin/prudynt"
grep -aFq 'loopback ingress required' "$output_dir/target/usr/bin/prudynt"
grep -aFq 'per-request JPEG quality and size are unsupported' \
	"$output_dir/target/usr/bin/prudynt"
grep -aFq 'http.loopback_only' "$output_dir/target/usr/bin/prudynt"
grep -aFq '127.0.0.1:' "$output_dir/target/usr/bin/prudynt"
grep -aFq 'D-Link media restart: replacing process without SDK teardown' \
	"$output_dir/target/usr/bin/prudynt"
grep -aFq '/etc/TZ' "$output_dir/target/usr/bin/prudynt"
grep -aFq 'HTTPMJPEG: pthread_create failed' \
	"$output_dir/target/usr/bin/prudynt"
grep -Fq '"loopback_only"' "$output_dir/target/etc/prudynt.json"
test -x "$output_dir/target/usr/sbin/wpa_supplicant"
test -x "$output_dir/target/usr/sbin/dropbear"
test -x "$output_dir/target/usr/sbin/mdnsd"
test -x "$output_dir/target/usr/sbin/thingino-controld"
test -x "$output_dir/target/etc/init.d/S95thingino-control"
test -x "$output_dir/target/sbin/mkfs.vfat"
test -x "$output_dir/target/usr/lib/mdev/automount"
test ! -e "$output_dir/target/usr/sbin/formatsd"
test ! -e "$output_dir/target/usr/sbin/envfromcard"
printf '%s  %s\n' \
	d8da684a19b1eac7b5f69844495cfd1c4d04f28db792b9eb605b6deb3978f271 "$output_dir/target/usr/lib/mdev/automount" \
	| sha256sum -c -
test -x "$output_dir/target/etc/init.d/S06ircut"
test -x "$output_dir/target/etc/init.d/S31prudynt"
printf '%s  %s\n' \
	491761453de5c8dc9eee17d1cf5775530f683ab5df4ca8894bd910882c51a861 "$output_dir/target/etc/init.d/S31prudynt" \
	| sha256sum -c -
grep -Fq 'IFS= read -r TZ_VALUE < /etc/TZ' \
	"$output_dir/target/etc/init.d/S31prudynt"
test "$(grep -c 'usleep 100000' "$output_dir/target/etc/init.d/S06ircut")" -eq 2
grep -Fq 'rm -f /run/transfer.bin /run/transfer.footer' \
	"$output_dir/target/etc/init.d/S06ircut"
test -z "$(grep -E '\bircut (on|off)\b' "$output_dir/target/etc/init.d/S06ircut" || true)"
test -z "$(grep -E 'curl|thingino-api\.key|API_URL' "$output_dir/target/etc/init.d/S31prudynt" || true)"
test -x "$output_dir/target/usr/bin/uhttpd"
test -x "$output_dir/target/usr/sbin/onvif-httpd"
test -x "$output_dir/target/etc/init.d/S94onvif-httpd"
test -d "$output_dir/target/usr/share/onvif"
test ! -e "$output_dir/target/usr/sbin/thingino-agentd"
test ! -e "$output_dir/target/usr/sbin/thingino-agent-rs"
test ! -e "$output_dir/target/usr/sbin/thingino-agentctl"
test ! -e "$output_dir/target/usr/libexec/thingino-agent/listener"
test ! -e "$output_dir/target/usr/libexec/thingino-agent/tls-proxy"
test ! -e "$output_dir/target/usr/libexec/thingino-agent/lib.sh"
test ! -e "$output_dir/target/usr/libexec/thingino-agent/adapters/null.sh"
test ! -e "$output_dir/target/usr/libexec/thingino-agent/adapters/prudynt.sh"
test ! -e "$output_dir/target/etc/init.d/S95thingino-agent"
test ! -e "$output_dir/target/var/www/x"
test ! -e "$output_dir/target/var/www/onvif"
test ! -e "$output_dir/target/var/www-portal"
test ! -e "$output_dir/target/usr/libexec/thingino-webui"
test ! -e "$output_dir/target/usr/sbin/recordmgr"
test ! -e "$output_dir/target/etc/init.d/S95recordmgr"
test ! -e "$output_dir/target/etc/init.d/S48webui-config"
test ! -e "$output_dir/target/etc/init.d/S91mqttsub"
test ! -e "$output_dir/target/usr/sbin/mqtt-sub-dispatcher"
test ! -e "$output_dir/target/usr/sbin/telegram-cam-register"
test ! -e "$output_dir/target/usr/sbin/telegram-cam-agent"
test -z "$(find "$output_dir/target" -type f -name '*.cgi' -print -quit)"
test "$(find "$output_dir/target" -print0 | tr -cd '\n' | wc -c)" -eq 0
test "$(find "$output_dir/target/usr/lib/modules" -mindepth 1 -maxdepth 1 \
	-type d | wc -l)" -eq 1
test "$(cd "$output_dir/target/var/www" && find . -type f -print | LC_ALL=C sort)" = "./assets/app.css
./assets/app.js
./index.html
./manifest.webmanifest"
printf '%s  %s\n' \
	5c5c9b40289f0f52cb9bab571c41519cf3c6e8a82d06bd66ae3852be96d5ea9e "$output_dir/target/var/www/assets/app.css" \
	7180124793f453ef542d654c0b8e8899fe05e7754349bf9870face07985f3798 "$output_dir/target/var/www/assets/app.js" \
	be4dc858466807072af04b972470e504938a577cd77aa12177dd8c0156480bb9 "$output_dir/target/var/www/index.html" \
	38bda3647c9bb6976180108beedeceb0dfde4068a3f157a18ae8527e8d553ce5 "$output_dir/target/var/www/manifest.webmanifest" \
	| sha256sum -c -
test -z "$(find "$output_dir/target/var/www" -type f -perm -0100 -print -quit)"
test -z "$(find "$output_dir/target/var/www" -type f -exec grep -Il '^#!.*\(sh\|ash\|bash\)' {} + | head -n 1)"
test -z "$(find "$output_dir/target" -path '*thingino-agent*' -print -quit)"
readelf_tool=$output_dir/host/bin/mipsel-linux-readelf
test -x "$readelf_tool"
test -z "$("$readelf_tool" -Ws "$output_dir/target/usr/bin/uhttpd" \
	| awk '$7 == "UND" && $8 ~ /^(vfork|exec|system|popen|posix_spawn)@/ { print; exit }')"
test "$("$readelf_tool" -Ws "$output_dir/target/usr/bin/uhttpd" \
	| awk '$7 == "UND" && $8 ~ /^fork@/ { count++ } END { print count + 0 }')" -eq 1
uhttpd_build=$(find "$output_dir/build" -mindepth 1 -maxdepth 1 -type d \
	-name 'thingino-uhttpd-*')
test "$(printf '%s\n' "$uhttpd_build" | wc -l)" -eq 1
test "$(find "$uhttpd_build" -type f \( -name '*.c' -o -name '*.h' \) \
	-exec sed -E 's@/\*.*\*/@@g; s@".*"@@g; s@//.*@@g' {} + \
	| grep -Eo '(^|[^[:alnum:]_])fork[[:space:]]*\(' | wc -l)" -eq 1
grep -Fq 'if (!nofork)' "$uhttpd_build/main.c"
grep -Fq 'switch (fork())' "$uhttpd_build/main.c"
test -z "$("$readelf_tool" -Ws "$output_dir/target/usr/bin/uhttpd" \
	| awk '$8 ~ /^(uh_create_process|uh_interpreter_add|cgi_dispatch)$/ { print; exit }')"
test -z "$("$readelf_tool" -d "$output_dir/target/usr/bin/uhttpd" \
	| grep -E 'Shared library: \[(libjct|libjson_script|libblobmsg_json|libjson-c)' \
	| head -n 1)"
test -z "$(strings "$output_dir/target/usr/bin/uhttpd" \
	| grep -E '(^/cgi-bin$|CGI execution|CGI/1\.1|Invalid interpreter|handler script)' \
	| head -n 1)"
test -z "$("$readelf_tool" -Ws "$output_dir/target/usr/sbin/onvif-httpd" \
	| awk '$7 == "UND" && $8 ~ /^(fork|vfork|exec|system|popen|posix_spawn)@/ { print; exit }')"
grep -Eq 'UHTTPD_COMMON_ARGS=.*-f([[:space:]]|$)' \
	"$output_dir/target/etc/init.d/S60uhttpd"
test ! -e "$output_dir/target/usr/lib/libaudioProcess.so"
printf '%s  %s\n' \
	14b18d23964f18b63cef3a32ca7a6dc7ae8ee6ebb001c646ffa0ace72c2273fe "$output_dir/target/usr/lib/libimp.so" \
	40fd7eb9237772f705a92e9792325f07f0fe022479923f8dc67653cc11450ea1 "$output_dir/target/usr/lib/libalog.so" \
	befca6166d2e25b749cc9fff4798332f42a2d997b00d4aee713358d803353b79 "$output_dir/target/usr/lib/libsysutils.so" \
	| sha256sum -c -
grep -qx '# CONFIG_IPV6 is not set' "$linux_config"
grep -qx '# BR2_PACKAGE_THINGINO_KOPT_IPV6 is not set' "$output_dir/.config"
grep -qx '# BR2_PACKAGE_THINGINO_ODHCP6C is not set' "$output_dir/.config"

install -m 0400 "$rootfs" "$result_dir/thingino-base.squashfs"
install -m 0400 "$linux_config" "$result_dir/thingino-linux.config"
install -m 0400 "$source_input/dcs6100-source-preparation.json" \
	"$result_dir/source-preparation.json"

rootfs_size=$(wc -c <"$result_dir/thingino-base.squashfs")
rootfs_sha=$(sha256sum "$result_dir/thingino-base.squashfs")
rootfs_sha=${rootfs_sha%% *}
config_sha=$(sha256sum "$result_dir/thingino-linux.config")
config_sha=${config_sha%% *}
vendor_bundle_sha=$(sed -n 's/.*"source_vendor_bundle_sha256": "\([0-9a-f]*\)".*/\1/p' "$vendor_site/build-site.private.json")
test "${#vendor_bundle_sha}" -eq 64
tx_isp_sha=$(sha256sum "$tx_isp"); tx_isp_sha=${tx_isp_sha%% *}
sensor_sha=$(sha256sum "$sensor"); sensor_sha=${sensor_sha%% *}
iq_sha=$(sha256sum "$iq"); iq_sha=${iq_sha%% *}
webui_css_sha=$(sha256sum "$output_dir/target/var/www/assets/app.css"); webui_css_sha=${webui_css_sha%% *}
webui_js_sha=$(sha256sum "$output_dir/target/var/www/assets/app.js"); webui_js_sha=${webui_js_sha%% *}
webui_index_sha=$(sha256sum "$output_dir/target/var/www/index.html"); webui_index_sha=${webui_index_sha%% *}
webui_manifest_sha=$(sha256sum "$output_dir/target/var/www/manifest.webmanifest"); webui_manifest_sha=${webui_manifest_sha%% *}

cat >"$result_dir/thingino-base.manifest.json" <<EOF
{
  "schema_version": 1,
  "target": "DCS-6100LHV2-A1",
  "profile": "$profile",
  "source_date_epoch": $source_epoch,
  "public_ingenic_lib_archive": false,
  "vendor_bundle_sha256": "$vendor_bundle_sha",
  "rootfs": {"filename": "thingino-base.squashfs", "size": $rootfs_size, "sha256": "$rootfs_sha"},
  "linux_config": {"filename": "thingino-linux.config", "sha256": "$config_sha"},
  "native_media": {
    "tx_isp_sha256": "$tx_isp_sha",
    "sensor_os02g10_sha256": "$sensor_sha",
    "native_iq_sha256": "$iq_sha"
  },
  "webui": {
    "contract": "dlink-static-webui-v1",
    "total_size": 188716,
    "files": {
      "assets/app.css": {"size": 26213, "sha256": "$webui_css_sha"},
      "assets/app.js": {"size": 161712, "sha256": "$webui_js_sha"},
      "index.html": {"size": 603, "sha256": "$webui_index_sha"},
      "manifest.webmanifest": {"size": 188, "sha256": "$webui_manifest_sha"}
    }
  }
}
EOF
chmod 0400 "$result_dir/thingino-base.manifest.json"
