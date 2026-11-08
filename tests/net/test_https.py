"""Tests for net.https: verified HTTPS GET.

Live tests need WiFi (from /config/wifi_config.json) and, for the private
repo, a token. Set it in RAM before run(), never in a file:
    import tests.net.test_https as t; t.TOKEN = '...'; t.run()
Without TOKEN the token comes from /config/ota_config.json if present.
"""
import io
import json

from tests.test_helper import TestRunner
import recovery
from net import https
from net.https import HttpError
from tests.net.wifi_join import _online, join_first

TOKEN = None

COMMIT = 'c41e56a30d385d4ef0cc6db14dcd807c2462b28c'
RAW = 'https://raw.githubusercontent.com/ACherniske/portalbox-firmware/%s/' % COMMIT
MAIN_URL = RAW + 'src/main.py'
MAIN_HASH = '7bb289dd1d63165516ba33b953bf5e6e2a885847'
WIFI_URL = RAW + 'src/net/wifi.py'      # 20481 bytes: many chunks
WIFI_SIZE = 20481
WIFI_HASH = '6312e9a23113c9dc7981e7b95a33f50214d4ca06'


# --- helpers ---------------------------------------------------------------

def _token():
    if TOKEN:
        return TOKEN
    try:
        with open('/config/ota_config.json') as f:
            return json.load(f).get('token', '')
    except OSError:
        return ''


def _auth():
    tok = _token()
    return {'Authorization': 'Bearer ' + tok} if tok else {}


def _blob_hash(data):
    from net import ota
    return ota._git_blob_hash(data)


class CountingSink:
    """Counts and git-blob-hashes the chunks as they come, so the test never
    holds the whole body (a 20 KB join can fail on a fragmented heap)."""

    def __init__(self, size=0):
        import hashlib
        self.sha = hashlib.sha1(('blob %d\0' % size).encode())
        self.count = 0
        self.chunks = 0
        self.biggest = 0

    def write(self, chunk):
        self.sha.update(chunk)
        self.count += len(chunk)
        self.chunks += 1
        self.biggest = max(self.biggest, len(chunk))
        return len(chunk)

    def blob_hash(self):
        import binascii
        return binascii.hexlify(self.sha.digest()).decode()


class FakeStream:
    def __init__(self, body):
        self.body = io.BytesIO(body)
        self.taken = 0
        self.closed = False

    def read(self, n):
        data = self.body.read(n)
        self.taken += len(data)
        return data

    def close(self):
        self.closed = True


class LazyStream(FakeStream):
    """head + `pad` spaces + tail, made as it is read: a body far bigger
    than the heap that the test itself never holds."""

    def __init__(self, head, pad, tail):
        self.parts = [head, pad, tail]
        self.size = len(head) + pad + len(tail)
        self.taken = 0
        self.closed = False

    def read(self, n):
        out = b''
        while n > 0 and self.parts:
            part = self.parts[0]
            if isinstance(part, int):
                k = min(n, part)
                out += b' ' * k
                self.parts[0] = part - k
                if not self.parts[0]:
                    self.parts.pop(0)
            else:
                out += part[:n]
                k = min(n, len(part))
                self.parts[0] = part[n:]
                if not self.parts[0]:
                    self.parts.pop(0)
            n -= k
        self.taken += len(out)
        return out


