# hardware/drivers/neopixel.py 
# NeoPixel LED driver for MicroPython

from machine import Pin
import neopixel


class neopixel_wrapper:
    """NeoPixel driver wrapper with a DotStar-like API.

    This wrapper keeps an internal logical pixel buffer (`self._pixels`)
    of unscaled RGB values and writes a scaled output buffer to the
    underlying NeoPixel driver for efficient `show()` calls.
    Brightness is exposed as an integer 0-31 to match DotStar.
    """

    def __init__(self, pin, num_pixels, brightness=31):
        # Handle pin as number or Pin object
        if isinstance(pin, int):
            pin = Pin(pin, Pin.OUT)

        self.pin = pin
        self.num_pixels = num_pixels

        # Accept either 0-31 (int) or 0.0-1.0 (float) for compatibility
        if isinstance(brightness, float) and 0.0 <= brightness <= 1.0:
            self._brightness = int(brightness * 31)
        else:
            self._brightness = max(0, min(31, int(brightness)))

        # Logical pixel storage (unscaled RGB) so brightness changes can be
        # reapplied without losing original color values.
        self._pixels = bytearray(num_pixels * 3)

        # Initialize NeoPixel instance and try to use its internal buffer
        self.np = neopixel.NeoPixel(self.pin, num_pixels)

        # Some NeoPixel implementations expose `buf` (bytearray). Prefer
        # writing directly into that buffer when available.
        self._has_buf = hasattr(self.np, 'buf') and isinstance(self.np.buf, (bytearray, memoryview))
        if self._has_buf:
            self._out_buf = self.np.buf
        else:
            # Fallback output buffer; we'll write per-pixel into `self.np`
            self._out_buf = bytearray(num_pixels * 3)

        # Initialize buffers to zero
        for i in range(len(self._pixels)):
            self._pixels[i] = 0
        for i in range(len(self._out_buf)):
            self._out_buf[i] = 0

        # Clear on startup
        self.clear()
        self.show()

        self._initialized = True

    @property
    def initialized(self):
        return self._initialized

    @property
    def brightness(self):
        """Brightness as integer 0-31 to match DotStar."""
        return self._brightness

    @brightness.setter
    def brightness(self, value):
        # Accept float 0.0-1.0 or int 0-31
        if isinstance(value, float) and 0.0 <= value <= 1.0:
            self._brightness = int(value * 31)
        else:
            self._brightness = max(0, min(31, int(value)))

        # Reapply scaled values into output buffer
        self._apply_brightness_to_outbuf()

    def _apply_brightness_to_outbuf(self):
        """Scale logical pixels into the output buffer according to brightness."""
        if self._brightness == 31:
            # Max brightness: copy logical buffer to out buffer
            for i in range(self.num_pixels * 3):
                self._out_buf[i] = self._pixels[i]
        else:
            scale = self._brightness / 31.0
            for i in range(self.num_pixels):
                base = i * 3
                r = int(self._pixels[base] * scale)
                g = int(self._pixels[base + 1] * scale)
                b = int(self._pixels[base + 2] * scale)
                self._out_buf[base] = r
                self._out_buf[base + 1] = g
                self._out_buf[base + 2] = b

    def set_pixel(self, index, r, g, b):
        """Set a single pixel color (logical RGB)."""
        if 0 <= index < self.num_pixels:
            base = index * 3

            # Store logical (unscaled) color
            self._pixels[base] = max(0, min(255, int(r)))
            self._pixels[base + 1] = max(0, min(255, int(g)))
            self._pixels[base + 2] = max(0, min(255, int(b)))

            # Write scaled value into output buffer immediately
            if self._brightness == 31:
                self._out_buf[base] = self._pixels[base]
                self._out_buf[base + 1] = self._pixels[base + 1]
                self._out_buf[base + 2] = self._pixels[base + 2]
            else:
                scale = self._brightness / 31.0
                self._out_buf[base] = int(self._pixels[base] * scale)
                self._out_buf[base + 1] = int(self._pixels[base + 1] * scale)
                self._out_buf[base + 2] = int(self._pixels[base + 2] * scale)

            # If not using underlying buf, also write into self.np per-index
            if not self._has_buf:
                self.np[index] = (self._out_buf[base], self._out_buf[base + 1], self._out_buf[base + 2])

    def get_pixel(self, index):
        if 0 <= index < self.num_pixels:
            base = index * 3
            return (self._pixels[base], self._pixels[base + 1], self._pixels[base + 2])
        return None

    def fill(self, r, g, b):
        for i in range(self.num_pixels):
            self.set_pixel(i, r, g, b)

    def clear(self):
        self.fill(0, 0, 0)

    def show(self):
        """Write the output buffer to the hardware in a single operation when possible."""
        if self._has_buf:
            # If _out_buf references self.np.buf, ensure it's up to date then write
            if self._out_buf is not self.np.buf:
                # copy to hardware buffer
                self.np.buf[:] = self._out_buf
            self.np.write()
        else:
            # Fallback: per-pixel assignments already updated in set_pixel,
            # but ensure write is called to flush to LEDs
            self.np.write()

    def write(self):
        self.show()
