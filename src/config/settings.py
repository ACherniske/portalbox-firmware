# config/settings.py
# Default settings for the device

from micropython import const

# Device Features - enable (1) or disable (0)
ENABLE_BUZZER = const(1)
ENABLE_RFID = const(1)
ENABLE_BUTTONS = const(1)
ENABLE_LEDS = const(1)

# -----------------------------------------------

# LED Settings
LED_TYPE = "dotstar"  # 'dotstar' or 'neopixel'
LED_COUNT = const(10)  # Number of LEDs in the strip
LED_BRIGHTNESS = const(31)

# RFID Settings
RFID_DEBOUNCE_MS = const(
    250
)  # Minimum time between reads of the same tag in milliseconds

# Device Timeouts
GRACE_TIMEOUT_MS = const(
    30000
)  # Time to wait when in grace period before going to idle (30s)
DEVICE_TIMEOUT_MS = const(
    60 * 60 * 1000
)  # Time to wait when idle before going to sleep (60m)

# Maker Portal server (its address is set per box in /config/api_config.json)
API_TIMEOUT_S = const(
    5
)  # Per network step (connect, handshake, read); keep well under the 30 s watchdog
