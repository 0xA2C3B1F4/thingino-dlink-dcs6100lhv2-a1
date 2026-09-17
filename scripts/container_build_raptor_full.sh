#!/bin/bash
# Offline full-media build. The host verifies every read-only input first.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1786006608
export TMPDIR=/work/tmp
unset LD_LIBRARY_PATH

sources=(raptor raptor-hal raptor-ipc raptor-common compy mbedtls
         mbedtls-framework slice99 datatype99 interface99 metalang99 libschrift)
binaries=(rvd rhd rsd ric rad rod rmr raptorctl rwd)
[[ "${RAPTOR_SOURCE_ID:-}" =~ ^tree-[0-9a-f]{12}$ ]] || exit 2

if [ "${1:-}" != --compile ]; then
    test "$(id -u)" = 0
    test -d /work && test -z "$(find /work -mindepth 1 -print -quit)"
    test -d /result && test -z "$(find /result -mindepth 1 -print -quit)"
    test -f /font-license
    test -d /headers/T31/1.1.6/en && test -d /headers/T31/1.1.4/zh
    test -d /target/lib && test -d /target/usr/lib
    mkdir -p /work/src /work/sdk /work/vendor "$TMPDIR"
    for name in "${sources[@]}"; do
        test -f "/inputs/$name.tar"
        tar --no-same-owner -xf "/inputs/$name.tar" -C /work/src
    done
    for name in libimp.so libalog.so libaudioProcess.so; do
        test -f "/target/usr/lib/$name"
        cp -L "/target/usr/lib/$name" /work/vendor/
    done
    tar --no-same-owner -xf /toolchain.tar.gz -C /work/sdk
    chown -R builder:builder /work /result
    exec runuser -u builder -- bash /build.sh --compile
fi

test "$(id -u)" = 1000
# Exercise the actual SDP parser and answer generator before cross-compilation.
make -C /work/src/raptor/tests test-rwd-sdp-direction \
    RWD_SDP_DIRECTION_CC=/usr/bin/gcc \
    "RWD_SDP_DIRECTION_BUILD=$TMPDIR/rwd-sdp-direction"
sdk=/work/sdk/mipsel-thingino-linux-gnu_sdk-buildroot
(cd "$sdk" && ./relocate-sdk.sh)
export PATH="$sdk/bin:$PATH"
prefix="$sdk/bin/mipsel-linux-"
cc="${prefix}gcc"
mkdir -p /work/empty-pkgconfig /work/src/deps
export PKG_CONFIG_LIBDIR=/work/empty-pkgconfig
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
export COMPY_FETCHCONTENT_SOURCE_ROOT=/work/src/deps
for name in slice99 datatype99 interface99 metalang99; do
    mv "/work/src/$name" "/work/src/deps/$name-src"
done
flags='-Os -flto -fPIC -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-unwind-tables -fno-ident'
"$cc" $flags -std=c99 -I/work/src/libschrift -c /work/src/libschrift/schrift.c -o /work/src/libschrift/schrift.o
"${prefix}gcc-ar" rcs /work/src/libschrift/libschrift.a /work/src/libschrift/schrift.o
for name in raptor-common raptor-ipc; do
    make -C "/work/src/$name" -j2 "CC=$cc -Wl,-z,max-page-size=4096" "AR=${prefix}ar"
done

build_compy() {
    cmake -S /work/src/compy -B "/work/src/compy/build-$1" \
        -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_C_COMPILER="$cc" \
        -DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_C_FLAGS="$flags" \
        -DCMAKE_AR="${prefix}gcc-ar" -DCMAKE_RANLIB="${prefix}gcc-ranlib" \
        -DCOMPY_SHARED=OFF -DCOMPY_TLS_MBEDTLS="$2" \
        -DCOMPY_TLS_WOLFSSL=OFF -DCOMPY_TLS_OPENSSL=OFF \
        -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
        -DPKG_CONFIG_EXECUTABLE=/usr/bin/pkg-config
    cmake --build "/work/src/compy/build-$1" -j2
}

build_compy plain OFF
# The D-Link sensor uses the 1.1.4 IVS ABI with the 1.1.6 general headers.
make -C /work/src/raptor-hal -j2 all PLATFORM=T31 \
    "CROSS_COMPILE=$prefix" INGENIC_HEADERS=/headers DLINK_OS02G10_IMP_1_1_4=1

