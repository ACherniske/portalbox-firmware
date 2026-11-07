# core/main.py
# Main application entry point for PortalBox firmware

import gc
import sys
import uasyncio as asyncio

import recovery
from core.device import get_device, cleanup_device
from core.fsm import DeviceFSM
from net import ota


# The app counts as healthy once the FSM loop has run this long: the crash
# count is cleared and a trial OTA version is committed.
HEALTHY_AFTER_S = 60


def _healthy():
    recovery.mark_healthy()
    print("[APP] Healthy: crash count cleared")


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

        print("[APP] Application initialization complete")

    # =========================================================================
    # Main Runtime
    # =========================================================================

    async def run(self):
        """Run the main application loop."""

        self.running = True

        try:

            self.initialize()

            asyncio.create_task(self._ota_loop())

            print("[APP] Starting FSM...")
            await self.fsm.run(mark_healthy=_healthy,
                               healthy_after_ms=HEALTHY_AFTER_S * 1000)

        except KeyboardInterrupt:

            print("\n[APP] Keyboard interrupt received")
            raise

        except Exception as e:

            print("\n[APP] FATAL ERROR")
            print("[APP] %s: %s" % (
                type(e).__name__,
                e
            ))

            sys.print_exception(e)
            # Re-raise so main.py logs the crash and resets the box.
            raise

        finally:

            await self.shutdown()

    # =========================================================================
    # Idle update check
    # =========================================================================

    def _ota_check(self):
        """Update if due; a card put in stops the download."""

        result = ota.run_if_due(ota.load_config(), at_boot=False,
                                idle_ms=self.fsm.idle_ms(),
                                abort=self.device.card_present)
        if result not in ('off', 'dev mode', 'not due'):
            print("[APP] OTA: %s" % result)

    async def _ota_loop(self, every_ms=ota.CHECK_EVERY_S * 1000):
        """Ask for an update check every CHECK_EVERY_S while running."""

        while self.running:
            await asyncio.sleep_ms(every_ms)
            if self.running:
                self._ota_check()

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

        # Ensure asyncio event loop fully exits
        asyncio.new_event_loop()


# Run automatically if executed directly
if __name__ == "__main__":
    main()