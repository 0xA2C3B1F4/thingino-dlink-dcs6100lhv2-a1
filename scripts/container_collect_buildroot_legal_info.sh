#!/bin/sh
set -eu
umask 077

workspace=/workspace
source_dir=$workspace/source
output_dir=$workspace/thingino-output
result_dir=/result
vendor_site=/input/vendor-site
audio_link=/input/media-link/libaudioProcess.so
rust_source=/input/rust-source
rust_toolchain=/input/rust-toolchain
ingenic_toolchain=$workspace/ingenic-glibc216-toolchain
ingenic_toolchain_archive=/input/ingenic-glibc216-toolchain.tar
supplemental_license=/input/RTL8188FU-COPYING.supplement
profile=dlink_dcs6100lhv2_a1_t31n_os02g10_rtl8188fu
source_epoch=1786006608
supplemental_sha256=4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e
mxml_version=4.0.4
mxml_source_filename=mxml-4.0.4.tar.gz
mxml_source_sha256_expected=c8d1728d6ccf71a862a1538bd5e132daa2181bb42fe14b078baa2ec1510c0150
mxml_license_sha256_expected=c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4
mxml_notice_sha256_expected=528125ed9bea128efa97005f9d1b0656b483508a9777c123c026d1d3058a38ac
mxml_override_recipe=$source_dir/package/thingino-mxml/mxml-override.mk
mxml_hash_file=$source_dir/package/all-patches/mxml/mxml.hash
linux_version=3.10.14
linux_source_filename=linux-3.10.14.tar.xz
linux_source_archive_member=linux-3.10.14/COPYING
linux_copying_sha256_expected=af8067302947c01fd9eee72befa54c7e3ef8a48fecde7fd71277f2290b2bf0f7
linux_license_files=COPYING
logcat_mini_version=29095262840a807d794ecbe4eda9ee2c425b5015
logcat_mini_source_filename=logcat-mini-29095262840a807d794ecbe4eda9ee2c425b5015-git4.tar.gz
logcat_mini_archive_license_member=logcat-mini-29095262840a807d794ecbe4eda9ee2c425b5015/LICENSE
logcat_mini_archive_copying_member=logcat-mini-29095262840a807d794ecbe4eda9ee2c425b5015/COPYING
logcat_mini_source_sha256_expected=3b8633bd44d600ca6f670979ae827e311eaf7464d98f0c6351f8e3e16e09fcd8
logcat_mini_license_sha256_expected=865ae85978be3b1da7943fb401412504dd21e92bb83227d5052c9a8c01ede388
logcat_mini_license=MIT
logcat_mini_license_files=LICENSE
logcat_mini_recipe=$source_dir/package/logcat-mini/logcat-mini.mk
certgen_recipe_sha256_expected=fb43fe008d9dcad613e6bf948f371fcd5142b08e5a16d346c450959ea00c404b
certgen_source_sha256_expected=512ca9723789ee4a703ce9ab4bdf2991988c0d76a0cbb356a6d850293f50d8ad
daynight_recipe_sha256_expected=36b8db2578fa3b07b143e2a43495d24bd1bbdcb339a5f4370db87609f1298302
daynight_source_sha256_expected=55cd93cfcb53c783d6868220d00251732f8d9a0142c1f1dff397bc48df8e0217
daynight_readme_sha256_expected=c9bc43788e95c5abcc949126c7d089853d3dc86ae81479db3af3a380fbc4cb15
local_package_inputs_verified=false
local_package_supplements_applied=false
phase=preflight
legal_info_exit_code=-1
supplement_applied=false
mxml_hash_override_applied=false
linux_license_override_applied=false
logcat_mini_license_override_applied=false
collection_present=false
complete=false
receipt_ready=false
supplement_destination=unmeasured
wifi_source_filename=unmeasured
wifi_source_sha256_before=unmeasured
wifi_source_sha256_after=unmeasured
wifi_source_unchanged=false
mxml_override_recipe_sha256=unmeasured
mxml_hash_file_sha256=unmeasured
mxml_source_archive=unmeasured
mxml_source_sha256_observed=unmeasured
mxml_license_sha256_observed=unmeasured
mxml_notice_sha256_observed=unmeasured
linux_source_archive=unmeasured
linux_source_sha256_before=unmeasured
linux_source_sha256_after=unmeasured
linux_source_unchanged=false
linux_archive_copying_sha256=unmeasured
linux_build_copying_sha256=unmeasured
logcat_mini_recipe_sha256=unmeasured
logcat_mini_source_archive=unmeasured
logcat_mini_source_sha256_before=unmeasured
logcat_mini_source_sha256_after=unmeasured
logcat_mini_source_unchanged=false
logcat_mini_archive_license_sha256=unmeasured
logcat_mini_build_license_sha256=unmeasured
logcat_mini_archive_copying_absent=false
logcat_mini_build_copying_absent=false
legal_info_export_verified=false
legal_info_export_file_count=0
legal_info_export_total_bytes=0
legal_info_export_tree_sha256=unmeasured
BUILDER_IMAGE_ID=${BUILDER_IMAGE_ID-unmeasured}
ORIGINAL_WORKSPACE_SHA256=${ORIGINAL_WORKSPACE_SHA256-unmeasured}
WORKSPACE_COPY_SHA256_BEFORE=${WORKSPACE_COPY_SHA256_BEFORE-unmeasured}
EXPECTED_INGENIC_TOOLCHAIN_SHA256=${EXPECTED_INGENIC_TOOLCHAIN_SHA256-unmeasured}

