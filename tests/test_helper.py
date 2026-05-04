import machine
import neopixel
import time

class TestRunner:
    # Pin for the onboard RGB LED ESP32-S3 Devkit C V1.1 uses GPIO38
    LED_PIN = 38
    
    # Colors (RGB format)
    COLOR_PASS = (0, 255, 0)   # Green
    COLOR_FAIL = (255, 0, 0)   # Red
    COLOR_RUN  = (0, 0, 255)   # Blue
    COLOR_OFF  = (0, 0, 0)

    def __init__(self):
        # S3 DevKit uses 1 addressable RGB LED
        self.np = neopixel.NeoPixel(machine.Pin(self.LED_PIN), 1)
        self.total_tests = 0
        self.failed_tests = 0

    def set_led(self, color):
        try:
            self.np[0] = color
            self.np.write()
        except:
            pass

    def run_test(self, name, test_func):
        self.total_tests += 1
        print(f"[TEST] {name}... ", end="")
        self.set_led(self.COLOR_RUN)
        
        try:
            test_func()
            print("PASS")
        except Exception as e:
            self.failed_tests += 1
            print(f"FAILED!\n  -> {e}")
            self.set_led(self.COLOR_FAIL)
            time.sleep(0.5)

    def final_report(self):
        print("-" * 30)
        print(f"Tests Run: {self.total_tests} | Failures: {self.failed_tests}")
        
        if self.failed_tests == 0:
            print("STATUS: SUCCESS")
            self.set_led(self.COLOR_PASS)
        else:
            print("STATUS: FAILED")
            self.set_led(self.COLOR_FAIL)
        print("-" * 30)
