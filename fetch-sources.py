#!/usr/bin/env python3
"""Fetch pinned, verified Chromium sources; no host installation or compilation."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

MARKER = ".rockchip-extracted"
CHUNK = 1024 * 1024


def matches(path, entry):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest() == entry["sha256"].lower()


def validate_entry(entry):
    url = urllib.parse.urlsplit(entry["url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Source URLs must use HTTPS without credentials")
    digest = entry["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
        raise ValueError("Expected a SHA256 digest")
    if type(entry["size"]) is not int or entry["size"] <= 0:
        raise ValueError("Expected a positive archive size")
    filename = Path(url.path).name
    if not filename or filename in (".", ".."):
        raise ValueError("URL has no archive filename")
    return filename


def download(entry, directory, attempts=3):
    filename = validate_entry(entry)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if matches(target, entry):
        return target
    for attempt in range(attempts):
        temporary = None
        try:
            fd, name = tempfile.mkstemp(prefix=f".{filename}.", dir=directory)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as output:
                request = urllib.request.Request(entry["url"], headers={"User-Agent": "chromium-rockchip-source-fetch/1"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    if hasattr(response, "geturl") and urllib.parse.urlsplit(response.geturl()).scheme != "https":
                        raise ValueError("Refusing an insecure redirect")
                    digest, total = hashlib.sha256(), 0
                    while block := response.read(CHUNK):
                        total += len(block)
                        if total > entry["size"]:
                            raise ValueError("Archive exceeds its pinned size")
                        digest.update(block)
                        output.write(block)
                if total != entry["size"] or digest.hexdigest() != entry["sha256"].lower():
                    raise ValueError("Archive size or SHA256 does not match manifest")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            return target
        except (OSError, ValueError):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(2 ** attempt, 4))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise ValueError("Download attempts must be positive")


def inside(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Archive path escapes extraction directory: {path.name}")
    return path


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive member: {name}")
    return path


def unpack(archive, stage, strip_root):
    first = None
    with tarfile.open(archive, "r|*") as source:
        for member in source:
            name = safe_name(member.name)
            if not name.parts:
                continue
            if strip_root:
                first = first or name.parts[0]
                if name.parts[0] != first:
                    raise ValueError("Source archive has multiple top-level roots")
                name = PurePosixPath(*name.parts[1:])
            if not name.parts:
                if not member.isdir():
                    raise ValueError("Archive root is not a directory")
                continue
            target = inside(stage, stage.joinpath(*name.parts))
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.isreg():
                with source.extractfile(member) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output, CHUNK)
                target.chmod(member.mode & 0o777)
            elif member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or "\\" in member.linkname:
                    raise ValueError("Unsafe symbolic link")
                inside(stage, target.parent / member.linkname)
                target.symlink_to(member.linkname)
            elif member.islnk():
                link = safe_name(member.linkname)
                if strip_root:
                    if not link.parts or link.parts[0] != first:
                        raise ValueError("Unsafe hard link root")
                    link = PurePosixPath(*link.parts[1:])
                origin = inside(stage, stage.joinpath(*link.parts))
                if origin.is_symlink() or not origin.is_file():
                    raise ValueError("Hard links must refer to an extracted regular file")
                os.link(origin, target, follow_symlinks=False)
            else:
                raise ValueError("Archive contains a device, FIFO, or unsupported member")


def verify_version(source, version):
    path = source / "chrome/VERSION"
    try:
        values = dict(line.strip().split("=", 1) for line in path.read_text().splitlines() if "=" in line)
        actual = ".".join(values[key] for key in ("MAJOR", "MINOR", "BUILD", "PATCH"))
    except (OSError, KeyError) as error:
        raise ValueError("Source has no valid chrome/VERSION") from error
    if actual != version:
        raise ValueError(f"Chromium source version {actual} differs from pinned {version}")


def extract(archive, target, strip_root=False, version=None, required=None):
    target = Path(target)
    if target.exists():
        if not (target / MARKER).is_file():
            raise ValueError(f"Existing directory has no completion marker; preserving it: {target}")
        if version:
            verify_version(target, version)
        if required and not (target / required).is_file():
            raise ValueError("Existing extraction lacks required packaging files")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.extract-", dir=target.parent))
    try:
        unpack(archive, stage, strip_root)
        if version:
            verify_version(stage, version)
        if required and not (stage / required).is_file():
            raise ValueError("Archive lacks required packaging files")
        (stage / MARKER).write_text(json.dumps({"version": version, "archive": Path(archive).name}) + "\n")
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def copy_patches(project, work, manifest):
    settings = manifest["rockchip_patches"]
    origin = inside(project, project / settings["directory"])
    patches = sorted(origin.glob("*.patch"))
    if len(patches) != settings["count"]:
        raise ValueError("Rockchip patch count differs from manifest")
    destination = work / "patches" / manifest["chromium"]["version"].split(".")[0]
    if destination.exists():
        existing = sorted(destination.glob("*.patch"))
        if [p.name for p in existing] != [p.name for p in patches] or any(
                old.read_bytes() != new.read_bytes() for old, new in zip(existing, patches)):
            raise ValueError("Existing patch directory differs; preserving it for inspection")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".patches-", dir=destination.parent))
    try:
        for patch in patches:
            shutil.copy2(patch, stage / patch.name)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def latest(version):
    url = "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Linux&num=1"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read(1024 * 1024))
    print(f"Pinned Chromium: {version}; latest Linux Stable: {data[0]['version']}")
    print("Version is informational; updating requires porting and testing the pinned patches.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path("work"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--check-latest", action="store_true", help="Only compare the pinned version with Chromiumdash")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    version = manifest["chromium"]["version"]
    if not version or any(c not in "0123456789." for c in version):
        raise ValueError("Invalid Chromium version")
    if args.check_latest:
        latest(version)
        return
    work = args.work.resolve()
    entries = (manifest["chromium"], manifest["debian"]["packaging"], manifest["debian"]["pregen"])
    for entry in entries:
        validate_entry(entry)
    archives = [download(entry, work / "downloads") for entry in entries]
    extract(archives[0], work / "source" / f"chromium-{version}", strip_root=True, version=version)
    extract(archives[1], work / "source/debian-bookworm", required="debian/rules")
    copy_patches(args.manifest.resolve().parent, work, manifest)
    print(f"Verified sources ready in {work}; nothing installed on the host.")


if __name__ == "__main__":
    main()
