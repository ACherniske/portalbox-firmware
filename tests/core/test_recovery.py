import os
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
