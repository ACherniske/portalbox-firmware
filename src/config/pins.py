# config/pins.py
# GPIO pin definitions

from micropython import const

# SPI Bus 1 (RFID)
SPI_ID = const(1)
SPI_SCK = const(38)
SPI_MOSI = const(37)
SPI_MISO = const(36)

# RFID Pins
RFID_CS = const(2)
RFID_RST = const(21)

# SPI Bus 2 (Dotstar)
SPI_LED_ID = const(2)
SPI_LED_SCK = const(13)
SPI_LED_MOSI = const(14)
SPI_LED_MISO = const(3) # Not used for DotStar, but defined for completeness
SPI_LED_BAUDRATE = const(1000000)

# Onboard Neopixel (if available)
ONBOARD_NEOPIXEL = None  # GPIO38, but currently used for SCK of SPI1
