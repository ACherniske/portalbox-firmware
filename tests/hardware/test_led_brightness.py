import uasyncio as asyncio
from tests.test_helper import TestRunner
from hardware.led import LEDController

BASE = 12          # driver brightness set at start (DotStar-style 0-31)
YELLOW = (255, 255, 0)
RED = (255, 0, 0)


class FakeDriver:
    """Records (color, brightness) on every fill."""

    def __init__(self):
        self.brightness = BASE
        self.num_leds = 4
        self.fills = []

    def fill(self, r, g, b):
        self.fills.append(((r, g, b), self.brightness))

    def show(self):
        pass


def _after_pulse(then):
    """Pulse green a while (brightness moves), then run `then(led)`;
    returns the driver."""
    driver = FakeDriver()
    led = LEDController(driver)

    async def go():
        led.pulse('GREEN', min_brightness=0.05, max_brightness=0.4,
                  step=0.05, interval_ms=10)
        await asyncio.sleep_ms(60)
        then(led)
        await asyncio.sleep_ms(30)
        led.stop()

    asyncio.run(go())
    return driver


def run():
    runner = TestRunner()

    def test_pulse_changes_brightness():
        # Guards the premise: the pulse really moved the brightness.
        driver = _after_pulse(lambda led: None)
        levels = set(b for c, b in driver.fills if c == (0, 255, 0))
        assert any(b != BASE for b in levels), "pulse never changed brightness: %r" % levels

    def test_blink_after_pulse_at_base():
        driver = _after_pulse(lambda led: led.blink('YELLOW', on_ms=20, off_ms=20))
        yellow = [b for c, b in driver.fills if c == YELLOW]
        assert yellow, "no yellow fill"
        assert all(b == BASE for b in yellow), "yellow at brightness %r, want %d" % (yellow, BASE)

    def test_fill_after_pulse_at_base():
        driver = _after_pulse(lambda led: led.fill('RED'))
        red = [b for c, b in driver.fills if c == RED]
        assert red == [BASE], "red at brightness %r, want [%d]" % (red, BASE)

    runner.run_test("Pulse moves the brightness", test_pulse_changes_brightness)
    runner.run_test("Blink after a pulse at normal brightness", test_blink_after_pulse_at_base)
    runner.run_test("Fill after a pulse at normal brightness", test_fill_after_pulse_at_base)

    runner.final_report()


if __name__ == "__main__":
    run()
