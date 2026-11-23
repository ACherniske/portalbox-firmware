# hardware/drivers/mfrc522.py
# Driver for MFRC522 RFID reader module

import time
from machine import Pin


class MFRC522:
    """
    Driver for MFRC522 RFID reader module.

    Supports reading and writing MIFARE Classic cards using SPI communication.
    """

    # Status codes
    OK = 0
    ERROR = 1
    NO_CARD = 2

    # MFRC522 commands - control the reader's internal operations
    PCD_IDLE = 0x00  # No action, cancels current command execution
    PCD_AUTHENT = 0x0E  # Performs authentication with MIFARE card
    PCD_RECEIVE = 0x08  # Activates the receiver circuits
    PCD_TRANSMIT = 0x04  # Transmits data from FIFO buffer
    PCD_TRANSCEIVE = 0x0C  # Transmits data and activates receiver
    PCD_RESET_PHASE = 0x0F  # Resets the MFRC522 to power-on state
    PCD_CAL_CRC = 0x03  # Activates CRC coprocessor for data integrity

    # MIFARE card commands - sent to RFID cards
    PICC_REQ_IDL = 0x26  # Request command for cards in idle state
    PICC_REQ_ALL = 0x52  # Request command for all cards
    PICC_ANTI_COLL = 0x93  # Anti-collision command
    PICC_SELECT_CARD = 0x93  # Select a specific card after anti-collision
    PICC_AUTHENT1A = 0x60  # Authentication with key A (most common)
    PICC_AUTHENT1B = 0x61  # Authentication with key B (alternative key)
    PICC_READ = 0x30  # Read 16 bytes from a memory block
    PICC_WRITE = 0xA0  # Write 16 bytes to a memory block
    PICC_DECREMENT = 0xC0  # Decrement value in a value block
    PICC_INCREMENT = 0xC1  # Increment value in a value block
    PICC_RESTORE = 0xC2  # Restore value from backup to value block
    PICC_TRANSFER = 0xB0  # Transfer value from internal register to block
    PICC_HALT = 0x50  # Put card into halt state

    # MFRC522 registers - memory addresses for configuring the reader
    COMMAND_REG = 0x01  # Command register
    COMM_INT_EN_REG = 0x02  # Communication interrupt enable
    DIV_INT_EN_REG = 0x03  # Divider interrupt enable
    COMM_INT_RQ_REG = 0x04  # Communication interrupt request
    DIV_INT_RQ_REG = 0x05  # Divider interrupt request
    ERROR_REG = 0x06  # Error register
    STATUS_1_REG = 0x07  # Status register 1
    STATUS_2_REG = 0x08  # Status register 2
    FIFO_DATA_REG = 0x09  # FIFO data register
    FIFO_LEVEL_REG = 0x0A  # FIFO level register
    WATER_LEVEL_REG = 0x0B  # Water level register
    CONTROL_REG = 0x0C  # Control register
    BIT_FRAMING_REG = 0x0D  # Bit framing register
    COLL_REG = 0x0E  # Collision register
    MODE_REG = 0x11  # Mode register
    TX_CONTROL_REG = 0x14  # Transmitter control register
    TX_AUTO_REG = 0x15  # Transmitter automatic control
    CRC_RESULT_REG_MSB = 0x21  # CRC result MSB
    CRC_RESULT_REG_LSB = 0x22  # CRC result LSB
    TIMER_MODE_REG = 0x2A  # Timer mode register
    TIMER_PRESCALER_REG = 0x2B  # Timer prescaler register
    TIMER_RELOAD_REG_H = 0x2C  # Timer reload register high byte
    TIMER_RELOAD_REG_L = 0x2D  # Timer reload register low byte
    TIMER_COUNTER_VALUE_REG_H = 0x2E  # Timer counter value high byte
    TIMER_COUNTER_VALUE_REG_L = 0x2F  # Timer counter value low byte

    def __init__(self, spi, cs_pin, rst_pin):
        """
        Initialize MFRC522 reader.

        Args:
            spi: Initialized SPI bus object
            cs_pin: Chip select pin object or pin number
            rst_pin: Reset pin object or pin number
        """
        # Initialize SPI
        self.spi = spi

        # Initialize pins (accept Pin object or pin number)
        self.cs = cs_pin if isinstance(cs_pin, Pin) else Pin(cs_pin, Pin.OUT)
        self.rst = rst_pin if isinstance(rst_pin, Pin) else Pin(rst_pin, Pin.OUT)

        # Set initial states
        self.cs.value(1)
        self.rst.value(1)

        # Default authentication key
        self.key = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]

        # Initialize card reader
        self._initialized = self._init()

    @property
    def initialized(self):
        """Return True if the reader was successfully initialized."""
        return self._initialized

    def _write_reg(self, reg, value):
        """
        Write a byte to a register.

        Args:
            reg: Register address
            value: Byte value to write
        """
        self.cs.value(0)
        self.spi.write(bytes([reg << 1 & 0x7E]))
        self.spi.write(bytes([value]))
        self.cs.value(1)

    def _read_reg(self, reg):
        """
        Read a byte from a register.

        Args:
            reg: Register address

        Returns:
            Byte value from register
        """
        self.cs.value(0)
        self.spi.write(bytes([((reg << 1) & 0x7E) | 0x80]))
        value = self.spi.read(1)
        self.cs.value(1)
        return value[0]

    def _set_bit(self, reg, mask):
        """
        Set specific bits in register.

        Args:
            reg: Register address
            mask: Bit mask to set
        """
        value = self._read_reg(reg)
        self._write_reg(reg, value | mask)

    def _clear_bit(self, reg, mask):
        """
        Clear specific bits in register.

        Args:
            reg: Register address
            mask: Bit mask to clear
        """
        value = self._read_reg(reg)
        self._write_reg(reg, value & (~mask))

    def _init(self):
        """
        Initialize the MFRC522 reader.

        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Reset the card reader
            self.rst.value(0)
            time.sleep(0.01)
            self.rst.value(1)
            time.sleep(0.01)

            # Soft reset
            self._write_reg(self.COMMAND_REG, self.PCD_RESET_PHASE)
            time.sleep(0.01)

            # Timer settings
            # TimerPrescaler*TimerReloadVal/6.78MHz = 24ms
            # Tauto=1; f(Timer) = 6.78MHz/TPreScaler
            self._write_reg(self.TIMER_MODE_REG, 0x8D)
            # TModeReg[3..0] + TPrescalerReg
            self._write_reg(self.TIMER_PRESCALER_REG, 0x3E)
            self._write_reg(self.TIMER_RELOAD_REG_L, 30)
            self._write_reg(self.TIMER_RELOAD_REG_H, 0)

            # Default 0x00
            self._write_reg(self.TX_AUTO_REG, 0x40)
            # Default 0x3F
            self._write_reg(self.MODE_REG, 0x3D)

            # Turn on antenna
            self.antenna_on()

            return True
        except Exception as e:
            print(f"RFID Reader initialization failed: {e}")
            return False

    def antenna_on(self):
        """Turn on the antenna."""
        temp = self._read_reg(self.TX_CONTROL_REG)
        if not (temp & 0x03):
            self._set_bit(self.TX_CONTROL_REG, 0x03)

    def antenna_off(self):
        """Turn off the antenna."""
        self._clear_bit(self.TX_CONTROL_REG, 0x03)

    def _card_write(self, command, data):
        """
        Write data to card.

        Args:
            command: PCD command to execute
            data: Data to send

        Returns:
            Tuple of (status, back_data, back_bits)
        """
        back_data = []
        back_bits = 0
        status = self.ERROR
        irq_en = 0x00
        wait_irq = 0x00
        n = 0

        if command == self.PCD_AUTHENT:
            irq_en = 0x12
            wait_irq = 0x10

        if command == self.PCD_TRANSCEIVE:
            irq_en = 0x77
            wait_irq = 0x30

        self._write_reg(self.COMM_INT_EN_REG, irq_en | 0x80)
        self._clear_bit(self.COMM_INT_RQ_REG, 0x80)
        self._set_bit(self.FIFO_LEVEL_REG, 0x80)

        self._write_reg(self.COMMAND_REG, self.PCD_IDLE)

        # Write data to FIFO
        for i in range(len(data)):
            self._write_reg(self.FIFO_DATA_REG, data[i])

        # Execute command
        self._write_reg(self.COMMAND_REG, command)
        if command == self.PCD_TRANSCEIVE:
            self._set_bit(self.BIT_FRAMING_REG, 0x80)

        # Wait for command to complete
        i = 2000
        while True:
            n = self._read_reg(self.COMM_INT_RQ_REG)
            i -= 1
            if not ((i != 0) and not (n & 0x01) and not (n & wait_irq)):
                break

        self._clear_bit(self.BIT_FRAMING_REG, 0x80)

        if i != 0:
            if (self._read_reg(self.ERROR_REG) & 0x1B) == 0x00:
                status = self.OK

                if n & irq_en & 0x01:
                    status = self.NO_CARD

                if command == self.PCD_TRANSCEIVE:
                    n = self._read_reg(self.FIFO_LEVEL_REG)
                    last_bits = self._read_reg(self.CONTROL_REG) & 0x07
                    if last_bits != 0:
                        back_bits = (n - 1) * 8 + last_bits
                    else:
                        back_bits = n * 8

                    if n == 0:
                        n = 1
                    elif n > 16:
                        n = 16

                    # Read data from FIFO
                    for i in range(n):
                        back_data.append(self._read_reg(self.FIFO_DATA_REG))
        else:
            status = self.ERROR

        return (status, back_data, back_bits)

    def request(self, req_mode):
        """
        Request a card with the specified mode.

        Args:
            req_mode: Request mode (PICC_REQ_IDL or PICC_REQ_ALL)

        Returns:
            Tuple of (status, back_data)
        """
        self._write_reg(self.BIT_FRAMING_REG, 0x07)

        (status, back_data, back_bits) = self._card_write(
            self.PCD_TRANSCEIVE, [req_mode]
        )

        if (status != self.OK) or (back_bits != 0x10):
            status = self.ERROR

        return (status, back_data)

    def anticoll(self):
        """
        Handle anti-collision for multiple cards.

        Returns:
            Tuple of (status, back_data) where back_data contains UID
        """
        back_data = []
        serial_number = []

        self._write_reg(self.BIT_FRAMING_REG, 0x00)

        serial_number.append(self.PICC_ANTI_COLL)
        serial_number.append(0x20)

        (status, back_data, back_bits) = self._card_write(
            self.PCD_TRANSCEIVE, serial_number
        )

        if status == self.OK:
            if len(back_data) == 5:
                # Check BCC (Block Check Character)
                bcc = 0
                for i in range(4):
                    bcc = bcc ^ back_data[i]
                if bcc != back_data[4]:
                    status = self.ERROR
            else:
                status = self.ERROR

        return (status, back_data)

    def select_card(self, uid):
        """
        Select card.

        Args:
            uid: Card UID from anti-collision

        Returns:
            Card capacity or ERROR
        """
        buf = []
        buf.append(self.PICC_SELECT_CARD)
        buf.append(0x70)

        for i in range(5):
            buf.append(uid[i])

        pout = self._calculate_crc(buf)
        buf.append(pout[0])
        buf.append(pout[1])

        (status, back_data, back_bits) = self._card_write(self.PCD_TRANSCEIVE, buf)

        if (status == self.OK) and (back_bits == 0x18):
            return back_data[0]
        else:
            return self.ERROR

    def auth(self, auth_mode, block_addr, sector_key, serial_num):
        """
        Authenticate with card.

        Args:
            auth_mode: Authentication mode (PICC_AUTHENT1A or PICC_AUTHENT1B)
            block_addr: Block address to authenticate
            sector_key: 6-byte authentication key
            serial_num: Card serial number (first 4 bytes of UID)

        Returns:
            Status code (OK or ERROR)
        """
        buf = []
        buf.append(auth_mode)
        buf.append(block_addr)

        for i in range(len(sector_key)):
            buf.append(sector_key[i])

        for i in range(4):
            buf.append(serial_num[i])

        (status, back_data, back_bits) = self._card_write(self.PCD_AUTHENT, buf)

        if status != self.OK:
            print("Authentication error")
        if not (self._read_reg(self.STATUS_2_REG) & 0x08):
            print("AUTH ERROR(status2reg & 0x08) != 0")

        return status

    def stop_crypto1(self):
        """Stop Crypto1 encryption."""
        self._clear_bit(self.STATUS_2_REG, 0x08)

    def read(self, block_addr):
        """
        Read block data.

        Args:
            block_addr: Block address to read

        Returns:
            16-byte list or None on error
        """
        recv_data = []
        recv_data.append(self.PICC_READ)
        recv_data.append(block_addr)

        pout = self._calculate_crc(recv_data)
        recv_data.append(pout[0])
        recv_data.append(pout[1])

        (status, back_data, back_bits) = self._card_write(
            self.PCD_TRANSCEIVE, recv_data
        )

        if status != self.OK:
            print("Read error")

        if len(back_data) == 16:
            return back_data
        else:
            return None

    def write(self, block_addr, data):
        """
        Write block data.

        Args:
            block_addr: Block address to write
            data: 16-byte list to write

        Returns:
            Status code (OK or ERROR)
        """
        buf = []
        buf.append(self.PICC_WRITE)
        buf.append(block_addr)

        crc = self._calculate_crc(buf)
        buf.append(crc[0])
        buf.append(crc[1])

        (status, back_data, back_bits) = self._card_write(self.PCD_TRANSCEIVE, buf)

        if not (
            (status == self.OK) and (back_bits == 4) and ((back_data[0] & 0x0F) == 0x0A)
        ):
            status = self.ERROR

        if status == self.OK:
            buf = []
            for i in range(16):
                buf.append(data[i])
            crc = self._calculate_crc(buf)
            buf.append(crc[0])
            buf.append(crc[1])

            (status, back_data, back_bits) = self._card_write(self.PCD_TRANSCEIVE, buf)

            if not (
                (status == self.OK)
                and (back_bits == 4)
                and ((back_data[0] & 0x0F) == 0x0A)
            ):
                print("Error writing data")

        return status

    def _calculate_crc(self, data):
        """
        Calculate CRC for data.

        Args:
            data: Data to calculate CRC for

        Returns:
            2-byte CRC list [LSB, MSB]
        """
        self._clear_bit(self.DIV_INT_RQ_REG, 0x04)
        self._set_bit(self.FIFO_LEVEL_REG, 0x80)

        for i in range(len(data)):
            self._write_reg(self.FIFO_DATA_REG, data[i])

        self._write_reg(self.COMMAND_REG, self.PCD_CAL_CRC)

        i = 0xFF
        while True:
            n = self._read_reg(self.DIV_INT_RQ_REG)
            i -= 1
            if not ((i != 0) and not (n & 0x04)):
                break

        ret_data = []
        ret_data.append(self._read_reg(self.CRC_RESULT_REG_LSB))
        ret_data.append(self._read_reg(self.CRC_RESULT_REG_MSB))

        return ret_data
