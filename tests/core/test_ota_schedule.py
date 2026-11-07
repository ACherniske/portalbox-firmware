import os
import time
import uasyncio as asyncio
from tests.test_helper import TestRunner
import recovery
import core.fsm as fsm_mod
import core.main as app_main
import core.system as system
from net import ota


# State and the dev flag are moved under ROOT so the suite never touches
# the box's real files.
ROOT = '/t_sched'
STATE = ROOT + '/ota_state.json'
DEV_FLAG = ROOT + '/dev_mode'

DAY = 24 * 3600
HOUR = 3600


def _at(year, month, day, hour=12):
    return time.mktime((year, month, day, hour, 0, 0, 0, 0))


NOW = _at(2026, 11, 6)
OLD = NOW - 30 * DAY   # last check long ago


def _rm_tree(path):
    try:
        is_dir = os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return
    if is_dir:
        for name in os.listdir(path):
            _rm_tree(path + '/' + name)
        os.rmdir(path)
    else:
        os.remove(path)


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass


class _Anything:
    """A fake device: every method exists and does nothing."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Patch:
    """Set attributes on modules and put them all back on undo()."""

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        while self.saved:
            obj, name, value = self.saved.pop()
            setattr(obj, name, value)


class _FakeTicks:
    """time stand-in: ticks_ms() reads clock[0]; add/diff are the real ones."""

    def __init__(self, clock):
        self.clock = clock
        self.ticks_add = time.ticks_add
        self.ticks_diff = time.ticks_diff

    def ticks_ms(self):
        return self.clock[0]


class _Run:
    """A fake run_update: records each call and returns `result`."""

    def __init__(self, result='up to date', during=None):
        self.result = result
        self.during = during
        self.calls = []

    def __call__(self, cfg, at_boot, abort=None):
        self.calls.append((at_boot, abort))
        if self.during:
            self.during()
        return self.result


ON = {'enabled': True}


def run():
    runner = TestRunner()
    patch = _Patch()

    def fresh(test):
        def wrapped():
            _rm_tree(ROOT)
            _mkdir(ROOT)
            patch.set(recovery, 'DEV_FLAG_PATH', DEV_FLAG)
            try:
                test()
            finally:
                patch.undo()
                _rm_tree(ROOT)
        return wrapped

    def check(cfg=ON, at_boot=True, idle_ms=0, abort=None, now=NOW, fake=None):
        return ota.run_if_due(cfg, at_boot, idle_ms=idle_ms, abort=abort, now=now,
                              run=fake, state_path=STATE)

    # --- DeviceFSM.idle_ms() -------------------------------------------------

    def fsm():
        patch.set(fsm_mod, 'get_device', lambda: _Anything())
        machine_ = fsm_mod.DeviceFSM()
        machine_._apply_state = lambda: None
        return machine_

    def test_idle_zero_in_session():
        m = fsm()
        m.state = fsm_mod.States.AUTH
        m.state_start_time = time.ticks_add(time.ticks_ms(), -700000)
        assert m.idle_ms() == 0, "idle_ms %d in a session" % m.idle_ms()

    def test_idle_grows():
        m = fsm()
        m.state = fsm_mod.States.IDLE
        m.state_start_time = time.ticks_add(time.ticks_ms(), -5000)
        got = m.idle_ms()
        assert 5000 <= got < 6000, "idle_ms %d, want ~5000" % got

    def test_idle_survives_ticks_wrap():
        # ticks_diff only reaches +-2**29 ms (~6.2 days): step a fake clock
        # an hour at a time through 7 days in IDLE.
        clock = [0]
        patch.set(fsm_mod, 'time', _FakeTicks(clock))
        m = fsm()
        m.state = fsm_mod.States.IDLE
        m.state_start_time = clock[0]
        for hour in range(1, 7 * 24 + 1):
            clock[0] = time.ticks_add(clock[0], HOUR * 1000)
            got = m.idle_ms()
            assert got >= 600000, "idle_ms %d after %d h idle" % (got, hour)

    # --- ota.due() -----------------------------------------------------------

    def test_due_at_boot():
        assert ota.due(NOW, {}, True, 0, True) is True, "not due at boot"

    def test_not_due_9_min():
        assert ota.due(NOW, {}, False, 599999, True) is False, "due at 599999 ms idle"

    def test_due_10_min():
        assert ota.IDLE_BEFORE_UPDATE_MS == 600000, "IDLE_BEFORE_UPDATE_MS %r" % ota.IDLE_BEFORE_UPDATE_MS
        assert ota.due(NOW, {}, False, 600000, True) is True, "not due at 10 min idle"

    def test_clock_not_set():
        # A real date, so only the clock flag can say no.
        assert ota.due(NOW, {}, True, 0, False) is False, "ran with clock not set"

    def test_not_within_day():
        state = {'last_success_epoch': NOW - DAY + 60, 'last_try_epoch': NOW - DAY + 60}
        assert ota.due(NOW, state, True, 0, True) is False, "ran again 1 min short of a day"
        state = {'last_success_epoch': NOW - DAY, 'last_try_epoch': NOW - DAY}
        assert ota.due(NOW, state, True, 0, True) is True, "skipped a day after the last run"

    def test_retry_after_hour():
        state = {'last_success_epoch': OLD, 'last_try_epoch': NOW - HOUR + 60}
        assert ota.due(NOW, state, True, 0, True) is False, "retried 59 min after a failure"
        state['last_try_epoch'] = NOW - HOUR
        assert ota.due(NOW, state, True, 0, True) is True, "no retry an hour after a failure"

    def test_bad_times_due():
        # A damaged state file must not block updates forever.
        state = {'last_success_epoch': 'x', 'last_try_epoch': None}
        assert ota.due(NOW, state, True, 0, True) is True, "bad stored times blocked the check"

    # --- ota.run_if_due() ----------------------------------------------------

    def test_try_saved_before_run():
        seen = []
        fake = _Run(during=lambda: seen.append(ota._load_ota_state(STATE).get('last_try_epoch')))
        check(fake=fake)
        assert seen == [NOW], "try time during the run %r, want %d" % (seen, NOW)

    def test_ok_saves_success():
        for result in ('up to date', 'bad version', 'installed abc'):
            _rm_tree(STATE)
            check(fake=_Run(result))
            got = ota._load_ota_state(STATE).get('last_success_epoch')
            assert got == NOW, "%r: success time %r" % (result, got)

    def test_failed_no_success():
        check(fake=_Run('failed: http 504'))
        state = ota._load_ota_state(STATE)
        assert 'last_success_epoch' not in state, "failure saved %r" % (state,)
        assert state.get('last_try_epoch') == NOW, "try time %r" % (state,)

    def test_failed_retries_after_hour():
        check(fake=_Run('failed: http 504'))
        fake = _Run()
        assert check(now=NOW + HOUR - 60, fake=fake) == 'not due', "retried within the hour"
        check(now=NOW + HOUR, fake=fake)
        assert len(fake.calls) == 1, "calls %r, want one retry after an hour" % (fake.calls,)

    def test_off_never_runs():
        fake = _Run()
        for enabled in (False, None, 'true'):
            got = check(cfg={'enabled': enabled}, fake=fake)
            assert got == 'off', "enabled=%r: result %r" % (enabled, got)
        assert fake.calls == [], "ran when off"
        assert ota._load_ota_state(STATE) == {}, "saved state when off"

    def test_dev_mode_idle():
        with open(DEV_FLAG, 'w') as f:
            f.write('')
        fake = _Run()
        got = check(at_boot=False, idle_ms=700000, fake=fake)
        assert got == 'dev mode', "result %r" % got
        assert fake.calls == [], "idle check ran in dev mode"

    def test_dev_mode_boot():
        # The boot path, through system._run_ota, with the flag file present.
        with open(DEV_FLAG, 'w') as f:
            f.write('')
        fake = _Run()
        patch.set(ota, 'load_config', lambda: ON)
        patch.set(ota, 'run_update', fake)
        patch.set(ota, '_OTA_STATE_PATH', STATE)
        system._run_ota()
        assert fake.calls == [], "boot check ran in dev mode"

    def test_clock_from_now():
        # Before the 2023 floor but years after the board's 2000 epoch, so
        # the day and hour waits are long over and only the clock says no.
        fake = _Run()
        assert check(now=_at(2023, 1, 1), fake=fake) == 'not due', "ran with a 2023 clock"
        assert fake.calls == [], "ran with a 2023 clock"

    def test_passes_abort():
        stop = lambda: False
        fake = _Run()
        check(at_boot=False, idle_ms=600000, abort=stop, fake=fake)
        assert fake.calls == [(False, stop)], "calls %r" % (fake.calls,)

    def test_boot_uses_schedule():
        # system._run_ota goes through run_if_due with at_boot=True.
        calls = []
        patch.set(ota, 'load_config', lambda: ON)
        patch.set(ota, 'run_if_due',
                  lambda cfg, at_boot, **k: calls.append((cfg, at_boot)) or 'not due')
        system._run_ota()
        assert calls == [(ON, True)], "calls %r" % (calls,)

    # --- core.main: the idle check -------------------------------------------

    def test_app_idle_check():
        calls = []

        class FakeFSM:
            def idle_ms(self):
                return 600000

        device = _Anything()
        patch.set(ota, 'load_config', lambda: ON)
        patch.set(ota, 'run_if_due',
                  lambda cfg, at_boot, idle_ms=0, abort=None: calls.append(
                      (cfg, at_boot, idle_ms, abort)) or 'not due')
        app = app_main.Application()
        app.fsm = FakeFSM()
        app.device = device
        app._ota_check()
        assert len(calls) == 1, "calls %r" % (calls,)
        cfg, at_boot, idle_ms, abort = calls[0]
        assert (cfg, at_boot, idle_ms) == (ON, False, 600000), "call %r" % (calls[0],)
        assert abort is not None and abort() is None, "abort is not device.card_present"

    def test_app_loop_checks():
        assert ota.CHECK_EVERY_S == 60, "CHECK_EVERY_S %r" % ota.CHECK_EVERY_S
        app = app_main.Application()
        app.running = True
        checks = []

        def one():
            checks.append(1)
            if len(checks) >= 2:
                app.running = False

        app._ota_check = one
        asyncio.run(app._ota_loop(every_ms=20))
        assert checks == [1, 1], "checks %r" % (checks,)

    def test_app_starts_loop():
        started = []

        class FakeFSM:
            async def run(self, mark_healthy=None, healthy_after_ms=None):
                await asyncio.sleep_ms(50)

            def stop(self):
                pass

        async def fake_loop(self, every_ms=0):
            started.append(1)

        patch.set(app_main, 'get_device', lambda: _Anything())
        patch.set(app_main, 'DeviceFSM', FakeFSM)
        patch.set(app_main, 'cleanup_device', lambda: None)
        patch.set(app_main.Application, '_ota_loop', fake_loop)
        asyncio.run(app_main.Application().run())
        assert started == [1], "update loop started %d times, want 1" % len(started)

    tests = [
        ("Idle 0 when in session", test_idle_zero_in_session),
        ("Idle grows in IDLE", test_idle_grows),
        ("Idle survives the ticks wrap", test_idle_survives_ticks_wrap),
        ("Due at boot", test_due_at_boot),
        ("Not due at 9 min idle", test_not_due_9_min),
        ("Due at 10 min idle", test_due_10_min),
        ("Clock not set skips", test_clock_not_set),
        ("Not due within a day of last run", test_not_within_day),
        ("Failure retries after an hour", test_retry_after_hour),
        ("Bad stored times still due", test_bad_times_due),
        ("Try time saved before the run", test_try_saved_before_run),
        ("OK result saves success time", test_ok_saves_success),
        ("Failure saves no success time", test_failed_no_success),
        ("Failed run retried after an hour", test_failed_retries_after_hour),
        ("Off never runs", test_off_never_runs),
        ("Dev mode never runs (idle)", test_dev_mode_idle),
        ("Dev mode never runs (boot)", test_dev_mode_boot),
        ("Clock check uses now", test_clock_from_now),
        ("Abort and idle label passed on", test_passes_abort),
        ("Boot goes through the schedule", test_boot_uses_schedule),
        ("App idle check passes idle time and card", test_app_idle_check),
        ("App loop checks every period", test_app_loop_checks),
        ("App starts the update loop", test_app_starts_loop),
    ]
    for name, test in tests:
        runner.run_test(name, fresh(test))

    runner.final_report()


if __name__ == "__main__":
    run()
