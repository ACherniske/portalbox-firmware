"""Recovery anchor: saved state, boot counting and the on-box event log.

OTA never replaces this file (see ANCHOR_FILES). It runs before the app on
every normal boot, so it must stay small and must not import app code.
"""

import os
import time
import json
import machine

# Paths are module globals so tests can move them somewhere harmless.
STATE_PATH = "/ota/state.json"
JOURNAL_PATH = "/ota/journal.json"
PREV_DIR = "/ota/prev"
STAGE_DIR = "/ota/stage"
LOG_PATH = "/log/events.log"
DEV_FLAG_PATH = "/config/dev_mode"
LIVE_ROOT = ""

MAX_BOOTS = 3
LOG_MAX = 16384
WDT_MS = 30000
ANCHOR_FILES = ("boot.py", "main.py", "recovery.py")
# Per-box: everything under config/ (wiring, settings, WiFi, OTA) and the
# WiFi CA cert are written by the flash tool (or by hand), never by an
# update. So the anchor can trust config/pins.py.
LOCAL_DIRS = ("config", "ota", "log")
LOCAL_FILES = ("ca-cert.pem",)

ENOENT = 2

_wdt = None


def _defaults():
    return {
        "v": 1,
        "version": None,
        "prev_version": None,
        "trial": False,
        "trial_boots": 0,
        "trial_wifi_fails": 0,
        "crash_count": 0,
        "bad": [],
        "managed": [],
    }


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _make_parent(path):
    built = ""
    for part in path.strip("/").split("/")[:-1]:
        built += "/" + part
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
        line = "%d %s %s\n" % (time.time(), tag, msg)
        _make_parent(LOG_PATH)
        try:
            size = os.stat(LOG_PATH)[6]
        except OSError:
            size = 0
        if size and size + len(line.encode()) > LOG_MAX:
            old = LOG_PATH + ".1"
            try:
                os.remove(old)
            except OSError:
                pass
            os.rename(LOG_PATH, old)
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass


def dev_mode():
    return _exists(DEV_FLAG_PATH)


def _valid(saved, defaults):
    if not isinstance(saved, dict):
        return False
    for key, default in defaults.items():
        if (
            key in saved
            and default is not None
            and type(saved[key]) is not type(default)
        ):
            return False
    return True


def _read_text(path):
    """The file's text, or None if it does not exist. Any other error
    (flash, memory) is raised: it says nothing about the file itself."""
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError as e:
        if e.errno == ENOENT:
            return None
        raise


def load_state():
    """The saved state, with defaults for missing keys.

    A missing file gives the defaults. A file that is not valid JSON, or has a
    wrong-typed field, also gives the defaults and is logged, so a bad file
    can never stop the box from booting. A read error is raised instead.
    """
    state = _defaults()
    text = _read_text(STATE_PATH)
    if text is None:
        return state
    try:
        saved = json.loads(text)
    except ValueError:
        saved = None
    if not _valid(saved, state):
        log("state", "state reset: bad %s" % STATE_PATH)
        return state
    state.update(saved)
    return state


def _write_json(path, obj):
    """Write to a temp file, then rename over, so a power cut keeps the old file."""
    _make_parent(path)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.rename(tmp, path)


def save_state(state):
    _write_json(STATE_PATH, state)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _rename(src, dst):
    _make_parent(dst)
    os.rename(src, dst)


def _rm_tree(path):
    try:
        is_dir = os.stat(path)[0] & 0x4000
    except OSError:
        return
    if is_dir:
        for name in os.listdir(path):
            _rm_tree(path + "/" + name)
        os.rmdir(path)
    else:
        os.remove(path)


def _prune(path):
    """Remove the live folders above `path` that are now empty.

    MicroPython imports a folder before name.py, so an empty folder left
    behind would shadow a module of the same name. Stops at the first
    folder that still has files; never touches LIVE_ROOT itself.
    """
    parts = path.split("/")[:-1]
    while parts:
        try:
            os.rmdir(_live("/".join(parts)))
        except OSError:
            return
        parts.pop()


