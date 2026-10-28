# Licensing and source provenance

The project's original build orchestration is covered by its root `LICENSE`.
That license does not relicense Chromium, imported patches, linked libraries,
container packages, or other third-party material.

## Chromium and Debian packaging

Chromium uses a BSD-style project license and includes many components with
their own licenses. Preserve the downloaded Chromium `LICENSE`, relevant
`third_party` license files, and the generated `chrome://credits` material
when distributing a browser build. Its bundled FFmpeg and codec components
have their own applicable terms. The exact upstream source version and all
locally applied patches must remain identifiable.

Debian packaging and Debian-maintained patches have their own provenance and
copyright notices in `debian/copyright` and patch headers. Keep those notices
with a redistributed source or binary package. The builder obtains packages
and Chromium build dependencies from official Debian Bookworm and Debian
security repositories. A pinned base image does not freeze subsequent APT
package versions; record the installed package manifest for a particular build.

## Rockchip Chromium patches

The imported MPP/V4L2 patches originate in
[JeffyCN/meta-rockchip](https://github.com/JeffyCN/meta-rockchip/tree/master/dynamic-layers/recipes-browser/chromium/chromium_152.0.7977).
They include work by Jeffy Chen and Damian Hobson-Garcia. Preserve their
original `From`, `Subject`, and `Signed-off-by` headers when adapting patches.
A sign-off records upstream provenance; it does not endorse local adaptations.
The changes target Chromium files and retain their applicable upstream terms.
Do not describe all imported patch code as newly authored MIT software.

The deployment-specific sandbox patch is original project work; it retains
the notices of the Chromium files it modifies. It is separate from the
upstream Rockchip patch series and should be identified as such.

## Companion libraries

| Component | Pinned source | License material to retain |
| --- | --- | --- |
| Rockchip MPP | `nyanmisaka/mpp` at `a9380ef333102ac318628f83b5f7a460d377749e` | `LICENSES/Apache-2.0`, `LICENSES/MIT`, and per-file notices |
| Rockchip RGA | `nyanmisaka/rk-mirrors` at `1d330cc28551943bed3380261a5a9c6fbd58ff53` | `COPYING` (Apache-2.0) and per-file notices |
| v4l-utils | `gjasny/v4l-utils` at `47c8c377cf299bc42cd73a5ed7f2821f24a7bc47` | Mixed GPL/LGPL components; retain `COPYING`, `COPYING.libv4l`, `COPYING.libdvbv5`, and individual file notices |
| libv4l-rkmpp | `JeffyCN/libv4l-rkmpp` at `14b5d17495db6a46e13778309f7f8d1b21e4e656` | `COPYING` (LGPL-2.1) and individual file notices |
| libv4l2 mmap/munmap plugin patch | `JeffyCN/meta-rockchip` at `9d84a3b12b76e34ef08bdde87405bb19ec45d434` | Original patch authorship and the affected libv4l2 LGPL notices |

The public Dockerfile builds MPP and RGA directly from the pinned source
archives and verifies their SHA256 digests. Their license texts and source
commit identifiers are retained under `/opt/rk/share/licenses` in the builder.
The companion-library build script separately pins and checks its source
archives and mmap patch.

When sharing compiled companion libraries, include the applicable license
texts, corresponding source or an appropriate source delivery mechanism,
local modifications, and build instructions as required by their licenses.
Keep LGPL libraries dynamically linked and preserve the ability to replace
them. Installing a build locally and distributing a compiled build are
different operations; this project does not grant rights beyond those of
the upstream components.

No Google proprietary browser branding, API credentials, Widevine CDM, or
other proprietary binary payload is supplied by the project Dockerfile.
