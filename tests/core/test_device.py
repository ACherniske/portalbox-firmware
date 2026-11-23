# tests/test_device.py
# Full hardware abstraction layer test

import time
import uasyncio as asyncio

from core.device import get_device, cleanup_device


async def main():
    """Run full device hardware test."""

    print("\n==============================")
    print(" DEVICE HARDWARE LAYER TEST")
    print("==============================\n")

    # Initialize device
    device = get_device()

    # Print hardware status
    device.print_status()

    # =========================================================
    # Startup Sequence
    # =========================================================

    print("[TEST] Running startup sequence...")
    device.startup_sequence()

    await asyncio.sleep(2)

    # =========================================================
    # LED Test
    # =========================================================

    if device.led:
        print("[TEST] Testing LEDs...")

        device.led.stop()

        colors = ["RED", "GREEN", "BLUE", "WHITE", "PURPLE", "CYAN", "YELLOW", "OFF"]

        for color in colors:
            print(f"[TEST] LED color: {color}")
            device.led.fill(color)
            await asyncio.sleep(1)

        print("[TEST] Running LED chase animation...")
        device.led.chase("BLUE", wait_ms=50)

        await asyncio.sleep(3)

        device.led.stop()
        device.led.clear()

    # =========================================================
    # Relay Test
    # =========================================================

    if device.relay:
        print("[TEST] Testing relays...")

        print("[TEST] Relays ON")
        device.relay.turn_on()
        await asyncio.sleep(2)

        print("[TEST] Relays OFF")
        device.relay.turn_off()
        await asyncio.sleep(2)

    # =========================================================
    # Button Test
    # =========================================================

    if device.button:
        print("[TEST] Testing button...")
        print("[TEST] Press the button within 10 seconds")

        start = time.ticks_ms()

        while time.ticks_diff(time.ticks_ms(), start) < 10000:
            if device.button.is_pressed:
                print("[TEST] Button pressed!")

                if device.led:
                    device.led.fill("GREEN")

                if device.buzzer:
                    device.buzzer.play(device.buzzer.BEEP_CARD_READ)

                await asyncio.sleep(1)

                if device.led:
                    device.led.clear()

                break

            await asyncio.sleep_ms(50)

    # =========================================================
    # RFID Test
    # =========================================================

    if device.rfid:
        print("[TEST] Testing RFID...")
        print("[TEST] Scan a card within 15 seconds")

        start = time.ticks_ms()

        while time.ticks_diff(time.ticks_ms(), start) < 15000:
            uid = device.rfid.read_card()

            if uid:
                print("[TEST] RFID card detected!")
                print(f"[TEST] UID: {uid}")

                if device.led:
                    device.led.fill("CYAN")

                if device.buzzer:
                    device.buzzer.play(device.buzzer.BEEP_TRIPLE)

                await asyncio.sleep(1)

                if device.led:
                    device.led.clear()

                break

            await asyncio.sleep_ms(100)

    # =========================================================
    # Buzzer Test
    # =========================================================

    if device.buzzer:
        print("[TEST] Testing buzzer...")

        device.buzzer.play(device.buzzer.BEEP_TRIPLE)

        await asyncio.sleep(2)

    # =========================================================
    # Shutdown Sequence
    # =========================================================

    print("[TEST] Running shutdown sequence...")
    device.shutdown_sequence()

    await asyncio.sleep(2)

    # Cleanup
    cleanup_device()

    print("\n==============================")
    print(" DEVICE TEST COMPLETE")
    print("==============================\n")


# Run test
try:
    asyncio.run(main())

except KeyboardInterrupt:
    print("\n[TEST] Interrupted by user")
    cleanup_device()