write_receipt() {
  receipt_status=$1
  observed_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  cat >"$result_dir/collection-receipt.json" <<EOF
{
  "schema_version": 1,
  "status": "$receipt_status",
  "phase": "$phase",
  "observed_at": "$observed_at",
  "command": "make -k -j1 -C /workspace/source/buildroot O=/workspace/thingino-output BR2_EXTERNAL=/workspace/source MXML_HASH_FILES=/workspace/source/package/all-patches/mxml/mxml.hash BR2_LINUX_KERNEL_LICENSE_FILES=COPYING LOGCAT_MINI_LICENSE=MIT LOGCAT_MINI_LICENSE_FILES=LICENSE legal-info",
  "command_exit_code": $legal_info_exit_code,
  "complete": $complete,
  "collection_present": $collection_present,
  "legal_review_approved": false,
  "publication_authorized": false,
  "builder_image_id": "$BUILDER_IMAGE_ID",
  "original_workspace_sha256": "$ORIGINAL_WORKSPACE_SHA256",
  "workspace_copy_sha256_before": "$WORKSPACE_COPY_SHA256_BEFORE",
  "supplement": {
    "applied": $supplement_applied,
    "sha256": "$supplemental_sha256",
    "destination": "$supplement_destination",
    "scope": "copied workspace only",
    "description": "Project-supplied text for the missing RTL8188FU COPYING reference; not an upstream grant or a per-file licensing decision"
  },
  "rtl8188fu_source_archive": {
    "filename": "$wifi_source_filename",
    "sha256_before": "$wifi_source_sha256_before",
    "sha256_after": "$wifi_source_sha256_after",
    "unchanged": $wifi_source_unchanged
  },
  "mxml_hash_selection": {
    "applied": $mxml_hash_override_applied,
    "make_variable": "MXML_HASH_FILES",
    "scope": "collector legal-info command on the copied workspace only",
    "meaning": "Selects the retained Thingino mxml 4.0.4 hash declarations for Buildroot source and legal-license checks; does not edit expected hashes or disable verification",
    "version_override_recipe": "$mxml_override_recipe",
    "version_override_recipe_sha256": "$mxml_override_recipe_sha256",
    "selected_hash_file": "$mxml_hash_file",
    "selected_hash_file_sha256": "$mxml_hash_file_sha256",
    "version": "$mxml_version",
    "source_archive": "$mxml_source_archive",
    "source_sha256_expected": "$mxml_source_sha256_expected",
    "source_sha256_observed": "$mxml_source_sha256_observed",
    "license_sha256_expected": "$mxml_license_sha256_expected",
    "license_sha256_observed": "$mxml_license_sha256_observed",
    "notice_sha256_expected": "$mxml_notice_sha256_expected",
    "notice_sha256_observed": "$mxml_notice_sha256_observed"
  },
  "linux_license_selection": {
    "applied": $linux_license_override_applied,
    "make_variable": "BR2_LINUX_KERNEL_LICENSE_FILES",
    "value": "$linux_license_files",
    "scope": "collector legal-info command on the copied workspace only",
    "meaning": "Selects the license file present in the retained Linux 3.10.14 source; does not add files, change license text, or hide Buildroot warnings",
    "version": "$linux_version",
    "source_archive": "$linux_source_archive",
    "source_sha256_before": "$linux_source_sha256_before",
    "source_sha256_after": "$linux_source_sha256_after",
    "source_unchanged": $linux_source_unchanged,
    "archive_copying_sha256": "$linux_archive_copying_sha256",
    "build_copying_sha256": "$linux_build_copying_sha256",
    "copying_sha256_expected": "$linux_copying_sha256_expected",
    "archive_binding": "The retained archive is inside the workspace image identified by workspace_copy_sha256_before; its whole-file SHA-256 is recorded here"
  },
  "logcat_mini_license_selection": {
    "applied": $logcat_mini_license_override_applied,
    "make_variables": {
      "LOGCAT_MINI_LICENSE": "$logcat_mini_license",
      "LOGCAT_MINI_LICENSE_FILES": "$logcat_mini_license_files"
    },
    "scope": "collector legal-info command on the copied workspace only",
    "meaning": "Corrects the retained logcat-mini commit's stale GPL-2.0/COPYING package metadata to its hash-bound MIT LICENSE; does not edit the recipe, source archive, or license text",
    "version": "$logcat_mini_version",
    "original_recipe": "$logcat_mini_recipe",
    "original_recipe_sha256": "$logcat_mini_recipe_sha256",
    "original_license_declaration": "GPL-2.0",
    "original_license_files_declaration": "COPYING",
    "source_archive": "$logcat_mini_source_archive",
    "source_sha256_expected": "$logcat_mini_source_sha256_expected",
    "source_sha256_before": "$logcat_mini_source_sha256_before",
    "source_sha256_after": "$logcat_mini_source_sha256_after",
    "source_unchanged": $logcat_mini_source_unchanged,
    "archive_license_sha256": "$logcat_mini_archive_license_sha256",
    "build_license_sha256": "$logcat_mini_build_license_sha256",
    "license_sha256_expected": "$logcat_mini_license_sha256_expected",
    "archive_copying_absent": $logcat_mini_archive_copying_absent,
    "build_copying_absent": $logcat_mini_build_copying_absent,
    "license_identity": "MIT License; Copyright (c) 2024 wltechblog"
  },
  "local_package_license_supplements": {
    "inputs_verified": $local_package_inputs_verified,
    "applied": $local_package_supplements_applied,
    "scope": "missing LICENSE files in copied build directories only",
    "canonical_gpl2_text_sha256": "$supplemental_sha256",
    "meaning": "Delivers the text named by existing package declarations; does not create grants, change source recipes, or override file-level license terms",
    "mbedtls_certgen": {
      "version": "1.0",
      "declaration": "GPL-2.0+",
      "recipe_sha256_expected": "$certgen_recipe_sha256_expected",
      "source_sha256_expected": "$certgen_source_sha256_expected"
    },
    "thingino_daynightd": {
      "version": "2.0.0",
      "recipe_declaration": "GPL-2.0",
      "readme_declaration": "GNU GPL v2.0",
      "c_header_declaration": "GPL version 2 or later",
      "recipe_sha256_expected": "$daynight_recipe_sha256_expected",
      "source_sha256_expected": "$daynight_source_sha256_expected",
      "readme_sha256_expected": "$daynight_readme_sha256_expected"
    }
  },
  "legal_info_export": {
    "method": "exclusive byte copy without source metadata",
    "verified": $legal_info_export_verified,
    "file_count": $legal_info_export_file_count,
    "total_bytes": $legal_info_export_total_bytes,
    "tree_sha256": "$legal_info_export_tree_sha256"
  },
  "limitations": [
    "Buildroot legal-info warnings are preserved and require review",
    "THINGINO_CONTROL_LICENSE is Unknown and has no LICENSE_FILES declaration",
    "Other selected packages still have unresolved missing license files or grant provenance",
    "This collection is not a legal approval or a complete corresponding-source determination"
  ]
}
EOF
  chmod 0400 "$result_dir/collection-receipt.json"
}

