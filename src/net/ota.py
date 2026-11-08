"""PortalBox OTA: update the box from GitHub, plus config and backup helpers.

run_update() picks the target version (net.ota_target), downloads and
checks it (net.ota_stage), and installs it on trial (recovery.install).
The caller connects WiFi first.

backup()/restore() are based on `ugit` by turfptax for the OpenMuscle
project: https://github.com/turfptax/ugit
"""

import os
import time

import ujson as json
import ubinascii as binascii
import uhashlib as hashlib
import machine

import recovery
from net import https, ota_stage, ota_target
from net.https import HttpError
from net.ota_target import OtaError

_API = 'https://api.github.com'
# Files come from the API by blob sha, as raw bytes: api.github.com's ECC
# certificate chain needs far less RAM to check than raw.githubusercontent.com's
# RSA-4096 one, which failed on boxes without PSRAM.
_RAW_MEDIA = 'application/vnd.github.raw'
_CONFIG_PATH = '/config/ota_config.json'
_OTA_STATE_PATH = '/config/ota_state.json'
_OTA_INTERVAL_SECONDS = 24 * 60 * 60   # wait after a good check
_RETRY_AFTER_FAIL_S = 60 * 60          # wait after a failed one
IDLE_BEFORE_UPDATE_MS = 600000         # idle this long before an idle check
CHECK_EVERY_S = 60                     # how often the app asks due()
# 2023-11-14 on the board's own epoch (2000 on this port, not 1970).
_MIN_VALID_EPOCH = time.mktime((2023, 11, 14, 0, 0, 0, 0, 0))
_PROTECTED_PATHS = [
    # backup()/restore() leave device-local and runtime paths alone
    _CONFIG_PATH,
    '/config/wifi_config.json',
    '/ota.backup',
    '/ota_log.txt',
    '/lib',
    '/sd',
    '/config',
    '/ca-cert.pem',
]


