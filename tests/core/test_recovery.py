import os
import sys
from tests.test_helper import TestRunner
import recovery


# Every path is moved under ROOT so the suite never touches the box's real
# state, journal or log (see tests/net/test_clock.py for why that matters).
ROOT = '/t_rec'

_REDIRECTS = {
    'STATE_PATH': ROOT + '/ota/state.json',
    'JOURNAL_PATH': ROOT + '/ota/journal.json',
    'PREV_DIR': ROOT + '/ota/prev',
    'STAGE_DIR': ROOT + '/ota/stage',
    'LOG_PATH': ROOT + '/log/events.log',
    'DEV_FLAG_PATH': ROOT + '/config/dev_mode',
    'LIVE_ROOT': ROOT + '/live',
}


def _is_dir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _rm_tree(path):
    if _is_dir(path):
        for name in os.listdir(path):
            _rm_tree(path + '/' + name)
        try:
            os.rmdir(path)
        except OSError:
            pass
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def _mkdirs(path):
    built = ''
    for part in path.strip('/').split('/'):
        built += '/' + part
        try:
            os.mkdir(built)
        except OSError:
            pass


def _write(path, text):
    _mkdirs(path.rsplit('/', 1)[0])
    with open(path, 'w') as f:
        f.write(text)


def _read(path):
    try:
        with open(path, 'r') as f:
            return f.read()
    except OSError:
        return None


def _size(path):
    return os.stat(path)[6]


class _FakeMachine:
    """Stands in for recovery's machine module: records every Pin and SPI
    call so the tests never switch the real relays, LEDs or buzzer."""

    def __init__(self):
        self.events = []      # ('init', pin, mode, value) or ('set', pin, value)
        self.spi_writes = []  # bytes of each SPI write
        machine_ = self

        class Pin:
            OUT = 1
            IN = 2
            PULL_UP = 3

            def __init__(self, num, mode=-1, pull=-1, value=None):
                self.num = num
                machine_.events.append(('init', num, mode, value))

            def on(self):
                machine_.events.append(('set', self.num, 1))

            def off(self):
                machine_.events.append(('set', self.num, 0))

            def value(self, v=None):
                if v is not None:
                    machine_.events.append(('set', self.num, 1 if v else 0))

        class SPI:
            def __init__(self, *args, **kwargs):
                pass

            def write(self, buf):
                machine_.spi_writes.append(bytes(buf))

        self.Pin = Pin
        self.SPI = SPI

    def level(self, pin):
        """Last level driven on pin, or None if never driven."""
        got = None
        for e in self.events:
            if e[0] == 'init' and e[1] == pin and e[3] is not None:
                got = e[3]
            elif e[0] == 'set' and e[1] == pin:
                got = e[2]
        return got

    def inits(self, pin):
        return [e for e in self.events if e[0] == 'init' and e[1] == pin]

    def ons(self, pin):
        return len([e for e in self.events if e == ('set', pin, 1)])


class _Stub:
    pass


def _with_pins(stub, fn):
    """Run fn with config.pins replaced by stub (None = the real module)."""
    saved = sys.modules.get('config.pins')
    try:
        if stub is not None:
            sys.modules['config.pins'] = stub
        fn()
    finally:
        if saved is None:
            sys.modules.pop('config.pins', None)
        else:
            sys.modules['config.pins'] = saved


def _count():
    return recovery.load_state()['crash_count']


def _set_count(n):
    state = recovery.load_state()
    state['crash_count'] = n
    recovery.save_state(state)


