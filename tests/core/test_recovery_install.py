import json
from tests.test_helper import TestRunner
from tests.core.test_recovery import (
    ROOT, _REDIRECTS, _exists, _rm_tree, _write, _read, _FakeMachine,
)
import recovery


# Files used by every test. 'sub/c.py' is new in v2 and lives in a new
# folder, so install and rollback must create and clean parent folders.
A, B, C = 'a.py', 'b.py', 'sub/c.py'


def _live(path):
    return recovery.LIVE_ROOT + '/' + path


def _prev(path):
    return recovery.PREV_DIR + '/' + path


def _stage(path):
    return recovery.STAGE_DIR + '/' + path


def _v1_box():
    """A box running v1 (a, b) with v2 (new a, new c, b deleted) staged."""
    _write(_live(A), 'v1')
    _write(_live(B), 'v1')
    _write(_stage(A), 'v2')
    _write(_stage(C), 'v2')
    state = recovery.load_state()
    state['version'] = 'v1'
    state['managed'] = [A, B]
    recovery.save_state(state)


def _install_v2():
    recovery.install('v2', [A, C], [B], [A, C])


def _journal():
    text = _read(recovery.JOURNAL_PATH)
    return None if text is None else json.loads(text)


def _write_journal(status, **extra):
    j = {
        'v': 1, 'status': status, 'version': 'v2', 'prev_version': 'v1',
        'files': [A, C], 'deletes': [B], 'new': [C], 'prev_managed': [A, B],
    }
    j.update(extra)
    _write(recovery.JOURNAL_PATH, json.dumps(j))


def _assert_v1_live(where):
    assert _read(_live(A)) == 'v1', "%s: a is %r" % (where, _read(_live(A)))
    assert _read(_live(B)) == 'v1', "%s: b is %r" % (where, _read(_live(B)))
    assert not _exists(_live(C)), "%s: c still live" % where
    # An empty folder would shadow a same-named module on import.
    assert not _exists(_live('sub')), "%s: empty sub/ left live" % where


def _assert_clean(where):
    for path in (recovery.JOURNAL_PATH, recovery.PREV_DIR, recovery.STAGE_DIR):
        assert not _exists(path), "%s: %s left behind" % (where, path)


