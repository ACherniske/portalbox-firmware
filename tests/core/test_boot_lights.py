from tests.test_helper import TestRunner
from hardware.led import LEDController
from core.device import Device
from core.feedback import apply_feedback

BLUE = (0, 0, 255)
OFF = (0, 0, 0)
NUM = 4


class FakeDriver:
    """Records the pixel colors on every show()."""

    def __init__(self):
        self.brightness = 31
        self.num_leds = NUM
        self.pixels = [OFF] * NUM
        self.frames = []

    def set_pixel(self, i, r, g, b):
        self.pixels[i] = (r, g, b)

    def fill(self, r, g, b):
        self.pixels = [(r, g, b)] * NUM

    def show(self):
        self.frames.append(list(self.pixels))


class FakeDevice:
    """A real LEDController on a FakeDriver; no buzzer. Device's LED
    methods are borrowed so feedback profiles reach the controller."""

    def __init__(self):
        self.driver = FakeDriver()
        self.led = LEDController(self.driver)
        self.buzzer = None

    def __getattr__(self, name):
        method = getattr(Device, name)
        return lambda *args, **kwargs: method(self, *args, **kwargs)


def run():
    runner = TestRunner()

    def test_boot_stages_white():
        # booting, connecting and the update check all show the same white
        seen = {}
        for name in ("booting", "wifi_connecting", "ota_checking"):
            dev = FakeDevice()
            apply_feedback(dev, name)
            assert dev.driver.frames, "%s showed nothing" % name
            seen[name] = dev.driver.frames[-1]
        colors = set(c for frame in seen.values() for c in frame)
        assert len(colors) == 1, "boot stages differ: %r" % (seen,)
        r, g, b = colors.pop()
        assert r == g == b and r > 0, "boot color %r is not white" % ((r, g, b),)

    def test_ready_cue_wipes_in_order():
        dev = FakeDevice()
        Device.startup_sequence(dev)
        lit = [f.count(BLUE) for f in dev.driver.frames]
        want = list(range(1, NUM + 1))
        assert lit[:NUM] == want, "blue LEDs per frame %r, want %r first" % (
            lit,
            want,
        )

    def test_ready_cue_holds_blue():
        # no dark gap: the wipe stays lit until the idle pulse takes over
        dev = FakeDevice()
        Device.startup_sequence(dev)
        frames = dev.driver.frames
        assert frames, "nothing shown before startup_sequence returned"
        assert frames[-1] == [BLUE] * NUM, "last frame %r, want all blue" % (
            frames[-1],
        )

    runner.run_test(
        "Booting, connecting, update check: same white", test_boot_stages_white
    )
    runner.run_test("Ready cue wipes blue LED by LED", test_ready_cue_wipes_in_order)
    runner.run_test("Ready cue ends solid blue", test_ready_cue_holds_blue)

    runner.final_report()


if __name__ == "__main__":
    run()