def _live(path):
    return LIVE_ROOT + "/" + path


def _prev(path):
    return PREV_DIR + "/" + path


def _stage(path):
    return STAGE_DIR + "/" + path


def _load_journal():
    """The journal, or None. A corrupt one is logged and removed; a read
    error is raised, so the journal survives for the next boot."""
    text = _read_text(JOURNAL_PATH)
    if text is None:
        return None
    try:
        journal = json.loads(text)
        for key in (
            "status",
            "version",
            "prev_version",
            "files",
            "deletes",
            "new",
            "prev_managed",
        ):
            journal[key]  # KeyError (or TypeError if not a dict) -> unreadable
        return journal
    except (ValueError, KeyError, TypeError):
        log("install", "journal unreadable, removed")
        _remove(JOURNAL_PATH)
        return None


def _protected(path):
    """True for a path an update must never replace or delete."""
    parts = path.split("/")
    if not path or "" in parts or ".." in parts:
        return True  # empty, leading '/', '//', trailing '/', or '..'
    if path in ANCHOR_FILES or path in LOCAL_FILES:
        return True
    if parts[0] in LOCAL_DIRS:
        return True
    # A folder: moving it would take every file inside along (config/).
    try:
        return os.stat(_live(path))[0] & 0x4000 != 0
    except OSError:
        return False


def install(version, files, deletes, managed):
    """Swap the staged files in and start a trial of `version`.

    Paths have no leading slash ('core/fsm.py'); staged copies are at
    STAGE_DIR/<path>. Every step is journaled, so a power cut at any point is
    undone by the next on_boot(); any other error undoes it at once and
    re-raises. Raises before touching anything if a trial or another install
    is pending, a path is listed twice, a staged file is missing, or any
    path is protected (the anchor, config/, /ota, /log, ca-cert.pem, folders).
    """
    for p in files + deletes + managed:
        if _protected(p):
            raise ValueError("protected path: %r" % (p,))
    state = load_state()
    if state["trial"]:
        raise RuntimeError("trial active")
    if _exists(JOURNAL_PATH):
        raise RuntimeError("install pending")
    if len(set(files + deletes)) != len(files) + len(deletes):
        raise ValueError("path listed twice")
    for f in files:
        if not _exists(_stage(f)):
            raise ValueError("not staged: " + f)
    # Old copies left by an earlier power cut must never be restored.
    _rm_tree(PREV_DIR)
    journal = {
        "v": 1,
        "status": "installing",
        "version": version,
        "prev_version": state["version"],
        "files": files,
        "deletes": deletes,
        # Files with no live copy yet: the only ones a rollback removes.
        "new": [f for f in files if not _exists(_live(f))],
        "prev_managed": state["managed"],
    }
    _write_json(JOURNAL_PATH, journal)
    try:
        for f in files:
            if _exists(_live(f)):
                _rename(_live(f), _prev(f))
            _rename(_stage(f), _live(f))
        for d in deletes:
            if _exists(_live(d)):
                _rename(_live(d), _prev(d))
                _prune(d)
    except Exception as e:
        log("install", "install %s failed: %r" % (version, e))
        rollback(False)
        raise
    # State before journal: a cut between the two leaves the journal at
    # 'installing', so the next boot undoes the install instead of
    # mistaking it for a finished commit.
    state["version"] = version
    state["prev_version"] = journal["prev_version"]
    state["managed"] = managed
    state["trial"] = True
    state["trial_boots"] = 0
    state["trial_wifi_fails"] = 0
    state["crash_count"] = 0
    save_state(state)
    journal["status"] = "trial"
    _write_json(JOURNAL_PATH, journal)
    log(
        "install",
        "install %s (%d files, %d deletes)" % (version, len(files), len(deletes)),
    )
    _rm_tree(STAGE_DIR)


