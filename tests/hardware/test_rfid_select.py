from tests.test_helper import TestRunner
import hardware.rfid as rfid_mod
from hardware.rfid import RFIDReader

UID5 = [0x23, 0xBE, 0x96, 0x03, 0x00]   # anticoll: 4 UID bytes + check byte


class FakeReader:
    """MFRC522 stand-in: select_card() returns `select` (the card's SAK byte,
    or ERROR when the select fails)."""
    OK = 0
    ERROR = 1
    PICC_REQ_IDL = 0x26
    initialized = True

    def __init__(self, select):
        self.select = select

    def request(self, mode):
        return self.OK, 0x0004

    def anticoll(self):
        return self.OK, list(UID5)

    def select_card(self, uid):
        return self.select


def _reader(select):
    # Build through the constructor with the hardware driver swapped out.
    real = rfid_mod.MFRC522
    rfid_mod.MFRC522 = lambda **kw: FakeReader(select)
    try:
        return RFIDReader(spi=None, cs_pin=0, rst_pin=0)
    finally:
        rfid_mod.MFRC522 = real


def run():
    runner = TestRunner()

    def test_mifare_classic_reads():
        status, uid = _reader(0x08).detect_card()
        assert status == FakeReader.OK, "status %r" % status
        assert uid == UID5[:4], "uid %r" % (uid,)

    def test_sak_zero_reads():
        # Ultralight/NTAG report SAK 0x00 when selected: a good read.
        status, uid = _reader(0x00).detect_card()
        assert status == FakeReader.OK, "SAK 0x00 card rejected"
        assert uid == UID5[:4], "uid %r" % (uid,)

    def test_failed_select_rejected():
        status, uid = _reader(FakeReader.ERROR).detect_card()
        assert status == FakeReader.ERROR, "failed select accepted, uid %r" % (uid,)
        assert uid is None, "uid %r on a failed select" % (uid,)

    def test_seven_byte_card_keeps_level_1_id():
        # SAK 0x04: UID not complete (7-byte card). The ID stays the first
        # 4 bytes of cascade level 1, as the server and the Pi boxes use.
        status, uid = _reader(0x04).detect_card()
        assert status == FakeReader.OK, "status %r" % status
        assert uid == UID5[:4], "uid %r" % (uid,)

    runner.run_test("MIFARE Classic (SAK 0x08) reads", test_mifare_classic_reads)
    runner.run_test("SAK 0x00 card reads", test_sak_zero_reads)
    runner.run_test("Failed select is rejected", test_failed_select_rejected)
    runner.run_test("7-byte card keeps the level-1 ID", test_seven_byte_card_keeps_level_1_id)

    runner.final_report()


if __name__ == "__main__":
    run()
