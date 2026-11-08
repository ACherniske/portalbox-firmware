"""HTTPS requests that check the server certificate.

The fork's requests module turns certificate checks off, so OTA downloads
and the Maker Portal API use this instead. The server must chain to a root in ROOTS_PATH and match
the host name; anything else raises before a byte of the body is read.
"""
import io
import json
import socket
import tls

import recovery

ROOTS_PATH = '/certs/github_roots.pem'
USER_AGENT = 'portalbox-firmware'
CHUNK = 1024
MAX_REDIRECTS = 3
TIMEOUT_S = 15
_REDIRECTS = (301, 302, 303, 307, 308)


class HttpError(Exception):
    """Non-200 reply (status), or a bad body (status 0)."""

    def __init__(self, status, message=''):
        super().__init__(status, message)
        self.status = status


def _split(url):
    """'https://host[:port]/path?q' -> ('host[:port]', '/path?q')."""
    if not url.startswith('https://'):
        raise ValueError('https only: %s' % url)
    host, slash, path = url[8:].partition('/')
    if not host:
        raise ValueError('no host: %s' % url)
    return host, '/' + path


def _roots():
    with open(ROOTS_PATH, 'rb') as f:
        return f.read()


_ctx = None   # one TLS context: the roots are parsed once, not per connection


