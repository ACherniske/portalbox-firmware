"""Host tests for scripts/release.py. Run: py -3 -m unittest discover -s scripts/tests"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import release  # noqa: E402


def _git(cwd, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
        ]
        + list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class TestRelease(unittest.TestCase):
    def setUp(self):
        """A clone of a bare origin, with one pushed commit on main."""
        self.tmp = tempfile.mkdtemp()
        origin = os.path.join(self.tmp, "origin.git")
        self.repo = os.path.join(self.tmp, "work")
        _git(self.tmp, "init", "--bare", "-b", "main", origin)
        _git(self.tmp, "clone", origin, self.repo)
        _git(self.repo, "switch", "-c", "main")
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("a\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-m", "a")
        _git(self.repo, "push", "-u", "origin", "main")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_release_command(self):
        self.assertEqual(
            release.release_command("v1.2.3", "main", "build/portalbox.bundle"),
            [
                "gh",
                "release",
                "create",
                "v1.2.3",
                "--target",
                "main",
                "--generate-notes",
                "build/portalbox.bundle",
            ],
        )

    def test_check_clean_passes_when_pushed(self):
        release.check_clean("main", self.repo)

    def test_check_clean_refuses_dirty(self):
        with open(os.path.join(self.repo, "new.txt"), "w") as f:
            f.write("x\n")
        with self.assertRaises(RuntimeError):
            release.check_clean("main", self.repo)

    def test_check_clean_refuses_unpushed(self):
        with open(os.path.join(self.repo, "a.txt"), "a") as f:
            f.write("b\n")
        _git(self.repo, "commit", "-am", "b")
        with self.assertRaises(RuntimeError):
            release.check_clean("main", self.repo)

    def test_check_clean_refuses_other_checkout(self):
        """The bundle is built from the working tree: it must be the target."""
        _git(self.repo, "switch", "-c", "other")
        with open(os.path.join(self.repo, "a.txt"), "a") as f:
            f.write("c\n")
        _git(self.repo, "commit", "-am", "c")
        _git(self.repo, "push", "-u", "origin", "other")
        with self.assertRaises(RuntimeError):
            release.check_clean("main", self.repo)

    def test_check_clean_refuses_unknown_target(self):
        with self.assertRaises(RuntimeError):
            release.check_clean("nope", self.repo)


if __name__ == "__main__":
    unittest.main()