def _git_blob_hash(data):
    """Return the Git blob SHA1 for the given bytes or text."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    header = ('blob %d\0' % len(data)).encode('utf-8')
    sha = hashlib.sha1(header + data)
    return binascii.hexlify(sha.digest()).decode('utf-8')


def _local_file_hash(filepath):
    """Return the Git-compatible blob hash for a local file."""
    try:
        with open(filepath, 'rb') as file_handle:
            data = file_handle.read()
    except Exception:
        data = b''
    return _git_blob_hash(data)


def _is_directory(path):
    """Return True when `path` is a directory on the device."""
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except Exception:
        return False


def _build_internal_tree(path='/'):
    """Recursively hash the local filesystem into a path-to-SHA map."""
    tree = {}
    os.chdir(path)

    for item in os.listdir():
        full = path + item if path.endswith('/') else path + '/' + item
        if _is_directory(full):
            if os.listdir(full):
                tree.update(_build_internal_tree(full))
        else:
            try:
                tree[full] = _local_file_hash(full)
            except OSError:
                pass

    os.chdir('/')
    return tree


def _get_storage_info():
    """Return total, free, and used storage in bytes."""
    stat = os.statvfs('/')
    block_size = stat[0]
    total_blocks = stat[2]
    free_blocks = stat[3]
    total = block_size * total_blocks
    free = block_size * free_blocks
    used = total - free
    return total, free, used


def _file_size(filepath):
    """Return the file size in bytes, or zero if unavailable."""
    try:
        return os.stat(filepath)[6]
    except Exception:
        return 0


def _is_ignored(path, ignore):
    """Return True if `path` matches any ignore entry."""
    for entry in ignore:
        normalized = entry.rstrip('/')
        if path == normalized or path.startswith(normalized + '/'):
            return True
    return False


def _fmt_size(size_bytes):
    """Format bytes as a compact human-readable string."""
    if size_bytes < 1024:
        return '%d B' % size_bytes
    if size_bytes < 1024 * 1024:
        return '%d KB' % (size_bytes // 1024)
    return '%d.%d MB' % (
        size_bytes // (1024 * 1024),
        (size_bytes % (1024 * 1024)) * 10 // (1024 * 1024),
    )


def storage_info():
    """Print and return the current filesystem storage statistics."""
    total, free, used = _get_storage_info()
    print(
        'Storage: %s total, %s used, %s free (%d%% free)' % (
            _fmt_size(total),
            _fmt_size(used),
            _fmt_size(free),
            (free * 100) // total if total else 0,
        )
    )
    return {'total': total, 'free': free, 'used': used}


def _ensure_ignore(ignore):
    """Normalize ignore paths and add OTA-protected paths."""
    if ignore is None:
        ignore = []

    for path in _PROTECTED_PATHS:
        if path not in ignore:
            ignore.append(path)
    return ignore


def _ensure_config_dir():
    """Create the `/config` directory if it does not exist."""
    try:
        os.mkdir('/config')
    except Exception:
        pass


def _load_config():
    """Load the OTA config JSON from the device."""
    try:
        with open(_CONFIG_PATH, 'r') as file_handle:
            return json.loads(file_handle.read())
    except Exception:
        return {}


def load_config(path=_CONFIG_PATH):
    """Return the OTA config with defaults. OTA is off unless `enabled` is true."""
    try:
        with open(path, 'r') as file_handle:
            stored = json.loads(file_handle.read())
        if not isinstance(stored, dict):
            stored = {}
    except Exception:
        stored = {}
    return {
        'user': stored.get('user', ''),
        'repository': stored.get('repository', ''),
        'branch': stored.get('branch', 'main'),
        'token': stored.get('token', ''),
        'ignore': stored.get('ignore', []),
        'enabled': stored.get('enabled') is True,
        'mode': stored.get('mode', 'release'),
    }


def _load_ota_state(state_path=_OTA_STATE_PATH):
    """Load the stored OTA state from disk."""
    try:
        with open(state_path, 'r') as file_handle:
            state = json.loads(file_handle.read())
            if isinstance(state, dict):
                return state
    except Exception:
        pass
    return {}


def _save_ota_state(state, state_path=_OTA_STATE_PATH):
    """Persist OTA state to disk."""
    try:
        _ensure_config_dir()
        with open(state_path, 'w') as file_handle:
            file_handle.write(json.dumps(state))
    except Exception as error:
        print('[OTA] Warning: failed to save OTA state: %s' % error)


def _since(now, state, key):
    last = state.get(key, 0)
    return now - last if isinstance(last, int) else _OTA_INTERVAL_SECONDS


def due(now, state, at_boot, idle_ms, clock_ok):
    """True when it is time to check for an update.

    Never with the clock unset; a day after a good check, an hour after
    a failed one; then at boot, or once the box has been idle 10 min.
    """
    if not clock_ok:
        print('[OTA] clock not set')
        return False
    if _since(now, state, 'last_success_epoch') < _OTA_INTERVAL_SECONDS:
        return False
    if _since(now, state, 'last_try_epoch') < _RETRY_AFTER_FAIL_S:
        return False
    return at_boot or idle_ms >= IDLE_BEFORE_UPDATE_MS


def run_if_due(cfg, at_boot, idle_ms=0, abort=None, now=None, run=None,
               state_path=None):
    """run_update() when OTA is on, not in dev mode, and due().

    Returns 'off', 'dev mode', 'not due', or run_update()'s result. The
    try time is saved before the run (an install resets the box inside
    it); the success time after any result but 'failed: ...'.
    """
    if cfg.get('enabled') is not True:
        return 'off'
    if recovery.dev_mode():
        return 'dev mode'
    now = time.time() if now is None else now
    state_path = state_path or _OTA_STATE_PATH
    state = _load_ota_state(state_path)
    if not due(now, state, at_boot, idle_ms, now >= _MIN_VALID_EPOCH):
        return 'not due'
    state['last_try_epoch'] = now
    _save_ota_state(state, state_path)
    result = (run or run_update)(cfg, at_boot, abort=abort)
    if not result.startswith('failed'):
        state['last_success_epoch'] = now
        _save_ota_state(state, state_path)
    return result


def _reason(e):
    """Short text for a failure, for the result and the log."""
    if isinstance(e, HttpError):
        msg = e.args[1] if len(e.args) > 1 else ''
        return ('http %d %s' % (e.status, msg)).rstrip()
    if isinstance(e, OtaError):
        return str(e)
    return ('%s %s' % (type(e).__name__, e)).rstrip()


def _local_hashes(tree):
    """Git blob sha of each box file the target tree also has."""
    local = {}
    for e in tree:
        if e['type'] == 'blob':
            sha = ota_stage.file_sha(recovery.LIVE_ROOT + '/' + e['path'])
            if sha:
                local[e['path']] = sha
            recovery.feed()
    return local


def _update(cfg, abort, get_json, fetch):
    if cfg.get('enabled') is not True:
        return 'off'
    state = recovery.load_state()
    if state['trial']:
        return 'failed: trial active'   # install() would refuse after the download
    auth = {'Authorization': 'Bearer ' + cfg['token']} if cfg.get('token') else {}
    session = https.Session()   # one kept connection: one TLS handshake, not one per file

    def api_get(path):
        return session.get_json(_API + path, auth)   # parsed as it arrives

    blobs = _API + '/repos/%s/%s/git/blobs/' % (cfg['user'], cfg['repository'])
    blob_headers = dict(auth)
    blob_headers['Accept'] = _RAW_MEDIA

    def blob_get(path, sha, sink):
        return session.get(blobs + sha, blob_headers, sink=sink)

    try:
        return _apply(cfg, abort, state, get_json or api_get, fetch or blob_get)
    finally:
        session.close()


def _apply(cfg, abort, state, get_json, fetch):
    target = ota_target.resolve_target(cfg, get_json)
    commit = target['commit']
    if commit in state['bad']:
        return 'bad version'
    if commit == state['version']:
        return 'up to date'
    tree = target['tree']
    plan = ota_target.build_plan(tree, _local_hashes(tree), state['managed'],
                                 cfg.get('ignore', []))
    if not plan['files'] and not plan['deletes']:
        # Same files (a docs-only commit, or a box set up by cable):
        # adopt the version without a trial or a reset.
        state['version'] = commit
        state['managed'] = plan['managed']
        recovery.save_state(state)
        return 'up to date'
    # Blob shas from the tree pin every file to the same version.
    ota_stage.stage(plan, tree, fetch, abort)
    # stage() checks only before each file: a card tapped during the
    # last download must stop the install and its reset too.
    if abort and abort():
        raise OtaError('aborted')
    recovery.install(commit, [p for p, _, _ in plan['files']], plan['deletes'],
                     plan['managed'])
    return 'installed ' + commit


def run_update(cfg, at_boot, abort=None, get_json=None, fetch=None, reset=machine.reset):
    """Update the box to the version `cfg` asks for. Never raises.

    Returns 'off', 'up to date', 'bad version', 'installed <commit>' or
    'failed: <why>', and logs it with 'boot' or 'idle'. abort() true
    between files, or after the last one, stops it before the install. After an install it calls reset(),
    so the box boots the new version on trial. It also resets when a
    failed install left its journal (its rollback failed too), so on_boot
    finishes the rollback. get_json and fetch default to one
    https.Session (JSON with get_json, each file's blob with get), with
    the token from cfg; tests pass fakes.
    """
    journal_before = recovery._exists(recovery.JOURNAL_PATH)
    try:
        result = _update(cfg, abort, get_json, fetch)
    except Exception as e:
        result = 'failed: ' + _reason(e)
        try:
            recovery._rm_tree(recovery.STAGE_DIR)
        except Exception:
            pass
    left_journal = not journal_before and recovery._exists(recovery.JOURNAL_PATH)
    recovery.log('ota', '%s %s' % ('boot' if at_boot else 'idle', result))
    if result.startswith('installed ') or left_journal:
        reset()
    return result


def create_config(user='', repository='', branch='main', token='', ignore=None,
                  enabled=False, mode='release'):
    """Write the OTA config file to `/config/ota_config.json`.

    mode 'release' follows the latest GitHub release; 'branch' follows
    the tip of `branch`. OTA stays off until `enabled` is True.
    """
    if ignore is None:
        ignore = []

    _ensure_config_dir()
    config = {
        'user': user,
        'repository': repository,
        'branch': branch,
        'token': token,
        'ignore': ignore,
        'enabled': enabled,
        'mode': mode,
    }

    with open(_CONFIG_PATH, 'w') as file_handle:
        file_handle.write(json.dumps(config))

    field_names = [key for key in config if config[key]]
    print('Config saved to %s' % _CONFIG_PATH)
    print('Fields stored: %s' % ', '.join(field_names))
    print('WARNING: OTA config can contain repository credentials. Keep it local.')


def show_config():
    """Print the current OTA configuration with token masking."""
    config = _load_config()
    if not config:
        print('No config found. Run ota.create_config() to set up.')
        return

    for key, value in config.items():
        if key == 'token' and value:
            print('  %s: %s' % (key, value[:3] + '*' * max(len(value) - 3, 0)))
        else:
            print('  %s: %s' % (key, value))


def backup(ignore=None):
    """Create a text backup of local files protected by the ignore list."""
    ignore = _ensure_ignore(ignore)
    local_tree = _build_internal_tree()

    backup_size = 0
    file_count = 0
    for path in local_tree:
        if not _is_ignored(path, ignore):
            backup_size += _file_size(path) + len(path) + 80
            file_count += 1

    _, free, _ = _get_storage_info()
    if backup_size > free:
        print('Not enough space for backup.')
        print('  Need: %s, Free: %s' % (_fmt_size(backup_size), _fmt_size(free)))
        return -1

    with open('/ota.backup', 'w') as file_handle:
        file_handle.write('ota backup v1\n')
        for path, sha in local_tree.items():
            if _is_ignored(path, ignore):
                continue
            file_handle.write('FILE:%s SHA:%s\n' % (path, sha))
            try:
                with open(path, 'rb') as data_file:
                    content = data_file.read()
                try:
                    file_handle.write('---\n')
                    file_handle.write(content.decode('utf-8'))
                    file_handle.write('\n---\n')
                except Exception:
                    file_handle.write('---BINARY:%d---\n' % len(content))
            except Exception:
                file_handle.write('---ERROR---\n')

    print('Backup saved to /ota.backup (%d files, ~%s)' % (file_count, _fmt_size(backup_size)))
    return file_count


def restore(ignore=None):
    """Restore files from `/ota.backup` created by `backup()`."""
    ignore = _ensure_ignore(ignore)

    try:
        file_handle = open('/ota.backup', 'r')
    except Exception:
        print('No backup file found at /ota.backup')
        return False

    header = file_handle.readline().strip()
    if not header.startswith('ota backup'):
        print('Invalid backup file format.')
        file_handle.close()
        return False

    restored = 0
    skipped = 0
    current_path = None
    current_content = None
    in_content = False

    for line in file_handle:
        line_stripped = line.rstrip('\n')

        if line_stripped.startswith('FILE:'):
            if current_path and current_content is not None:
                if not _is_ignored(current_path, ignore):
                    _restore_file(current_path, current_content)
                    restored += 1
                else:
                    skipped += 1
            current_path = line_stripped.split(' SHA:')[0][5:]
            current_content = None
            in_content = False
        elif line_stripped == '---' and not in_content:
            in_content = True
            current_content = ''
        elif line_stripped == '---' and in_content:
            in_content = False
        elif line_stripped.startswith('---BINARY:') or line_stripped == '---ERROR---':
            in_content = False
            current_content = None
            print('  skip (binary/error): %s' % current_path)
        elif in_content:
            if current_content:
                current_content += '\n' + line_stripped
            else:
                current_content = line_stripped

    if current_path and current_content is not None:
        if not _is_ignored(current_path, ignore):
            _restore_file(current_path, current_content)
            restored += 1
        else:
            skipped += 1

    file_handle.close()
    print('Restore complete: %d files restored, %d skipped' % (restored, skipped))
    return True


def _restore_file(filepath, content):
    """Restore a single file and create parent directories as needed."""
    parts = filepath.split('/')
    for index in range(1, len(parts)):
        directory = '/'.join(parts[:index])
        if directory:
            try:
                os.mkdir(directory)
            except Exception:
                pass

    with open(filepath, 'w') as file_handle:
        file_handle.write(content)
    print('  restored:', filepath)


