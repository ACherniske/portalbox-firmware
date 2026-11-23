from tests.test_helper import TestRunner
import os
from net import clock
from net.wifi import WifiManager
from net.models import WiFiConfig

CA_FILE = "/test_ap_ca.pem"

STRONG = b"\x44\x12\x44\x6b\xae\x60"
WEAK = b"\x48\xb4\xc3\xd8\x65\x60"
OTHER = b"\x00\x11\x22\x33\x44\x55"

# Keyword arguments the firmware's WLAN.connect() accepts
# (h-milz fork e244d80, ports/esp32/network_wlan.c network_wlan_connect).
CONNECT_KEYWORDS = (
    "bssid",
    "eap_method",
    "username",
    "password",
    "identity",
    "ca_cert",
    "ttls_phase2_method",
    "client_cert",
    "private_key",
    "private_key_password",
    "disable_time_check",
)


def _ap(ssid, bssid, rssi):
    # Same shape as WLAN.scan(): (ssid, bssid, channel, rssi, security, hidden)
    return (ssid, bssid, 6, rssi, 5, False)


class FakeNIC:
    """Connects at once. scan() plays one result list per call (repeating
    the last); an exception in the list is raised instead."""

    def __init__(self, scans):
        self.scans = list(scans)
        self.scan_calls = 0
        self.connect_args = []
        self.connect_kw = []
        self.current = 1000

    def scan(self):
        result = self.scans[min(self.scan_calls, len(self.scans) - 1)]
        self.scan_calls += 1
        if isinstance(result, Exception):
            raise result
        return result

    def connect(self, *args, **kw):
        # The firmware's connect(): ssid and key are positional only; the
        # rest are these keywords. Anything else raises, as on the box.
        extra = [k for k in kw if k not in CONNECT_KEYWORDS]
        if extra or len(args) > 2:
            raise TypeError("extra keyword arguments given")
        self.connect_args.append(args)
        self.connect_kw.append(kw)
        self.current = 1010

    def status(self, *args):
        return self.current

    def isconnected(self):
        return self.current == 1010

    def active(self, *args):
        return True

    def disconnect(self):
        self.current = 1000

    def ifconfig(self):
        return ("10.0.0.2", "255.255.255.0", "10.0.0.1", "10.0.0.1")


def _manager(scans, config=None):
    if config is None:
        config = WiFiConfig()
        config.set_wpa2_psk("net", "password123")
    mgr = WifiManager(config=config, verbose=False)
    mgr.nic = FakeNIC(scans)
    mgr._sync_time = lambda: None
    return mgr


def _stop_autosave():
    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None


def _connect(mgr, password="password123"):
    try:
        ok = mgr.connect(password, skip_init=True)
    finally:
        _stop_autosave()
    assert ok is True, "not connected: %r" % mgr.last_error
    return mgr.nic.connect_kw[-1]


