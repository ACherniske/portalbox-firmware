# boot.py
# Runs on every reset, including mpremote's soft reset. Keep it tiny:
# it only makes sure the relays are off. main.py starts the app, and
# MicroPython runs main.py only on a normal boot, so tools like mpremote
# get a clean REPL with the app stopped.

import machine


def _relays_low():
    """Drive both relay pins low (relays off) as early as possible."""

    try:
        from config.pins import RELAY_AC_PIN, RELAY_USB_PIN
        pins = (RELAY_AC_PIN, RELAY_USB_PIN)
    except Exception:
        pins = (18, 9)

    for pin in pins:
        try:
            machine.Pin(pin, machine.Pin.OUT, value=0)
        except Exception as error:
            print("[BOOT] Could not drive relay pin %s low: %s" % (pin, error))


_relays_low()
