#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("find-reusable-build.sh").resolve()
FAKE_GH = """#!/bin/sh
case "$1" in
    run) cat "$FAKE_RUNS" ;;
    api) case "$2" in *"/$FAKE_NO_ARTIFACT/"*) ;; *) echo browser ;; esac ;;
esac
"""


class FindReusableBuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.commit("build-browser.py", "one")
        self.built = self.git("rev-parse", "HEAD").strip()
        gh = self.root / "bin/gh"
        gh.parent.mkdir()
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.runs = self.root / "runs"
        self.runs.write_text(f"100 {self.built}\n")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def commit(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git("add", path)
        self.git("commit", "-qm", path)

    def find(self, no_artifact="none"):
        env = dict(os.environ, PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
                   FAKE_RUNS=str(self.runs), FAKE_NO_ARTIFACT=no_artifact)
        return subprocess.run(["sh", str(SCRIPT)], cwd=self.repo, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_docs_and_tools_reuse_the_build(self):
        for path in ("README.md", "patches/chromium/1/PORT-REPORT.md", "install.py",
                     "test_install.py", ".github/workflows/build.yml"):
            self.commit(path, "changed")
        self.assertEqual(self.find(), "100")

    def test_build_inputs_need_a_build(self):
        for path in ("build-browser.py", "sources.json", "Dockerfile",
                     "patches/chromium/1/0001.patch", ".github/workflows/build-stage.yml",
                     "unknown-new-file"):
            with self.subTest(path=path):
                self.commit(path, f"changed {path}")
                self.assertEqual(self.find(), "")
                self.git("reset", "-q", "--hard", self.built)

    def test_expired_archive_falls_back_to_older_matching_run(self):
        self.runs.write_text(f"200 {self.built}\n100 {self.built}\n")
        self.assertEqual(self.find(no_artifact="200"), "100")
        self.runs.write_text(f"200 {self.built}\n")
        self.assertEqual(self.find(no_artifact="200"), "")

    def test_unknown_commit_is_not_reused(self):
        self.runs.write_text("300 0000000000000000000000000000000000000001\n")
        self.assertEqual(self.find(), "")


if __name__ == "__main__":
    unittest.main()
