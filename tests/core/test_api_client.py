"""Tests for core.api, the Maker Portal client.

https.request and the WiFi interface are faked: no request leaves the box.
Every failure must come back as a refusal (fail closed) and never raise.
"""
import json

from tests.test_helper import TestRunner
import recovery
from config import settings
from core import api
from net import https
from net.https import HttpError

URL = 'https://portal.example'
ACT = 'https://portal.example/api/v2/box-activation.php?mac=80b54eea38b0'
BOX = 'https://portal.example/api/v2/box.php?mac=80b54eea38b0'
USER = '23BE9603'      # 599692803
TRAINER = '73889103'   # 1938329859
PROXY = 'C38EC002'     # 3280912386
NOAUTH = '83EE8F03'    # 2213449475


class _WLAN:
    def __init__(self, up):
        self.up = up

    def isconnected(self):
        return self.up

    def config(self, key):
        assert key == 'mac', key
        return b'\x80\xb5\x4e\xea\x38\xb0'


class _Network:
    STA_IF = 0

    def __init__(self, up):
        self.wlan = _WLAN(up)

    def WLAN(self, interface):
        assert interface == 0, interface
        return self.wlan


class _Server:
    """Stands in for https.request: records each call, then replies or raises."""

    def __init__(self, status=200, body=b'', raises=None):
        self.status = status
        self.body = body
        self.raises = raises
        self.calls = []

    def __call__(self, method, url, headers=None, body=None, max_bytes=4096, timeout_s=None):
        self.calls.append({'method': method, 'url': url, 'headers': headers or {},
                           'body': body, 'timeout_s': timeout_s})
        if self.raises:
            raise self.raises
        return self.status, self.body


CONFIG = '/t_api_config.json'
NO_FILE = object()


def _with(fn, status=200, body=b'', raises=None, up=True, url=URL, timeout_s=5, config=None):
    """Run fn(server, logs) with the fakes in place; put everything back.

    The server URL comes from a config file at CONFIG holding {"url": url},
    or the raw text config, or no file at all for url=NO_FILE."""
    import os
    server = _Server(status, body, raises)
    logs = []
    saved = (https.request, api.network, recovery.log, api.CONFIG_PATH,
             getattr(settings, 'API_TIMEOUT_S', None))
    try:
        os.remove(CONFIG)
    except OSError:
        pass
    if config is not None or url is not NO_FILE:
        with open(CONFIG, 'w') as f:
            f.write(config if config is not None else json.dumps({'url': url}))
    https.request = server
    api.network = _Network(up)
    recovery.log = lambda tag, msg: logs.append((tag, msg))
    api.CONFIG_PATH = CONFIG
    settings.API_TIMEOUT_S = timeout_s
    try:
        fn(server, logs)
    finally:
        https.request, api.network, recovery.log, api.CONFIG_PATH = saved[:4]
        settings.API_TIMEOUT_S = saved[4]
        try:
            os.remove(CONFIG)
        except OSError:
            pass


def _equipment(timeout):
    return json.dumps({'equipment': {'id': 5, 'timeout': timeout}, 'user': {'id': 1}}).encode()


def _one(server):
    assert len(server.calls) == 1, "want 1 request, got %d" % len(server.calls)
    return server.calls[0]


