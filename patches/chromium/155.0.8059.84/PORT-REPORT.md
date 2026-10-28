# Rockchip V4L2/MPP patches for Chromium 155.0.8059.84

Prepared from the Chromium 152 patch set maintained by Rockchip engineer
Jeffy Chen in `JeffyCN/meta-rockchip`, directory
`dynamic-layers/recipes-browser/chromium/chromium_152.0.7977`.

Target: the official Chromium Gitiles tag `155.0.8059.84`.
Scope: enable stateful hardware decoding and V4L2 encoding through the MPP
libv4l plugin. No proprietary Mali DDK or compositor changes are included.

## Apply in filename order

The eight `.patch` files were individually checked with `git apply --check`
and then applied to a clean tree of their affected official Chromium files.
The complete resulting diff was compared byte-for-byte with the ported tree.
This verifies patch application, not compilation or runtime compatibility.

Port adaptations:

- 0001: omit a HEVC profile-map addition already present upstream in 155.
- 0002: retain 155's new explicit nullptr initialization in GetFreeRequest,
  inserting the ChromeOS guard around that implementation.
- 0003, 0004, 0008, 0019: apply upstream logic without semantic changes.
- 0005: replace the original platform-dependent `/usr/lib64/libv4l2.so`
  path with `/opt/chromium-rockchip/lib/libv4l2.so.0` in both consumers.
- 0020: deployment-specific additive sandbox permissions and preloading in
  both desktop GPU and dedicated hardware video decode pre-sandbox hooks.

The source and validation trees are in sibling `port-work/`, including a
pristine git commit, per-stage commits, and an independently reapplied tree.

## Required companion runtime

Build and stage a libv4l2 containing Rockchip's mmap/munmap plugin-hook patch,
JeffyCN/libv4l-rkmpp, and a compatible MPP library. Do not replace host global
libraries. The fixed Chromium library prefix must resolve at runtime.

The bridge requires a regular configuration node `/dev/video-dec0`, with
controlled write permissions (for example root:video 0660). Encoding also
needs `/dev/video-enc0` if enabled. Their contents select codec capabilities.

Relevant GN configuration: `use_v4l2_codec=true`, `use_vaapi=false`,
`proprietary_codecs=true`, `ffmpeg_branding="Chrome"`. The upstream Yocto recipe
also supplies `use_v4lplugin=true`; check whether that argument exists in the
actual build configuration before adding it. The patch set does not define it.

## Sandbox remains enabled: runtime verification still required

No patch disables the sandbox and no launcher flags are installed here.
The original Yocto recipe uses `--no-sandbox`; that is deliberately not
adopted. Chromium's existing V4L2 pre-sandbox hook already permits video-dec
and media-dec nodes. Its DMA-heap permission is MediaTek-specific, however,
and desktop Linux's GPU hook only applies the V4L2 allowlist to ChromeOS or
Chromecast configurations. Patch 0020 adds the exact dummy decoder/encoder
nodes on Linux when video acceleration is enabled, and bounded MPP permissions in:

`media/gpu/sandbox/hardware_video_decoding_sandbox_hook_linux.cc`

and `content/common/gpu_pre_sandbox_hook_linux.cc`.

The added write access is limited to `/dev/video-dec0`, `/dev/video-enc0`,
`/dev/mpp_service`, `/dev/rga`,
`/dev/dri/card0`, `/dev/dma_heap/system`, and `/dev/dma_heap/cma`. Read access
covers the exact device-tree compatible path, kernel version, two MPP
capability files, and known library/plugin paths beneath the fixed prefix.
No recursive permissions are added. Libraries are preloaded before seccomp
to resolve constructors and dependencies. Missing libraries do not abort
startup, so software decoding remains available.

The current plugin master optionally enables both RGA and EGL/GLES/GBM
conversion. This allowlist assumes RGA conversion or direct MPP output, not
an additional EGL converter backend. Disable GPU conversion during its build
unless that backend's dependencies are deliberately supported and tested.

GPU and V4L2 BPF policies already permit ioctl calls. GPU fcntl delegates
standard operations to the existing baseline policy; no syscall policy is
weakened here. Runtime tracing remains necessary to identify any additional
board-specific accesses and establish that sandboxed playback works.

Upstream patch 0018 is an AMD/VAAPI logging compile workaround, not a sandbox
allowlist. Patch 0019 adds H.264 High 4:2:2 mapping, not a sandbox change.

## Remaining validation

Compile GN targets on the complete source/dependency tree, repair any modern
API or toolchain incompatibility, then test video playback with sandboxing.
Confirm actual V4L2/MPP decoder selection and MPP device descriptors; GPU
rendering status alone does not prove hardware video decoding. Test H.264,
VP9/AV1 as available, seek/flush, decoder teardown, and software fallback.

Native build tooling is an independent concern: Google's prebuilt LLVM/Rust
packages may target x86-64 build hosts. Debian native packaging/toolchains
need their own compatibility checks against the target version.
