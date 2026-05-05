"""Standalone OTA test launcher for PortalBox.

This script loads WiFi configuration from `/config/wifi_config.json`,
establishes connection, then pulls the `ota-test` branch from the configured
GitHub repository using the OTA helpers in `net.ota`.
"""

import time
import urequests
from net import ota
from net.models import WiFiConfig
from net.wifi import WifiManager

OTA_BRANCH = 'ota-test'
RESET_AFTER_UPDATE = True


def main():
    """Load WiFi config, connect, then pull the OTA test branch."""
    # 1. Load WiFi Config
    print('[OTA TEST] Loading WiFi configuration...')
    conf = WiFiConfig()
    if not conf.load():
        print('[OTA TEST] Failed to load WiFi config from /config/wifi_config.json')
        return

    # 2. Initialize WiFi Manager
    print('[OTA TEST] Initializing WiFi manager...')
    wifi = WifiManager(config=conf)

    # 3. Connect with retry
    print('[OTA TEST] Connecting to WiFi (%s)...' % conf.ssid)
    pwd = conf.get_decrypted_password()

    if wifi.connect_with_retry(password=pwd):
        print('[OTA TEST] WiFi connected!')
        print('-' * 50)

        # 4. Run OTA update
        try:
            print('[OTA TEST] Updating from branch: %s' % OTA_BRANCH)
            ota.pull_all(branch=OTA_BRANCH, isconnected=True,
                         reset_after=RESET_AFTER_UPDATE)
            print('[OTA TEST] Update complete')
        except Exception as e:
            print('[OTA TEST] Update failed: %s' % e)

    else:
        print('[OTA TEST] WiFi connection failed: %s' % wifi.last_error)


if __name__ == '__main__':
    main()
