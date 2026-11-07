"""Tests for net.ota_stage and ota.run_update: download, check, install.

Offline: GitHub replies and file bodies come from fakes. Every recovery
path is moved under ROOT, so the box's real files are never touched.
"""
import os
import json
import uhashlib as hashlib
import ubinascii as binascii

from tests.test_helper import TestRunner
from tests.core.test_recovery import ROOT, _REDIRECTS, _exists, _rm_tree, _write, _read
import recovery
from net import https, ota, ota_stage, ota_target
from net.https import HttpError
from net.ota_target import OtaError

REPO = '/repos/u/r'
C0 = 'c0' * 20          # version the box runs
C1 = 'c1' * 20          # target commit
SRC = 'b0' * 20
RAW = 'https://raw.githubusercontent.com/u/r/' + C1 + '/'
API = 'https://api.github.com'
CFG = {'user': 'u', 'repository': 'r', 'branch': 'main', 'mode': 'branch',
       'enabled': True, 'ignore': [], 'token': ''}
MARGIN = 32768


def _git_sha(text):
    data = text.encode()
    h = hashlib.sha1(('blob %d\0' % len(data)).encode())
    h.update(data)
    return binascii.hexlify(h.digest()).decode()


def _with_manifest(files, extra=()):
    files = dict(files)
    listed = ['src/' + p for p in sorted(files)] + ['src/manifest.json']
    files['manifest.json'] = json.dumps(listed + list(extra))
    return files


def _base():
    return {p: 'v1 ' + p for p in ota_target.REQUIRED if p != 'manifest.json'}


def _v1():
    files = _base()
    files['net/old.py'] = 'old'
    files['config/pins.py'] = 'box pins'
    return _with_manifest(files)


def _v2(extra=()):
    """core/main.py changed (several chunks), net/new.py added, net/old.py
    gone, config/pins.py differs (per-box: never fetched)."""
    files = _base()
    files['core/main.py'] = 'v2 main ' + 'x' * 2000
    files['net/new.py'] = 'new'
    files['config/pins.py'] = 'repo pins'
    return _with_manifest(files, extra)


CHANGED = ('core/main.py', 'net/new.py', 'manifest.json')


def _tree(files):
    tree = [{'path': p, 'sha': _git_sha(t), 'size': len(t.encode()), 'type': 'blob'}
            for p, t in files.items()]
    for d in ('certs', 'config', 'core', 'net'):
        tree.append({'path': d, 'sha': 'd0' * 20, 'size': 0, 'type': 'tree'})
    return tree


def _managed(files):
    return [p for p in files
            if p.split('/')[0] != 'config' and p not in recovery.ANCHOR_FILES]