finish() {
  rc=$?
  trap - EXIT
  set +e
  if [ "$receipt_ready" = true ]; then
    if [ "$complete" = true ] && [ "$rc" -eq 0 ]; then
      write_receipt complete
    else
      complete=false
      write_receipt failed
    fi
  fi
  exit "$rc"
}
trap finish EXIT

test -d "$result_dir"
test -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)"
receipt_ready=true
test -d "$source_dir/.git"
test -f "$output_dir/.config"
test -d "$source_dir/dl"
test -f "$vendor_site/build-site.private.json"
test -f "$audio_link"
test ! -L "$audio_link"
test -x "$rust_toolchain/bin/rustc"
test -f "$rust_source/library/Cargo.toml"
test -x "$ingenic_toolchain/bin/mips-linux-gnu-gcc"
test -f "$ingenic_toolchain_archive"
test ! -L "$ingenic_toolchain_archive"
test -f "$supplemental_license"
test -f "$mxml_override_recipe"
test -f "$mxml_hash_file"
test -f "$logcat_mini_recipe"

# This pinned Buildroot consumes <PACKAGE>_HASH_FILES for both downloads and
# legal-license checks. Select the already-retained Thingino 4.0.4 hashes only
# for this collector invocation; do not replace hashes or waive verification.
grep -Fq '$(2)_HASH_FILES = \' "$source_dir/buildroot/package/pkg-generic.mk"
grep -Fq '$$($(2)_HASH_FILES))$$(sep))' "$source_dir/buildroot/package/pkg-generic.mk"
grep -Fq '$(foreach f,$($(PKG)_HASH_FILES),-H' "$source_dir/buildroot/package/pkg-download.mk"
test "$(sed -n 's/^override MXML_VERSION = \(.*\)$/\1/p' "$mxml_override_recipe")" = "$mxml_version"
grep -Fqx 'override MXML_SOURCE = mxml-$(MXML_VERSION).tar.gz' "$mxml_override_recipe"