def run():
    runner = TestRunner()

    # --- card ids and MAC -------------------------------------------------

    def test_card_id_decimal():
        for uid, want in ((USER, '599692803'), (TRAINER, '1938329859'),
                          ('c38ec002', '3280912386'), ('0', '0')):
            got = api.card_id(uid)
            assert got == want, "%r -> %r, want %r" % (uid, got, want)

    def test_card_id_bad():
        for uid in (None, '', 'XYZ', '0x23', '-1', '+1', '12 3', 1234):
            got = api.card_id(uid)
            assert got is None, "%r -> %r, want None" % (uid, got)

    def test_mac():
        def check(server, logs):
            assert api.mac() == '80b54eea38b0', api.mac()
        _with(check)

    def test_bad_uid_sends_nothing():
        def check(server, logs):
            assert api.activate('ZZ') == (False, None)
            assert api.change_card(USER, 'ZZ') is None
            assert api.change_card('ZZ', USER) is None
            assert api.deactivate('') is False
            assert server.calls == [], server.calls
        _with(check)

    # --- activate ---------------------------------------------------------

    def test_activate_request():
        def check(server, logs):
            api.activate(USER)
            call = _one(server)
            assert call['method'] == 'PUT', call['method']
            assert call['url'] == ACT, call['url']
            assert call['headers'].get('Authorization') == 'Bearer 599692803', call['headers']
            assert not call['body'], call['body']
            assert call['timeout_s'] == 7, call['timeout_s']
        _with(check, body=_equipment(30), timeout_s=7)

    def test_url_trailing_slash():
        def check(server, logs):
            api.activate(USER)
            assert _one(server)['url'] == ACT, server.calls[0]['url']
        _with(check, body=_equipment(30), url=URL + '/')

    def test_activate_timeouts():
        for timeout, want in ((30, (True, 1800000)), (1, (True, 60000)), (0, (True, 0)),
                              (None, (True, None)), ('abc', (True, None)),
                              (-5, (True, None)), (True, (True, None))):
            def check(server, logs):
                got = api.activate(USER)
                assert got == want, "timeout %r -> %r, want %r" % (timeout, got, want)
            _with(check, body=_equipment(timeout))

    def test_activate_missing_timeout():
        def check(server, logs):
            got = api.activate(USER)
            assert got == (True, None), got
        _with(check, body=b'{"equipment": {"id": 5}, "user": {}}')

    def test_activate_bad_body_refused():
        for body in (b'', b'not json', b'[1]', b'{"user": {}}', b'{"equipment": 5}'):
            def check(server, logs):
                got = api.activate(USER)
                assert got == (False, None), "body %r -> %r" % (body, got)
                assert logs, "body %r not logged" % (body,)
            _with(check, body=body)

    def test_activate_refusals():
        for status in (401, 403, 400, 404, 409, 500):
            def check(server, logs):
                got = api.activate(USER)
                assert got == (False, None), "%d -> %r" % (status, got)
            _with(check, status=status, body=_equipment(30))

    # --- change card ------------------------------------------------------

    def test_change_card_request():
        def check(server, logs):
            api.change_card(USER, TRAINER)
            call = _one(server)
            assert call['method'] == 'POST', call['method']
            assert call['url'] == ACT, call['url']
            assert call['headers'].get('Authorization') == 'Bearer 599692803', call['headers']
            assert call['headers'].get('Content-Type') == 'application/json', call['headers']
            assert json.loads(call['body']) == {'card': 1938329859}, call['body']
            assert call['timeout_s'] == 5, call['timeout_s']
        _with(check, body=b'training')

    def test_change_card_modes():
        for body, want in ((b'user', 'user'), (b'proxy', 'proxy'), (b'training', 'training'),
                           (b'proxy\n', 'proxy'), (b'admin', None), (b'', None),
                           (b'\xff\xfe', None)):
            def check(server, logs):
                got = api.change_card(USER, PROXY)
                assert got == want, "body %r -> %r, want %r" % (body, got, want)
            _with(check, body=body)

    def test_change_card_refusals():
        for status in (400, 401, 403, 404, 500):
            def check(server, logs):
                got = api.change_card(USER, PROXY)
                assert got is None, "%d -> %r" % (status, got)
            _with(check, status=status, body=b'proxy')

    # --- deactivate -------------------------------------------------------

    def test_deactivate_request():
        def check(server, logs):
            assert api.deactivate(USER) is True
            call = _one(server)
            assert call['method'] == 'DELETE', call['method']
            assert call['url'] == ACT, call['url']
            assert call['headers'].get('Authorization') == 'Bearer 599692803', call['headers']
            assert call['timeout_s'] == 5, call['timeout_s']
        _with(check)

    def test_deactivate_refusals():
        for status in (401, 403, 500):
            def check(server, logs):
                got = api.deactivate(USER)
                assert got is False, "%d -> %r" % (status, got)
                assert logs, "%d not logged" % status
            _with(check, status=status)

    # --- startup ----------------------------------------------------------

    def test_startup_request():
        def check(server, logs):
            assert api.startup() == 'ok'
            call = _one(server)
            assert call['method'] == 'POST', call['method']
            assert call['url'] == BOX, call['url']
            assert 'Authorization' not in call['headers'], call['headers']
            assert call['body'] in ('startup', b'startup'), call['body']
            assert call['timeout_s'] == 5, call['timeout_s']
        _with(check, body=b'{"id": 5}')

    def test_startup_answers():
        for status, want in ((404, 'not registered'), (409, 'out of service'),
                             (500, 'error'), (401, 'error')):
            def check(server, logs):
                got = api.startup()
                assert got == want, "%d -> %r, want %r" % (status, got, want)
            _with(check, status=status)

    # --- failures ---------------------------------------------------------

    def _all_refuse(server, logs, what):
        assert api.activate(USER) == (False, None), what
        assert api.change_card(USER, PROXY) is None, what
        assert api.deactivate(USER) is False, what
        assert api.startup() == 'error', what

    def test_errors_refuse_and_log():
        for err in (OSError(110), MemoryError(), HttpError(0, 'short body'), ValueError('x')):
            def check(server, logs):
                _all_refuse(server, logs, repr(err))
                assert len(server.calls) == 4, "%r: %d calls" % (err, len(server.calls))
                assert len(logs) >= 4 and all(tag == 'api' for tag, _ in logs), logs
            _with(check, raises=err)

    def test_wifi_down_sends_nothing():
        def check(server, logs):
            _all_refuse(server, logs, 'wifi down')
            assert server.calls == [], server.calls
            assert logs, "wifi down not logged"
        _with(check, up=False)

    # --- mock -------------------------------------------------------------

    def test_empty_url_uses_mock():
        def check(server, logs):
            assert api.activate(USER) == (True, None)
            assert api.activate(NOAUTH) == (False, None)
            assert api.activate('ZZ') == (False, None)
            assert api.change_card(TRAINER, USER) == 'training'
            assert api.change_card(USER, NOAUTH) is None
            assert api.change_card(USER, PROXY) == 'proxy'
            assert api.change_card(USER, USER) == 'user'
            assert api.deactivate(USER) is True
            assert api.startup() == 'ok'
            assert server.calls == [], server.calls
        _with(check, url='')

    def test_no_config_refuses():
        # A box without its server address must not fall back to the test cards.
        def check(server, logs):
            _all_refuse(server, logs, 'no config')
            assert server.calls == [], server.calls
            assert len(logs) >= 4, "not logged: %r" % (logs,)
        _with(check, url=NO_FILE)

    def test_bad_config_refuses():
        for text in ('not json', '[]', '{}', '{"url": 5}', '{"url": null}'):
            def check(server, logs):
                _all_refuse(server, logs, text)
                assert server.calls == [], "%r: %r" % (text, server.calls)
                assert logs, "%r not logged" % text
            _with(check, config=text)

    # --- hold(): the calls of one tap share one connection ------------------

    class _FakeSession:
        made = []

        def __init__(self):
            self.calls = []
            self.closed = 0
            self.raises = None
            _FakeSession.made.append(self)

        def request(self, method, url, headers=None, body=None, max_bytes=4096, timeout_s=None):
            self.calls.append((method, url, timeout_s))
            if self.raises:
                raise self.raises
            return 200, _equipment(0)

        def close(self):
            self.closed += 1

    def _with_sessions(check, raises=None):
        def fn(server, logs):
            saved = https.Session
            _FakeSession.made = []
            https.Session = _FakeSession
            try:
                check(server, logs, raises)
            finally:
                api.release()
                https.Session = saved
        _with(fn)

    def test_hold_shares_one_connection():
        def check(server, logs, raises):
            api.hold()
            api.activate(USER)
            api.change_card(USER, PROXY)
            api.deactivate(USER)
            assert len(_FakeSession.made) == 1, "%d sessions" % len(_FakeSession.made)
            s = _FakeSession.made[0]
            assert [c[0] for c in s.calls] == ['PUT', 'POST', 'DELETE'], s.calls
            assert s.calls[0][1] == ACT and s.calls[0][2] == 5, s.calls[0]
            assert server.calls == [], "one-shot requests while held: %r" % (server.calls,)
            api.release()
            assert s.closed == 1, "closed %d times" % s.closed
        _with_sessions(check)

    def test_release_ends_sharing():
        def check(server, logs, raises):
            api.hold()
            api.activate(USER)
            api.release()
            api.deactivate(USER)
            assert len(_FakeSession.made) == 1, "%d sessions" % len(_FakeSession.made)
            assert [c['method'] for c in server.calls] == ['DELETE'], server.calls
        _with_sessions(check)

    def test_hold_without_calls_opens_nothing():
        def check(server, logs, raises):
            api.hold()
            api.release()
            assert _FakeSession.made == [], "session opened with no call"
        _with_sessions(check)

    def test_held_error_refuses():
        def check(server, logs, raises):
            api.hold()
            api.activate(USER)
            _FakeSession.made[0].raises = OSError(113)
            assert api.change_card(USER, PROXY) is None, "error not refused"
            assert logs, "error not logged"
            api.release()
            assert _FakeSession.made[0].closed == 1, "not closed after an error"
        _with_sessions(check)

    # --- the TLS context is freed between taps -------------------------------

    def _with_forget(check):
        """check(server, logs, forgot): forgot gets len(server.calls) each
        time api frees the TLS context."""
        def fn(server, logs, *rest):
            had = hasattr(https, 'forget_context')
            saved = getattr(https, 'forget_context', None)
            forgot = []
            https.forget_context = lambda: forgot.append(len(server.calls))
            try:
                check(server, logs, forgot)
            finally:
                if had:
                    https.forget_context = saved
                else:
                    del https.forget_context
        return fn

    def test_one_shot_frees_tls():
        def check(server, logs, forgot):
            api.activate(USER)
            assert forgot == [1], "freed %r, want once after the call" % (forgot,)
        _with(_with_forget(check), body=_equipment(30))

    def test_one_shot_error_frees_tls():
        def check(server, logs, forgot):
            assert api.activate(USER) == (False, None)
            assert forgot == [1], "freed %r after an error" % (forgot,)
        _with(_with_forget(check), raises=OSError(113))

    def test_held_frees_tls_on_release():
        def check(server, logs, forgot):
            api.hold()
            api.activate(USER)
            api.change_card(USER, PROXY)
            assert forgot == [], "freed while held: %r" % (forgot,)
            api.release()
            assert forgot == [0], "freed %r on release, want once" % (forgot,)
        _with_sessions(lambda server, logs, raises: _with_forget(check)(server, logs))

    def test_no_server_call_frees_nothing():
        def check(server, logs, forgot):
            api.hold()
            api.release()
            api.activate(USER)   # WiFi down: no request
            assert forgot == [], "freed %r with no call" % (forgot,)
        _with(_with_forget(check), up=False)

    runner.run_test("card_id: hex uid -> decimal", test_card_id_decimal)
    runner.run_test("card_id: bad uid -> None", test_card_id_bad)
    runner.run_test("mac: 12 lowercase hex", test_mac)
    runner.run_test("Bad uid sends nothing", test_bad_uid_sends_nothing)
    runner.run_test("activate: PUT, URL, bearer, timeout", test_activate_request)
    runner.run_test("Config url trailing slash ignored", test_url_trailing_slash)
    runner.run_test("activate: timeout minutes -> ms", test_activate_timeouts)
    runner.run_test("activate: no timeout -> None", test_activate_missing_timeout)
    runner.run_test("activate: bad body refused", test_activate_bad_body_refused)
    runner.run_test("activate: non-200 refused", test_activate_refusals)
    runner.run_test("change_card: POST, bearer, JSON card", test_change_card_request)
    runner.run_test("change_card: modes from body", test_change_card_modes)
    runner.run_test("change_card: non-200 -> None", test_change_card_refusals)
    runner.run_test("deactivate: DELETE, bearer", test_deactivate_request)
    runner.run_test("deactivate: non-200 -> False", test_deactivate_refusals)
    runner.run_test("startup: POST box.php, no bearer", test_startup_request)
    runner.run_test("startup: 404, 409, others", test_startup_answers)
    runner.run_test("Errors refuse, log, never raise", test_errors_refuse_and_log)
    runner.run_test("WiFi down: refuse, no request", test_wifi_down_sends_nothing)
    runner.run_test("Config url '' uses the mock", test_empty_url_uses_mock)
    runner.run_test("No config file: refuse every card", test_no_config_refuses)
    runner.run_test("Bad config file: refuse every card", test_bad_config_refuses)
    runner.run_test("hold(): one connection for the calls of a tap", test_hold_shares_one_connection)
    runner.run_test("release() ends sharing", test_release_ends_sharing)
    runner.run_test("hold() with no call opens nothing", test_hold_without_calls_opens_nothing)
    runner.run_test("Held connection error: refused, logged, closed", test_held_error_refuses)
    runner.run_test("One-shot call frees the TLS context after", test_one_shot_frees_tls)
    runner.run_test("One-shot error frees the TLS context", test_one_shot_error_frees_tls)
    runner.run_test("Held calls free the TLS context on release", test_held_frees_tls_on_release)
    runner.run_test("No server call frees nothing", test_no_server_call_frees_nothing)
    runner.final_report()


if __name__ == "__main__":
    run()
