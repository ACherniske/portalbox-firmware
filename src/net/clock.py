# net/clock.py
#
# Keeps the RTC at or above a "floor" so TLS certificate dates validate.
#
# After power-on the ESP32 clock reads 2000-01-01, which makes the
# WPA2-Enterprise server certificate look "not valid yet" (the mbedTLS
# client checks dates and ignores ESP-IDF's disable_time_check). NTP can
# only fix the clock after a connection, so before connecting we move the
# clock forward to the later of CLOCK_FLOOR and the last time we saved.
# The clock is never moved backwards.

import os
import time
import machine

# UTC date. Bump on releases so a fresh box starts near the real date.
CLOCK_FLOOR = (2026, 11, 4)

# One integer: time.time() seconds (board epoch is 2000-01-01).
SAVED_TIME_FILE = '/config/last_time.txt'

AUTOSAVE_TIMER_ID = 0
AUTOSAVE_PERIOD_MS = 3_600_000

_autosave_timer = None


def load_saved():
    """Return the saved time in seconds, or None if missing or unreadable."""
    try:
        with open(SAVED_TIME_FILE, 'r') as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def save_now():
    """Save the current time. Returns True on success."""
    try:
        try:
            os.mkdir('/config')
        except OSError:
            pass
        with open(SAVED_TIME_FILE, 'w') as f:
            f.write(str(time.time()))
        return True
    except OSError as e:
        print("[Clock] Saving time failed: %s" % e)
        return False


def floor_seconds():
    """The earliest time the clock may show: CLOCK_FLOOR or the saved time."""
    return max(time.mktime(CLOCK_FLOOR + (0, 0, 0, 0, 0)), load_saved() or 0)


def apply_floor():
    """Move the clock up to the floor if it is behind. Returns True if changed."""
    floor = floor_seconds()
    if time.time() >= floor:
        return False
    tm = time.gmtime(floor)
    # RTC.datetime(): (year, month, day, weekday, hours, minutes, seconds, subseconds)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
    return True


def _autosave_tick(timer):
    try:
        save_now()
    except Exception as e:
        print("[Clock] Autosave failed: %s" % e)


def start_autosave():
    """Save the time every AUTOSAVE_PERIOD_MS. Safe to call more than once."""
    global _autosave_timer
    if _autosave_timer is not None:
        return
    _autosave_timer = machine.Timer(AUTOSAVE_TIMER_ID)
    _autosave_timer.init(period=AUTOSAVE_PERIOD_MS, mode=machine.Timer.PERIODIC,
                         callback=_autosave_tick)
