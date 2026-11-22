from tests.test_helper import TestRunner
import recovery
from config import settings


def run():
    runner = TestRunner()

    def test_session_timeout_is_60_minutes():
        expected = 60 * 60 * 1000
        assert settings.DEVICE_TIMEOUT_MS == expected, (
            "DEVICE_TIMEOUT_MS is %d ms (%.1f h), want %d" % (
                settings.DEVICE_TIMEOUT_MS,
                settings.DEVICE_TIMEOUT_MS / 3_600_000,
                expected))

    def test_api_timeout_under_watchdog():
        # One network step must end, and the watchdog be fed, well before it fires.
        t = settings.API_TIMEOUT_S
        assert 0 < t * 1000 <= recovery.WDT_MS // 3, (
            "API_TIMEOUT_S is %r s; watchdog %d ms" % (t, recovery.WDT_MS))

    runner.run_test("Session timeout is 60 minutes", test_session_timeout_is_60_minutes)
    runner.run_test("API timeout well under the watchdog", test_api_timeout_under_watchdog)
    runner.final_report()


if __name__ == "__main__":
    run()
