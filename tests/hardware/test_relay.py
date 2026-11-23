# main.py
# Relay test script
#
# Tests:
# 1. AC relay individually
# 2. USB relay individually
# 3. Both relays together
#
# Update these pins to match your hardware.

from hardware.relay import Relay
import time

# =========================================================
# Relay Pin Configuration
# =========================================================

AC_RELAY_PIN = 18
USB_RELAY_PIN = 9

# =========================================================
# Relay Setup
# =========================================================

relay = Relay(ac_relay_pin=AC_RELAY_PIN, usb_relay_pin=USB_RELAY_PIN)

print("Relay test starting...")

# =========================================================
# Test AC Relay
# =========================================================

print("Testing AC relay ON")
relay.relay_ac.on()
time.sleep(2)

print("Testing AC relay OFF")
relay.relay_ac.off()
time.sleep(1)

# =========================================================
# Test USB Relay
# =========================================================

print("Testing USB relay ON")
relay.relay_usb.on()
time.sleep(2)

print("Testing USB relay OFF")
relay.relay_usb.off()
time.sleep(1)

# =========================================================
# Test Both Relays
# =========================================================

print("Testing BOTH relays ON")
relay.turn_on()
time.sleep(3)

print("Testing BOTH relays OFF")
relay.turn_off()

print("Relay test complete")
