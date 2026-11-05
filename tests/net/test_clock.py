import os
import time
import machine
from tests.test_helper import TestRunner
import net.clock as clock


def _set_rtc(year, month, day):
    # RTC.datetime(): (year, month, day, weekday, hours, minutes, seconds, subseconds)
    machine.RTC().datetime((year, month, day, 0, 12, 0, 0, 0))


def _remove_saved():
    try:
        os.remove(clock.SAVED_TIME_FILE)
    except OSError:
        pass


# The suite saves and deletes times freely, so it must not touch the board's
# real saved time: a stray future value there would set the next boot's floor.
TEST_SAVED_FILE = '/config/test_last_time.txt'


def _read(path):
    try:
        with open(path, 'r') as f:
            return f.read()
    except OSError:
        return None


def _floor_constant():
    return time.mktime(clock.CLOCK_FLOOR + (0, 0, 0, 0, 0))


def run():
    runner = TestRunner()
    saved_rtc = time.time()
    t0 = time.ticks_ms()

    try:
        os.mkdir('/config')
    except OSError:
        pass

    real_saved_file = clock.SAVED_TIME_FILE
    real_saved = _read(real_saved_file)
    clock.SAVED_TIME_FILE = TEST_SAVED_FILE

    def test_floor_moves_old_clock_forward():
        _remove_saved()
        _set_rtc(2000, 1, 1)
        assert clock.apply_floor() is True, "apply_floor should report a change"
        assert time.time() >= clock.floor_seconds(), "clock still below the floor"

    def test_floor_leaves_later_clock():
        _remove_saved()
        _set_rtc(2030, 1, 1)
        assert clock.apply_floor() is False, "apply_floor changed a later clock"
        assert time.localtime()[0] == 2030, "clock was moved backwards"

    def test_saved_time_round_trip():
        _remove_saved()
        _set_rtc(2027, 3, 1)
        expected = time.time()
        assert clock.save_now() is True, "save_now failed"
        loaded = clock.load_saved()
        assert loaded is not None, "load_saved returned None"
        assert abs(loaded - expected) <= 2, "saved %s, loaded %s" % (expected, loaded)

    def test_saved_time_raises_floor():
        _remove_saved()
        _set_rtc(2027, 3, 1)
        clock.save_now()
        _set_rtc(2000, 1, 1)
        clock.apply_floor()
        assert time.localtime()[:3] == (2027, 3, 1), "got %s" % (time.localtime()[:3],)

    def test_bad_file_falls_back():
        for content in ("garbage", ""):
            with open(clock.SAVED_TIME_FILE, 'w') as f:
                f.write(content)
            assert clock.load_saved() is None, "load_saved accepted %r" % content
            assert clock.floor_seconds() == _floor_constant(), "floor ignored CLOCK_FLOOR for %r" % content
        _remove_saved()

    def test_autosave_idempotent():
        _remove_saved()
        clock.start_autosave()
        first = clock._autosave_timer
        clock.start_autosave()
        assert first is not None, "no timer created"
        assert clock._autosave_timer is first, "second call made a new timer"
        clock._autosave_tick(None)
        assert clock.load_saved() is not None, "tick did not save the time"

    def test_connect_applies_floor():
        from net.wifi import WifiManager
        from net.models import WiFiConfig

        class FakeNIC:
            def active(self, *a):
                return True
            def connect(self, ssid, **kw):
                pass
            def status(self, *a):
                return 1010
            def isconnected(self):
                return True
            def ifconfig(self):
                return ('10.0.0.2', '255.255.255.0', '10.0.0.1', '10.0.0.1')

        _remove_saved()
        _set_rtc(2000, 1, 1)
        config = WiFiConfig()
        config.set_wpa2_psk('test-net', 'password123')
        mgr = WifiManager(config=config, verbose=False)
        mgr.nic = FakeNIC()
        mgr._sync_time = lambda: None   # no real network for NTP
        assert mgr.connect('password123', skip_init=True) is True, "fake connect failed"
        assert time.time() >= clock.floor_seconds(), "connect() did not apply the clock floor"
        assert clock._autosave_timer is not None, "connect() did not start the autosave"

    runner.run_test("Floor moves a 2000-era clock forward", test_floor_moves_old_clock_forward)
    runner.run_test("Floor leaves a later clock alone", test_floor_leaves_later_clock)
    runner.run_test("Saved time round-trip", test_saved_time_round_trip)
    runner.run_test("Saved time raises the floor", test_saved_time_raises_floor)
    runner.run_test("Bad saved-time file falls back to CLOCK_FLOOR", test_bad_file_falls_back)
    runner.run_test("Autosave start is idempotent and saves", test_autosave_idempotent)
    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None
    runner.run_test("connect() applies the floor and starts autosave", test_connect_applies_floor)

    # Leave the board as we found it: real time back, no timer, no test file.
    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None
    _remove_saved()
    clock.SAVED_TIME_FILE = real_saved_file

    def test_real_saved_time_untouched():
        assert clock.SAVED_TIME_FILE == real_saved_file, "SAVED_TIME_FILE not restored"
        after = _read(real_saved_file)
        assert after == real_saved, "real saved time changed: %r -> %r" % (real_saved, after)

    runner.run_test("Board's real saved time untouched", test_real_saved_time_untouched)
    tm = time.gmtime(saved_rtc + time.ticks_diff(time.ticks_ms(), t0) // 1000)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))

    runner.final_report()


if __name__ == "__main__":
    run()
