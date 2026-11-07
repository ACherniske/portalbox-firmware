# tests/test_api.py
# Simple API layer test utility
#
# Scans RFID cards and prints:
# - Raw UID
# - Auth status
# - Trainer status
# - Proxy status
#
# Useful for validating mock API configuration.

import time

from core.device import get_device
from core import api


def print_card_results(uid):
    """Print API helper results for a UID."""

    print("\n========================================")
    print(f"UID: {uid}")

    print(f"Authorized : {api.is_auth(uid)}")
    print(f"Trainer    : {api.is_trainer(uid)}")
    print(f"Proxy      : {api.is_proxy(uid)}")

    print("========================================\n")


def main():
    """Main test loop."""

    print("========================================")
    print(" Mock API Test Utility")
    print("========================================")
    print("Scan RFID cards to test API helpers.")
    print("Press CTRL+C to exit.")
    print("")

    device = get_device()

    last_uid = None

    try:

        while True:

            uid = device.read_card()

            # Only print when a new card appears
            if uid and uid != last_uid:

                print_card_results(uid)

                # Audible feedback
                device.buzzer_card_read()

                last_uid = uid

            # Reset when card removed
            elif not uid:
                last_uid = None

            time.sleep_ms(100)

    except KeyboardInterrupt:

        print("\n[Test] Stopping API test")

    finally:

        device.cleanup()


if __name__ == "__main__":
    main()
