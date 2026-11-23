# hardware/drivers/dotstar.py
# DotStar LED driver for MicroPython

from micropython import const

# Start and end frame constants
_START_FRAME = const(0x00)
_END_FRAME = const(0xFF)
_LED_START = const(0xE0)  # First 3 bits set to 1 (APA102 requirement)


class DotStar:
    """Low-level driver for DotStar (APA102) LED strips.

    The DotStar protocol requires:
    - Start frame: 4 bytes of 0x00
    - LED frames: 4 bytes per LED (0xE0 + brightness, blue, green, red)
    - End frame: a number of 0xFF bytes (see end frame sizing below)

    Colors are stored as RGB tuples in the public API; internally
    they are encoded BGR in the transmission buffer.
    """

    def __init__(self, spi, num_leds=10, brightness=31):
        """
        Initialize Dotstar driver.

        Args:
            spi: Initialized SPI bus object
            num_leds: Number of LEDs in the strip
            brightness: Global brightness level (0-31)
        """
        self.spi = spi
        self.num_leds = num_leds
        self._brightness = max(0, min(31, brightness))  # Clamp 0-31

        # End frame size as recommended for APA102: (n + 15) // 16
        self._end_bytes = (num_leds + 15) // 16

        # Preallocate full transmission buffer:
        # 4 bytes start + 4 * num_leds (per-LED) + end frame bytes
        total = 4 + (num_leds * 4) + self._end_bytes
        self._buf = bytearray(total)

        # Start frame (4 bytes of 0x00)
        for i in range(4):
            self._buf[i] = _START_FRAME

        # Initialize LED frames (header + B,G,R)
        for i in range(num_leds):
            base = 4 + (i * 4)
            self._buf[base] = _LED_START | self._brightness
            self._buf[base + 1] = 0
            self._buf[base + 2] = 0
            self._buf[base + 3] = 0

        # End frame bytes (0xFF)
        end_start = 4 + (num_leds * 4)
        for i in range(self._end_bytes):
            self._buf[end_start + i] = _END_FRAME

    @property
    def brightness(self):
        """Get current brightness level (0-31)."""
        return self._brightness

    @brightness.setter
    def brightness(self, value):
        """
        Set brightness level.

        Args:
            value: Brightness level (0-31), will be clamped to valid range
        """
        # Update internal brightness and rewrite all LED headers
        self._brightness = max(0, min(31, value))

        for i in range(self.num_leds):
            base = 4 + (i * 4)
            self._buf[base] = _LED_START | self._brightness

    def set_pixel(self, index, r, g, b):
        """
        Set color for a single pixel.

        Args:
            index: LED index (0 to num_leds-1)
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)
        """
        if 0 <= index < self.num_leds:
            base = 4 + (index * 4)

            # Header (brightness) + B, G, R
            self._buf[base] = _LED_START | self._brightness
            self._buf[base + 1] = b
            self._buf[base + 2] = g
            self._buf[base + 3] = r

    def get_pixel(self, index):
        """
        Get color for a single pixel.

        Args:
            index: LED index (0 to num_leds-1)

        Returns:
            Tuple of (r, g, b) or None if index invalid
        """
        if 0 <= index < self.num_leds:
            base = 4 + (index * 4)
            r = self._buf[base + 3]
            g = self._buf[base + 2]
            b = self._buf[base + 1]
            return (r, g, b)
        return None

    def fill(self, r, g, b):
        """
        Set all pixels to the same color.

        Args:
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)
        """
        for i in range(self.num_leds):
            self.set_pixel(i, r, g, b)

    def clear(self):
        """Turn off all LEDs (set to black)."""
        self.fill(0, 0, 0)

    def show(self):
        """Update LEDs with current buffer values."""
        # Single SPI write of the prebuilt buffer (start + led frames + end)
        self.spi.write(self._buf)

    def write(self):
        """Alias for show() for compatibility."""
        self.show()
