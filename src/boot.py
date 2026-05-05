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


def _fmt_size(size_bytes):
    """Format bytes as a compact human-readable string."""
    try:
        size = float(size_bytes)
    except Exception:
        return str(size_bytes)
    if size < 1024:
        return '%d B' % int(size)
    for unit in ['KB', 'MB', 'GB', 'TB']:
        size = size / 1024.0
        if size < 1024.0:
            return '%.1f %s' % (size, unit)
    return '%.1f PB' % size


def _print_storage_info():
    """Print total, used, and free storage for debugging."""
    try:
        total, free, used = ota._get_storage_info()
        print('\n' + '=' * 60)
        print('STORAGE INFO')
        print('=' * 60)
        print('Total: %s' % _fmt_size(total))
        print('Used:  %s' % _fmt_size(used))
        print('Free:  %s' % _fmt_size(free))
        print('=' * 60)
    except Exception as error:
        print('[BOOT] Storage check failed: %s' % error)


def main():
    """Execute the boot sequence."""
    _print_banner()

    config = WiFiConfig()
    _load_wifi_config(config)
    password = _get_password(config)
    wifi = _connect_wifi(config, password)
    _print_connection_status(wifi)
    _print_storage_info()

    ota.run_daily_update_if_due(isconnected=True, reset_after=False)
    print('\n[BOOT] Boot sequence complete. Ready for operation.')


main()
