"""Tests for net.ota_target: pick the release, plan from its bundle header.

Offline: GitHub replies are canned dicts served by a fake get_json; bundle
headers are lists of (path, size, sha).
"""
import os

from tests.test_helper import TestRunner
from net import ota, ota_target
from net.https import HttpError
from net.ota_target import OtaError

REPO = '/repos/u/r'
CFG = {'user': 'u', 'repository': 'r', 'enabled': True, 'ignore': []}
CFG_PATH = '/test_ota_cfg.json'
# What the box needs to boot and reach GitHub again, as .mpy (anchors stay .py).
REQUIRED = ('boot.py', 'main.py', 'recovery.py', 'core/system.mpy', 'core/main.mpy',
            'net/__init__.mpy', 'net/ota.mpy', 'net/ota_target.mpy', 'net/ota_stage.mpy',
            'net/https.mpy', 'net/wifi.mpy', 'certs/github_roots.pem')
ANCHORS = ('boot.py', 'main.py', 'recovery.py')


def _sha(n):
    return ('%02x' % n) * 20


def _header(extra=()):
    """Bundle header files: every required file (size 10), plus extra."""
    return [(p, 10, _sha(i + 1)) for i, p in enumerate(REQUIRED)] + list(extra)


def _local(files):
    """local_hashes where every file on the box matches the header."""
    return {p: sha for p, size, sha in files}


def _anchors(files):
    return {p: sha for p, size, sha in files if p in ANCHORS}


def _github(assets=None, release=True):
    """Canned API replies keyed by path; anything else is a 404."""
    replies = {}
    if release:
        replies[REPO + '/releases/latest'] = {
            'tag_name': 'v0.2.0', 'body': 'notes',
            'assets': assets if assets is not None else [
                {'name': 'other.zip', 'id': 1, 'size': 5},
                {'name': 'portalbox.bundle', 'id': 77, 'size': 9000}]}
    return _FakeGitHub(replies)


class _FakeGitHub:
    """get_json stand-in that records every path asked for."""

    def __init__(self, replies):
        self.replies = replies
        self.asked = []

    def __call__(self, path):
        self.asked.append(path)
        if path not in self.replies:
            raise HttpError(404)
        return self.replies[path]


def _raises(fn, text):
    try:
        fn()
    except OtaError as e:
        assert text in str(e), "OtaError %r, expected %r" % (str(e), text)
        return
    raise AssertionError("no OtaError (%s)" % text)


