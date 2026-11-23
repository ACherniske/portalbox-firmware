# core/main.py
# Main application entry point for PortalBox firmware

import gc
import sys
import uasyncio as asyncio

import recovery
import core.device as device_mod
from core import api
from core.device import get_device, cleanup_device
from core.fsm import DeviceFSM


# The app counts as healthy once the FSM loop has run this long: the crash
# count is cleared and a trial OTA version is committed.
HEALTHY_AFTER_S = 60

# Without these the box can't do its job, so the run is not healthy.
_NEEDED_HARDWARE = ("rfid", "relay")


class Application:
    """
    Main application controller.

    Responsible for:
    - Hardware initialization
    - FSM lifecycle
    - Graceful shutdown
    - Fatal error handling
    """

    def __init__(self):
        """Initialize application."""

        print("\n" + "=" * 60)
        print("PortalBox Firmware - Main Application")
        print("=" * 60)

        self.device = None
        self.fsm = None

        self.running = False

    # =========================================================================
    # Initialization
    # =========================================================================

    def initialize(self):
        """Initialize hardware and FSM."""

        print("[APP] Initializing application...")

        gc.collect()

        self.device = get_device()

        self.device.print_status()

        # Initialize FSM
        self.fsm = DeviceFSM()

        self._report_startup()

        print("[APP] Application initialization complete")

    def _report_startup(self):
        """Tell the server the box is up and log its answer. Informational
        only: a failure never stops the box, since every card tap asks the
        server again."""

        try:
            result = api.startup()
        except Exception as e:
            result = "error %s" % (repr(e),)
        print("[APP] Server startup report: %s" % result)
        recovery.log("app", "server startup: %s" % result)

    # =========================================================================
    # Main Runtime
    # =========================================================================

    async def run(self):
        """Run the main application loop."""

        self.running = True

        try:
            self.initialize()

            # Ready cue only now: startup work done, cards next.
            if self.device:
                self.device.startup_sequence()

            asyncio.create_task(self._ota_loop())

            print("[APP] Starting FSM...")
            await self.fsm.run(
                mark_healthy=self._healthy, healthy_after_ms=HEALTHY_AFTER_S * 1000
            )

        except KeyboardInterrupt:
            print("\n[APP] Keyboard interrupt received")
            raise

        except Exception as e:
            print("\n[APP] FATAL ERROR")
            print("[APP] %s: %s" % (type(e).__name__, e))

            sys.print_exception(e)
            # Re-raise so main.py logs the crash and resets the box.
            raise

        finally:
            await self.shutdown()

    def _healthy(self):
        """Clear the crash count and commit a trial version, but only when
        the card reader and relay started: a version that breaks them must
        not be kept."""
        status = self.device.get_status()
        failed = [name for name in _NEEDED_HARDWARE if not status.get(name)]
        if failed:
            recovery.log("app", "not healthy: %s failed" % ", ".join(failed))
            print("[APP] Not healthy: %s failed" % ", ".join(failed))
            return
        recovery.mark_healthy()
        print("[APP] Healthy: crash count cleared")

    # =========================================================================
    # Idle update check
    # =========================================================================

    def _ota_check(self):
        """Update if due; a card put in stops the download."""

        from net import ota

        result = ota.run_if_due(
            ota.load_config(),
            at_boot=False,
            idle_ms=self.fsm.idle_ms(),
            abort=self.device.card_present,
        )
        if result not in ("off", "dev mode", "not due"):
            print("[APP] OTA: %s" % result)

    async def _ota_loop(self, every_ms=None):
        """Ask for an update check every CHECK_EVERY_S while running."""

        if every_ms is None:
            from net import ota

            every_ms = ota.CHECK_EVERY_S * 1000

        while self.running:
            await asyncio.sleep_ms(every_ms)
            if self.running:
                # uasyncio drops a task's exception silently: without this,
                # one failed check would end idle updates until the next boot.
                try:
                    self._ota_check()
                except Exception as e:
                    recovery.log("ota", "idle check failed: %r" % (e,))
                    print("[APP] OTA idle check failed: %r" % (e,))

    # =========================================================================
    # Shutdown
    # =========================================================================

    async def shutdown(self):
        """Gracefully shutdown application."""

        print("\n[APP] Shutting down application...")

        self.running = False

        try:
            # Stop FSM
            if self.fsm:
                self.fsm.stop()

            await asyncio.sleep_ms(100)

            # Cleanup hardware
            cleanup_device()

            gc.collect()

            print("[APP] Shutdown complete")

        except Exception as e:
            print("[APP] ERROR during shutdown: %s" % e)
            sys.print_exception(e)


# =========================================================================
# Entry Point
# =========================================================================


async def _main():
    """Async wrapper for application."""

    app = Application()

    await app.run()


def main():
    """Synchronous entry point."""

    try:
        asyncio.run(_main())

    finally:
        _power_off()

        # Ensure asyncio event loop fully exits
        asyncio.new_event_loop()


def _power_off():
    """Relays off, whatever stopped the app. A Ctrl-C that lands between
    tasks skips Application.shutdown(). Never starts the hardware: if
    shutdown already cleaned up, there is nothing to do."""
    device = device_mod._device
    if device is None:
        return
    try:
        device.relays_off()
    finally:
        cleanup_device()


# Run automatically if executed directly
if __name__ == "__main__":
    main()
