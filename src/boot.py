# boot.py
# Runs on boot to initialize WiFi and hand off OTA scheduling to net.ota.

import sys

from net import WiFiConfig, WifiManager, ota


def _print_banner():
    """Display boot metadata."""
    print('\n' + '=' * 60)
    print('PortalBox Firmware - Boot Sequence')
    print('=' * 60)
    print('MicroPython: %s' % sys.version)
    print('Platform: %s' % sys.platform)
    print('=' * 60)


def _load_wifi_config(config):
    """Load the device WiFi configuration."""
    print('[BOOT] Loading WiFi configuration...')
    if not config.load():
        print('[BOOT] ERROR: Failed to load WiFi configuration')
        print('[BOOT] Please configure WiFi settings in /config/wifi_config.json')
        sys.exit(1)

    print('[BOOT] Configuration loaded: %s' % config.ssid)


def _get_password(config):
    """Return the decrypted WiFi password or stop boot if unavailable."""
    print('[BOOT] Decrypting password...')
    password = config.get_decrypted_password()
    if not password:
        print('[BOOT] ERROR: Failed to decrypt password from configuration')
        sys.exit(1)

    print('[BOOT] Password successfully decrypted')
    return password


def _connect_wifi(config, password):
    """Connect to the configured WiFi network."""
    print('[BOOT] Initializing WiFi manager...')
    wifi = WifiManager(config=config, verbose=True)

    print('[BOOT] Connecting to network...')
    if not wifi.connect_with_retry(password):
        print('\n' + '=' * 60)
        print('WIFI CONNECTION FAILED')
        print('=' * 60)

        status = wifi.get_status()
        if 'last_error' in status:
            print('Error: %s' % status['last_error'])

        print('\n[BOOT] Boot sequence failed. System halted.')
        print('=' * 60)
        sys.exit(1)

    return wifi


def _print_connection_status(wifi):
    """Print connection details after WiFi is up."""
    print('\n' + '=' * 60)
    print('WIFI CONNECTION SUCCESSFUL')
    print('=' * 60)

    status = wifi.get_status()
    print('SSID: %s' % status['ssid'])
    print('Network Type: %s' % status['network_type'])
    print('IP Address: %s' % status['ip'])
    print('Gateway: %s' % status['gateway'])
    print('DNS: %s' % status['dns'])
    print('=' * 60)


def main():
    """Execute the boot sequence."""
    _print_banner()

    config = WiFiConfig()
    _load_wifi_config(config)
    password = _get_password(config)
    wifi = _connect_wifi(config, password)
    _print_connection_status(wifi)

    ota.run_daily_update_if_due(isconnected=True, reset_after=False)
    print('\n[BOOT] Boot sequence complete. Ready for operation.')


main()
