# boot.py
# Minimal bootloader / safe-mode entry point.

import time
import machine

SAFE_MODE_PIN = 0          # BOOT button on many ESP32 boards
SAFE_MODE_HOLD_MS = 3000   # Hold duration to enter safe mode


def _safe_mode_requested():
    """
    Check if the BOOT button is being held.

    Returns:
        bool: True if safe mode requested
    """

    try:
        button = machine.Pin(
            SAFE_MODE_PIN,
            machine.Pin.IN,
            machine.Pin.PULL_UP
        )

        start = time.ticks_ms()

        while time.ticks_diff(
            time.ticks_ms(),
            start
        ) < SAFE_MODE_HOLD_MS:

            # Button pressed (active low)
            if button.value():
                return False

            time.sleep_ms(50)

        return True

    except Exception as error:
        print("[BOOT] Safe mode check failed:", error)
        return False


def _safe_mode():
    """
    Enter safe mode.

    Safe mode:
    - skips WiFi
    - skips OTA
    - skips main app
    - leaves REPL available over USB/COM
    """

    print("\n" + "=" * 60)
    print("SAFE MODE ENABLED")
    print("=" * 60)
    print("WiFi startup skipped")
    print("OTA skipped")
    print("Main application skipped")
    print("REPL active over USB/COM")
    print("=" * 60)

    # Idle forever so REPL stays accessible
    while True:
        time.sleep(1)


def launch():
    """Boot entry point."""

    print("\n[BOOT] PortalBox bootloader starting...")

    # Give time to interrupt boot from COM
    print("[BOOT] Hold BOOT button for safe mode...")
    time.sleep_ms(1500)

    if _safe_mode_requested():
        _safe_mode()
        return

    try:
        print("[BOOT] Launching system manager...")

        from core.system import boot

        boot()

    except Exception as error:
        print("\n" + "=" * 60)
        print("BOOT FAILURE")
        print("=" * 60)
        print(error)
        print("=" * 60)

        # Keep REPL alive after crash
        while True:
            time.sleep(1)


launch()
