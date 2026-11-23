"""Publish a release that boxes update to over WiFi.

By hand (creates the tag on the target branch's tip):

    py -3 scripts/release.py vX.Y.Z [--target <branch>] [--attach FILE]... [--notes TEXT]

    Checks that the working tree is clean and is the pushed tip of the target
    branch (default main), builds the bundle with the tag as its version
    (scripts/build.py, needs MPY_CROSS), and creates the GitHub release with
    the bundle and any --attach files.

From an existing tag (CI, after the tag was pushed):

    python scripts/release.py vX.Y.Z --from-tag [--bundle FILE] [--attach FILE]... [--notes TEXT] [--check-only]

    Checks that the tree is clean, HEAD is the tag's commit, and that commit is
    on origin/main. --check-only stops there. --bundle uses that bundle
    (its version must equal the tag) instead of building one. The release is
    created from the tag (gh --verify-tag), never a new one.

gh runs in the repo root, so give FILE paths from there (or absolute).
A tag containing '-' (v0.3.0-rc1) is published as a prerelease, which boxes
skip. Any other release becomes the latest one, which every box with OTA on
installs.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build  # noqa: E402


def is_prerelease(tag):
    return "-" in tag


def release_command(tag, target, assets, prerelease=False, notes=None):
    cmd = ["gh", "release", "create", tag]
    cmd += ["--target", target] if target else ["--verify-tag"]
    cmd += ["--generate-notes"]
    if notes:
        cmd += ["--notes", notes]
    if prerelease:
        cmd += ["--prerelease"]
    return cmd + list(assets)


def _git(repo_dir, *args):
    done = subprocess.run(
        ["git"] + list(args), cwd=repo_dir, capture_output=True, text=True
    )
    if done.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), done.stderr.strip()))
    return done.stdout.strip()


def check_clean(target, repo_dir=REPO):
    """Raise RuntimeError unless the tree is clean, HEAD is `target`, and
    `target` is pushed (same commit as origin/<target>)."""
    if _git(repo_dir, "status", "--porcelain"):
        raise RuntimeError("working tree not clean")
    local = _git(repo_dir, "rev-parse", target)
    if _git(repo_dir, "rev-parse", "origin/" + target) != local:
        raise RuntimeError(
            "%s is not pushed (differs from origin/%s)" % (target, target)
        )
    if _git(repo_dir, "rev-parse", "HEAD") != local:
        raise RuntimeError(
            "check out %s first: the bundle is built from the working tree" % target
        )


def check_tag(tag, branch="main", repo_dir=REPO):
    """Raise RuntimeError unless the tree is clean, HEAD is `tag`'s commit,
    and that commit is on origin/<branch> (its tip or an older commit)."""
    if _git(repo_dir, "status", "--porcelain"):
        raise RuntimeError("working tree not clean")
    commit = _git(repo_dir, "rev-parse", "--verify", tag + "^{commit}")
    if _git(repo_dir, "rev-parse", "HEAD") != commit:
        raise RuntimeError(
            "check out %s first: the bundle is built from the working tree" % tag
        )
    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "origin/" + branch],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if done.returncode == 1:
        raise RuntimeError("%s is not on origin/%s" % (tag, branch))
    if done.returncode != 0:
        raise RuntimeError("git merge-base: %s" % done.stderr.strip())


def _option(argv, name):
    """The value after `name` in argv, or None."""
    if name not in argv:
        return None
    return argv[argv.index(name) + 1]


def _options(argv, name):
    """Every value after `name` in argv, in order."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == name]


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    tag = argv[0]
    attach = _options(argv, "--attach")
    notes = _option(argv, "--notes")
    if "--from-tag" in argv:
        check_tag(tag, "main")
        if "--check-only" in argv:
            print("%s can be released" % tag)
            return 0
        target = None
        bundle = _option(argv, "--bundle")
        if bundle:
            header, _ = build.read_bundle(bundle)
            if header["version"] != tag:
                raise RuntimeError(
                    "%s is version %s, not %s" % (bundle, header["version"], tag)
                )
    else:
        target = _option(argv, "--target") or "main"
        check_clean(target)
        bundle = None
    if not bundle:
        bundle = os.path.relpath(build.build(REPO, tag), REPO)
        print("built %s" % bundle)
    subprocess.run(
        release_command(tag, target, [bundle] + attach, is_prerelease(tag), notes),
        cwd=REPO,
        check=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