class FakeNet:
    """Stands in for https._request: plays scripted responses and records
    (host, path, headers) of each call and the streams it handed out. A
    body may be bytes or a ready stream (LazyStream)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.streams = []

    def __call__(self, host, path, headers):
        self.calls.append((host, path, dict(headers)))
        status, hdrs, body = self.responses.pop(0)
        stream = body if hasattr(body, 'read') else FakeStream(body)
        self.streams.append(stream)
        return status, hdrs, stream

    def all_closed(self):
        return all(s.closed for s in self.streams)


class _FakeSocketModule:
    """Stands in for socket, tls and the TLS stream in https._request,
    recording each step (and each feed) in order."""
    PROTOCOL_TLS_CLIENT = 0
    CERT_REQUIRED = 2

    def __init__(self, head):
        self.events = []
        self.head = list(head)
        self.verify_mode = None

    # socket
    def getaddrinfo(self, name, port):
        self.events.append('dns')
        return [(2, 1, 0, '', ('10.0.0.1', port))]

    def socket(self):
        return self

    def settimeout(self, s):
        pass

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
        self.events.append('write')
        return len(data)

    def readline(self):
        self.events.append('head')
        return self.head.pop(0) if self.head else b''

    def close(self):
        self.events.append('close')


def test_feeds_after_dns_and_head():
    """DNS + connect, and head lines + the first body read, can each take
    up to TIMEOUT_S: feed after DNS and after the reply head too."""
    fake = _FakeSocketModule([b'HTTP/1.0 200 OK\r\n', b'Content-Length: 2\r\n', b'\r\n'])
    saved = (https.socket, https.tls, https._roots, recovery.feed, https._ctx)
    https.socket = https.tls = fake
    https._roots = lambda: b''
    https._ctx = None   # a context cached by an earlier test is a real one
    recovery.feed = lambda: fake.events.append('feed')
    try:
        status, reply, stream = https._request('example.com', '/x', {})
    finally:
        https.socket, https.tls, https._roots, recovery.feed, https._ctx = saved
    ev = fake.events
    assert status == 200 and reply == {'content-length': '2'}, "reply %r %r" % (status, reply)
    dns, connect = ev.index('dns'), ev.index('connect')
    assert 'feed' in ev[dns + 1:connect], "no feed between DNS and connect: %r" % ev
    last_head = len(ev) - 1 - ev[::-1].index('head')
    assert 'feed' in ev[last_head + 1:], "no feed after the reply head: %r" % ev


def _with_fake(responses, fn):
    real = https._request
    fake = FakeNet(responses)
    https._request = fake
    try:
        return fn(fake)
    finally:
        https._request = real


def _expect_http_error(fn, status):
    try:
        fn()
    except HttpError as e:
        assert e.status == status, 'status %r, wanted %r' % (e.status, status)
        return
    raise AssertionError('no HttpError(%r)' % status)


# --- live tests ------------------------------------------------------------

def test_api_over_verified_tls():
    _online()
    body = https.get('https://api.github.com/zen')
    assert isinstance(body, bytes) and len(body) > 0, repr(body)


def test_raw_file_matches_blob_hash():
    _online()
    body = https.get(MAIN_URL, headers=_auth())
    got = _blob_hash(body)
    assert got == MAIN_HASH, 'hash %s (len %d)' % (got, len(body))


def test_blob_api_gives_raw_bytes():
    """GitHub's side of the OTA download: the blob API, asked with the media
    type ota uses, streams the file's bytes (not base64 JSON)."""
    from net import ota
    _online()
    headers = _auth()
    headers['Accept'] = ota._RAW_MEDIA
    sink = CountingSink(WIFI_SIZE)
    url = 'https://api.github.com/repos/ACherniske/portalbox-firmware/git/blobs/' + WIFI_HASH
    got = https.get(url, headers=headers, sink=sink)
    assert got == WIFI_SIZE and sink.count == WIFI_SIZE, 'got %d bytes, want %d' % (
        sink.count, WIFI_SIZE)
    assert sink.blob_hash() == WIFI_HASH, 'hash %s' % sink.blob_hash()


def test_self_signed_refused():
    _online()
    try:
        https.get('https://self-signed.badssl.com/')
    except HttpError as e:
        raise AssertionError('reached HTTP status %r' % e.status)
    except Exception:
        return
    raise AssertionError('self-signed server accepted')


def test_wrong_host_refused():
    _online()
    try:
        https.get('https://wrong.host.badssl.com/')
    except HttpError as e:
        raise AssertionError('reached HTTP status %r' % e.status)
    except Exception:
        return
    raise AssertionError('wrong-host certificate accepted')


def test_missing_repo_is_404():
    _online()
    _expect_http_error(
        lambda: https.get('https://api.github.com/repos/ACherniske/no-such-repo-x'),
        404)


