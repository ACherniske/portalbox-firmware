"""Tests for net.https.request: PUT, POST and DELETE over verified TLS.

The live test needs WiFi (/config/wifi_config.json) and a Maker Portal
server in /config/api_config.json ({"url": "https://..."}; skipped without
one); it sends a request the server refuses before touching its database.
"""
import io
import json

from tests.test_helper import TestRunner
import recovery
from net import https
from net.https import HttpError

API_CONFIG = '/config/api_config.json'


def _live_url():
    """The box's Maker Portal activation URL, or None without a server."""
    try:
        with open(API_CONFIG) as f:
            url = json.load(f)['url']
    except Exception:
        return None
    if not isinstance(url, str) or not url:
        return None
    return url.rstrip('/') + '/api/v2/box-activation.php?mac=000000000000'


class _FakeNet:
    """Stands in for socket, tls and the TLS stream: records what is
    written, the timeout set, and each step (and feed) in order."""
    PROTOCOL_TLS_CLIENT = 0
    CERT_REQUIRED = 2

    def __init__(self, reply):
        head, _, body = reply.partition(b'\r\n\r\n')
        self.head = [line + b'\r\n' for line in head.split(b'\r\n')] + [b'\r\n']
        self.body = io.BytesIO(body)
        self.sent = b''
        self.timeouts = []
        self.events = []
        self.closed = False

    # socket
    def getaddrinfo(self, name, port):
        self.events.append('dns')
        return [(2, 1, 0, '', ('10.0.0.1', port))]

    def socket(self):
        return self

    def settimeout(self, s):
        self.timeouts.append(s)

    def connect(self, addr):
        self.events.append('connect')

    # tls
    def SSLContext(self, protocol):
        return self

    def load_verify_locations(self, data):
        pass

    def wrap_socket(self, sock, server_hostname=None):
        self.events.append('handshake')
        return self

    # stream
    def write(self, data):
        self.sent += bytes(data)
        return len(data)

    def readline(self):
        self.events.append('head')
        return self.head.pop(0) if self.head else b''

    def read(self, n):
        return self.body.read(n)

    def close(self):
        self.closed = True
        self.events.append('close')


def _with_net(reply, fn):
    fake = _FakeNet(reply)
    saved = (https.socket, https.tls, https._roots, recovery.feed, https._ctx)
    https.socket = https.tls = fake
    https._roots = lambda: b''
    https._ctx = None   # a context cached by an earlier test is a real one
    recovery.feed = lambda: fake.events.append('feed')
    try:
        result = fn()
    finally:
        https.socket, https.tls, https._roots, recovery.feed, https._ctx = saved
    return fake, result


def _head_and_body(sent):
    head, _, body = sent.partition(b'\r\n\r\n')
    lines = head.decode().split('\r\n')
    return lines[0], lines[1:], body


OK_EMPTY = b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n'


# --- tests -----------------------------------------------------------------

def test_put_without_body():
    fake, result = _with_net(OK_EMPTY, lambda: https.request(
        'PUT', 'https://portal.example/api/v2/box-activation.php?mac=ab12',
        headers={'Authorization': 'Bearer 42'}))
    first, headers, body = _head_and_body(fake.sent)
    assert first == 'PUT /api/v2/box-activation.php?mac=ab12 HTTP/1.0', first
    assert 'Host: portal.example' in headers, headers
    assert 'Authorization: Bearer 42' in headers, headers
    assert 'Content-Length: 0' in headers, headers
    assert body == b'', body
    assert result == (200, b''), result


def test_post_sends_body():
    fake, result = _with_net(b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nproxy',
                             lambda: https.request(
                                 'POST', 'https://portal.example/x', body='{"card": 7}',
                                 headers={'Content-Type': 'application/json'}))
    first, headers, body = _head_and_body(fake.sent)
    assert first == 'POST /x HTTP/1.0', first
    assert 'Content-Length: 11' in headers, headers
    assert 'Content-Type: application/json' in headers, headers
    assert body == b'{"card": 7}', body
    assert result == (200, b'proxy'), result


