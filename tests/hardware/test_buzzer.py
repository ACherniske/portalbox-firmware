# Buzzer test script
#
# Plays 3 quick pips.

import uasyncio as asyncio
from hardware.buzzer import Buzzer

# =========================================================
# Configuration
# =========================================================

BUZZER_PIN = 4

# =========================================================
# Setup
# =========================================================

buzzer = Buzzer(
    pin_num=BUZZER_PIN,
    enabled=True,
    volume=50
)

# =========================================================
# Test Task
# =========================================================

async def buzzer_test():
    print("Playing 3 quick pips...")

    # Pattern: 3 short high-pitched beeps
    pattern = (
        (2000, 100),
        (2000, 100),
        (2000, 100),
    )

    buzzer.play(pattern)

    # Wait long enough for pattern to finish
    await asyncio.sleep(1)

    buzzer.stop()

    print("Buzzer test complete")


# =========================================================
# Main
# =========================================================

asyncio.run(buzzer_test())
