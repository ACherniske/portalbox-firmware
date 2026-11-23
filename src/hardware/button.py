# hardware/button.py
# Simple button input module

from machine import Pin


class Button:
    """
    Simple GPIO button wrapper.

    Supports:
    - Reading pressed/released state
    - Internal pull-up resistor
    - Active-high or active-low buttons
    """

    def __init__(self, pin_num: int, pull_up: bool = True, active_high: bool = True):
        """
        Initialize button.

        Args:
            pin_num: GPIO pin number
            pull_up: Enable internal pull-up resistor
            active_high:
                True  -> pressed reads HIGH
                False -> pressed reads LOW
        """

        if pull_up:
            self.pin = Pin(pin_num, Pin.IN, Pin.PULL_UP)
        else:
            self.pin = Pin(pin_num, Pin.IN)

        self.active_high = active_high

    @property
    def value(self) -> int:
        """Raw GPIO value."""
        return self.pin.value()

    @property
    def is_pressed(self) -> bool:
        """Check if button is currently pressed."""
        state = self.pin.value()

        if self.active_high:
            return state == 1
        else:
            return state == 0

    @property
    def is_released(self) -> bool:
        """Check if button is currently released."""
        return not self.is_pressed
