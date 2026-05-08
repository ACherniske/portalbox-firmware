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
        self._init_buzzer()
        self._init_relay()
        self._init_led()
        self._init_rfid()
        self._init_button()

    def _init_button(self):
        """Initialize button."""
        try:
            self.button = Button(pins.BUTTON)
            print("[Device] Button initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize button: {e}")
            self.button = None

    def _init_buzzer(self):
        """Initialize buzzer."""
        try:
            self.buzzer = Buzzer(pins.BUZZER)
            print("[Device] Buzzer initialized")
        except Exception as e:
            print(f"[Device] ERROR: Failed to initialize buzzer: {e}")
            self.buzzer = None

    def _init_relay(self):
        """Initialize relay controller."""
        try:
            self.relay = Relay(
                pins.AC_RELAY,
                pins.USB_RELAY
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

            if self.rfid.is_initialized():
                print("[Device] RFID reader initialized")
            else:
                print("[Device] ERROR: RFID reader failed to initialize")
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
