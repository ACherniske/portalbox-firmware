import sys
from tests.test_helper import TestRunner

# Runs /boot.py and /main.py with a fake `machine` (no real pin is driven)
# and a stub `core.system` that only records whether the app was started.
# boot.py uses the real recovery anchor (imported with the fake machine);
# main.py gets a stub recovery so no test touches the box's real state.

PWRON_RESET = 1
SOFT_RESET = 5


class _FakePin:
    IN = 1
    OUT = 3
    PULL_UP = 2
    made = []

    def __init__(self, pin_id, mode=None, pull=None, value=None):
        self.pin_id = pin_id
        _FakePin.made.append((pin_id, mode, value))

    def value(self, *args):
        # Buttons not pressed: BOOT (GPIO0) is active-low, the box button active-high.
        return 1 if self.pin_id == 0 else 0


class _FakeMachine:
    Pin = _FakePin
    PWRON_RESET = PWRON_RESET

    def __init__(self, trace, cause=SOFT_RESET):
        self.trace = trace
        self.cause = cause

    def reset_cause(self):
        return self.cause

    def reset(self):
        self.trace.append(('reset',))


class _StubSystem:
    def __init__(self, trace=None, error=None):
        self.calls = 0
        self.trace = trace if trace is not None else []
        self.error = error

    def boot(self):
        self.calls += 1
        self.trace.append(('app',))
        if self.error is not None:
            raise self.error


class _StubRecovery:
    """Records what main.py asks of the anchor."""

    def __init__(self, trace, action=('run', ''), dev=False, boot_error=None,
                 log_fails=None):
        self.trace = trace
        self.action = action
        self.dev = dev
        self.boot_error = boot_error
        self.log_fails = log_fails  # tag whose log() raises MemoryError

    def on_boot(self, power_on):
        self.trace.append(('on_boot', power_on))
        if self.boot_error is not None:
            raise self.boot_error
        return self.action

    def stuck_forever(self, reason, rollback_trial=True):
        self.trace.append(('stuck', reason))
        self.rollback_trial = rollback_trial

    def dev_mode(self):
        return self.dev

    def arm_watchdog(self):
        self.trace.append(('arm',))

    def log(self, tag, msg):
        if tag == self.log_fails:
            raise MemoryError()
        self.trace.append(('log', tag, msg))

    def relays_low(self):
        self.trace.append(('relays_low',))


class _NoPins:
    pass


class _OtherPins:
    # Another box's wiring, as the flash tool would write it.
    BUTTON_PIN = 5
    RELAY_AC_PIN = 40
    RELAY_USB_PIN = 41


class _BrokenRecovery:
    def relays_low(self):
        raise MemoryError()


class _BadStr(Exception):
    """An error that can't even be turned into text (as when memory is out)."""

    def __str__(self):
        raise MemoryError()


_SAVED = ('machine', 'core.system', 'config.pins', 'recovery')


def _run_file(path, system, pins=None, machine=None, recovery=None):
    """Exec path; returns True if 'recovery' is still reachable afterwards
    (in sys.modules, or as a global the next file would share)."""
    saved = {name: sys.modules.get(name) for name in _SAVED}
    _FakePin.made = []
    try:
        import core  # noqa: F401  real package, so `core.system` resolves to the stub
        sys.modules['machine'] = machine if machine is not None else _FakeMachine([])
        sys.modules['core.system'] = system
        if pins is not None:
            sys.modules['config.pins'] = pins
        if recovery is not None:
            sys.modules['recovery'] = recovery
        else:
            # Import the real anchor fresh, so it picks up the fake machine.
            sys.modules.pop('recovery', None)
        with open(path) as source:
            code = source.read()
        scope = {'__name__': '__main__'}
        exec(code, scope)
        return 'recovery' in sys.modules or 'recovery' in scope
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


_LAST = {}


