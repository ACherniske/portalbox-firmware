# core/system.py
# System boot manager.
#
# Responsibilities:
# - Hardware (HAL) initialization
# - WiFi initialization
# - OTA scheduling
# - Launching the main application
#
# Called by boot.py after safe-mode checks.

import sys
import time
import machine

import recovery
from net import WiFiConfig, WifiManager, ota
from core.main import main
from core.device import get_device
from core.feedback import apply_feedback


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

        # Uses the public ota.storage_info() wrapper rather than the
        # private ota._get_storage_info(), so this module doesn't
        # reach across ota.py's module boundary. Note: storage_info()
        # also prints its own one-line summary before this function's
        # boxed printout — a small cosmetic duplication, not a
        # functional issue.
        info = ota.storage_info()

        print('\n' + '=' * 60)
        print('STORAGE INFO')
        print('=' * 60)

        print('Total: %s' % _fmt_size(info['total']))
        print('Used:  %s' % _fmt_size(info['used']))
        print('Free:  %s' % _fmt_size(info['free']))

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


def _wifi_retry_reset(delay_s=30, sleep=time.sleep, reset=machine.reset):
    """
    WiFi didn't connect: wait, then reset to try again from a clean boot.

    A WiFi outage is not a crash, so this boot's crash count is undone
    first (a trial version still counts it, see recovery). The wait is
    in 1 s steps that feed the watchdog. reset() never returns on the box.

    Args:
        delay_s: seconds to wait before the reset
        sleep, reset: replaced by tests
    """

    print('[SYSTEM] WiFi failed, reset in %d s' % delay_s)
    recovery.log('wifi', 'failed, reset in %d s' % delay_s)
    recovery.note_wifi_failure()

    for _ in range(delay_s):
        recovery.feed()
        sleep(1)

    reset()


def boot():
    """
    Full system startup sequence.
    """

    _print_banner()

    # =========================================================
    # Hardware first — LED/buzzer feedback needs this before any
    # WiFi/OTA work, which is the whole point of this ordering.
    # =========================================================

    device = get_device()

    apply_feedback(device, 'booting')

    # =========================================================
    # Load WiFi configuration
    # =========================================================

    config = WiFiConfig()

    if not _load_wifi_config(config):

        # A bad config can't fix itself: show the stuck signal.
        recovery.stuck_forever('wifi config')

    # =========================================================
    # Decrypt password
    # =========================================================

    password = _get_password(config)

    if not password:

        # A bad config can't fix itself: show the stuck signal.
        recovery.stuck_forever('wifi config')

    # =========================================================
    # Connect WiFi
    # =========================================================

    apply_feedback(device, 'wifi_connecting')

    wifi = _connect_wifi(config, password)

    if not wifi:

        print('[SYSTEM] WiFi unavailable')

        _wifi_retry_reset()

    # =========================================================
    # WiFi connected — proceed with OTA and launch
    # =========================================================

    _print_connection_status(wifi)

    _print_storage_info()

    apply_feedback(device, 'ota_checking')

    _run_ota()

    # =========================================================
    # Launch main application
    #
    # device.startup_sequence() is the single owner of the "ready"
    # cue (blue wipe + triple beep). core/main.py's
    # Application.initialize() must NOT also call it.
    # =========================================================

    print('\n[SYSTEM] Launching main application...\n')

    device.startup_sequence()

    main()