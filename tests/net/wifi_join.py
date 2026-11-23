"""Join WiFi for the live network tests, the way core/system.py does at boot.

Kept small and apart from the test suites: a suite that loads a big module
just before the WPA2-Enterprise login grows the MicroPython heap, which
takes the ESP-IDF RAM the login needs ('WiFi Out of Memory').
"""


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
        raise Exception("no /config/wifi_config.json")
    password = config.get_decrypted_password()
    if not WifiManager(config=config, verbose=False).connect_with_retry(password):
        raise Exception("WiFi did not connect")
    from net import clock

    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None


def join_first():
    """Join WiFi before the offline tests, on a fresh heap, as the box does
    at boot. After them the heap is fragmented; if it grows during the
    WPA2-Enterprise login it takes the ESP-IDF RAM the login needs and the
    join fails ('WiFi Out of Memory'). A failed join here is left to the
    live tests to report."""
    try:
        _online()
    except Exception as e:
        print("[TEST] WiFi join before the tests failed: %r" % (e,))
