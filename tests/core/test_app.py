import uasyncio as asyncio
from tests.test_helper import TestRunner
import recovery
import core.device as device_mod
import core.main as app_main
from core import api


class _Patch:
    """Set attributes and put them all back on undo()."""

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        while self.saved:
            obj, name, value = self.saved.pop()
            setattr(obj, name, value)


class FakeDevice:
    """Records relay calls; get_status() reports what the test sets."""

    def __init__(self, rfid=True, relay=True):
        self.status = {'rfid': rfid, 'relay': relay, 'led': True,
                       'buzzer': True, 'button': True, 'relay_state': False}
        self.relay_on = True
        self.calls = []

    def get_status(self):
        return self.status

    def relays_off(self):
        self.relay_on = False
        self.calls.append('relays_off')

    def cleanup(self):
        self.calls.append('cleanup')

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def run():
    runner = TestRunner()
    patch = _Patch()
    marked = []
    logged = []

    def fresh(test):
        def wrapped():
            marked.clear()
            logged.clear()
            patch.set(recovery, 'mark_healthy', lambda: marked.append(1))
            patch.set(recovery, 'log', lambda tag, msg: logged.append((tag, msg)))
            try:
                test()
            finally:
                patch.undo()
        return wrapped

    # --- item 4: main() turns the relays off whatever stopped the app -------

    class _Interrupting:
        """asyncio stand-in: run() raises like a Ctrl-C between tasks."""

        def run(self, coro):
            coro.close()
            raise KeyboardInterrupt

        def new_event_loop(self):
            pass

    def test_main_interrupt_relays_off():
        dev = FakeDevice()
        patch.set(device_mod, '_device', dev)
        patch.set(app_main, 'asyncio', _Interrupting())
        try:
            app_main.main()
        except KeyboardInterrupt:
            pass
        assert dev.relay_on is False, "relay left on after Ctrl-C: calls %r" % dev.calls
        assert dev.calls[0] == 'relays_off', "relays_off not first: %r" % dev.calls

    def test_main_no_device_creates_none():
        # Shutdown already cleaned up: main() must not start the hardware again.
        patch.set(device_mod, '_device', None)
        made = []
        patch.set(device_mod, 'get_device', lambda: made.append(1))
        patch.set(app_main, 'get_device', lambda: made.append(1))
        patch.set(app_main, 'asyncio', _Interrupting())
        try:
            app_main.main()
        except KeyboardInterrupt:
            pass
        assert made == [], "main() created a device"

    # --- item 5: healthy only with the card reader and relay working -------

    def healthy_with(rfid, relay):
        app = app_main.Application()
        app.device = FakeDevice(rfid=rfid, relay=relay)
        app._healthy()

    def test_healthy_marks():
        healthy_with(True, True)
        assert marked == [1], "not marked healthy with working hardware"

    def test_no_rfid_not_healthy():
        healthy_with(False, True)
        assert marked == [], "marked healthy without the card reader"
        assert any(tag == 'app' and 'rfid' in msg for tag, msg in logged), "no log: %r" % logged

    def test_no_relay_not_healthy():
        healthy_with(True, False)
        assert marked == [], "marked healthy without the relay"
        assert any(tag == 'app' and 'relay' in msg for tag, msg in logged), "no log: %r" % logged

    def test_fsm_gets_health_check():
        got = []

        class FakeFSM:
            async def run(self, mark_healthy=None, healthy_after_ms=None):
                got.append(mark_healthy)

            def stop(self):
                pass

        async def no_loop(self, every_ms=0):
            pass

        dev = FakeDevice(rfid=False)
        patch.set(app_main, 'get_device', lambda: dev)
        patch.set(app_main, 'DeviceFSM', FakeFSM)
        patch.set(app_main, 'cleanup_device', lambda: None)
        patch.set(app_main.Application, '_ota_loop', no_loop)
        patch.set(api, 'startup', lambda: 'ok')
        asyncio.run(app_main.Application().run())
        assert len(got) == 1 and got[0] is not None, "mark_healthy %r" % (got,)
        got[0]()
        assert marked == [], "FSM's mark_healthy skips the hardware check"

    # --- startup report: once, before the FSM; never stops the app -------

    def run_app_with_startup(startup):
        """Run the app with fakes; returns the events in order."""
        events = []

        class FakeFSM:
            async def run(self, mark_healthy=None, healthy_after_ms=None):
                events.append('fsm')

            def stop(self):
                pass

        async def no_loop(self, every_ms=0):
            pass

        def fake_startup():
            events.append('startup')
            return startup()

        dev = FakeDevice()
        patch.set(app_main, 'get_device', lambda: dev)
        patch.set(app_main, 'DeviceFSM', FakeFSM)
        patch.set(app_main, 'cleanup_device', lambda: None)
        patch.set(app_main.Application, '_ota_loop', no_loop)
        patch.set(api, 'startup', fake_startup)
        asyncio.run(app_main.Application().run())
        return events

    def test_startup_before_fsm():
        events = run_app_with_startup(lambda: 'ok')
        assert events == ['startup', 'fsm'], "events %r" % (events,)
        assert any(tag == 'app' and 'ok' in msg for tag, msg in logged), "no log: %r" % logged

    def test_startup_refused_keeps_running():
        events = run_app_with_startup(lambda: 'not registered')
        assert events == ['startup', 'fsm'], "events %r" % (events,)
        assert any(tag == 'app' and 'not registered' in msg for tag, msg in logged),             "no log: %r" % logged

    def test_startup_error_keeps_running():
        def boom():
            raise OSError(113)
        events = run_app_with_startup(boom)
        assert events == ['startup', 'fsm'], "events %r" % (events,)
        assert any(tag == 'app' and 'OSError' in msg for tag, msg in logged),             "no log: %r" % logged

    # --- item 8b: an error in one idle check doesn't end the OTA loop -------

    def test_ota_loop_survives_error():
        app = app_main.Application()
        app.running = True
        checks = []

        def check():
            checks.append(1)
            if len(checks) == 1:
                raise MemoryError('test')
            app.running = False

        app._ota_check = check
        asyncio.run(app._ota_loop(every_ms=20))
        assert checks == [1, 1], "checks %r" % (checks,)
        assert any(tag == 'ota' and 'MemoryError' in msg for tag, msg in logged), "no log: %r" % logged

    tests = [
        ("Ctrl-C in main() turns the relays off", test_main_interrupt_relays_off),
        ("main() never creates a device", test_main_no_device_creates_none),
        ("Healthy with reader and relay", test_healthy_marks),
        ("Not healthy without the card reader", test_no_rfid_not_healthy),
        ("Not healthy without the relay", test_no_relay_not_healthy),
        ("FSM gets the hardware health check", test_fsm_gets_health_check),
        ("OTA loop survives a failed check", test_ota_loop_survives_error),
        ("Startup report goes to the server before the FSM", test_startup_before_fsm),
        ("Box not registered: logged, app keeps running", test_startup_refused_keeps_running),
        ("Startup report error: logged, app keeps running", test_startup_error_keeps_running),
    ]
    for name, test in tests:
        runner.run_test(name, fresh(test))

    runner.final_report()


if __name__ == "__main__":
    run()
