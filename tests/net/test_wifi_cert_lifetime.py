import gc
import os
from tests.test_helper import TestRunner
import net.wifi as wifi
from net.wifi import WifiManager
from net.models import WiFiConfig, NetworkStatus

CA_FILE = "/test_ca.pem"
CERT_FILE = "/test_cert.pem"
KEY_FILE = "/test_key.pem"
PEM = "-----BEGIN CERTIFICATE-----\nMIIBtest\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIBkey\n-----END PRIVATE KEY-----\n"


class FakeNIC:
    """Records only the id() of each str kwarg, so the test never keeps the
    strings alive itself (ESP-IDF keeps a raw pointer, not a reference)."""

    def __init__(self, raise_on_connect=False):
        self.raise_on_connect = raise_on_connect
        self.ids = {}
        self.held_at_disconnect = None  # len(wifi._held) when disconnect() ran

    def connect(self, ssid, **kw):
        self.ids = {k: id(v) for k, v in kw.items() if isinstance(v, str)}
        if self.raise_on_connect:
            raise OSError("fake connect error")

    def isconnected(self):
        return False

    def disconnect(self):
        self.held_at_disconnect = len(wifi._held)


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _peap_manager(nic):
    config = WiFiConfig()
    config.set_eap_peap("net", "user", "pw", ca_cert_file=CA_FILE)
    mgr = WifiManager(config=config, verbose=False)
    mgr.nic = nic
    return mgr


def _held_ids():
    return [id(v) for v in wifi._held]


def run():
    runner = TestRunner()
    _write(CA_FILE, PEM)
    _write(CERT_FILE, PEM)
    _write(KEY_FILE, KEY)

    def test_peap_cert_held_after_return():
        nic = FakeNIC()
        mgr = _peap_manager(nic)
        assert mgr._connect_eap_peap("pw") is True, "fake connect failed"
        cert_id = nic.ids["ca_cert"]
        del mgr
        gc.collect()
        held = [v for v in wifi._held if id(v) == cert_id]
        assert held, "ca_cert passed to nic.connect() is not held"
        assert held[0].startswith("-----BEGIN"), "held object is not the cert"

    def test_second_connect_replaces_held():
        nic = FakeNIC()
        mgr = _peap_manager(nic)
        mgr._connect_eap_peap("pw")
        mgr._connect_eap_peap("pw")
        assert len(wifi._held) == 1, "expected 1 held value, got %d" % len(wifi._held)
        assert _held_ids() == [nic.ids["ca_cert"]], (
            "held value is not the second call's cert"
        )

    def test_disconnect_releases():
        mgr = _peap_manager(FakeNIC())
        mgr._connect_eap_peap("pw")
        mgr.disconnect()
        assert wifi._held == [], "disconnect() left %d value(s) held" % len(wifi._held)

    def test_connect_error_releases():
        mgr = _peap_manager(FakeNIC(raise_on_connect=True))
        assert mgr._connect_eap_peap("pw") is False, "connect error not reported"
        assert mgr.status == NetworkStatus.CONNECT_FAIL, "status is %s" % mgr.status
        assert wifi._held == [], "connect error left %d value(s) held" % len(wifi._held)

    def test_disconnect_stops_driver_first():
        # Not connected, but the driver may still be retrying the login and
        # reading the cert: stop it before the cert is freed.
        nic = FakeNIC()
        mgr = _peap_manager(nic)
        mgr._connect_eap_peap("pw")
        mgr.disconnect()
        assert nic.held_at_disconnect is not None, "nic.disconnect() not called"
        assert nic.held_at_disconnect > 0, "cert freed before nic.disconnect()"
        assert wifi._held == [], "disconnect() left %d value(s) held" % len(wifi._held)

    def test_connect_error_stops_driver_first():
        nic = FakeNIC(raise_on_connect=True)
        mgr = _peap_manager(nic)
        mgr._connect_eap_peap("pw")
        assert nic.held_at_disconnect is not None, (
            "nic.disconnect() not called on error"
        )
        assert nic.held_at_disconnect > 0, "cert freed before nic.disconnect()"

    def test_tls_key_password_held():
        nic = FakeNIC()
        config = WiFiConfig()
        config.set_eap_tls("net", CERT_FILE, KEY_FILE, private_key_password="keypw")
        mgr = WifiManager(config=config, verbose=False)
        mgr.nic = nic
        assert mgr._connect_eap_tls("".join(["key", "pw"])) is True, (
            "fake connect failed"
        )
        assert nic.ids["private_key_password"] in _held_ids(), (
            "private_key_password not held"
        )

    def test_tls_key_material_held():
        nic = FakeNIC()
        config = WiFiConfig()
        config.set_eap_tls("net", CERT_FILE, KEY_FILE)
        mgr = WifiManager(config=config, verbose=False)
        mgr.nic = nic
        assert mgr._connect_eap_tls() is True, "fake connect failed"
        ids = _held_ids()
        assert nic.ids["client_cert"] in ids, "client_cert not held"
        assert nic.ids["private_key"] in ids, "private_key not held"

    runner.run_test(
        "PEAP cert held after the call returns", test_peap_cert_held_after_return
    )
    runner.run_test(
        "Second connect replaces the held cert", test_second_connect_replaces_held
    )
    runner.run_test("disconnect() releases held certs", test_disconnect_releases)
    runner.run_test("Connect error releases held certs", test_connect_error_releases)
    runner.run_test("TLS client cert and key held", test_tls_key_material_held)
    runner.run_test(
        "disconnect() stops the driver before freeing",
        test_disconnect_stops_driver_first,
    )
    runner.run_test(
        "Connect error stops the driver before freeing",
        test_connect_error_stops_driver_first,
    )
    runner.run_test("TLS key password held", test_tls_key_password_held)

    if hasattr(wifi, "_release"):
        wifi._release()
    for path in (CA_FILE, CERT_FILE, KEY_FILE):
        _remove(path)

    runner.final_report()


if __name__ == "__main__":
    run()
