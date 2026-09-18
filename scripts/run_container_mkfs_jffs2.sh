#!/bin/sh
set -eu

usage() {
  echo "usage: DCS6100_BUILDER_IMAGE=IMAGE $0 --little-endian --eraseblock=0x8000 --pagesize=0x100 --pad=1507328 --squash --faketime --root DIR --output FILE" >&2
  exit 2
}

little=no
eraseblock=
pagesize=
pad=
squash=no
faketime=no
root=
output=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --little-endian) little=yes; shift ;;
    --eraseblock=*) eraseblock=${1#*=}; shift ;;
    --pagesize=*) pagesize=${1#*=}; shift ;;
    --pad=*) pad=${1#*=}; shift ;;
    --squash) squash=yes; shift ;;
    --faketime) faketime=yes; shift ;;
    --root) [ "$#" -ge 2 ] || usage; root=$2; shift 2 ;;
    --output) [ "$#" -ge 2 ] || usage; output=$2; shift 2 ;;
    *) usage ;;
  esac
done

[ "$little" = yes ] && [ "$eraseblock" = 0x8000 ] && \
  [ "$pagesize" = 0x100 ] && [ "$pad" = 1507328 ] && \
  [ "$squash" = yes ] && [ "$faketime" = yes ] || usage
[ -n "${DCS6100_BUILDER_IMAGE:-}" ] || {
  echo "DCS6100_BUILDER_IMAGE is required" >&2
  exit 1
}
command -v docker >/dev/null 2>&1 || {
  echo "docker is required" >&2
  exit 1
}
docker image inspect "$DCS6100_BUILDER_IMAGE" >/dev/null
[ -d "$root" ] && [ ! -L "$root" ] || {
  echo "JFFS2 root must be a non-symlink directory" >&2
  exit 1
}
[ ! -e "$output" ] && [ ! -L "$output" ] || {
  echo "JFFS2 output must not already exist" >&2
  exit 1
}
root=$(cd "$root" && pwd -P)
output_parent=$(cd "$(dirname "$output")" && pwd -P)
output_name=$(basename "$output")
case "$output_name" in
  *[!A-Za-z0-9_.-]*|'') echo "invalid JFFS2 output name" >&2; exit 1 ;;
esac

docker run --rm --network none --platform linux/arm64 \
  --entrypoint /usr/sbin/mkfs.jffs2 \
  -v "$root:/input:ro" \
  -v "$output_parent:/output" \
  "$DCS6100_BUILDER_IMAGE" \
  --little-endian --eraseblock=0x8000 --pagesize=0x100 \
  --pad=1507328 --squash --faketime \
  --root /input --output "/output/$output_name"
[ -f "$output_parent/$output_name" ] && [ ! -L "$output_parent/$output_name" ] || {
  echo "JFFS2 builder did not create the output" >&2
  exit 1
}
docker run --rm --network none --platform linux/arm64 \
  -v "$output_parent/$output_name:/input/data.jffs2:ro" \
  "$DCS6100_BUILDER_IMAGE" /bin/sh -c \
  'errors=$(jffs2dump -c /input/data.jffs2 2>&1 >/dev/null) || exit "$?"; test -z "$errors"'
chmod 0600 "$output_parent/$output_name"