def test_feeds_through_connect_and_handshake():
    """Connect and the TLS handshake can each take up to TIMEOUT_S (15 s),
    so the 30 s watchdog must be fed at the start of each request, after
    connect and after the handshake: 3 feeds even for a reply with no body read."""
    _online()
    feeds = []
    real = recovery.feed
    recovery.feed = lambda: feeds.append(1)
    try:
        _expect_http_error(
            lambda: https.get('https://api.github.com/repos/ACherniske/no-such-repo-x'),
            404)
    finally:
        recovery.feed = real
    assert len(feeds) >= 3, "%d feeds for one request, want >= 3" % len(feeds)


def test_sink_gets_every_byte():
    _online()
    sink = CountingSink(WIFI_SIZE)
    feeds = [0]
    real_feed = recovery.feed

    def counting_feed():
        feeds[0] += 1
    recovery.feed = counting_feed
    try:
        n = https.get(WIFI_URL, headers=_auth(), sink=sink)
    finally:
        recovery.feed = real_feed
    assert n == WIFI_SIZE, 'returned %r' % n
    assert sink.count == WIFI_SIZE, 'wrote %d' % sink.count
    assert sink.blob_hash() == WIFI_HASH, 'hash mismatch'
    assert sink.biggest <= 1024, 'chunk of %d' % sink.biggest
    assert feeds[0] >= sink.chunks, 'feeds %d < chunks %d' % (feeds[0], sink.chunks)


def test_body_over_max_bytes():
    _online()
    _expect_http_error(lambda: https.get(WIFI_URL, headers=_auth(), max_bytes=1000), 0)


# --- offline tests (fake connection) ---------------------------------------

def test_http_url_refused():
    try:
        https.get('http://api.github.com/zen')
    except ValueError:
        return
    raise AssertionError('http URL accepted')


def test_follows_redirect_with_headers():
    def run(fake):
        body = https.get('https://a.example/one', headers={'Authorization': 'Bearer t'})
        assert body == b'done', repr(body)
        assert len(fake.calls) == 2, fake.calls
        host, path, hdrs = fake.calls[1]
        assert (host, path) == ('b.example', '/two?x=1'), (host, path)
        assert hdrs.get('Authorization') == 'Bearer t', hdrs
        assert hdrs.get('User-Agent'), hdrs
        assert fake.all_closed(), 'stream left open'
    _with_fake([(301, {'location': 'https://b.example/two?x=1'}, b''),
                (200, {'content-length': '4'}, b'done')], run)


def test_relative_redirect_stays_on_host():
    def run(fake):
        assert https.get('https://a.example:8443/one') == b'ok'
        assert fake.calls[1][:2] == ('a.example:8443', '/two'), fake.calls[1]
    _with_fake([(302, {'location': '/two'}, b''), (200, {}, b'ok')], run)


def test_declared_length_over_max_reads_nothing():
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/', max_bytes=100), 0)
        assert fake.streams[0].taken == 0, 'read %d bytes' % fake.streams[0].taken
        assert fake.all_closed(), 'stream left open'
    _with_fake([(200, {'content-length': '5000'}, b'x' * 5000)], run)


def test_redirect_limit():
    hop = (302, {'location': 'https://a.example/again'}, b'')
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/'), 302)
        assert len(fake.calls) == 4, len(fake.calls)
    _with_fake([hop, hop, hop, hop], run)


def test_three_redirects_allowed():
    hop = (302, {'location': 'https://a.example/again'}, b'')
    def run(fake):
        assert https.get('https://a.example/') == b'ok'
    _with_fake([hop, hop, hop, (200, {}, b'ok')], run)


def test_redirect_to_http_refused():
    def run(fake):
        try:
            https.get('https://a.example/')
        except ValueError:
            assert len(fake.calls) == 1, len(fake.calls)
            return
        raise AssertionError('followed redirect to http')
    _with_fake([(301, {'location': 'http://b.example/'}, b'')], run)


def test_short_read_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/'), 0)
    _with_fake([(200, {'content-length': '10'}, b'12345')], run)


def test_short_read_with_sink_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/', sink=CountingSink()), 0)
    _with_fake([(200, {'content-length': '3000'}, b'x' * 2000)], run)


def test_no_length_reads_to_end():
    def run(fake):
        assert https.get('https://a.example/') == b'abc' * 500
    _with_fake([(200, {}, b'abc' * 500)], run)


def test_over_max_without_length():
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/', max_bytes=100), 0)
    _with_fake([(200, {}, b'x' * 101)], run)


