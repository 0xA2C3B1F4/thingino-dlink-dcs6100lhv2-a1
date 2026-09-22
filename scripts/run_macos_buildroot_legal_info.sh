#!/bin/sh
set -eu
umask 077

usage() {
  echo "usage: $0 --task-scratch-root DIR --builder-lock DIR --builder-image IMAGE --expected-builder-image-id SHA256 --container-name NAME --original-workspace-image FILE --workspace-copy-image FILE --expected-original-sha256 SHA256 --vendor-site DIR --audio-link FILE --rust-source DIR --rust-toolchain DIR --ingenic-toolchain-archive FILE --expected-ingenic-toolchain-sha256 SHA256 --result DIR" >&2
  exit 2
}

task_scratch_root= builder_lock= builder_image= expected_builder_image_id=
container_name= original_workspace_image= workspace_copy_image=
expected_original_sha256= vendor_site= audio_link= rust_source= rust_toolchain=
ingenic_toolchain_archive= expected_ingenic_toolchain_sha256= result_dir=

reject_symlink_path() {
  candidate=$1
  case "$candidate" in
    /*) ;;
    *) echo "mount paths must be absolute: $1" >&2; exit 1 ;;
  esac
  while [ "$candidate" != / ]; do
    [ ! -L "$candidate" ] || {
      echo "mount path contains a symlink: $1" >&2
      exit 1
    }
    candidate=$(dirname "$candidate")
  done
}

resolved_file() {
  file_parent=$(cd "$(dirname "$1")" && pwd -P)
  printf '%s/%s\n' "$file_parent" "$(basename "$1")"
}

paths_overlap() {
  case "$1" in "$2"|"$2"/*) return 0 ;; esac
  case "$2" in "$1"|"$1"/*) return 0 ;; esac
  return 1
}

file_identity() {
  stat -f '%d:%i' "$1" 2>/dev/null || stat -c '%d:%i' "$1"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-scratch-root) task_scratch_root=${2-}; shift 2 ;;
    --builder-lock) builder_lock=${2-}; shift 2 ;;
    --builder-image) builder_image=${2-}; shift 2 ;;
    --expected-builder-image-id) expected_builder_image_id=${2-}; shift 2 ;;
    --container-name) container_name=${2-}; shift 2 ;;
    --original-workspace-image) original_workspace_image=${2-}; shift 2 ;;
    --workspace-copy-image) workspace_copy_image=${2-}; shift 2 ;;
    --expected-original-sha256) expected_original_sha256=${2-}; shift 2 ;;
    --vendor-site) vendor_site=${2-}; shift 2 ;;
    --audio-link) audio_link=${2-}; shift 2 ;;
    --rust-source) rust_source=${2-}; shift 2 ;;
    --rust-toolchain) rust_toolchain=${2-}; shift 2 ;;
    --ingenic-toolchain-archive) ingenic_toolchain_archive=${2-}; shift 2 ;;
    --expected-ingenic-toolchain-sha256) expected_ingenic_toolchain_sha256=${2-}; shift 2 ;;
    --result) result_dir=${2-}; shift 2 ;;
    *) usage ;;
  esac
done

for value in "$task_scratch_root" "$builder_lock" "$builder_image" \
  "$expected_builder_image_id" "$container_name" "$original_workspace_image" \
  "$workspace_copy_image" "$expected_original_sha256" "$vendor_site" \
  "$audio_link" "$rust_source" "$rust_toolchain" "$ingenic_toolchain_archive" \
  "$expected_ingenic_toolchain_sha256" "$result_dir"; do
  [ -n "$value" ] || usage
done

[ "$(uname -s)" = Darwin ] || {
  echo "this runner is only for macOS Docker Desktop" >&2
  exit 1
}
case "$container_name" in
  *[!A-Za-z0-9_.-]*|'') echo "invalid container name" >&2; exit 1 ;;
esac
case "$expected_builder_image_id" in
  sha256:*) [ "${#expected_builder_image_id}" -eq 71 ] ;;
  *) false ;;
esac || { echo "expected builder image ID must be sha256:<64 lowercase hex>" >&2; exit 1; }
case "${expected_builder_image_id#sha256:}" in
  *[!0-9a-f]*) echo "expected builder image ID must be sha256:<64 lowercase hex>" >&2; exit 1 ;;
esac
for digest in "$expected_original_sha256" "$expected_ingenic_toolchain_sha256"; do
  [ "${#digest}" -eq 64 ] || { echo "expected SHA-256 must be 64 lowercase hex" >&2; exit 1; }
  case "$digest" in *[!0-9a-f]*) echo "expected SHA-256 must be 64 lowercase hex" >&2; exit 1 ;; esac
done

[ -d "$task_scratch_root" ] && [ ! -L "$task_scratch_root" ] && \
  [ -w "$task_scratch_root" ] || {
  echo "task scratch root must be a writable non-symlink directory" >&2
  exit 1
}
reject_symlink_path "$task_scratch_root"
task_scratch_root=$(cd "$task_scratch_root" && pwd -P)
case "$task_scratch_root" in /) echo "task scratch root must not be the filesystem root" >&2; exit 1 ;; esac
[ -d "$builder_lock" ] && [ -f "$builder_lock/OWNER.md" ] && \
  [ ! -L "$builder_lock/OWNER.md" ] || {
  echo "builder lock and OWNER.md are required" >&2
  exit 1
}
reject_symlink_path "$builder_lock"
reject_symlink_path "$builder_lock/OWNER.md"
builder_lock=$(cd "$builder_lock" && pwd -P)
case "$builder_lock" in
  "$task_scratch_root"/*) ;;
  *) echo "builder lock escaped the task scratch root" >&2; exit 1 ;;
esac
for file in "$original_workspace_image" "$workspace_copy_image" \
  "$audio_link" "$ingenic_toolchain_archive"; do
  [ -f "$file" ] && [ ! -L "$file" ] || {
    echo "required file is missing or symlinked: $file" >&2
    exit 1
  }
  reject_symlink_path "$file"
done
for directory in "$vendor_site" "$rust_source" "$rust_toolchain" "$result_dir"; do
  [ -d "$directory" ] && [ ! -L "$directory" ] || {
    echo "required directory is missing or symlinked: $directory" >&2
    exit 1
  }
  reject_symlink_path "$directory"
done
[ -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)" ] || {
  echo "result directory must be empty" >&2
  exit 1
}
for path in "$workspace_copy_image" "$result_dir"; do
  parent=$(cd "$(dirname "$path")" && pwd -P)
  resolved="$parent/$(basename "$path")"
  case "$resolved" in "$task_scratch_root"/*) ;; *) echo "task-owned output escaped the task scratch root" >&2; exit 1 ;; esac
done
original_workspace_image=$(resolved_file "$original_workspace_image")
workspace_copy_image=$(resolved_file "$workspace_copy_image")
audio_link=$(resolved_file "$audio_link")
ingenic_toolchain_archive=$(resolved_file "$ingenic_toolchain_archive")
vendor_site=$(cd "$vendor_site" && pwd -P)
rust_source=$(cd "$rust_source" && pwd -P)
rust_toolchain=$(cd "$rust_toolchain" && pwd -P)
result_dir=$(cd "$result_dir" && pwd -P)
[ "$original_workspace_image" != "$workspace_copy_image" ] || {
  echo "workspace copy must differ from the accepted original" >&2
  exit 1
}
[ "$(file_identity "$original_workspace_image")" != \
  "$(file_identity "$workspace_copy_image")" ] || {
  echo "workspace copy must not be a hardlink to the accepted original" >&2
  exit 1
}

script_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
container_script="$script_root/scripts/container_collect_buildroot_legal_info.sh"
supplemental_license="$script_root/third_party/licenses/RTL8188FU-GPL-2.0-only.txt"
for file in "$container_script" "$supplemental_license"; do
  [ -f "$file" ] && [ ! -L "$file" ] || { echo "repository legal-info input is missing" >&2; exit 1; }
done
reject_symlink_path "$container_script"
reject_symlink_path "$supplemental_license"
container_script=$(resolved_file "$container_script")
supplemental_license=$(resolved_file "$supplemental_license")

for writable in "$workspace_copy_image" "$result_dir"; do
  for readonly in "$original_workspace_image" "$container_script" \
    "$supplemental_license" "$vendor_site" "$audio_link" "$rust_source" \
    "$rust_toolchain" "$ingenic_toolchain_archive"; do
    paths_overlap "$writable" "$readonly" && {
      echo "writable mount overlaps a read-only input" >&2
      exit 1
    }
  done
done
copy_identity=$(file_identity "$workspace_copy_image")
for readonly_file in "$original_workspace_image" "$container_script" \
  "$supplemental_license" "$audio_link" "$ingenic_toolchain_archive"; do
  [ "$copy_identity" != "$(file_identity "$readonly_file")" ] || {
    echo "workspace copy must not hardlink a read-only input" >&2
    exit 1
  }
done
supplemental_sha256=$(shasum -a 256 "$supplemental_license"); supplemental_sha256=${supplemental_sha256%% *}
[ "$supplemental_sha256" = 4f2416509c30c4f0c4de101cc30c571e3be33fdb5c42cabc0d2915e8ed5ea51e ] || {
  echo "RTL8188FU supplemental license identity mismatch" >&2
  exit 1
}
original_sha256=$(shasum -a 256 "$original_workspace_image"); original_sha256=${original_sha256%% *}
copy_sha256=$(shasum -a 256 "$workspace_copy_image"); copy_sha256=${copy_sha256%% *}
[ "$original_sha256" = "$expected_original_sha256" ] && \
  [ "$copy_sha256" = "$expected_original_sha256" ] || {
  echo "workspace copy is not byte-identical to the expected accepted original" >&2
  exit 1
}
ingenic_sha256=$(shasum -a 256 "$ingenic_toolchain_archive"); ingenic_sha256=${ingenic_sha256%% *}
[ "$ingenic_sha256" = "$expected_ingenic_toolchain_sha256" ] || {
  echo "Ingenic toolchain input identity mismatch" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
resolved_image=$(docker image inspect "$builder_image" --format '{{.Id}}')
[ "$resolved_image" = "$expected_builder_image_id" ] || {
  echo "builder image ID mismatch" >&2
  exit 1
}
if docker container inspect "$container_name" >/dev/null 2>&1; then
  echo "container name already exists: $container_name" >&2
  exit 1
fi
for container in $(docker ps -q); do
  if docker inspect "$container" --format '{{range .Mounts}}{{println .Destination}}{{end}}' | grep -Eq '^/(build|collect)\.sh$' || \
    docker top "$container" -eo args 2>/dev/null | grep -Eq 'container_(build_thingino|build_split_kernels|collect_buildroot_legal_info)|qemu[^ ]*|cargo .*mipsel'; then
    echo "another MIPS or Thingino builder is active: $container" >&2
    exit 1
  fi
done

chmod 0700 "$result_dir"
set +e
docker run --name "$container_name" --network none --privileged \
  --platform linux/arm64 --entrypoint /bin/sh \
  -e "ORIGINAL_WORKSPACE_SHA256=$expected_original_sha256" \
  -e "WORKSPACE_COPY_SHA256_BEFORE=$copy_sha256" \
  -e "BUILDER_IMAGE_ID=$resolved_image" \
  -e "EXPECTED_INGENIC_TOOLCHAIN_SHA256=$expected_ingenic_toolchain_sha256" \
  -v "$container_script:/collect.sh:ro" \
  -v "$supplemental_license:/input/RTL8188FU-COPYING.supplement:ro" \
  -v "$original_workspace_image:/input/original-workspace.ext4:ro" \
  -v "$workspace_copy_image:/input/workspace.ext4" \
  -v "$vendor_site:/input/vendor-site:ro" \
  -v "$audio_link:/input/media-link/libaudioProcess.so:ro" \
  -v "$rust_source:/input/rust-source:ro" \
  -v "$rust_toolchain:/input/rust-toolchain:ro" \
  -v "$ingenic_toolchain_archive:/input/ingenic-glibc216-toolchain.tar:ro" \
  -v "$result_dir:/result" \
  "$builder_image" -c \
  'mkdir -p /workspace; mount -o loop /input/workspace.ext4 /workspace; trap "umount /workspace" EXIT HUP INT TERM; /collect.sh'
container_status=$?
set -e
original_after=$(shasum -a 256 "$original_workspace_image"); original_after=${original_after%% *}
if [ "$original_after" = "$expected_original_sha256" ]; then
  original_unchanged=true
else
  original_unchanged=false
fi
observed_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
cat >"$result_dir/original-workspace-postcheck.json" <<EOF
{
  "schema_version": 1,
  "observed_at": "$observed_at",
  "expected_sha256": "$expected_original_sha256",
  "observed_sha256": "$original_after",
  "unchanged": $original_unchanged,
  "container_exit_code": $container_status
}
EOF
chmod 0400 "$result_dir/original-workspace-postcheck.json"
[ "$original_after" = "$expected_original_sha256" ] || {
  echo "accepted original workspace changed despite its read-only mount" >&2
  exit 1
}
exit "$container_status"
