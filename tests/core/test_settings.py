from tests.test_helper import TestRunner
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

    runner.run_test("Session timeout is 60 minutes", test_session_timeout_is_60_minutes)
    runner.final_report()


if __name__ == "__main__":
    run()
