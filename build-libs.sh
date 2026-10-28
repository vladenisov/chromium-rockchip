#!/bin/sh
# Run inside the native ARM64 builder container (Dockerfile); never installs on host.
set -eu

work=${CHROMIUM_WORK_ROOT:-/work}
prefix=/opt/chromium-rockchip
stage="$work/stage"
downloads="$work/source"
build="$work/build/libs"
case "$work" in /*) ;; *) echo 'CHROMIUM_WORK_ROOT must be absolute' >&2; exit 2 ;; esac
test "$work" != / || exit 2
test -f /opt/rk/lib/pkgconfig/rockchip_mpp.pc
test -f /opt/rk/lib/pkgconfig/librga.pc
mkdir -p "$downloads" "$build" "$stage$prefix/lib"

fetch() {
    target="$downloads/$1"
    if ! test -f "$target"; then
        curl --fail --location --retry 3 --output "$target.part" "$3"
        mv "$target.part" "$target"
    fi
    printf '%s  %s\n' "$2" "$target" | sha256sum --check --status
}

fetch v4l-utils-src.tar.gz \
    1f52bcfb48ca33805862370980a6fce5dfccccf4448c5011dba4e6b16fb57caf \
    https://codeload.github.com/gjasny/v4l-utils/tar.gz/47c8c377cf299bc42cd73a5ed7f2821f24a7bc47
fetch libv4l-rkmpp-src.tar.gz \
    6f0338d08dd69df9f29df35f67946eaf1090e93a4687965b1366a5bf786afa23 \
    https://codeload.github.com/JeffyCN/libv4l-rkmpp/tar.gz/14b5d17495db6a46e13778309f7f8d1b21e4e656
fetch v4l-utils-mmap.patch \
    2b6b3e39a0bc46518bb1c77b165412818c8d3be2f0d1ba63aff762243e7860e3 \
    https://raw.githubusercontent.com/JeffyCN/meta-rockchip/9d84a3b12b76e34ef08bdde87405bb19ec45d434/recipes-multimedia/v4l2apps/v4l-utils/0001-libv4l2-Support-mmap-to-libv4l-plugin.patch

# Rebuild only this script's private working directories, retaining downloads.
rm -rf "$build/v4l-utils" "$build/rkmpp" "$build/rkmpp-out" "$build/pkgconfig"
mkdir -p "$build/v4l-utils" "$build/rkmpp" "$build/pkgconfig"
tar -xzf "$downloads/v4l-utils-src.tar.gz" --strip-components=1 -C "$build/v4l-utils"
tar -xzf "$downloads/libv4l-rkmpp-src.tar.gz" --strip-components=1 -C "$build/rkmpp"

export LDFLAGS="-Wl,-rpath,$prefix/lib"
(
    cd "$build/v4l-utils"
    patch --batch --forward -p1 -i "$downloads/v4l-utils-mmap.patch"
    # Upstream bootstrap seeds config.rpath and gettext po makefiles first.
    bash ./bootstrap.sh
    ./configure --prefix="$prefix" --libdir="$prefix/lib" \
        --disable-static --disable-v4l-utils --disable-libdvbv5 \
        --disable-qv4l2 --disable-qvidcap --disable-bpf \
        --without-jpeg --without-libudev
    make -j "${LIBS_JOBS:-2}"
    make DESTDIR="$stage" install
)

# Keep installed .pc files relocatable to their production prefix. Only the
# private build copies contain DESTDIR so Meson uses the patched plugin ABI.
for pc in "$stage$prefix/lib/pkgconfig/"*.pc; do
    # --libdir makes libdir absolute, so it needs rewriting alongside prefix.
    sed -e "s|^prefix=.*|prefix=$stage$prefix|" \
        -e "s|^libdir=$prefix/|libdir=$stage$prefix/|" \
        "$pc" > "$build/pkgconfig/$(basename "$pc")"
done
export PKG_CONFIG_PATH="$build/pkgconfig:/opt/rk/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$stage$prefix/lib:/opt/rk/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Upstream has no GPU-conversion feature switch. Pin the exact Meson text and
# require RGA rather than accidentally enabling an EGL converter on this host.
python3 - "$build/rkmpp/meson.build" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
gpu = "gpu_dep = (egl_dep.found() and glesv2_dep.found() and\n           gbm_dep.found() and drm_dep.found()) ? true : false"
rga = "rga_dep = dependency('librga', required : false)"
assert s.count(gpu) == 1 and s.count(rga) == 1, 'Pinned Meson source changed'
p.write_text(s.replace(gpu, 'gpu_dep = false').replace(rga, "rga_dep = dependency('librga', required : true)"))
PY

meson setup "$build/rkmpp-out" "$build/rkmpp" \
    --prefix="$prefix" --libdir=lib --buildtype=release \
    -Dlibv4l2subdir=libv4l \
    -Dc_link_args="-Wl,-rpath,$prefix/lib"
meson compile -C "$build/rkmpp-out" -j "${LIBS_JOBS:-2}"
DESTDIR="$stage" meson install -C "$build/rkmpp-out"
cp -a /opt/rk/lib/librockchip_mpp.so* /opt/rk/lib/librga.so* "$stage$prefix/lib/"

test -f "$stage$prefix/lib/libv4l2.so.0"
test -f "$stage$prefix/lib/libv4l/plugins/libv4l-rkmpp.so"
grep -q '^#define HAVE_RGA 1$' "$build/rkmpp-out/config.h"
grep -q '^#define HAVE_GPU 0$' "$build/rkmpp-out/config.h"
python3 - "$stage$prefix/lib" <<'PY'
import ctypes
from pathlib import Path
import sys
root = Path(sys.argv[1])
ctypes.CDLL(str(root / 'libv4l2.so.0'))
ctypes.CDLL(str(root / 'libv4l/plugins/libv4l-rkmpp.so'))
print('Patched libv4l2 and MPP plugin load successfully; RGA enabled, EGL conversion disabled')
PY

cat > "$stage$prefix/lib/SOURCE-VERSIONS.txt" <<'EOF'
v4l-utils 1.22.1 47c8c377cf299bc42cd73a5ed7f2821f24a7bc47
libv4l-rkmpp 14b5d17495db6a46e13778309f7f8d1b21e4e656
v4l-utils mmap patch meta-rockchip 9d84a3b12b76e34ef08bdde87405bb19ec45d434
MPP and RGA copied unchanged from the builder image /opt/rk
Production prefix /opt/chromium-rockchip; staged install only
EOF
