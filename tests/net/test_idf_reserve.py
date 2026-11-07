"""The firmware keeps ESP-IDF RAM free for WiFi logins, however much the
Python heap wants.

Needs the box's WiFi (bucknell.edu config on the box) and a REPL without the
watchdog (safe mode, or reset_to_repl). Run it alone: the ballast grows the
Python heap for good, so reset the box afterwards.
"""
import gc
import time
from tests.test_helper import TestRunner
from tests.ram_helper import (IDF_RESERVE_FREE, IDF_RESERVE_BLOCK, FAIL_BLOCK,
                              idf_ram, fill)

LOGINS = 5


def _wifi():
    from net import WiFiConfig, WifiManager
    config = WiFiConfig()
    if not config.load():
        raise Exception('no /config/wifi_config.json')
    return WifiManager(config=config, verbose=False), config.get_decrypted_password()


def _strongest_bssid(ssid):
    """BSSID of the strongest access point for `ssid`. The test pins it so it
    tests memory only: one campus access point logs the box in but never
    gives it an IP address."""
    import network
    nic = network.WLAN(network.STA_IF)
    nic.active(True)
    aps = [ap for ap in nic.scan() if ap[0].decode('utf-8', 'replace') == ssid]
    if not aps:
        raise Exception('no access point for the configured network')
    return max(aps, key=lambda ap: ap[3])[1]


def _stop_autosave():
    from net import clock
    if clock._autosave_timer is not None:
        clock._autosave_timer.deinit()
        clock._autosave_timer = None


def run():
    runner = TestRunner()
    held = []

    def test_heap_stops_at_reserve():
        held.append(fill(stop_free=IDF_RESERVE_FREE - 4096))
        free, largest = idf_ram()
        assert free >= IDF_RESERVE_FREE and largest >= IDF_RESERVE_BLOCK, (
            "Python grew into the reserve: free %d (min %d), largest %d (min %d)"
            % (free, IDF_RESERVE_FREE, largest, IDF_RESERVE_BLOCK))

    def test_wifi_with_heap_full():
        # Load the WiFi and HTTPS code first: with the heap full nothing can
        # be imported. Then grow the heap to its limit and free a few blocks:
        # Python keeps the RAM it took from ESP-IDF, but has room to run.
        del held[:]
        gc.collect()
        mgr, password = _wifi()
        mgr.config.bssid = _strongest_bssid(mgr.config.ssid)
        from net import https
        ballast = fill(stop_block=FAIL_BLOCK)
        del ballast[-8:]
        held.append(ballast)
        gc.collect()
        free, largest = idf_ram()
        try:
            for n in range(LOGINS):
                assert mgr.connect(password), "login %d of %d failed (%s) at free %d, largest %d" % (
                    n + 1, LOGINS, mgr.last_error, free, largest)
                if n < LOGINS - 1:
                    mgr.nic.disconnect()
                    time.sleep(1)
            body = https.get('https://api.github.com/zen', max_bytes=512)
            assert body, "empty HTTPS body"
        finally:
            if mgr.nic is not None:
                mgr.nic.disconnect()
            _stop_autosave()

    runner.run_test("Python heap stops at the reserve", test_heap_stops_at_reserve)
    runner.run_test("WiFi logs in with the Python heap full", test_wifi_with_heap_full)
    runner.final_report()


if __name__ == "__main__":
    run()
