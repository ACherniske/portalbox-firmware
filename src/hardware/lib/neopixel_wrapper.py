# hardware/drivers/neopixel.py
# NeoPixel LED driver for MicroPython

from machine import Pin
import neopixel


class neopixel_wrapper:
    """NeoPixel driver wrapper with a DotStar-like RGB API.

    Public API uses RGB ordering:
        set_pixel(index, r, g, b)

    Internal hardware transmission is converted to GRB ordering
    for WS2812/NeoPixel compatibility.
    """

    def __init__(self, pin, num_pixels, brightness=31):
        # Handle pin as number or Pin object
        if isinstance(pin, int):
            pin = Pin(pin, Pin.OUT)

        self.pin = pin
        self.num_pixels = num_pixels

        # Accept either 0-31 or 0.0-1.0
        if isinstance(brightness, float) and 0.0 <= brightness <= 1.0:
            self._brightness = int(brightness * 31)
        else:
            self._brightness = max(0, min(31, int(brightness)))

        # Store logical RGB values
        self._pixels = bytearray(num_pixels * 3)

        # Create NeoPixel object
        self.np = neopixel.NeoPixel(self.pin, num_pixels)

        # Use internal NeoPixel buffer if available
        self._has_buf = hasattr(self.np, 'buf') and isinstance(
            self.np.buf,
            (bytearray, memoryview)
        )

        if self._has_buf:
            self._out_buf = self.np.buf
        else:
            self._out_buf = bytearray(num_pixels * 3)

        # Initialize buffers
        for i in range(len(self._pixels)):
            self._pixels[i] = 0

        for i in range(len(self._out_buf)):
            self._out_buf[i] = 0

        # Clear LEDs
        self.clear()
        self.show()

        self._initialized = True

    @property
    def initialized(self):
        return self._initialized

    @property
    def brightness(self):
        return self._brightness

    @brightness.setter
    def brightness(self, value):
        if isinstance(value, float) and 0.0 <= value <= 1.0:
            self._brightness = int(value * 31)
        else:
            self._brightness = max(0, min(31, int(value)))

        self._apply_brightness_to_outbuf()

    def _write_scaled_pixel(self, index, r, g, b):
        """Write scaled RGB value into GRB hardware buffer."""
        base = index * 3

        # Convert RGB -> GRB
        self._out_buf[base] = g
        self._out_buf[base + 1] = r
        self._out_buf[base + 2] = b

    def _apply_brightness_to_outbuf(self):
        """Apply brightness scaling to all pixels."""
        scale = self._brightness / 31.0

        for i in range(self.num_pixels):
            base = i * 3

            r = int(self._pixels[base] * scale)
            g = int(self._pixels[base + 1] * scale)
            b = int(self._pixels[base + 2] * scale)

            self._write_scaled_pixel(i, r, g, b)

    def set_pixel(self, index, r, g, b):
        """Set a single pixel using RGB ordering."""
        if 0 <= index < self.num_pixels:
            base = index * 3

            # Store logical RGB values
            self._pixels[base] = max(0, min(255, int(r)))
            self._pixels[base + 1] = max(0, min(255, int(g)))
            self._pixels[base + 2] = max(0, min(255, int(b)))

            # Apply brightness scaling
            scale = self._brightness / 31.0

            sr = int(self._pixels[base] * scale)
            sg = int(self._pixels[base + 1] * scale)
            sb = int(self._pixels[base + 2] * scale)

            # Write RGB -> GRB
            self._write_scaled_pixel(index, sr, sg, sb)

            # Fallback mode without direct buffer access
            if not self._has_buf:
                self.np[index] = (sg, sr, sb)

    def get_pixel(self, index):
        """Get logical RGB pixel value."""
        if 0 <= index < self.num_pixels:
            base = index * 3
            return (
                self._pixels[base],
                self._pixels[base + 1],
                self._pixels[base + 2]
            )
        return None

    def fill(self, r, g, b):
        """Fill strip with RGB color."""
        for i in range(self.num_pixels):
            self.set_pixel(i, r, g, b)

    def clear(self):
        """Turn all LEDs off."""
        self.fill(0, 0, 0)

    def show(self):
        """Write buffer to LEDs."""
        if self._has_buf:
            if self._out_buf is not self.np.buf:
                self.np.buf[:] = self._out_buf

            self.np.write()
        else:
            self.np.write()

    def write(self):
        """Compatibility alias."""
        self.show()
