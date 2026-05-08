# hardware/buzzer.py
# Buzzer control module

from machine import Pin, PWM
import uasyncio as asyncio


class Buzzer:
    """
    Non-blocking buzzer controller with pattern-based beeping support.
    
    Supports single beeps, repeated patterns, and continuous beeping.
    """

    # Common beep patterns (freq, duration_ms)
    BEEP_CARD_READ = ((1000, 100),)
    BEEP_TIMEOUT = ((1000, 300),)

    def __init__(self, pin_num: int, enabled: bool = True, volume: int = 50):
        """
        Initialize the buzzer.
        
        Args:
            pin_num: GPIO pin number for the buzzer
            enabled: Whether the buzzer is enabled
            volume: Volume level (0-100)
        """
        self.pwm = PWM(Pin(pin_num))
        self.enabled = enabled
        self.volume = volume
        self._current_task = None  # Track active beep task

    async def beep(self, frequency: int, duration_ms: int):
        """
        Play a single non-blocking beep.
        
        Args:
            frequency: Beep frequency in Hz
            duration_ms: Duration in milliseconds
        """
        if not self.enabled:
            return

        duty = int(1023 * self.volume / 100)
        self.pwm.freq(frequency)
        self.pwm.duty(duty)
        await asyncio.sleep_ms(duration_ms)
        self.pwm.duty(0)

    async def _play_task(
        self,
        pattern,
        repeat: int = 1,
        continuous: bool = False,
        interval_ms: int = 1000
    ):
        """
        Internal task to play beep patterns.
        
        Args:
            pattern: Tuple of (frequency, duration) pairs
            repeat: Number of times to repeat the pattern
            continuous: If True, play pattern continuously
            interval_ms: Interval between pattern repetitions (continuous mode)
        """
        try:
            iterations = None if continuous else repeat
            count = 0
            while iterations is None or count < iterations:
                for frequency, duration in pattern:
                    await self.beep(frequency, duration)
                    await asyncio.sleep_ms(50)
                if continuous:
                    await asyncio.sleep_ms(interval_ms)
                count += 1
        except asyncio.CancelledError:
            self.pwm.duty(0)  # Ensure buzzer is off on cancel

    def play(
        self,
        pattern,
        repeat: int = 1,
        continuous: bool = False,
        interval_ms: int = 1000
    ):
        """
        Start playing a beep pattern.
        
        Args:
            pattern: Tuple of (frequency, duration) pairs
            repeat: Number of times to repeat the pattern
            continuous: If True, play pattern until stop() is called
            interval_ms: Interval between pattern repetitions (continuous mode)
        """
        self.stop()
        self._current_task = asyncio.create_task(
            self._play_task(pattern, repeat, continuous, interval_ms)
        )

    def stop(self):
        """Cancel any active beeping and turn off the buzzer."""
        if self._current_task:
            self._current_task.cancel()
            self._current_task = None
        self.pwm.duty(0)  # Ensure buzzer is off