mxml_source_hash_declaration=$(awk -v name="$mxml_source_filename" \
  '$1 == "sha256" && $3 == name { print $2 }' "$mxml_hash_file")
mxml_license_hash_declaration=$(awk \
  '$1 == "sha256" && $3 == "LICENSE" { print $2 }' "$mxml_hash_file")
mxml_notice_hash_declaration=$(awk \
  '$1 == "sha256" && $3 == "NOTICE" { print $2 }' "$mxml_hash_file")
test "$mxml_source_hash_declaration" = "$mxml_source_sha256_expected"
test "$mxml_license_hash_declaration" = "$mxml_license_sha256_expected"
test "$mxml_notice_hash_declaration" = "$mxml_notice_sha256_expected"

mxml_source_archive_matches=$(find "$source_dir/dl" -type f -name "$mxml_source_filename")
test "$(printf '%s\n' "$mxml_source_archive_matches" | wc -l)" -eq 1
test -f "$mxml_source_archive_matches"
mxml_source_archive=$mxml_source_archive_matches
mxml_source_sha256_observed=$(sha256sum "$mxml_source_archive"); mxml_source_sha256_observed=${mxml_source_sha256_observed%% *}
test "$mxml_source_sha256_observed" = "$mxml_source_hash_declaration"

mxml_build=$output_dir/build/mxml-$mxml_version
test -f "$mxml_build/LICENSE"
test -f "$mxml_build/NOTICE"
mxml_license_sha256_observed=$(sha256sum "$mxml_build/LICENSE"); mxml_license_sha256_observed=${mxml_license_sha256_observed%% *}
mxml_notice_sha256_observed=$(sha256sum "$mxml_build/NOTICE"); mxml_notice_sha256_observed=${mxml_notice_sha256_observed%% *}
test "$mxml_license_sha256_observed" = "$mxml_license_hash_declaration"
test "$mxml_notice_sha256_observed" = "$mxml_notice_hash_declaration"
mxml_override_recipe_sha256=$(sha256sum "$mxml_override_recipe"); mxml_override_recipe_sha256=${mxml_override_recipe_sha256%% *}
mxml_hash_file_sha256=$(sha256sum "$mxml_hash_file"); mxml_hash_file_sha256=${mxml_hash_file_sha256%% *}

# Buildroot's current default names SPDX license paths added after Linux 3.10.
# This retained 3.10.14 archive has the version-appropriate top-level COPYING.
grep -Fqx 'LINUX_LICENSE_FILES = $(call qstrip,$(BR2_LINUX_KERNEL_LICENSE_FILES))' \
  "$source_dir/buildroot/linux/linux.mk"
