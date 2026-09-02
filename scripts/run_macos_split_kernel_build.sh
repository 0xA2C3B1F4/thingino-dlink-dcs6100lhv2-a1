#!/bin/sh
set -eu

usage() {
  echo "usage: $0 --task-scratch-root DIR --builder-lock DIR --builder-image IMAGE --container-name NAME --workspace-image FILE --installer-fragment FILE --final-fragment FILE --vendor-site DIR --audio-link FILE --rust-source DIR --rust-toolchain DIR --ingenic-toolchain-archive FILE --result DIR" >&2
  exit 2
}

task_scratch_root=
builder_lock=
builder_image=
container_name=
workspace_image=
installer_fragment=
final_fragment=
vendor_site=
audio_link=
rust_source=
rust_toolchain=
ingenic_toolchain_archive=
result_dir=

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-scratch-root) task_scratch_root=${2-}; shift 2 ;;
    --builder-lock) builder_lock=${2-}; shift 2 ;;
    --builder-image) builder_image=${2-}; shift 2 ;;
    --container-name) container_name=${2-}; shift 2 ;;
    --workspace-image) workspace_image=${2-}; shift 2 ;;
    --installer-fragment) installer_fragment=${2-}; shift 2 ;;
    --final-fragment) final_fragment=${2-}; shift 2 ;;
    --vendor-site) vendor_site=${2-}; shift 2 ;;
    --audio-link) audio_link=${2-}; shift 2 ;;
    --rust-source) rust_source=${2-}; shift 2 ;;
    --rust-toolchain) rust_toolchain=${2-}; shift 2 ;;
    --ingenic-toolchain-archive) ingenic_toolchain_archive=${2-}; shift 2 ;;
    --result) result_dir=${2-}; shift 2 ;;
    *) usage ;;
  esac
done

for value in "$task_scratch_root" "$builder_lock" "$builder_image" "$container_name" \
  "$workspace_image" "$installer_fragment" "$final_fragment" \
  "$vendor_site" "$audio_link" "$rust_source" "$rust_toolchain" \
  "$ingenic_toolchain_archive" "$result_dir"; do
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
[ -f "$workspace_image" ] && [ ! -L "$workspace_image" ] || {
  echo "completed Thingino workspace image is required" >&2
  exit 1
}
workspace_parent=$(cd "$(dirname "$workspace_image")" && pwd -P)
workspace_name=$(basename "$workspace_image")
case "$workspace_parent/$workspace_name" in
  "$task_scratch_root"/*) ;;
  *) echo "resolved workspace image escaped the task scratch root" >&2; exit 1 ;;
esac
[ -d "$builder_lock" ] && [ -f "$builder_lock/OWNER.md" ] && \
  [ ! -L "$builder_lock/OWNER.md" ] || {
  echo "builder lock and OWNER.md are required" >&2
  exit 1
}
for file in "$installer_fragment" "$final_fragment"; do
  [ -f "$file" ] && [ ! -L "$file" ] || {
    echo "kernel fragment is missing or symlinked: $file" >&2
    exit 1
  }
done
for directory in "$vendor_site" "$rust_source" "$rust_toolchain"; do
  [ -d "$directory" ] && [ ! -L "$directory" ] || {
    echo "required input directory is missing or symlinked: $directory" >&2
    exit 1
  }
done
[ -f "$audio_link" ] && [ ! -L "$audio_link" ] || {
  echo "audio link input is missing or symlinked" >&2
  exit 1
}
[ -f "$ingenic_toolchain_archive" ] && [ ! -L "$ingenic_toolchain_archive" ] || {
  echo "Ingenic toolchain archive is missing or symlinked" >&2
  exit 1
}
[ -d "$result_dir" ] && [ ! -L "$result_dir" ] || {
  echo "result directory is missing or symlinked" >&2
  exit 1
}
[ -z "$(find "$result_dir" -mindepth 1 -maxdepth 1 -print -quit)" ] || {
  echo "result directory must be empty" >&2
  exit 1
}
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
  if docker inspect "$container" --format '{{range .Mounts}}{{println .Destination}}{{end}}' | \
      grep -qx '/build.sh' || \
    docker top "$container" -eo args 2>/dev/null | \
      grep -Eq 'container_build_thingino|container_build_split_kernels|qemu[^ ]*|cargo .*mipsel'; then
    echo "another MIPS or Thingino builder is active: $container" >&2
    exit 1
  fi
done

script_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
build_script="$script_root/scripts/container_build_split_kernels.sh"
[ -f "$build_script" ] && [ ! -L "$build_script" ] || {
  echo "repository split-kernel build script is missing" >&2
  exit 1
}

resolved_image=$(docker image inspect "$builder_image" --format '{{.Id}}')
echo "builder_image_id=$resolved_image"
docker run --name "$container_name" --network none --privileged \
  --platform linux/arm64 --entrypoint /bin/sh \
  -v "$build_script:/build.sh:ro" \
  -v "$installer_fragment:/input/installer-kernel.fragment:ro" \
  -v "$final_fragment:/input/final-kernel.fragment:ro" \
  -v "$vendor_site:/input/vendor-site:ro" \
  -v "$audio_link:/input/media-link/libaudioProcess.so:ro" \
  -v "$rust_source:/input/rust-source:ro" \
  -v "$rust_toolchain:/input/rust-toolchain:ro" \
  -v "$ingenic_toolchain_archive:/input/ingenic-glibc216-toolchain.tar:ro" \
  -v "$workspace_image:/input/workspace.ext4" \
  -v "$result_dir:/result" \
  "$builder_image" -c \
  'mkdir -p /workspace; mount -o loop /input/workspace.ext4 /workspace; trap "umount /workspace" EXIT HUP INT TERM; /build.sh'