def test_json_streamed_not_held():
    """200 KB of JSON parses though it would never fit in the heap whole:
    get_json parses as the bytes arrive (the OTA tree reply failed with a
    MemoryError when its 9.5 KB body was joined on a fragmented heap)."""
    body = LazyStream(b'{"tree": [1, 2], ', 200000, b'"ok": true}')
    def run(fake):
        got = https.get_json('https://a.example/', max_bytes=300000)
        assert got == {'tree': [1, 2], 'ok': True}, repr(got)
        assert fake.all_closed(), 'stream left open'
    _with_fake([(200, {'content-length': str(body.size)}, body)], run)


def test_json_over_max_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get_json('https://a.example/', max_bytes=100), 0)
        assert fake.streams[0].taken == 0, 'read %d bytes' % fake.streams[0].taken
        _expect_http_error(lambda: https.get_json('https://a.example/', max_bytes=100), 0)
    _with_fake([(200, {'content-length': '5000'}, b'[' + b' ' * 4998 + b']'),
                (200, {}, b'[' + b' ' * 200 + b']')], run)


def test_json_short_body_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get_json('https://a.example/'), 0)
    _with_fake([(200, {'content-length': '50'}, b'{"a": "only part')], run)


def test_json_feeds_while_reading():
    body = LazyStream(b'[', 20000, b']')
    feeds = [0]
    real_feed = recovery.feed

    def counting_feed():
        feeds[0] += 1
    recovery.feed = counting_feed
    try:
        _with_fake([(200, {'content-length': str(body.size)}, body)],
                   lambda fake: https.get_json('https://a.example/'))
    finally:
        recovery.feed = real_feed
    assert feeds[0] >= 20000 // https.CHUNK, 'fed %d times for 20 KB' % feeds[0]


def test_json_non_200_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get_json('https://a.example/'), 404)
        assert fake.all_closed(), 'stream left open'
    _with_fake([(404, {}, b'{"message": "Not Found"}')], run)


def test_non_200_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/'), 500)
        assert fake.all_closed(), 'stream left open'
    _with_fake([(500, {}, b'oops')], run)


# --- keep-alive session (fake connections speaking raw HTTP) ---------------

def _reply(status, body=b'', headers=None):
    """Raw HTTP/1.1 reply bytes; Content-Length added unless headers say
    otherwise (pass {'content-length': None} to leave it out)."""
    hdrs = {'content-length': str(len(body))}
    hdrs.update(headers or {})
    head = 'HTTP/1.1 %d X\r\n' % status
    for k, v in hdrs.items():
        if v is not None:
            head += '%s: %s\r\n' % (k, v)
    return head.encode() + b'\r\n' + body


class FakeConn:
    """One TLS connection: serves `replies` (raw bytes) in order, one per
    request written; after them, or after `close_after` replies, it reads
    as closed by the server (b''). requests: (method line, headers)."""

    def __init__(self, host, replies, close_after=None):
        self.host = host
        self.replies = list(replies)
        self.close_after = close_after
        self.requests = []
        self.buf = io.BytesIO(b'')
        self.closed = False

    def write(self, data):
        lines = data.decode().split('\r\n')
        hdrs = {}
        for line in lines[1:]:
            if line:
                k, _, v = line.partition(':')
                hdrs[k.strip()] = v.strip()
        self.requests.append((lines[0], hdrs))
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


class FakeConnector:
    """Stands in for https._connect: each call hands out the next FakeConn
    from `conns` (lists of replies, or (replies, close_after))."""

    def __init__(self, conns):
        self.plan = list(conns)
        self.made = []

    def __call__(self, host):
        spec = self.plan.pop(0)
        replies, close_after = spec if isinstance(spec, tuple) else (spec, None)
        conn = FakeConn(host, replies, close_after)
        self.made.append(conn)
        return conn


def _with_session(conns, fn):
    real = https._connect
    fake = FakeConnector(conns)
    https._connect = fake
    s = https.Session()
    try:
        return fn(s, fake)
    finally:
        s.close()
        https._connect = real


