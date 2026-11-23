"""Tests for ota_stage.stage_bundle: an update streamed from one bundle.

Offline: the bundle is built here in the format scripts/build.py writes and
fed in chunks by a fake fetch. Every recovery path is moved under ROOT, so
the box's real files are never touched.
"""

import json
import uhashlib as hashlib
import ubinascii as binascii

from tests.test_helper import TestRunner
from tests.core.test_recovery import ROOT, _REDIRECTS, _exists, _rm_tree, _write, _read
import recovery
from net import ota_stage
from net.ota_target import OtaError


def _git_sha(data):
    h = hashlib.sha1(("blob %d\0" % len(data)).encode())
    h.update(data)
    return binascii.hexlify(h.digest()).decode()


FILES = {
    "core/a.mpy": b"A" * 2500,  # several chunks
    "core/b.mpy": b"bee",
    "net/c.mpy": b"C" * 150,
    "certs/r.pem": b"",  # empty file
}


def _bundle(files=FILES, version="v2", mpy=None, fmt=1):
    """Bundle bytes as scripts/build.py writes them."""
    names = sorted(files)
    header = {
        "format": fmt,
        "mpy": mpy or ota_stage.firmware_mpy(),
        "version": version,
        "files": [[p, len(files[p]), _git_sha(files[p])] for p in names],
    }
    return json.dumps(header).encode() + b"\n" + b"".join(files[p] for p in names)


class _Fetch:
    """fetch(sink): feeds `data` in `size`-byte chunks. cut: stop that many
    bytes early. errors: raised one per call, once 500 bytes (past the
    header) went out."""

    def __init__(self, data, size=100, cut=0, errors=()):
        self.data = data
        self.size = size
        self.cut = cut
        self.errors = list(errors)
        self.calls = 0

    def __call__(self, sink):
        self.calls += 1
        end = len(self.data) - self.cut
        for at in range(0, end, self.size):
            sink.write(self.data[at : min(at + self.size, end)])
            if self.errors and at >= 500:
                raise self.errors.pop(0)


class _PlanFor:
    """plan_for(header): stages `paths`; records each header it saw."""

    def __init__(self, paths, error=None):
        self.paths = paths
        self.error = error
        self.headers = []

    def __call__(self, header):
        self.headers.append(header)
        if self.error:
            raise self.error
        files = [(p, sha, size) for p, size, sha in header["files"] if p in self.paths]
        return {"files": files, "deletes": [], "managed": list(self.paths)}


def _staged(path):
    return _read(recovery.STAGE_DIR + "/" + path)


def _raises(fn, text=None):
    try:
        fn()
    except OtaError as e:
        if text is not None:
            assert str(e) == text, "error %r, wanted %r" % (str(e), text)
        return e
    raise AssertionError("no OtaError")


