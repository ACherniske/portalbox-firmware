"""PortalBox OTA update helpers.

Based on `ugit` by turfptax for the OpenMuscle project:
https://github.com/turfptax/ugit

This PortalBox adaptation provides GitHub-based firmware updates by syncing
all repository files under `/src` (minus ignored/protected paths).
Caller is responsible for establishing WiFi connection before initiating updates.
"""

import gc
import os
import time

import ujson as json
import ubinascii as binascii
import uhashlib as hashlib
import urequests
import machine

_GITHUB_API = 'https://api.github.com/repos'
_GITHUB_RAW = 'https://raw.githubusercontent.com'
_USER_AGENT = 'portalbox-ota'
_CONFIG_PATH = '/config/ota_config.json'
_OTA_STATE_PATH = '/config/ota_state.json'
_OTA_INTERVAL_SECONDS = 24 * 60 * 60
# 2023-11-14 on the board's own epoch (2000 on this port, not 1970).
_MIN_VALID_EPOCH = time.mktime((2023, 11, 14, 0, 0, 0, 0, 0))
_PROTECTED_PATHS = [
    # Protect device-local and runtime paths so OTA cannot wipe secrets
    _CONFIG_PATH,
    '/config/wifi_config.json',
    '/ota.backup',
    '/ota_log.txt',
    '/lib',
    '/sd',
    '/config',
    '/ca-cert.pem',
]


def _headers(token=''):
    """Build request headers for GitHub API calls."""
    headers = {'User-Agent': _USER_AGENT}
    if token:
        headers['authorization'] = 'bearer %s' % token
    return headers


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


def _is_usb_cdc():
    """Detect boards that should avoid automatic resets."""
    try:
        machine_str = os.uname().machine.upper()
        for chip in ('ESP32S3', 'ESP32S2', 'ESP32C6', 'ESP32C3', 'ESP32H2'):
            if chip in machine_str:
                return True
    except Exception:
        pass
    return False


def _is_ignored(path, ignore):
    """Return True if `path` matches any ignore entry."""
    for entry in ignore:
        normalized = entry.rstrip('/')
        if path == normalized or path.startswith(normalized + '/'):
            return True
    return False


def _index_git_tree(git_tree):
    """Index the Git tree by absolute path for quick lookups."""
    indexed = {}
    for item in git_tree['tree']:
        path = item['path']
        if not path.startswith('/'):
            path = '/' + path
        indexed[path] = item
    return indexed


def _src_paths_from_git_index(git_index):
    """Return sorted absolute paths for blob files under `/src` in the repo."""
    src_paths = []
    for path, item in git_index.items():
        if item.get('type') == 'blob' and path.startswith('/src/'):
            src_paths.append(path)
    src_paths.sort()
    return src_paths


def _device_path_from_repo_path(repo_path):
    """Strip /src/ prefix to map repo paths to device deployment paths."""
    if repo_path.startswith('/src/'):
        return '/' + repo_path[5:]
    return repo_path


def _repo_download_size(manifest_paths, git_index, local_tree, ignore):
    """Estimate bytes that must be downloaded for the manifest files."""
    download = 0
    for path in manifest_paths:
        if _is_ignored(path, ignore):
            continue
        item = git_index.get(path)
        if not item or item['type'] != 'blob':
            continue
        git_sha = item.get('sha', '')
        device_path = _device_path_from_repo_path(path)
        local_sha = local_tree.get(device_path, '')
        if not (git_sha and local_sha and git_sha == local_sha):
            download += item.get('size', 0)
    return download


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


def _require_connected(isconnected):
    """Require caller-managed WiFi connectivity before OTA operations."""
    if not isconnected:
        raise RuntimeError('WiFi must be connected first. Call with isconnected=True.')


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


