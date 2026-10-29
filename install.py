#!/usr/bin/env python3
"""Install a staged Rockchip Chromium independently of the system browser."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

VERSION = '155.0.8059.84'
MARKER = '# Managed by chromium-rockchip installer v1'
REQUIRED_FILES = (
    'chrome', 'icudtl.dat', 'resources.pak', 'locales/en-US.pak',
    'libEGL.so', 'libGLESv2.so', 'lib/libv4l2.so.0',
    'lib/librockchip_mpp.so.1', 'lib/librga.so.2',
    'lib/libv4l/plugins/libv4l-rkmpp.so',
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def artifact_digest(source):
    """Hash runtime content, normalized link targets, and effective file modes."""
    result = hashlib.sha256()
    for path in sorted(source.rglob('*'), key=lambda p: p.relative_to(source).as_posix()):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(source):
                raise ValueError(f'Source symlink escapes stage: {path}')
            record = ['link', relative, target.relative_to(source).as_posix()]
        elif path.is_dir():
            record = ['directory', relative]
        elif path.is_file():
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            record = ['file', relative, mode, digest(path)]
        else:
            raise ValueError(f'Unsupported staged file: {path}')
        result.update((json.dumps(record, separators=(',', ':')) + '\n').encode())
    return result.hexdigest()


def validate_source(source):
    for path in source.rglob('*'):
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(source):
                raise ValueError(f'Source symlink escapes stage: {path}')
        elif not (path.is_file() or path.is_dir()):
            raise ValueError(f'Unsupported staged file: {path}')


def read_version(chrome):
    return subprocess.run([str(chrome), '--version'], check=True,
                          text=True, capture_output=True, timeout=30).stdout


def launcher(headless=False):
    # One build serves both uses: a desktop window when a Wayland or X11 session is
    # available, otherwise headless with CDP. Each mode keeps its own profile, so a
    # window and a headless instance can run at the same time.
    choose = ('headless=1\n' if headless else
              'headless=${CHROMIUM_ROCKCHIP_HEADLESS:-}\n'
              'if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then\n'
              '    headless=1\n'
              'fi\n')
    return f'''#!/bin/sh
{MARKER}
set -eu
test -n "${{HOME:-}}" || {{ echo 'HOME is required' >&2; exit 2; }}
{choose}config="${{XDG_CONFIG_HOME:-$HOME/.config}}"
set -- --enable-gpu --ignore-gpu-blocklist \\
    --enable-features=AcceleratedVideoDecoder,AcceleratedVideoEncoder "$@"
if [ -n "$headless" ]; then
    port=${{CDP_PORT:-9223}}
    case "$port" in ''|*[!0-9]*) echo 'Invalid CDP_PORT' >&2; exit 2 ;; esac
    if [ "${{#port}}" -gt 5 ] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        echo 'Invalid CDP_PORT' >&2; exit 2
    fi
    profile="$config/chromium-rockchip-headless"
    set -- --headless=new --ozone-platform=headless --use-gl=angle --use-angle=gl-egl \\
        --remote-debugging-address=127.0.0.1 --remote-debugging-port="$port" "$@"
else
    profile="$config/chromium-rockchip"
    set -- --ozone-platform-hint=auto "$@"
fi
umask 077
mkdir -p "$profile"
export CHROME_DEVEL_SANDBOX=/opt/chromium-rockchip/chrome-sandbox
exec /opt/chromium-rockchip/chrome --user-data-dir="$profile" "$@"
'''


def bridge_config(root, kind):
    # Radxa's rockchip-chromium-x11-utils writes the extended form (type=dec plus the SoC's
    # codecs and size limits); keep it rather than shrinking it back to the bare default.
    node = root / f'dev/video-{kind}0'
    if not node.exists() and not node.is_symlink():
        return f'{kind}\n'
    text = node.read_text(errors='replace') if node.is_file() and not node.is_symlink() else ''
    if text.splitlines()[:1] in ([kind], [f'type={kind}']) and '\\' not in text:
        return text
    raise ValueError(f'Refusing existing unrelated device/config: {node}')


def managed_files(gid, dec='dec\n', enc='enc\n'):
    return {
        'usr/local/bin/chromium-rockchip': (launcher(), 0o755),
        'usr/local/bin/chromium-rockchip-headless': (launcher(True), 0o755),
        'usr/share/applications/chromium-rockchip.desktop': (
            f'''{MARKER}
[Desktop Entry]
Type=Application
Name=Chromium (Rockchip)
Exec=/usr/local/bin/chromium-rockchip %U
Icon=/opt/chromium-rockchip/chromium.png
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
''', 0o644),
        'etc/tmpfiles.d/chromium-rockchip.conf': (
            f'{MARKER}\nf /dev/video-dec0 0660 root {gid} - {dec.replace(chr(10), chr(92) + "n")}\n'
            f'f /dev/video-enc0 0660 root {gid} - {enc.replace(chr(10), chr(92) + "n")}\n', 0o644),
    }


def safe_parents(root, path):
    relative = path.relative_to(root)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Destination parent is a symlink: {current}')
        if current.exists() and not current.is_dir():
            raise ValueError(f'Destination parent is not a directory: {current}')
        if current.exists() and current.stat().st_mode & 0o022:
            raise ValueError(f'Destination parent is writable by others: {current}')
        if current.exists() and os.geteuid() == 0 and current.stat().st_uid != 0:
            raise ValueError(f'Destination parent must belong to root: {current}')


def protected_version(path):
    marker = path / '.chromium-rockchip-install.json'
    if not path.is_dir() or marker.is_symlink() or not marker.is_file():
        raise ValueError(f'Unmanaged version directory: {path}')
    metadata = json.loads(marker.read_text())
    if metadata.get('managed_by') != MARKER:
        raise ValueError(f'Unmanaged version directory: {path}')
    if digest(path / 'chrome') != metadata.get('chrome_sha256'):
        raise ValueError(f'Installed binary hash differs: {path}')
    for item in [path, *path.rglob('*')]:
        if item.is_symlink():
            if not item.resolve(strict=True).is_relative_to(path):
                raise ValueError(f'Installed symlink escapes prefix: {item}')
            continue
        info = item.stat()
        if info.st_mode & 0o022 or (os.geteuid() == 0 and info.st_uid != 0):
            raise ValueError(f'Installed path is not protected: {item}')
    return metadata


def preflight(plan):
    root, version = plan['root'], plan['version']
    if root.stat().st_mode & 0o022:
        raise ValueError(f'Installation root is writable by others: {root}')
    if os.geteuid() == 0 and root.stat().st_uid != 0:
        raise ValueError(f'Installation root must belong to root: {root}')
    prefix = root / 'opt/chromium-rockchip'
    temporary = prefix.with_name('.chromium-rockchip-current')
    if os.path.lexists(temporary):
        raise ValueError(f'Temporary prefix already exists: {temporary}')
    for name in [*plan['files'], 'dev/video-dec0', 'dev/video-enc0',
                 'opt/chromium-rockchip', str(version.relative_to(root))]:
        safe_parents(root, root / name)
    if prefix.is_symlink():
        old = prefix.resolve(strict=True)
        if old.parent != root / 'opt' or not old.name.startswith('chromium-rockchip-'):
            raise ValueError(f'Unmanaged prefix symlink: {prefix}')
        protected_version(old)
    elif prefix.exists():
        raise ValueError(f'Refusing existing real prefix directory: {prefix}')
    if version.exists() or version.is_symlink():
        if version.is_symlink():
            raise ValueError(f'Version path is a symlink: {version}')
        metadata = protected_version(version)
        if metadata.get('artifact_sha256') != plan['sha']:
            raise ValueError(f'Existing runtime artifact hash differs: {version}')
    for name in plan['files']:
        target = root / name
        if target.is_symlink() or (target.exists() and
                (not target.is_file() or MARKER not in target.read_text()[:200])):
            raise ValueError(f'Refusing unmanaged destination: {target}')
    for kind in ('dec', 'enc'):
        if bridge_config(root, kind) != plan['bridge'][kind]:
            raise ValueError(f'Device config changed since planning: dev/video-{kind}0')


def prepare(stage, root, gid, run_version=read_version):
    source, root = Path(stage).resolve(), Path(root).resolve()
    if (source / 'opt/chromium-rockchip').is_dir():
        candidate = (source / 'opt/chromium-rockchip').resolve()
        if not candidate.is_relative_to(source):
            raise ValueError('Source prefix symlink escapes stage')
        source = candidate
    if not source.is_dir() or not root.is_dir():
        raise ValueError('Stage and installation root must exist')
    validate_source(source)
    sandbox = 'chrome-sandbox' if (source / 'chrome-sandbox').is_file() else 'chrome_sandbox'
    for name in REQUIRED_FILES + (sandbox,):
        if not (source / name).is_file():
            raise ValueError(f'Missing staged runtime file: {name}')
    if (source / sandbox).is_symlink():
        raise ValueError('Sandbox executable must be a regular file')
    output = run_version(source / 'chrome')
    if re.search(r'(?<![\d.])' + re.escape(VERSION) + r'(?![\d.])', output) is None:
        raise ValueError(f'Expected Chromium {VERSION}, got: {output.strip()}')
    if not isinstance(gid, int) or not 0 <= gid <= 2**32 - 2:
        raise ValueError('Invalid video group ID')
    sha = artifact_digest(source)
    version = root / f'opt/chromium-rockchip-{VERSION}-{sha[:12]}'
    bridge = {kind: bridge_config(root, kind) for kind in ('dec', 'enc')}
    plan = dict(source=source, root=root, gid=gid, sha=sha,
                chrome_sha=digest(source / 'chrome'), version=version,
                sandbox=sandbox, bridge=bridge, files=managed_files(gid, **bridge))
    preflight(plan)
    return plan


def make_parents(path, chown):
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        chown(directory, 0, 0)


def atomic_file(path, text, mode, chown):
    make_parents(path.parent, chown)
    fd, temporary = tempfile.mkstemp(prefix='.chromium-rk-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        chown(temporary, 0, 0)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def install_version(plan, chown):
    version, source = plan['version'], plan['source']
    make_parents(version.parent, chown)
    if version.exists():
        return
    temporary = Path(tempfile.mkdtemp(prefix='.chromium-rk-', dir=version.parent))
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True, symlinks=True)
        for path in temporary.rglob('*'):
            if path.is_symlink():
                original = source / path.relative_to(temporary)
                target = original.resolve(strict=True)
                if not target.is_relative_to(source):
                    raise ValueError(f'Source symlink changed during copy: {original}')
                path.unlink()
                path.symlink_to(os.path.relpath(temporary / target.relative_to(source),
                                                path.parent))
        validate_source(temporary)
        if artifact_digest(temporary) != plan['sha']:
            raise ValueError('Staged runtime artifact changed during copy')
        if plan['sandbox'] != 'chrome-sandbox':
            (temporary / plan['sandbox']).rename(temporary / 'chrome-sandbox')
        metadata = dict(managed_by=MARKER, version=VERSION,
                        chrome_sha256=plan['chrome_sha'],
                        artifact_sha256=plan['sha'])
        (temporary / '.chromium-rockchip-install.json').write_text(json.dumps(metadata))
        for path in [temporary, *temporary.rglob('*')]:
            if path.is_symlink():
                continue
            executable = path.stat().st_mode & 0o111
            chown(path, 0, 0)
            os.chmod(path, 0o755 if path.is_dir() or executable else 0o644)
        sandbox = temporary / 'chrome-sandbox'
        if sandbox.is_symlink():
            raise ValueError('Sandbox executable must be a regular file')
        os.chmod(sandbox, 0o4755)
        os.rename(temporary, version)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def apply(plan, chown=os.chown):
    preflight(plan)
    install_version(plan, chown)
    for name, (text, mode) in plan['files'].items():
        atomic_file(plan['root'] / name, text, mode, chown)
    for kind in ('dec', 'enc'):
        node = plan['root'] / f'dev/video-{kind}0'
        make_parents(node.parent, chown)
        if not node.exists():
            with node.open('x') as stream:
                stream.write(plan['bridge'][kind])
        chown(node, 0, plan['gid'])
        node.chmod(0o660)
    prefix = plan['root'] / 'opt/chromium-rockchip'
    temporary = prefix.with_name('.chromium-rockchip-current')
    if os.path.lexists(temporary):
        raise ValueError(f'Temporary prefix already exists: {temporary}')
    try:
        temporary.symlink_to(plan['version'].name)
        os.replace(temporary, prefix)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--video-gid', type=int)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        gid = args.video_gid if args.video_gid is not None else grp.getgrnam('video').gr_gid
        plan = prepare(args.stage, args.root, gid)
        if args.apply:
            if os.geteuid() != 0:
                raise ValueError('--apply requires root')
            apply(plan)
            print(f'Installed {plan["version"]}; stock Chromium unchanged')
        else:
            print(f'DRY RUN: install {plan["version"]}; no files changed')
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
