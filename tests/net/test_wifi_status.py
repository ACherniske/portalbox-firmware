from tests.test_helper import TestRunner
import net.wifi as wifi
from net import clock
from net.wifi import WifiManager
from net.models import WiFiConfig, NetworkStatus

CA_FILE = '/test_ca.pem'


class FakeNIC:
    """status() plays a script, repeating its last value. isconnected() is
    True once status() has returned GOT_IP (1010)."""

    def __init__(self, script):
        self.script = list(script)
        self.polls = 0
        self.current = None

    def status(self, *args):
        value = self.script[min(self.polls, len(self.script) - 1)]
        self.polls += 1
        self.current = value
        return value

    def isconnected(self):
        return self.current == 1010

    def active(self, *args):
        return True

    def connect(self, ssid, **kw):
        pass

    def disconnect(self):
        pass

    def ifconfig(self):
        return ('10.0.0.2', '255.255.255.0', '10.0.0.1', '10.0.0.1')


def _manager(script, pre=1000, enterprise=False):
    config = WiFiConfig()
    if enterprise:
        config.set_eap_peap('net', 'user', 'pw', ca_cert_file=CA_FILE)
    else:
        config.set_wpa2_psk('net', 'password123')
    config.timeout = 3
    mgr = WifiManager(config=config, verbose=False)
    mgr.nic = FakeNIC(script)
    mgr._sync_time = lambda: None   # no real network for NTP
    mgr._pre_status = pre
    return mgr


def _stop_autosave():
    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None


def run():
    runner = TestRunner()

    def test_no_ap_fails_fast():
        mgr = _manager([1001, 201])
        assert mgr._wait_for_connection() is False, "reported connected"
        assert mgr.nic.polls <= 3, "took %d polls to fail" % mgr.nic.polls
        assert mgr.last_error == "Access point not found", "last_error is %r" % mgr.last_error
        assert mgr.status == NetworkStatus.NO_AP_FOUND, "status is %s" % mgr.status

    def test_auth_fail_fails_fast():
        mgr = _manager([1001, 202])
        assert mgr._wait_for_connection() is False, "reported connected"
        assert mgr.nic.polls <= 3, "took %d polls to fail" % mgr.nic.polls
        assert mgr.status == NetworkStatus.WRONG_PASSWORD, "status is %s" % mgr.status
        assert mgr.last_error == wifi._FAILURE_MESSAGES[202], "last_error is %r" % mgr.last_error

    def test_stale_code_ignored():
        mgr = _manager([201, 201, 201, 1010], pre=201)
        assert mgr._wait_for_connection() is True, "stale code failed the attempt: %r" % mgr.last_error
        _stop_autosave()

    def test_repeat_code_after_connecting_fails():
        mgr = _manager([201, 1001, 201], pre=201)
        assert mgr._wait_for_connection() is False, "new failure ignored"
        assert mgr.status == NetworkStatus.NO_AP_FOUND, "status is %s" % mgr.status

    def test_enterprise_timeout_message():
        mgr = _manager([1001], enterprise=True)
        assert mgr._wait_for_connection() is False, "reported connected"
        assert "Timed out during login (EAP)" in (mgr.last_error or ''), "last_error is %r" % mgr.last_error

    def test_got_ip():
        mgr = _manager([1001, 1010])
        assert mgr._wait_for_connection() is True, "not connected: %r" % mgr.last_error
        assert mgr.status == NetworkStatus.GOT_IP, "status is %s" % mgr.status
        _stop_autosave()

    def test_connect_records_pre_status():
        # First status() read is before nic.connect(): the 201 left from the
        # last attempt. connect() must snapshot it so the wait ignores it.
        mgr = _manager([201, 201, 1010], pre=None)
        assert mgr.connect('password123', skip_init=True) is True, "not connected: %r" % mgr.last_error
        assert mgr._pre_status == 201, "_pre_status is %r" % mgr._pre_status
        _stop_autosave()

    def test_every_status_named():
        for name in dir(NetworkStatus):
            if name.startswith('_'):
                continue
            code = getattr(NetworkStatus, name)
            label = WifiManager._get_status_name(None, code)
            assert not label.startswith('UNKNOWN'), "%s (%s) has no name" % (name, code)

    runner.run_test("No AP found fails fast", test_no_ap_fails_fast)
    runner.run_test("Auth failure fails fast", test_auth_fail_fails_fast)
    runner.run_test("Stale pre-connect code ignored", test_stale_code_ignored)
    runner.run_test("Same code after CONNECTING still fails", test_repeat_code_after_connecting_fails)
    runner.run_test("Enterprise timeout says EAP", test_enterprise_timeout_message)
    runner.run_test("GOT_IP connects", test_got_ip)
    runner.run_test("connect() records the pre-connect code", test_connect_records_pre_status)
    runner.run_test("Every status code has a name", test_every_status_named)

    _stop_autosave()
    runner.final_report()


if __name__ == "__main__":
    run()
