# tests/core/test_device_status.py
# The hardware report: OK, OFF (turned off in settings) or FAILED per part.
# Builds a Device without setting up any hardware.

from tests.test_helper import TestRunner
from config import settings
import core.device as device_mod
from core.device import Device

_FLAGS = (
    ("buzzer", "ENABLE_BUZZER"),
    ("rfid", "ENABLE_RFID"),
    ("led", "ENABLE_LEDS"),
    ("button", "ENABLE_BUTTONS"),
)


class _Part:
    pass


class _Reader:
    def __init__(self, initialized):
        self.is_initialized = initialized


class _Relay:
    def __init__(self, state=False):
        self.state = state


class _Patch:
    """Set attributes and put them all back on undo(); a name the object did
    not have is removed again."""

    _MISSING = object()

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name, self._MISSING)))
        setattr(obj, name, value)

    def undo(self):
        while self.saved:
            obj, name, value = self.saved.pop()
            if value is self._MISSING:
                delattr(obj, name)
            else:
                setattr(obj, name, value)


class _BareDevice(Device):
    """A Device that sets up no hardware (MicroPython has no __new__)."""

    def __init__(self):
        pass


def _device(**parts):
    """A Device with every part present and working, except those given."""
    device = _BareDevice()
    device.buzzer = _Part()
    device.relay = _Relay()
    device.rfid = _Reader(True)
    device.led = _Part()
    device.button = _Part()
    for name, value in parts.items():
        setattr(device, name, value)
    return device


def run():
    runner = TestRunner()
    patch = _Patch()

    def fresh(test):
        def wrapped():
            for _, flag in _FLAGS:
                patch.set(settings, flag, 1)
            try:
                test()
            finally:
                patch.undo()

        return wrapped

    def test_all_present_ok():
        status = _device().get_status()
        for name in ("buzzer", "rfid", "led", "button", "relay"):
            assert status[name] == "OK", "%s is %r" % (name, status[name])

    def test_disabled_part_is_off():
        for name, flag in _FLAGS:
            patch.set(settings, flag, 0)
            status = _device(**{name: None}).get_status()
            assert status[name] == "OFF", "%s off: %r" % (name, status[name])
            for other, _ in _FLAGS:
                if other != name:
                    assert status[other] == "OK", "%s is %r" % (other, status[other])
            patch.set(settings, flag, 1)

    def test_off_but_set_up():
        # The flag decides, even if the part is there (and the reader is set up).
        for name, flag in _FLAGS:
            patch.set(settings, flag, 0)
            status = _device().get_status()
            assert status[name] == "OFF", "%s off but set up: %r" % (name, status[name])
            patch.set(settings, flag, 1)

    def test_enabled_missing_part_failed():
        for name, _ in _FLAGS:
            status = _device(**{name: None}).get_status()
            assert status[name] == "FAILED", "%s missing: %r" % (name, status[name])

    def test_rfid_not_initialized_failed():
        status = _device(rfid=_Reader(False)).get_status()
        assert status["rfid"] == "FAILED", "rfid %r" % status["rfid"]

    def test_relay_missing_failed():
        status = _device(relay=None).get_status()
        assert status["relay"] == "FAILED", "relay %r" % status["relay"]
        assert status["relay_state"] is None, "state %r" % status["relay_state"]

    def test_relay_state_unchanged():
        status = _device(relay=_Relay(True)).get_status()
        assert status["relay_state"] is True, "state %r" % status["relay_state"]
        status = _device(relay=_Relay(False)).get_status()
        assert status["relay_state"] is False, "state %r" % status["relay_state"]

    def test_print_status_says_off():
        lines = []

        def fake_print(*args, **kwargs):
            lines.append(" ".join(str(a) for a in args))

        patch.set(settings, "ENABLE_BUZZER", 0)
        patch.set(device_mod, "print", fake_print)
        _device(buzzer=None, rfid=_Reader(False)).print_status()
        for want in ("  BUZZER: OFF", "  RFID: FAILED", "  RELAY: OK", "  Relay: OFF"):
            assert want in lines, "no %r in %r" % (want, lines)

    tests = [
        ("Every part present: OK", test_all_present_ok),
        ("Part turned off in settings: OFF", test_disabled_part_is_off),
        ("Turned off but still set up: OFF", test_off_but_set_up),
        ("Part turned on but missing: FAILED", test_enabled_missing_part_failed),
        ("Card reader not initialized: FAILED", test_rfid_not_initialized_failed),
        ("Relay missing: FAILED, no state", test_relay_missing_failed),
        ("Relay state unchanged", test_relay_state_unchanged),
        ("Report prints OFF", test_print_status_says_off),
    ]
    for name, test in tests:
        runner.run_test(name, fresh(test))

    runner.final_report()


if __name__ == "__main__":
    run()
