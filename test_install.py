import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('rk_install', HERE / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'host'
        self.root.mkdir()
        self.stage = self.base / 'stage' / 'opt/chromium-rockchip'
        self.stage.mkdir(parents=True)
        for name in installer.REQUIRED_FILES + ('chrome_sandbox',):
            p = self.stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('test-build\n')
        (self.stage / 'chrome').chmod(0o755)

    def plan(self):
        return installer.prepare(self.stage, self.root, 44,
                                 lambda _: 'Chromium 155.0.8059.84\n')

    def apply(self, plan):
        return installer.apply(plan, chown=lambda *_: None)

    def test_default_cli_dry_run_does_not_write_host(self):
        chrome = self.stage / 'chrome'
        chrome.write_text('#!/bin/sh\nprintf "Chromium 155.0.8059.84\\n"\n')
        p = subprocess.run(['python3', str(HERE / 'install.py'), '--stage',
                            str(self.stage), '--root', str(self.root),
                            '--video-gid', '44'], text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('DRY RUN', p.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unrelated_collision_preflight_writes_nothing(self):
        target = self.root / 'usr/local/bin/chromium-rockchip'
        target.parent.mkdir(parents=True)
        target.write_text('precious local launcher')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            self.plan()
        self.assertFalse((self.root / 'opt').exists())
        self.assertEqual(target.read_text(), 'precious local launcher')

    def test_source_symlink_escape_is_rejected(self):
        outside = self.base / 'secret'
        outside.write_text('unrelated')
        (self.stage / 'escape').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.plan()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrapped_stage_prefix_symlink_escape_rejected(self):
        wrapper = self.base / 'wrapper'
        (wrapper / 'opt').mkdir(parents=True)
        (wrapper / 'opt/chromium-rockchip').symlink_to(self.stage)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            installer.prepare(wrapper, self.root, 44,
                              lambda _: 'Chromium 155.0.8059.84')

    def test_symlinked_destination_parent_is_rejected(self):
        unrelated = self.base / 'unrelated'
        unrelated.mkdir()
        (self.root / 'usr').symlink_to(unrelated)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.plan()
        self.assertEqual(list(unrelated.iterdir()), [])

    def test_existing_device_node_or_unrelated_file_is_not_overwritten(self):
        node = self.root / 'dev/video-dec0'
        node.parent.mkdir(parents=True)
        node.write_text('camera configuration')
        with self.assertRaisesRegex(ValueError, 'video-dec0'):
            self.plan()
        self.assertEqual(node.read_text(), 'camera configuration')

    def test_vendor_rk3588_bridge_config_is_kept(self):
        node = self.root / 'dev/video-dec0'
        node.parent.mkdir(parents=True)
        vendor = 'type=dec\ncodecs=VP8:VP9:H.264:H.265:AV1\nmax-width=7680\nmax-height=4320\n'
        node.write_text(vendor)
        plan = self.plan()
        self.assertEqual(plan['bridge']['dec'], vendor)
        tmpfiles = plan['files']['etc/tmpfiles.d/chromium-rockchip.conf'][0]
        self.assertIn('- type=dec\\ncodecs=VP8:VP9:H.264:H.265:AV1\\nmax-width=7680\\nmax-height=4320\\n\n', tmpfiles)
        self.assertIn('- enc\\n\n', tmpfiles)

    def test_version_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, '155.0.8059.84'):
            installer.prepare(self.stage, self.root, 44,
                              lambda _: 'Chromium 132.0.0.0')

    def test_writable_install_parent_rejected_before_any_install(self):
        parent = self.root / 'opt'
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'writable'):
            self.plan()
        self.assertEqual(list(parent.iterdir()), [])

    def test_temporary_switch_collision_preflight_writes_nothing(self):
        collision = self.root / 'opt/.chromium-rockchip-current'
        collision.parent.mkdir()
        collision.write_text('unrelated')
        with self.assertRaisesRegex(ValueError, 'Temporary prefix'):
            self.plan()
        self.assertFalse((self.root / 'usr').exists())

    def test_sandbox_symlink_rejected_before_writing(self):
        sandbox = self.stage / 'chrome_sandbox'
        sandbox.unlink()
        sandbox.symlink_to('chrome')
        with self.assertRaisesRegex(ValueError, 'Sandbox'):
            self.plan()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_new_versions_keep_old_prefix_and_stock_binary(self):
        stock = self.root / 'usr/bin/chromium'
        stock.parent.mkdir(parents=True)
        stock.write_text('stock')
        first = self.plan()
        self.apply(first)
        first_target = (self.root / 'opt/chromium-rockchip').resolve()
        self.assertEqual((first_target / 'chrome-sandbox').stat().st_mode & 0o7777,
                         0o4755)
        self.assertFalse((first_target / 'chrome_sandbox').exists())
        (self.stage / 'chrome').write_text('second build\n')
        self.apply(self.plan())
        self.assertNotEqual((self.root / 'opt/chromium-rockchip').resolve(),
                            first_target)
        self.assertTrue(first_target.is_dir())
        self.assertEqual(stock.read_text(), 'stock')

    def test_internal_source_symlink_stays_inside_installed_prefix(self):
        (self.stage / 'safe-link').symlink_to(self.stage / 'resources.pak')
        self.apply(self.plan())
        target = (self.root / 'opt/chromium-rockchip').resolve()
        self.assertEqual((target / 'safe-link').resolve(), target / 'resources.pak')

    def test_changed_component_creates_new_version_with_identical_chrome(self):
        component = self.stage / 'libmedia.so'
        component.write_bytes(b'first video decoder build')
        self.apply(self.plan())
        current = self.root / 'opt/chromium-rockchip'
        old = current.resolve()
        chrome_before = (old / 'chrome').read_bytes()
        component.write_bytes(b'updated video decoder build')
        self.apply(self.plan())
        new = current.resolve()
        self.assertNotEqual(new, old)
        self.assertEqual((new / 'chrome').read_bytes(), chrome_before)
        self.assertEqual((old / 'libmedia.so').read_bytes(), b'first video decoder build')
        self.assertEqual((new / 'libmedia.so').read_bytes(), b'updated video decoder build')

    def test_artifact_id_ignores_timestamps_and_source_directory(self):
        import os
        import shutil
        first = self.plan()['version'].name
        for path in self.stage.rglob('*'):
            os.utime(path, (1234567890, 1234567890), follow_symlinks=False)
        copied = self.base / 'same-artifact'
        shutil.copytree(self.stage, copied)
        other = installer.prepare(copied, self.root, 44,
                                  lambda _: 'Chromium 155.0.8059.84')
        self.assertEqual(first, other['version'].name)

    def test_one_launcher_picks_screen_or_headless(self):
        self.apply(self.plan())
        text = (self.root / 'usr/local/bin/chromium-rockchip').read_text()
        logger = self.base / 'argv-logger'
        logger.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        logger.chmod(0o755)
        portable = self.base / 'launcher'
        portable.write_text(text.replace('/opt/chromium-rockchip/chrome', str(logger)))
        home = self.base / 'home'

        def run(**env):
            result = subprocess.run(['sh', str(portable), 'https://example.org'],
                                    env={'HOME': str(home), 'PATH': '/usr/bin:/bin', **env},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        screen = run(WAYLAND_DISPLAY='wayland-0')
        self.assertNotIn('--headless=new', screen)
        self.assertIn('--ozone-platform-hint=auto', screen)
        self.assertIn(f'--user-data-dir={home}/.config/chromium-rockchip', screen)
        self.assertIn('--ozone-platform-hint=auto', run(DISPLAY=':0'))
        console = run()
        self.assertIn('--headless=new', console)
        self.assertIn('--remote-debugging-port=9223', console)
        # Separate profiles let a desktop window and a headless instance run at once.
        self.assertIn(f'--user-data-dir={home}/.config/chromium-rockchip-headless', console)
        self.assertIn('--headless=new', run(WAYLAND_DISPLAY='wayland-0', CHROMIUM_ROCKCHIP_HEADLESS='1'))

    def test_desktop_entry_can_be_chosen_as_default_browser(self):
        self.apply(self.plan())
        entry = (self.root / 'usr/share/applications/chromium-rockchip.desktop').read_text()
        self.assertIn('MimeType=text/html;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;', entry)
        self.assertIn('Exec=/usr/local/bin/chromium-rockchip %U', entry)

    def test_launchers_forward_arguments_and_validate_cdp_port(self):
        self.apply(self.plan())
        launcher = self.root / 'usr/local/bin/chromium-rockchip-headless'
        text = launcher.read_text()
        self.assertIn('"$@"', text)
        self.assertNotIn('--no-sandbox', text)
        self.assertIn('--remote-debugging-address=127.0.0.1', text)
        logger = self.base / 'argv-logger'
        logger.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        logger.chmod(0o755)
        script = text.replace('/opt/chromium-rockchip/chrome', str(logger))
        portable = self.base / 'launcher'
        portable.write_text(script)
        home = self.base / 'home with spaces'
        result = subprocess.run(['sh', str(portable), 'https://example.org/a b'],
                                env={'HOME': str(home), 'PATH': '/usr/bin:/bin',
                                     'CDP_PORT': '9333'}, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertIn('--remote-debugging-port=9333', args)
        self.assertEqual(args[-1], 'https://example.org/a b')
        self.assertTrue((home / '.config/chromium-rockchip-headless').is_dir())
        bad = subprocess.run(['sh', str(portable)],
                             env={'HOME': str(home), 'PATH': '/usr/bin:/bin',
                                  'CDP_PORT': '9333; touch /tmp/unwanted'},
                             text=True, capture_output=True)
        self.assertEqual(bad.returncode, 2)


if __name__ == '__main__':
    unittest.main()
