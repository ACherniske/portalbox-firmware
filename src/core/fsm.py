# core/fsm.py
# Finite State Machine for device operation

import time
import uasyncio as asyncio

from micropython import const
from core.device import get_device
from config import settings

# Future API layer
# from core.api import api


class States:
    """FSM state definitions."""
    IDLE = const(0)
    AUTH = const(1)
    NOAUTH = const(2)
    GRACE = const(3)
    PROXY = const(4)
    TRAINING = const(5)
    TIMEOUT = const(6)

STATE_NAMES = {
    States.IDLE: 'IDLE',
    States.AUTH: 'AUTH',
    States.NOAUTH: 'NOAUTH',
    States.GRACE: 'GRACE',
    States.PROXY: 'PROXY',
    States.TRAINING: 'TRAINING',
    States.TIMEOUT: 'TIMEOUT'
}


class DeviceFSM:
    """
    Device finite state machine.

    Handles
    - User authentication flow
    - Grace period logic
    - Timeout management
    - Relay control
    - LED state indication
    - Buzzer feedback
    Future:
    - API interactions for auth and logging
    """

    def __init__(self):
        """Initialize FSM"""
        self.device = get_device()

        self.state = States.IDLE

        self.current_uid = None

        self.state_start_time = time.ticks_ms()
        self.last_card_seen_time = time.ticks_ms()

        self.running = False

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _set_state(self, new_state):
        """Transition to a new state.
        
        Args:
            new_state: State enum value
        """
        if self.state == new_state:
            return

        old_name = STATE_NAMES[self.state]
        new_name = STATE_NAMES[new_state]

        print(f"[FSM] State change: {old_name} -> {new_name}")

        self.state = new_state
        self.state_start_time = time.ticks_ms()

        self._apply_state()

    def _state_elapsed(self):
        """Get milliseconds elapsed since entering current state."""
        return time.ticks_diff(
            time.ticks_ms(),
            self.state_start_time
        )

    def _device_timeout_expired(self):
        """Check if device timeout has expired."""
        return self._state_elapsed() >= settings.device_timeout_ms

    def _grace_timeout_expired(self):
        """Check if grace period has expired."""
        return self._state_elapsed() >= settings.grace_period_ms

    # =========================================================================
    # Hardware State Application
    # =========================================================================

    def _apply_state(self):
        """Apply hardware behavior for active state."""

        led = self.device.led
        buzzer = self.device.buzzer
        relay = self.device.relay

        # Stop previous animations/sounds
        if led:
            led.stop_animation()

        if buzzer:
            buzzer.stop()


        # =========================================================
        # IDLE
        # =========================================================

        if self.state == States.IDLE:

            if relay:
                relay.turn_off()

            if led:
                led.pulse(
                    'BLUE',
                    min_brightness = 0.05,
                    max_brightness = 0.3,
                    step = 0.02
                )

        # =========================================================
        # AUTH
        # =========================================================

        elif self.state == States.AUTH:

            if relay:
                relay.turn_on()

            if led:
                led.pulse(
                    'GREEN',
                    min_brightness = 0.05,
                    max_brightness = 0.4,
                    step = 0.02
                )

            if buzzer:
                buzzer.play(Buzzer.BEEP_CARD_READ)

        # =========================================================
        # NOAUTH
        # =========================================================

        elif self.state == States.NOAUTH:

            if relay:
                relay.turn_off()

            if led:
                led.pulse(
                    'RED',
                    min_brightness = 0.05,
                    max_brightness = 0.4,
                    step = 0.02
                )

            if buzzer:
                buzzer.play(Buzzer.BEEP_TIMEOUT)

        # =========================================================
        # GRACE
        # =========================================================

        elif self.state == States.GRACE:

            if relay:
                relay.turn_on()

            if led:
                led.blink(
                    'YELLOW',
                    on_ms = 500,
                    off_ms = 500
                )

            if buzzer:
                buzzer.play(
                    Buzzer.BEEP_CARD_READ,
                    continuous = True
                    interval_ms = 1000
                )

        # =========================================================
        # PROXY
        # =========================================================

        elif self.state == States.PROXY:

            if relay:
                relay.turn_on()

            if led:
                led.pulse(
                    'ORANGE',
                    min_brightness = 0.05,
                    max_brightness = 0.4,
                    step = 0.02
                )

            if buzzer:
                buzzer.play(Buzzer.BEEP_CARD_READ)

        # =========================================================
        # TRAINING
        # =========================================================

        elif self.state == States.TRAINING:

            if relay:
                relay.turn_on()

            if led:
                led.pulse(
                    'PURPLE',
                    min_brightness = 0.05,
                    max_brightness = 0.4,
                    step = 0.02
                )

            if buzzer:
                buzzer.play(Buzzer.BEEP_CARD_READ)

        # =========================================================
        # TIMEOUT
        # =========================================================

        elif self.state == States.TIMEOUT:

            if relay:
                relay.turn_off()

            if led:
                led.blink(
                    'RED',
                    on_ms = 250,
                    off_ms = 250
                )

            if buzzer:
                buzzer.play(
                    Buzzer.BEEP_TIMEOUT, 
                    continuous=True,
                    interval_ms=1000
                )

    # =========================================================================
    # Card Handling
    # =========================================================================

    def _read_card(self):
        """
        Read RFID card.

        Returns:
            UID string or None
        """
        if not self.device.rfid:
            return None

        return self.device.rfid.read_card()

    def _card_present(self):
        """Check if a card is currently present."""
        return self._read_card() is not None

    # =========================================================================
    # Future API Hooks
    # ========================================================================

    def _is_auth(self, uid):
        """Check if UID is authorized via API."""
        # Future implementation
        return False

    def _is_trainer(self, uid):
        """Check if UID is a trainer card via API."""
        # Future implementation
        return False

    def _is_proxy(self, uid):
        """Check if UID is a proxy card via API."""
        # Future implementation
        return False

    # =========================================================================
    # FSM Update Logic
    # =========================================================================

    async def update(self):
        """Self FSM update tick."""

        uid = self._read_card()

        # =========================================================
        # IDLE
        # =========================================================

        if self.state == States.IDLE:

            if uid:

                if self._is_auth(uid):
                    self.current_uid = uid
                    self._set_state(States.AUTH)

                else:
                    self._set_state(States.NOAUTH)


        # =========================================================
        # AUTH
        # =========================================================

        elif self.state == States.AUTH:

            if not uid:
                self._set_state(States.GRACE)

            elif self._device_timeout_expired():
                self._set_state(States.TIMEOUT)

        # =========================================================
        # NOAUTH
        # =========================================================

        elif self.state == States.NOAUTH:

            if not uid:
                self._set_state(States.IDLE)

        # =========================================================
        # GRACE
        # =========================================================

        elif self.state == States.GRACE:

            if self.device.button and self.device.button.is_pressed:
                self._set_state(States.IDLE)

            elif self._grace_timeout_expired():
                self._set_state(States.IDLE)

            elif uid:

                if self._is_proxy(uid):
                    self._set_state(States.PROXY)

                elif self._is_auth(uid):
                    
                    if self._is_trainer(uid):
                        self._set_state(States.TRAINING)

                    else:
                        self._set_state(States.AUTH)

                else:
                    self._set_state(States.NOAUTH)

        # =========================================================
        # PROXY
        # =========================================================

        elif self.state == States.PROXY:

            if not uid:
                self._set_state(States.GRACE)

            elif self._device_timeout_expired():
                self._set_state(States.TIMEOUT)

        # =========================================================
        # TRAINING
        # =========================================================

        elif self.state == States.TRAINING:

            if not uid:
                self._set_state(States.IDLE)

        # =========================================================
        # TIMEOUT
        # =========================================================

        elif self.state == States.TIMEOUT:

            if not uid:
                self._set_state(States.IDLE)

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def run(self):
        """Run FSM loop."""
        print("[FSM] Starting main loop")

        self.running = True

        self._apply_state()

        while self.running:
            await self.update()
            await asyncio.sleep_ms(100)

    def stop(self):
        """Stop FSM loop."""
        print("[FSM] Stopping main loop")
        self.running = False

        if self.device.led:
            self.device.led.stop()
            self.device.led.clear()

        if self.device.buzzer:
            self.device.buzzer.stop()

        print("[FSM] FSM stopped and hardware reset")
