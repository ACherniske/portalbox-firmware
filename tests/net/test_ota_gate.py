import time
from tests.test_helper import TestRunner
from net import ota


def _at(year, month, day, hour=12):
    # Board epoch, whatever it is (2000 on this port).
    return time.mktime((year, month, day, hour, 0, 0, 0, 0))


def run():
    runner = TestRunner()

    def test_unset_clock_runs():
        assert ota._should_run_daily_ota(_at(2000, 1, 1), {}) is True, "unset clock skipped OTA"

    def test_recent_run_skips():
        now = _at(2026, 11, 5)
        state = {'last_success_epoch': now - 10}
        assert ota._should_run_daily_ota(now, state) is False, "ran again 10 s after the last OTA"

    def test_day_old_run_runs():
        now = _at(2026, 11, 5)
        state = {'last_success_epoch': now - 25 * 3600}
        assert ota._should_run_daily_ota(now, state) is True, "skipped OTA 25 h after the last one"

    runner.run_test("Unset clock runs OTA", test_unset_clock_runs)
    runner.run_test("Run 10 s ago skips OTA", test_recent_run_skips)
    runner.run_test("Run 25 h ago runs OTA", test_day_old_run_runs)

    runner.final_report()


if __name__ == "__main__":
    run()
