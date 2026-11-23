import os
from tests.test_helper import TestRunner
from net.models import WiFiConfig, NetworkType
import time

TEST_DIR = "/t_config"


def run():
    runner = TestRunner()

    def test_save_load_psk():
        """Tests standard WPA2 PSK saving and loading."""
        config = WiFiConfig()
        test_ssid = "Test_Router"
        test_pass = "Password123!"

        # Setup and save
        config.set_wpa2_psk(test_ssid, test_pass)
        assert config.save() is True, "Failed to save PSK config"

        time.sleep(0.2)  # Small delay to ensure file system has time to write

        # Create new instance and load
        new_config = WiFiConfig()
        assert new_config.load() is True, "Failed to load PSK config"

        # Verify data integrity
        assert new_config.ssid == test_ssid
        assert new_config.network_type == NetworkType.WPA2_PSK
        assert new_config.verify_password(test_pass) is True, (
            "Password verification failed after load"
        )
        assert new_config.get_decrypted_password() == test_pass, (
            "Decryption failed after load"
        )

    def test_save_load_enterprise():
        """Tests WPA2 Enterprise specific fields."""
        config = WiFiConfig()
        test_user = "student_01"
        test_pass = "UniPass"

        config.set_wpa2_enterprise("Eduroam", test_user, test_pass)
        config.identity = "anonymous@uni.edu"
        config.save()

        time.sleep(0.2)  # Small delay to ensure file system has time to write

        new_config = WiFiConfig()
        new_config.load()

        assert new_config.username == test_user
        assert new_config.identity == "anonymous@uni.edu"
        assert new_config.verify_password(test_pass) is True

    def test_missing_file():
        """Verifies behavior when no config file exists."""
        try:
            os.remove(WiFiConfig.CONFIG_FILE)
        except Exception:
            pass

        config = WiFiConfig()
        # Loading a non-existent file should return False, not crash
        assert config.load() is False, "Load should return False if file is missing"

    def _read(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    real_path = WiFiConfig.CONFIG_FILE
    real_before = _read(real_path)

    def test_real_config_untouched():
        assert _read(real_path) == real_before, "the box's own %s changed" % real_path

    # The tests save, load and delete a config file: point them at a scratch
    # file so the box keeps its own WiFi config.
    try:
        os.mkdir(TEST_DIR)
    except OSError:
        pass
    WiFiConfig.CONFIG_FILE = TEST_DIR + "/wifi_config.json"
    try:
        runner.run_test("PSK Config Round-trip", test_save_load_psk)
        runner.run_test("Enterprise Config Round-trip", test_save_load_enterprise)
        runner.run_test("Missing Config Handling", test_missing_file)
    finally:
        WiFiConfig.CONFIG_FILE = real_path
        for name in os.listdir(TEST_DIR):
            os.remove(TEST_DIR + "/" + name)
        os.rmdir(TEST_DIR)
    runner.run_test("Box's real WiFi config untouched", test_real_config_untouched)

    runner.final_report()


if __name__ == "__main__":
    run()
