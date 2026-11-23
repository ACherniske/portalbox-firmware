"""Tests for ota.run_update: latest release -> bundle -> stage -> install.

Offline: GitHub replies and the bundle come from fakes. Every recovery
path is moved under ROOT, so the box's real files are never touched.
"""

from tests.test_helper import TestRunner
from tests.core.test_recovery import ROOT, _REDIRECTS, _exists, _rm_tree, _write, _read
from tests.net.test_ota_bundle import _bundle
import recovery
from net import https, ota, ota_target
from net.https import HttpError

REPO = "/repos/u/r"
API = "https://api.github.com"
OLD = "v0.1.0"  # version the box runs
TAG = "v0.2.0"  # latest release
ASSET = 77
CFG = {"user": "u", "repository": "r", "enabled": True, "ignore": [], "token": ""}


def _base():
    return {p: "v1 " + p for p in ota_target.REQUIRED}


def _v1():
    files = _base()
    files["net/old.mpy"] = "old"
    files["config/pins.py"] = "box pins"
    return files


def _v2():
    """core/main.mpy changed (several chunks), net/new.mpy added,
    net/old.mpy gone, config/pins.py differs (per-box: never written)."""
    files = _base()
    files["core/main.mpy"] = "v2 main " + "x" * 2000
    files["net/new.mpy"] = "new"
    files["config/pins.py"] = "repo pins"
    return files


CHANGED = ("core/main.mpy", "net/new.mpy")


def _managed(files):
    return [
        p
        for p in files
        if p.split("/")[0] != "config" and p not in recovery.ANCHOR_FILES
    ]


def _live(path):
    return recovery.LIVE_ROOT + "/" + path


def _box(files, version=OLD, **state_extra):
    for p, t in files.items():
        _write(_live(p), t)
    state = recovery.load_state()
    state["version"] = version
    state["managed"] = _managed(files)
    state.update(state_extra)
    recovery.save_state(state)


def _logged(text):
    return text in (_read(recovery.LOG_PATH) or "")


