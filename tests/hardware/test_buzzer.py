# main.py
# Active buzzer test script
#
# Plays:
# - single beep
# - triple beep
# - continuous warning beep
#
# Buzzer GPIO: 4

import uasyncio as asyncio
from hardware.buzzer import Buzzer

# =========================================================
# Setup
# =========================================================

BUZZER_PIN = 4

buzzer = Buzzer(pin_num=BUZZER_PIN, enabled=True)

# =========================================================
# Test Routine
# =========================================================


async def buzzer_test():
    print("Starting buzzer test...")

    # -----------------------------------------------------
    # Single beep
    # -----------------------------------------------------

    print("Single beep")
    buzzer.play(Buzzer.BEEP_CARD_READ)

    await asyncio.sleep(1)

    # -----------------------------------------------------
    # Triple beep
    # -----------------------------------------------------

    print("Triple beep")

    triple_pattern = (100, 100, 100)

    buzzer.play(triple_pattern)

    await asyncio.sleep(1)

    # -----------------------------------------------------
    # Continuous warning beep
    # -----------------------------------------------------

    print("Continuous warning beep")

    warning_pattern = (300,)

    buzzer.play(warning_pattern, continuous=True, interval_ms=500)

    await asyncio.sleep(5)

    # -----------------------------------------------------
    # Stop buzzer
    # -----------------------------------------------------

    buzzer.stop()

    print("Buzzer test complete")


# =========================================================
# Main
# =========================================================

asyncio.run(buzzer_test())
