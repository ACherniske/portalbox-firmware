"""Load a box by USB cable with precompiled .mpy modules.

    py -3 scripts/deploy.py COM16 [--tests]

Builds the same files a release ships (scripts/build.py, version 'cable'),
stops the box at the REPL, and copies only what differs. Each X.py an X.mpy
replaces is deleted (MicroPython imports a .py ahead of its .mpy), and so is
an old manifest.json. config/pins.py and config/settings.py are copied only
to a box that has none: they are per box. --tests also copies tests/ as .py.
Needs MPY_CROSS (see BUILD_GUIDE.md), mpremote and pyserial.
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build  # noqa: E402

EXTRAS = ("config/pins.py", "config/settings.py", "tools/configure_wifi.py")
BATCH = 15  # mpremote commands chained per call

# Runs on the box: git blob sha of every file outside tests/, log/, ota/, sd/.
_LIST_BOX = r"""
import os, json, uhashlib, ubinascii
def _walk(d, out):
    for name in os.listdir(d):
        p = d + '/' + name if d != '/' else '/' + name
        if p in ('/tests', '/log', '/ota', '/sd'):
            continue
        st = os.stat(p)
        if st[0] & 0x4000:
            _walk(p, out)
            continue
        h = uhashlib.sha1(('blob %d\0' % st[6]).encode())
        with open(p, 'rb') as f:
            while True:
                c = f.read(1024)
                if not c:
                    break
                h.update(c)
        out[p[1:]] = ubinascii.hexlify(h.digest()).decode()
_out = {}
_walk('/', _out)
print('@@' + json.dumps(_out))
"""


def deploy_actions(bundle_files, box_hashes, box_paths, local_extras):
    """(copies, deletes), both sorted device paths.

    bundle_files and local_extras: device path -> sha; box_hashes: box
    path -> sha; box_paths: every file on the box. Copies each bundle file
    that differs or is missing, config/ extras only when missing, other
    extras when they differ. Deletes each X.py whose X.mpy is in the bundle
    (never an anchor or config/), and manifest.json.
    """
    copies = [p for p, sha in bundle_files.items() if box_hashes.get(p) != sha]
    for path, sha in local_extras.items():
        if path.startswith("config/"):
            if path not in box_paths:
                copies.append(path)
        elif box_hashes.get(path) != sha:
            copies.append(path)
    deletes = []
    for path in bundle_files:
        if not path.endswith(".mpy"):
            continue
        py = path[:-4] + ".py"
        if py in box_paths and py not in build.ANCHORS and not py.startswith("config/"):
            deletes.append(py)
    if "manifest.json" in box_paths:
        deletes.append("manifest.json")
    return sorted(copies), sorted(deletes)


def to_repl(port):
    """Hard-reset the box and stop main.py in its 1.5 s safe-mode wait,
    before the watchdog is armed, leaving a plain REPL."""
    import serial

    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0.05
    s.dtr = False
    s.rts = False
    s.open()
    s.rts = True
    time.sleep(0.2)
    s.rts = False
    out = b""
    end = time.time() + 4
    while time.time() < end:
        s.write(b"\x03")
        out += s.read(4096)
        time.sleep(0.1)
    s.write(b"\x02")
    time.sleep(0.3)
    s.write(b'print("REPL" + " OK")\r')
    time.sleep(0.5)
    out += s.read(8192)
    s.close()
    if b"REPL OK" not in out:
        raise RuntimeError("box did not stop at the REPL")


def _mpremote(port, args):
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + args
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError("mpremote failed: %s" % (done.stderr or done.stdout).strip())
    return done.stdout


def _box_hashes(port):
    out = _mpremote(port, ["exec", _LIST_BOX])
    for line in out.splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:])
    raise RuntimeError("no file list from the box: %s" % out.strip())


def _make_dirs(port, paths):
    dirs = sorted(set(p.rsplit("/", 1)[0] for p in paths if "/" in p))
    if not dirs:
        return
    script = (
        "import os\nfor d in %r:\n"
        '    p = ""\n'
        '    for part in d.split("/"):\n'
        '        p += "/" + part\n'
        "        try:\n"
        "            os.mkdir(p)\n"
        "        except OSError:\n"
        "            pass\n"
    ) % (dirs,)
    _mpremote(port, ["exec", script])


def _run_batched(port, commands):
    for at in range(0, len(commands), BATCH):
        args = []
        for command in commands[at : at + BATCH]:
            if args:
                args.append("+")
            args.extend(command)
        _mpremote(port, args)


def _blob(path):
    with open(path, "rb") as f:
        return build.git_blob_sha(f.read())


def _test_files():
    out = subprocess.run(
        ["git", "ls-files", "tests"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.splitlines() if p.endswith(".py")]


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    port = argv[0]
    with_tests = "--tests" in argv[1:]

    bundle_path = build.build(REPO, "cable")
    header, _ = build.read_bundle(bundle_path)
    bundle_files = {p: sha for p, _, sha in header["files"]}
    src = os.path.join(REPO, "src")
    extras = {p: _blob(os.path.join(src, p)) for p in EXTRAS}
    files_dir = os.path.join(REPO, "build", "files")

    to_repl(port)
    box = _box_hashes(port)
    copies, deletes = deploy_actions(bundle_files, box, set(box), extras)

    # Copy first, then delete: a cut-off run leaves the old .py beside its
    # new .mpy (the .py still imports), never a module with neither.
    _make_dirs(port, copies)
    commands = []
    for path in copies:
        local = os.path.join(files_dir if path in bundle_files else src, path)
        commands.append(["cp", local, ":/" + path])
        print("copy   " + path)
    for path in deletes:
        commands.append(["rm", ":/" + path])
        print("delete " + path)
    _run_batched(port, commands)

    if with_tests:
        tests = _test_files()
        _make_dirs(port, tests)
        _run_batched(port, [["cp", os.path.join(REPO, p), ":/" + p] for p in tests])
        print("copied %d test files" % len(tests))

    print(
        "%d copied, %d deleted. Reset the box to boot it." % (len(copies), len(deletes))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
