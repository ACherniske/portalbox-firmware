# config/pins.py
# GPIO pin definitions

from micropython import const

# Relay Control
AC_RELAY = const(18)
USB_RELAY = const(9)

# Buzzer
BUZZER = const(4)

# Button
BUTTON = const(1)

# I2C Bus
I2C_ID = const(0)
I2C_SCL = const(48)
I2C_SDA = const(47)
I2C_FREQ = const(400000)

# SPI Bus 1 (RFID, Display)
SPI_ID = const(1)
SPI_SCK = const(38)
SPI_MOSI = const(37)
SPI_MISO = const(36)
SPI_BAUDRATE = const(1000000)

RFID_CS = const(2)
RFID_RST = const(21)

SPI_DISPLAY_CS = const(7)
SPI_DISPLAY_DC = const(11)
SPI_DISPLAY_RST = const(12)

# SPI Bus 2 (Dotstar, Neopixel)
SPI_LED_ID = const(2)
SPI_LED_SCK = const(13)
SPI_LED_MOSI = const(14)
SPI_LED_MISO = const(9)
SPI_LED_BAUDRATE = const(1000000)
