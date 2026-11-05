"""Recovery anchor: saved state, boot counting and the on-box event log.

OTA never replaces this file (see ANCHOR_FILES). It runs before the app on
every normal boot, so it must stay small and must not import app code.
"""
import os
import time
import json
import machine

# Paths are module globals so tests can move them somewhere harmless.
STATE_PATH = '/ota/state.json'
JOURNAL_PATH = '/ota/journal.json'
PREV_DIR = '/ota/prev'
STAGE_DIR = '/ota/stage'
LOG_PATH = '/log/events.log'
DEV_FLAG_PATH = '/config/dev_mode'
LIVE_ROOT = ''

MAX_BOOTS = 3
LOG_MAX = 16384
WDT_MS = 30000
ANCHOR_FILES = ('boot.py', 'main.py', 'recovery.py')

_wdt = None


def _defaults():
    return {
        'v': 1,
        'version': None,
        'prev_version': None,
        'trial': False,
        'trial_boots': 0,
        'trial_wifi_fails': 0,
        'crash_count': 0,
        'bad': [],
        'managed': [],
    }


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _make_parent(path):
    built = ''
    for part in path.strip('/').split('/')[:-1]:
        built += '/' + part
        try:
            os.mkdir(built)
        except OSError:
            pass


def log(tag, msg):
    """Append one line to the event log. Never raises.

    Keeps at most LOG_MAX bytes per file: the full file moves to LOG_PATH.1
    (replacing the older one) before a line that would pass the cap.
    """
    try:
        line = '%d %s %s\n' % (time.time(), tag, msg)
        _make_parent(LOG_PATH)
        try:
            size = os.stat(LOG_PATH)[6]
        except OSError:
            size = 0
        if size and size + len(line.encode()) > LOG_MAX:
            old = LOG_PATH + '.1'
            try:
                os.remove(old)
            except OSError:
                pass
            os.rename(LOG_PATH, old)
        with open(LOG_PATH, 'a') as f:
            f.write(line)
    except Exception:
        pass


def dev_mode():
    return _exists(DEV_FLAG_PATH)


def _valid(saved, defaults):
    if not isinstance(saved, dict):
        return False
    for key, default in defaults.items():
        if key in saved and default is not None and type(saved[key]) is not type(default):
            return False
    return True


def load_state():
    """The saved state, with defaults for missing keys.

    A missing file gives the defaults. A file that is not valid JSON, or has a
    wrong-typed field, also gives the defaults and is logged, so a bad file
    can never stop the box from booting.
    """
    state = _defaults()
    try:
        with open(STATE_PATH, 'r') as f:
            text = f.read()
    except OSError:
        return state
    try:
        saved = json.loads(text)
    except Exception:
        saved = None
    if not _valid(saved, state):
        log('state', 'state reset: bad %s' % STATE_PATH)
        return state
    state.update(saved)
    return state


def save_state(state):
    """Write to a temp file, then rename over, so a power cut keeps the old file."""
    _make_parent(STATE_PATH)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.rename(tmp, STATE_PATH)


def on_boot(power_on):
    """Decide whether this boot runs the app. Returns ('run', '') or ('stuck', reason).

    Each boot adds one to crash_count; mark_healthy() clears it. A power-on
    reset clears it first, so a box someone has unplugged starts clean.
    Dev mode never counts.
    """
    if dev_mode():
        return ('run', '')
    state = load_state()
    if power_on:
        state['crash_count'] = 0
    if state['crash_count'] >= MAX_BOOTS:
        log('boot', 'stuck: crash loop (%d unhealthy boots)' % state['crash_count'])
        return ('stuck', 'crash loop')
    state['crash_count'] += 1
    save_state(state)
    return ('run', '')


def mark_healthy():
    state = load_state()
    state['crash_count'] = 0
    save_state(state)


def note_wifi_failure():
    """A WiFi failure resets the box but is not a crash: undo this boot's count."""
    state = load_state()
    state['crash_count'] = max(0, state['crash_count'] - 1)
    save_state(state)


def arm_watchdog(timeout_ms=WDT_MS):
    global _wdt
    if _wdt is None:
        _wdt = machine.WDT(timeout=timeout_ms)


def feed():
    if _wdt is not None:
        _wdt.feed()
