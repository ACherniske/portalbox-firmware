# main.py
# Combined LED controller test
#
# Tests:
# - Onboard NeoPixel (GPIO38)
# - External DotStar strip (CLK=13, DATA=14)
#
# Requires:
# - hardware/drivers/neopixel.py
# - hardware/drivers/dotstar.py
# - hardware/led_controller.py

import uasyncio as asyncio
from machine import Pin, SPI

from hardware.lib.neopixel_wrapper import neopixel_wrapper
from hardware.lib.dotstar import DotStar
from hardware.led import LEDController


# =========================================================
# Hardware Setup
# =========================================================

# Onboard NeoPixel
onboard_np = neopixel_wrapper(
    pin=38,
    num_pixels=1,
    brightness=31
)

# DotStar SPI
spi = SPI(
    1,
    baudrate=4000000,
    polarity=0,
    phase=0,
    sck=Pin(13),
    mosi=Pin(14)
)

# External DotStar strip
dotstars = DotStar(
    spi,
    num_leds=10,
    brightness=31
)

# LED Controllers
onboard = LEDController(onboard_np)
strip = LEDController(dotstars)


# =========================================================
# Demo Task
# =========================================================

async def led_demo():
    print("Starting LED test...")

    # -----------------------------------------------------
    # Solid Colors
    # -----------------------------------------------------

    onboard.fill("RED")
    strip.fill("RED")
    await asyncio.sleep(1)

    onboard.fill("GREEN")
    strip.fill("GREEN")
    await asyncio.sleep(1)

    onboard.fill("BLUE")
    strip.fill("BLUE")
    await asyncio.sleep(1)

    # -----------------------------------------------------
    # Blink Test
    # -----------------------------------------------------

    onboard.blink("WHITE", count=6, on_ms=200, off_ms=200)
    strip.blink("WHITE", count=6, on_ms=200, off_ms=200)

    await onboard.wait_animation()
    await strip.wait_animation()

    # -----------------------------------------------------
    # Wipe Test
    # -----------------------------------------------------

    onboard.fill("OFF")

    strip.wipe("CYAN", wait_ms=100, clear_after=True)
    await strip.wait_animation()

    # -----------------------------------------------------
    # Chase Test
    # -----------------------------------------------------

    strip.chase("PURPLE", background="OFF", wait_ms=75)

    onboard.pulse(
        "MAGENTA",
        min_brightness=2,
        max_brightness=31,
        step=1,
        interval_ms=30
    )

    await asyncio.sleep(5)

    # Stop animations
    onboard.stop()
    strip.stop()

    # -----------------------------------------------------
    # Rainbow Test
    # -----------------------------------------------------

    strip.rainbow(wait_ms=10, continuous=True)

    onboard.fill("YELLOW")

    await asyncio.sleep(5)

    # -----------------------------------------------------
    # Cleanup
    # -----------------------------------------------------

    onboard.stop()
    strip.stop()

    onboard.clear()
    strip.clear()

    print("LED test complete")


# =========================================================
# Main Loop
# =========================================================

async def main():
    await led_demo()

    # Keep asyncio alive
    while True:
        await asyncio.sleep(1)


asyncio.run(main())
