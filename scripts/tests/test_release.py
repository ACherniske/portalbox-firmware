"""Host tests for scripts/release.py. Run: py -3 -m unittest discover -s scripts/tests"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import build  # noqa: E402
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

    def _commit(self, line):
        with open(os.path.join(self.repo, "a.txt"), "a") as f:
            f.write(line + "\n")
        _git(self.repo, "commit", "-am", line)

    def test_release_command(self):
        self.assertEqual(
            release.release_command("v1.2.3", "main", ["build/portalbox.bundle"]),
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

    def test_release_command_from_tag(self):
        self.assertEqual(
            release.release_command("v1", None, ["b", "f.bin"], notes="n"),
            [
                "gh",
                "release",
                "create",
                "v1",
                "--verify-tag",
                "--generate-notes",
                "--notes",
                "n",
                "b",
                "f.bin",
            ],
        )

    def test_release_command_prerelease(self):
        cmd = release.release_command("v1-rc1", None, ["b", "f.bin"], prerelease=True)
        self.assertIn("--prerelease", cmd)
        self.assertLess(cmd.index("--prerelease"), cmd.index("b"))
        self.assertEqual(cmd[-2:], ["b", "f.bin"])

    def test_release_command_not_prerelease_by_default(self):
        self.assertNotIn("--prerelease", release.release_command("v1", None, ["b"]))

    def test_is_prerelease(self):
        self.assertTrue(release.is_prerelease("v0.3.0-rc1"))
        self.assertTrue(release.is_prerelease("v0.3.0-beta.1"))
        self.assertFalse(release.is_prerelease("v0.3.0"))

    def test_check_tag_passes_on_main_tip(self):
        _git(self.repo, "tag", "v1")
        _git(self.repo, "checkout", "v1")
        release.check_tag("v1", "main", self.repo)

    def test_check_tag_accepts_older_commit_on_branch(self):
        """Tagged late: the tag's commit is on main but main has moved on."""
        _git(self.repo, "tag", "v1")
        self._commit("2")
        _git(self.repo, "push", "origin", "main")
        _git(self.repo, "checkout", "v1")
        release.check_tag("v1", "main", self.repo)

    def test_check_tag_refuses_not_on_branch(self):
        _git(self.repo, "switch", "-c", "other")
        self._commit("o")
        _git(self.repo, "push", "-u", "origin", "other")
        _git(self.repo, "tag", "v1")
        _git(self.repo, "checkout", "v1")
        with self.assertRaises(RuntimeError):
            release.check_tag("v1", "main", self.repo)

    def test_check_tag_refuses_head_not_tag(self):
        """The bundle is built from the working tree: it must be the tag's."""
        _git(self.repo, "tag", "v1")
        self._commit("2")
        _git(self.repo, "push", "origin", "main")
        with self.assertRaises(RuntimeError):
            release.check_tag("v1", "main", self.repo)

    def test_check_tag_refuses_missing_tag(self):
        with self.assertRaises(RuntimeError):
            release.check_tag("v9", "main", self.repo)

    def test_check_tag_refuses_dirty(self):
        _git(self.repo, "tag", "v1")
        _git(self.repo, "checkout", "v1")
        with open(os.path.join(self.repo, "new.txt"), "w") as f:
            f.write("x\n")
        with self.assertRaises(RuntimeError):
            release.check_tag("v1", "main", self.repo)

    def test_main_check_only_does_not_build(self):
        with mock.patch.object(release, "check_tag") as check, mock.patch.object(
            build, "build"
        ) as built, mock.patch.object(release.subprocess, "run") as run:
            self.assertEqual(release.main(["v1", "--from-tag", "--check-only"]), 0)
        check.assert_called_once_with("v1", "main")
        built.assert_not_called()
        run.assert_not_called()

    def test_main_from_tag_uses_given_bundle(self):
        path = os.path.join(self.tmp, "portalbox.bundle")
        build.write_bundle([("main.py", b"x")], "v1", path)
        with mock.patch.object(release, "check_tag") as check, mock.patch.object(
            build, "build"
        ) as built, mock.patch.object(release.subprocess, "run") as run:
            argv = ["v1", "--from-tag", "--bundle", path, "--attach", "f.bin"]
            self.assertEqual(release.main(argv), 0)
        check.assert_called_once_with("v1", "main")
        built.assert_not_called()
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[-2:], [path, "f.bin"])
        self.assertIn("--verify-tag", cmd)
        self.assertNotIn("--target", cmd)
        self.assertNotIn("--prerelease", cmd)

    def test_main_from_tag_rc_is_prerelease(self):
        path = os.path.join(self.tmp, "portalbox.bundle")
        build.write_bundle([("main.py", b"x")], "v1-rc1", path)
        with mock.patch.object(release, "check_tag"), mock.patch.object(
            release.subprocess, "run"
        ) as run:
            release.main(["v1-rc1", "--from-tag", "--bundle", path])
        self.assertIn("--prerelease", run.call_args[0][0])

    def test_main_refuses_bundle_for_other_version(self):
        path = os.path.join(self.tmp, "portalbox.bundle")
        build.write_bundle([("main.py", b"x")], "v0", path)
        with mock.patch.object(release, "check_tag"), mock.patch.object(
            release.subprocess, "run"
        ) as run:
            with self.assertRaises(RuntimeError):
                release.main(["v1", "--from-tag", "--bundle", path])
        run.assert_not_called()

    def test_main_by_hand_attaches_extra_files(self):
        """The by-hand mode keeps its checks and build, and attaches --attach."""
        bundle = os.path.join(release.REPO, "build", "portalbox.bundle")
        with mock.patch.object(release, "check_clean") as check, mock.patch.object(
            build, "build", return_value=bundle
        ), mock.patch.object(release.subprocess, "run") as run:
            argv = ["v1", "--target", "dev", "--attach", "f.bin", "--notes", "n"]
            self.assertEqual(release.main(argv), 0)
        check.assert_called_once_with("dev")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[4:6], ["--target", "dev"])
        self.assertEqual(cmd[-2:], [os.path.join("build", "portalbox.bundle"), "f.bin"])
        self.assertIn("--notes", cmd)

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
