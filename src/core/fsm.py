# core/fsm.py
# Finite State Machine for device operation

import time
import uasyncio as asyncio

from micropython import const

import recovery
from core.device import get_device
from core.feedback import apply_feedback
from config import settings

# Maker Portal server (or the test cards when the box config says so)
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

# idle_ms() stops growing here (1 day): well past the 10 idle minutes an
# update check needs, well short of the ~6.2-day ticks wrap.
IDLE_MS_CAP = 24 * 3600 * 1000

# Longest session timeout ticks_diff can measure (2**29 ms, ~6.2 days), less
# an hour so a slow poll can't step past it. Longer server timeouts end here.
SESSION_TIMEOUT_CAP_MS = (1 << 29) - 3600 * 1000

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

# States with a card in the reader that holds the session.
_SESSION_STATES = (
    States.AUTH,
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

        # The card that started the server session (None: no session)
        self.current_uid = None

        # That session's timeout in ms from the server: 0 = no limit,
        # None = settings.DEVICE_TIMEOUT_MS
        self.session_timeout_ms = None

        # The card whose tap started the current state (the one in the reader)
        self.card_uid = None

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

        # Power going off ends the server session (once).
        ended_uid = None

        if old_state in _RELAY_ON_STATES and new_state not in _RELAY_ON_STATES:
            ended_uid = self.current_uid
            self._end_session()

        if new_state == States.IDLE:
            self.card_uid = None

        # =========================================================
        # APPLY NEW STATE
        # =========================================================

        self.state = new_state

        self.state_start_time = time.ticks_ms()

        # Reset removal debounce when entering a new state.
        self._card_absent_polls = 0

        self._apply_state()

        # Tell the server only after the relay is off: a slow or failing
        # server call must never keep the tool powered.
        if ended_uid:
            api.deactivate(ended_uid)

    def _state_elapsed(self):
        """Get milliseconds elapsed since entering current state."""

        return time.ticks_diff(
            time.ticks_ms(),
            self.state_start_time
        )

    def idle_ms(self):
        """Milliseconds since entering IDLE, at most IDLE_MS_CAP; 0 when not IDLE.

        ticks_diff only reaches 2**29 ms (~6.2 days), so past the cap the
        start time moves forward and the value stays at the cap.
        """

        if self.state != States.IDLE:
            return 0
        elapsed = self._state_elapsed()
        if elapsed < 0 or elapsed > IDLE_MS_CAP:
            self.state_start_time = time.ticks_add(time.ticks_ms(), -IDLE_MS_CAP)
            elapsed = IDLE_MS_CAP
        return elapsed

    def _device_timeout_expired(self):
        """Check if device timeout has expired."""

        device_timeout_ms = self.session_timeout_ms

        # The server's 0 means no limit.
        if device_timeout_ms == 0:
            return False

        if device_timeout_ms is None:
            device_timeout_ms = getattr(
                settings,
                'DEVICE_TIMEOUT_MS',
                getattr(settings, 'device_timeout_ms', 0)
            )

        return (
            self._state_elapsed()
            >= min(device_timeout_ms, SESSION_TIMEOUT_CAP_MS)
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
    # Server Sessions
    # =========================================================================

    def _start_session(self, uid):
        """Ask the server to start a session for the card: AUTH or NOAUTH."""

        ok, timeout_ms = api.activate(uid)

        if ok:
            self.current_uid = uid
            self.session_timeout_ms = timeout_ms
            self._set_state(States.AUTH)
        else:
            self._set_state(States.NOAUTH)

    def _end_session(self):
        """Forget the session; the caller tells the server."""

        self.current_uid = None
        self.session_timeout_ms = None
        self.trainer_uid = None
        self.trainee_uid = None

    def _other_card_in_grace(self, uid):
        """A card other than the session's during grace: the server says
        what it may do."""

        mode = api.change_card(self.current_uid, uid)

        if mode == 'proxy':

            self._set_state(
                States.PROXY
            )

        # Known server limit: the server says 'training' for any user card
        # when the session's user may train, even one authorized on its own,
        # so a trainer cannot hand off to an authorized user.
        elif mode == 'training':

            self.trainer_uid = self.current_uid
            self.trainee_uid = uid

            self._set_state(
                States.TRAINING
            )

        elif mode == 'user':

            self._set_state(
                States.AUTH
            )

        # Refused, or no answer: hand off. The old session ends and the new
        # card gets its own; power stays on until the server refuses the new
        # card. A slow server must not stretch grace: once it runs out, power
        # off (IDLE).
        else:

            if self._grace_timeout_expired():
                self._set_state(States.IDLE)
                return

            old_uid = self.current_uid
            self._end_session()
            api.deactivate(old_uid)

            if self._grace_timeout_expired():
                self._set_state(States.IDLE)
                return

            self._start_session(uid)

    # =========================================================================
    # FSM Update Logic
    # =========================================================================

    async def update(self):
        """FSM update tick."""

        uid = self._read_card()
        card_present = self._card_present()

        # A different card during a session: the session card left and a new
        # one came in faster than the removal debounce. Handle it as a tap
        # during grace, in this same poll (GRACE branch below).
        if uid and self.state in _SESSION_STATES and uid != self.card_uid:
            print(f"[FSM] Card swapped during session: {uid}")
            self._set_state(States.GRACE)

        # =========================================================
        # IDLE
        # =========================================================

        if self.state == States.IDLE:

            if uid:

                print(f"[FSM] Card detected: {uid}")

                self.card_uid = uid

                self._start_session(uid)

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

                self.card_uid = uid

                # The card that started the session: resume, no server call.
                if uid == self.current_uid:

                    self._set_state(
                        States.AUTH
                    )

                    return

                # Another card: up to three server calls, on one connection.
                api.hold()
                try:
                    self._other_card_in_grace(uid)
                finally:
                    api.release()

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

            elif self._device_timeout_expired():

                self._set_state(
                    States.TIMEOUT
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
