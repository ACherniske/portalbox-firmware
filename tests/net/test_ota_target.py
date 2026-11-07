"""Tests for net.ota_target: pick the target version, refuse unsafe trees.

Offline: GitHub replies are canned dicts served by a fake get_json.
"""
import os

from tests.test_helper import TestRunner
from net import ota, ota_target
from net.https import HttpError
from net.ota_target import OtaError

REPO = '/repos/u/r'
CFG = {'user': 'u', 'repository': 'r', 'branch': 'main', 'mode': 'release',
       'enabled': True, 'ignore': []}
C1 = 'c1' * 20          # commit
T1 = 't1' * 20          # annotated tag object
ROOT = 'a0' * 20        # root tree
SRC = 'b0' * 20         # src tree
CFG_PATH = '/test_ota_cfg.json'
# Owner rulings 2026-11-06: what the box needs to boot and reach GitHub again
# (net/ota_stage.py added after the Task 8 review: ota imports it).
REQUIRED = ('boot.py', 'main.py', 'recovery.py', 'core/system.py', 'core/main.py',
            'net/ota.py', 'net/ota_target.py', 'net/ota_stage.py', 'net/https.py',
            'net/wifi.py', 'certs/github_roots.pem', 'manifest.json')


def _sha(n):
    return ('%02x' % n) * 20


def _blob(path, sha, size=10):
    return {'path': path, 'mode': '100644', 'type': 'blob', 'sha': sha,
            'size': size, 'url': 'https://x'}


def _src_tree(extra=()):
    tree = [_blob(p, _sha(i + 1)) for i, p in enumerate(REQUIRED)]
    tree.append({'path': 'core', 'mode': '040000', 'type': 'tree', 'sha': _sha(99)})
    tree.extend(extra)
    return tree


def _github(ref_obj=None, tag_obj=None, src_tree=None, truncated=False,
            release=True, root=None):
    """Canned API replies keyed by path; anything else is a 404."""
    replies = {
        REPO + '/git/ref/tags/v1.0': {'object': ref_obj or {'type': 'commit', 'sha': C1}},
        REPO + '/git/ref/heads/main': {'object': {'type': 'commit', 'sha': C1}},
        REPO + '/git/trees/' + C1: {'sha': ROOT, 'truncated': False, 'tree': root or [
            {'path': 'docs', 'type': 'tree', 'sha': _sha(50)},
            {'path': 'src', 'type': 'tree', 'sha': SRC},
            {'path': 'README.md', 'type': 'blob', 'sha': _sha(51), 'size': 5}]},
        REPO + '/git/trees/' + SRC + '?recursive=1':
            {'sha': SRC, 'truncated': truncated, 'tree': src_tree or _src_tree()},
    }
    if release:
        replies[REPO + '/releases/latest'] = {'tag_name': 'v1.0'}
    if tag_obj:
        replies[REPO + '/git/tags/' + T1] = {'object': tag_obj}
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


def _local(tree):
    """local_hashes where every file on the box matches the target."""
    return {e['path']: e['sha'] for e in tree if e['type'] == 'blob'}


