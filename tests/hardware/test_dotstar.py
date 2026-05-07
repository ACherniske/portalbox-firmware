# Sequential DotStar APA102 LED test
# CLK  -> GPIO13
# DATA -> GPIO14

from machine import Pin, SPI
from hardware.lib.dotstar import DotStar
import time

NUM_LEDS = 10

# Initialize SPI
spi = SPI(
    1,
    baudrate=4000000,
    polarity=0,
    phase=0,
    sck=Pin(13),
    mosi=Pin(14)
)

# Create DotStar strip
dots = DotStar(
    spi,
    num_leds=NUM_LEDS,
    brightness=31
)

# Start with all LEDs off
dots.clear()
dots.show()

# Turn LEDs on one by one in green
for i in range(NUM_LEDS):
    dots.set_pixel(i, 0, 255, 0)  # Green
    dots.show()
    time.sleep(0.2)

# Keep all LEDs on for 2 seconds
time.sleep(2)

# Turn all LEDs off
dots.clear()
dots.show()

# Keep program alive
while True:
    time.sleep(1)
