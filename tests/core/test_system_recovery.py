import os
import uasyncio as asyncio
from tests.test_helper import TestRunner
import recovery
import core.system as system
import core.main as app_main
import core.fsm as fsm_mod
import net.wifi as wifi
from net.models import WiFiConfig


# Recovery paths are moved under ROOT so the suite never touches the box's
# real state or log. Every fake below stands in for hardware or WiFi.
ROOT = '/t_sys'

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


def _read(path):
    try:
        with open(path, 'r') as f:
            return f.read()
    except OSError:
        return None


def _count():
    return recovery.load_state()['crash_count']


def _set_count(n):
    state = recovery.load_state()
    state['crash_count'] = n
    recovery.save_state(state)


class _Anything:
    """A fake device: every method exists and does nothing."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Stuck(Exception):
    pass


class _Retried(Exception):
    pass


class _FakeNIC:
    """status() plays a script, repeating its last value; connected once
    status() has returned GOT_IP (1010)."""

    def __init__(self, script):
        self.script = list(script)
        self.polls = 0
        self.current = None

    def status(self, *args):
        value = self.script[min(self.polls, len(self.script) - 1)]
        self.polls += 1
        self.current = value
        return value

    def isconnected(self):
        return self.current == 1010

    def active(self, *args):
        return True

    def connect(self, ssid, **kw):
        pass

    def disconnect(self):
        pass

    def ifconfig(self):
        return ('10.0.0.2', '255.255.255.0', '10.0.0.1', '10.0.0.1')


def _wifi_manager(script):
    config = WiFiConfig()
    config.set_wpa2_psk('net', 'password123')
    config.timeout = 10
    mgr = wifi.WifiManager(config=config, verbose=False)
    mgr.nic = _FakeNIC(script)
    mgr._sync_time = lambda: None   # no real network for NTP
    mgr._pre_status = 1000
    return mgr


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


def run():
    runner = TestRunner()

    real = {name: getattr(recovery, name) for name in _REDIRECTS}
    real_files = {p: _read(p) for p in (real['STATE_PATH'], real['LOG_PATH'])}
    for name, path in _REDIRECTS.items():
        setattr(recovery, name, path)

    patch = _Patch()

    def fresh(test):
        def wrapped():
            _rm_tree(ROOT)
            try:
                test()
            finally:
                patch.undo()
                asyncio.new_event_loop()
        return wrapped

    def feed_counter():
        feeds = []
        patch.set(recovery, 'feed', lambda: feeds.append(1))
        return feeds

    # --- WiFi failure: reset without counting a crash ---------------------

    def test_wifi_retry_reset():
        _set_count(1)
        events = []
        counts = []
        patch.set(recovery, 'feed', lambda: events.append('feed'))

        def fake_sleep(s):
            events.append('sleep %s' % s)
            counts.append(_count())

        system._wifi_retry_reset(2, sleep=fake_sleep,
                                 reset=lambda: events.append('reset'))
        assert events == ['feed', 'sleep 1', 'feed', 'sleep 1', 'reset'], \
            "events %r" % (events,)
        assert counts == [0, 0], "count while waiting %r, want 0 (undone first)" % (counts,)
        log = _read(recovery.LOG_PATH) or ''
        assert 'wifi failed, reset in 2 s' in log, "log %r" % log

    # --- Healthy mark -----------------------------------------------------

    def test_healthy_fires():
        calls = []
        asyncio.run(app_main._mark_healthy_after(0, mark=lambda: calls.append(1)))
        assert calls == [1], "mark called %d times" % len(calls)

    def test_healthy_waits():
        calls = []

        async def go():
            asyncio.create_task(
                app_main._mark_healthy_after(1, mark=lambda: calls.append(1)))
            await asyncio.sleep_ms(300)
            early = len(calls)
            await asyncio.sleep_ms(1000)
            return early

        early = asyncio.run(go())
        assert early == 0, "marked healthy before the delay"
        assert calls == [1], "mark called %d times after the delay" % len(calls)

    # --- Application: healthy task, re-raise after shutdown ----------------

    def fake_app(fsm_run):
        """Patch core.main so Application uses fakes; returns the record."""
        rec = {'healthy': [], 'cleanup': 0, 'stop': 0}

        class FakeFSM:
            def __init__(self):
                pass

            async def run(self):
                await fsm_run()

            def stop(self):
                rec['stop'] += 1

        async def fake_healthy(seconds, mark=None):
            rec['healthy'].append(seconds)

        def fake_cleanup():
            rec['cleanup'] += 1

        patch.set(app_main, 'get_device', lambda: _Anything())
        patch.set(app_main, 'DeviceFSM', FakeFSM)
        patch.set(app_main, 'cleanup_device', fake_cleanup)
        patch.set(app_main, '_mark_healthy_after', fake_healthy)
        return rec

    def test_app_starts_healthy():
        async def fsm_run():
            await asyncio.sleep_ms(50)

        rec = fake_app(fsm_run)
        assert app_main.HEALTHY_AFTER_S == 60, "HEALTHY_AFTER_S %r" % app_main.HEALTHY_AFTER_S
        asyncio.run(app_main.Application().run())
        assert rec['healthy'] == [60], "healthy task %r, want [60]" % (rec['healthy'],)

    def test_app_crash_reraises():
        async def fsm_run():
            raise ValueError('boom')

        rec = fake_app(fsm_run)
        try:
            asyncio.run(app_main.Application().run())
            raised = None
        except ValueError as e:
            raised = e
        assert raised is not None, "crash was swallowed"
        assert rec['stop'] == 1 and rec['cleanup'] == 1, "shutdown %r" % (rec,)

    def test_app_interrupt_reraises():
        async def fsm_run():
            raise KeyboardInterrupt

        rec = fake_app(fsm_run)
        try:
            asyncio.run(app_main.Application().run())
            raised = False
        except KeyboardInterrupt:
            raised = True
        assert raised, "interrupt was swallowed"
        assert rec['stop'] == 1 and rec['cleanup'] == 1, "shutdown %r" % (rec,)

    # --- Watchdog feeding -------------------------------------------------

    def test_fsm_feeds():
        feeds = feed_counter()
        patch.set(fsm_mod, 'get_device', lambda: _Anything())
        machine_ = fsm_mod.DeviceFSM()
        machine_._apply_state = lambda: None
        loops = []

        async def update():
            loops.append(1)
            if len(loops) >= 3:
                machine_.running = False

        machine_.update = update
        asyncio.run(machine_.run())
        assert len(feeds) >= 3, "%d feeds in %d loops" % (len(feeds), len(loops))

    def test_wifi_wait_feeds():
        feeds = feed_counter()
        mgr = _wifi_manager([1001, 1001, 1010])
        assert mgr._wait_for_connection() is True, "did not connect: %r" % mgr.last_error
        assert len(feeds) >= 2, "%d feeds over %d polls" % (len(feeds), mgr.nic.polls)

    def test_wifi_retry_delay_feeds():
        feeds = feed_counter()
        mgr = _wifi_manager([1001])
        mgr.config.retry_attempts = 2
        mgr.config.retry_delay = 3
        mgr._init_interface = lambda: False   # fail each attempt at once
        assert mgr.connect_with_retry('pw') is False, "reported connected"
        assert len(feeds) == 3, "%d feeds in a 3 s retry delay, want 3" % len(feeds)

    # --- boot(): which failure leads where ---------------------------------

    def fake_boot(config_ok=True, password='pw', wifi_ok=False):
        rec = {'stuck': [], 'retry': 0, 'connect': 0}

        def stuck(reason, *args, **kwargs):
            rec['stuck'].append(reason)
            raise _Stuck()

        def retry(*args, **kwargs):
            rec['retry'] += 1
            raise _Retried()

        def connect(config, pw):
            rec['connect'] += 1
            return _Anything() if wifi_ok else None

        class FakeConfig:
            pass

        patch.set(system, 'get_device', lambda: _Anything())
        patch.set(system, 'apply_feedback', lambda *a, **k: None)
        patch.set(system, 'WiFiConfig', FakeConfig)
        patch.set(system, '_load_wifi_config', lambda c: config_ok)
        patch.set(system, '_get_password', lambda c: password)
        patch.set(system, '_connect_wifi', connect)
        patch.set(system, '_wifi_retry_reset', retry)
        patch.set(recovery, 'stuck_forever', stuck)
        return rec

    def boot_outcome():
        try:
            system.boot()
        except _Stuck:
            return 'stuck'
        except _Retried:
            return 'retry'
        return 'returned'

    def test_boot_no_config_stuck():
        rec = fake_boot(config_ok=False)
        got = boot_outcome()
        assert got == 'stuck' and rec['stuck'] == ['wifi config'], "%s %r" % (got, rec)
        assert rec['connect'] == 0, "tried to connect without a config"

    def test_boot_bad_password_stuck():
        rec = fake_boot(password=None)
        got = boot_outcome()
        assert got == 'stuck' and rec['stuck'] == ['wifi config'], "%s %r" % (got, rec)
        assert rec['connect'] == 0, "tried to connect without a password"

    def test_boot_connect_fail_retries():
        rec = fake_boot(wifi_ok=False)
        got = boot_outcome()
        assert got == 'retry' and rec['retry'] == 1, "%s %r" % (got, rec)
        assert rec['stuck'] == [], "WiFi outage showed stuck %r" % (rec['stuck'],)

    runner.run_test("WiFi failure resets without counting", fresh(test_wifi_retry_reset))
    runner.run_test("Healthy mark fires after delay", fresh(test_healthy_fires))
    runner.run_test("Healthy mark waits its delay", fresh(test_healthy_waits))
    runner.run_test("App starts the 60 s healthy timer", fresh(test_app_starts_healthy))
    runner.run_test("App crash re-raised after shutdown", fresh(test_app_crash_reraises))
    runner.run_test("App interrupt re-raised after shutdown", fresh(test_app_interrupt_reraises))
    runner.run_test("FSM loop feeds the watchdog", fresh(test_fsm_feeds))
    runner.run_test("WiFi wait loop feeds the watchdog", fresh(test_wifi_wait_feeds))
    runner.run_test("WiFi retry delay feeds each second", fresh(test_wifi_retry_delay_feeds))
    runner.run_test("No WiFi config: stuck 'wifi config'", fresh(test_boot_no_config_stuck))
    runner.run_test("Bad WiFi password: stuck 'wifi config'", fresh(test_boot_bad_password_stuck))
    runner.run_test("WiFi connect failure: retry by reset", fresh(test_boot_connect_fail_retries))

    # Leave the board as we found it.
    _rm_tree(ROOT)
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
