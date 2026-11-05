import sys
from tests.test_helper import TestRunner

# Runs /boot.py and /main.py with a fake `machine` (no real pin is driven)
# and a stub `core.system` that only records whether the app was started.


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


class _StubSystem:
    def __init__(self):
        self.calls = 0

    def boot(self):
        self.calls += 1


class _NoPins:
    pass


_SAVED = ('machine', 'core.system', 'config.pins')


def _run_file(path, system, pins=None):
    saved = {name: sys.modules.get(name) for name in _SAVED}
    _FakePin.made = []
    try:
        import core  # real package, so `core.system` resolves to the stub
        sys.modules['machine'] = _FakeMachine
        sys.modules['core.system'] = system
        if pins is not None:
            sys.modules['config.pins'] = pins
        with open(path) as source:
            code = source.read()
        exec(code, {'__name__': '__main__'})
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _driven_low(pin_id):
    return any(p == pin_id and m == _FakePin.OUT and v == 0 for p, m, v in _FakePin.made)


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

    def test_normal_boot_starts_app_once():
        # Firmware order on a normal boot: boot.py, then main.py. The stub
        # returns like the real app does after Ctrl-C (it catches it).
        system = _StubSystem()
        _run_file('/boot.py', system)
        _run_file('/main.py', system)
        assert system.calls == 1, "normal boot started the app %d times" % system.calls

    runner.run_test("boot.py does not start the app", test_boot_does_not_start_app)
    runner.run_test("boot.py drives both relays low", test_boot_drives_relays_low)
    runner.run_test("boot.py relays low without pins module", test_boot_relays_low_without_pins)
    runner.run_test("Normal boot starts the app once", test_normal_boot_starts_app_once)

    runner.final_report()


if __name__ == "__main__":
    run()
