"""Pick the version to update to, and refuse updates that are unsafe to install.

resolve_target() asks GitHub for the latest release and its bundle asset.
build_plan() compares the bundle's header with the box and says what to
change. Nothing is downloaded or installed here.
"""
import recovery
from net.https import HttpError


class OtaError(Exception):
    pass


# The box must be able to boot and reach GitHub again after any update.
# The old code on the box checks the new bundle, so renaming one of these
# later means boxes on the old list need a cable.
REQUIRED = (
    'boot.py', 'main.py', 'recovery.py',
    'core/system.mpy', 'core/main.mpy',
    'net/__init__.mpy', 'net/ota.mpy', 'net/ota_target.mpy', 'net/ota_stage.mpy',
    'net/https.mpy', 'net/wifi.mpy',
    'certs/github_roots.pem',
)

BUNDLE_NAME = 'portalbox.bundle'


def resolve_target(cfg, get_json):
    """Return {'version': tag, 'asset': id} of the latest release's bundle.

    get_json(path) returns the parsed reply for an API path such as
    '/repos/u/r/releases/latest' and raises HttpError on a bad status.
    Only releases are followed: a branch in cfg is ignored.
    """
    repo = '/repos/%s/%s' % (cfg['user'], cfg['repository'])
    try:
        release = get_json(repo + '/releases/latest')
    except HttpError as e:
        if e.status == 404:
            raise OtaError('no release')
        raise
    for asset in release.get('assets', []):
        if asset.get('name') == BUNDLE_NAME:
            return {'version': release['tag_name'], 'asset': asset['id']}
    raise OtaError('no bundle')


def _norm(entry):
    """Ignore entries may be written '/src/x', 'src/x', '/x' or 'x/'."""
    entry = entry.lstrip('/')
    if entry.startswith('src/'):
        entry = entry[4:]
    return entry.rstrip('/')


def _ignored(path, ignore):
    for entry in ignore:
        if path == entry or path.startswith(entry + '/'):
            return True
    return False


def _local_only(path):
    """Per-box files that OTA never writes or deletes (same rule as recovery)."""
    return (path in recovery.LOCAL_FILES
            or path.split('/')[0] in recovery.LOCAL_DIRS)


def shadowed_py(files):
    """The X.py for each X.mpy in the bundle header: MicroPython imports a
    .py ahead of its .mpy, so a stale one must go."""
    return sorted(p[:-4] + '.py' for p, _, _ in files if p.endswith('.mpy'))


def build_plan(files, local_hashes, managed, ignore):
    """Say what an update to the bundle's `files` changes on this box.

    files: header entries (path, size, sha). local_hashes: device path ->
    git blob sha of box files (header paths and shadowed_py paths).
    managed: files the last update installed. ignore: paths OTA leaves
    alone. Returns {'files': [(path, sha, size)] changed or new,
    'deletes': [managed paths gone from the bundle, and each X.py an X.mpy
    replaces], 'managed': [every path this update owns]}. Raises OtaError
    for a bundle that is unsafe to install.
    """
    blobs = {}
    for path, size, sha in files:
        parts = path.split('/')
        if not path or '' in parts or '..' in parts:
            raise OtaError('bad path %r' % path)
        blobs[path] = (sha, size)
    for path in REQUIRED:
        if path not in blobs:
            raise OtaError('missing ' + path)
    for path in recovery.ANCHOR_FILES:
        if blobs[path][0] != local_hashes.get(path):
            raise OtaError('anchor changed, needs cable')

    ignore = [_norm(i) for i in ignore]

    def owned(path):
        return not (path in recovery.ANCHOR_FILES or _local_only(path)
                    or _ignored(path, ignore))

    planned = []
    new_managed = []
    for path, (sha, size) in blobs.items():
        if not owned(path):
            continue
        new_managed.append(path)
        if sha != local_hashes.get(path):
            planned.append((path, sha, size))
    deletes = [p for p in managed if p not in blobs and owned(p)]
    for py in shadowed_py(files):
        if py in local_hashes and owned(py) and owned(py[:-3] + '.mpy') and py not in deletes:
            deletes.append(py)
    return {'files': planned, 'deletes': deletes, 'managed': new_managed}
