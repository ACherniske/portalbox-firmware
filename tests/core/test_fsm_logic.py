import time
import uasyncio as asyncio
from tests.test_helper import TestRunner
import core.fsm as fsm_mod
from core.fsm import States
from config import settings

# Cards: A, B authorized users; T trainer; P proxy; X, Y known cards with no
# authorization (a trainer can train them).
A, B, T, P, X, Y = (
    "AAAA0001",
    "BBBB0002",
    "TTTT0003",
    "PPPP0004",
    "XXXX0005",
    "YYYY0006",
)


class FakeApi:
    """Stands in for core.api and answers as the server does; every call
    lands in the shared events list."""

    def __init__(self, events):
        self.events = events
        self.users = {A, B, T}
        self.trainers = {T}
        self.proxies = {P}
        self.known = {A, B, T, X, Y}
        self.timeout_ms = None  # equipment timeout in the activate reply
        self.change_answer = None  # forces change_card's answer when set
        self.raise_on_deactivate = False
        self.clock = None  # set by Rig
        self.slow = {}  # call name -> ms the call takes

    def _took(self, name):
        if name in self.slow:
            self.clock.now = time.ticks_add(self.clock.now, self.slow[name])

    def activate(self, uid):
        self.events.append(("activate", uid))
        self._took("activate")
        if uid in self.users:
            return (True, self.timeout_ms)
        return (False, None)

    def change_card(self, activating, new):
        self.events.append(("change", activating, new))
        self._took("change")
        if self.change_answer is not None:
            return self.change_answer
        if new in self.proxies:
            return "proxy"
        if activating in self.trainers and new in self.known:
            return "training"
        return None

    def deactivate(self, uid):
        self.events.append(("deactivate", uid))
        self._took("deactivate")
        if self.raise_on_deactivate:
            raise OSError(110)
        return True

    def hold(self):
        self.events.append(("hold",))

    def release(self):
        self.events.append(("release",))

    def calls(self, name):
        return [e for e in self.events if e[0] == name]


class FakeDevice:
    """Relay state plus the card in the reader; LED/buzzer calls do nothing."""

    def __init__(self, events):
        self.events = events
        self.relay = False
        self.uid = None  # what read_card() returns on the next poll
        self.present = False
        self.button_pressed = False

    def relays_on(self):
        self.relay = True
        self.events.append(("relay", True))

    def relays_off(self):
        self.relay = False
        self.events.append(("relay", False))

    def read_card(self):
        uid, self.uid = self.uid, None  # the reader reports a card once
        return uid

    def card_present(self):
        return self.present

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class FakeTicks:
    def __init__(self):
        self.now = 1000
        self.ticks_add = time.ticks_add
        self.ticks_diff = time.ticks_diff

    def ticks_ms(self):
        return self.now


class Rig:
    def __init__(self):
        self.events = []
        self.api = FakeApi(self.events)
        self.dev = FakeDevice(self.events)
        self.clock = FakeTicks()
        self.api.clock = self.clock
        self.saved = [
            (fsm_mod, n, getattr(fsm_mod, n)) for n in ("api", "get_device", "time")
        ]
        fsm_mod.api = self.api
        fsm_mod.get_device = lambda: self.dev
        fsm_mod.time = self.clock
        self.fsm = fsm_mod.DeviceFSM()

    def undo(self):
        for obj, name, value in self.saved:
            setattr(obj, name, value)

    def tick(self, uid=None, present=None):
        """One FSM poll. uid: a card the reader reports now (it is then present)."""
        if uid is not None:
            self.dev.uid = uid
            self.dev.present = True
        if present is not None:
            self.dev.present = present
        asyncio.run(self.fsm.update())
        self.clock.now = time.ticks_add(self.clock.now, 100)

    def tap(self, uid):
        self.tick(uid)

    def remove(self):
        for _ in range(self.fsm._card_absent_polls_required):
            self.tick(present=False)

    def wait_ms(self, ms):
        self.clock.now = time.ticks_add(self.clock.now, ms)

    @property
    def state(self):
        return self.fsm.state