grep -Fq 'default "COPYING LICENSES/preferred/GPL-2.0 LICENSES/exceptions/Linux-syscall-note"' \
  "$source_dir/buildroot/linux/Config.in"
grep -Fqx '# Official kernel.org tarball -- no custom git needed' "$source_dir/thingino.mk"
test "$(sed -n 's/^BR2_LINUX_KERNEL_VERSION="\(.*\)"$/\1/p' "$output_dir/.config")" = "$linux_version"
test "$(sed -n 's/^BR2_LINUX_KERNEL_LICENSE_FILES="\(.*\)"$/\1/p' "$output_dir/.config")" = \
  'COPYING LICENSES/preferred/GPL-2.0 LICENSES/exceptions/Linux-syscall-note'

linux_source_archive_matches=$(find "$source_dir/dl" -type f -name "$linux_source_filename")
test "$(printf '%s\n' "$linux_source_archive_matches" | wc -l)" -eq 1
test -f "$linux_source_archive_matches"
linux_source_archive=$linux_source_archive_matches
linux_source_sha256_before=$(sha256sum "$linux_source_archive"); linux_source_sha256_before=${linux_source_sha256_before%% *}
linux_archive_copying_sha256=$(tar -xOf "$linux_source_archive" "$linux_source_archive_member" | sha256sum); linux_archive_copying_sha256=${linux_archive_copying_sha256%% *}
test "$linux_archive_copying_sha256" = "$linux_copying_sha256_expected"

linux_build=$output_dir/build/linux-$linux_version
test -f "$linux_build/COPYING"
test ! -e "$linux_build/LICENSES/preferred/GPL-2.0"
test ! -e "$linux_build/LICENSES/exceptions/Linux-syscall-note"
linux_build_copying_sha256=$(sha256sum "$linux_build/COPYING"); linux_build_copying_sha256=${linux_build_copying_sha256%% *}
test "$linux_build_copying_sha256" = "$linux_archive_copying_sha256"

# BEGIN_LOGCAT_MINI_LICENSE_PREFLIGHT
# The retained logcat-mini commit contains an MIT LICENSE, while its package
# recipe still names GPL-2.0/COPYING. Bind the collector-only correction to the
# exact recipe declarations, archive, and extracted license before overriding.
test "$(sed -n 's/^LOGCAT_MINI_VERSION = \(.*\)$/\1/p' "$logcat_mini_recipe")" = \
  "$logcat_mini_version"
test "$(sed -n 's/^LOGCAT_MINI_LICENSE = \(.*\)$/\1/p' "$logcat_mini_recipe")" = 'GPL-2.0'
test "$(sed -n 's/^LOGCAT_MINI_LICENSE_FILES = \(.*\)$/\1/p' "$logcat_mini_recipe")" = 'COPYING'
grep -Fqx 'LOGCAT_MINI_SITE_METHOD = git' "$logcat_mini_recipe"
grep -Fqx 'LOGCAT_MINI_SITE = https://github.com/wltechblog/logcat-mini' "$logcat_mini_recipe"
grep -Fqx 'LOGCAT_MINI_SITE_BRANCH = main' "$logcat_mini_recipe"
logcat_mini_recipe_sha256=$(sha256sum "$logcat_mini_recipe"); logcat_mini_recipe_sha256=${logcat_mini_recipe_sha256%% *}

logcat_mini_source_archive_matches=$(find "$source_dir/dl" -type f -name "$logcat_mini_source_filename")
test "$(printf '%s\n' "$logcat_mini_source_archive_matches" | wc -l)" -eq 1
test -f "$logcat_mini_source_archive_matches"
logcat_mini_source_archive=$logcat_mini_source_archive_matches
logcat_mini_source_sha256_before=$(sha256sum "$logcat_mini_source_archive"); logcat_mini_source_sha256_before=${logcat_mini_source_sha256_before%% *}
test "$logcat_mini_source_sha256_before" = "$logcat_mini_source_sha256_expected"
logcat_mini_archive_entries=$(tar -tzf "$logcat_mini_source_archive")
printf '%s\n' "$logcat_mini_archive_entries" | grep -Fqx "$logcat_mini_archive_license_member"
if printf '%s\n' "$logcat_mini_archive_entries" | grep -Fqx "$logcat_mini_archive_copying_member"; then
  exit 1
