# Chromium Rockchip

Build official Chromium with Rockchip MPP video acceleration on native ARM64
Debian Bookworm. One build serves both a desktop browser (Wayland or X11) and
headless automation over CDP; the launcher picks the mode automatically.

**Experimental:** with this patch set Chromium 155 decodes H.264 through
V4L2/MPP on RK3588 in headless mode: the verifier reports `V4L2VideoDecoder`
as a platform decoder at full frame rate, and decoded NV12 frames are shown
without a copy (patch 0021). This was checked on RK3588 (Mali-G610, Panfrost
Mesa) with both a native component build and the static build produced by the
GitHub Actions workflow. Other codecs, resolutions and desktop (X11/Wayland)
sessions are not verified yet.

## Pinned inputs

- Official Chromium **155.0.8059.84**, the Linux stable release checked on
  2026-10-19, downloaded directly from Google's official source archive.
- Debian Bookworm build compatibility patches and pre-generated build inputs
  from **155.0.8059.79-1~deb12u1**. These supply the system LLVM/Rust integration;
  the Chromium source being compiled remains 155.0.8059.84.
- Rockchip's maintained Chromium 152 V4L2/MPP patches, adapted to 155, with a
  separate patch for sandbox device/library access.
- Pinned MPP, RGA, libv4l-rkmpp and patched libv4l2 sources.

Source URLs, sizes and SHA256 checksums are in [sources.json](sources.json).
Patches retain their upstream author information; see
[LICENSES.md](LICENSES.md) and the
[port report](patches/chromium/155.0.8059.84/PORT-REPORT.md).
The system FFmpeg is not used to add Chromium video decoding: Chromium needs
its own V4L2/MPP integration.

## Target environment

The development target is RK3588 with the Rockchip BSP 6.1 kernel and
Panthor/Panfrost Mesa. A working MPP kernel interface is required:

```
/dev/mpp_service
/dev/rga
/dev/dma_heap/system
/dev/dma_heap/cma
/dev/dri/card0
```

GPU rendering and video decoding are separate checks. On the target board,
stock Chromium 152 already renders with Mali-G610 in headless mode, but the
baseline H.264 test reports `FFmpegVideoDecoder` and a non-platform decoder.
The patched build must demonstrate actual hardware video decoding to pass.
Other boards, kernel families and proprietary Mali drivers are not validated.

## Build

