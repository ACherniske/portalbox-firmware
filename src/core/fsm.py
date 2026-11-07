# core/fsm.py
# Finite State Machine for device operation

import time
import uasyncio as asyncio

from micropython import const

import recovery
from core.device import get_device
from core.feedback import apply_feedback
from config import settings

# Mock API layer
from core import api


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
    States.IDLE: 'idle',
    States.AUTH: 'auth',
    States.NOAUTH: 'noauth',
    States.GRACE: 'grace',
    States.PROXY: 'proxy',
    States.TRAINING: 'training',
    States.TIMEOUT: 'timeout'
}

# States in which the relay should be energized (equipment powered).
# This is an authorization decision the FSM owns directly — unlike
# LED/buzzer feedback, it is not cosmetic and does not belong in
# core/feedback.py's profile table.
_RELAY_ON_STATES = (
    States.AUTH,
    States.GRACE,
    States.PROXY,
    States.TRAINING
)


class DeviceFSM:
    """
    Device finite state machine.
    """

    def __init__(self):
        """Initialize FSM."""

        self.device = get_device()

        self.state = States.IDLE

        # Main authenticated user
        self.current_uid = None

        # Training session data
        self.trainer_uid = None
        self.trainee_uid = None

        self.state_start_time = time.ticks_ms()

        self.running = False

        # Require consecutive absent polls before treating card as removed.
        # With 100ms loop interval and value 3, removal must be absent ~300ms.
        self._card_absent_polls = 0
        self._card_absent_polls_required = 3

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _set_state(self, new_state):
        """
        Transition to a new state.

        Args:
            new_state: State enum value
        """

        if self.state == new_state:
            return

        old_state = self.state

        old_name = STATE_NAMES[old_state]
        new_name = STATE_NAMES[new_state]

        print(f"[FSM] State change: {old_name} -> {new_name}")

        # =========================================================
        # EXIT / CLEANUP EVENTS
        # =========================================================

        if new_state == States.IDLE:
            if self.current_uid:
                api.log_session_end(self.current_uid)

            self.current_uid = None
            self.trainer_uid = None
            self.trainee_uid = None

        # =========================================================
        # APPLY NEW STATE
        # =========================================================

        self.state = new_state

        self.state_start_time = time.ticks_ms()

        # Reset removal debounce when entering a new state.
        self._card_absent_polls = 0

        self._apply_state()

    def _state_elapsed(self):
        """Get milliseconds elapsed since entering current state."""

        return time.ticks_diff(
            time.ticks_ms(),
            self.state_start_time
        )

    def idle_ms(self):
        """Milliseconds since entering IDLE; 0 when not IDLE."""

        if self.state != States.IDLE:
            return 0
        return self._state_elapsed()

    def _device_timeout_expired(self):
        """Check if device timeout has expired."""

        device_timeout_ms = getattr(
            settings,
            'DEVICE_TIMEOUT_MS',
            getattr(settings, 'device_timeout_ms', 0)
        )

        return (
            self._state_elapsed()
            >= device_timeout_ms
        )

    def _grace_timeout_expired(self):
        """Check if grace timeout has expired."""

        grace_timeout_ms = getattr(
            settings,
            'GRACE_TIMEOUT_MS',
            getattr(settings, 'grace_period_ms', 0)
        )

        return (
            self._state_elapsed()
            >= grace_timeout_ms
        )

    def _card_removed(self, card_present):
        """Debounced card-removal detector for stable state transitions."""

        if card_present:
            self._card_absent_polls = 0
            return False

        self._card_absent_polls += 1

        return (
            self._card_absent_polls
            >= self._card_absent_polls_required
        )

    # =========================================================================
    # Hardware State Application
    # =========================================================================

    def _apply_state(self):
        """
        Apply hardware behavior for current state.

        Relay switching is an authorization decision the FSM makes
        directly. LED/buzzer feedback is cosmetic and is delegated to
        core/feedback.py, which is the single place that defines what
        each state "looks and sounds like" (shared with core/system.py
        for boot-phase narration).
        """

        if self.state in _RELAY_ON_STATES:
            self.device.relays_on()
        else:
            self.device.relays_off()

        apply_feedback(
            self.device,
            STATE_NAMES[self.state]
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

        return self.device.read_card()

    def _card_present(self):
        """Check if a card is currently present."""

        return self.device.card_present()

    # =========================================================================
    # API Hooks
    # =========================================================================

    def _is_auth(self, uid):
        """Check if UID is authorized."""

        return api.is_auth(uid)

    def _is_trainer(self, uid):
        """Check if UID is trainer card."""

        return api.is_trainer(uid)

    def _is_proxy(self, uid):
        """Check if UID is proxy card."""

        return api.is_proxy(uid)

    # =========================================================================
    # FSM Update Logic
    # =========================================================================

    async def update(self):
        """FSM update tick."""

        uid = self._read_card()
        card_present = self._card_present()

        # =========================================================
        # IDLE
        # =========================================================

        if self.state == States.IDLE:

            if uid:

                print(f"[FSM] Card detected: {uid}")

                # -------------------------------------------------
                # Authorized User
                # -------------------------------------------------

                if self._is_auth(uid):

                    self.current_uid = uid

                    api.log_session_start(uid)

                    self._set_state(
                        States.AUTH
                    )

                # -------------------------------------------------
                # Unauthorized User
                # -------------------------------------------------

                else:

                    api.log_session_fail(uid)

                    self._set_state(
                        States.NOAUTH
                    )

        # =========================================================
        # AUTH
        # =========================================================

        elif self.state == States.AUTH:

            if self._card_removed(card_present):

                self._set_state(
                    States.GRACE
                )

            elif self._device_timeout_expired():

                self._set_state(
                    States.TIMEOUT
                )

        # =========================================================
        # NOAUTH
        # =========================================================

        elif self.state == States.NOAUTH:

            if self._card_removed(card_present):

                self._set_state(
                    States.IDLE
                )

        # =========================================================
        # GRACE
        # =========================================================

        elif self.state == States.GRACE:

            # -----------------------------------------------------
            # Manual cancel button
            # -----------------------------------------------------

            if self.device.button_pressed:

                self._set_state(
                    States.IDLE
                )

            # -----------------------------------------------------
            # Grace timeout expired
            # -----------------------------------------------------

            elif self._grace_timeout_expired():

                self._set_state(
                    States.IDLE
                )

            # -----------------------------------------------------
            # Card scanned during grace
            # -----------------------------------------------------

            elif uid:

                print(f"[FSM] Grace card detected: {uid}")

                # =================================================
                # TRAINING FLOW
                #
                # Trainer authenticates first:
                #
                # Trainer card -> AUTH
                # Remove card -> GRACE
                # Trainee card -> TRAINING
                # =================================================

                if (
                    self.current_uid
                    and self._is_trainer(self.current_uid)
                ):

                    # Trainer tapped again: resume existing trainer session.
                    if uid == self.current_uid:

                        self._set_state(
                            States.AUTH
                        )

                    # Another authorized user tapped: hand off session.
                    elif self._is_auth(uid):

                        api.log_session_end(
                            self.current_uid
                        )

                        self.current_uid = uid

                        api.log_session_start(uid)

                        self._set_state(
                            States.AUTH
                        )

                    # Non-authorized card while trainer is active: training flow.
                    else:

                        self.trainer_uid = self.current_uid

                        self.trainee_uid = uid

                        api.log_training_start(
                            self.trainer_uid,
                            self.trainee_uid
                        )

                        self._set_state(
                            States.TRAINING
                        )

                # =================================================
                # PROXY FLOW
                # =================================================

                elif self._is_proxy(uid):

                    api.log_proxy_start(
                        uid,
                        self.current_uid
                    )

                    self._set_state(
                        States.PROXY
                    )

                # =================================================
                # NORMAL AUTH FLOW
                # =================================================

                elif self._is_auth(uid):
                    if self.current_uid and uid != self.current_uid:
                        api.log_session_end(self.current_uid)
                        api.log_session_start(uid)
                    elif not self.current_uid:
                        api.log_session_start(uid)

                    self.current_uid = uid
                    self._set_state(States.AUTH)

                # =================================================
                # UNAUTHORIZED
                # =================================================

                else:

                    api.log_session_fail(uid)

                    self._set_state(
                        States.NOAUTH
                    )

        # =========================================================
        # PROXY
        # =========================================================

        elif self.state == States.PROXY:

            if self._card_removed(card_present):

                self._set_state(
                    States.GRACE
                )

            elif self._device_timeout_expired():

                self._set_state(
                    States.TIMEOUT
                )

        # =========================================================
        # TRAINING
        # =========================================================

        elif self.state == States.TRAINING:

            if self._card_removed(card_present):

                self._set_state(
                    States.IDLE
                )

        # =========================================================
        # TIMEOUT
        # =========================================================

        elif self.state == States.TIMEOUT:

            if self._card_removed(card_present):

                self._set_state(
                    States.IDLE
                )

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def run(self, mark_healthy=None, healthy_after_ms=60000):
        """Run FSM loop.

        mark_healthy is called once, on the first loop pass at least
        healthy_after_ms after the start. It lives in the loop, not a timer,
        so a loop stuck in an await is never marked healthy.
        """

        print("[FSM] Starting main loop")

        self.running = True

        # Apply initial state
        self._apply_state()

        start = time.ticks_ms()

        while self.running:

            await self.update()

            recovery.feed()

            if mark_healthy is not None and                     time.ticks_diff(time.ticks_ms(), start) >= healthy_after_ms:
                mark_healthy()
                mark_healthy = None

            await asyncio.sleep_ms(100)

    def stop(self):
        """Stop FSM loop."""

        print("[FSM] Stopping main loop")

        self.running = False

        self.device.led_stop()
        self.device.led_clear()

        self.device.buzzer_stop()

        self.device.relays_off()

        print("[FSM] FSM stopped and hardware reset")
