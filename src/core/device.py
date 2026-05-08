# core/device.py
# Hardware abstraction layer - manages access to hardware components with unified API

from machine import Pin, SPI
import gc
from config import pins, settings
from hardware.buzzer import Buzzer
from hardware.led import LEDController
from hardware.relay import Relay
from hardware.button import Button
from hardware.rfid import RFIDReader

class Device:
    """
    Central hardware manager

    Coordinates all hardware components and provides unified API for controlling them.
    """

    def __init__(self):
        """Initialize hardware abstraction layer."""
        print("[Device] Initializing hardware components...")

        # Communication buses
        self.spi = None
        self.spi_led = None

        # Hardware components
        self.buzzer = None
        self.relay = None
        self.rfid = None
        self.led = None
        self.button = None

        # Initialization
        self._init_buses()
        self._init_hardware()

        # cleanup
        gc.collect()
        print("[Device] Hardware initialization complete")

    def _init_buses(self):
        """Initialize communication buses."""

        # SPI bus for RFID
        try:
            self.spi = SPI(
                id = pins.SPI_ID,
                baudrate = pins.SPI_BAUDRATE,
                polarity = 0,
                phase = 0,
                sck = Pin(pins.SPI_SCK),
                mosi = Pin(pins.SPI_MOSI),
                miso = Pin(pins.SPI_MISO)
            )
            print("[Device] SPI bus 1 initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize SPI bus 1: {e}")
            self.spi = None

        # SPI bus for LEDs
        try:
            self.spi_led = SPI(
                id = pins.SPI_LED_ID,
                baudrate = pins.SPI_LED_BAUDRATE,
                polarity = 0,
                phase = 0,
                sck = Pin(pins.SPI_LED_SCK),
                mosi = Pin(pins.SPI_LED_MOSI),
                miso = Pin(pins.SPI_LED_MISO)
            )
            print("[Device] SPI bus 2 initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize SPI bus 2: {e}")
            self.spi_led = None

    def _init_hardware(self):
        """Initialize all hardware components."""

        if settings.ENABLE_BUZZER:
            self._init_buzzer()
        else:
            print("[Device] Buzzer disabled in settings")

        # Relay currently always enabled
        self._init_relay()

        if settings.ENABLE_LEDS:
            self._init_led()
        else:
            print("[Device] LEDs disabled in settings")

        if settings.ENABLE_RFID:
            self._init_rfid()
        else:
            print("[Device] RFID disabled in settings")

        if settings.ENABLE_BUTTONS:
            self._init_button()
        else:
            print("[Device] Buttons disabled in settings")

    def _init_button(self):
        """Initialize button."""
        try:
            self.button = Button(pins.BUTTON_PIN)
            print("[Device] Button initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize button: {e}")
            self.button = None

    def _init_buzzer(self):
        """Initialize buzzer."""
        try:
            self.buzzer = Buzzer(pins.BUZZER_PIN)
            print("[Device] Buzzer initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize buzzer: {e}")
            self.buzzer = None

    def _init_relay(self):
        """Initialize relay controller."""
        try:
            self.relay = Relay(
                pins.RELAY_AC_PIN,
                pins.RELAY_USB_PIN
            )
            print("[Device] Relay controller initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize relay controller: {e}")
            self.relay = None

    def _init_rfid(self):
        """Initialize RFID reader."""
        try:
            if self.spi is None:
                print("[Device] ERROR: Cannot initialize RFID reader without SPI bus")
                return

            self.rfid = RFIDReader(
                spi = self.spi,
                cs_pin = pins.RFID_CS,
                rst_pin = pins.RFID_RST,
                debounce_ms = settings.RFID_DEBOUNCE_MS
            )

            if self.rfid.is_initialized:
                print("[Device] RFID reader initialized")
            else:
                print("[Device] ERROR: RFID reader failed to initialize")
                self.rfid = None
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize RFID reader: {e}")
            self.rfid = None

    def _init_led(self):
        """Initialize LED controller."""
        try:
            if self.spi_led is None:
                print("[Device] ERROR: Cannot initialize LED controller without SPI bus")
                return

            # import appropriate LED driver based on settings
            if settings.LED_TYPE == "dotstar":
                from hardware.lib.dotstar import DotStar
                driver = DotStar(
                    spi = self.spi_led,
                    num_leds = settings.LED_COUNT,
                    brightness = settings.LED_BRIGHTNESS
                )

            elif settings.LED_TYPE == "neopixel":
                from hardware.lib.neopixel_wrapper import neopixel_wrapper
                driver = neopixel_wrapper(
                    pin = pins.SPI_LED_MOSI, # NeoPixel uses MOSI pin for data
                    num_pixels = settings.LED_COUNT,
                    brightness = settings.LED_BRIGHTNESS
                )

            else:
                print(f"[Device] ERROR: Unknown LED type '{settings.LED_TYPE}'")
                return

            # Wrap driver in LEDController for unified API
            self.led = LEDController(driver)

        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize LED controller: {e}")
            self.led = None

    # =========================================================================
    # High-level hardware control methods
    # =========================================================================

    def startup_sequence(self):
        """
        Run startup sequence with visual and audio feedback

        Call this after initialzation to indicate device is ready
        """
        print("[Device] Running startup sequence...")

        # Startup beep
        if self.buzzer:
            self.buzzer.play(
                (
                    100,
                    100,
                )
            )

        # Startup LED animation
        if self.led:
            self.led.stop()
            self.led.wipe(
                'BLUE',
                wait_ms = 30,
                clear_after = True
            )

        print("[Device] Startup sequence complete")

    def shutdown_sequence(self):
        """
        Run shutdown sequence with visual and audio feedback

        Call this before shutdown to indicate device is powering down
        """
        print("[Device] Running shutdown sequence...")

        # Shutdown LED sequence
        if self.led:
            self.led.stop()

            self.led.fill('RED')

        # Shutdown beep
        if self.buzzer:
            self.buzzer.play(
                (
                    300,
                )
            )

        # Turn off relays
        if self.relay:
            self.relay.turn_off()

        # Stop RFID crypto session
        if self.rfid:
            self.rfid.stop_crypto()

        # Fully clear LEDs after animation trigger
        if self.led:
            self.led.clear()

        # Ensure buzzer is silent
        if self.buzzer:
            self.buzzer.stop()

        print("[Device] Shutdown sequence complete")

    def cleanup(self):
        """
        Cleanup and deinitialize hardware components

        Call this when completely done with hardware
        """

        self.shutdown_sequence()

        # Deinitialize components
        if self.spi:
            self.spi.deinit()
            self.spi = None

        if self.spi_led:
            self.spi_led.deinit()
            self.spi_led = None

        gc.collect()
        print("[Device] Hardware cleanup complete")

    # =========================================================================
    # Component status methods
    # =========================================================================

    def get_status(self):
        """
        Get status of all hardware components.
        
        Returns:
            dict: Status of each component
        """
        return {
            'buzzer': self.buzzer is not None,
            'relay': self.relay is not None,
            'rfid': self.rfid is not None and self.rfid.is_initialized,
            'led': self.led is not None,
            'button': self.button is not None,
            'relay_state': self.relay.state if self.relay else None,
        }

    def print_status(self):
        """Print hardware status to console."""
        print("\n=== Hardware Status ===")
        status = self.get_status()

        for component, active in status.items():
            if component == 'relay_state':
                if active is not None:
                    print(f"  Relay: {'ON' if active else 'OFF'}")
            else:
                status_str = "OK" if active else "FAILED"
                print(f"  {component.upper()}: {status_str}")

        print("=====================\n")

# Singleton instance of Device
_device = None

def get_device():
    """Get the singleton Device instance, initializing if necessary."""
    global _device
    if _device is None:
        _device = Device()
    return _device

def cleanup_device():
    """Cleanup the singleton Device instance."""
    global _device
    if _device is not None:
        _device.cleanup()
        _device = None