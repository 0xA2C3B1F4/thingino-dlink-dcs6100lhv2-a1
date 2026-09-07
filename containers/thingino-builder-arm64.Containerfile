FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

ARG DEBIAN_SNAPSHOT=20260806T000000Z
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture amd64 \
    && printf '%s\n' \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ bookworm main" \
      "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/ bookworm-security main" \
      > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends \
      autoconf bc bison build-essential ca-certificates ccache cmake cpio curl \
      dialog file flex gawk git jq libcrypt-dev libncurses-dev \
      libusb-1.0-0-dev m4 make mtd-utils mtools nodejs npm parted patch perl python3 \
      python3-jsonschema ripgrep rsync shfmt squashfs-tools swig \
      u-boot-tools unzip wget whiptail xz-utils zstd \
      libc6:amd64 libgcc-s1:amd64 libstdc++6:amd64 zlib1g:amd64 \
    && rm -rf /var/lib/apt/lists/*

COPY webui/package.json webui/package-lock.json /opt/dcs6100-webui/
RUN cd /opt/dcs6100-webui \
    && npm ci --ignore-scripts --no-audit --no-fund \
    && test "$(node -e 'process.stdout.write(require("esbuild").version)')" = 0.25.9 \
    && rm -rf /root/.npm

RUN groupadd --gid 1000 builder \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash builder

ENV LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC
