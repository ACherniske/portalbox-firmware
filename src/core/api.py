# core/api.py
"""Maker Portal client: the server decides whether a card powers the tool.

Every call answers; none raises. No WiFi, a timeout, a TLS error, a non-200
reply or a body it can't read all come back as a refusal (the tool stays
off) and go to the event log. The card is the credential: it goes in the
Authorization header as a decimal number, the box's MAC in the URL.

The server address lives on the box, never in the repo: CONFIG_PATH holds
{"url": "https://..."} (see config/example_api_config.json). No file, or
one without a url string, refuses every card. "url": "" answers from the
built-in test cards (core/api_mock.py).
"""
import json
import network

import recovery
from config import settings
from core import api_mock

CONFIG_PATH = '/config/api_config.json'
_ACTIVATION = '/api/v2/box-activation.php'
_BOX = '/api/v2/box.php'
_HEX = '0123456789abcdefABCDEF'
_MODES = (b'user', b'proxy', b'training')


def card_id(uid_hex):
    """'23BE9603' -> '599692803' (the number the server stores), or None."""
    if not isinstance(uid_hex, str) or not uid_hex:
        return None
    for c in uid_hex:
        if c not in _HEX:
            return None
    return str(int(uid_hex, 16))


def _wlan():
    return network.WLAN(network.STA_IF)


def mac():
    """The WiFi MAC as the server stores it: 12 lowercase hex, no colons."""
    return ''.join('%02x' % b for b in _wlan().config('mac'))


def _log(msg):
    try:
        recovery.log('api', msg)
    except Exception:
        pass


def _server(name):
    """The server URL from CONFIG_PATH: '' = test cards, None = unusable (logged)."""
    try:
        with open(CONFIG_PATH) as f:
            url = json.load(f).get('url')
    except Exception as e:
        _log('%s: no api config (%r)' % (name, e))
        return None
    if not isinstance(url, str):
        _log('%s: api config url %r' % (name, url))
        return None
    return url


def _call(name, server, method, path, card=None, body=None, headers=None):
    """Send one request; (status, body bytes), or None after a logged failure."""
    try:
        if not _wlan().isconnected():
            _log(name + ': no wifi')
            return None
        sent = {}
        if card is not None:
            sent['Authorization'] = 'Bearer ' + card
        if headers:
            sent.update(headers)
        url = server.rstrip('/') + path + '?mac=' + mac()
        from net import https   # loads TLS only once a card is checked
        return https.request(method, url, sent, body, timeout_s=settings.API_TIMEOUT_S)
    except Exception as e:
        _log('%s: %r' % (name, e))
        return None


def _ok(name, reply):
    """True for a 200 reply; logs anything else."""
    if reply is None:
        return False
    if reply[0] != 200:
        _log('%s: %d' % (name, reply[0]))
        return False
    return True


def _timeout_ms(minutes):
    """The equipment's timeout: minutes -> ms, 0 = no limit, else None."""
    if type(minutes) is int and minutes >= 0:
        return minutes * 60000
    return None


def activate(uid):
    """Start a session: (True, timeout ms / 0 for no limit / None) or (False, None)."""
    server = _server('activate')
    if server == '':
        return api_mock.activate(uid)
    card = card_id(uid)
    if server is None or card is None:
        return (False, None)
    reply = _call('activate', server, 'PUT', _ACTIVATION, card)
    if not _ok('activate', reply):
        return (False, None)
    try:
        equipment = json.loads(reply[1])['equipment']
        timeout = equipment.get('timeout')
    except Exception as e:
        _log('activate: bad body %r' % (e,))
        return (False, None)
    return (True, _timeout_ms(timeout))


def change_card(activating_uid, new_uid):
    """Another card during a session: 'user', 'proxy', 'training' or None."""
    server = _server('change card')
    if server == '':
        return api_mock.change_card(activating_uid, new_uid)
    card = card_id(activating_uid)
    new = card_id(new_uid)
    if server is None or card is None or new is None:
        return None
    reply = _call('change card', server, 'POST', _ACTIVATION, card,
                  json.dumps({'card': int(new)}), {'Content-Type': 'application/json'})
    if not _ok('change card', reply):
        return None
    mode = reply[1].strip()
    if mode not in _MODES:
        _log('change card: bad body %r' % (mode[:20],))
        return None
    return mode.decode()


def deactivate(uid):
    """End the session the card started. True when the server recorded it."""
    server = _server('deactivate')
    if server == '':
        return api_mock.deactivate(uid)
    card = card_id(uid)
    if server is None or card is None:
        return False
    return _ok('deactivate', _call('deactivate', server, 'DELETE', _ACTIVATION, card))


def startup():
    """Tell the server the box is up: 'ok', 'not registered', 'out of service' or 'error'."""
    server = _server('startup')
    if server == '':
        return api_mock.startup()
    if server is None:
        return 'error'
    reply = _call('startup', server, 'POST', _BOX, body='startup')
    if reply is None:
        return 'error'
    if reply[0] == 404:
        return 'not registered'
    if reply[0] == 409:
        return 'out of service'
    if not _ok('startup', reply):
        return 'error'
    return 'ok'


# The FSM's old calls, answered by the test cards until fsm.py moves to the
# calls above.
is_auth = api_mock.is_auth
is_trainer = api_mock.is_trainer
is_proxy = api_mock.is_proxy
log_session_start = api_mock.log_session_start
log_session_end = api_mock.log_session_end
log_session_fail = api_mock.log_session_fail
log_training_start = api_mock.log_training_start
log_proxy_start = api_mock.log_proxy_start
