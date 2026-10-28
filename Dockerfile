# syntax=docker/dockerfile:1
# Public native ARM64 builder: no dependency on a local homelab image.
ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

FROM ${DEBIAN_IMAGE} AS rockchip-sdk
ARG MPP_REF=a9380ef333102ac318628f83b5f7a460d377749e
ARG MPP_SHA256=3f117218f712fdb782dbc0897e29bf32e001091e0ab65febb50f48eb82614082
ARG RGA_REF=1d330cc28551943bed3380261a5a9c6fbd58ff53
ARG RGA_SHA256=30bd712507fcc6be075cd4aaa2622d46fde203fa6f3418b85d0fc1e2136b8ec4
ARG SDK_JOBS=2
ENV DEBIAN_FRONTEND=noninteractive
RUN test "$(dpkg --print-architecture)" = arm64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends eatmydata ca-certificates \
    && eatmydata apt-get install -y --no-install-recommends \
      build-essential curl cmake meson ninja-build pkg-config libdrm-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl --fail --location --retry 3 \
      "https://codeload.github.com/nyanmisaka/mpp/tar.gz/${MPP_REF}" -o mpp.tar.gz \
    && printf '%s  mpp.tar.gz\n' "$MPP_SHA256" | sha256sum --check --status \
    && mkdir mpp \
    && tar xzf mpp.tar.gz --strip-components=1 -C mpp \
    && cmake -S mpp -B mpp-build -DCMAKE_INSTALL_PREFIX=/opt/rk \
      -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_SHARED_LIBS=ON -DBUILD_TEST=OFF \
    && cmake --build mpp-build -j "$SDK_JOBS" \
    && cmake --install mpp-build \
    && mkdir -p /opt/rk/share/licenses/mpp \
    && cp -a mpp/LICENSES/. /opt/rk/share/licenses/mpp/ \
    && printf '%s\n' "$MPP_REF" > /opt/rk/share/licenses/mpp/SOURCE-COMMIT
RUN curl --fail --location --retry 3 \
      "https://codeload.github.com/nyanmisaka/rk-mirrors/tar.gz/${RGA_REF}" -o rga.tar.gz \
    && printf '%s  rga.tar.gz\n' "$RGA_SHA256" | sha256sum --check --status \
    && mkdir rga \
    && tar xzf rga.tar.gz --strip-components=1 -C rga \
    && meson setup rga-build rga --prefix=/opt/rk --libdir=lib --buildtype=release \
      --default-library=shared -Dcpp_args=-fpermissive \
      -Dlibdrm=false -Dlibrga_demo=false \
    && ninja -C rga-build -j "$SDK_JOBS" install \
    && mkdir -p /opt/rk/share/licenses/rga \
    && cp rga/COPYING /opt/rk/share/licenses/rga/ \
    && printf '%s\n' "$RGA_REF" > /opt/rk/share/licenses/rga/SOURCE-COMMIT

FROM ${DEBIAN_IMAGE} AS builder
ENV DEBIAN_FRONTEND=noninteractive
# Debian's security Chromium packaging provides a native ARM64 LLVM/Rust
# toolchain. Google depot_tools' x86-64 prebuilt tools are not required here.
ENV PATH=/usr/lib/llvm-22/bin:/usr/bin:/bin:/usr/local/bin
RUN test "$(dpkg --print-architecture)" = arm64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends eatmydata ca-certificates \
    && printf '%s\n' \
      'deb-src https://deb.debian.org/debian bookworm main' \
      'deb-src https://deb.debian.org/debian bookworm-updates main' \
      'deb-src https://security.debian.org/debian-security bookworm-security main' \
      > /etc/apt/sources.list.d/chromium-build.list \
    && apt-get update \
    && eatmydata apt-get build-dep -y --no-install-recommends chromium \
    && eatmydata apt-get install -y --no-install-recommends \
      build-essential git quilt cmake meson ninja-build autoconf automake libtool \
      gettext autopoint curl patch pkg-config libdrm-dev libudev-dev libjpeg-dev \
      zlib1g-dev python3 python3-pytest clang-22 lld-22 rustc-web cargo-web \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 builder \
    && useradd --uid 1000 --gid 1000 --create-home builder \
    && mkdir -p /work \
    && chown 1000:1000 /work
COPY --from=rockchip-sdk /opt/rk /opt/rk
WORKDIR /work
USER 1000:1000
