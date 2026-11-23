"""Host tests for scripts/deploy.py. Run: py -3 -m unittest discover -s scripts/tests"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import deploy  # noqa: E402

BUNDLE = {
    "boot.py": "b1",
    "main.py": "m1",
    "recovery.py": "r1",
    "core/fsm.mpy": "f1",
    "net/ota.mpy": "o1",
    "certs/github_roots.pem": "c1",
}
EXTRAS = {
    "config/pins.py": "p1",
    "config/settings.py": "s1",
    "tools/configure_wifi.py": "w1",
}


def _box(changes=None):
    """Box hashes equal to the bundle and extras, then `changes`
    (path -> sha, or None to remove it) applied."""
    hashes = dict(BUNDLE)
    hashes.update(EXTRAS)
    for path, sha in (changes or {}).items():
        if sha is None:
            hashes.pop(path, None)
        else:
            hashes[path] = sha
    return hashes


def _actions(hashes, extra_paths=()):
    return deploy.deploy_actions(BUNDLE, hashes, set(hashes) | set(extra_paths), EXTRAS)


class TestDeployActions(unittest.TestCase):
    def test_actions_nothing_when_same(self):
        self.assertEqual(_actions(_box()), ([], []))

    def test_actions_copy_changed_only(self):
        copies, deletes = _actions(
            _box({"core/fsm.mpy": "old", "net/ota.mpy": None, "main.py": "old"})
        )
        self.assertEqual(copies, ["core/fsm.mpy", "main.py", "net/ota.mpy"])
        self.assertEqual(deletes, [])

    def test_actions_delete_shadowed_py(self):
        hashes = _box()
        hashes["core/fsm.py"] = "src"
        hashes["net/wifi.py"] = "src"  # no net/wifi.mpy in the bundle: kept
        copies, deletes = _actions(hashes)
        self.assertEqual(deletes, ["core/fsm.py"])
        self.assertNotIn("main.py", deletes)
        self.assertEqual(copies, [])

    def test_actions_config_only_if_missing(self):
        copies, _ = _actions(
            _box({"config/settings.py": "box buzzer", "config/pins.py": None})
        )
        self.assertIn("config/pins.py", copies)
        self.assertNotIn("config/settings.py", copies)

    def test_actions_config_py_never_deleted(self):
        bundle = dict(BUNDLE)
        bundle["config/pins.mpy"] = "x"  # never built, but never delete config either
        hashes = _box()
        _, deletes = deploy.deploy_actions(bundle, hashes, set(hashes), EXTRAS)
        self.assertNotIn("config/pins.py", deletes)

    def test_actions_tool_copied_when_different(self):
        copies, _ = _actions(_box({"tools/configure_wifi.py": "old"}))
        self.assertIn("tools/configure_wifi.py", copies)

    def test_actions_delete_manifest(self):
        _, deletes = _actions(_box(), extra_paths=["manifest.json"])
        self.assertEqual(deletes, ["manifest.json"])


if __name__ == "__main__":
    unittest.main()