def _run_main(action=('run', ''), dev=False, error=None, cause=SOFT_RESET,
              boot_error=None, log_fails=None, pins=None):
    """Run /main.py; returns the trace of anchor, app and reset calls."""
    trace = []
    _LAST['recovery'] = _StubRecovery(trace, action, dev, boot_error, log_fails)
    _run_file('/main.py', _StubSystem(trace, error),
              machine=_FakeMachine(trace, cause),
              recovery=_LAST['recovery'], pins=pins)
    return trace


def _driven_low(pin_id):
    return any(p == pin_id and m == _FakePin.OUT and v == 0 for p, m, v in _FakePin.made)


def _names(trace):
    return [e[0] for e in trace if e[0] != 'log']


def _logged(trace, tag, text):
    return any(e[0] == 'log' and e[1] == tag and text in e[2] for e in trace)


def run():
    runner = TestRunner()

    def test_boot_does_not_start_app():
        system = _StubSystem()
        _run_file('/boot.py', system)
        assert system.calls == 0, "boot.py started the app (%d calls)" % system.calls

    def test_boot_drives_relays_low():
        _run_file('/boot.py', _StubSystem())
        assert _driven_low(18) and _driven_low(9), "relays not driven low: %r" % _FakePin.made

    def test_boot_relays_low_without_pins():
        _run_file('/boot.py', _StubSystem(), pins=_NoPins())
        assert _driven_low(18) and _driven_low(9), "fallback pins not driven low: %r" % _FakePin.made

    def test_boot_forgets_recovery():
        # Otherwise a tool that copies a new recovery.py in the same session
        # would import (and test) the old one boot.py loaded.
        loaded = _run_file('/boot.py', _StubSystem())
        assert not loaded, "boot.py left recovery reachable (sys.modules or global)"

    def test_normal_boot_starts_app_once():
        # Firmware order on a normal boot: boot.py, then main.py. The stub
        # returns like the real app does after Ctrl-C (it catches it).
        system = _StubSystem()
        _run_file('/boot.py', system)
        _run_file('/main.py', system, recovery=_StubRecovery([]))
        assert system.calls == 1, "normal boot started the app %d times" % system.calls

    def test_stuck_shows_signal_not_app():
        trace = _run_main(action=('stuck', 'crash loop'))
        assert _names(trace) == ['on_boot', 'stuck'], "stuck boot: %r" % trace
        assert ('stuck', 'crash loop') in trace, "reason: %r" % trace
        # on_boot itself failing (flash full) is stuck too, not a blind run.
        trace = _run_main(boot_error=OSError(28))
        assert _names(trace) == ['on_boot', 'stuck'], "on_boot error: %r" % trace
        assert _logged(trace, 'boot', 'on_boot failed'), "error not logged: %r" % trace

    def test_boot_check_stuck_no_rollback_reset():
        # Stuck from the boot check itself must not reset, or a broken
        # recovery during a trial would reset forever with no signal.
        _run_main(action=('stuck', 'install recovery failed'))
        assert _LAST['recovery'].rollback_trial is False, "boot-check stuck may reset"

    def test_safe_mode_button_from_config():
        _run_main(pins=_OtherPins())
        inputs = [p for p, m, v in _FakePin.made if m == _FakePin.IN]
        assert 5 in inputs, "config BUTTON_PIN 5 not read: %r" % (_FakePin.made,)

    def test_boot_survives_broken_recovery():
        for pins, want in ((_NoPins(), (18, 9)), (_OtherPins(), (40, 41))):
            try:
                _run_file('/boot.py', _StubSystem(), pins=pins, recovery=_BrokenRecovery())
                raised = None
            except BaseException as e:
                raised = e
            assert raised is None, "boot.py raised %r" % (raised,)
            for pin in want:
                assert _driven_low(pin), "relay %d not driven low: %r" % (pin, _FakePin.made)

    def test_watchdog_then_app_then_reset():
        trace = _run_main(error=ValueError('boom'))
        assert _names(trace) == ['on_boot', 'arm', 'app', 'reset'], "order: %r" % trace
        assert _logged(trace, 'crash', 'ValueError: boom'), "crash not logged: %r" % trace
        # An app that returns is reset too.
        trace = _run_main()
        assert _names(trace) == ['on_boot', 'arm', 'app', 'reset'], "app returned: %r" % trace

    def test_dev_mode_no_watchdog_no_reset():
        trace = _run_main(dev=True, error=ValueError('boom'))
        assert _names(trace) == ['on_boot', 'app'], "dev boot: %r" % trace
        assert _logged(trace, 'boot', 'DEV MODE'), "no dev banner logged: %r" % trace
        assert _logged(trace, 'crash', 'ValueError: boom'), "dev crash not logged: %r" % trace

    def test_interrupt_logged_then_reset():
        # Ctrl-C (a tool connecting) or SystemExit is not an Exception.
        for error in (KeyboardInterrupt(), SystemExit()):
            name = type(error).__name__
            trace = _run_main(error=error)
            assert _names(trace) == ['on_boot', 'arm', 'app', 'reset'], "%s: %r" % (name, trace)
            assert _logged(trace, 'boot', 'interrupted: ' + name), "%s not logged: %r" % (name, trace)
        trace = _run_main(dev=True, error=KeyboardInterrupt())
        assert _names(trace) == ['on_boot', 'app'], "dev Ctrl-C: %r" % trace

    def test_crash_log_failure_still_resets():
        trace = _run_main(error=ValueError('boom'), log_fails='crash')
        assert _names(trace)[-1] == 'reset', "log failure skipped reset: %r" % trace
        trace = _run_main(error=_BadStr())
        assert _names(trace)[-1] == 'reset', "unprintable error skipped reset: %r" % trace

    def test_dev_app_returns_to_repl():
        trace = _run_main(dev=True)
        assert _names(trace) == ['on_boot', 'app'], "dev return: %r" % trace
        assert _logged(trace, 'boot', 'app returned'), "return not logged: %r" % trace

    def test_power_on_flag():
        trace = _run_main(cause=PWRON_RESET)
        assert _names(trace)[0] == 'on_boot' and ('on_boot', True) in trace, "power-on: %r" % trace
        assert _logged(trace, 'boot', 'reset cause PWRON_RESET'), "cause not logged: %r" % trace
        trace = _run_main(cause=SOFT_RESET)
        assert ('on_boot', False) in trace, "soft reset: %r" % trace

    runner.run_test("boot.py does not start the app", test_boot_does_not_start_app)
    runner.run_test("boot.py drives both relays low", test_boot_drives_relays_low)
    runner.run_test("boot.py relays low without pins module", test_boot_relays_low_without_pins)
    runner.run_test("boot.py leaves no stale recovery module", test_boot_forgets_recovery)
    runner.run_test("Normal boot starts the app once", test_normal_boot_starts_app_once)
    runner.run_test("Stuck boot shows the signal, not the app", test_stuck_shows_signal_not_app)
    runner.run_test("Watchdog armed before app, reset after", test_watchdog_then_app_then_reset)
    runner.run_test("Boot-check stuck never resets", test_boot_check_stuck_no_rollback_reset)
    runner.run_test("Safe-mode button pin from config", test_safe_mode_button_from_config)
    runner.run_test("boot.py survives a broken recovery", test_boot_survives_broken_recovery)
    runner.run_test("Dev mode: no watchdog, no reset", test_dev_mode_no_watchdog_no_reset)
    runner.run_test("Reset cause logged, power-on reaches on_boot", test_power_on_flag)
    runner.run_test("Ctrl-C or SystemExit logged, then reset", test_interrupt_logged_then_reset)
    runner.run_test("Crash log failure still resets", test_crash_log_failure_still_resets)
    runner.run_test("Dev mode: app return stays at REPL", test_dev_app_returns_to_repl)

    runner.final_report()


if __name__ == "__main__":
    run()