fi
logcat_mini_archive_copying_absent=true
logcat_mini_archive_license_sha256=$(tar -xOzf "$logcat_mini_source_archive" "$logcat_mini_archive_license_member" | sha256sum); logcat_mini_archive_license_sha256=${logcat_mini_archive_license_sha256%% *}
test "$logcat_mini_archive_license_sha256" = "$logcat_mini_license_sha256_expected"

logcat_mini_build=$output_dir/build/logcat-mini-$logcat_mini_version
test -f "$logcat_mini_build/LICENSE"
test ! -e "$logcat_mini_build/COPYING"
logcat_mini_build_copying_absent=true
logcat_mini_build_license_sha256=$(sha256sum "$logcat_mini_build/LICENSE"); logcat_mini_build_license_sha256=${logcat_mini_build_license_sha256%% *}
test "$logcat_mini_build_license_sha256" = "$logcat_mini_archive_license_sha256"
# END_LOGCAT_MINI_LICENSE_PREFLIGHT

# BEGIN_LOCAL_PACKAGE_LICENSE_PREFLIGHT
# These exact local packages already declare GPL terms, but omit the text
# named by LICENSE_FILES. Bind source and build copies before supplying text.
local_license_check() {
  test -f "$1"
  test ! -L "$1"
  local_license_hash=$(sha256sum "$1")
  local_license_hash=${local_license_hash%% *}
  test "$local_license_hash" = "$2"
}
certgen_package=$source_dir/package/mbedtls-certgen
certgen_build=$output_dir/build/mbedtls-certgen-1.0
daynight_package=$source_dir/package/thingino-daynightd
daynight_build=$output_dir/build/thingino-daynightd-2.0.0
verify_local_package_license_inputs() {
  local_package_inputs_verified=false
  local_license_check "$supplemental_license" "$supplemental_sha256"
  local_license_check "$certgen_package/mbedtls-certgen.mk" "$certgen_recipe_sha256_expected"
  local_license_check "$certgen_package/files/mbedtls-certgen.c" "$certgen_source_sha256_expected"
  local_license_check "$certgen_build/mbedtls-certgen.c" "$certgen_source_sha256_expected"
  local_license_check "$daynight_package/thingino-daynightd.mk" "$daynight_recipe_sha256_expected"
  local_license_check "$daynight_package/files/daynightd.c" "$daynight_source_sha256_expected"
  local_license_check "$daynight_build/files/daynightd.c" "$daynight_source_sha256_expected"
  local_license_check "$daynight_package/files/README.md" "$daynight_readme_sha256_expected"
  local_license_check "$daynight_build/files/README.md" "$daynight_readme_sha256_expected"
  local_package_inputs_verified=true
}
verify_local_package_license_inputs
for local_license_directory in "$certgen_build" "$daynight_build"; do
  test -d "$local_license_directory"
  test ! -L "$local_license_directory"
  test ! -e "$local_license_directory/LICENSE"
  test ! -L "$local_license_directory/LICENSE"
done
test ! -e "$certgen_package/files/LICENSE"
test ! -L "$certgen_package/files/LICENSE"
test ! -e "$daynight_package/LICENSE"
test ! -L "$daynight_package/LICENSE"
# END_LOCAL_PACKAGE_LICENSE_PREFLIGHT

actual_supplemental_sha256=$(sha256sum "$supplemental_license"); actual_supplemental_sha256=${actual_supplemental_sha256%% *}
test "$actual_supplemental_sha256" = "$supplemental_sha256"
actual_ingenic_sha256=$(sha256sum "$ingenic_toolchain_archive"); actual_ingenic_sha256=${actual_ingenic_sha256%% *}
test "$actual_ingenic_sha256" = "$EXPECTED_INGENIC_TOOLCHAIN_SHA256"
wifi_recipe=$source_dir/package/wifi-rtl8188fu/wifi-rtl8188fu.mk
test -f "$wifi_recipe"
wifi_version=$(sed -n 's/^WIFI_RTL8188FU_VERSION = \([0-9a-f]*\)$/\1/p' "$wifi_recipe")
test "${#wifi_version}" -eq 40
wifi_build=$output_dir/build/wifi-rtl8188fu-$wifi_version
test -d "$wifi_build"
test ! -e "$wifi_build/COPYING"
supplement_destination=$wifi_build/COPYING
wifi_source_archive=$(find "$source_dir/dl" -type f -name "*$wifi_version*.tar*")
test "$(printf '%s\n' "$wifi_source_archive" | wc -l)" -eq 1
test -f "$wifi_source_archive"
wifi_source_filename=$(basename "$wifi_source_archive")
wifi_source_sha256_before=$(sha256sum "$wifi_source_archive"); wifi_source_sha256_before=${wifi_source_sha256_before%% *}

