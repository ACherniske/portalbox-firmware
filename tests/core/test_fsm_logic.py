import time
import uasyncio as asyncio
from tests.test_helper import TestRunner
import core.fsm as fsm_mod
from core.fsm import States
from config import settings

# Cards: A, B authorized users; T trainer; P proxy; X unauthorized.
A, B, T, P, X = 'AAAA0001', 'BBBB0002', 'TTTT0003', 'PPPP0004', 'XXXX0005'


class FakeApi:
    """Stands in for core.api; every log call lands in the shared events list."""

    def __init__(self, events):
        self.events = events
        self.auth = {A, B, T, P}
        self.trainers = {T}
        self.proxies = {P}
        self.raise_on_end = False

    def is_auth(self, uid):
        return uid in self.auth

    def is_trainer(self, uid):
        return uid in self.trainers

    def is_proxy(self, uid):
        return uid in self.proxies

    def log_session_start(self, uid):
        self.events.append(('start', uid))

    def log_session_end(self, uid):
        self.events.append(('end', uid))
        if self.raise_on_end:
            raise OSError(110)

    def log_session_fail(self, uid):
        self.events.append(('fail', uid))

    def log_training_start(self, trainer, trainee):
        self.events.append(('training', trainer, trainee))

    def log_proxy_start(self, proxy, user):
        self.events.append(('proxy', proxy, user))


class FakeDevice:
    """Relay state plus the card in the reader; LED/buzzer calls do nothing."""

    def __init__(self, events):
        self.events = events
        self.relay = False
        self.uid = None          # what read_card() returns on the next poll
        self.present = False
        self.button_pressed = False

    def relays_on(self):
        self.relay = True
        self.events.append(('relay', True))

    def relays_off(self):
        self.relay = False
        self.events.append(('relay', False))

    def read_card(self):
        uid, self.uid = self.uid, None   # the reader reports a card once
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
        self.saved = [(fsm_mod, n, getattr(fsm_mod, n)) for n in ('api', 'get_device', 'time')]
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

    # --- item 1: power off before the session-end log ------------------------

    def test_relay_off_before_end_log(r):
        r.tap(A)
        assert r.state == States.AUTH and r.dev.relay, "not in AUTH with relay on"
        r.remove()
        assert r.state == States.GRACE, "state %d after removal" % r.state
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.tick(present=False)
        assert r.state == States.IDLE, "state %d after grace" % r.state
        off = r.events.index(('relay', False))
        end = r.events.index(('end', A))
        assert off < end, "end log before relay off: %r" % (r.events,)

    def test_relay_off_when_end_log_raises(r):
        r.tap(A)
        r.remove()
        r.wait_ms(settings.GRACE_TIMEOUT_MS)
        r.api.raise_on_end = True
        try:
            r.tick(present=False)
        except OSError:
            pass
        assert r.dev.relay is False, "relay still on after a failed end log"

    # --- item 2: TRAINING ends on the device timeout -------------------------

    def test_training_times_out(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        assert r.state == States.TRAINING and r.dev.relay, "state %d, relay %s" % (r.state, r.dev.relay)
        r.wait_ms(settings.DEVICE_TIMEOUT_MS)
        r.tick()
        assert r.state == States.TIMEOUT, "state %d after the device timeout" % r.state
        assert r.dev.relay is False, "relay on after the training timeout"

    def test_auth_times_out(r):
        # Guards the existing AUTH timeout while TRAINING gets one.
        r.tap(A)
        r.wait_ms(settings.DEVICE_TIMEOUT_MS)
        r.tick()
        assert r.state == States.TIMEOUT and not r.dev.relay, "state %d relay %s" % (r.state, r.dev.relay)

    # --- item 3: a different card during a session is a grace tap ------------

    def test_swap_to_unauthorized_cuts_power(r):
        r.tap(A)
        r.tap(X)   # A slid out, X slid in within the removal debounce
        assert r.state == States.NOAUTH, "state %d after swap to X" % r.state
        assert r.dev.relay is False, "relay on with an unauthorized card"
        assert ('fail', X) in r.events, "no failed-access log: %r" % (r.events,)

    def test_swap_to_authorized_hands_off(r):
        r.tap(A)
        r.tap(B)
        assert r.state == States.AUTH and r.dev.relay, "state %d relay %s" % (r.state, r.dev.relay)
        assert r.fsm.current_uid == B, "session user %r" % r.fsm.current_uid
        assert r.events.index(('end', A)) < r.events.index(('start', B)), "logs %r" % (r.events,)

    def test_swap_in_proxy_cuts_power(r):
        r.tap(A)
        r.remove()
        r.tap(P)
        assert r.state == States.PROXY, "state %d after proxy tap" % r.state
        r.tap(X)
        assert r.state == States.NOAUTH and not r.dev.relay, "state %d relay %s" % (r.state, r.dev.relay)

    def test_swap_in_training_to_new_trainee(r):
        r.tap(T)
        r.remove()
        r.tap(X)
        r.tap('YYYY0006')   # trainee X swapped for another unauthorized card
        assert r.state == States.TRAINING, "state %d" % r.state
        assert r.fsm.trainee_uid == 'YYYY0006', "trainee %r" % r.fsm.trainee_uid

    # A re-read of the card in the reader (debounce ran out) changes nothing:
    # no new log, no relay switching, same state.

    def reread_changes_nothing(r, uid, state):
        before = list(r.events)
        r.tap(uid)
        assert r.state == state, "state %d after re-read" % r.state
        assert r.events == before, "re-read added %r" % (r.events[len(before):],)

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
        ("Relay off before the session-end log", test_relay_off_before_end_log),
        ("Relay off when the end log raises", test_relay_off_when_end_log_raises),
        ("TRAINING ends on the device timeout", test_training_times_out),
        ("AUTH ends on the device timeout", test_auth_times_out),
        ("Swap to an unauthorized card cuts power", test_swap_to_unauthorized_cuts_power),
        ("Swap to an authorized card hands off", test_swap_to_authorized_hands_off),
        ("Swap during PROXY cuts power", test_swap_in_proxy_cuts_power),
        ("Swap during TRAINING starts a new trainee", test_swap_in_training_to_new_trainee),
        ("Session card re-read keeps the session", test_same_card_reread_keeps_session),
        ("Proxy card re-read keeps PROXY", test_proxy_card_reread_keeps_proxy),
        ("Trainee card re-read keeps TRAINING", test_trainee_reread_keeps_training),
    ]
    for name, test in tests:
        runner.run_test(name, rig_test(test))

    runner.final_report()


if __name__ == "__main__":
    run()
