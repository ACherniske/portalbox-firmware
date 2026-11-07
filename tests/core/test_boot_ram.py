"""A full boot (hardware, then WiFi) keeps the ESP-IDF reserve and connects.

Runs the real core.system.boot() with the WiFi-failure reset, the OTA check
and the app replaced, so the box neither resets nor installs. Needs the
box's WiFi and a REPL without the watchdog (safe mode, or reset_to_repl).
Run it alone, then reset the box: boot() starts the hardware.
"""
from tests.test_helper import TestRunner
from tests.ram_helper import IDF_RESERVE_FREE, IDF_RESERVE_BLOCK, idf_ram


def run():
    runner = TestRunner()

    def test_full_boot_keeps_reserve():
        from core import system
        saved = (system._wifi_retry_reset, system._run_ota, system.main, system._connect_wifi)
        failed = []
        at_login = []

        def connect_wifi(*args):
            # The reserve is for WiFi to use, so check it just before the
            # login, not after: by then WiFi holds part of it.
            at_login.append(idf_ram())
            return saved[3](*args)

        system._wifi_retry_reset = lambda *a, **k: failed.append(1)
        system._run_ota = lambda: None
        system.main = lambda: None
        system._connect_wifi = connect_wifi
        try:
            system.boot()
        finally:
            (system._wifi_retry_reset, system._run_ota, system.main,
             system._connect_wifi) = saved
        free, largest = at_login[0] if at_login else (0, 0)
        import network
        nic = network.WLAN(network.STA_IF)
        connected = nic.isconnected()
        nic.disconnect()
        from net import clock
        if clock._autosave_timer is not None:
            clock._autosave_timer.deinit()
            clock._autosave_timer = None
        assert connected and not failed, "WiFi did not connect (free %d, largest %d)" % (free, largest)
        assert free >= IDF_RESERVE_FREE and largest >= IDF_RESERVE_BLOCK, (
            "reserve not kept at login: free %d (min %d), largest %d (min %d)"
            % (free, IDF_RESERVE_FREE, largest, IDF_RESERVE_BLOCK))

    runner.run_test("Full boot keeps the reserve and connects", test_full_boot_keeps_reserve)
    runner.final_report()


if __name__ == "__main__":
    run()
