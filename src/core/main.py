# core/main.py
# Main application entry point for PortalBox firmware

import gc
import sys
import uasyncio as asyncio

from core.device import get_device, cleanup_device
from core.fsm import DeviceFSM
from core.system import boot


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

        # Initialize device hardware
        self.device = get_device()

        self.device.print_status()

        # Run startup sequence
        self.device.startup_sequence()

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

            print("[APP] Starting FSM...")
            await self.fsm.run()

        except KeyboardInterrupt:

            print("\n[APP] Keyboard interrupt received")

        except Exception as e:

            print("\n[APP] FATAL ERROR")
            print("[APP] %s: %s" % (
                type(e).__name__,
                e
            ))

            sys.print_exception(e)

        finally:

            await self.shutdown()

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
