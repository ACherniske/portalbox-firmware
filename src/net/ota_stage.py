"""Download an update into the stage folder and check every byte.

Each file goes straight to flash while its git blob SHA-1 is worked out,
so no file is ever held whole in RAM. Nothing here touches the live files:
recovery.install() swaps the staged copies in.
"""
import os
import json
import uhashlib as hashlib
import ubinascii as binascii

import recovery
from net.ota_target import OtaError

MARGIN = 32768      # left free after staging, for the log, state and journal
CHUNK = 1024


def _blob_hash(size):
    return hashlib.sha1(('blob %d\0' % size).encode())


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
            raise OtaError('bad hash ' + self.path)
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
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return _hex(h)


def free_space():
    stat = os.statvfs('/')
    return stat[0] * stat[3]


def space_needed(files):
    """Flash the staged files take: each rounded up to whole blocks, two
    metadata blocks per folder (LittleFS), plus MARGIN."""
    block = os.statvfs('/')[0]
    total = sum(-(-size // block) * block for _, _, size in files)
    folders = set(p.rsplit('/', 1)[0] if '/' in p else '' for p, _, _ in files)
    return total + 2 * block * len(folders) + MARGIN


def stage(plan, tree, raw_base, fetch, abort=None, free_bytes=None):
    """Download plan['files'] into recovery.STAGE_DIR and check each one.

    fetch(url, sink) streams the body at url to sink.write. The URL is
    raw_base + 'src/' + path. Raises OtaError for: less free flash than
    space_needed() ('no space'), abort() true before a file ('aborted'), a size or hash
    that differs from the tree ('bad hash <path>'), or a manifest path
    that is not a file in the target tree ('manifest <path> missing').
    On any error the stage folder is removed and the error re-raised.
    """
    try:
        _stage(plan, tree, raw_base, fetch, abort, free_bytes)
    except BaseException:
        try:
            recovery._rm_tree(recovery.STAGE_DIR)
        except OSError:
            pass   # keep the first error; the next run clears the stage first
        raise


def _stage(plan, tree, raw_base, fetch, abort, free_bytes):
    recovery._rm_tree(recovery.STAGE_DIR)   # leftovers from a cut-off run
    files = plan['files']
    if space_needed(files) > (free_space() if free_bytes is None else free_bytes):
        raise OtaError('no space')
    for path, sha, size in files:
        if abort and abort():
            raise OtaError('aborted')
        dest = recovery.STAGE_DIR + '/' + path
        recovery._make_parent(dest)
        with open(dest, 'wb') as f:
            sink = _Sink(f, path, size)
            fetch(raw_base + 'src/' + path, sink)
        if sink.count != size or _hex(sink.hash) != sha:
            raise OtaError('bad hash ' + path)
    _check_manifest(files, tree)


def _check_manifest(files, tree):
    """Every file the target's manifest lists must be in the target tree."""
    if any(path == 'manifest.json' for path, _, _ in files):
        where = recovery.STAGE_DIR + '/manifest.json'
    else:
        where = recovery.LIVE_ROOT + '/manifest.json'   # unchanged: same bytes
    try:
        with open(where, 'r') as f:
            listed = json.loads(f.read())
    except ValueError:
        raise OtaError('bad manifest')
    blobs = set(e['path'] for e in tree if e['type'] == 'blob')
    for entry in listed:
        path = entry[4:] if entry.startswith('src/') else entry
        if path not in blobs:
            raise OtaError('manifest %s missing' % path)
