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
                pins.SPI_ID,
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
                pins.SPI_LED_ID,
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

            print("[Device] LED controller initialized")

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
            self.buzzer.play(Buzzer.BEEP_TRIPLE)

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
            self.buzzer.play(Buzzer.BEEP_TIMEOUT)

        # Turn off relays
        if self.relay:
            self.relay.turn_off()

        # Stop RFID crypto session
        if self.rfid:
            self.rfid.stop_crypto()

        # Clear LEDs
        if self.led:
            self.led.clear()

        # Stop buzzer
        if self.buzzer:
            self.buzzer.stop()

        print("[Device] Shutdown sequence complete")

    def cleanup(self):
        """
        Cleanup and deinitialize hardware components

        Call this when completely done with hardware
        """

        self.shutdown_sequence()

        # Deinitialize SPI buses
        if self.spi:
            self.spi.deinit()
            self.spi = None

        if self.spi_led:
            self.spi_led.deinit()
            self.spi_led = None

        # Clear references
        self.buzzer = None
        self.relay = None
        self.rfid = None
        self.led = None
        self.button = None

        gc.collect()

        print("[Device] Hardware cleanup complete")

    # =========================================================================
    # Unified Hardware API
    # =========================================================================

    def relays_on(self):
        """Turn on all relays."""
        if self.relay:
            self.relay.turn_on()

    def relays_off(self):
        """Turn off all relays."""
        if self.relay:
            self.relay.turn_off()

    @property
    def relays_enabled(self):
        """Get relay state"""
        if self.relay:
            return self.relay.state
        return None

    def led_fill(self, color):
        """Fill all LEDs with a color."""
        if self.led:
            self.led.fill(color)

    def led_clear(self):
        """Turn off all LEDs."""
        if self.led:
            self.led.clear()

    def led_stop(self):
        """Stop any active LED animations."""
        if self.led:
            self.led.stop()

    def led_pulse(
        self,
        color,
        min_brightness = 0.05,
        max_brightness = 0.4,
        step = 0.02,
        interval_ms = 50
    ):
        """Start a pulsing animation on the LEDs."""
        if self.led:
            self.led.pulse(
                color,
                min_brightness = min_brightness,
                max_brightness = max_brightness,
                step = step,
                interval_ms = interval_ms
            )

    def led_blink(
        self,
        color,
        count = None,
        on_ms = 250,
        off_ms = 250
    ):
        """Start a blinking animation on the LEDs."""
        if self.led:
            self.led.blink(
                color,
                count = count,
                on_ms = on_ms,
                off_ms = off_ms
            )

    def led_chase(
        self,
        color,
        background = 'OFF',
        wait_ms = 50,
        continuous = True
    ):
        """Start a chasing animation on the LEDs."""
        if self.led:
            self.led.chase(
                color,
                background = background,
                wait_ms = wait_ms,
                continuous = continuous
            )

    def led_wipe(
        self,
        color,
        wait_ms = 50,
        clear_after = False
    ):
        """Start a wiping animation on the LEDs."""
        if self.led:
            self.led.wipe(
                color,
                wait_ms = wait_ms,
                clear_after = clear_after
            )

    def led_rainbow(
        self,
        wait_ms = 50,
        continuous = True
    ):
        """Start a rainbow animation on the LEDs."""
        if self.led:
            self.led.rainbow(
                wait_ms = wait_ms,
                continuous = continuous
            )

    def buzzer_play(
        self,
        pattern,
        repeat = 1,
        continuous = False,
        interval_ms = 1000
    ):
        """Play a buzzer pattern."""
        if self.buzzer:
            self.buzzer.play(
                pattern,
                repeat = repeat,
                continuous = continuous,
                interval_ms = interval_ms
            )

    def buzzer_stop(self):
        """Stop any buzzer patterns."""
        if self.buzzer:
            self.buzzer.stop()

    def buzzer_card_read(self):
        """Play card read buzzer pattern."""
        if self.buzzer:
            self.buzzer.play(Buzzer.BEEP_CARD_READ)

    def buzzer_timeout(self):
        """Play timeout buzzer pattern."""
        if self.buzzer:
            self.buzzer.play(Buzzer.BEEP_TIMEOUT)

    def buzzer_triple(self):
        """Play triple beep buzzer pattern."""
        if self.buzzer:
            self.buzzer.play(Buzzer.BEEP_TRIPLE)

    def buzzer_card_repeat(self, interval_ms = 1000):
        if self.buzzer:
            self.buzzer.play(
                Buzzer.BEEP_CARD_READ,
                continuous = True,
                interval_ms = interval_ms
            )

    def buzzer_timeout_repeat(self, interval_ms = 1000):
        if self.buzzer:
            self.buzzer.play(
                Buzzer.BEEP_TIMEOUT,
                continuous = True,
                interval_ms = interval_ms
            )

    @property
    def button_pressed(self):
        """Check if button is currently pressed."""
        if self.button:
            return self.button.is_pressed

        return False

    def read_card(self):
        """Read RFID card UID."""
        if self.rfid:
            return self.rfid.read_card()

        return None

    def stop_crypto(self):
        """Stop RFID crypto session."""
        if self.rfid:
            self.rfid.stop_crypto()

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