def _resolve_config(user=None, repository=None, branch=None, token=None, ignore=None):
    """Merge explicit arguments with stored OTA config values."""
    config = _load_config()
    return {
        'user': user or config.get('user', ''),
        'repository': repository or config.get('repository', ''),
        'branch': branch or config.get('branch', 'main'),
        'token': token if token is not None else config.get('token', ''),
        'ignore': ignore if ignore is not None else config.get('ignore', []),
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


def _should_run_daily_ota(now_epoch, state):
    """Return True when the stored OTA timestamp is stale or unavailable."""
    if now_epoch < _MIN_VALID_EPOCH:
        print('[OTA] Clock not synchronized yet; running OTA this boot.')
        return True

    last_run = state.get('last_success_epoch', 0)
    if not isinstance(last_run, int):
        return True
    return (now_epoch - last_run) >= _OTA_INTERVAL_SECONDS


def run_daily_update_if_due(user=None, repository=None, branch=None, token=None,
                            ignore=None, isconnected=False, reset_after=False,
                            state_path=_OTA_STATE_PATH):
    """Run OTA once per day, using a persisted timestamp gate."""
    config = _resolve_config(user, repository, branch, token, ignore)
    ignore = _ensure_ignore(config['ignore'])

    if not config['user'] or not config['repository']:
        print('OTA config incomplete. Run ota.create_config() first.')
        return None

    if not isconnected:
        print('WiFi must be connected before OTA can run.')
        return None

    state = _load_ota_state(state_path)
    now_epoch = int(time.time())

    if not _should_run_daily_ota(now_epoch, state):
        next_run = state.get('last_success_epoch', 0) + _OTA_INTERVAL_SECONDS
        print('[OTA] Not due yet. Next run after epoch: %d' % next_run)
        return None

    print('[OTA] Running daily update check...')
    try:
        log = pull_all(
            user=config['user'], repository=config['repository'],
            branch=config['branch'], token=config['token'],
            ignore=ignore, isconnected=True, reset_after=reset_after,
        )
        state['last_success_epoch'] = int(time.time())
        _save_ota_state(state, state_path)
        print('[OTA] Update completed. Timestamp saved.')
        return log
    except Exception as error:
        print('[OTA] Daily update failed: %s' % error)
        return None


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


def pull_git_tree(user, repository, branch='main', token=''):
    """Fetch the recursive Git tree for the requested repository branch."""
    if urequests is None:
        raise RuntimeError('urequests is unavailable')

    url = '%s/%s/%s/git/trees/%s?recursive=1' % (_GITHUB_API, user, repository, branch)
    response = urequests.get(url, headers=_headers(token))
    data = json.loads(response.content.decode('utf-8'))
    response.close()

    if 'tree' not in data:
        raise Exception('Branch "%s" not found for %s/%s' % (branch, user, repository))
    return data


def pull(filepath, raw_url, token=''):
    """Download a file from GitHub and write it to the device."""
    if urequests is None:
        raise RuntimeError('urequests is unavailable')

    response = urequests.get(raw_url, headers=_headers(token))
    try:
        parts = filepath.split('/')
        for index in range(1, len(parts)):
            directory = '/'.join(parts[:index])
            if directory:
                try:
                    os.mkdir(directory)
                except Exception:
                    pass

        # Stream response body in small chunks to avoid large memory allocations
        with open(filepath, 'wb') as file_handle:
            reader = getattr(response, 'raw', None)
            if reader is not None:
                while True:
                    chunk = reader.read(1024)
                    if not chunk:
                        break
                    file_handle.write(chunk)
                    gc.collect()
            else:
                # Fallback: some urequests implementations only expose .content
                data = response.content
                file_handle.write(data)
                del data
                gc.collect()
    finally:
        try:
            response.close()
        except Exception:
            pass

    return None


def pull_all(user=None, repository=None, branch=None, token=None,
             ssid=None, password=None, ignore=None,
             isconnected=False, reset_after=False):
    """Sync the device to all repository blob files under `/src`."""
    config = _resolve_config(user, repository, branch, token, ignore)
    ignore = _ensure_ignore(config['ignore'])

    if not config['user'] or not config['repository']:
        raise ValueError('user and repository required. Pass them or run ota.create_config()')

    _require_connected(isconnected)

    os.chdir('/')

    print('Fetching repository tree...')
    git_tree = pull_git_tree(
        config['user'],
        config['repository'],
        config['branch'],
        config['token'],
    )
    git_index = _index_git_tree(git_tree)
    manifest_paths = _src_paths_from_git_index(git_index)
    print('Targeting %d files under /src' % len(manifest_paths))

    print('Scanning local files...')
    local_tree = _build_internal_tree()

    device_manifest_paths = [_device_path_from_repo_path(p) for p in manifest_paths]
    device_manifest_set = set(device_manifest_paths)

    log = []
    updated = 0
    skipped = 0
    deleted = 0

    gc.collect()

    for path in manifest_paths:
        if _is_ignored(path, ignore):
            skipped += 1
            continue

        item = git_index.get(path)
        if not item:
            log.append(path + ' missing from git tree')
            continue

        if item['type'] != 'blob':
            continue

        git_sha = item.get('sha', '')
        device_path = _device_path_from_repo_path(path)
        local_sha = local_tree.get(device_path, '')
        if git_sha and local_sha and git_sha == local_sha:
            skipped += 1
            log.append(path + ' unchanged')
            continue

        try:
            raw_base = '%s/%s/%s/%s/' % (
                _GITHUB_RAW,
                config['user'],
                config['repository'],
                config['branch'],
            )
            device_path = _device_path_from_repo_path(path)
            pull(device_path, raw_base + item['path'], config['token'])
            updated += 1
            log.append(device_path + ' updated')
            print('  updated:', device_path)
            gc.collect()
        except Exception as error:
            log.append(path + ' FAILED: ' + str(error))
            print('  FAILED:', path, error)

    for local_path in local_tree:
        if local_path not in device_manifest_set and not _is_ignored(local_path, ignore):
            try:
                os.remove(local_path)
                deleted += 1
                log.append(local_path + ' deleted')
                print('  deleted:', local_path)
            except Exception:
                log.append(local_path + ' delete failed')

    summary = 'ota: %d updated, %d skipped, %d deleted' % (updated, skipped, deleted)
    print(summary)
    log.insert(0, summary)

    try:
        with open('/ota_log.txt', 'w') as file_handle:
            file_handle.write('\n'.join(log))
    except Exception:
        pass

    if reset_after:
        if _is_usb_cdc():
            print('WARNING: USB-CDC board detected (%s).' % os.uname().machine)
            print('machine.reset() skipped — reset manually or power-cycle.')
            print('Auto-reset can brick USB-CDC boards if boot.py/main.py crashes.')
        else:
            print('Resetting device in 5 seconds...')
            time.sleep(5)
            if machine is not None:
                machine.reset()

    return log


def check_for_updates(user=None, repository=None, branch=None, token=None,
                      ignore=None, isconnected=False,
                      ssid=None, password=None):
    """Return new, changed, and deleted files for repo blobs under `/src`."""
    config = _resolve_config(user, repository, branch, token, ignore)
    ignore = _ensure_ignore(config['ignore'])

    if not config['user'] or not config['repository']:
        raise ValueError('user and repository required. Pass them or run ota.create_config()')

    _require_connected(isconnected)

    os.chdir('/')
    git_tree = pull_git_tree(
        config['user'],
        config['repository'],
        config['branch'],
        config['token'],
    )
    git_index = _index_git_tree(git_tree)
    manifest_paths = _src_paths_from_git_index(git_index)
    local_tree = _build_internal_tree()
    device_manifest_paths = [_device_path_from_repo_path(p) for p in manifest_paths]
    device_manifest_set = set(device_manifest_paths)

    new = []
    changed = []
    deleted = []

    for path in manifest_paths:
        if _is_ignored(path, ignore):
            continue

        item = git_index.get(path)
        if not item or item['type'] != 'blob':
            continue

        device_path = _device_path_from_repo_path(path)
        local_sha = local_tree.get(device_path, '')
        if not local_sha:
            new.append(path)
        elif item.get('sha', '') != local_sha:
            changed.append(path)

    for local_path in local_tree:
        if local_path not in device_manifest_set and not _is_ignored(local_path, ignore):
            deleted.append(local_path)

    return {'new': new, 'changed': changed, 'deleted': deleted}


def update(branch=None, token=None):
    """Replace this module file with the latest version from GitHub."""
    if token is None:
        token = _load_config().get('token', '')
    if branch is None:
        branch = 'main'

    try:
        dest = __file__
    except Exception:
        dest = '/ota.py'

    raw_url = '%s/ACherniske/portalbox-firmware/%s/src/net/ota.py' % (
        _GITHUB_RAW,
        branch,
    )
    pull(dest, raw_url, token)
    print('ota updated at %s from branch %s. Reset device to use new version.' % (dest, branch))


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


def safe_pull_all(user=None, repository=None, branch=None, token=None,
                  ssid=None, password=None, ignore=None,
                  isconnected=False, reset_after=False):
    """Back up first, then run `pull_all()` if storage is sufficient."""
    config = _resolve_config(user, repository, branch, token, ignore)
    ignore = _ensure_ignore(config['ignore'])

    if not config['user'] or not config['repository']:
        raise ValueError('user and repository required. Pass them or run ota.create_config()')

    _require_connected(isconnected)

    os.chdir('/')

    print('Pre-flight check...')
    total, free, used = _get_storage_info()
    print('  Storage: %s free of %s' % (_fmt_size(free), _fmt_size(total)))

    git_tree = pull_git_tree(
        config['user'],
        config['repository'],
        config['branch'],
        config['token'],
    )
    git_index = _index_git_tree(git_tree)
    manifest_paths = _src_paths_from_git_index(git_index)
    local_tree = _build_internal_tree()

    download_size = _repo_download_size(manifest_paths, git_index, local_tree, ignore)

    backup_size = 0
    for path in local_tree:
        if not _is_ignored(path, ignore):
            backup_size += _file_size(path) + len(path) + 80

    needed = backup_size + download_size + 4096
    print('  Backup estimate: %s' % _fmt_size(backup_size))
    print('  Download estimate: %s' % _fmt_size(download_size))
    print('  Total needed: %s' % _fmt_size(needed))

    if needed > free:
        shortfall = needed - free
        print('\nNot enough space! Short by %s.' % _fmt_size(shortfall))
        print('Free up space or use pull_all() without backup.')
        return None

    print('\nCreating backup...')
    result = backup(ignore)
    if result == -1:
        print('Backup failed. Aborting safe update.')
        return None

    print('\nStarting update...')
    try:
        log = pull_all(
            user=config['user'], repository=config['repository'],
            branch=config['branch'], token=config['token'],
            ignore=ignore, isconnected=True, reset_after=False,
        )
    except Exception as error:
        print('\nUpdate FAILED: %s' % str(error))
        print('Your backup is at /ota.backup')
        print('Run ota.restore() to roll back.')
        return None

    print('\nUpdate complete with backup at /ota.backup')
    if reset_after:
        if _is_usb_cdc():
            print('WARNING: USB-CDC board detected (%s).' % os.uname().machine)
            print('machine.reset() skipped — reset manually or power-cycle.')
            print('Auto-reset can brick USB-CDC boards if boot.py/main.py crashes.')
        else:
            print('Resetting device in 5 seconds...')
            time.sleep(5)
            if machine is not None:
                machine.reset()

    return log
