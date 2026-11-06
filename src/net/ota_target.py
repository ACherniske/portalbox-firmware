"""Pick the version to update to, and refuse trees that are unsafe to install.

resolve_target() asks GitHub which commit to run and lists its src/ folder.
build_plan() compares that list with the box and says what to change.
Nothing is downloaded or installed here.
"""
import recovery
from net.https import HttpError


class OtaError(Exception):
    pass


# The box must be able to boot and reach GitHub again after any update.
# The old code on the box checks the new tree, so renaming one of these
# later means boxes on the old list need a cable.
REQUIRED = (
    'boot.py', 'main.py', 'recovery.py',
    'core/system.py', 'core/main.py',
    'net/ota.py', 'net/ota_target.py', 'net/https.py', 'net/wifi.py',
    'certs/github_roots.pem', 'manifest.json',
)

_MAX_TAG_DEPTH = 3


def _commit_of(obj, get_json, repo):
    """Follow annotated tag objects down to the commit sha."""
    for _ in range(_MAX_TAG_DEPTH):
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            raise OtaError('ref is a %s' % obj['type'])
        obj = get_json(repo + '/git/tags/' + obj['sha'])['object']
    raise OtaError('tag chain too long')


def resolve_target(cfg, get_json):
    """Return {'commit', 'label', 'tree'} for the version `cfg` asks for.

    get_json(path) returns the parsed reply for an API path such as
    '/repos/u/r/releases/latest' and raises HttpError on a bad status.
    tree holds the blobs and folders of src/, as device paths.
    """
    repo = '/repos/%s/%s' % (cfg['user'], cfg['repository'])
    mode = cfg.get('mode', 'release')
    if mode == 'release':
        try:
            label = get_json(repo + '/releases/latest')['tag_name']
        except HttpError as e:
            if e.status == 404:
                raise OtaError('no release')
            raise
        obj = get_json(repo + '/git/ref/tags/' + label)['object']
    elif mode == 'branch':
        label = cfg['branch']
        obj = get_json(repo + '/git/ref/heads/' + label)['object']
    else:
        raise OtaError('bad mode %s' % mode)
    commit = _commit_of(obj, get_json, repo)

    # Only src/ is fetched: the whole repo's list is too big for the heap.
    src = None
    for e in get_json(repo + '/git/trees/' + commit)['tree']:
        if e['path'] == 'src' and e['type'] == 'tree':
            src = e['sha']
    if src is None:
        raise OtaError('no src')
    reply = get_json(repo + '/git/trees/' + src + '?recursive=1')
    if reply.get('truncated'):
        raise OtaError('tree truncated')
    tree = [{'path': e['path'], 'sha': e['sha'], 'size': e.get('size', 0),
             'type': e['type']} for e in reply['tree']]
    return {'commit': commit, 'label': label, 'tree': tree}


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


def build_plan(tree, local_hashes, managed, ignore):
    """Say what an update to `tree` changes on this box.

    tree: entries from resolve_target. local_hashes: device path -> git blob
    sha of files on the box. managed: files the last update installed.
    ignore: paths OTA leaves alone. Returns {'files': [(path, sha, size)]
    changed or new, 'deletes': [managed paths gone from the target],
    'managed': [every path this update owns]}. Raises OtaError for a tree
    that is unsafe to install.
    """
    blobs = {}
    for e in tree:
        if e['type'] == 'blob':
            blobs[e['path']] = e
    for path in REQUIRED:
        if path not in blobs:
            raise OtaError('missing ' + path)
    for path in recovery.ANCHOR_FILES:
        if blobs[path]['sha'] != local_hashes.get(path):
            raise OtaError('anchor changed, needs cable')

    ignore = [_norm(i) for i in ignore]

    def owned(path):
        return not (path in recovery.ANCHOR_FILES or _local_only(path)
                    or _ignored(path, ignore))

    files = []
    new_managed = []
    for path, e in blobs.items():
        if not owned(path):
            continue
        new_managed.append(path)
        if e['sha'] != local_hashes.get(path):
            files.append((path, e['sha'], e['size']))
    deletes = [p for p in managed if p not in blobs and owned(p)]
    return {'files': files, 'deletes': deletes, 'managed': new_managed}
