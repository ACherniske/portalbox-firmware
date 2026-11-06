# boot.py
# Runs on every reset, including mpremote's soft reset. Keep it tiny:
# it only makes sure the relays are off. main.py starts the app, and
# MicroPython runs main.py only on a normal boot, so tools like mpremote
# get a clean REPL with the app stopped.

import sys
import recovery

recovery.relays_low()

# Forget the module so a tool that copies a new recovery.py and then
# imports it in the same session gets the new file, not this one.
del sys.modules['recovery']
del recovery