def rollback(mark_bad):
    """Put back the files the journaled install replaced. Safe to re-run.

    The journal is marked 'rolling_back' first, so a power cut part way
    through is finished by the next on_boot() with the same mark_bad.
    """
    journal = _load_journal()
    if journal is None:
        return
    if journal["status"] == "rolling_back":
        mark_bad = journal.get("mark_bad", mark_bad)
    else:
        journal["status"] = "rolling_back"
        journal["mark_bad"] = mark_bad
        _write_json(JOURNAL_PATH, journal)
    for f in journal["files"]:
        if _exists(_prev(f)):
            _remove(_live(f))
            _rename(_prev(f), _live(f))
        elif f in journal["new"]:
            _remove(_live(f))
            _prune(f)
        # Otherwise it was never moved, or is already back. Leave it.
    for d in journal["deletes"]:
        if _exists(_prev(d)):
            _remove(_live(d))
            _rename(_prev(d), _live(d))
    state = load_state()
    state["version"] = journal["prev_version"]
    state["prev_version"] = None
    state["managed"] = journal["prev_managed"]
    state["trial"] = False
    state["trial_boots"] = 0
    state["trial_wifi_fails"] = 0
    state["crash_count"] = 0
    if mark_bad and journal["version"] not in state["bad"]:
        state["bad"].append(journal["version"])
    save_state(state)
    # Folders before the journal: a cut here re-runs the rollback, which is
    # safe because every file is already back.
    _rm_tree(STAGE_DIR)
    _rm_tree(PREV_DIR)
    _remove(JOURNAL_PATH)
    log("install", "rollback %s bad=%s" % (journal["version"], mark_bad))


def _finish_commit():
    # The state was saved first. Old copies go before the journal, so a cut
    # here leaves the journal and the next boot finishes the commit.
    _rm_tree(PREV_DIR)
    _remove(JOURNAL_PATH)


def clear_state():
    """Forget every update: state, journal, old and staged copies.

    For a box flashed by hand. Live files are left as they are.
    """
    for path in (STATE_PATH, STATE_PATH + ".tmp", JOURNAL_PATH, JOURNAL_PATH + ".tmp"):
        _remove(path)
    _rm_tree(PREV_DIR)
    _rm_tree(STAGE_DIR)


def _finish_interrupted(journal, state):
    """Complete whatever a power cut stopped. Returns the state afterwards."""
    if journal is None:
        if state["trial"]:
            # Nothing to roll back to; keep the files and stop the trial.
            state["trial"] = False
            save_state(state)
            log("install", "trial without journal cleared")
        return state
    status = journal["status"]
    if status == "installing":
        rollback(False)
        log("install", "interrupted install undone")
    elif status == "rolling_back":
        rollback(False)  # reuses the mark_bad saved in the journal
        log("install", "interrupted rollback finished")
    elif status == "trial" and not state["trial"]:
        _finish_commit()
        log("install", "interrupted commit finished")
    return load_state()


def on_boot(power_on):
    """Decide whether this boot runs the app. Returns ('run', '') or ('stuck', reason).

    First finishes any install, rollback or commit a power cut stopped.
    While a new version is on trial, each boot counts a trial boot (power-on
    does not clear it); after MAX_BOOTS without mark_healthy() it is rolled
    back, marked bad unless every failed boot was a WiFi failure.
    Otherwise each boot adds one to crash_count; mark_healthy() clears it.
    A power-on reset clears it first, so a box someone has unplugged starts
    clean. Dev mode never counts.
    """
    if dev_mode():
        return ("run", "")
    try:
        state = _finish_interrupted(_load_journal(), load_state())
        if state["trial"]:
            if state["trial_boots"] < MAX_BOOTS:
                state["trial_boots"] += 1
                save_state(state)
                return ("run", "")
            rollback(state["trial_wifi_fails"] < state["trial_boots"])
            state = load_state()
    except Exception as e:
        # The files may be half old, half new: running the app is unsafe.
        log("boot", "stuck: install recovery failed: %r" % (e,))
        return ("stuck", "install recovery failed")
    if power_on:
        state["crash_count"] = 0
    if state["crash_count"] >= MAX_BOOTS:
        log("boot", "stuck: crash loop (%d unhealthy boots)" % state["crash_count"])
        return ("stuck", "crash loop")
    state["crash_count"] += 1
    save_state(state)
    return ("run", "")


