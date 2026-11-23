"""Publish a release that boxes update to over WiFi.

    py -3 scripts/release.py vX.Y.Z [--target <branch>]     (default main)

Checks that the working tree is clean and is the pushed tip of the target
branch, builds the bundle with the tag as its version (scripts/build.py,
needs MPY_CROSS), and creates the GitHub release with the bundle attached.
The release becomes the latest one, which every box with OTA on installs.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build  # noqa: E402


def release_command(tag, target, bundle):
    return [
        "gh",
        "release",
        "create",
        tag,
        "--target",
        target,
        "--generate-notes",
        bundle,
    ]


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


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    tag = argv[0]
    target = "main"
    if "--target" in argv:
        target = argv[argv.index("--target") + 1]
    check_clean(target)
    bundle = build.build(REPO, tag)
    print("built %s" % bundle)
    subprocess.run(
        release_command(tag, target, os.path.relpath(bundle, REPO)),
        cwd=REPO,
        check=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