def run():
    runner = TestRunner()

    # --- resolve_target ----------------------------------------------------

    def test_latest_release_bundle():
        gj = _github()
        t = ota_target.resolve_target(CFG, gj)
        assert t == {'version': 'v0.2.0', 'asset': 77}, "target %r" % (t,)
        assert gj.asked == [REPO + '/releases/latest'], "asked %r" % gj.asked

    def test_no_release():
        _raises(lambda: ota_target.resolve_target(CFG, _github(release=False)), 'no release')

    def test_no_bundle_asset():
        gj = _github(assets=[{'name': 'portalbox.zip', 'id': 3}])
        _raises(lambda: ota_target.resolve_target(CFG, gj), 'no bundle')
        _raises(lambda: ota_target.resolve_target(CFG, _github(assets=[])), 'no bundle')

    def test_branch_mode_ignored():
        gj = _github()
        t = ota_target.resolve_target(dict(CFG, mode='branch', branch='main'), gj)
        assert t['version'] == 'v0.2.0', "target %r" % (t,)
        assert gj.asked == [REPO + '/releases/latest'], "asked %r" % gj.asked

    # --- build_plan --------------------------------------------------------

    def test_missing_required():
        for req in REQUIRED:
            files = [f for f in _header() if f[0] != req]
            _raises(lambda: ota_target.build_plan(files, _local(files), [], []),
                    'missing ' + req)

    def test_anchor_change():
        for anchor in ANCHORS:
            files = _header()
            local = _local(files)
            local[anchor] = _sha(200)
            _raises(lambda: ota_target.build_plan(files, local, [], []),
                    'anchor changed, needs cable')
            del local[anchor]
            _raises(lambda: ota_target.build_plan(files, local, [], []),
                    'anchor changed, needs cable')

    def test_unchanged_skipped():
        files = _header()
        local = _local(files)
        local['core/main.mpy'] = _sha(201)     # this one changed
        del local['net/https.mpy']             # this one is new on the box
        plan = ota_target.build_plan(files, local, [], [])
        got = sorted(p for p, sha, size in plan['files'])
        assert got == ['core/main.mpy', 'net/https.mpy'], "files %r" % got
        f = [x for x in plan['files'] if x[0] == 'core/main.mpy'][0]
        assert f == ('core/main.mpy', _sha(REQUIRED.index('core/main.mpy') + 1), 10), \
            "entry %r" % (f,)

    def test_anchor_never_planned():
        files = _header()
        plan = ota_target.build_plan(files, _anchors(files), [], [])
        planned = [p for p, sha, size in plan['files']]
        for a in ANCHORS:
            assert a not in planned, "anchor %s in files" % a
            assert a not in plan['managed'], "anchor %s in managed" % a
        assert 'core/main.mpy' in planned and 'core/main.mpy' in plan['managed'], \
            "code file not planned"

    def test_deletes_only_managed():
        files = _header()
        managed = ['core/old.mpy', 'core/main.mpy', 'boot.py', 'config/pins.py', 'core/kept.mpy']
        plan = ota_target.build_plan(files, _local(files), managed, ['core/kept.mpy'])
        assert plan['deletes'] == ['core/old.mpy'], "deletes %r" % plan['deletes']

    def test_header_local_paths_not_owned():
        extra = [('config/settings.mpy', 5, _sha(150)), ('config/pins.py', 5, _sha(151)),
                 ('ota/state.json', 5, _sha(152)), ('log/events.log', 5, _sha(153)),
                 ('ca-cert.pem', 5, _sha(154)), ('hardware/led.mpy', 5, _sha(155)),
                 ('tools/configure_wifi.py', 5, _sha(156)), ('hardware/lib/x.mpy', 5, _sha(157))]
        files = _header(extra)
        ignore = ['/src/hardware/led.mpy', 'tools/', 'hardware/lib']
        local = _anchors(files)
        local['config/settings.py'] = _sha(160)   # its .mpy is not owned: keep the .py
        plan = ota_target.build_plan(files, local, [], ignore)
        planned = [p for p, sha, size in plan['files']]
        for p, size, sha in extra:
            assert p not in planned, "%s in files" % p
            assert p not in plan['managed'], "%s in managed" % p
        assert 'config/settings.py' not in plan['deletes'], "config .py deleted"
        assert 'certs/github_roots.pem' in planned, "roots not planned"

    def test_shadowed_py_list():
        got = ota_target.shadowed_py([('core/fsm.mpy', 1, 'a'), ('boot.py', 1, 'b'),
                                      ('certs/r.pem', 1, 'c'), ('net/__init__.mpy', 1, 'd')])
        assert got == ['core/fsm.py', 'net/__init__.py'], "shadowed %r" % got

    def test_shadowed_py_deleted():
        files = _header([('core/fsm.mpy', 5, _sha(170))])
        local = _local(files)
        local['core/fsm.py'] = _sha(171)
        local['net/wifi.py'] = _sha(172)
        plan = ota_target.build_plan(files, local, [], [])
        assert 'core/fsm.py' in plan['deletes'], "deletes %r" % plan['deletes']
        assert 'net/wifi.py' in plan['deletes'], "deletes %r" % plan['deletes']
        assert 'main.py' not in plan['deletes'], "anchor deleted"
        assert 'core/fsm.py' not in plan['managed'], "deleted .py managed"

    def test_header_bad_path_refused():
        for bad in ('../boot.py', '/core/x.mpy', 'core//x.mpy', 'core/../x.mpy', ''):
            files = _header([(bad, 5, _sha(180))])
            _raises(lambda: ota_target.build_plan(files, _local(files), [], []), 'bad path')

    # --- config ------------------------------------------------------------

    def _write_cfg(text):
        with open(CFG_PATH, 'w') as f:
            f.write(text)

    def _rm_cfg():
        try:
            os.remove(CFG_PATH)
        except OSError:
            pass

    def test_off_without_config():
        _rm_cfg()
        cfg = ota.load_config(CFG_PATH)
        assert cfg['enabled'] is False, "enabled %r with no file" % cfg['enabled']
        assert cfg['mode'] == 'release', "mode %r" % cfg['mode']

    def test_config_read():
        try:
            _write_cfg('{"user": "u", "repository": "r", "enabled": true, "mode": "branch"}')
            cfg = ota.load_config(CFG_PATH)
            assert cfg['enabled'] is True and cfg['mode'] == 'branch', "cfg %r" % cfg
            assert cfg['branch'] == 'main' and cfg['ignore'] == [], "defaults %r" % cfg
            _write_cfg('{"user": "u", "repository": "r", "enabled": "false"}')
            assert ota.load_config(CFG_PATH)['enabled'] is False, "string enabled OTA"
            _write_cfg('{"user": "u", "repository": "r"}')
            assert ota.load_config(CFG_PATH)['enabled'] is False, "OTA on without enabled"
            _write_cfg('not json')
            assert ota.load_config(CFG_PATH)['enabled'] is False, "bad file enabled OTA"
        finally:
            _rm_cfg()

    runner.run_test("Latest release and its bundle", test_latest_release_bundle)
    runner.run_test("No release is an OtaError", test_no_release)
    runner.run_test("Release without a bundle refused", test_no_bundle_asset)
    runner.run_test("Branch mode ignored", test_branch_mode_ignored)
    runner.run_test("Missing required file refused", test_missing_required)
    runner.run_test("Anchor change refused", test_anchor_change)
    runner.run_test("Unchanged files skipped", test_unchanged_skipped)
    runner.run_test("Anchor files never planned", test_anchor_never_planned)
    runner.run_test("Deletes only managed files", test_deletes_only_managed)
    runner.run_test("Local-only and ignored files never planned", test_header_local_paths_not_owned)
    runner.run_test("Shadowed .py list", test_shadowed_py_list)
    runner.run_test("Shadowed .py deleted", test_shadowed_py_deleted)
    runner.run_test("Bad header path refused", test_header_bad_path_refused)
    runner.run_test("OTA off without config", test_off_without_config)
    runner.run_test("Config read with defaults", test_config_read)

    runner.final_report()


if __name__ == "__main__":
    run()
