# MFRC522 RFID test
#
# Wiring:
# SCK  -> GPIO38
# MOSI -> GPIO37
# MISO -> GPIO36
# RST  -> GPIO21
# CS   -> GPIO2
#
# Waits for an RFID card to be scanned,
# prints the UID, then exits.

from machine import Pin, SPI
from hardware.rfid import RFIDReader
import time

# =========================================================
# SPI Setup
# =========================================================

spi = SPI(
    1, baudrate=1000000, polarity=0, phase=0, sck=Pin(38), mosi=Pin(37), miso=Pin(36)
)

# =========================================================
# RFID Reader Setup
# =========================================================

rfid = RFIDReader(spi=spi, cs_pin=2, rst_pin=21)

# Verify initialization
if not rfid.is_initialized:
    print("RFID initialization failed")
    raise SystemExit

print("RFID reader initialized")
print("Waiting for RFID card...")

# =========================================================
# Wait for Card
# =========================================================

while True:
    uid = rfid.read_card()

    if uid is not None:
        print("Card UID:", uid)
        break

    time.sleep_ms(100)

print("Test complete")