class _FakeGitHub:
    """get_json for releases/latest: tag and assets (default: the bundle)."""

    def __init__(self, tag=TAG, assets=None, error=None):
        self.calls = []
        self.error = error
        self.reply = {
            "tag_name": tag,
            "body": "notes",
            "assets": assets
            if assets is not None
            else [{"name": "portalbox.bundle", "id": ASSET}],
        }

    def __call__(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        if path != REPO + "/releases/latest":
            raise HttpError(404)
        return self.reply


class _FakeFetch:
    """fetch(asset, sink): the bundle of `files` in 100-byte chunks.
    flip: change one byte inside that path's body. error: raised after
    the first chunk. asked: asset ids requested; done: the last chunk went out."""

    def __init__(self, files, version=TAG, flip=None, error=None):
        bodies = dict((p, t.encode()) for p, t in files.items())
        self.data = bytearray(_bundle(bodies, version))
        if flip:
            names = sorted(bodies)
            at = len(self.data) - sum(
                len(bodies[p]) for p in names[names.index(flip) :]
            )
            self.data[at] ^= 1
        self.error = error
        self.asked = []
        self.done = False

    def __call__(self, asset, sink):
        self.asked.append(asset)
        for at in range(0, len(self.data), 100):
            sink.write(bytes(self.data[at : at + 100]))
            if self.error:
                raise self.error
        self.done = True


class _Reset:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


def run():
    runner = TestRunner()

    real = {name: getattr(recovery, name) for name in _REDIRECTS}
    real_files = {
        p: _read(p)
        for p in (real["STATE_PATH"], real["JOURNAL_PATH"], real["LOG_PATH"])
    }
    for name, path in _REDIRECTS.items():
        setattr(recovery, name, path)

    def fresh(test):
        def wrapped():
            _rm_tree(ROOT)
            _box(_v1())
            test()

        return wrapped

    def assert_v1_live(where):
        for p, t in _v1().items():
            assert _read(_live(p)) == t, "%s: live %s changed" % (where, p)
        assert not _exists(_live("net/new.mpy")), "%s: new file live" % where

    def assert_no_stage(where):
        assert not _exists(recovery.STAGE_DIR), "%s: stage dir left" % where

    def run_update(github=None, fetch=None, cfg=CFG, at_boot=True, reset=None, **kw):
        return ota.run_update(
            cfg,
            at_boot,
            get_json=github or _FakeGitHub(),
            fetch=fetch or _FakeFetch(_v2()),
            reset=reset or _Reset(),
            **kw,
        )

    def test_run_installs():
        reset = _Reset()
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch, reset=reset)
        assert got == "installed " + TAG, "result %r" % got
        assert fetch.asked == [ASSET], "asked %r" % fetch.asked
        for p in CHANGED:
            assert _read(_live(p)) == _v2()[p], "live %s not updated" % p
        assert not _exists(_live("net/old.mpy")), "net/old.mpy not deleted"
        assert _read(_live("config/pins.py")) == "box pins", "per-box file replaced"
        state = recovery.load_state()
        assert state["trial"] is True and state["version"] == TAG, "state %r" % state
        assert reset.calls == 1, "reset %d times" % reset.calls
        assert _logged("ota boot installed " + TAG), "not logged"

    def test_run_up_to_date_no_download():
        _box(_v1(), version=TAG)
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch)
        assert got == "up to date", "result %r" % got
        assert fetch.asked == [], "downloaded %r" % fetch.asked

    def test_bad_version_skipped():
        _box(_v1(), bad=[TAG])
        reset = _Reset()
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch, reset=reset)
        assert got == "bad version", "result %r" % got
        assert fetch.asked == [] and reset.calls == 0, "downloaded or reset"
        assert _logged("ota boot bad version"), "not logged"

    def test_run_no_bundle_failed():
        reset = _Reset()
        got = run_update(github=_FakeGitHub(assets=[]), reset=reset)
        assert got == "failed: no bundle", "result %r" % got
        assert reset.calls == 0, "reset after failure"
        assert _logged("ota boot failed: no bundle"), "not logged"

    def test_run_bundle_version_mismatch():
        got = run_update(fetch=_FakeFetch(_v2(), version="v0.1.9"))
        assert got == "failed: bundle v0.1.9, release v0.2.0", "result %r" % got
        assert_v1_live("version mismatch")
        assert_no_stage("version mismatch")

    def test_run_deletes_shadowed_py():
        live = _v1()
        live["core/system.py"] = "old source"  # cable leftover beside core/system.mpy
        _box(live, managed=_managed(_v1()))  # never installed by OTA
        got = run_update()
        assert got == "installed " + TAG, "result %r" % got
        assert not _exists(_live("core/system.py")), "shadowing .py left"
        recovery.rollback(False)
        assert _read(_live("core/system.py")) == "old source", "rollback lost the .py"

    def test_no_change_adopts_tag():
        _box(_v1(), version=None, managed=[])  # set up by cable: nothing saved yet
        reset = _Reset()
        fetch = _FakeFetch(_v1())
        got = run_update(fetch=fetch, reset=reset)
        assert got == "up to date", "result %r" % got
        state = recovery.load_state()
        assert state["version"] == TAG, "version %r" % state["version"]
        assert sorted(state["managed"]) == sorted(_managed(_v1())), "managed %r" % (
            state["managed"],
        )
        assert state["trial"] is False, "trial started"
        assert reset.calls == 0, "reset"
        assert_no_stage("no change")

    def test_delete_only_installs():
        live = _v1()
        live["net/extra.mpy"] = "extra"  # owned by OTA, gone from the bundle
        _box(live)
        reset = _Reset()
        got = run_update(fetch=_FakeFetch(_v1()), reset=reset)
        assert got == "installed " + TAG, "result %r" % got
        assert not _exists(_live("net/extra.mpy")), "net/extra.mpy not deleted"
        assert reset.calls == 1, "reset %d times" % reset.calls

    def test_http_403_failed():
        reset = _Reset()
        got = run_update(github=_FakeGitHub(error=HttpError(403)), reset=reset)
        assert got == "failed: http 403", "result %r" % got
        assert reset.calls == 0, "reset after failure"
        assert _logged("ota boot failed: "), "not logged"

    def test_run_never_raises():
        reset = _Reset()
        got = run_update(fetch=_FakeFetch(_v2(), error=MemoryError()), reset=reset)
        assert got == "failed: MemoryError", "result %r" % got
        assert reset.calls == 0, "reset after failure"
        assert_no_stage("MemoryError")
        assert_v1_live("MemoryError")
        assert recovery.load_state()["trial"] is False, "trial started"

    def test_failed_text():
        got = run_update(fetch=_FakeFetch(_v2(), flip="core/main.mpy"))
        assert got == "failed: bad hash core/main.mpy", "OtaError: %r" % got

    def test_install_refused_stage_removed():
        _write(recovery.JOURNAL_PATH, "{}")  # an earlier install still pending
        reset = _Reset()
        got = run_update(reset=reset)
        assert got.startswith("failed: ") and "install pending" in got, (
            "result %r" % got
        )
        assert_no_stage("install refused")
        assert_v1_live("install refused")
        assert reset.calls == 0, "reset after failure"

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
        assert got.startswith("failed: "), "result %r" % got
        assert _exists(recovery.JOURNAL_PATH), (
            "journal gone: test did not cut the install"
        )
        assert reset.calls == 1, "reset %d times, want 1" % reset.calls

    def test_off():
        github = _FakeGitHub()
        for enabled in (False, None, "true"):
            cfg = dict(CFG)
            cfg["enabled"] = enabled
            got = run_update(github=github, cfg=cfg)
            assert got == "off", "enabled=%r: result %r" % (enabled, got)
        assert github.calls == [], "asked GitHub while off"
        assert _logged("ota boot off"), "not logged"

    def test_trial_active_refused():
        _box(_v1(), trial=True)
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch)
        assert got == "failed: trial active", "result %r" % got
        assert fetch.asked == [], "downloaded during a trial"

    def test_abort_after_last_file():
        """A card tapped as the last chunk arrived: no install, no reset."""
        reset = _Reset()
        fetch = _FakeFetch(_v2())
        got = run_update(fetch=fetch, reset=reset, abort=lambda: fetch.done)
        assert fetch.done, "bundle not fully sent"
        assert got == "failed: aborted", "result %r" % got
        assert reset.calls == 0, "reset %d times" % reset.calls
        assert_v1_live("abort after last file")
        assert_no_stage("abort after last file")
        state = recovery.load_state()
        assert state["trial"] is False and state["version"] != TAG, "state %r" % state

    def test_idle_logged():
        got = run_update(at_boot=False)
        assert got == "installed " + TAG, "result %r" % got
        assert _logged("ota idle installed " + TAG), "not logged as idle"

    def via_https(token="tok", github=None):
        """run_update with its default fetchers over a fake https.Session.
        Returns (result, [(url, headers, kind)], [sessions made])."""
        github = github or _FakeGitHub()
        fetch = _FakeFetch(_v2())
        seen = []
        sessions = []

        class FakeSession:
            def __init__(self):
                self.closed = False
                sessions.append(self)

            def get(self, url, headers=None, sink=None, max_bytes=65536):
                seen.append((url, headers, "get"))
                prefix = API + REPO + "/releases/assets/"
                if url.startswith(prefix) and sink is not None:
                    return fetch(int(url[len(prefix) :]), sink)
                raise HttpError(404)

            def get_json(self, url, headers=None, max_bytes=65536):
                seen.append((url, headers, "json"))
                return github(url[len(API) :])

            def close(self):
                self.closed = True

        real = https.Session
        https.Session = FakeSession
        try:
            cfg = dict(CFG)
            cfg["token"] = token
            got = ota.run_update(cfg, True, reset=_Reset())
        finally:
            https.Session = real
        return got, seen, sessions

    def test_default_fetch_sends_token_and_accept():
        got, seen, sessions = via_https()
        assert got == "installed " + TAG, "result %r" % got
        assert seen[0] == (
            API + REPO + "/releases/latest",
            {"Authorization": "Bearer tok"},
            "json",
        ), "first %r" % (seen[0],)
        assert seen[1] == (
            API + REPO + "/releases/assets/77",
            {"Authorization": "Bearer tok", "Accept": "application/octet-stream"},
            "get",
        ), "second %r" % (seen[1],)
        assert len(seen) == 2, "requests %r" % seen
        assert len(sessions) == 1 and sessions[0].closed, (
            "session not one, or left open"
        )

    def test_no_token_no_auth_header():
        _rm_tree(ROOT)
        _box(_v1())
        got, seen, sessions = via_https(token="")
        assert got == "installed " + TAG, "result %r" % got
        assert seen[1][1] == {"Accept": "application/octet-stream"}, "headers %r" % (
            seen[1][1],
        )

    def test_session_closed_after_failure():
        got, seen, sessions = via_https(github=_FakeGitHub(error=HttpError(403)))
        assert got == "failed: http 403", "result %r" % got
        assert len(sessions) == 1 and sessions[0].closed, (
            "session left open after a failure"
        )

    runner.run_test("Good bundle staged and installed", fresh(test_run_installs))
    runner.run_test(
        "Same version: up to date, no download", fresh(test_run_up_to_date_no_download)
    )
    runner.run_test("Bad version skipped", fresh(test_bad_version_skipped))
    runner.run_test("Release without a bundle fails", fresh(test_run_no_bundle_failed))
    runner.run_test(
        "Bundle for another tag refused", fresh(test_run_bundle_version_mismatch)
    )
    runner.run_test(
        "Shadowing .py deleted, back on rollback", fresh(test_run_deletes_shadowed_py)
    )
    runner.run_test("No file changes adopts the tag", fresh(test_no_change_adopts_tag))
    runner.run_test("Delete-only update installs", fresh(test_delete_only_installs))
    runner.run_test("HTTP 403 is failed, not raised", fresh(test_http_403_failed))
    runner.run_test("Run never raises", fresh(test_run_never_raises))
    runner.run_test("Failure text names the cause", fresh(test_failed_text))
    runner.run_test(
        "Install refused, stage removed", fresh(test_install_refused_stage_removed)
    )
    runner.run_test(
        "Install that leaves its journal resets", fresh(test_journal_left_resets)
    )
    runner.run_test("Off does nothing", fresh(test_off))
    runner.run_test(
        "Trial active refused before download", fresh(test_trial_active_refused)
    )
    runner.run_test(
        "Abort after the last chunk: no install", fresh(test_abort_after_last_file)
    )
    runner.run_test("Idle run labelled in the log", fresh(test_idle_logged))
    runner.run_test(
        "Default fetch sends token and Accept",
        fresh(test_default_fetch_sends_token_and_accept),
    )
    runner.run_test("No token, no Authorization", fresh(test_no_token_no_auth_header))
    runner.run_test(
        "Session closed after a failure", fresh(test_session_closed_after_failure)
    )

    # Leave the board as we found it.
    _rm_tree(ROOT)
    for name, path in real.items():
        setattr(recovery, name, path)

    def test_real_files_untouched():
        for path, before in real_files.items():
            after = _read(path)
            assert after == before, "%s changed: %r -> %r" % (path, before, after)

    runner.run_test(
        "Box's real state, journal and log untouched", test_real_files_untouched
    )
    runner.final_report()


if __name__ == "__main__":
    run()
