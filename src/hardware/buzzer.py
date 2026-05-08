# hardware/buzzer.py
# Active buzzer controller

from machine import Pin
import uasyncio as asyncio


class Buzzer:
    """Async active buzzer controller."""

    # Duration-only patterns (ms)
    BEEP_CARD_READ = (100,)
    BEEP_TIMEOUT = (300,)
    BEEP_TRIPLE = (100, 100, 100)

    def __init__(self, pin_num: int, enabled: bool = True):
        self.pin = Pin(pin_num, Pin.OUT)
        self.enabled = enabled
        self._current_task = None

        self.pin.off()

    async def beep(self, duration_ms: int):
        """Play a single beep."""
        if not self.enabled:
            return

        self.pin.on()
        await asyncio.sleep_ms(duration_ms)
        self.pin.off()

    async def _play_task(
        self,
        pattern,
        repeat=1,
        continuous=False,
        interval_ms=1000
    ):
        try:
            iterations = None if continuous else repeat
            count = 0

            while iterations is None or count < iterations:
                for duration in pattern:
                    await self.beep(duration)
                    await asyncio.sleep_ms(50)

                if continuous:
                    await asyncio.sleep_ms(interval_ms)

                count += 1

        except asyncio.CancelledError:
            self.pin.off()

    def play(
        self,
        pattern,
        repeat=1,
        continuous=False,
        interval_ms=1000
    ):
        self.stop()

        self._current_task = asyncio.create_task(
            self._play_task(
                pattern,
                repeat,
                continuous,
                interval_ms
            )
        )

    def stop(self):
        if self._current_task:
            self._current_task.cancel()
            self._current_task = None

        self.pin.off()
