"""LED control module with async animations."""

import uasyncio as asyncio


# Color constants (RGB format)
COLORS = {
    'OFF': (0, 0, 0),
    'RED': (255, 0, 0),
    'GREEN': (0, 255, 0),
    'BLUE': (0, 0, 255),
    'WHITE': (255, 255, 255),
    'ORANGE': (255, 115, 0),
    'YELLOW': (255, 255, 0),
    'PURPLE': (128, 0, 128),
    'CYAN': (0, 255, 255),
    'MAGENTA': (255, 0, 255),
}


class LEDController:
    """
    Async LED controller with non-blocking animations.

    Works with any LED driver that implements:
    - fill(r, g, b) or fill(color_tuple)
    - set_pixel(index, r, g, b) or set_pixel(index, color_tuple)
    - show()
    - brightness property (optional)
    - num_leds or num_pixels attribute
    """

    def __init__(self, driver):
        """
        Initialize async LED controller.

        Args:
            driver: LED driver instance (Dotstar, NeoPixel, etc.)
        """
        self.driver = driver
        print(f"[LED] Initialized with driver: {type(driver).__name__}")

        # Handle different attribute names
        if hasattr(driver, 'num_leds'):
            self.num_leds = driver.num_leds
        elif hasattr(driver, 'num_pixels'):
            self.num_leds = driver.num_pixels
        else:
            self.num_leds = 10  # Default fallback

        # Animation state
        self._current_task = None
        self._animation_running = False

    @property
    def brightness(self):
        """Get current brightness."""
        return getattr(self.driver, 'brightness', 1.0)

    @brightness.setter
    def brightness(self, value):
        """Set brightness on the active driver.

        Both supported drivers expose a `brightness` property. DotStar uses
        integer 0-31 brightness; NeoPixel wrapper also accepts integer 0-31.
        """
        if isinstance(value, float):
            # Normalize float values into DotStar-style range.
            value = int(max(0.0, min(1.0, value)) * 31)
        else:
            value = max(0, min(31, int(value)))

        self.driver.brightness = value

    def _normalize_color(self, color):
        """
        Normalize color to RGB tuple.

        Args:
            color: RGB tuple, color name string, or None

        Returns:
            RGB tuple (r, g, b)
        """
        if color is None:
            return (0, 0, 0)
        if isinstance(color, str):
            return COLORS.get(color.upper(), COLORS['OFF'])
        return color

    def _fill_driver(self, color):
        """Fill all LEDs using the driver fill method."""
        r, g, b = color
        self.driver.fill(r, g, b)

    def _set_pixel_driver(self, index, color):
        """Set pixel using the driver set_pixel method."""
        r, g, b = color
        self.driver.set_pixel(index, r, g, b)

    def _show(self):
        """Update LED display."""
        self.driver.show()

    def stop(self):
        """Stop current animation."""
        if self._current_task:
            self._current_task.cancel()
            self._current_task = None
        self._animation_running = False

    def fill(self, color):
        """
        Fill all LEDs with a color (synchronous).

        Args:
            color: RGB tuple (r, g, b) or color name string
        """
        self.stop()  # Stop any running animation
        color = self._normalize_color(color)
        self._fill_driver(color)
        self._show()

    def clear(self):
        """Turn off all LEDs."""
        self.fill(COLORS['OFF'])

    def set_pixel(self, index, color):
        """
        Set a single pixel color.

        Args:
            index: LED index
            color: RGB tuple (r, g, b) or color name string
        """
        color = self._normalize_color(color)
        self._set_pixel_driver(index, color)

    def show(self):
        """Update LED display."""
        self._show()

    async def _blink_task(self, color, count, on_ms, off_ms):
        """
        Internal blink task.

        Args:
            color: RGB tuple
            count: Number of blinks (None for infinite)
            on_ms: On duration in milliseconds
            off_ms: Off duration in milliseconds
        """
        try:
            self._animation_running = True
            iterations = 0

            while count is None or iterations < count:
                # Turn on
                self._fill_driver(color)
                self._show()
                await asyncio.sleep_ms(on_ms)

                # Turn off
                self._fill_driver((0, 0, 0))
                self._show()
                await asyncio.sleep_ms(off_ms)

                iterations += 1

        except asyncio.CancelledError:
            self._fill_driver((0, 0, 0))
            self._show()
        finally:
            self._animation_running = False

    def blink(self, color, count=None, on_ms=250, off_ms=250):
        """
        Start blinking animation (non-blocking).

        Args:
            color: RGB tuple (r, g, b) or color name string
            count: Number of blinks (None for infinite)
            on_ms: On duration in milliseconds
            off_ms: Off duration in milliseconds
        """
        self.stop()
        color = self._normalize_color(color)
        self._current_task = asyncio.create_task(
            self._blink_task(color, count, on_ms, off_ms)
        )

    async def _pulse_task(self, color, min_brightness, max_brightness,
                          step, interval_ms):
        """
        Internal pulse task.

        Args:
            color: RGB tuple
            min_brightness: Minimum brightness
            max_brightness: Maximum brightness
            step: Brightness change per step
            interval_ms: Time between steps in milliseconds
        """
        try:
            self._animation_running = True
            brightness = min_brightness
            rising = True

            while True:
                # Update brightness
                if rising:
                    brightness += step
                    if brightness >= max_brightness:
                        brightness = max_brightness
                        rising = False
                else:
                    brightness -= step
                    if brightness <= min_brightness:
                        brightness = min_brightness
                        rising = True

                # Apply brightness and show
                self.brightness = brightness
                self._fill_driver(color)
                self._show()

                await asyncio.sleep_ms(interval_ms)

        except asyncio.CancelledError:
            pass
        finally:
            self._animation_running = False

    def pulse(self, color, min_brightness=0.1, max_brightness=1.0,
              step=0.05, interval_ms=50):
        """
        Start pulsing animation (non-blocking).

        Args:
            color: RGB tuple (r, g, b) or color name string
            min_brightness: Minimum brightness (0.0-1.0 or 0-31 for Dotstar)
            max_brightness: Maximum brightness (0.0-1.0 or 0-31 for Dotstar)
            step: Brightness change per step
            interval_ms: Time between steps in milliseconds
        """
        self.stop()
        color = self._normalize_color(color)
        self._current_task = asyncio.create_task(
            self._pulse_task(color, min_brightness, max_brightness,
                             step, interval_ms)
        )

    async def _rainbow_task(self, wait_ms, continuous):
        """
        Internal rainbow cycle task.

        Args:
            wait_ms: Delay between updates in milliseconds
            continuous: If True, loop continuously
        """
        try:
            self._animation_running = True

            while True:
                for j in range(256):
                    for i in range(self.num_leds):
                        pixel_index = (i * 256 // self.num_leds) + j
                        color = self._wheel(pixel_index & 255)
                        self._set_pixel_driver(i, color)
                    self._show()
                    await asyncio.sleep_ms(wait_ms)

                if not continuous:
                    break

        except asyncio.CancelledError:
            pass
        finally:
            self._animation_running = False

    def rainbow(self, wait_ms=20, continuous=True):
        """
        Start rainbow animation (non-blocking).

        Args:
            wait_ms: Delay between updates in milliseconds
            continuous: If True, loop continuously
        """
        self.stop()
        self._current_task = asyncio.create_task(
            self._rainbow_task(wait_ms, continuous)
        )

    def _wheel(self, pos):
        """
        Generate rainbow colors across 0-255 positions.

        Args:
            pos: Position in color wheel (0-255)

        Returns:
            RGB tuple
        """
        if pos < 85:
            return (pos * 3, 255 - pos * 3, 0)
        elif pos < 170:
            pos -= 85
            return (255 - pos * 3, 0, pos * 3)
        else:
            pos -= 170
            return (0, pos * 3, 255 - pos * 3)

    async def _chase_task(self, color, background, wait_ms, continuous):
        """
        Internal chase effect task.

        Args:
            color: Chase color RGB tuple
            background: Background color RGB tuple
            wait_ms: Delay between steps in milliseconds
            continuous: If True, loop continuously
        """
        try:
            self._animation_running = True

            while True:
                for i in range(self.num_leds):
                    self._fill_driver(background)
                    self._set_pixel_driver(i, color)
                    self._show()
                    await asyncio.sleep_ms(wait_ms)

                if not continuous:
                    break

        except asyncio.CancelledError:
            pass
        finally:
            self._animation_running = False

    def chase(self, color, background='OFF', wait_ms=50, continuous=True):
        """
        Start chase effect (non-blocking).

        Args:
            color: Chase color (RGB tuple or color name)
            background: Background color (RGB tuple or color name)
            wait_ms: Delay between steps in milliseconds
            continuous: If True, loop continuously
        """
        self.stop()
        color = self._normalize_color(color)
        background = self._normalize_color(background)
        self._current_task = asyncio.create_task(
            self._chase_task(color, background, wait_ms, continuous)
        )

    async def _wipe_task(self, color, wait_ms, clear_after):
        """
        Internal wipe effect task.

        Args:
            color: Wipe color RGB tuple
            wait_ms: Delay between steps in milliseconds
            clear_after: If True, clear LEDs after wipe
        """
        try:
            self._animation_running = True

            for i in range(self.num_leds):
                self._set_pixel_driver(i, color)
                self._show()
                await asyncio.sleep_ms(wait_ms)

            if clear_after:
                await asyncio.sleep_ms(wait_ms * 2)
                self._fill_driver((0, 0, 0))
                self._show()

        except asyncio.CancelledError:
            pass
        finally:
            self._animation_running = False

    def wipe(self, color, wait_ms=50, clear_after=False):
        """
        Start wipe effect (non-blocking).

        Args:
            color: Wipe color (RGB tuple or color name)
            wait_ms: Delay between steps in milliseconds
            clear_after: If True, clear LEDs after wipe completes
        """
        self.stop()
        color = self._normalize_color(color)
        self._current_task = asyncio.create_task(
            self._wipe_task(color, wait_ms, clear_after)
        )

    @property
    def is_animating(self):
        """Check if an animation is currently running."""
        return self._animation_running

    async def wait_animation(self):
        """Wait for current animation to complete."""
        if self._current_task:
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass
