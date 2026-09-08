#!/bin/bash
# Offline C component build. Inputs are verified and mounted read-only by the host.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1786006608
export TMPDIR=/work/tmp
if [ "${1-}" != --compile ]; then
    mkdir -p /work/src /work/sdk /work/result "$TMPDIR"
    for archive in /inputs/*.tar; do tar --no-same-owner -xf "$archive" -C /work/src; done
    tar --no-same-owner -xf /toolchain.tar.gz -C /work/sdk
    chown -R builder:builder /work
    exec runuser -u builder -- bash /build.sh --compile
fi
test "$(id -u)" = 1000
sdk=/work/sdk/mipsel-thingino-linux-gnu_sdk-buildroot
(cd "$sdk" && ./relocate-sdk.sh)
export PATH="$sdk/bin:$PATH"
prefix="$sdk/bin/mipsel-linux-"
cc="${prefix}gcc"
# CMake's TLS lookup is confined to the freshly built static closure.
export PKG_CONFIG_LIBDIR=/work/tls/lib/pkgconfig
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
export COMPY_FETCHCONTENT_SOURCE_ROOT=/work/src/deps
mkdir -p /work/src/deps
for name in slice99 datatype99 interface99 metalang99; do
    mv "/work/src/$name" "/work/src/deps/$name-src"
done
cp -a /work/src/mbedtls-framework/. /work/src/mbedtls/framework/
# Match Raptor's standalone TLS configuration; upstream disables DTLS-SRTP.
python3 /work/src/mbedtls/scripts/config.py \
    -f /work/src/mbedtls/include/mbedtls/mbedtls_config.h set MBEDTLS_SSL_DTLS_SRTP
flags='-Os -flto -fPIC -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-unwind-tables -fno-ident'
cmake -S /work/src/mbedtls -B /work/mbedtls-build \
    -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_C_COMPILER="$cc" \
    -DCMAKE_AR="${prefix}gcc-ar" -DCMAKE_RANLIB="${prefix}gcc-ranlib" \
    -DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_C_FLAGS="$flags" \
    -DCMAKE_INSTALL_PREFIX=/work/tls -DUSE_SHARED_MBEDTLS_LIBRARY=OFF \
    -DUSE_STATIC_MBEDTLS_LIBRARY=ON -DENABLE_TESTING=OFF -DENABLE_PROGRAMS=OFF
cmake --build /work/mbedtls-build -j2
cmake --install /work/mbedtls-build
for name in raptor-common raptor-ipc; do
    make -C "/work/src/$name" -j2 "CC=$cc -Wl,-z,max-page-size=4096" "AR=${prefix}ar"
done
cmake -S /work/src/compy -B /work/src/compy/build-mips \
    -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_C_COMPILER="$cc" \
    -DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_C_FLAGS="$flags" \
    -DCMAKE_AR="${prefix}gcc-ar" -DCMAKE_RANLIB="${prefix}gcc-ranlib" \
    -DCOMPY_TLS_MBEDTLS=ON -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
    -DPKG_CONFIG_EXECUTABLE=/usr/bin/pkg-config
cmake --build /work/src/compy/build-mips -j2
make -C /work/src/raptor -j2 rwd PLATFORM=T31 TLS=1 \
    "CROSS_COMPILE=$prefix" "EXTRA_CFLAGS=-I/work/tls/include -fPIE" \
    'LDFLAGS=-pie -Wl,--gc-sections -Wl,-z,max-page-size=4096 -lpthread -lm -lrt -flto' \
    'LDFLAGS_TLS=/work/tls/lib/libmbedtls.a /work/tls/lib/libmbedx509.a /work/tls/lib/libmbedcrypto.a' \
    RSS_BUILD_HASH=6bc7f44 RSS_BUILD_TIME=2026-08-06T08:56:48Z
cp /work/src/raptor/rwd/rwd /work/result/rwd
cp /work/src/raptor-common/librss_common.so /work/result/
cp /work/src/raptor-ipc/librss_ipc.so /work/result/
for file in /work/result/*; do
    "${prefix}strip" --strip-unneeded "$file"
    "${prefix}readelf" -l -d "$file"
done
