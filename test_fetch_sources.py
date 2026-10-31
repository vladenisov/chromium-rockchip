import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("fetch_sources", Path(__file__).with_name("fetch-sources.py"))
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


def archive(path, entries):
    with tarfile.open(path, "w") as out:
        for name, value in entries:
            item = tarfile.TarInfo(name)
            if isinstance(value, tuple):
                item.type, item.linkname = value
                out.addfile(item)
            else:
                data = value.encode()
                item.size = len(data)
                out.addfile(item, io.BytesIO(data))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_bad_digest_does_not_publish_or_leave_partial(self):
        entry = {"url": "https://example.org/source.tar", "size": 3, "sha256": "0" * 64}
        with patch.object(fetch.urllib.request, "urlopen", return_value=io.BytesIO(b"abc")):
            with self.assertRaises(ValueError):
                fetch.download(entry, self.root, attempts=1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_verified_download_reused_without_network(self):
        data = b"abc"
        entry = {"url": "https://example.org/source.tar", "size": 3,
                 "sha256": hashlib.sha256(data).hexdigest()}
        target = self.root / "source.tar"
        target.write_bytes(data)
        with patch.object(fetch.urllib.request, "urlopen", side_effect=AssertionError("network")):
            self.assertEqual(fetch.download(entry, self.root), target)

    def test_oversized_download_is_rejected(self):
        entry = {"url": "https://example.org/source.tar", "size": 2, "sha256": "0" * 64}
        with patch.object(fetch.urllib.request, "urlopen", return_value=io.BytesIO(b"abc")):
            with self.assertRaises(ValueError):
                fetch.download(entry, self.root, attempts=1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_traversal_and_escaping_links_rejected(self):
        for member, value in [("../escape", "x"), ("/absolute", "x"),
                              ("top/link", (tarfile.SYMTYPE, "../../escape")),
                              ("top/link", (tarfile.LNKTYPE, "../escape"))]:
            with self.subTest(member=member, value=value):
                source = self.root / "bad.tar"
                archive(source, [(member, value)])
                target = self.root / "result"
                with self.assertRaises(ValueError):
                    fetch.extract(source, target, strip_root=True)
                self.assertFalse(target.exists())
                self.assertFalse((self.root / "escape").exists())

    def test_good_source_and_packaging(self):
        source = self.root / "source.tar"
        version = "MAJOR=156\nMINOR=0\nBUILD=8078\nPATCH=79\n"
        archive(source, [("chromium/chrome/VERSION", version), ("chromium/nested/file", "ok")])
        target = self.root / "chromium"
        fetch.extract(source, target, strip_root=True, version="156.0.8078.79")
        self.assertEqual((target / "nested/file").read_text(), "ok")
        self.assertTrue((target / fetch.MARKER).exists())
        (target / "nested/file").write_text("patched")
        fetch.extract(source, target, strip_root=True, version="156.0.8078.79")
        self.assertEqual((target / "nested/file").read_text(), "patched")
        archive(source, [("debian/rules", "rules")])
        fetch.extract(source, self.root / "packaging", required="debian/rules")
        self.assertEqual((self.root / "packaging/debian/rules").read_text(), "rules")

    def test_incomplete_or_wrong_version_never_marked_ready(self):
        source = self.root / "source.tar"
        archive(source, [("root/file", "x")])
        target = self.root / "chromium"
        with self.assertRaises(ValueError):
            fetch.extract(source, target, strip_root=True, version="156.0.8078.79")
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".chromium.extract-*")))

    def test_existing_unmarked_source_is_preserved(self):
        target = self.root / "chromium"
        target.mkdir()
        (target / "private").write_text("keep")
        with self.assertRaises(ValueError):
            fetch.extract(self.root / "missing.tar", target, strip_root=True)
        self.assertEqual((target / "private").read_text(), "keep")

    def test_interrupted_extraction_is_not_ready_and_cleans_stage(self):
        target = self.root / "chromium"
        def interrupted(archive, stage, strip_root):
            (stage / "partial").write_text("unfinished")
            raise OSError("disk write interrupted")
        with patch.object(fetch, "unpack", side_effect=interrupted):
            with self.assertRaises(OSError):
                fetch.extract(self.root / "source.tar", target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".chromium.extract-*")))

    def test_internal_symlink_allowed_but_hardlink_to_symlink_rejected(self):
        source = self.root / "source.tar"
        archive(source, [("root/nested/file", "ok"),
                         ("root/link", (tarfile.SYMTYPE, "nested/file"))])
        target = self.root / "good"
        fetch.extract(source, target, strip_root=True)
        self.assertEqual((target / "link").read_text(), "ok")
        archive(source, [("root/nested/file", "ok"),
                         ("root/nested/link", (tarfile.SYMTYPE, "file")),
                         ("root/hard", (tarfile.LNKTYPE, "root/nested/link"))])
        with self.assertRaises(ValueError):
            fetch.extract(source, self.root / "bad", strip_root=True)

    def test_device_node_rejected(self):
        source = self.root / "source.tar"
        with tarfile.open(source, "w") as out:
            item = tarfile.TarInfo("root/device")
            item.type = tarfile.CHRTYPE
            out.addfile(item)
        with self.assertRaises(ValueError):
            fetch.extract(source, self.root / "bad", strip_root=True)


if __name__ == "__main__":
    unittest.main()
