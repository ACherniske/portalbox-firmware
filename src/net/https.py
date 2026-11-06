"""HTTPS GET that checks the server certificate.

The fork's requests module turns certificate checks off, so OTA downloads
use this instead. The server must chain to a root in ROOTS_PATH and match
the host name; anything else raises before a byte of the body is read.
"""
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


def _request(host, path, headers):
    """Send the GET and read the reply head.

    Returns (status, headers with lower-case names, stream at the body).
    """
    name, _, port = host.partition(':')
    port = int(port) if port else 443
    ctx = tls.SSLContext(tls.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = tls.CERT_REQUIRED
    ctx.load_verify_locations(_roots())
    addr = socket.getaddrinfo(name, port)[0][-1]
    sock = socket.socket()
    try:
        sock.settimeout(TIMEOUT_S)
        sock.connect(addr)
        stream = ctx.wrap_socket(sock, server_hostname=name)
    except Exception:
        sock.close()
        raise
    try:
        lines = ['GET %s HTTP/1.0' % path, 'Host: %s' % host]
        for key, value in headers.items():
            lines.append('%s: %s' % (key, value))
        stream.write(('\r\n'.join(lines) + '\r\n\r\n').encode())
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


def get(url, headers=None, sink=None, max_bytes=65536):
    """GET an https URL. Returns the body, or with sink the byte count.

    Follows up to MAX_REDIRECTS redirects to https URLs, sending the same
    headers each time. Non-200 -> HttpError(status); body over max_bytes
    (no sink) or shorter than Content-Length -> HttpError(0). With sink,
    each chunk (<= CHUNK bytes) goes to sink.write.
    """
    sent = {'User-Agent': USER_AGENT}
    sent.update(headers or {})
    for hop in range(MAX_REDIRECTS + 1):
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
            return _read_body(stream, reply, sink, max_bytes)
        finally:
            stream.close()