def run():
    runner = TestRunner()
    real = {name: getattr(recovery, name) for name in _REDIRECTS}
    for name, path in _REDIRECTS.items():
        setattr(recovery, name, path)

    def fresh(test):
        def wrapped():
            _rm_tree(ROOT)
            _write(recovery.LIVE_ROOT + "/core/a.mpy", "live a")
            test()

        return wrapped

    def assert_clean(where):
        assert not _exists(recovery.STAGE_DIR), "%s: stage dir left" % where
        assert _read(recovery.LIVE_ROOT + "/core/a.mpy") == "live a", (
            "%s: live changed" % where
        )

    def test_stage_only_planned_files():
        plan_for = _PlanFor(["core/a.mpy", "certs/r.pem"])
        plan = ota_stage.stage_bundle(_Fetch(_bundle()), plan_for)
        assert [p for p, _, _ in plan["files"]] == ["certs/r.pem", "core/a.mpy"], plan
        assert _staged("core/a.mpy") == "A" * 2500, "a staged wrong"
        assert _staged("certs/r.pem") == "", "empty file not staged"
        assert not _exists(recovery.STAGE_DIR + "/core/b.mpy"), "unplanned b staged"
        assert not _exists(recovery.STAGE_DIR + "/net/c.mpy"), "unplanned c staged"
        h = plan_for.headers[0]
        assert h["version"] == "v2" and len(h["files"]) == 4, h
        assert _read(recovery.LIVE_ROOT + "/core/a.mpy") == "live a", "live changed"

    def test_mpy_version_mismatch_refused():
        plan_for = _PlanFor(["core/a.mpy"])
        box = ota_stage.firmware_mpy()
        _raises(
            lambda: ota_stage.stage_bundle(_Fetch(_bundle(mpy="6.2")), plan_for),
            "mpy 6.2, box %s" % box,
        )
        assert not plan_for.headers, "plan_for called"
        assert_clean("mpy mismatch")

    def test_bad_header_refused():
        no_newline = b"{" + b" " * 9000
        for data in (
            no_newline,
            b"not json\n",
            _bundle(fmt=2),
            b'{"format": 1, "mpy": "6.3", "version": "v", "files": [["a", 1]]}\n',
        ):
            _raises(
                lambda: ota_stage.stage_bundle(_Fetch(data), _PlanFor([])), "bad bundle"
            )
            assert_clean("bad header")

    def test_hash_mismatch():
        data = bytearray(_bundle())
        data[-200] ^= 1  # inside core/a.mpy's or later bytes
        e = _raises(
            lambda: ota_stage.stage_bundle(_Fetch(bytes(data)), _PlanFor(list(FILES)))
        )
        assert str(e).startswith("bad hash "), str(e)
        assert_clean("hash mismatch")

    def test_short_bundle():
        _raises(
            lambda: ota_stage.stage_bundle(
                _Fetch(_bundle(), cut=3), _PlanFor(["core/a.mpy"])
            ),
            "short bundle",
        )
        assert_clean("short")

    def test_long_bundle():
        _raises(
            lambda: ota_stage.stage_bundle(
                _Fetch(_bundle() + b"0123456789"), _PlanFor(["core/a.mpy"])
            ),
            "bundle too long",
        )
        assert_clean("long")

    def test_no_space():
        _raises(
            lambda: ota_stage.stage_bundle(
                _Fetch(_bundle()), _PlanFor(["core/a.mpy"]), free_bytes=1000
            ),
            "no space",
        )
        assert_clean("no space")

    def test_abort_mid_bundle():
        n = [0]

        def abort():
            n[0] += 1
            return n[0] > 2

        _raises(
            lambda: ota_stage.stage_bundle(
                _Fetch(_bundle()), _PlanFor(list(FILES)), abort
            ),
            "aborted",
        )
        assert_clean("abort")

    def test_dropped_bundle_restarted_once():
        fetch = _Fetch(_bundle(), errors=[OSError(113)])
        plan_for = _PlanFor(["core/a.mpy"])
        ota_stage.stage_bundle(fetch, plan_for)
        assert fetch.calls == 2 and len(plan_for.headers) == 2, (
            fetch.calls,
            len(plan_for.headers),
        )
        assert _staged("core/a.mpy") == "A" * 2500, "restart staged wrong"

    def test_second_drop_gives_up():
        fetch = _Fetch(_bundle(), errors=[OSError(113), OSError(113)])
        try:
            ota_stage.stage_bundle(fetch, _PlanFor(["core/a.mpy"]))
        except OSError:
            pass
        else:
            raise AssertionError("second drop not raised")
        assert fetch.calls == 2, fetch.calls
        assert_clean("second drop")

    def test_bad_hash_not_retried():
        data = bytearray(_bundle())
        data[-1] ^= 1
        fetch = _Fetch(bytes(data))
        _raises(lambda: ota_stage.stage_bundle(fetch, _PlanFor(list(FILES))))
        assert fetch.calls == 1, fetch.calls

    def test_plan_for_error_stops():
        _raises(
            lambda: ota_stage.stage_bundle(
                _Fetch(_bundle()), _PlanFor([], OtaError("anchor changed, needs cable"))
            ),
            "anchor changed, needs cable",
        )
        assert_clean("plan error")

    def test_firmware_mpy():
        import sys

        v = sys.implementation._mpy
        assert ota_stage.firmware_mpy() == "%d.%d" % (v & 0xFF, (v >> 8) & 3), (
            ota_stage.firmware_mpy()
        )
        assert ota_stage.firmware_mpy() == "6.3", ota_stage.firmware_mpy()

    runner.run_test("Only planned files staged", fresh(test_stage_only_planned_files))
    runner.run_test(
        "Other .mpy version refused", fresh(test_mpy_version_mismatch_refused)
    )
    runner.run_test("Bad header refused", fresh(test_bad_header_refused))
    runner.run_test("Hash mismatch refused", fresh(test_hash_mismatch))
    runner.run_test("Short bundle refused", fresh(test_short_bundle))
    runner.run_test("Long bundle refused", fresh(test_long_bundle))
    runner.run_test("No space refused", fresh(test_no_space))
    runner.run_test("Abort mid-bundle", fresh(test_abort_mid_bundle))
    runner.run_test(
        "Dropped bundle restarted once", fresh(test_dropped_bundle_restarted_once)
    )
    runner.run_test("Second drop gives up", fresh(test_second_drop_gives_up))
    runner.run_test("Bad hash not retried", fresh(test_bad_hash_not_retried))
    runner.run_test("plan_for error stops it", fresh(test_plan_for_error_stops))
    runner.run_test("Firmware .mpy version", test_firmware_mpy)

    _rm_tree(ROOT)
    for name, path in real.items():
        setattr(recovery, name, path)
    runner.final_report()


if __name__ == "__main__":
    run()
