"""Build the box's files: modules compiled to .mpy, packed in one bundle.

Usage: py -3 scripts/build.py [--version V]

MPY_CROSS names the mpy-cross command (built from the firmware's MicroPython
fork, see BUILD_GUIDE.md), for example:
    MPY_CROSS="wsl -e /home/<you>/build/micropython-e244d80/mpy-cross/build/mpy-cross"

Output: build/files/<device path> and build/portalbox.bundle. The bundle is
one JSON header line, then each file's bytes back to back in header order:
    {"format": 1, "mpy": "6.3", "version": "<tag>", "files": [[path, size, sha], ...]}
sha is the file's git blob SHA-1 (what the box already checks).
"""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess

MPY_VERSION = "6.3"
BUNDLE_NAME = "portalbox.bundle"
FORMAT = 1
HEADER_MAX = 8192
# MicroPython runs boot.py and main.py only as source; recovery checks all three.
ANCHORS = ("boot.py", "main.py", "recovery.py")
# Per-box files and hand-run tools: never compiled, never in the bundle.
_NOT_SHIPPED_DIRS = ("config", "tools")


def git_blob_sha(data):
    """Git blob SHA-1 of bytes, as git hash-object gives it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _source_modules(src_dir):
    """Every .py under src_dir outside config/ and tools/, as 'a/b.py'."""
    found = []
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), src_dir).replace(
                os.sep, "/"
            )
            if rel.endswith(".py") and rel.split("/")[0] not in _NOT_SHIPPED_DIRS:
                found.append(rel)
    return sorted(found)


def ship_list(src_dir):
    """(source path, device path) for each file that goes on a box, sorted by
    device path. The list is src/manifest.json without config/ and the
    manifest itself; modules become .mpy, the anchors stay .py. A module on
    disk that the manifest misses is an error: it would never reach a box."""
    with open(os.path.join(src_dir, "manifest.json")) as f:
        listed = [p[4:] if p.startswith("src/") else p for p in json.load(f)]
    for rel in _source_modules(src_dir):
        if rel not in listed:
            raise ValueError("not in manifest: " + rel)
    pairs = []
    for rel in listed:
        if rel == "manifest.json" or rel.split("/")[0] in _NOT_SHIPPED_DIRS:
            continue
        if rel.endswith(".py") and rel not in ANCHORS:
            pairs.append((rel, rel[:-3] + ".mpy"))
        else:
            pairs.append((rel, rel))
    return sorted(pairs, key=lambda pair: pair[1])


def check_mpy_cross(cmd):
    """Refuse an mpy-cross that writes another .mpy version than the firmware's."""
    out = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
    text = out.stdout + out.stderr
    if "mpy v" + MPY_VERSION not in text:
        raise RuntimeError(
            "mpy-cross must emit mpy v%s, says: %s" % (MPY_VERSION, text.strip())
        )


def compile_all(src_dir, out_dir, cmd):
    """Compile the ship list into out_dir/<device path>; copy the rest as is.

    mpy-cross runs in src_dir with relative paths only, so a Linux build run
    through WSL sees the same files."""
    for rel, device in ship_list(src_dir):
        dest = os.path.join(out_dir, device)
        os.makedirs(os.path.dirname(dest) or out_dir, exist_ok=True)
        if device == rel:
            shutil.copyfile(os.path.join(src_dir, rel), dest)
            continue
        rel_dest = os.path.relpath(dest, src_dir).replace(os.sep, "/")
        done = subprocess.run(
            cmd + ["-o", rel_dest, rel], cwd=src_dir, capture_output=True, text=True
        )
        if done.returncode != 0 or not os.path.exists(dest):
            raise RuntimeError(
                "mpy-cross failed on %s: %s" % (rel, done.stderr.strip())
            )


def write_bundle(files, version, path):
    """Write (device path, bytes) pairs as a bundle; returns its header."""
    files = sorted(files)
    paths = [p for p, _ in files]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate path in bundle")
    header = {
        "format": FORMAT,
        "mpy": MPY_VERSION,
        "version": version,
        "files": [[p, len(data), git_blob_sha(data)] for p, data in files],
    }
    line = (json.dumps(header, separators=(",", ":")) + "\n").encode()
    if len(line) > HEADER_MAX:
        raise ValueError("bundle header %d bytes, max %d" % (len(line), HEADER_MAX))
    with open(path, "wb") as f:
        f.write(line)
        for _, data in files:
            f.write(data)
    return header


def read_bundle(path):
    """(header, {device path: bytes}) of a bundle; raises ValueError if the
    body does not match the header."""
    with open(path, "rb") as f:
        data = f.read()
    line, _, body = data.partition(b"\n")
    header = json.loads(line)
    bodies = {}
    at = 0
    for p, size, sha in header["files"]:
        chunk = body[at : at + size]
        if len(chunk) != size or git_blob_sha(chunk) != sha:
            raise ValueError("bad bundle entry " + p)
        bodies[p] = chunk
        at += size
    if at != len(body):
        raise ValueError("bundle has %d extra bytes" % (len(body) - at))
    return header, bodies


def mpy_cross_command():
    value = os.environ.get("MPY_CROSS", "").strip()
    if not value:
        raise RuntimeError(
            "set MPY_CROSS to the mpy-cross command (see BUILD_GUIDE.md)"
        )
    return shlex.split(value)


def build(repo_dir, version="dev"):
    """Compile into build/files and write build/portalbox.bundle; returns its path."""
    src_dir = os.path.join(repo_dir, "src")
    out_dir = os.path.join(repo_dir, "build")
    files_dir = os.path.join(out_dir, "files")
    cmd = mpy_cross_command()
    check_mpy_cross(cmd)
    shutil.rmtree(files_dir, ignore_errors=True)  # no stale files from an older list
    compile_all(src_dir, files_dir, cmd)
    files = []
    for _, device in ship_list(src_dir):
        with open(os.path.join(files_dir, device), "rb") as f:
            files.append((device, f.read()))
    path = os.path.join(out_dir, BUNDLE_NAME)
    write_bundle(files, version, path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version", default="dev", help="version written in the bundle header"
    )
    args = parser.parse_args()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = build(repo, args.version)
    header, _ = read_bundle(path)
    print(
        "%s: %d files, %d bytes" % (path, len(header["files"]), os.path.getsize(path))
    )


if __name__ == "__main__":
    main()
