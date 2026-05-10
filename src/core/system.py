# core/system.py
# System boot manager.
#
# Responsibilities:
# - WiFi initialization
# - OTA scheduling
# - Launching the main application
#
# Called by boot.py after safe-mode checks.

import sys

from net import WiFiConfig, WifiManager, ota
from core.main import main


def _print_banner():
    """Display boot metadata."""

    print('\n' + '=' * 60)
    print('PortalBox Firmware - System Startup')
    print('=' * 60)
    print('MicroPython: %s' % sys.version)
    print('Platform: %s' % sys.platform)
    print('=' * 60)


def _load_wifi_config(config):
    """
    Load WiFi configuration.

    Args:
        config: WiFiConfig instance

    Returns:
        bool: True if config loaded successfully
    """

    print('[SYSTEM] Loading WiFi configuration...')

    if not config.load():

        print('[SYSTEM] ERROR: Failed to load WiFi configuration')
        print('[SYSTEM] Please configure /config/wifi_config.json')

        return False

    print('[SYSTEM] Configuration loaded:')
    print('  SSID: %s' % config.ssid)

    return True


def _get_password(config):
    """
    Get decrypted WiFi password.

    Args:
        config: WiFiConfig instance

    Returns:
        str | None: decrypted password
    """

    print('[SYSTEM] Decrypting WiFi password...')

    password = config.get_decrypted_password()

    if not password:

        print('[SYSTEM] ERROR: Failed to decrypt password')

        return None

    print('[SYSTEM] Password decrypted successfully')

    return password


def _connect_wifi(config, password):
    """
    Connect to WiFi network.

    Args:
        config: WiFiConfig instance
        password: decrypted WiFi password

    Returns:
        WifiManager | None
    """

    print('[SYSTEM] Initializing WiFi manager...')

    wifi = WifiManager(
        config = config,
        verbose = True
    )

    print('[SYSTEM] Connecting to WiFi...')

    if not wifi.connect_with_retry(password):

        print('\n' + '=' * 60)
        print('WIFI CONNECTION FAILED')
        print('=' * 60)

        status = wifi.get_status()

        if 'last_error' in status:
            print('Error: %s' % status['last_error'])

        print('=' * 60)

        return None

    return wifi


def _print_connection_status(wifi):
    """
    Print WiFi connection information.

    Args:
        wifi: WifiManager instance
    """

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
    """
    Format byte count as human-readable string.

    Args:
        size_bytes: byte count

    Returns:
        str
    """

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
    """Print filesystem storage information."""

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

        print('[SYSTEM] Storage check failed: %s' % error)


def _run_ota():
    """Run OTA update scheduler."""

    try:

        print('[SYSTEM] Running OTA scheduler...')

        ota.run_daily_update_if_due(
            isconnected = True,
            reset_after = False
        )

        print('[SYSTEM] OTA check complete')

    except Exception as error:

        print('[SYSTEM] OTA scheduler failed: %s' % error)


def boot():
    """
    Full system startup sequence.
    """

    _print_banner()

    # =========================================================
    # Load WiFi configuration
    # =========================================================

    config = WiFiConfig()

    if not _load_wifi_config(config):

        print('[SYSTEM] Continuing without WiFi')

        main()

        return

    # =========================================================
    # Decrypt password
    # =========================================================

    password = _get_password(config)

    if not password:

        print('[SYSTEM] Continuing without WiFi')

        main()

        return

    # =========================================================
    # Connect WiFi
    # =========================================================

    wifi = _connect_wifi(config, password)

    if wifi:

        _print_connection_status(wifi)

        _print_storage_info()

        _run_ota()

    else:

        print('[SYSTEM] WiFi unavailable')
        print('[SYSTEM] Continuing offline')

    # =========================================================
    # Launch main application
    # =========================================================

    print('\n[SYSTEM] Launching main application...\n')

    main()