def run():
    runner = TestRunner()

    real = {name: getattr(recovery, name) for name in _REDIRECTS}
    real_files = {p: _read(p) for p in (
        real['STATE_PATH'], real['JOURNAL_PATH'], real['LOG_PATH'])}

    for name, path in _REDIRECTS.items():
        setattr(recovery, name, path)

    def fresh(test):
        def wrapped():
            _rm_tree(ROOT)
            _v1_box()
            test()
        return wrapped

    def test_install_swaps_files():
        _install_v2()
        assert _read(_live(A)) == 'v2', "a is %r" % _read(_live(A))
        assert _read(_live(C)) == 'v2', "c is %r" % _read(_live(C))
        assert not _exists(_live(B)), "b still live"
        assert _read(_prev(A)) == 'v1' and _read(_prev(B)) == 'v1', "prev copies missing"
        assert not _exists(recovery.STAGE_DIR), "stage dir left behind"
        state = recovery.load_state()
        assert state['trial'] is True, "not on trial"
        assert state['version'] == 'v2' and state['prev_version'] == 'v1', "versions %r" % state
        assert state['managed'] == [A, C], "managed %r" % (state['managed'],)
        j = _journal()
        assert j['status'] == 'trial', "journal %r" % (j,)
        assert j['new'] == [C], "new files %r" % (j['new'],)

    def test_healthy_trial_commits():
        _install_v2()
        recovery.on_boot(False)
        recovery.mark_healthy()
        _assert_clean("after commit")
        state = recovery.load_state()
        assert state['trial'] is False, "still on trial"
        assert state['version'] == 'v2', "version %r" % state['version']
        assert _read(_live(A)) == 'v2', "a is %r" % _read(_live(A))

    def test_failed_trial_rolls_back_as_bad():
        _install_v2()
        for i in range(3):
            got = recovery.on_boot(False)
            assert got == ('run', ''), "trial boot %d: %r" % (i + 1, got)
        got = recovery.on_boot(False)
        assert got == ('run', ''), "boot after rollback: %r" % (got,)
        _assert_v1_live("after rollback")
        _assert_clean("after rollback")
        state = recovery.load_state()
        assert state['bad'] == ['v2'], "bad %r" % (state['bad'],)
        assert state['version'] == 'v1' and state['trial'] is False, "state %r" % state
        assert state['managed'] == [A, B], "managed %r" % (state['managed'],)
        assert 'rollback v2 bad=True' in (_read(recovery.LOG_PATH) or ''), "no rollback log"

    def test_wifi_only_trial_not_bad():
        _install_v2()
        for i in range(3):
            recovery.on_boot(False)
            recovery.note_wifi_failure()
        recovery.on_boot(False)
        _assert_v1_live("after wifi rollback")
        assert 'v2' not in recovery.load_state()['bad'], "v2 marked bad"

    def test_power_on_keeps_trial():
        _install_v2()
        for i in range(3):
            recovery.on_boot(True)
        assert _read(_live(A)) == 'v2', "rolled back too early"
        recovery.on_boot(True)
        _assert_v1_live("4th power-on boot")

    def check_install_undone(where):
        got = recovery.on_boot(False)
        assert got == ('run', ''), "%s: %r" % (where, got)
        _assert_v1_live(where)
        _assert_clean(where)
        state = recovery.load_state()
        assert state['version'] == 'v1' and state['trial'] is False, "%s: %r" % (where, state)
        assert state['bad'] == [], "%s: marked bad" % where
        assert 'interrupted install undone' in (_read(recovery.LOG_PATH) or ''), "%s: no log" % where

    def test_interrupted_nothing_moved():
        _write_journal('installing')
        check_install_undone("(i) nothing moved")

    def test_interrupted_mid_swap():
        _write_journal('installing')
        _write(_prev(A), _read(_live(A)))
        recovery._remove(_live(A))
        check_install_undone("(ii) a in prev, stage a kept")

    def test_interrupted_before_delete():
        _write_journal('installing')
        _write(_prev(A), 'v1')
        _write(_live(A), 'v2')
        _write(_live(C), 'v2')
        _rm_tree(recovery.STAGE_DIR)
        check_install_undone("(iii) a, c swapped, b not moved")

    def test_interrupted_after_state_saved():
        # Power cut between saving the trial state and marking the journal.
        _install_v2()
        _write_journal('installing')
        check_install_undone("(iv) state saved, journal still installing")

    def test_interrupted_rollback_finishes():
        # A rollback was cut after a and b came back but before c went.
        # The plan's first rule ("no prev, no stage -> remove live") would
        # delete the restored a here.
        _install_v2()
        _write_journal('rolling_back', mark_bad=True)
        recovery._remove(_live(A))
        recovery._rename(_prev(A), _live(A))
        recovery._rename(_prev(B), _live(B))
        got = recovery.on_boot(False)
        assert got == ('run', ''), "boot: %r" % (got,)
        _assert_v1_live("rollback finished")
        _assert_clean("rollback finished")
        assert recovery.load_state()['bad'] == ['v2'], "bad %r" % (recovery.load_state()['bad'],)

    def test_rollback_after_state_saved():
        # Cut after the state was saved but before the journal went:
        # running it again must not add v2 to bad twice.
        _install_v2()
        recovery.rollback(True)
        _write_journal('rolling_back', mark_bad=True)
        recovery.on_boot(False)
        _assert_v1_live("second pass")
        _assert_clean("second pass")
        assert recovery.load_state()['bad'] == ['v2'], "bad %r" % (recovery.load_state()['bad'],)

    def test_interrupted_commit_finishes():
        _install_v2()
        state = recovery.load_state()
        state['trial'] = False
        recovery.save_state(state)
        got = recovery.on_boot(False)
        assert got == ('run', ''), "boot: %r" % (got,)
        assert _read(_live(A)) == 'v2' and _read(_live(C)) == 'v2', "commit was undone"
        _assert_clean("commit finished")
        assert recovery.load_state()['version'] == 'v2', "version changed"

    def test_rollback_twice_is_safe():
        _install_v2()
        recovery.rollback(False)
        recovery.rollback(False)
        _assert_v1_live("rollback twice")
        _assert_clean("rollback twice")

    def test_install_refused_during_trial():
        _install_v2()
        _write(_stage(A), 'v3')
        try:
            recovery.install('v3', [A], [], [A])
        except RuntimeError as e:
            assert 'trial active' in str(e), "message %r" % str(e)
        else:
            assert False, "install ran during a trial"
        assert _read(_live(A)) == 'v2', "live a changed"

    def test_install_refuses_missing_stage():
        recovery._remove(_stage(C))
        try:
            _install_v2()
        except ValueError:
            pass
        else:
            assert False, "install ran with a missing staged file"
        assert _read(_live(A)) == 'v1' and _read(_live(B)) == 'v1', "live files changed"
        assert not _exists(recovery.JOURNAL_PATH), "journal written"
        assert recovery.load_state()['trial'] is False, "trial started"

    def test_trial_without_journal_cleared():
        _install_v2()
        recovery._remove(recovery.JOURNAL_PATH)
        got = recovery.on_boot(False)
        assert got == ('run', ''), "boot: %r" % (got,)
        assert recovery.load_state()['trial'] is False, "still on trial"
        assert _read(_live(A)) == 'v2', "live files changed"
        assert 'trial without journal cleared' in (_read(recovery.LOG_PATH) or ''), "no log"

    def test_subfolder_replace_and_delete():
        R, D = 'pkg/r.py', 'gone/d.py'
        _write(_live(R), 'v1')
        _write(_live(D), 'v1')
        _write(_stage(R), 'v2')
        recovery.install('v2', [R, A, C], [D], [R, A, C])
        assert _read(_live(R)) == 'v2', "r is %r" % _read(_live(R))
        assert _read(_prev(R)) == 'v1' and _read(_prev(D)) == 'v1', "prev copies missing"
        assert not _exists(_live('gone')), "emptied gone/ left live"
        recovery.rollback(False)
        assert _read(_live(R)) == 'v1', "r after rollback %r" % _read(_live(R))
        assert _read(_live(D)) == 'v1', "d after rollback %r" % _read(_live(D))
        _assert_v1_live("subfolder rollback")

    def test_stale_prev_not_restored():
        # Old copies left in prev by an earlier power cut must never come back.
        _write(_prev(C), 'v0')
        _install_v2()
        recovery.rollback(False)
        _assert_v1_live("stale prev")

    def test_commit_cut_while_removing_prev():
        _install_v2()
        real_rm = recovery._rm_tree

        def cut(path):
            if path == recovery.PREV_DIR:
                raise OSError(5)
            real_rm(path)
        recovery._rm_tree = cut
        try:
            recovery.mark_healthy()
        except OSError:
            pass
        finally:
            recovery._rm_tree = real_rm
        recovery.on_boot(False)
        _assert_clean("commit finished after cut")
        assert _read(_live(A)) == 'v2', "commit was undone"

    def test_recovery_error_is_stuck():
        _write_journal('installing')
        _write(_prev(A), _read(_live(A)))
        recovery._remove(_live(A))
        real_rename = recovery._rename

        def broken(src, dst):
            raise OSError(28)
        recovery._rename = broken
        try:
            got = recovery.on_boot(False)
        finally:
            recovery._rename = real_rename
        assert got == ('stuck', 'install recovery failed'), "got %r" % (got,)
        assert 'install recovery failed' in (_read(recovery.LOG_PATH) or ''), "no log"

    def test_install_refuses_duplicates():
        for files, deletes in (([A, A, C], [B]), ([A, C], [A])):
            try:
                recovery.install('v2', files, deletes, files)
            except ValueError:
                pass
            else:
                assert False, "install ran with %r / %r" % (files, deletes)
            assert _read(_live(A)) == 'v1' and _read(_live(B)) == 'v1', "live files changed"
            assert not _exists(recovery.JOURNAL_PATH), "journal written"

    def test_failed_swap_rolls_back():
        real_rename = recovery._rename

        def fail_on_c(src, dst):
            if src == _stage(C):
                raise OSError(28)
            real_rename(src, dst)
        recovery._rename = fail_on_c
        try:
            _install_v2()
        except OSError:
            pass
        else:
            assert False, "install did not raise"
        finally:
            recovery._rename = real_rename
        _assert_v1_live("failed swap")
        assert not _exists(recovery.JOURNAL_PATH), "journal left"
        assert recovery.load_state()['trial'] is False, "trial started"

    def test_install_pending_refused():
        _write_journal('installing')
        try:
            _install_v2()
        except RuntimeError as e:
            assert 'install pending' in str(e), "message %r" % str(e)
        else:
            assert False, "install ran over a pending journal"

    def test_corrupt_journal():
        _write(recovery.JOURNAL_PATH, '{bad json')
        got = recovery.on_boot(False)
        assert got == ('run', ''), "boot: %r" % (got,)
        assert not _exists(recovery.JOURNAL_PATH), "corrupt journal kept"
        assert 'journal unreadable' in (_read(recovery.LOG_PATH) or ''), "no log"

    def test_mixed_trial_is_bad():
        _install_v2()
        for wifi in (True, True, False):
            recovery.on_boot(False)
            if wifi:
                recovery.note_wifi_failure()
        recovery.on_boot(False)
        _assert_v1_live("mixed trial")
        assert recovery.load_state()['bad'] == ['v2'], "bad %r" % (recovery.load_state()['bad'],)

    def test_install_refuses_protected():
        # The anchor, the recovery folders and per-box files are never
        # replaced or deleted by an update, whatever the OTA code asks.
        _write(_live('config/wifi_config.json'), 'mine')
        _write(_live('core/fsm.py'), 'v1')
        bad = ('boot.py', 'main.py', 'recovery.py', 'ota', 'ota/state.json',
               'log', 'log/events.log', 'config/wifi_config.json',
               'config/ota_config.json', 'config/last_time.txt',
               'config/dev_mode', 'config/pins.py', 'config/settings.py',
               'config/manifest.json', 'ca-cert.pem', 'config', 'core',
               '/core/fsm.py', 'core/../main.py', 'core//fsm.py', '', 'core/')
        # The check comes first, so nothing needs staging to be refused.
        for p in bad:
            for files, deletes, managed in (([A, p], [], [A]), ([A], [p], [A]), ([A], [], [A, p])):
                try:
                    recovery.install('v2', files, deletes, managed)
                    raised = None
                except ValueError as e:
                    raised = str(e)
                assert raised is not None and 'protected path' in raised, \
                    "%r in %r/%r/%r: %r" % (p, files, deletes, managed, raised)
                assert not _exists(recovery.JOURNAL_PATH), "journal written for %r" % p
        assert _read(_live('config/wifi_config.json')) == 'mine', "wifi config touched"
        _assert_v1_live("after refusals")
        assert recovery.load_state()['trial'] is False, "trial started"

    def test_install_allows_code_folders():
        # Only per-box paths are protected, not every folder name.
        _write(_live('core/fsm.py'), 'v1')
        _write(_stage('core/fsm.py'), 'v2')
        recovery.install('v2', [A, C, 'core/fsm.py'], [B], [A, C, 'core/fsm.py'])
        assert _read(_live('core/fsm.py')) == 'v2', "core/fsm.py not installed"

    def quiet_stuck(*args, **kwargs):
        # A fake machine, so no real relay, LED or buzzer is driven.
        real_machine = recovery.machine
        recovery.machine = _FakeMachine()
        try:
            recovery.stuck_forever(*args, **kwargs)
        finally:
            recovery.machine = real_machine

    def test_trial_stuck_rolls_back():
        # A trial version that shows the stuck signal from the app resets,
        # so the boot counts and the old version comes back (marked bad).
        _install_v2()
        resets = []
        for i in range(3):
            assert recovery.on_boot(False) == ('run', ''), "trial boot %d" % (i + 1)
            quiet_stuck('wifi config', beep_ms=1, period_ms=2, cycles=1,
                                   reset=lambda: resets.append(1))
            assert len(resets) == i + 1, "stuck in trial did not reset (boot %d)" % (i + 1)
        assert 'stuck during trial' in (_read(recovery.LOG_PATH) or ''), "not logged"
        recovery.on_boot(False)
        _assert_v1_live("after stuck trial")
        assert recovery.load_state()['bad'] == ['v2'], "bad %r" % (recovery.load_state()['bad'],)

    def test_stuck_no_reset_unless_trial_from_app():
        resets = []
        # Committed version: stuck stays stuck.
        quiet_stuck('t', beep_ms=1, period_ms=2, cycles=1,
                               reset=lambda: resets.append(1))
        assert resets == [], "stuck reset a committed version"
        # The boot check itself (rollback_trial=False): no reset loop.
        _install_v2()
        quiet_stuck('t', beep_ms=1, period_ms=2, cycles=1,
                               rollback_trial=False, reset=lambda: resets.append(1))
        assert resets == [], "boot-check stuck reset during a trial"

    def test_journal_read_error_kept():
        _write_journal('installing')

        def eio(*args, **kwargs):
            raise OSError(5)

        recovery.open = eio
        try:
            got = recovery.on_boot(False)
        finally:
            del recovery.open
        assert got == ('stuck', 'install recovery failed'), "boot %r" % (got,)
        assert _exists(recovery.JOURNAL_PATH), "journal removed on a read error"

        # Out of memory while parsing it: not corrupt either.
        class _NoMemJson:
            dump = recovery.json.dump

            @staticmethod
            def loads(text):
                raise MemoryError()

        real_json = recovery.json
        recovery.json = _NoMemJson
        try:
            try:
                recovery._load_journal()
                raised = None
            except MemoryError as e:
                raised = e
        finally:
            recovery.json = real_json
        assert raised is not None, "MemoryError swallowed"
        assert _exists(recovery.JOURNAL_PATH), "journal removed on MemoryError"

    def test_clear_state_wipes():
        _install_v2()
        recovery.clear_state()
        for path in (recovery.STATE_PATH, recovery.JOURNAL_PATH,
                     recovery.PREV_DIR, recovery.STAGE_DIR):
            assert not _exists(path), "%s left behind" % path

    runner.run_test("Install swaps files", fresh(test_install_swaps_files))
    runner.run_test("Healthy trial commits", fresh(test_healthy_trial_commits))
    runner.run_test("Three failed trial boots roll back as bad", fresh(test_failed_trial_rolls_back_as_bad))
    runner.run_test("WiFi-only trial failure not marked bad", fresh(test_wifi_only_trial_not_bad))
    runner.run_test("Power-on does not reset trial", fresh(test_power_on_keeps_trial))
    runner.run_test("Interrupted install undone (i)", fresh(test_interrupted_nothing_moved))
    runner.run_test("Interrupted install undone (ii)", fresh(test_interrupted_mid_swap))
    runner.run_test("Interrupted install undone (iii)", fresh(test_interrupted_before_delete))
    runner.run_test("Interrupted install undone (iv)", fresh(test_interrupted_after_state_saved))
    runner.run_test("Interrupted rollback finishes", fresh(test_interrupted_rollback_finishes))
    runner.run_test("Rollback rerun after state saved", fresh(test_rollback_after_state_saved))
    runner.run_test("Interrupted commit finishes", fresh(test_interrupted_commit_finishes))
    runner.run_test("Rollback twice is safe", fresh(test_rollback_twice_is_safe))
    runner.run_test("Install refused during trial", fresh(test_install_refused_during_trial))
    runner.run_test("Install refuses missing staged file", fresh(test_install_refuses_missing_stage))
    runner.run_test("Trial without journal is cleared", fresh(test_trial_without_journal_cleared))
    runner.run_test("Clear state wipes", fresh(test_clear_state_wipes))
    runner.run_test("Subfolder replace and delete", fresh(test_subfolder_replace_and_delete))
    runner.run_test("Stale prev copy not restored", fresh(test_stale_prev_not_restored))
    runner.run_test("Commit cut while removing prev", fresh(test_commit_cut_while_removing_prev))
    runner.run_test("Recovery error gives stuck", fresh(test_recovery_error_is_stuck))
    runner.run_test("Install refuses duplicate paths", fresh(test_install_refuses_duplicates))
    runner.run_test("Failed swap rolls back", fresh(test_failed_swap_rolls_back))
    runner.run_test("Install pending refused", fresh(test_install_pending_refused))
    runner.run_test("Corrupt journal is dropped", fresh(test_corrupt_journal))
    runner.run_test("Mixed WiFi and crash trial is bad", fresh(test_mixed_trial_is_bad))
    runner.run_test("Install refuses protected paths", fresh(test_install_refuses_protected))
    runner.run_test("Install allows files in code folders", fresh(test_install_allows_code_folders))
    runner.run_test("Stuck during trial resets and rolls back", fresh(test_trial_stuck_rolls_back))
    runner.run_test("Stuck resets only for a trial, from the app", fresh(test_stuck_no_reset_unless_trial_from_app))
    runner.run_test("Journal read error keeps the journal", fresh(test_journal_read_error_kept))

    # Leave the board as we found it.
    _rm_tree(ROOT)
    for name, path in real.items():
        setattr(recovery, name, path)

    def test_real_files_untouched():
        for path, before in real_files.items():
            after = _read(path)
            assert after == before, "%s changed: %r -> %r" % (path, before, after)

    runner.run_test("Box's real state, journal and log untouched", test_real_files_untouched)
    runner.final_report()


if __name__ == "__main__":
    run()
