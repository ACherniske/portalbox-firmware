# boot.py
# Runs on every reset, including mpremote's soft reset. Keep it tiny:
# it only makes sure the relays are off. main.py starts the app, and
# MicroPython runs main.py only on a normal boot, so tools like mpremote
# get a clean REPL with the app stopped.

import sys

try:
    import recovery

    recovery.relays_low()
except BaseException as e:
    # A broken recovery.py must not stop main.py from running: MicroPython
    # skips main.py when boot.py fails. Turn the relays off by hand.
    import machine

    try:
        from config.pins import RELAY_AC_PIN, RELAY_USB_PIN

        relay_pins = (RELAY_AC_PIN, RELAY_USB_PIN)
    except BaseException:
        relay_pins = (18, 9)
    for pin in relay_pins:
        try:
            machine.Pin(pin, machine.Pin.OUT, value=0)
        except Exception:
            pass
    try:
        print("[BOOT] recovery failed in boot.py: %r" % (e,))
    except BaseException:
        pass

# Forget the module so a tool that copies a new recovery.py and then
# imports it in the same session gets the new file, not this one.
sys.modules.pop("recovery", None)
try:
    del recovery
except NameError:
    pass