def _target_tree():
    return ota_target.resolve_target(CFG, _github())['tree']


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

    def test_release_lightweight():
        t = ota_target.resolve_target(CFG, _github())
        assert t['commit'] == C1, "commit %r" % t['commit']
        assert t['label'] == 'v1.0', "label %r" % t['label']

    def test_annotated_tag():
        gj = _github(ref_obj={'type': 'tag', 'sha': T1},
                     tag_obj={'type': 'commit', 'sha': C1})
        t = ota_target.resolve_target(CFG, gj)
        assert t['commit'] == C1, "tag object not dereferenced: %r" % t['commit']
        assert REPO + '/git/tags/' + T1 in gj.asked, "tag object never fetched"

    def test_branch_mode():
        cfg = dict(CFG, mode='branch')
        gj = _github(release=False)
        t = ota_target.resolve_target(cfg, gj)
        assert t['commit'] == C1 and t['label'] == 'main', "branch %r" % t
        assert REPO + '/releases/latest' not in gj.asked, "branch mode asked for a release"

    def test_no_release():
        _raises(lambda: ota_target.resolve_target(CFG, _github(release=False)),
                'no release')

    def test_truncated():
        _raises(lambda: ota_target.resolve_target(CFG, _github(truncated=True)),
                'tree truncated')

    def test_src_only():
        gj = _github()
        tree = ota_target.resolve_target(CFG, gj)['tree']
        paths = [e['path'] for e in tree]
        assert 'boot.py' in paths, "src paths not device-relative: %r" % paths[:3]
        assert 'README.md' not in paths and 'docs' not in paths, "repo root leaked in"
        assert REPO + '/git/trees/' + C1 + '?recursive=1' not in gj.asked, \
            "fetched the whole repo recursively"
        assert sorted(tree[0].keys()) == ['path', 'sha', 'size', 'type'], \
            "entry keys %r" % sorted(tree[0].keys())

    def test_no_src():
        gj = _github(root=[{'path': 'docs', 'type': 'tree', 'sha': _sha(50)}])
        _raises(lambda: ota_target.resolve_target(CFG, gj), 'no src')

    def test_bad_mode():
        _raises(lambda: ota_target.resolve_target(dict(CFG, mode='tip'), _github()),
                'bad mode')

    # --- build_plan --------------------------------------------------------

    def test_missing_required():
        for req in REQUIRED:
            tree = [e for e in _target_tree() if e['path'] != req]
            _raises(lambda: ota_target.build_plan(tree, _local(tree), [], []),
                    'missing ' + req)

    def test_anchor_change():
        for anchor in ('boot.py', 'main.py', 'recovery.py'):
            tree = _target_tree()
            local = _local(tree)
            local[anchor] = _sha(200)
            _raises(lambda: ota_target.build_plan(tree, local, [], []),
                    'anchor changed, needs cable')
            del local[anchor]
            _raises(lambda: ota_target.build_plan(tree, local, [], []),
                    'anchor changed, needs cable')

    def test_unchanged_skipped():
        tree = _target_tree()
        local = _local(tree)
        local['core/main.py'] = _sha(201)     # this one changed
        del local['net/https.py']             # this one is new on the box
        plan = ota_target.build_plan(tree, local, [], [])
        got = sorted(p for p, sha, size in plan['files'])
        assert got == ['core/main.py', 'net/https.py'], "files %r" % got
        f = [x for x in plan['files'] if x[0] == 'core/main.py'][0]
        assert f[1] == _sha(REQUIRED.index('core/main.py') + 1) and f[2] == 10, \
            "entry %r" % (f,)

    def test_anchor_never_planned():
        tree = _target_tree()
        plan = ota_target.build_plan(tree, {k: v for k, v in _local(tree).items()
                                            if k in ota_target.recovery.ANCHOR_FILES}, [], [])
        planned = [p for p, sha, size in plan['files']]
        for a in ('boot.py', 'main.py', 'recovery.py'):
            assert a not in planned, "anchor %s in files" % a
            assert a not in plan['managed'], "anchor %s in managed" % a
        assert 'core/main.py' in planned and 'core/main.py' in plan['managed'], \
            "code file not planned"
        assert 'core' not in planned, "folder entry planned"

    def test_deletes_only_managed():
        tree = _target_tree()
        managed = ['core/old.py', 'core/main.py', 'boot.py', 'config/pins.py', 'core/kept.py']
        plan = ota_target.build_plan(tree, _local(tree), managed, ['core/kept.py'])
        assert plan['deletes'] == ['core/old.py'], "deletes %r" % plan['deletes']
        # A box-only file never managed by OTA (e.g. hardware/extra.py) is
        # absent from managed, so it is not deleted even though not in target.

    def test_local_and_ignored():
        extra = [_blob('config/pins.py', _sha(150)),
                 _blob('config/manifest.json', _sha(151)),
                 _blob('ota/state.json', _sha(152)),
                 _blob('log/events.log', _sha(153)),
                 _blob('ca-cert.pem', _sha(154)),
                 _blob('hardware/led.py', _sha(155)),
                 _blob('tools/configure_wifi.py', _sha(156)),
                 _blob('hardware/lib/x.py', _sha(157))]
        tree = ota_target.resolve_target(CFG, _github(src_tree=_src_tree(extra)))['tree']
        ignore = ['/src/hardware/led.py', 'tools/', 'hardware/lib']
        anchors = {k: v for k, v in _local(tree).items() if k in ('boot.py', 'main.py', 'recovery.py')}
        plan = ota_target.build_plan(tree, anchors, [], ignore)
        planned = [p for p, sha, size in plan['files']]
        for p in ('config/pins.py', 'config/manifest.json', 'ota/state.json',
                  'log/events.log', 'ca-cert.pem', 'hardware/led.py',
                  'tools/configure_wifi.py', 'hardware/lib/x.py'):
            assert p not in planned, "%s in files" % p
            assert p not in plan['managed'], "%s in managed" % p
        assert 'manifest.json' in planned, "top-level manifest not planned"

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

    def test_manifest_lists_required():
        import json
        with open('/manifest.json') as f:
            listed = json.load(f)
        missing = [p for p in ota_target.REQUIRED if 'src/' + p not in listed]
        assert not missing, "manifest.json misses %r" % missing

    runner.run_test("Release resolves to its commit", test_release_lightweight)
    runner.run_test("Annotated tag dereferenced", test_annotated_tag)
    runner.run_test("Branch mode uses branch tip", test_branch_mode)
    runner.run_test("No release is an OtaError", test_no_release)
    runner.run_test("Truncated tree refused", test_truncated)
    runner.run_test("Only the src folder is fetched", test_src_only)
    runner.run_test("No src folder refused", test_no_src)
    runner.run_test("Unknown mode refused", test_bad_mode)
    runner.run_test("Missing required file refused", test_missing_required)
    runner.run_test("Anchor change refused", test_anchor_change)
    runner.run_test("Unchanged files skipped", test_unchanged_skipped)
    runner.run_test("Anchor files never planned", test_anchor_never_planned)
    runner.run_test("Deletes only managed files", test_deletes_only_managed)
    runner.run_test("Local-only and ignored files never planned", test_local_and_ignored)
    runner.run_test("OTA off without config", test_off_without_config)
    runner.run_test("Config read with defaults", test_config_read)
    runner.run_test("Manifest lists every required file", test_manifest_lists_required)

    runner.final_report()


if __name__ == "__main__":
    run()
