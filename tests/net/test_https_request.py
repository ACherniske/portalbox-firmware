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
    from tests.net.test_https import _online
    _online()   # joined already by run(); a no-op then
    status, body = https.request('PUT', url, timeout_s=10)
    assert status == 401, 'status %r body %r' % (status, body)


def run():
    from tests.net.test_https import join_first
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
    runner.final_report()
