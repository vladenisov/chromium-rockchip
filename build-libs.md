`build-libs.sh` runs in the native ARM64 builder container with the Chromium
runtime directory mounted at `/work` and the existing pinned MPP/RGA SDK at
`/opt/rk`. It stages files under `/work/stage/opt/chromium-rockchip`; it never
installs into the running host. `LIBS_JOBS` defaults to 2.

The script checks SHA256 for two pinned source archives and the Rockchip
libv4l2 mmap/munmap plugin ABI patch. It builds only libv4l libraries and the
MPP plugin, with RGA conversion required and optional EGL conversion disabled.
Installed libraries use the production prefix `/opt/chromium-rockchip/lib`.
The plugin's installed path matches Chromium patch 0020 exactly:
`lib/libv4l/plugins/libv4l-rkmpp.so` beneath that prefix.

Prerequisites: C/C++ compiler, make, autoconf, automake, libtool, gettext and
autopoint, pkg-config, Meson/Ninja, Python 3, patch, curl, tar, SHA256 tools,
zlib development headers, and the `/opt/rk` headers/pkg-config files.

Verification performed by the script: required library files exist, generated
configuration enables RGA and disables GPU conversion, and both shared
libraries load through the dynamic loader using the staged dependencies.
This verifies library build/load, not hardware video playback in Chromium.

The source archives remain cached in `/work/source`. Only the private
`/work/build/libs` work directories are replaced on rerun. MPP and RGA are
copied unchanged from the builder image; their original source pins belong
to that image's build recipe. The staged `SOURCE-VERSIONS.txt` records the
companion library commits for inspection after installation.