def run():
    runner = TestRunner()

    def test_joins_strongest():
        mgr = _manager(
            [
                [
                    _ap(b"net", WEAK, -88),
                    _ap(b"other", OTHER, -40),  # stronger, but not our network
                    _ap(b"net", STRONG, -63),
                ]
            ]
        )
        kw = _connect(mgr)
        assert kw.get("bssid") == STRONG, "bssid %r" % (kw.get("bssid"),)

    def test_no_match_no_bssid():
        mgr = _manager([[_ap(b"other", OTHER, -40)]])
        kw = _connect(mgr)
        assert "bssid" not in kw, "bssid passed: %r" % (kw.get("bssid"),)
        assert mgr.nic.scan_calls == 1, "scan_calls %d" % mgr.nic.scan_calls

    def test_scan_error_no_bssid():
        mgr = _manager([OSError(-1)])
        kw = _connect(mgr)
        assert "bssid" not in kw, "bssid passed: %r" % (kw.get("bssid"),)

    def test_config_bssid_wins():
        config = WiFiConfig()
        config.set_wpa2_psk("net", "password123", bssid=WEAK)
        mgr = _manager([[_ap(b"net", STRONG, -63)]], config)
        kw = _connect(mgr)
        assert kw.get("bssid") == WEAK, "bssid %r" % (kw.get("bssid"),)
        assert mgr.nic.scan_calls == 0, "scanned %d times" % mgr.nic.scan_calls

    def test_rescans_each_attempt():
        mgr = _manager(
            [
                [_ap(b"net", WEAK, -88), _ap(b"net", STRONG, -63)],
                [_ap(b"net", WEAK, -60), _ap(b"net", STRONG, -80)],
            ]
        )
        first = _connect(mgr)
        mgr.nic.disconnect()
        second = _connect(mgr)
        assert first.get("bssid") == STRONG, "first bssid %r" % (first.get("bssid"),)
        assert second.get("bssid") == WEAK, "second bssid %r" % (second.get("bssid"),)

    def test_enterprise_joins_strongest():
        with open(CA_FILE, "w") as f:
            f.write("-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n")
        try:
            config = WiFiConfig()
            config.set_eap_peap("net", "user", "pw", ca_cert_file=CA_FILE)
            mgr = _manager([[_ap(b"net", WEAK, -88), _ap(b"net", STRONG, -63)]], config)
            kw = _connect(mgr, "pw")
            mgr._stop_and_release()
        finally:
            os.remove(CA_FILE)
        assert kw.get("bssid") == STRONG, "bssid %r" % (kw.get("bssid"),)

    def test_psk_key_positional():
        mgr = _manager([[_ap(b"net", STRONG, -63)]])
        _connect(mgr)
        args = mgr.nic.connect_args[-1]
        assert args == ("net", "password123"), "connect args %r" % (args,)

    def test_wpa3_psk_connects():
        config = WiFiConfig()
        config.set_wpa3_psk("net", "password123")
        mgr = _manager([[_ap(b"net", STRONG, -63)]], config)
        kw = _connect(mgr)
        args = mgr.nic.connect_args[-1]
        assert args == ("net", "password123"), "connect args %r" % (args,)
        assert kw.get("bssid") == STRONG, "bssid %r" % (kw.get("bssid"),)

    def test_wpa3_setting_warned():
        # The firmware has no WPA3 switch: say so instead of claiming WPA3.
        config = WiFiConfig()
        config.set_wpa3_psk("net", "password123")
        mgr = _manager([[_ap(b"net", STRONG, -63)]], config)
        logs = []
        mgr._log = logs.append
        _connect(mgr)
        assert any("WARNING" in m and "wpa3" in m for m in logs), "no warning: %r" % (
            logs,
        )
        assert not any("WPA3-PSK" in m for m in logs), "claims WPA3: %r" % (logs,)

    def test_wpa2_no_wpa3_warning():
        mgr = _manager([[_ap(b"net", STRONG, -63)]])
        logs = []
        mgr._log = logs.append
        _connect(mgr)
        assert not any("wpa3" in m.lower() for m in logs), "wpa3 logged: %r" % (logs,)

    runner.run_test("WPA2-PSK passes the key positionally", test_psk_key_positional)
    runner.run_test(
        "WPA3-PSK connects with the firmware's arguments", test_wpa3_psk_connects
    )
    runner.run_test("wpa3 in the config: warned, not claimed", test_wpa3_setting_warned)
    runner.run_test("WPA2-PSK: no wpa3 warning", test_wpa2_no_wpa3_warning)
    runner.run_test("Joins the strongest AP of the network", test_joins_strongest)
    runner.run_test("No matching AP connects without bssid", test_no_match_no_bssid)
    runner.run_test("Scan error connects without bssid", test_scan_error_no_bssid)
    runner.run_test("Config bssid wins, no scan", test_config_bssid_wins)
    runner.run_test("Each attempt scans again", test_rescans_each_attempt)
    runner.run_test(
        "Enterprise login joins the strongest AP", test_enterprise_joins_strongest
    )

    runner.final_report()


if __name__ == "__main__":
    run()
