# hardware/drivers/dotstar.py
# DotStar LED driver for MicroPython

import gc
from micropython import const

# Start and end frame constants
_START_FRAME = const(0x00)
_END_FRAME = const(0xFF)
_LED_START = const(0xE0)  # First 3 bits set to 1 (APA102 requirement)

class DotStar:
    """
    Low-level driver for DotStar APA102 LED strips.

    The DotStar protocol requires:
    - Start frame: 4 bytes of 0x00
    - LED frames: 4 bytes per LED (0xE0 + brightness, blue, green, red)
    - End frame: 4 bytes of 0xFF

    Colors are stored in RGB format and converted to GBR for transmission.
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
        self._leds = [(0, 0, 0)] * num_leds  # RGB format

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
        self._brightness = max(0, min(31, value))

    def _write_bytes(self, data):
        """
        Write data to SPI bus.

        Args:
            data: Byte array to write
        """
        self.spi.write(bytearray(data))

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
            self._leds[index] = (r, g, b)

    def get_pixel(self, index):
        """
        Get color for a single pixel.

        Args:
            index: LED index (0 to num_leds-1)

        Returns:
            Tuple of (r, g, b) or None if index invalid
        """
        if 0 <= index < self.num_leds:
            return self._leds[index]
        return None

    def fill(self, r, g, b):
        """
        Set all pixels to the same color.

        Args:
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)
        """
        self._leds = [(r, g, b)] * self.num_leds

    def clear(self):
        """Turn off all LEDs (set to black)."""
        self.fill(0, 0, 0)

    def show(self):
        """Update LEDs with current buffer values."""
        # Start frame
        self._write_bytes([_START_FRAME] * 4)

        # LED frames (convert RGB to GBR for APA102)
        for r, g, b in self._leds:
            self._write_bytes([
                _LED_START | self._brightness,
                b,  # Blue
                g,  # Green
                r   # Red
            ])

        # End frame
        self._write_bytes([_END_FRAME] * 4)

        # Collect garbage
        gc.collect()

    def write(self):
        """Alias for show() for compatibility."""
        self.show()