def run():
    runner = TestRunner()

    def rig_test(test):
        def wrapped():
            rig = Rig()
            try:
                test(rig)
            finally:
                rig.undo()

        return wrapped

    def relay_offs(r):
        return [i for i, e in enumerate(r.events) if e == ("relay", False)]

    # --- a session ends once, right after the relay goes off -----------------

    def test_relay_off_before_deactivate(r):
        r.tap(A)
        assert r.state == States.AUTH and r.dev.relay, "not in AUTH with relay on"
        r.remove()
        assert r.state == States.GRACE, "state %d after removal" % r.state
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.tick(present=False)
        assert r.state == States.IDLE, "state %d after grace" % r.state
        off = r.events.index(("relay", False))
        end = r.events.index(("deactivate", A))
        assert off < end, "deactivate before relay off: %r" % (r.events,)
        assert len(r.api.calls("deactivate")) == 1, "deactivates %r" % (
            r.api.calls("deactivate"),
        )

    def test_relay_off_when_deactivate_raises(r):
        r.tap(A)
        r.remove()
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.api.raise_on_deactivate = True
        try:
            r.tick(present=False)
        except OSError:
            pass
        assert r.dev.relay is False, "relay still on after a failed deactivate"

    def test_timeout_ends_session_once(r):
        r.tap(A)
        r.wait_ms(settings.DEVICE_TIMEOUT_MS)
        r.tick()
        assert r.state == States.TIMEOUT, "state %d" % r.state
        assert r.api.calls("deactivate") == [("deactivate", A)], "at TIMEOUT: %r" % (
            r.events,
        )
        assert relay_offs(r)[-1] < r.events.index(("deactivate", A)), "order %r" % (
            r.events,
        )
        r.remove()
        assert r.state == States.IDLE, "state %d after removal" % r.state
        assert len(r.api.calls("deactivate")) == 1, "repeated at IDLE: %r" % (r.events,)

    def test_button_in_grace_ends_session(r):
        r.tap(A)
        r.remove()
        r.dev.button_pressed = True
        r.tick(present=False)
        assert r.state == States.IDLE, "state %d" % r.state
        assert r.api.calls("deactivate") == [("deactivate", A)], "events %r" % (
            r.events,
        )

    def test_training_end_deactivates_trainer(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        r.remove()
        assert r.state == States.IDLE, "state %d" % r.state
        assert r.api.calls("deactivate") == [("deactivate", T)], "events %r" % (
            r.events,
        )

    # --- IDLE tap: the server decides -----------------------------------------

    def test_idle_tap_activates(r):
        r.tap(A)
        assert r.api.calls("activate") == [("activate", A)], "events %r" % (r.events,)
        assert r.state == States.AUTH and r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )

    def test_activate_refused_never_powers(r):
        r.tap(X)
        assert r.api.calls("activate") == [("activate", X)], "events %r" % (r.events,)
        assert r.state == States.NOAUTH, "state %d" % r.state
        assert ("relay", True) not in r.events, "relay went on: %r" % (r.events,)
        r.remove()
        assert r.state == States.IDLE, "state %d after removal" % r.state
        assert r.api.calls("deactivate") == [], "no session to end: %r" % (r.events,)

    # --- GRACE tap -------------------------------------------------------------

    def test_same_card_in_grace_no_call(r):
        r.tap(A)
        r.remove()
        before = len(r.events)
        r.tap(A)
        assert r.state == States.AUTH and r.dev.relay, "state %d" % r.state
        calls = [e for e in r.events[before:] if e[0] != "relay"]
        assert calls == [], "server called: %r" % (calls,)

    def test_activating_card_back_after_proxy(r):
        r.tap(A)
        r.remove()
        r.tap(P)
        r.remove()
        assert r.state == States.GRACE, "state %d" % r.state
        n = len(r.api.calls("change"))
        r.tap(A)
        assert r.state == States.AUTH, "state %d" % r.state
        assert len(r.api.calls("change")) == n, "change_card for the activating card"

    def test_grace_proxy(r):
        r.tap(A)
        r.remove()
        r.tap(P)
        assert ("change", A, P) in r.events, "events %r" % (r.events,)
        assert r.state == States.PROXY and r.dev.relay, "state %d" % r.state

    def test_grace_training(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        assert ("change", T, X) in r.events, "events %r" % (r.events,)
        assert r.state == States.TRAINING and r.dev.relay, "state %d" % r.state
        assert r.fsm.trainee_uid == X, "trainee %r" % r.fsm.trainee_uid

    def test_grace_user_answer(r):
        r.api.change_answer = "user"
        r.tap(A)
        r.remove()
        r.tap(B)
        assert r.state == States.AUTH and r.dev.relay, "state %d" % r.state
        assert r.api.calls("deactivate") == [], "session ended: %r" % (r.events,)
        r.remove()
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.tick(present=False)
        assert r.api.calls("deactivate") == [("deactivate", A)], (
            "ends the activating card's session: %r" % (r.events,)
        )

    def test_refused_hands_off_with_power(r):
        r.tap(A)
        r.remove()
        start = len(r.events)
        r.tap(B)
        tail = r.events[start:]
        calls = [e for e in tail if e[0] != "relay"]
        want = [
            ("hold",),
            ("change", A, B),
            ("deactivate", A),
            ("activate", B),
            ("release",),
        ]
        assert calls == want, "calls %r" % (calls,)
        assert ("relay", False) not in tail, "power cut in the hand-off: %r" % (tail,)
        assert r.state == States.AUTH and r.dev.relay, "state %d" % r.state
        assert r.fsm.current_uid == B, "session card %r" % r.fsm.current_uid
        r.remove()
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.tick(present=False)
        assert r.api.calls("deactivate")[-1] == ("deactivate", B), "events %r" % (
            r.events,
        )

    def test_hand_off_releases_when_a_call_raises(r):
        r.tap(A)
        r.remove()
        r.api.raise_on_deactivate = True
        try:
            r.tap(B)
        except OSError:
            pass
        assert r.events[-1] == ("release",), "connection kept: %r" % (r.events[-3:],)

    def test_refused_hand_off_to_refused_card(r):
        r.tap(A)
        r.remove()
        r.tap(X)
        assert r.state == States.NOAUTH and not r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )
        assert r.api.calls("deactivate") == [("deactivate", A)], "once: %r" % (
            r.events,
        )
        r.remove()
        assert r.state == States.IDLE, "state %d" % r.state
        assert r.api.calls("deactivate") == [("deactivate", A)], "repeated: %r" % (
            r.events,
        )

    def grace_runs_out_in_hand_off(r, slow_call):
        r.tap(A)
        r.remove()
        r.wait_ms(settings.GRACE_TIMEOUT_MS - 1000)
        r.api.slow[slow_call] = 2000
        r.tap(B)
        assert r.state == States.IDLE and not r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )
        assert ("activate", B) not in r.events, "new card activated after grace: %r" % (
            r.events,
        )
        assert r.api.calls("deactivate") == [("deactivate", A)], "deactivates %r" % (
            r.events,
        )

    def test_grace_out_during_change(r):
        grace_runs_out_in_hand_off(r, "change")
        assert relay_offs(r)[-1] < r.events.index(("deactivate", A)), (
            "deactivate before relay off: %r" % (r.events,)
        )

    def test_grace_out_during_deactivate(r):
        grace_runs_out_in_hand_off(r, "deactivate")

    # --- timeout: the server's, else the setting -------------------------------

    def test_training_times_out(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        assert r.state == States.TRAINING and r.dev.relay, "state %d, relay %s" % (
            r.state,
            r.dev.relay,
        )
        r.wait_ms(settings.DEVICE_TIMEOUT_MS)
        r.tick()
        assert r.state == States.TIMEOUT, "state %d after the device timeout" % r.state
        assert r.dev.relay is False, "relay on after the training timeout"

    def test_auth_times_out(r):
        # No timeout from the server -> the setting.
        r.tap(A)
        r.wait_ms(settings.DEVICE_TIMEOUT_MS - 200)
        r.tick()
        assert r.state == States.AUTH, "timed out early: state %d" % r.state
        r.wait_ms(200)
        r.tick()
        assert r.state == States.TIMEOUT and not r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )

    def test_server_timeout_used(r):
        r.api.timeout_ms = 120000
        r.tap(A)
        r.wait_ms(120000)
        r.tick()
        assert r.state == States.TIMEOUT and not r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )

    def test_server_timeout_zero_never(r):
        r.api.timeout_ms = 0
        r.tap(A)
        r.wait_ms(2 * settings.DEVICE_TIMEOUT_MS)
        r.tick()
        assert r.state == States.AUTH and r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )

    def test_server_timeout_per_session(r):
        r.api.timeout_ms = 0
        r.tap(A)
        r.remove()
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.tick(present=False)
        r.api.timeout_ms = None
        r.tap(B)
        r.wait_ms(settings.DEVICE_TIMEOUT_MS)
        r.tick()
        assert r.state == States.TIMEOUT, (
            "kept the last session's timeout: state %d" % r.state
        )

    def test_huge_server_timeout_capped(r):
        r.api.timeout_ms = 10000 * 60000  # past the ticks range (~6.2 days)
        r.tap(A)
        r.wait_ms(fsm_mod.SESSION_TIMEOUT_CAP_MS)
        r.tick()
        assert r.state == States.TIMEOUT and not r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )

    # --- a different card during a session is a grace tap ----------------------

    def test_swap_to_unauthorized_cuts_power(r):
        r.tap(A)
        r.tap(X)  # A slid out, X slid in within the removal debounce
        assert r.state == States.NOAUTH, "state %d after swap to X" % r.state
        assert r.dev.relay is False, "relay on with an unauthorized card"
        assert ("activate", X) in r.events, "X not checked: %r" % (r.events,)

    def test_swap_to_authorized_hands_off(r):
        r.tap(A)
        r.tap(B)
        assert r.state == States.AUTH and r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )
        assert r.fsm.current_uid == B, "session user %r" % r.fsm.current_uid
        assert r.events.index(("deactivate", A)) < r.events.index(("activate", B)), (
            "calls %r" % (r.events,)
        )

    def test_swap_in_proxy_cuts_power(r):
        r.tap(A)
        r.remove()
        r.tap(P)
        assert r.state == States.PROXY, "state %d after proxy tap" % r.state
        r.tap(X)
        assert r.state == States.NOAUTH and not r.dev.relay, "state %d relay %s" % (
            r.state,
            r.dev.relay,
        )

    def test_swap_in_training_to_new_trainee(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        r.tap(Y)  # trainee X swapped for another card
        assert r.state == States.TRAINING, "state %d" % r.state
        assert r.fsm.trainee_uid == Y, "trainee %r" % r.fsm.trainee_uid

    # A re-read of the card in the reader (debounce ran out) changes nothing:
    # no server call, no relay switching, same state.

    def reread_changes_nothing(r, uid, state):
        before = list(r.events)
        r.tap(uid)
        assert r.state == state, "state %d after re-read" % r.state
        assert r.events == before, "re-read added %r" % (r.events[len(before) :],)

    def test_same_card_reread_keeps_session(r):
        r.tap(A)
        reread_changes_nothing(r, A, States.AUTH)

    def test_proxy_card_reread_keeps_proxy(r):
        r.tap(A)
        r.remove()
        r.tap(P)
        reread_changes_nothing(r, P, States.PROXY)

    def test_trainee_reread_keeps_training(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        reread_changes_nothing(r, X, States.TRAINING)

    tests = [
        ("Relay off before deactivate", test_relay_off_before_deactivate),
        ("Relay off when deactivate raises", test_relay_off_when_deactivate_raises),
        ("TIMEOUT ends the session once", test_timeout_ends_session_once),
        ("Button in GRACE ends the session", test_button_in_grace_ends_session),
        (
            "Training end deactivates the trainer's card",
            test_training_end_deactivates_trainer,
        ),
        ("IDLE tap activates", test_idle_tap_activates),
        ("Refused activate never powers the tool", test_activate_refused_never_powers),
        ("Same card in GRACE: no server call", test_same_card_in_grace_no_call),
        (
            "Activating card back after PROXY: no change_card",
            test_activating_card_back_after_proxy,
        ),
        ("GRACE tap: proxy -> PROXY", test_grace_proxy),
        ("GRACE tap: training -> TRAINING", test_grace_training),
        ("GRACE tap: user -> AUTH, same session", test_grace_user_answer),
        (
            "Refused change_card hands off with power on",
            test_refused_hands_off_with_power,
        ),
        (
            "Hand-off to a refused card: NOAUTH, one deactivate",
            test_refused_hand_off_to_refused_card,
        ),
        (
            "Hand-off releases the connection when a call raises",
            test_hand_off_releases_when_a_call_raises,
        ),
        (
            "Grace runs out during change_card: power off, no activate",
            test_grace_out_during_change,
        ),
        (
            "Grace runs out during deactivate: power off, no activate",
            test_grace_out_during_deactivate,
        ),
        ("TRAINING ends on the device timeout", test_training_times_out),
        ("AUTH ends on the setting timeout", test_auth_times_out),
        ("Server timeout used", test_server_timeout_used),
        ("Server timeout 0: no limit", test_server_timeout_zero_never),
        ("Server timeout is per session", test_server_timeout_per_session),
        ("Huge server timeout capped", test_huge_server_timeout_capped),
        (
            "Swap to an unauthorized card cuts power",
            test_swap_to_unauthorized_cuts_power,
        ),
        ("Swap to an authorized card hands off", test_swap_to_authorized_hands_off),
        ("Swap during PROXY cuts power", test_swap_in_proxy_cuts_power),
        (
            "Swap during TRAINING starts a new trainee",
            test_swap_in_training_to_new_trainee,
        ),
        ("Session card re-read keeps the session", test_same_card_reread_keeps_session),
        ("Proxy card re-read keeps PROXY", test_proxy_card_reread_keeps_proxy),
        ("Trainee card re-read keeps TRAINING", test_trainee_reread_keeps_training),
    ]
    for name, test in tests:
        runner.run_test(name, rig_test(test))

    runner.final_report()


if __name__ == "__main__":
    run()