def test_delete_and_bytes_body():
    fake, result = _with_net(OK_EMPTY, lambda: https.request(
        'DELETE', 'https://portal.example/y', body=b'startup'))
    first, headers, body = _head_and_body(fake.sent)
    assert first == 'DELETE /y HTTP/1.0', first
    assert 'Content-Length: 7' in headers and body == b'startup', (headers, body)


def test_refusal_returned_not_raised():
    fake, result = _with_net(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 6\r\n\r\nno way',
                             lambda: https.request('PUT', 'https://portal.example/z'))
    assert result == (403, b'no way'), result
    assert fake.closed, 'stream left open'


def test_redirect_returned_not_followed():
    fake, result = _with_net(
        b'HTTP/1.1 302 Found\r\nLocation: https://evil.example/\r\nContent-Length: 0\r\n\r\n',
        lambda: https.request('PUT', 'https://portal.example/z'))
    assert result == (302, b''), result
    assert fake.events.count('connect') == 1, fake.events


def test_body_without_length_read_to_end():
    fake, result = _with_net(b'HTTP/1.0 401 Unauthorized\r\n\r\nNo Authorization header provided.',
                             lambda: https.request('PUT', 'https://portal.example/z'))
    assert result == (401, b'No Authorization header provided.'), result


def test_body_over_max_is_error():
    def call():
        try:
            https.request('PUT', 'https://portal.example/z', max_bytes=4)
        except HttpError as e:
            return e.status
        return None
    fake, status = _with_net(b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello', call)
    assert status == 0, status
    assert fake.closed, 'stream left open after error'


def test_timeout_is_per_call():
    fake, _ = _with_net(OK_EMPTY, lambda: https.request('PUT', 'https://portal.example/z',
                                                        timeout_s=5))
    assert fake.timeouts == [5], fake.timeouts
    fake, _ = _with_net(OK_EMPTY, lambda: https.request('PUT', 'https://portal.example/z'))
    assert fake.timeouts == [https.TIMEOUT_S], fake.timeouts


def test_feeds_through_each_step():
    fake, _ = _with_net(OK_EMPTY, lambda: https.request('PUT', 'https://portal.example/z'))
    ev = fake.events
    dns, connect, shake = ev.index('dns'), ev.index('connect'), ev.index('handshake')
    assert 'feed' in ev[:dns], "no feed before DNS: %r" % ev
    assert 'feed' in ev[dns + 1:connect], "no feed between DNS and connect: %r" % ev
    assert 'feed' in ev[connect + 1:shake], "no feed between connect and handshake: %r" % ev
    last_head = len(ev) - 1 - ev[::-1].index('head')
    assert 'feed' in ev[last_head + 1:], "no feed after the reply head: %r" % ev


def test_http_url_refused():
    try:
        https.request('PUT', 'http://portal.example/z')
    except ValueError:
        return
    raise AssertionError('plain http accepted')


def test_live_refusal_over_verified_tls():
    """The server answers a PUT with no card 401, before any database
    access: proves method, TLS roots and status handling end to end."""
    url = _live_url()
    if url is None:
        print('SKIP (no server in %s) ' % API_CONFIG, end='')
        return
    from tests.net.wifi_join import _online
    _online()   # joined already by run(); a no-op then
    status, body = https.request('PUT', url, timeout_s=10)
    assert status == 401, 'status %r body %r' % (status, body)


# --- Session.request: one kept connection for a run of calls ----------------

class _KeptConn:
    """One TLS connection: serves `replies` in order, one per request; after
    `close_after` replies it reads as closed by the server (b'').
    requests: [request line, headers dict, body bytes]."""

    def __init__(self, host, timeout_s, replies, close_after):
        self.host = host
        self.timeout_s = timeout_s
        self.replies = list(replies)
        self.close_after = close_after
        self.requests = []
        self.buf = io.BytesIO(b'')
        self.closed = False

    def write(self, data):
        text = bytes(data).decode()
        first = text.split('\r\n', 1)[0]
        if ' HTTP/1.' not in first:   # a body after its head
            self.requests[-1][2] += bytes(data)
            return len(data)
        hdrs = {}
        for line in text.split('\r\n')[1:]:
            if line:
                k, _, v = line.partition(':')
                hdrs[k.strip()] = v.strip()
        self.requests.append([first, hdrs, b''])
        served = len(self.requests) - 1
        if self.replies and (self.close_after is None or served < self.close_after):
            self.buf = io.BytesIO(self.replies.pop(0))
        else:
            self.buf = io.BytesIO(b'')
        return len(data)

    def readline(self):
        return self.buf.readline()

    def read(self, n):
        return self.buf.read(n)

    def close(self):
        self.closed = True


class _Connector:
    """Stands in for https._connect: hands out the next _KeptConn from
    `plan` (lists of replies, or (replies, close_after))."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.made = []

    def __call__(self, host, timeout_s=None):
        spec = self.plan.pop(0)
        replies, close_after = spec if isinstance(spec, tuple) else (spec, None)
        conn = _KeptConn(host, timeout_s, replies, close_after)
        self.made.append(conn)
        return conn


def _with_kept(plan, fn):
    saved = (https._connect, recovery.feed)
    fake = _Connector(plan)
    https._connect = fake
    recovery.feed = lambda: None
    s = https.Session()
    try:
        return fn(s, fake)
    finally:
        s.close()
        https._connect, recovery.feed = saved


def _r(status, body=b'', extra=''):
    head = 'HTTP/1.1 %d X\r\n%sContent-Length: %d\r\n\r\n' % (status, extra, len(body))
    return head.encode() + body


def _raises(fn):
    try:
        fn()
    except (OSError, HttpError):
        return True
    return False


URL = 'https://portal.example/api/v2/box-activation.php?mac=ab12'
LINE = '/api/v2/box-activation.php?mac=ab12 HTTP/1.1'


def test_session_request_reuses_connection():
    def run(s, fake):
        assert s.request('POST', URL, {'Authorization': 'Bearer 1'}, '{"card": 2}') == (403, b'no')
        assert s.request('POST', URL, body=b'{"card": 3}') == (200, b'training')
        assert s.request('DELETE', URL) == (200, b'')
        assert len(fake.made) == 1, '%d connections' % len(fake.made)
        lines = [r[0] for r in fake.made[0].requests]
        assert lines == ['POST ' + LINE, 'POST ' + LINE, 'DELETE ' + LINE], lines
        first = fake.made[0].requests[0]
        assert first[1].get('Authorization') == 'Bearer 1', first[1]
        assert first[1].get('Host') == 'portal.example', first[1]
        assert first[1].get('Content-Length') == '11' and first[2] == b'{"card": 2}', first
        last = fake.made[0].requests[2]
        assert last[1].get('Content-Length') == '0', last
    _with_kept([[_r(403, b'no'), _r(200, b'training'), _r(200)]], run)


def test_session_request_not_resent():
    """A request that fails on a reused connection may have reached the
    server: it is not sent again. The next call reconnects."""
    def run(s, fake):
        assert s.request('PUT', URL) == (200, b'')
        assert _raises(lambda: s.request('PUT', URL)), 'failed request did not raise'
        assert len(fake.made) == 1, 'request sent again on a new connection'
        assert fake.made[0].closed, 'dead connection kept'
        assert s.request('DELETE', URL) == (200, b'')
        assert len(fake.made) == 2, '%d connections' % len(fake.made)
    _with_kept([([_r(200)], 1), [_r(200)]], run)


def test_session_request_timeout():
    def run(s, fake):
        s.request('PUT', URL, timeout_s=5)
        assert fake.made[0].timeout_s == 5, fake.made[0].timeout_s
    _with_kept([[_r(200)]], run)


def test_session_request_honours_close():
    def run(s, fake):
        assert s.request('PUT', URL) == (200, b'')
        assert fake.made[0].closed, 'kept a connection the server closes'
        s.request('DELETE', URL)
        assert len(fake.made) == 2, '%d connections' % len(fake.made)
    _with_kept([[_r(200, b'', 'Connection: close\r\n')], [_r(200)]], run)


def test_session_request_chunked_refused():
    def run(s, fake):
        try:
            s.request('PUT', URL)
        except HttpError as e:
            assert e.status == 0, e.status
        else:
            raise AssertionError('chunked reply accepted')
        assert fake.made[0].closed, 'connection kept after a chunked reply'
    chunked = b'HTTP/1.1 200 X\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n'
    _with_kept([[chunked]], run)


def test_session_request_over_max_closes():
    def run(s, fake):
        assert _raises(lambda: s.request('PUT', URL, max_bytes=4)), 'body over max accepted'
        assert fake.made[0].closed, 'connection kept after an error'
    _with_kept([[_r(200, b'hello')]], run)


def test_live_session_one_connection():
    """Two refused PUTs (no card: 401 before any database access) on one
    kept connection to the box's server."""
    url = _live_url()
    if url is None:
        print('SKIP (no server in %s) ' % API_CONFIG, end='')
        return
    real = https._connect
    made = [0]

    def counting(host, timeout_s=None):
        made[0] += 1
        return real(host, timeout_s)
    https._connect = counting
    s = https.Session()
    try:
        first = s.request('PUT', url, timeout_s=10)
        second = s.request('PUT', url, timeout_s=10)
    finally:
        s.close()
        https._connect = real
    assert first[0] == 401 and second[0] == 401, (first, second)
    assert made[0] == 1, '%d connections' % made[0]


def test_forget_context_rebuilds():
    saved = https._ctx
    try:
        https._ctx = None
        first = https._context()
        assert https._context() is first, 'context not cached'
        https.forget_context()
        assert https._ctx is None, 'context kept after forget'
        assert https._context() is not first, 'context not rebuilt'
    finally:
        https._ctx = saved


def run():
    from tests.net.wifi_join import join_first
    join_first()
    runner = TestRunner()
    runner.run_test("PUT without a body: line, Host, header, length 0", test_put_without_body)
    runner.run_test("POST sends its body with Content-Length", test_post_sends_body)
    runner.run_test("DELETE and a bytes body", test_delete_and_bytes_body)
    runner.run_test("A refusal (403) is returned, not raised", test_refusal_returned_not_raised)
    runner.run_test("A redirect is returned, not followed", test_redirect_returned_not_followed)
    runner.run_test("A body without length is read to the end", test_body_without_length_read_to_end)
    runner.run_test("Body over max_bytes is HttpError(0), stream closed", test_body_over_max_is_error)
    runner.run_test("timeout_s is per call, TIMEOUT_S by default", test_timeout_is_per_call)
    runner.run_test("Watchdog fed through DNS, connect, handshake, head", test_feeds_through_each_step)
    runner.run_test("Plain http refused", test_http_url_refused)
    runner.run_test("Live: server refuses a PUT with no card (401)", test_live_refusal_over_verified_tls)
    runner.run_test("Session.request reuses one connection", test_session_request_reuses_connection)
    runner.run_test("Session.request: failure on a reused connection not resent", test_session_request_not_resent)
    runner.run_test("Session.request: timeout_s reaches the connection", test_session_request_timeout)
    runner.run_test("Session.request honours Connection: close", test_session_request_honours_close)
    runner.run_test("Session.request refuses chunked replies", test_session_request_chunked_refused)
    runner.run_test("Session.request: body over max closes", test_session_request_over_max_closes)
    runner.run_test("Live: two calls on one kept connection", test_live_session_one_connection)
    runner.run_test("forget_context() frees the context; next call rebuilds", test_forget_context_rebuilds)
    runner.final_report()