def _context():
    global _ctx
    if _ctx is None:
        ctx = tls.SSLContext(tls.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = tls.CERT_REQUIRED
        ctx.load_verify_locations(_roots())
        _ctx = ctx
    return _ctx


def _connect(host, timeout_s=None):
    """Open a verified TLS connection to 'host[:port]'; returns the stream.

    timeout_s (default TIMEOUT_S) bounds each socket step on its own.
    """
    name, _, port = host.partition(':')
    port = int(port) if port else 443
    ctx = _context()
    addr = socket.getaddrinfo(name, port)[0][-1]
    recovery.feed()   # DNS, then connect, each can be slow
    sock = socket.socket()
    try:
        sock.settimeout(TIMEOUT_S if timeout_s is None else timeout_s)
        sock.connect(addr)
        # Connect and the handshake can each take up to TIMEOUT_S: two in
        # a row would pass the 30 s watchdog.
        recovery.feed()
        stream = ctx.wrap_socket(sock, server_hostname=name)
        recovery.feed()
    except Exception:
        sock.close()
        raise
    return stream


def _exchange(stream, host, path, headers, version, method='GET', body=None):
    """Send the request (GET unless method says otherwise) on stream and
    read the reply head. A body goes out after the head as given; the
    caller puts its Content-Length in headers.

    Returns (status, headers with lower-case names); the stream is left
    at the body. Does not close the stream.
    """
    lines = ['%s %s %s' % (method, path, version), 'Host: %s' % host]
    for key, value in headers.items():
        lines.append('%s: %s' % (key, value))
    stream.write(('\r\n'.join(lines) + '\r\n\r\n').encode())
    if body:
        stream.write(body)
    parts = stream.readline().split(None, 2)
    if len(parts) < 2 or not parts[0].startswith(b'HTTP/'):
        raise HttpError(0, 'bad status line')
    status = int(parts[1])
    reply = {}
    while True:
        line = stream.readline()
        if not line or line == b'\r\n':
            break
        key, _, value = line.decode().partition(':')
        reply[key.strip().lower()] = value.strip()
    recovery.feed()   # head lines, then the first body read, each can be slow
    return status, reply


def _request(host, path, headers):
    """One-shot: connect, send the GET (HTTP/1.0) and read the reply head.

    Returns (status, headers with lower-case names, stream at the body).
    """
    stream = _connect(host)
    try:
        status, reply = _exchange(stream, host, path, headers, 'HTTP/1.0')
    except Exception:
        stream.close()
        raise
    return status, reply, stream


def _read_body(stream, reply, sink, max_bytes):
    length = reply.get('content-length')
    length = int(length) if length is not None else None
    if sink is None and length is not None and length > max_bytes:
        raise HttpError(0, 'body over %d bytes' % max_bytes)
    total = 0
    parts = []
    while length is None or total < length:
        want = CHUNK if length is None else min(CHUNK, length - total)
        chunk = stream.read(want)
        if not chunk:
            break
        total += len(chunk)
        if sink is None:
            if total > max_bytes:
                raise HttpError(0, 'body over %d bytes' % max_bytes)
            parts.append(chunk)
        else:
            sink.write(chunk)
        recovery.feed()
    if length is not None and total < length:
        raise HttpError(0, 'short read')
    if sink is None:
        return b''.join(parts)
    return total


class _JsonBody(io.IOBase):
    """The reply body as a stream for json.load, read CHUNK bytes at a
    time, so a big reply is never held whole. Same limits as _read_body:
    over max_bytes or short of Content-Length -> HttpError(0)."""

    def __init__(self, stream, reply, max_bytes):
        length = reply.get('content-length')
        self.length = int(length) if length is not None else None
        if self.length is not None and self.length > max_bytes:
            raise HttpError(0, 'body over %d bytes' % max_bytes)
        self.stream = stream
        self.max_bytes = max_bytes
        self.total = 0
        self.buf = b''
        self.pos = 0

    def _fill(self):
        want = CHUNK if self.length is None else min(CHUNK, self.length - self.total)
        chunk = self.stream.read(want) if want > 0 else b''
        if not chunk:
            if self.length is not None and self.total < self.length:
                raise HttpError(0, 'short read')
            return False
        self.total += len(chunk)
        if self.total > self.max_bytes:
            raise HttpError(0, 'body over %d bytes' % self.max_bytes)
        self.buf = chunk
        self.pos = 0
        recovery.feed()
        return True

    def readinto(self, out):
        if self.pos >= len(self.buf) and not self._fill():
            return 0
        n = min(len(out), len(self.buf) - self.pos)
        out[:n] = self.buf[self.pos:self.pos + n]
        self.pos += n
        return n


def get(url, headers=None, sink=None, max_bytes=65536):
    """GET an https URL. Returns the body, or with sink the byte count.

    Follows up to MAX_REDIRECTS redirects to https URLs, sending the same
    headers each time. Non-200 -> HttpError(status); body over max_bytes
    (no sink) or shorter than Content-Length -> HttpError(0). With sink,
    each chunk (<= CHUNK bytes) goes to sink.write.
    """
    return _get(url, headers, lambda stream, reply: _read_body(stream, reply, sink, max_bytes))


def get_json(url, headers=None, max_bytes=65536):
    """GET an https URL and parse its JSON body as it arrives.

    Like get() without a sink, but the body is never held whole: a reply
    of several KB needs no single block that big on the heap.
    """
    return _get(url, headers, lambda stream, reply: json.load(_JsonBody(stream, reply, max_bytes)))


def request(method, url, headers=None, body=None, max_bytes=4096, timeout_s=None):
    """Send one request (PUT, POST, DELETE...) and return (status, body bytes).

    Any status comes back as is: the caller decides what a 401 or 409
    means. Redirects are not followed. body may be str or bytes and goes
    out with Content-Length (0 without one). timeout_s (default TIMEOUT_S)
    bounds each socket step. Network and TLS errors raise; a body over
    max_bytes or shorter than Content-Length -> HttpError(0).
    """
    host, path = _split(url)
    if body is None:
        body = b''
    elif isinstance(body, str):
        body = body.encode()
    sent = {'User-Agent': USER_AGENT, 'Content-Length': str(len(body))}
    sent.update(headers or {})
    recovery.feed()
    stream = _connect(host, timeout_s)
    try:
        status, reply = _exchange(stream, host, path, sent, 'HTTP/1.0', method, body)
        return status, _read_body(stream, reply, None, max_bytes)
    finally:
        stream.close()


def _get(url, headers, read):
    sent = {'User-Agent': USER_AGENT}
    sent.update(headers or {})
    for hop in range(MAX_REDIRECTS + 1):
        recovery.feed()
        host, path = _split(url)
        status, reply, stream = _request(host, path, sent)
        try:
            if status in _REDIRECTS and hop < MAX_REDIRECTS:
                url = reply.get('location', '')
                if url.startswith('/'):
                    url = 'https://' + host + url
                continue
            if status != 200:
                raise HttpError(status)
            return read(stream, reply)
        finally:
            stream.close()


class Session:
    """Keep-alive GETs: one TLS connection per host, reused while the
    server keeps it open, so a run of requests costs one handshake.

    Each body is read to its Content-Length; a reply without one is read
    to the end and the connection closed. Chunked replies are refused. A
    request on a reused connection that the server has meanwhile closed
    is sent once more on a new connection. Any error closes the
    connection. Call close() when done.
    """

    def __init__(self):
        self.host = None
        self.stream = None

    def get(self, url, headers=None, sink=None, max_bytes=65536):
        """Like https.get(), on the kept connection."""
        return self._get(url, headers,
                         lambda stream, reply: _read_body(stream, reply, sink, max_bytes))

    def get_json(self, url, headers=None, max_bytes=65536):
        """Like https.get_json(), on the kept connection."""
        return self._get(url, headers,
                         lambda stream, reply: json.load(_JsonBody(stream, reply, max_bytes)))

    def close(self):
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                pass
        self.stream = None
        self.host = None

    def _send(self, host, path, headers):
        if self.stream is not None and self.host != host:
            self.close()
        reused = self.stream is not None
        if not reused:
            self.stream = _connect(host)
            self.host = host
        try:
            return _exchange(self.stream, host, path, headers, 'HTTP/1.1')
        except (OSError, HttpError):
            self.close()
            if not reused:
                raise
        # The server closed the kept connection while it sat idle.
        self.stream = _connect(host)
        self.host = host
        return _exchange(self.stream, host, path, headers, 'HTTP/1.1')

    def _get(self, url, headers, read):
        sent = {'User-Agent': USER_AGENT}
        sent.update(headers or {})
        for hop in range(MAX_REDIRECTS + 1):
            recovery.feed()
            host, path = _split(url)
            try:
                status, reply = self._send(host, path, sent)
                if status in _REDIRECTS and hop < MAX_REDIRECTS:
                    url = reply.get('location', '')
                    if url.startswith('/'):
                        url = 'https://' + host + url
                    self.close()   # its body is not read: start clean
                    continue
                if status != 200:
                    raise HttpError(status)
                if 'chunked' in reply.get('transfer-encoding', '').lower():
                    raise HttpError(0, 'chunked reply')
                result = read(self.stream, reply)
            except BaseException:
                self.close()
                raise
            if ('content-length' not in reply
                    or reply.get('connection', '').lower() == 'close'):
                self.close()
            return result