# /target is the root from this build, not a previously installed firmware.
# rpath-link is for the linker only; no host path is embedded in the binaries.
link_flags='-pie -Wl,--gc-sections -Wl,-z,max-page-size=4096 -flto -lpthread -lrt -latomic'
hal_flags="$link_flags -L/work/vendor -limp -lalog -lm -ldl -Wl,-rpath-link,/work/vendor -Wl,-rpath-link,/target/lib -Wl,-rpath-link,/target/usr/lib"
make -C /work/src/raptor -j2 rvd rhd rsd ric rad rod rmr raptorctl \
    PLATFORM=T31 "CROSS_COMPILE=$prefix" \
    COMPY_BUILD=/work/src/compy/build-plain \
    "EXTRA_CFLAGS=-fPIE -I/work/src/libschrift" \
    "LDFLAGS=$link_flags -L/work/src/libschrift" "LDFLAGS_HAL=$hal_flags" \
    TLS=0 AAC=0 OPUS=0 MP3=0 AUDIO_EFFECTS=0 WEBTORRENT=0 IVS_DETECT=0 PERSONDET=0 \
    "RSS_BUILD_HASH=$RAPTOR_SOURCE_ID" RSS_BUILD_TIME=2026-08-06T08:56:48Z

# Only the WebRTC daemon links TLS. HTTP stays behind authenticated uhttpd.
cp -a /work/src/mbedtls-framework/. /work/src/mbedtls/framework/
python3 /work/src/mbedtls/scripts/config.py \
    -f /work/src/mbedtls/include/mbedtls/mbedtls_config.h set MBEDTLS_SSL_DTLS_SRTP
cmake -S /work/src/mbedtls -B /work/mbedtls-build \
    -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_C_COMPILER="$cc" \
    -DCMAKE_AR="${prefix}gcc-ar" -DCMAKE_RANLIB="${prefix}gcc-ranlib" \
    -DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_C_FLAGS="$flags" \
    -DCMAKE_INSTALL_PREFIX=/work/tls -DUSE_SHARED_MBEDTLS_LIBRARY=OFF \
    -DUSE_STATIC_MBEDTLS_LIBRARY=ON -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=OFF
cmake --build /work/mbedtls-build -j2
cmake --install /work/mbedtls-build
export PKG_CONFIG_LIBDIR=/work/tls/lib/pkgconfig
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
build_compy tls ON
make -C /work/src/raptor -j2 rwd PLATFORM=T31 TLS=1 \
    "CROSS_COMPILE=$prefix" 'EXTRA_CFLAGS=-I/work/tls/include -fPIE' \
    COMPY_BUILD=/work/src/compy/build-tls "LDFLAGS=$link_flags -lm" \
    'LDFLAGS_TLS=/work/tls/lib/libmbedtls.a /work/tls/lib/libmbedx509.a /work/tls/lib/libmbedcrypto.a' \
    AAC=0 OPUS=0 MP3=0 AUDIO_EFFECTS=0 WEBTORRENT=0 IVS_DETECT=0 PERSONDET=0 \
    "RSS_BUILD_HASH=$RAPTOR_SOURCE_ID" RSS_BUILD_TIME=2026-08-06T08:56:48Z

mkdir -p /result/root/usr/bin /result/root/usr/lib /result/root/usr/share/fonts \
    /result/root/usr/share/raptor/audio \
    /result/root/usr/share/licenses/ubuntu-font \
    /result/root/usr/share/licenses/libschrift /result/readelf
for name in "${binaries[@]}"; do
    install -m 0755 "/work/src/raptor/$name/$name" "/result/root/usr/bin/$name"
done
install -m 0644 /work/src/libschrift/resources/Ubuntu-Regular.ttf \
    /result/root/usr/share/fonts/default.ttf
install -m 0644 /font-license /result/root/usr/share/licenses/ubuntu-font/LICENCE.txt
install -m 0644 /work/src/libschrift/LICENSE \
    /result/root/usr/share/licenses/libschrift/LICENSE
python3 - /result/root/usr/share/raptor/audio/motion.pcm <<'PYCODE'
from pathlib import Path
import struct
import sys

# Fixed 250 ms, 1 kHz triangle alert. RAD accepts only this 16 kHz mono
# PCM16LE asset and applies the total ten-second playback bound itself.
rate = 16_000
count = rate // 4
fade = rate // 50
samples = bytearray()
for index in range(count):
    phase = index % 16
    triangle = phase if phase <= 8 else 16 - phase
    value = (triangle * 2 - 8) * 750
    envelope = min(index, count - 1 - index, fade)
    value = value * envelope // fade
    samples += struct.pack("<h", value)
Path(sys.argv[1]).write_bytes(samples)
PYCODE
test "$(wc -c </result/root/usr/share/raptor/audio/motion.pcm)" -eq 8000
test "$(sha256sum /result/root/usr/share/raptor/audio/motion.pcm | cut -d ' ' -f1)" = \
    507c4135606518c8158e2dcb28fe76170df1f7e9b9ad9aef4b267787b7f22fad
for name in common ipc; do
    install -m 0755 "/work/src/raptor-$name/librss_$name.so" /result/root/usr/lib/
done
while IFS= read -r file; do
    "${prefix}strip" --strip-unneeded "$file"
    "${prefix}readelf" -W -h -l -d -A -V "$file" >"/result/readelf/$(basename "$file").txt"
done < <(find /result/root/usr/bin /result/root/usr/lib -type f | sort)
(cd /result/root && find . -type f -print0 | sort -z | xargs -0 sha256sum) >/result/SHA256SUMS
