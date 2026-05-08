# hardware/relay.py
# Relay control module

from machine import Pin


class Relay:
    """Control relay to turn equipment on and off."""

    def __init__(self, ac_relay_pin: int, usb_relay_pin: int):
        """
        Initialize relay controller.

        Args:
            ac_relay_pin: GPIO pin number for AC relay
            usb_relay_pin: GPIO pin number for USB relay
        """
        self.relay_ac = Pin(ac_relay_pin, Pin.OUT)
        self.relay_usb = Pin(usb_relay_pin, Pin.OUT)
        self._state = False
        self.turn_off()  # Ensure relays are off at start

    @property
    def state(self) -> bool:
        """Get the current relay state."""
        return self._state

    @property
    def is_on(self) -> bool:
        """Check if relays are currently on."""
        return self._state

    def turn_on(self):
        """Turn both relays on."""
        self.relay_ac.on()
        self.relay_usb.on()
        self._state = True

    def turn_off(self):
        """Turn both relays off."""
        self.relay_ac.off()
        self.relay_usb.off()
        self._state = False

    def toggle(self):
        """Toggle relays between on and off states."""
        if self._state:
            self.turn_off()
        else:
            self.turn_on()