def test_session_reuses_connection():
    def run(s, fake):
        assert s.get('https://a.example/1') == b'one'
        assert s.get_json('https://a.example/2') == {'n': 2}
        sink = CountingSink(5)
        assert s.get('https://a.example/3', sink=sink) == 5
        assert len(fake.made) == 1, '%d connections' % len(fake.made)
        lines = [r[0] for r in fake.made[0].requests]
        assert lines == ['GET /1 HTTP/1.1', 'GET /2 HTTP/1.1', 'GET /3 HTTP/1.1'], lines
    _with_session([[_reply(200, b'one'), _reply(200, b'{"n": 2}\n  '), _reply(200, b'three')]], run)


def test_session_sends_headers_each_time():
    def run(s, fake):
        s.get('https://a.example/1', {'Authorization': 'Bearer t'})
        s.get('https://a.example/2', {'Authorization': 'Bearer t', 'Accept': 'x/y'})
        h1, h2 = fake.made[0].requests[0][1], fake.made[0].requests[1][1]
        assert h1.get('Host') == 'a.example' and h1.get('Authorization') == 'Bearer t', h1
        assert h2.get('Accept') == 'x/y' and h2.get('User-Agent') == https.USER_AGENT, h2
    _with_session([[_reply(200, b'a'), _reply(200, b'b')]], run)


def test_session_reconnects_stale_connection():
    """The server closed the kept connection while idle: reconnect once
    and send the request again."""
    def run(s, fake):
        assert s.get('https://a.example/1') == b'one'
        assert s.get('https://a.example/2') == b'two'
        assert len(fake.made) == 2, '%d connections' % len(fake.made)
        assert fake.made[0].closed, 'stale connection not closed'
    _with_session([([_reply(200, b'one')], 1), [_reply(200, b'two')]], run)


def test_session_new_connection_failure_not_retried():
    def run(s, fake):
        _expect_http_error(lambda: s.get('https://a.example/1'), 0)
        assert len(fake.made) == 1, '%d connections' % len(fake.made)
    _with_session([[]], run)


def test_session_error_closes_connection():
    def run(s, fake):
        _expect_http_error(lambda: s.get('https://a.example/1'), 503)
        assert fake.made[0].closed, 'connection kept after an error'
        assert s.get('https://a.example/2') == b'ok'
        assert len(fake.made) == 2, '%d connections' % len(fake.made)
    _with_session([[_reply(503, b'busy')], [_reply(200, b'ok')]], run)


def test_session_honours_connection_close():
    def run(s, fake):
        assert s.get('https://a.example/1') == b'one'
        assert fake.made[0].closed, 'kept a connection the server closes'
        assert s.get('https://a.example/2') == b'two'
        assert len(fake.made) == 2, '%d connections' % len(fake.made)
    _with_session([[_reply(200, b'one', {'connection': 'close'})], [_reply(200, b'two')]], run)


def test_session_no_length_reads_to_end_and_closes():
    def run(s, fake):
        assert s.get('https://a.example/1') == b'all of it'
        assert fake.made[0].closed, 'kept a connection with no reply length'
    _with_session([[_reply(200, b'all of it', {'content-length': None})]], run)


def test_session_refuses_chunked():
    def run(s, fake):
        _expect_http_error(lambda: s.get('https://a.example/1'), 0)
        assert fake.made[0].closed, 'connection kept after a chunked reply'
    _with_session([[_reply(200, b'5\r\nhello\r\n0\r\n\r\n',
                          {'content-length': None, 'transfer-encoding': 'chunked'})]], run)


def test_session_new_host_new_connection():
    def run(s, fake):
        s.get('https://a.example/1')
        s.get('https://b.example/2')
        assert [c.host for c in fake.made] == ['a.example', 'b.example'], fake.made
        assert fake.made[0].closed, 'first host connection left open'
        sent_to_a = [r[0] for r in fake.made[0].requests]
        assert sent_to_a == ['GET /1 HTTP/1.1'], 'a.example was sent %r' % sent_to_a
    _with_session([[_reply(200, b'a')], [_reply(200, b'b')]], run)


def test_session_close_closes_connection():
    real = https._connect
    fake = FakeConnector([[_reply(200, b'a')]])
    https._connect = fake
    try:
        s = https.Session()
        s.get('https://a.example/1')
        s.close()
    finally:
        https._connect = real
    assert fake.made[0].closed, 'close() left the connection open'


