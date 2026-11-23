# main.py
# App entry point: safe-mode check, recovery anchor, watchdog, then the app.
#
# MicroPython runs main.py only on a normal boot (power-up, reset, Ctrl-D at
# the REPL), not on mpremote's soft reset, so tools get a clean REPL.
# boot.py has already turned the relays off.

import gc
import io
import sys
import time
import machine

SAFE_MODE_PIN = 0  # BOOT button on many ESP32 boards
SAFE_MODE_HOLD_MS = 3000  # Hold duration to enter safe mode


def _safe_mode_requested():
    """
    Check if the BOOT button or the box button is being held.

    Returns:
        bool: True if safe mode requested
    """

    try:
        # BOOT button is active-low.
        boot_pin = machine.Pin(SAFE_MODE_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

        # Box button is inverted (active-high for safe boot). Its pin comes
        # from config/pins.py, a per-box file that OTA never changes.
        box_pin = None

        try:
            from config.pins import BUTTON_PIN

            if BUTTON_PIN != SAFE_MODE_PIN:
                box_pin = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
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


def _cause_name(cause):
    """machine.reset_cause() as a name, e.g. WDT_RESET."""
    for name in (
        "PWRON_RESET",
        "HARD_RESET",
        "WDT_RESET",
        "DEEPSLEEP_RESET",
        "SOFT_RESET",
    ):
        if getattr(machine, name, None) == cause:
            return name
    return str(cause)


def _log_crash(recovery, error):
    """Print the traceback and log it as one line. Never raises: the box
    must still reset after a crash that left no memory to describe it."""
    try:
        gc.collect()
        buf = io.StringIO()
        sys.print_exception(error, buf)
        trace = buf.getvalue()
        print("\n[BOOT] crash %s: %s" % (type(error).__name__, error))
        print(trace)
        recovery.log(
            "crash",
            "%s: %s | %s"
            % (type(error).__name__, error, trace.strip().replace("\n", " | ")),
        )
    except Exception:
        pass


def _run_app(recovery):
    """Run the app; log how it ended. Never raises."""
    try:
        print("[BOOT] Launching system manager...")

        from core.system import boot

        boot()
        recovery.log("boot", "app returned")

    except Exception as error:
        _log_crash(recovery, error)

    except BaseException as error:
        # Ctrl-C (a tool connecting) or SystemExit: not a crash, but the end.
        recovery.log("boot", "interrupted: %s" % type(error).__name__)


def launch():
    """App entry point."""

    print("\n[BOOT] PortalBox bootloader starting...")

    # Give time to interrupt boot from COM
    print("[BOOT] Hold BOOT button for safe mode...")
    time.sleep_ms(1500)

    if _safe_mode_requested():
        _safe_mode()
        return

    import recovery

    cause = machine.reset_cause()
    recovery.log("boot", "start, reset cause %s" % _cause_name(cause))
    # The fork also reports a brownout as PWRON_RESET, so a box that browns
    # out in a loop clears its count each time and is never stuck (known gap).
    power_on = cause == machine.PWRON_RESET
    try:
        action, reason = recovery.on_boot(power_on)
    except Exception as error:
        # Saving the boot count failed (flash full or broken): don't run blind.
        recovery.log("boot", "on_boot failed: %r" % (error,))
        action, reason = "stuck", "boot check failed"

    if action == "stuck":
        # Never reset from here: if recovery itself is what fails, a reset
        # would loop forever with no signal.
        recovery.stuck_forever(reason, rollback_trial=False)
        return

    dev = recovery.dev_mode()
    if dev:
        print("[BOOT] DEV MODE: watchdog and OTA off")
        recovery.log("boot", "DEV MODE: watchdog and OTA off")
    else:
        recovery.arm_watchdog()

    try:
        _run_app(recovery)
    finally:
        # Whatever happened, a box out of dev mode starts over.
        if not dev:
            machine.reset()

    print("[BOOT] Dev mode: staying at the REPL")


launch()
