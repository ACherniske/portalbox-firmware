"""The OTA code stays out of RAM until an update check runs.

Each test imports a module fresh (its packages removed from sys.modules
first) and checks which modules came along. sys.modules is put back after.
"""
import sys
from tests.test_helper import TestRunner

OTA_MODULES = ('net.ota', 'net.https', 'net.ota_stage', 'net.ota_target')


def _fresh_import(name):
    """Import `name` with every net.* and core.* module unloaded first.
    Returns the module names it loaded; restores sys.modules after."""
    saved = dict(sys.modules)
    try:
        for key in list(sys.modules):
            if key == 'net' or key.startswith('net.') or key == 'core' or key.startswith('core.'):
                del sys.modules[key]
        __import__(name)
        return [key for key in sys.modules if key not in saved or sys.modules[key] is not saved[key]]
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def _loaded(modules, names):
    return [name for name in names if name in modules]


def run():
    runner = TestRunner()

    def test_net_loads_no_ota():
        loaded = _loaded(_fresh_import('net'), OTA_MODULES)
        assert not loaded, "import net loaded %s" % (loaded,)

    def test_system_loads_no_ota_or_app():
        loaded = _loaded(_fresh_import('core.system'), OTA_MODULES + ('core.main',))
        assert not loaded, "import core.system loaded %s" % (loaded,)

    def test_app_loads_no_ota():
        loaded = _loaded(_fresh_import('core.main'), OTA_MODULES)
        assert not loaded, "import core.main loaded %s" % (loaded,)

    runner.run_test("Importing net loads no OTA code", test_net_loads_no_ota)
    runner.run_test("Boot code loads no OTA or app code", test_system_loads_no_ota_or_app)
    runner.run_test("App module loads no OTA code", test_app_loads_no_ota)
    runner.final_report()


if __name__ == "__main__":
    run()