# BEGIN_LOCAL_PACKAGE_LICENSE_APPLY
install -m 0644 "$supplemental_license" "$certgen_build/LICENSE"
install -m 0644 "$supplemental_license" "$daynight_build/LICENSE"
chown builder:builder "$certgen_build/LICENSE" "$daynight_build/LICENSE"
local_license_check "$certgen_build/LICENSE" "$supplemental_sha256"
local_license_check "$daynight_build/LICENSE" "$supplemental_sha256"
local_package_supplements_applied=true
# END_LOCAL_PACKAGE_LICENSE_APPLY

install -m 0644 "$supplemental_license" "$wifi_build/COPYING"
chown builder:builder "$wifi_build/COPYING"
supplement_applied=true
phase=legal-info
mxml_hash_override_applied=true
linux_license_override_applied=true
logcat_mini_license_override_applied=true
set +e
timeout --signal=TERM --kill-after=60s 7200s \
  runuser -u builder -- env \
  LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC SOURCE_DATE_EPOCH=$source_epoch \
  THINGINO_CONTAINER_BUILD=1 CAMERA=$profile PRISTINE=1 CCACHE_DISABLE=1 \
  BR2_DL_DIR=$source_dir/dl THINGINO_OUTPUT_DIR=$output_dir \
  DCS6100_VENDOR_BUNDLE_DIR=$vendor_site \
  DCS6100_AUDIOPROCESS_LINK_FILE=$audio_link \
  DCS6100_RUST_TOOLCHAIN_DIR=$rust_toolchain \
  DCS6100_RUST_SOURCE_DIR=$rust_source \
  DCS6100_INGENIC_TOOLCHAIN_DIR=$ingenic_toolchain WORKFLOW=1 \
  make -k -j1 -C "$source_dir/buildroot" O="$output_dir" \
  BR2_EXTERNAL="$source_dir" MXML_HASH_FILES="$mxml_hash_file" \
  BR2_LINUX_KERNEL_LICENSE_FILES="$linux_license_files" \
  LOGCAT_MINI_LICENSE="$logcat_mini_license" \
  LOGCAT_MINI_LICENSE_FILES="$logcat_mini_license_files" legal-info \
  >"$result_dir/buildroot-legal-info.log" 2>&1
legal_info_exit_code=$?
set -e
chmod 0400 "$result_dir/buildroot-legal-info.log"

