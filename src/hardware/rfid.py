# hardware/rfid.py
# Interface for MFRC522 RFID reader with debouncing and card management

from machine import Pin
from hardware.lib.mfrc522 import MFRC522
import time


class RFIDReader:
    """Interface for MFRC522 RFID reader with debouncing and card management."""

    def __init__(self, spi, cs_pin, rst_pin, debounce_ms=1000):
        """
        Initialize RFID reader.

        Args:
            spi: Initialized SPI bus object
            cs_pin: Chip select pin number or Pin object
            rst_pin: Reset pin number or Pin object
            debounce_ms: Debounce time in milliseconds (default 1000)
        """
        self.reader = MFRC522(
            spi=spi,
            cs_pin=cs_pin,
            rst_pin=rst_pin
        )

        self.last_uid = None
        self.last_read_time = 0
        self.debounce_ms = debounce_ms

        if self.reader.initialized:
            print("[RFID] Initialized MFRC522")
        else:
            print("[RFID] Failed to initialize MFRC522")

    def detect_card(self):
        """
        Detect if a card is present.

        Returns:
            Tuple of (status, uid) where uid is first 4 bytes or None
        """
        # Request card
        (status, atqa) = self.reader.request(self.reader.PICC_REQ_IDL)
        if status != self.reader.OK:
            return self.reader.ERROR, None

        # Anti-collision
        (status, uid) = self.reader.anticoll()
        if status != self.reader.OK:
            return self.reader.ERROR, None

        # Select card
        if self.reader.select_card(uid) == 0:
            return self.reader.ERROR, None

        return self.reader.OK, uid[:4]  # Return only the first 4 bytes of UID

    def read_card(self):
        """
        Read RFID card and return UID as hex string.

        Includes debouncing to prevent multiple reads of the same card.

        Returns:
            Hex string UID or None if no card or within debounce period
        """
        try:
            (status, uid) = self.detect_card()

            if status == self.reader.OK and uid is not None:
                # Convert UID to hex string
                uid_str = ''.join([f'{b:02X}' for b in uid])

                # Debounce check
                current_time = time.ticks_ms()
                if (uid_str == self.last_uid and
                    time.ticks_diff(current_time, self.last_read_time) <
                        self.debounce_ms):
                    return None

                self.last_uid = uid_str
                self.last_read_time = current_time

                print(f"[RFID] Card detected: {uid_str}")
                return uid_str

        except Exception as e:
            print(f"[RFID] Error: {e}")

        return None

    def card_present(self):
        """
        Check if any RFID card is currently present.

        This method does not apply UID debounce logic and should be used
        for "wait until card removed" flows.

        Returns:
            bool: True if a card is detected, else False
        """
        try:
            # Presence check should be lightweight and independent from UID
            # read/debounce logic. A simple request is enough to know whether
            # a card is in the RF field.
            (status, atqa) = self.reader.request(self.reader.PICC_REQ_IDL)
            if status == self.reader.OK:
                return True

            # Fallback for cards not in IDLE.
            (status, atqa) = self.reader.request(self.reader.PICC_REQ_ALL)
            return status == self.reader.OK

        except Exception:
            return False

    def read_block(self, block_addr, key=None):
        """
        Read a block from card.

        Args:
            block_addr: Block address to read
            key: 6-byte authentication key (uses default if None)

        Returns:
            Tuple of (status, data) where data is 16-byte list or None
        """
        if key is None:
            key = self.reader.key

        # Detect card
        (status, uid) = self.detect_card()
        if status != self.reader.OK:
            return self.reader.ERROR, None

        # Authenticate
        status = self.reader.auth(
            self.reader.PICC_AUTHENT1A,
            block_addr,
            key,
            uid
        )
        if status != self.reader.OK:
            return self.reader.ERROR, None

        # Read block
        data = self.reader.read(block_addr)
        self.reader.stop_crypto1()

        if data is None:
            return self.reader.ERROR, None

        return self.reader.OK, data

    def write_block(self, block_addr, data, key=None):
        """
        Write a block to card.

        Args:
            block_addr: Block address to write
            data: 16-byte list to write
            key: 6-byte authentication key (uses default if None)

        Returns:
            Status code (OK or ERROR)
        """
        if key is None:
            key = self.reader.key

        # Detect card
        (status, uid) = self.detect_card()
        if status != self.reader.OK:
            return self.reader.ERROR

        # Authenticate
        status = self.reader.auth(
            self.reader.PICC_AUTHENT1A,
            block_addr,
            key,
            uid
        )
        if status != self.reader.OK:
            return self.reader.ERROR

        # Write block
        status = self.reader.write(block_addr, data)
        self.reader.stop_crypto1()

        return status

    def stop_crypto(self):
        """Stop crypto authentication."""
        try:
            self.reader.stop_crypto1()
        except Exception:
            pass

    @property
    def is_initialized(self):
        """Check if reader is initialized."""
        return self.reader.initialized
