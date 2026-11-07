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


def _online():
    """Connect WiFi and set the clock the way core/system.py does."""
    import network
    import time
    sta = network.WLAN(network.STA_IF)
    if sta.isconnected() and time.localtime()[0] >= 2025:
        return
    from net.models import WiFiConfig
    from net.wifi import WifiManager
    config = WiFiConfig()
    if not config.load():
        raise Exception('no /config/wifi_config.json')
    password = config.get_decrypted_password()
    if not WifiManager(config=config, verbose=False).connect_with_retry(password):
        raise Exception('WiFi did not connect')
    from net import clock
    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None


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


class FakeNet:
    """Stands in for https._request: plays scripted responses and records
    (host, path, headers) of each call and the streams it handed out."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.streams = []

    def __call__(self, host, path, headers):
        self.calls.append((host, path, dict(headers)))
        status, hdrs, body = self.responses.pop(0)
        stream = FakeStream(body)
        self.streams.append(stream)
        return status, hdrs, stream

    def all_closed(self):
        return all(s.closed for s in self.streams)


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


def test_non_200_is_error():
    def run(fake):
        _expect_http_error(lambda: https.get('https://a.example/'), 500)
        assert fake.all_closed(), 'stream left open'
    _with_fake([(500, {}, b'oops')], run)


def run():
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
    runner.run_test("GitHub API over verified TLS", test_api_over_verified_tls)
    runner.run_test("Raw file matches its blob hash", test_raw_file_matches_blob_hash)
    runner.run_test("Self-signed server refused", test_self_signed_refused)
    runner.run_test("Wrong host refused", test_wrong_host_refused)
    runner.run_test("Missing repo is 404", test_missing_repo_is_404)
    runner.run_test("Feeds through connect and handshake", test_feeds_through_connect_and_handshake)
    runner.run_test("Sink gets every byte", test_sink_gets_every_byte)
    runner.run_test("Body over max_bytes is an error", test_body_over_max_bytes)
    runner.final_report()