phase=collect
verify_local_package_license_inputs
local_license_check "$certgen_build/LICENSE" "$supplemental_sha256"
local_license_check "$daynight_build/LICENSE" "$supplemental_sha256"
wifi_source_sha256_after=$(sha256sum "$wifi_source_archive"); wifi_source_sha256_after=${wifi_source_sha256_after%% *}
test "$wifi_source_sha256_after" = "$wifi_source_sha256_before"
wifi_source_unchanged=true
linux_source_sha256_after=$(sha256sum "$linux_source_archive"); linux_source_sha256_after=${linux_source_sha256_after%% *}
test "$linux_source_sha256_after" = "$linux_source_sha256_before"
linux_source_unchanged=true
logcat_mini_source_sha256_after=$(sha256sum "$logcat_mini_source_archive"); logcat_mini_source_sha256_after=${logcat_mini_source_sha256_after%% *}
test "$logcat_mini_source_sha256_after" = "$logcat_mini_source_sha256_before"
logcat_mini_source_unchanged=true
if [ -d "$output_dir/legal-info" ]; then
  # Docker Desktop may reject metadata changes on APFS bind mounts. Copy only
  # directory and file bytes, reject unsafe entry types, then verify every file.
  export_stats=$(python3 - "$output_dir/legal-info" "$result_dir/legal-info" <<'PY'
# BEGIN_BYTE_ONLY_EXPORT
import hashlib
import os
import stat
import sys
from pathlib import Path


source_root = Path(sys.argv[1])
destination_root = Path(sys.argv[2])
if source_root.is_symlink() or not source_root.is_dir():
    raise SystemExit("legal-info source is not a real directory")
if destination_root.exists() or destination_root.is_symlink():
    raise SystemExit("legal-info destination already exists")


def open_source(path: Path):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError(f"unsupported source entry: {path}")
    return os.fdopen(descriptor, "rb")


def open_destination(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def digest_file(path: Path):
    digest = hashlib.sha256()
    size = 0
    with open_source(path) as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


source_manifest = {}
source_directories = set()
os.mkdir(destination_root, 0o700)


def copy_directory(source_directory: Path, destination_directory: Path, prefix: Path):
    if not stat.S_ISDIR(os.stat(source_directory, follow_symlinks=False).st_mode):
        raise RuntimeError(f"unsupported source directory: {source_directory}")
    with os.scandir(source_directory) as iterator:
        entries = sorted(iterator, key=lambda entry: entry.name)
    for entry in entries:
        relative = prefix / entry.name
        source_path = source_directory / entry.name
        destination_path = destination_directory / entry.name
        mode = entry.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            os.mkdir(destination_path, 0o700)
            source_directories.add(relative.as_posix())
            copy_directory(source_path, destination_path, relative)
        elif stat.S_ISREG(mode):
            digest = hashlib.sha256()
            size = 0
            with open_source(source_path) as source, open_destination(destination_path) as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            source_manifest[relative.as_posix()] = (size, digest.hexdigest())
        else:
            raise RuntimeError(f"unsupported source entry: {source_path}")


copy_directory(source_root, destination_root, Path())
destination_manifest = {}
destination_directories = set()
for root, directory_names, file_names in os.walk(destination_root, followlinks=False):
    root_path = Path(root)
    for name in directory_names:
        path = root_path / name
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f"unsupported destination entry: {path}")
        destination_directories.add(path.relative_to(destination_root).as_posix())
    for name in file_names:
        path = root_path / name
        relative = path.relative_to(destination_root).as_posix()
        destination_manifest[relative] = digest_file(path)
if destination_directories != source_directories or destination_manifest != source_manifest:
    raise RuntimeError("legal-info byte verification failed")

tree_digest = hashlib.sha256()
total_bytes = 0
for relative in sorted(source_directories):
    tree_digest.update(b"directory\0")
    tree_digest.update(relative.encode("utf-8", "surrogateescape"))
    tree_digest.update(b"\n")
for relative, (size, digest) in sorted(source_manifest.items()):
    tree_digest.update(b"file\0")
    tree_digest.update(relative.encode("utf-8", "surrogateescape"))
    tree_digest.update(b"\0")
    tree_digest.update(str(size).encode("ascii"))
    tree_digest.update(b"\0")
    tree_digest.update(digest.encode("ascii"))
    tree_digest.update(b"\n")
    total_bytes += size
print(len(source_manifest), total_bytes, tree_digest.hexdigest())
# END_BYTE_ONLY_EXPORT
PY
  )
  set -- $export_stats
  test "$#" -eq 3
  legal_info_export_file_count=$1
  legal_info_export_total_bytes=$2
  legal_info_export_tree_sha256=$3
  test "$legal_info_export_file_count" -gt 0
  test "${#legal_info_export_tree_sha256}" -eq 64
  legal_info_export_verified=true
  collection_present=true
fi
if [ "$legal_info_exit_code" -eq 0 ] && \
  [ -f "$result_dir/legal-info/README" ] && \
  [ -f "$result_dir/legal-info/manifest.csv" ] && \
  [ -f "$result_dir/legal-info/host-manifest.csv" ] && \
  [ -f "$result_dir/legal-info/buildroot.config" ] && \
  [ -f "$result_dir/legal-info/legal-info.sha256" ] && \
  [ -d "$result_dir/legal-info/licenses" ] && \
  [ -d "$result_dir/legal-info/host-licenses" ] && \
  [ -d "$result_dir/legal-info/sources" ] && \
  [ -d "$result_dir/legal-info/host-sources" ]; then
  complete=true
  phase=complete
  exit 0
fi
phase=failed
if [ "$legal_info_exit_code" -eq 0 ]; then legal_info_exit_code=1; fi
exit "$legal_info_exit_code"
