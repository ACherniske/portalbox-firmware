from tests.test_helper import TestRunner
from core import api_mock as api

# Values a JSON reply could carry. Only a real True grants anything.
TRUE_ONLY = [
    (True, True),
    (False, False),
    ('false', False),
    ('true', False),
    (1, False),
    ('yes', False),
    (None, False),
]


def run():
    runner = TestRunner()

    def check(flag, func):
        saved = api.USERS
        try:
            for value, want in TRUE_ONLY:
                api.USERS = {'C1': {'name': 'x', flag: value}}
                got = func('C1')
                assert got is want, "%s=%r gave %r, want %r" % (flag, value, got, want)
            api.USERS = {}
            assert func('C1') is False, "unknown card gave %r" % (func('C1'),)
        finally:
            api.USERS = saved

    runner.run_test("is_auth accepts only True", lambda: check('auth', api.is_auth))
    runner.run_test("is_trainer accepts only True", lambda: check('trainer', api.is_trainer))
    runner.run_test("is_proxy accepts only True", lambda: check('proxy', api.is_proxy))

    runner.final_report()


if __name__ == "__main__":
    run()
