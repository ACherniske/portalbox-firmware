# main.py
# App entry point: safe-mode check, then the PortalBox app.
#
# MicroPython runs main.py only on a normal boot (power-up, reset, Ctrl-D at
# the REPL), not on mpremote's soft reset, so tools get a clean REPL.
# boot.py has already turned the relays off.

import time
import machine

SAFE_MODE_PIN = 0          # BOOT button on many ESP32 boards
SAFE_MODE_HOLD_MS = 3000   # Hold duration to enter safe mode


def _safe_mode_requested():
    """
    Check if the BOOT button or the box button is being held.

    Returns:
        bool: True if safe mode requested
    """

    try:
        # BOOT button is active-low.
        boot_pin = machine.Pin(
            SAFE_MODE_PIN,
            machine.Pin.IN,
            machine.Pin.PULL_UP
        )

        # Box button is inverted (active-high for safe boot).
        box_pin = None

        try:
            from config.pins import BUTTON_PIN
            if BUTTON_PIN != SAFE_MODE_PIN:
                box_pin = machine.Pin(
                    BUTTON_PIN,
                    machine.Pin.IN,
                    machine.Pin.PULL_UP
                )
        except Exception:
            # If config/pins isn't available, fall back to BOOT pin only.
            pass

        def button_pressed():
            if boot_pin.value() == 0:
                return True

            if box_pin is not None and box_pin.value() == 1:
                return True

            return False

        # Require a button to be held continuously for the full hold duration.
        if not button_pressed():
            return False

        start = time.ticks_ms()

        while time.ticks_diff(time.ticks_ms(), start) < SAFE_MODE_HOLD_MS:
            if not button_pressed():
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
    """App entry point."""

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
