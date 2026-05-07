# main.py
# Onboard NeoPixel test
# DATA -> GPIO38

from neopixel_wrapper import neopixel_wrapper
import time

# Most ESP32-S3 boards only have 1 onboard NeoPixel
NUM_LEDS = 1
DATA_PIN = 38

# Create NeoPixel object
pixel = neopixel_wrapper(
    pin=DATA_PIN,
    num_pixels=NUM_LEDS,
    brightness=31
)

# Ensure LED starts off
pixel.clear()
pixel.show()

# Turn onboard LED green
pixel.set_pixel(0, 0, 255, 0)
pixel.show()

# Keep LED on for 5 seconds
time.sleep(5)

# Turn LED off
pixel.clear()
pixel.show()

# Keep script alive
while True:
    time.sleep(1)