def mark_healthy():
    """The app ran long enough: clear the count, and commit a trial version."""
    if dev_mode():
        return
    state = load_state()
    state["crash_count"] = 0
    committing = state["trial"]
    if committing:
        state["trial"] = False
        state["trial_boots"] = 0
        state["trial_wifi_fails"] = 0
    save_state(state)
    if committing:
        _finish_commit()
        log("install", "commit %s" % state["version"])


def note_wifi_failure():
    """A WiFi failure resets the box but is not a crash: it ends the run of
    unhealthy boots, so the count starts again from 0.

    During a trial it is counted, so a trial that only failed on WiFi is
    rolled back without being marked bad.
    """
    if dev_mode():
        return
    state = load_state()
    state["crash_count"] = 0
    if state["trial"]:
        state["trial_wifi_fails"] += 1
    save_state(state)


def arm_watchdog(timeout_ms=WDT_MS):
    global _wdt
    if _wdt is None:
        _wdt = machine.WDT(timeout=timeout_ms)


def feed():
    if _wdt is not None:
        _wdt.feed()


def relays_low():
    """Drive both relay pins low (relays off). Never raises.

    Uses config.pins when it has the relay names, else pins 18 and 9.
    """
    try:
        from config.pins import RELAY_AC_PIN, RELAY_USB_PIN

        pins = (RELAY_AC_PIN, RELAY_USB_PIN)
    except Exception:
        pins = (18, 9)
    for pin in pins:
        try:
            machine.Pin(pin, machine.Pin.OUT, value=0)
        except Exception as e:
            print("[BOOT] relay pin %s not driven low: %s" % (pin, e))


def _leds_red():
    """All LEDs red. Skipped (and logged) if the LED code can't load."""
    try:
        from config.pins import (
            SPI_LED_ID,
            SPI_LED_SCK,
            SPI_LED_MOSI,
            SPI_LED_MISO,
            SPI_LED_BAUDRATE,
        )
        from config.settings import LED_COUNT
        from hardware.lib.dotstar import DotStar

        spi = machine.SPI(
            SPI_LED_ID,
            baudrate=SPI_LED_BAUDRATE,
            polarity=0,
            phase=0,
            sck=machine.Pin(SPI_LED_SCK),
            mosi=machine.Pin(SPI_LED_MOSI),
            miso=machine.Pin(SPI_LED_MISO),
        )
        leds = DotStar(spi, num_leds=LED_COUNT)
        leds.fill(255, 0, 0)
        leds.show()
    except Exception as e:
        log("stuck", "no LEDs: %r" % (e,))


def _on_trial():
    try:
        return load_state()["trial"]
    except Exception:
        return False


def stuck_forever(
    reason, beep_ms=100, period_ms=5000, cycles=None, rollback_trial=True, reset=None
):
    """The 'needs a cable' signal: relays off, all LEDs red, a short beep
    every period_ms. cycles=None loops forever; tests pass a count.

    A trial version that gets here resets instead: the boot counts as a
    failed trial, so the old version comes back. The boot check passes
    rollback_trial=False, since resetting there could loop forever.
    """
    relays_low()
    if rollback_trial and _on_trial():
        log("stuck", "stuck during trial: %s, reset" % reason)
        print("[BOOT] Stuck during trial (%s): reset to roll back" % reason)
        (reset or machine.reset)()
        return
    log("stuck", reason)
    print("[BOOT] STUCK (%s): needs a cable" % reason)
    _leds_red()
    try:
        from config.pins import BUZZER_PIN
    except Exception:
        BUZZER_PIN = 4
    try:
        buzzer = machine.Pin(BUZZER_PIN, machine.Pin.OUT, value=0)
    except Exception:
        buzzer = None
    n = 0
    while cycles is None or n < cycles:
        if buzzer is not None:
            buzzer.on()
        time.sleep_ms(beep_ms)
        if buzzer is not None:
            buzzer.off()
        feed()
        time.sleep_ms(period_ms - beep_ms)
        n += 1