def _need(paths, target):
    """Space stage() must see free: sizes rounded up to flash blocks, two
    metadata blocks per folder (stage root included), plus the margin."""
    bs = os.statvfs('/')[0]
    total = sum(-(-len(target[p].encode()) // bs) * bs for p in paths)
    dirs = set(p.rsplit('/', 1)[0] if '/' in p else '' for p in paths)
    return total + 2 * bs * len(dirs) + MARGIN


def _live(path):
    return recovery.LIVE_ROOT + '/' + path


def _box(files, version=C0, **state_extra):
    for p, t in files.items():
        _write(_live(p), t)
    state = recovery.load_state()
    state['version'] = version
    state['managed'] = _managed(files)
    state.update(state_extra)
    recovery.save_state(state)


def _plan(target, live):
    local = {p: _git_sha(t) for p, t in live.items()}
    return ota_target.build_plan(_tree(target), local, _managed(live), [])


def _logged(text):
    return text in (_read(recovery.LOG_PATH) or '')


class _FakeGitHub:
    """Branch mode replies for a target whose src/ holds `files`."""

    def __init__(self, files, error=None):
        self.calls = []
        self.error = error
        self.replies = {
            REPO + '/git/ref/heads/main': {'object': {'type': 'commit', 'sha': C1}},
            REPO + '/git/trees/' + C1: {'tree': [{'path': 'src', 'type': 'tree', 'sha': SRC}]},
            REPO + '/git/trees/' + SRC + '?recursive=1':
                {'truncated': False, 'tree': _tree(files)},
        }

    def __call__(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        if path not in self.replies:
            raise HttpError(404)
        return self.replies[path]


class _FakeFetch:
    """Serves target files by raw URL in 100-byte chunks, like https.get
    with a sink. short: drop the last 3 bytes; wrong: change the last byte;
    long: add 2000 bytes; error: raise it (mid=True: after writing the
    first chunk). written: bytes the sink took, per path."""

    def __init__(self, files, short=(), wrong=(), long=(), error=None, mid=False):
        self.bodies = {RAW + 'src/' + p: t.encode() for p, t in files.items()}
        self.urls = []
        self.short = short
        self.wrong = wrong
        self.long = long
        self.error = error
        self.mid = mid
        self.written = {}

    def __call__(self, url, sink):
        self.urls.append(url)
        if self.error and not self.mid:
            raise self.error
        body = self.bodies[url]
        path = url[len(RAW) + 4:]
        if path in self.short:
            body = body[:-3]
        if path in self.wrong:
            body = body[:-1] + b'!'
        if path in self.long:
            body = body + b'x' * 2000
        for i in range(0, len(body), 100):
            sink.write(body[i:i + 100])
            self.written[path] = self.written.get(path, 0) + len(body[i:i + 100])
            if self.error:
                raise self.error
        return len(body)

    def paths(self):
        return sorted(u[len(RAW) + 4:] for u in self.urls)


class _Reset:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


def _raises(fn, kind=OtaError):
    try:
        fn()
    except kind as e:
        return e
    raise AssertionError('no %s raised' % kind.__name__)


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
            _box(_v1())
            test()
        return wrapped

    def stage(target, fetch, **kw):
        plan = _plan(target, _v1())
        ota_stage.stage(plan, _tree(target), RAW, fetch, **kw)

    def assert_v1_live(where):
        for p, t in _v1().items():
            assert _read(_live(p)) == t, "%s: live %s changed" % (where, p)
        assert not _exists(_live('net/new.py')), "%s: new file live" % where

    def assert_no_stage(where):
        assert not _exists(recovery.STAGE_DIR), "%s: stage dir left" % where

    # --- stage ---------------------------------------------------------------

    def test_stage_good():
        fetch = _FakeFetch(_v2())
        stage(_v2(), fetch)
        assert fetch.paths() == sorted(CHANGED), "fetched %r" % (fetch.paths(),)
        for p in CHANGED:
            got = _read(recovery.STAGE_DIR + '/' + p)
            assert got == _v2()[p], "staged %s is %r" % (p, (got or '')[:40])
        assert_v1_live("after stage")

    def test_hash_mismatch():
        e = _raises(lambda: stage(_v2(), _FakeFetch(_v2(), wrong=('core/main.py',))))
        assert str(e) == 'bad hash core/main.py', "error %r" % str(e)
        assert_no_stage("hash mismatch")
        assert_v1_live("hash mismatch")

    def test_short_body():
        e = _raises(lambda: stage(_v2(), _FakeFetch(_v2(), short=('net/new.py',))))
        assert str(e) == 'bad hash net/new.py', "error %r" % str(e)
        assert_no_stage("short body")

    def test_no_space():
        fetch = _FakeFetch(_v2())
        e = _raises(lambda: stage(_v2(), fetch, free_bytes=10))
        assert str(e) == 'no space', "error %r" % str(e)
        assert fetch.urls == [], "downloaded before the space check: %r" % fetch.urls

    def test_space_margin():
        need = _need(CHANGED, _v2())
        e = _raises(lambda: stage(_v2(), _FakeFetch(_v2()), free_bytes=need - 1))
        assert str(e) == 'no space', "error %r" % str(e)
        _rm_tree(recovery.STAGE_DIR)
        stage(_v2(), _FakeFetch(_v2()), free_bytes=need)

    def test_abort_before_first_file():
        fetch = _FakeFetch(_v2())
        e = _raises(lambda: stage(_v2(), fetch, abort=lambda: True))
        assert str(e) == 'aborted', "error %r" % str(e)
        assert fetch.urls == [], "fetched %r though aborted" % fetch.urls

    def test_long_body_stops_early():
        fetch = _FakeFetch(_v2(), long=('net/new.py',))
        e = _raises(lambda: stage(_v2(), fetch))
        assert str(e) == 'bad hash net/new.py', "error %r" % str(e)
        size = len(_v2()['net/new.py'].encode())
        wrote = fetch.written.get('net/new.py', 0)
        assert wrote <= size, "wrote %d bytes of a %d byte file" % (wrote, size)
        assert_no_stage("long body")

    def test_stale_stage_cleared():
        _write(recovery.STAGE_DIR + '/stale.py', 'x' * 5000)
        stage(_v2(), _FakeFetch(_v2()))
        assert not _exists(recovery.STAGE_DIR + '/stale.py'), "stale stage file kept"

    def test_bad_manifest():
        target = _v2()
        target['manifest.json'] = 'not json'
        e = _raises(lambda: stage(target, _FakeFetch(target)))
        assert str(e) == 'bad manifest', "error %r" % str(e)
        assert_no_stage("bad manifest")

    def test_sink_error():
        fetch = _FakeFetch(_v2(), error=OSError(28), mid=True)
        _raises(lambda: stage(_v2(), fetch), OSError)
        assert_no_stage("sink error")
        assert_v1_live("sink error")

    def test_abort_between_files():
        fetch = _FakeFetch(_v2())
        e = _raises(lambda: stage(_v2(), fetch, abort=lambda: len(fetch.urls) >= 1))
        assert str(e) == 'aborted', "error %r" % str(e)
        assert len(fetch.urls) == 1, "fetched %r after abort" % fetch.urls
        assert_no_stage("abort")

    def test_manifest_missing():
        target = _v2(extra=['src/net/gone.py'])
        e = _raises(lambda: stage(target, _FakeFetch(target)))
        assert str(e) == 'manifest net/gone.py missing', "error %r" % str(e)
        assert_no_stage("manifest missing")

    def test_unchanged_manifest_checked_live():
        live = _v1()
        live['manifest.json'] = json.dumps(['src/boot.py', 'src/net/gone.py'])
        _box(live)
        target = _v2()
        target['manifest.json'] = live['manifest.json']
        fetch = _FakeFetch(target)
        plan = _plan(target, live)
        e = _raises(lambda: ota_stage.stage(plan, _tree(target), RAW, fetch))
        assert str(e) == 'manifest net/gone.py missing', "error %r" % str(e)
        assert 'manifest.json' not in fetch.paths(), "manifest fetched though unchanged"

    # --- run_update ----------------------------------------------------------

    def run_update(github=None, fetch=None, cfg=CFG, at_boot=True, reset=None, **kw):
        return ota.run_update(cfg, at_boot, get_json=github or _FakeGitHub(_v2()),
                              fetch=fetch or _FakeFetch(_v2()), reset=reset or _Reset(), **kw)

    def test_run_installs():
        reset = _Reset()
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch, reset=reset)
        assert got == 'installed ' + C1, "result %r" % got
        assert fetch.paths() == sorted(CHANGED), "fetched %r" % (fetch.paths(),)
        for p in CHANGED:
            assert _read(_live(p)) == _v2()[p], "live %s not updated" % p
        assert not _exists(_live('net/old.py')), "net/old.py not deleted"
        assert _read(_live('config/pins.py')) == 'box pins', "per-box file replaced"
        state = recovery.load_state()
        assert state['trial'] is True and state['version'] == C1, "state %r" % state
        assert reset.calls == 1, "reset %d times" % reset.calls
        assert _logged('ota boot installed ' + C1), "not logged"

    def test_bad_version_skipped():
        _box(_v1(), bad=[C1])
        reset = _Reset()
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch, reset=reset)
        assert got == 'bad version', "result %r" % got
        assert fetch.urls == [] and reset.calls == 0, "downloaded or reset"
        assert _logged('ota boot bad version'), "not logged"

    def test_same_version_up_to_date():
        _box(_v1(), version=C1)
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch)
        assert got == 'up to date', "result %r" % got
        assert fetch.urls == [], "downloaded %r" % fetch.urls

    def test_http_403_failed():
        reset = _Reset()
        got = run_update(github=_FakeGitHub(_v2(), error=HttpError(403)), reset=reset)
        assert got.startswith('failed: ') and '403' in got, "result %r" % got
        assert reset.calls == 0, "reset after failure"
        assert _logged('ota boot failed: '), "not logged"

    def test_run_never_raises():
        reset = _Reset()
        got = run_update(fetch=_FakeFetch(_v2(), error=MemoryError()), reset=reset)
        assert got.startswith('failed: '), "result %r" % got
        assert reset.calls == 0, "reset after failure"
        assert_no_stage("MemoryError")
        assert_v1_live("MemoryError")
        assert recovery.load_state()['trial'] is False, "trial started"

    def test_install_refused_stage_removed():
        _write(recovery.JOURNAL_PATH, '{}')   # an earlier install still pending
        reset = _Reset()
        got = run_update(reset=reset)
        assert got.startswith('failed: ') and 'install pending' in got, "result %r" % got
        assert_no_stage("install refused")
        assert_v1_live("install refused")
        assert reset.calls == 0, "reset after failure"

    def test_failed_text():
        got = run_update(fetch=_FakeFetch(_v2(), wrong=('core/main.py',)))
        assert got == 'failed: bad hash core/main.py', "OtaError: %r" % got
        got = run_update(github=_FakeGitHub(_v2(), error=HttpError(403)))
        assert got == 'failed: http 403', "HttpError: %r" % got
        got = run_update(fetch=_FakeFetch(_v2(), error=MemoryError()))
        assert got == 'failed: MemoryError', "MemoryError: %r" % got

    def test_default_fetchers_send_token():
        github = _FakeGitHub(_v2())
        fetch = _FakeFetch(_v2())
        seen = []

        def fake_get(url, headers=None, sink=None, max_bytes=65536):
            seen.append((url[:30], headers))
            if url.startswith(API):
                return json.dumps(github(url[len(API):])).encode()
            return fetch(url, sink)

        real = https.get
        https.get = fake_get
        try:
            cfg = dict(CFG)
            cfg['token'] = 'tok'
            got = ota.run_update(cfg, True, reset=_Reset())
        finally:
            https.get = real
        assert got == 'installed ' + C1, "result %r" % got
        assert len(github.calls) == 3 and len(fetch.urls) == len(CHANGED), \
            "api %d raw %d" % (len(github.calls), len(fetch.urls))
        for url, headers in seen:
            assert headers == {'Authorization': 'Bearer tok'}, "%s headers %r" % (url, headers)

    def test_delete_only_installs():
        live = _v1()
        live['net/extra.py'] = 'extra'      # owned by OTA, not in the manifest
        _box(live)
        reset = _Reset()
        fetch = _FakeFetch(_v1())
        got = run_update(github=_FakeGitHub(_v1()), fetch=fetch, reset=reset)
        assert got == 'installed ' + C1, "result %r" % got
        assert not _exists(_live('net/extra.py')), "net/extra.py not deleted"
        assert fetch.urls == [], "downloaded %r" % fetch.urls
        assert reset.calls == 1, "reset %d times" % reset.calls

    def test_journal_left_resets():
        """install() fails mid-swap and its rollback fails too: the journal
        stays, so reset and let on_boot finish the rollback."""
        real_rename, real_rollback = recovery._rename, recovery.rollback
        renames = []

        def bad_rename(src, dst):
            renames.append(src)
            if len(renames) == 2:
                raise OSError(5)
            real_rename(src, dst)

        def bad_rollback(mark_bad):
            raise OSError(5)

        recovery._rename, recovery.rollback = bad_rename, bad_rollback
        reset = _Reset()
        try:
            got = run_update(reset=reset)
        finally:
            recovery._rename, recovery.rollback = real_rename, real_rollback
        assert got.startswith('failed: '), "result %r" % got
        assert _exists(recovery.JOURNAL_PATH), "journal gone: test did not cut the install"
        assert reset.calls == 1, "reset %d times, want 1" % reset.calls

    def test_off():
        github = _FakeGitHub(_v2())
        for enabled in (False, None, 'true'):
            cfg = dict(CFG)
            cfg['enabled'] = enabled
            got = run_update(github=github, cfg=cfg)
            assert got == 'off', "enabled=%r: result %r" % (enabled, got)
        assert github.calls == [], "asked GitHub while off"
        assert _logged('ota boot off'), "not logged"

    def test_trial_active_refused():
        _box(_v1(), trial=True)
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch)
        assert got == 'failed: trial active', "result %r" % got
        assert fetch.urls == [], "downloaded during a trial"

    def test_no_change_saves_version():
        _box(_v1(), version=None, managed=[])   # set up by cable: nothing saved yet
        reset = _Reset()
        fetch = _FakeFetch(_v1())
        got = run_update(github=_FakeGitHub(_v1()), fetch=fetch, reset=reset)
        assert got == 'up to date', "result %r" % got
        state = recovery.load_state()
        assert state['version'] == C1, "version %r" % state['version']
        assert sorted(state['managed']) == sorted(_managed(_v1())), "managed %r" % (state['managed'],)
        assert state['trial'] is False, "trial started"
        assert fetch.urls == [] and reset.calls == 0, "downloaded or reset"

    def test_idle_logged():
        run_update(at_boot=False)
        assert _logged('ota idle installed ' + C1), "log %r" % _read(recovery.LOG_PATH)

    runner.run_test("Good files staged, live untouched", fresh(test_stage_good))
    runner.run_test("Hash mismatch fails, live untouched", fresh(test_hash_mismatch))
    runner.run_test("Short body fails", fresh(test_short_body))
    runner.run_test("No space refused before download", fresh(test_no_space))
    runner.run_test("Space counts blocks, folders and 32 KB margin", fresh(test_space_margin))
    runner.run_test("Abort before the first file", fresh(test_abort_before_first_file))
    runner.run_test("Too-long body stops at its size", fresh(test_long_body_stops_early))
    runner.run_test("Leftover stage cleared first", fresh(test_stale_stage_cleared))
    runner.run_test("Bad manifest refused", fresh(test_bad_manifest))
    runner.run_test("Sink write error fails, stage removed", fresh(test_sink_error))
    runner.run_test("Abort between files", fresh(test_abort_between_files))
    runner.run_test("Missing manifest entry refused", fresh(test_manifest_missing))
    runner.run_test("Unchanged manifest checked from live copy", fresh(test_unchanged_manifest_checked_live))
    runner.run_test("Good files staged and installed", fresh(test_run_installs))
    runner.run_test("Bad version skipped", fresh(test_bad_version_skipped))
    runner.run_test("Same version is up to date", fresh(test_same_version_up_to_date))
    runner.run_test("HTTP 403 is failed, not raised", fresh(test_http_403_failed))
    runner.run_test("Run never raises", fresh(test_run_never_raises))
    runner.run_test("Install refused, stage removed", fresh(test_install_refused_stage_removed))
    runner.run_test("Failure text names the cause", fresh(test_failed_text))
    runner.run_test("Default fetchers send the token", fresh(test_default_fetchers_send_token))
    runner.run_test("Delete-only update installs", fresh(test_delete_only_installs))
    runner.run_test("Install that leaves its journal resets", fresh(test_journal_left_resets))
    runner.run_test("Off does nothing", fresh(test_off))
    runner.run_test("Trial active refused before download", fresh(test_trial_active_refused))
    runner.run_test("No file changes saves the version", fresh(test_no_change_saves_version))
    runner.run_test("Idle run labelled in the log", fresh(test_idle_logged))

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
