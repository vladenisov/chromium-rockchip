#!/usr/bin/env python3
"""Resumable native build inside the builder image (Dockerfile); never installs on the host."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

VERSION = "155.0.8059.84"
PREFIX = Path("opt/chromium-rockchip")
RUST_ALLOCATOR_COMPONENTS = (
    ("third_party/webrtc_overrides/BUILD.gn", 'component("webrtc_component") {'),
)
RUST_STD_GN = "build/rust/std/BUILD.gn"
RUST_STD_LDFLAGS = '    config("prebuilt_stdlib_libs") {\n      ldflags = []'
RUST_STD_FLAGS = '        this_file = "$lib_dir/lib$lib.rlib"\n        ldflags += [ this_file ]\n'
# lib_dir must stay in use (GN rejects unused variables); it is relative to the build directory.
RUST_STD_LIBS = '        libs += [ "$root_build_dir/$lib_dir/lib$lib.rlib" ]\n'
V4L2_GN = "media/gpu/v4l2/BUILD.gn"
V4L2_DEPS = '  deps = [\n    ":v4l2_status",\n'


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def compile_jobs():
    # One job fits the daytime 3 GB container; raise it only together with the container memory.
    jobs = int(os.environ.get("CHROMIUM_JOBS", "1"))
    if jobs < 1:
        raise ValueError(f"CHROMIUM_JOBS must be positive, got {jobs}")
    return jobs


def component_build():
    # Static like Debian by default. The component build needs less memory per link but
    # several Rust std/allocator link fixes below.
    return os.environ.get("CHROMIUM_COMPONENT_BUILD", "0") == "1"


def build_deadline():
    value = os.environ.get("CHROMIUM_BUILD_DEADLINE")
    return int(value) if value else None


class BuildPaused(Exception):
    """Compilation stopped at CHROMIUM_BUILD_DEADLINE; rerunning resumes it."""


class Status:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, stage, state, **extra):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = dict(version=VERSION, stage=stage, state=state,
                     updated_at=timestamp(), **extra)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def run(self, stage, action):
        print(f"[{timestamp()}] {stage}", flush=True)
        self.write(stage, "running", started_at=timestamp())
        try:
            result = action()
        except BuildPaused as error:
            self.write(stage, "paused", reason=str(error))
            raise
        except BaseException as error:
            self.write(stage, "failed", error=str(error),
                       error_type=type(error).__name__)
            raise
        self.write(stage, "complete")
        return result


def verify_version(source):
    values = {}
    for line in (Path(source) / "chrome/VERSION").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    actual = ".".join(values.get(key, "?") for key in ("MAJOR", "MINOR", "BUILD", "PATCH"))
    if actual != VERSION:
        raise ValueError(f"Expected Chromium {VERSION}, found {actual}")


def stage_runtime(output, stage, source=None):
    output, stage = Path(output), Path(stage)
    # Validate before touching an existing staging tree or copying a partial build.
    for name in ("chrome", "chrome_sandbox", "chrome_crashpad_handler"):
        if not (output / name).is_file():
            raise FileNotFoundError(output / name)
    stage.mkdir(parents=True, exist_ok=True)
    binaries = {"chrome", "chrome_sandbox", "chrome_crashpad_handler"}
    for entry in output.iterdir():
        runtime = (entry.name in binaries or entry.name.endswith((".pak", ".dat", ".bin", ".json"))
                   or entry.name.endswith(".so") or ".so." in entry.name)
        if runtime and entry.is_file():
            shutil.copy2(entry, stage / entry.name, follow_symlinks=True)
    for directory in ("resources", "swiftshader"):
        if (output / directory).is_dir():
            shutil.copytree(output / directory, stage / directory, dirs_exist_ok=True)
    (stage / "locales").mkdir(exist_ok=True)
    for locale in ("en-US.pak", "ru.pak"):
        if (output / "locales" / locale).is_file():
            shutil.copy2(output / "locales" / locale, stage / "locales" / locale)
    if source is not None:
        icon = Path(source) / "chrome/app/theme/chromium/product_logo_256.png"
        if icon.is_file():
            shutil.copy2(icon, stage / "chromium.png")


class Build:
    def __init__(self, work, project):
        self.work, self.project = Path(work), Path(project)
        self.source = self.work / "source" / f"chromium-{VERSION}"
        self.stage = self.work / "stage" / PREFIX
        self.markers = self.work / "build/markers"
        self.logs = self.work / "logs"
        self.markers.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.status = Status(self.work / "build-status.json")

    def command(self, args, capture=False, check=True, env=None):
        effective_env = {**os.environ, **(env or {})}
        with (self.logs / "build-browser.log").open("a") as log:
            log.write(f"\n[{timestamp()}] {args!r}\n")
            log.flush()
            result = subprocess.run(args, cwd=self.source, env=effective_env,
                                    stdout=subprocess.PIPE if capture else log,
                                    stderr=log, text=True, check=False)
            if capture and result.stdout:
                log.write(result.stdout)
            if check and result.returncode:
                raise RuntimeError(f"Command failed ({result.returncode}): {args!r}; see {self.logs / 'build-browser.log'}")
            return result

    def once(self, name, action):
        marker = self.markers / name
        if not marker.exists():
            action()
            marker.write_text(timestamp() + "\n")

    def check_source(self):
        verify_version(self.source)
        candidates = (self.source / ".rockchip-extracted",
                      self.source.parent / ".rockchip-extracted", self.work / ".rockchip-extracted")
        if not any(path.is_file() for path in candidates):
            raise RuntimeError("Source extraction is not marked complete (.rockchip-extracted missing)")

    def prepare_debian(self):
        source = self.work / "source/debian-bookworm/debian"
        destination = self.source / "debian"
        self.once("debian-copied", lambda: shutil.copytree(source, destination, dirs_exist_ok=True))
        result = self.command(["quilt", "unapplied"], capture=True, check=False,
                              env={"QUILT_PATCHES": "debian/patches"})
        if result.returncode not in (0, 1):
            raise RuntimeError("Unable to inspect Debian quilt state; see build log")
        if result.stdout.strip():
            self.command(["quilt", "push", "-a"], env={"QUILT_PATCHES": "debian/patches"})

    def patch_rockchip(self):
        patches = sorted((self.work / "patches/155").glob("*.patch"))
        if len(patches) != 8:
            raise RuntimeError(f"Expected 8 reviewed Rockchip patches, found {len(patches)}")
        for patch in patches:
            digest = hashlib.sha256(patch.read_bytes()).hexdigest()
            marker = self.markers / (patch.name + ".sha256")
            if marker.exists():
                if marker.read_text().strip() != digest:
                    raise RuntimeError(f"Applied patch changed: {patch.name}")
                continue
            args = ["patch", "--batch", "-p1", "-i", str(patch)]
            forward = self.command(args + ["--forward", "--dry-run"], check=False)
            if forward.returncode == 0:
                self.command(args + ["--forward"])
            elif self.command(args + ["--reverse", "--dry-run"], check=False).returncode:
                raise RuntimeError(f"Patch neither applicable nor fully applied: {patch.name}")
            marker.write_text(digest + "\n")

    def prepare_rules(self):
        rules = self.source / "debian/rules"
        text = rules.read_text()
        # The DEPS mold binary is x86-64 only; native arm64 builds must link with lld.
        component = "true" if component_build() else "false"
        no_mold = f"defines+=is_component_build={component} use_mold=false "
        if "# ROCKCHIP NATIVE RUNNER" in text:
            if "use_mold=false" not in text:
                rules.write_text(text.replace("defines+=is_component_build=true ", no_mold, 1))
            return
        if text.count("is_official_build=true") != 1:
            raise RuntimeError("Expected exactly one is_official_build=true in pinned Debian rules")
        text = text.replace("is_official_build=true", "is_official_build=false")
        text += ('\n# ROCKCHIP NATIVE RUNNER\n'
                 + no_mold + 'concurrent_links=1 blink_symbol_level=0 v8_symbol_level=0\n'
                 '.PHONY: rkmpp-configure\n'
                 'rkmpp-configure: out/Release/gn\n'
                 '\t./out/Release/gn gen out/Release --args="$(defines)"\n')
        rules.write_text(text)

    def link_rust_allocator(self):
        # The component build links the system Rust std into every shared library that reaches
        # Rust code, but the allocator only into the first one (libbase.so), where its symbols
        # are hidden. Components that call into std directly need their own copy.
        for path, target in RUST_ALLOCATOR_COMPONENTS:
            gn = self.source / path
            text = gn.read_text()
            patched = target + '\n  deps = [ "//build/rust/allocator" ]'
            if patched in text:
                continue
            if text.count(target) != 1:
                raise RuntimeError(f"Expected exactly one {target} in {path}")
            gn.write_text(text.replace(target, patched, 1))

    def libm_before_rust_std(self):
        # The prebuilt std rlibs go to every link ahead of the objects, and compiler_builtins
        # defines fmod/floor/sqrt/... itself. Any C++ component using them would pull those
        # members, then core/std, then the allocator it lacks. Resolve them from libm first.
        gn = self.source / RUST_STD_GN
        text = gn.read_text()
        patched = RUST_STD_LDFLAGS.replace("ldflags = []", 'ldflags = [ "-lm" ]')
        if patched in text:
            return
        if text.count(RUST_STD_LDFLAGS) != 1:
            raise RuntimeError(f"Expected exactly one prebuilt_stdlib_libs ldflags in {RUST_STD_GN}")
        gn.write_text(text.replace(RUST_STD_LDFLAGS, patched, 1))

    def rust_std_after_shared_libraries(self):
        # As ldflags the std rlibs come first on every link line, so a shared library that
        # leaves rust_eh_personality to libbase.so (libnet.so, libskia.so, ...) pulls std out
        # of the rlib whenever it is listed before libbase.so, and with std its allocator.
        # As libs they follow the shared libraries, which then resolve those symbols.
        gn = self.source / RUST_STD_GN
        text = gn.read_text()
        if RUST_STD_LIBS in text:
            return
        if text.count(RUST_STD_FLAGS) != 1:
            raise RuntimeError(f"Expected exactly one std rlib ldflags loop in {RUST_STD_GN}")
        text = text.replace(RUST_STD_FLAGS, RUST_STD_LIBS, 1)
        gn.write_text(text.replace('ldflags = [ "-lm" ]', 'ldflags = [ "-lm" ]\n      libs = []', 1))

    def depend_on_v4l2_stubs(self):
        # Rockchip patch 0004 declares the libv4l2_stubs generator, but nothing in the v4l2
        # source set depends on it, so v4l2_device.cc can compile before v4l2_stubs.h exists.
        gn = self.source / V4L2_GN
        text = gn.read_text()
        patched = V4L2_DEPS.replace('":v4l2_status",\n', '":libv4l2_stubs",\n    ":v4l2_status",\n')
        if patched in text:
            return
        if text.count(V4L2_DEPS) != 1:
            raise RuntimeError(f"Expected exactly one v4l2 deps list in {V4L2_GN}")
        gn.write_text(text.replace(V4L2_DEPS, patched, 1))

    def prepare_pregen(self):
        archive = self.work / "downloads/chromium_155.0.8059.79.orig-pre-gen.tar.xz"
        self.once("pregen-extracted", lambda: self.command(["tar", "-xJf", str(archive)]))

    def configure(self):
        self.once("debian-configured", lambda: self.command([
            "make", "-f", "debian/rules", "override_dh_auto_configure", "njobs=1"]))
        self.command(["make", "-f", "debian/rules", "out/Release/gn", "njobs=1"])
        self.command(["make", "-f", "debian/rules", "rkmpp-configure", "njobs=1"])

    def libraries(self):
        needed = (self.stage / "lib/libv4l2.so.0", self.stage / "lib/libv4l/plugins/libv4l-rkmpp.so",
                  self.stage / "lib/SOURCE-VERSIONS.txt")
        if not all(path.is_file() for path in needed):
            self.command([str(self.project / "build-libs.sh")],
                         env={"CHROMIUM_WORK_ROOT": str(self.work)})

    def verify_staged(self):
        env = {"LD_LIBRARY_PATH": f"{self.stage}:{self.stage / 'lib'}:" + os.environ.get("LD_LIBRARY_PATH", "")}
        result = self.command([str(self.stage / "chrome"), "--version"], capture=True, env=env)
        if VERSION not in result.stdout:
            raise RuntimeError(f"Staged browser version mismatch: {result.stdout.strip()}")

    def compile(self):
        ninja = ["ninja", "-C", "out/Release", f"-j{compile_jobs()}", "chrome", "chrome_sandbox"]
        deadline = build_deadline()
        if deadline is None:
            self.command(ninja)
            return
        remaining = deadline - int(time.time())
        if remaining <= 0:
            raise BuildPaused("deadline reached before compilation")
        # SIGINT lets Ninja finish its logs and remove interrupted outputs.
        result = self.command(["timeout", "--signal=INT", "--kill-after=300", str(remaining)] + ninja,
                              check=False)
        if result.returncode == 124:
            raise BuildPaused(f"compilation stopped after {remaining} seconds")
        if result.returncode:
            raise RuntimeError(f"Ninja failed ({result.returncode}); see {self.logs / 'build-browser.log'}")

    def steps(self):
        component_fixes = (("rust-allocator", self.link_rust_allocator),
                           ("rust-std-libm", self.libm_before_rust_std),
                           ("rust-std-last", self.rust_std_after_shared_libraries))
        return ((("verify-source", self.check_source), ("debian-patches", self.prepare_debian),
                 ("rockchip-patches", self.patch_rockchip), ("build-rules", self.prepare_rules),
                 ("pregen", self.prepare_pregen))
                + (component_fixes if component_build() else ())
                + (("v4l2-stubs", self.depend_on_v4l2_stubs),
                   ("configure", self.configure),
                   ("rockchip-libraries", self.libraries),
                   ("compile", self.compile),
                   ("stage-runtime", lambda: stage_runtime(self.source / "out/Release", self.stage, self.source)),
                   ("verify-staged-version", self.verify_staged)))

    def run(self):
        for name, action in self.steps():
            self.status.run(name, action)
        self.status.write("built-not-installed", "complete", hardware_verified=False, stage_path=str(self.stage))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default=os.environ.get("CHROMIUM_WORK_ROOT", "/work"))
    parser.add_argument("--project", default="/project")
    args = parser.parse_args()
    if not Path(args.work).is_absolute() or Path(args.work) == Path("/"):
        parser.error("--work must be an absolute private build directory")
    build = Build(args.work, args.project)
    with (build.work / "build-runner.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another build runner is active")
        try:
            build.run()
        except BuildPaused as paused:
            print(f"Paused: {paused}; rerun to resume", flush=True)
            sys.exit(75)


if __name__ == "__main__":
    main()