Use native ARM64 Linux with Docker. Keep the working directory on a large
disk: upstream Chromium recommends at least 100 GB free space and more than
16 GB RAM. An 8 GB machine needs ample swap and a substantially longer build.
The defaults use one Ninja job, one linker, a static build (like Debian's)
without debug symbols or ThinLTO, a 3 GB container memory limit and two CPUs.
Set `CHROMIUM_JOBS` for more parallel compilers and
`CHROMIUM_COMPONENT_BUILD=1` for a component build, whose links need less
memory on small machines. Choose the build type before the first `make build`
of a working directory. No host swap
or system packages are changed by the public build commands.

```sh
make image
make fetch WORK_DIR=/path/on/large-disk/chromium-work
make build WORK_DIR=/path/on/large-disk/chromium-work
```

`make image` builds a native ARM64 builder from official Debian. `make fetch`
verifies the archives, extracts official sources and copies the patch set.
`make build` applies Debian compatibility patches, then Rockchip patches,
builds the private multimedia libraries and compiles Chromium.

Memory limits can be overridden with `MEMORY`, `MEMORY_SWAP` and `CPUS`.
Docker can only use swap already configured on the host; the swap limit does
not allocate a swapfile. Insufficient memory can terminate compiler processes.

Progress and errors are saved in:

```
WORK_DIR/build-status.json
WORK_DIR/logs/build-browser.log
```

Rerunning `make build` resumes prepared stages and Ninja work. With
`CHROMIUM_BUILD_DEADLINE` (Unix time) Ninja stops cleanly at that moment and
the build exits with status 75; the next run continues from there. Applied patches
are fingerprinted: changing a patch under an existing prepared tree fails
instead of silently mixing versions. Use a fresh working directory when
changing source versions or the patch set.

The staged result is `WORK_DIR/stage/opt/chromium-rockchip/`. It contains the
browser's runtime/component libraries, locales and the isolated MPP/V4L2
libraries, not compiler objects or generated build trees.

## Install and run

The multimedia integration uses the fixed `/opt/chromium-rockchip` prefix.
The installer creates a protected versioned installation and a current-prefix
symlink, separate launchers and a desktop entry. It preserves the distribution
Chromium and its profile. Review the dry run first:

```sh
python3 install.py --stage /path/to/chromium-work/stage
sudo python3 install.py --stage /path/to/chromium-work/stage --apply
```

See [INSTALL.md](INSTALL.md) for installation details and rollback.

`chromium-rockchip` opens a browser window when a Wayland or X11 session is
available (`WAYLAND_DISPLAY` or `DISPLAY` is set) and otherwise runs headless.
Set `CHROMIUM_ROCKCHIP_HEADLESS=1`, or run `chromium-rockchip-headless`, to
force headless mode from a desktop session. Headless mode serves CDP on
`127.0.0.1:9223`; change the port with `CDP_PORT`. The two modes use separate
profiles, so a window and a headless instance can run at the same time.

The launchers enable GPU/video acceleration without disabling Chromium's
sandbox. Rendering and video compatibility still depend on the actual kernel,
Mesa driver and stream codec.

## Verify actual video decoding

Install the small verifier dependency in a Python virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-verify.txt
make fixture WORK_DIR=/path/to/chromium-work
make verify WORK_DIR=/path/to/chromium-work
```

The fixture generator needs FFmpeg with libx264. The verifier runs Chromium
with its sandbox, a temporary profile and a localhost-only video server. It
collects CDP GPU and Media data, checks at least 25 advancing frames, and
requires an explicit final hardware decoder plus the platform-decoder flag.
An open MPP descriptor is supporting evidence, not sufficient proof. GPU
rendering, an advertised video capability, a generic Mojo decoder, or a
hardware attempt followed by software fallback do not pass as hardware video.

The JSON result is `WORK_DIR/verification.json`. A built binary alone does not
mean `hardware_verified=true`.

## Development and updates

```sh
make test
make latest
```

`make latest` checks Chromium's official stable-release API without changing
the pinned release. New releases require updating the manifest, reviewing and
porting patches, updating the exact-version guards, and testing a clean build
and real playback. The scripts never silently downgrade to Chromium 132 or
apply an unreviewed patch family to a newer release.

CI runs the automation tests and syntax checks on every push.

## GitHub Actions build

The `Build` workflow compiles the browser on GitHub's native ARM64 runners
(`ubuntu-24.04-arm`). A hosted job may run for six hours, so the build is a
chain of stages: each one stops Ninja before its time runs out and uploads the
build tree as a short-lived artifact, and the next stage continues from it.
The finishing stage strips the runtime and uploads
`chromium-rockchip-VERSION-arm64.tar.xz` with its SHA256. Start it manually
from the Actions tab, or push a `v*` tag to also publish a GitHub prerelease.

A stage that fails still uploads its build tree. After fixing the cause, start
the workflow with `resume-run` (that run's ID) and `resume-tree` (the stage
number) to continue from it instead of compiling from scratch. Tree
artifacts are kept for two days.

To install a downloaded build:

```sh
sha256sum --check chromium-rockchip-155.0.8059.84-arm64.tar.xz.sha256
mkdir stage && tar -xJf chromium-rockchip-155.0.8059.84-arm64.tar.xz -C stage
python3 install.py --stage stage
sudo python3 install.py --stage stage --apply
```

Runners have no Rockchip hardware: a workflow build is compiled and its
version checked, never hardware-verified. Large source trees,
build outputs, browser profiles and machine-specific data are excluded from
the repository.