def test_one_tls_context_for_all_connections():
    """Roots are parsed once, not per connection (each parse held ~4 KB of
    system RAM until the garbage collector freed the context)."""
    fake = _FakeSocketModule([])
    made = [0]
    loaded = [0]

    def ssl_context(protocol):
        made[0] += 1
        return fake

    def load(data):
        loaded[0] += 1
    fake.SSLContext = ssl_context
    fake.load_verify_locations = load
    saved = (https.socket, https.tls, https._roots, https._ctx)
    https.socket = https.tls = fake
    https._roots = lambda: b''
    https._ctx = None
    try:
        https._connect('a.example').close()
        https._connect('b.example').close()
    finally:
        https.socket, https.tls, https._roots, https._ctx = saved
    assert made[0] == 1 and loaded[0] == 1, 'contexts %d, root loads %d' % (made[0], loaded[0])


def run():
    join_first()
    runner = TestRunner()
    runner.run_test("http URL refused", test_http_url_refused)
    runner.run_test("Follows redirect with headers", test_follows_redirect_with_headers)
    runner.run_test("Three redirects allowed", test_three_redirects_allowed)
    runner.run_test("More than three redirects is an error", test_redirect_limit)
    runner.run_test("Redirect to http refused", test_redirect_to_http_refused)
    runner.run_test("Relative redirect stays on host", test_relative_redirect_stays_on_host)
    runner.run_test("Declared length over max reads nothing", test_declared_length_over_max_reads_nothing)
    runner.run_test("Short read is an error", test_short_read_is_error)
    runner.run_test("Short read with sink is an error", test_short_read_with_sink_is_error)
    runner.run_test("No Content-Length reads to the end", test_no_length_reads_to_end)
    runner.run_test("Over max_bytes without length", test_over_max_without_length)
    runner.run_test("Non-200 is an error", test_non_200_is_error)
    runner.run_test("JSON parsed as it arrives, never held whole", test_json_streamed_not_held)
    runner.run_test("JSON over max_bytes is an error", test_json_over_max_is_error)
    runner.run_test("Short JSON body is an error", test_json_short_body_is_error)
    runner.run_test("Feeds while reading JSON", test_json_feeds_while_reading)
    runner.run_test("JSON non-200 is an error", test_json_non_200_is_error)
    runner.run_test("Session reuses one connection", test_session_reuses_connection)
    runner.run_test("Session sends headers each time", test_session_sends_headers_each_time)
    runner.run_test("Session reconnects a stale connection once", test_session_reconnects_stale_connection)
    runner.run_test("Session: new connection failure not retried", test_session_new_connection_failure_not_retried)
    runner.run_test("Session error closes the connection", test_session_error_closes_connection)
    runner.run_test("Session honours Connection: close", test_session_honours_connection_close)
    runner.run_test("Session: no length reads to end, then closes", test_session_no_length_reads_to_end_and_closes)
    runner.run_test("Session refuses chunked replies", test_session_refuses_chunked)
    runner.run_test("Session: new host, new connection", test_session_new_host_new_connection)
    runner.run_test("Session close() closes the connection", test_session_close_closes_connection)
    runner.run_test("One TLS context for all connections", test_one_tls_context_for_all_connections)
    runner.run_test("Feeds after DNS and after the reply head", test_feeds_after_dns_and_head)
    runner.run_test("GitHub API over verified TLS", test_api_over_verified_tls)
    runner.run_test("Raw file matches its blob hash", test_raw_file_matches_blob_hash)
    runner.run_test("Blob API gives raw bytes", test_blob_api_gives_raw_bytes)
    runner.run_test("Self-signed server refused", test_self_signed_refused)
    runner.run_test("Wrong host refused", test_wrong_host_refused)
    runner.run_test("Missing repo is 404", test_missing_repo_is_404)
    runner.run_test("Feeds through connect and handshake", test_feeds_through_connect_and_handshake)
    runner.run_test("Sink gets every byte", test_sink_gets_every_byte)
    runner.run_test("Body over max_bytes is an error", test_body_over_max_bytes)
    runner.final_report()
