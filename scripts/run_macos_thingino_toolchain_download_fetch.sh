#!/bin/sh
set -eu

usage() {
  echo "usage: $0 --task-scratch-root DIR --builder-lock DIR --builder-image IMAGE --container-name NAME --source-checkout DIR --workspace-image FILE --result DIR" >&2
  exit 2
}

task_scratch_root=
builder_lock=
builder_image=
container_name=
source_checkout=
workspace_image=
result_dir=

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-scratch-root) task_scratch_root=${2-}; shift 2 ;;
    --builder-lock) builder_lock=${2-}; shift 2 ;;
    --builder-image) builder_image=${2-}; shift 2 ;;
    --container-name) container_name=${2-}; shift 2 ;;
    --source-checkout) source_checkout=${2-}; shift 2 ;;
    --workspace-image) workspace_image=${2-}; shift 2 ;;
    --result) result_dir=${2-}; shift 2 ;;
    *) usage ;;
  esac
done

for value in "$task_scratch_root" "$builder_lock" "$builder_image" \
  "$container_name" "$source_checkout" "$workspace_image" "$result_dir"; do
  [ -n "$value" ] || usage
done

[ "$(uname -s)" = Darwin ] || {
  echo "this runner is only for macOS Docker Desktop" >&2
  exit 1
}
[ -d "$task_scratch_root" ] && [ ! -L "$task_scratch_root" ] && \
  [ -w "$task_scratch_root" ] || {
  echo "task scratch root must be a writable non-symlink directory" >&2
  exit 1
}
task_scratch_root=$(cd "$task_scratch_root" && pwd -P)
case "$task_scratch_root" in
  /) echo "task scratch root must not be the filesystem root" >&2; exit 1 ;;
  /*) ;;
  *) echo "task scratch root must resolve to an absolute path" >&2; exit 1 ;;
esac
[ -d "$builder_lock" ] && [ -f "$builder_lock/OWNER.md" ] && \
  [ ! -L "$builder_lock/OWNER.md" ] || {
  echo "builder lock and OWNER.md are required" >&2
  exit 1
}
[ -d "$source_checkout/.git" ] && [ ! -L "$source_checkout" ] || {
  echo "pinned Thingino source checkout is missing or invalid" >&2
  exit 1
}
[ -d "$result_dir" ] && [ ! -L "$result_dir" ] && \
  [ -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)" ] || {
  echo "result directory must be empty" >&2
  exit 1
}
[ ! -e "$workspace_image" ] && [ ! -L "$workspace_image" ] || {
  echo "workspace image must not already exist" >&2
  exit 1
}
workspace_parent=$(cd "$(dirname "$workspace_image")" && pwd -P)
workspace_name=$(basename "$workspace_image")
case "$workspace_parent/$workspace_name" in
  "$task_scratch_root"/*) ;;
  *) echo "resolved workspace image escaped the task scratch root" >&2; exit 1 ;;
esac
case "$container_name" in
  *[!A-Za-z0-9_.-]*|'') echo "invalid container name" >&2; exit 1 ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo "docker is required" >&2
  exit 1
}
docker image inspect "$builder_image" >/dev/null
if docker container inspect "$container_name" >/dev/null 2>&1; then
  echo "container name already exists: $container_name" >&2
  exit 1
fi
for container in $(docker ps -q); do
  if docker top "$container" -eo args 2>/dev/null | \
    grep -Eq 'container_fetch_thingino|container_build_thingino|container_build_split_kernels|qemu[^ ]*|cargo .*mipsel'; then
    echo "another MIPS or Thingino builder is active: $container" >&2
    exit 1
  fi
done

script_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
build_script="$script_root/scripts/container_fetch_thingino_toolchain_downloads.sh"
[ -f "$build_script" ] && [ ! -L "$build_script" ] || {
  echo "repository toolchain download-fetch script is missing" >&2
  exit 1
}

docker run --rm --network none --platform linux/arm64 \
  -v "$workspace_parent:/host" \
  "$builder_image" /bin/sh -c \
  'truncate -s 20G "/host/$1" && mkfs.ext4 -q -F -L dcs6100-tc-fetch "/host/$1"' \
  toolchain-download-workspace "$workspace_name"

docker run --rm --name "$container_name" --privileged \
  --platform linux/arm64 --entrypoint /bin/sh \
  -v "$build_script:/build.sh:ro" \
  -v "$source_checkout:/input/source:ro" \
  -v "$workspace_image:/input/workspace.ext4" \
  -v "$result_dir:/result" \
  "$builder_image" -c \
  'mkdir -p /workspace; mount -o loop /input/workspace.ext4 /workspace; trap "umount /workspace" EXIT HUP INT TERM; /build.sh'