def run():
    runner = TestRunner()

    real = {name: getattr(recovery, name) for name in _REDIRECTS}
    real_log_max = recovery.LOG_MAX
    real_files = {p: _read(p) for p in (real['STATE_PATH'], real['LOG_PATH'])}

    for name, path in _REDIRECTS.items():
        setattr(recovery, name, path)

    def fresh(test):
        def wrapped():
            _rm_tree(ROOT)
            recovery.LOG_MAX = real_log_max
            test()
        return wrapped

    def test_fresh_box_runs():
        assert recovery.load_state() == {
            'v': 1, 'version': None, 'prev_version': None, 'trial': False,
            'trial_boots': 0, 'trial_wifi_fails': 0, 'crash_count': 0,
            'bad': [], 'managed': [],
        }, "defaults: %r" % (recovery.load_state(),)
        assert recovery.on_boot(False) == ('run', ''), "fresh box did not run"
        assert _count() == 1, "crash_count %d, want 1" % _count()
        assert not _exists(recovery.STATE_PATH + '.tmp'), "save left the .tmp file"

    def test_three_unhealthy_then_stuck():
        for i in range(3):
            got = recovery.on_boot(False)
            assert got == ('run', ''), "boot %d: %r" % (i + 1, got)
        got = recovery.on_boot(False)
        assert got == ('stuck', 'crash loop'), "4th boot: %r" % (got,)

    def test_healthy_clears_count():
        recovery.on_boot(False)
        recovery.on_boot(False)
        recovery.mark_healthy()
        assert _count() == 0, "crash_count %d after healthy" % _count()
        for i in range(3):
            got = recovery.on_boot(False)
            assert got == ('run', ''), "boot %d after healthy: %r" % (i + 1, got)

    def test_power_on_clears_count():
        _set_count(3)
        got = recovery.on_boot(True)
        assert got == ('run', ''), "power-on boot: %r" % (got,)
        assert _count() == 1, "crash_count %d, want 1 (this boot)" % _count()

    def test_wifi_failure_not_a_crash():
        recovery.on_boot(False)
        recovery.note_wifi_failure()
        assert _count() == 0, "crash_count %d after wifi failure" % _count()
        for i in range(5):
            got = recovery.on_boot(False)
            assert got == ('run', ''), "pair %d: %r" % (i + 1, got)
            recovery.note_wifi_failure()
        recovery.note_wifi_failure()
        assert _count() == 0, "crash_count went to %d" % _count()

    def test_dev_mode_never_counts():
        _set_count(3)
        _write(recovery.DEV_FLAG_PATH, '')
        assert recovery.dev_mode() is True, "dev_mode() missed the flag"
        got = recovery.on_boot(False)
        assert got == ('run', ''), "dev boot: %r" % (got,)
        assert _count() == 3, "crash_count changed to %d" % _count()

    def test_corrupt_state_resets():
        _write(recovery.STATE_PATH, '{bad json')
        assert _count() == 0, "corrupt state gave crash_count %d" % _count()
        log = _read(recovery.LOG_PATH) or ''
        assert 'state reset' in log, "log: %r" % log

    def test_wrong_type_resets():
        # A string count would make on_boot raise on every boot.
        _write(recovery.STATE_PATH, '{"crash_count": "3"}')
        assert _count() == 0, "wrong-typed state gave crash_count %r" % _count()
        assert recovery.on_boot(False) == ('run', ''), "on_boot failed on wrong-typed state"

    def test_log_rotates_at_cap():
        recovery.LOG_MAX = 200
        for i in range(20):
            recovery.log('test', 'line %d' % i)
        assert _exists(recovery.LOG_PATH + '.1'), "no rotated file"
        assert _size(recovery.LOG_PATH) < 200, "log is %d B" % _size(recovery.LOG_PATH)
        assert _size(recovery.LOG_PATH + '.1') <= 200, "rotated file over cap"
        assert 'line 19' in _read(recovery.LOG_PATH), "newest line not in log"

    def test_feed_before_arm():
        recovery.feed()

    real_machine = recovery.machine

    def test_stuck_signal():
        fake = _FakeMachine()
        recovery.machine = fake
        try:
            recovery.stuck_forever('t', beep_ms=10, period_ms=20, cycles=2)
        finally:
            recovery.machine = real_machine
        for pin in (18, 9):
            inits = fake.inits(pin)
            ok = inits and inits[0][2] == fake.Pin.OUT and inits[0][3] == 0
            assert ok, "relay %d not driven low as output: %r" % (pin, inits)
            assert fake.level(pin) == 0, "relay %d ends at %r" % (pin, fake.level(pin))
        assert fake.ons(4) == 2, "buzzer beeped %d times, want 2" % fake.ons(4)
        assert fake.level(4) == 0, "buzzer left on"
        assert fake.spi_writes, "LEDs never written"
        frame = fake.spi_writes[-1]
        for i in range(10):
            b, g, r = frame[5 + 4 * i], frame[6 + 4 * i], frame[7 + 4 * i]
            assert r > 0 and g == 0 and b == 0, "LED %d is %r" % (i, (r, g, b))
        log = _read(recovery.LOG_PATH) or ''
        assert 'stuck t' in log, "log: %r" % log

    def test_stuck_feeds():
        class _FakeWdt:
            fed = 0

            def feed(self):
                _FakeWdt.fed += 1

        fake = _FakeMachine()
        recovery.machine = fake
        recovery._wdt = _FakeWdt()
        try:
            recovery.stuck_forever('t', beep_ms=1, period_ms=2, cycles=3)
        finally:
            recovery.machine = real_machine
            recovery._wdt = None
        assert _FakeWdt.fed == 3, "fed %d times in 3 cycles" % _FakeWdt.fed

    def test_stuck_fallbacks():
        fake = _FakeMachine()
        recovery.machine = fake
        try:
            # No pin names at all: buzzer on 4, LEDs skipped and logged.
            _with_pins(_Stub(), lambda: recovery.stuck_forever('t', beep_ms=1, period_ms=2, cycles=2))
            assert fake.ons(4) == 2, "fallback buzzer 4 beeped %d times" % fake.ons(4)
            assert not fake.spi_writes, "LEDs written without pin names"
            log = _read(recovery.LOG_PATH) or ''
            assert 'no LEDs' in log, "LED failure not logged: %r" % log
            # A config buzzer pin is used instead of 4.
            fake.events = []
            stub = _Stub()
            stub.BUZZER_PIN = 42
            _with_pins(stub, lambda: recovery.stuck_forever('t', beep_ms=1, period_ms=2, cycles=2))
            assert fake.ons(42) == 2, "config buzzer 42 beeped %d times" % fake.ons(42)
            assert fake.ons(4) == 0, "used fallback 4 when config had BUZZER_PIN"
        finally:
            recovery.machine = real_machine

    def test_relays_low_pins():
        fake = _FakeMachine()
        recovery.machine = fake
        try:
            # Missing relay names fall back to 18 and 9.
            _with_pins(_Stub(), recovery.relays_low)
            for pin in (18, 9):
                assert fake.level(pin) == 0, "fallback relay %d: %r" % (pin, fake.inits(pin))
            # Names present: those pins are used instead.
            fake.events = []
            stub = _Stub()
            stub.RELAY_AC_PIN = 40
            stub.RELAY_USB_PIN = 41
            _with_pins(stub, recovery.relays_low)
            for pin in (40, 41):
                assert fake.level(pin) == 0, "config relay %d: %r" % (pin, fake.events)
            assert not fake.inits(18), "used fallback 18 when config had pins"
        finally:
            recovery.machine = real_machine

    runner.run_test("Fresh box runs", fresh(test_fresh_box_runs))
    runner.run_test("Three unhealthy boots then stuck", fresh(test_three_unhealthy_then_stuck))
    runner.run_test("Healthy clears count", fresh(test_healthy_clears_count))
    runner.run_test("Power-on clears count", fresh(test_power_on_clears_count))
    runner.run_test("WiFi failure is not a crash", fresh(test_wifi_failure_not_a_crash))
    runner.run_test("Dev mode never counts", fresh(test_dev_mode_never_counts))
    runner.run_test("Corrupt state resets", fresh(test_corrupt_state_resets))
    runner.run_test("Wrong-typed state resets", fresh(test_wrong_type_resets))
    runner.run_test("Log rotates at cap", fresh(test_log_rotates_at_cap))
    runner.run_test("feed before arm is safe", fresh(test_feed_before_arm))
    runner.run_test("Stuck signal drives relays low and beeps", fresh(test_stuck_signal))
    runner.run_test("Relays low without pins module", fresh(test_relays_low_pins))
    runner.run_test("Stuck loop feeds the watchdog each cycle", fresh(test_stuck_feeds))
    runner.run_test("Stuck buzzer pin from config, else 4; no LEDs logged", fresh(test_stuck_fallbacks))

    # Leave the board as we found it.
    _rm_tree(ROOT)
    recovery.LOG_MAX = real_log_max
    for name, path in real.items():
        setattr(recovery, name, path)

    def test_real_files_untouched():
        for path, before in real_files.items():
            after = _read(path)
            assert after == before, "%s changed: %r -> %r" % (path, before, after)

    runner.run_test("Box's real state and log untouched", test_real_files_untouched)
    runner.final_report()


if __name__ == "__main__":
    run()
