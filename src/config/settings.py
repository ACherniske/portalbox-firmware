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
LED_TYPE = 'dotstar'  # 'dotstar' or 'neopixel'
LED_COUNT = const(10)  # Number of LEDs in the strip
LED_BRIGHTNESS = const(31)

# RFID Settings
RFID_DEBOUNCE_MS = const(1000)  # Minimum time between reads of the same tag in milliseconds