"""Stream a release bundle into the stage folder and check every byte.

Each file goes straight to flash while its git blob SHA-1 is worked out,
so no file is ever held whole in RAM. Nothing here touches the live files:
recovery.install() swaps the staged copies in.
"""

import os
import sys
import json
import uhashlib as hashlib
import ubinascii as binascii

import recovery
from net.ota_target import OtaError

MARGIN = 32768  # left free after staging, for the log, state and journal
CHUNK = 1024


def _blob_hash(size):
    return hashlib.sha1(("blob %d\0" % size).encode())


def _hex(h):
    return binascii.hexlify(h.digest()).decode()


class _Sink:
    """Writes each chunk to a file and hashes it as a git blob of `size`
    bytes. A body longer than `size` stops at once, before it fills flash."""

    def __init__(self, f, path, size):
        self.f = f
        self.path = path
        self.size = size
        self.count = 0
        self.hash = _blob_hash(size)

    def write(self, chunk):
        if self.count + len(chunk) > self.size:
            raise OtaError("bad hash " + self.path)
        self.f.write(chunk)
        self.hash.update(chunk)
        self.count += len(chunk)


def file_sha(path):
    """Git blob sha of a file on the box, read in chunks. None if missing."""
    try:
        size = os.stat(path)[6]
    except OSError:
        return None
    h = _blob_hash(size)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return _hex(h)


def free_space():
    stat = os.statvfs("/")
    return stat[0] * stat[3]


def space_needed(files):
    """Flash the staged files take: each rounded up to whole blocks, two
    metadata blocks per folder (LittleFS), plus MARGIN."""
    block = os.statvfs("/")[0]
    total = sum(-(-size // block) * block for _, _, size in files)
    folders = set(p.rsplit("/", 1)[0] if "/" in p else "" for p, _, _ in files)
    return total + 2 * block * len(folders) + MARGIN


def _transient(e):
    """A dropped download worth one more try: a network error, a server
    error (5xx) or a broken body (HttpError status 0). Not a 404, a bad
    hash or running out of memory."""
    if isinstance(e, OSError):
        return True
    status = getattr(e, "status", None)
    return status is not None and (status == 0 or status >= 500)


# One JSON header line, then each file's bytes back to back in header order
# (scripts/build.py writes it):
#   {"format": 1, "mpy": "6.3", "version": "<tag>", "files": [[path, size, sha], ...]}

BUNDLE_FORMAT = 1
HEADER_MAX = 8192


def firmware_mpy():
    """The .mpy version this firmware loads, e.g. '6.3'."""
    v = sys.implementation._mpy
    return "%d.%d" % (v & 0xFF, (v >> 8) & 3)


def parse_header(line):
    """The bundle's header line -> {'version', 'files': [(path, size, sha)]}.

    OtaError('bad bundle') for anything malformed; OtaError('mpy X, box Y')
    for modules built for another firmware (they would not import).
    """
    try:
        h = json.loads(line)
        files = []
        for path, size, sha in h["files"]:
            if not (
                isinstance(path, str)
                and isinstance(size, int)
                and size >= 0
                and isinstance(sha, str)
            ):
                raise ValueError
            files.append((path, size, sha))
        ok = h["format"] == BUNDLE_FORMAT and isinstance(h["version"], str)
    except (ValueError, KeyError, TypeError):
        raise OtaError("bad bundle")
    if not ok:
        raise OtaError("bad bundle")
    if h.get("mpy") != firmware_mpy():
        raise OtaError("mpy %s, box %s" % (h.get("mpy"), firmware_mpy()))
    return {"version": h["version"], "files": files}


class _BundleSink:
    """Takes the bundle chunk by chunk (sink.write). After the header it asks
    plan_for(header) for the plan, checks the space, then writes each
    planned file to the stage folder through _Sink and reads past the rest."""

    def __init__(self, plan_for, abort, free_bytes):
        self.plan_for = plan_for
        self.abort = abort
        self.free_bytes = free_bytes
        self.head = b""  # header bytes until the newline; None after
        self.plan = None
        self.files = []
        self.keep = {}
        self.index = 0  # file being read
        self.left = 0  # its bytes still to come
        self.f = None
        self.sink = None

    def write(self, chunk):
        if self.abort and self.abort():
            raise OtaError("aborted")
        if self.head is not None:
            self.head += chunk
            end = self.head.find(b"\n")
            if end < 0 or end >= HEADER_MAX:
                if end >= HEADER_MAX or len(self.head) > HEADER_MAX:
                    raise OtaError("bad bundle")
                return
            line, chunk = self.head[:end], self.head[end + 1 :]
            self.head = None
            self._start(parse_header(line))
        self._body(chunk)

    def _start(self, header):
        plan = self.plan_for(header)
        free = free_space() if self.free_bytes is None else self.free_bytes
        if space_needed(plan["files"]) > free:
            raise OtaError("no space")
        self.plan = plan
        self.keep = set(p for p, _, _ in plan["files"])
        self.files = header["files"]
        self.index = -1
        self._next()

    def _next(self):
        """Move to the next file; files of 0 bytes are done at once."""
        while True:
            self.index += 1
            if self.index >= len(self.files):
                return
            path, size, _ = self.files[self.index]
            self.left = size
            if path in self.keep:
                dest = recovery.STAGE_DIR + "/" + path
                recovery._make_parent(dest)
                self.f = open(dest, "wb")
                self.sink = _Sink(self.f, path, size)
            if size:
                return
            self._done()

    def _done(self):
        """The current file is complete: check what was staged."""
        path, size, sha = self.files[self.index]
        if self.sink is not None:
            self.f.close()
            self.f = None
            if self.sink.count != size or _hex(self.sink.hash) != sha:
                self.sink = None
                raise OtaError("bad hash " + path)
            self.sink = None

    def _body(self, data):
        pos = 0
        while pos < len(data):
            if self.index >= len(self.files):
                raise OtaError("bundle too long")
            take = min(self.left, len(data) - pos)
            if self.sink is not None:
                self.sink.write(data[pos : pos + take])
            self.left -= take
            pos += take
            if self.left == 0:
                self._done()
                self._next()

    def finish(self):
        """The body ended: every file must have come."""
        if self.head is not None or self.index < len(self.files):
            raise OtaError("short bundle")

    def close(self):
        if self.f is not None:
            self.f.close()
            self.f = None


def stage_bundle(fetch, plan_for, abort=None, free_bytes=None):
    """Stream a bundle into recovery.STAGE_DIR, staging only what the plan
    names, each file checked by size and git blob hash. Returns the plan.

    fetch(sink) streams the bundle to sink.write. plan_for(header) returns
    {'files': [(path, sha, size)], 'deletes', 'managed'} or raises
    OtaError. Raises OtaError: 'bad bundle', 'mpy X, box Y', 'no space',
    'aborted' (abort() true when a chunk arrives), 'bad hash <path>',
    'short bundle', 'bundle too long'. A dropped download (_transient)
    starts the bundle over once. On any error the stage folder is removed
    and the error re-raised.
    """
    try:
        for attempt in (1, 2):
            recovery._rm_tree(recovery.STAGE_DIR)  # leftovers, or the dropped try
            sink = _BundleSink(plan_for, abort, free_bytes)
            try:
                fetch(sink)
                sink.finish()
                return sink.plan
            except Exception as e:
                sink.close()
                if attempt == 2 or not _transient(e):
                    raise
    except BaseException:
        try:
            recovery._rm_tree(recovery.STAGE_DIR)
        except OSError:
            pass  # keep the first error; the next run clears the stage first
        raise
