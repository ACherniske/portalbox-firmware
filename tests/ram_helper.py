"""Shared numbers and helpers for the RAM tests.

IDF_RESERVE_FREE and IDF_RESERVE_BLOCK must match the values in
firmware/0001-esp32-keep-idf-heap-reserve.patch (ports/esp32/gccollect.c).
"""
import esp32
import gc

# ESP-IDF RAM the firmware keeps from the Python heap: total free, and one
# contiguous block (the WiFi login's TLS needs a ~17 KB buffer in one piece).
IDF_RESERVE_FREE = 65536
IDF_RESERVE_BLOCK = 40960

# Logins worked whenever the largest free block was >= 30 KB and timed out
# at 22.5 KB (the boot failure on the old firmware).
FAIL_BLOCK = 24576


def idf_ram():
    """(free, largest free block) of ESP-IDF's data RAM, measured here so
    the tests don't depend on the code under test."""
    gc.collect()
    rows = esp32.idf_heap_info(esp32.HEAP_DATA)
    return sum(r[1] for r in rows), max(r[2] for r in rows)


def fill(stop_free=0, stop_block=0):
    """Grow the Python heap with 4 KB blocks until MemoryError, or until
    ESP-IDF free RAM drops below stop_free or its largest block below
    stop_block. Returns the blocks: the caller keeps them alive. The heap
    never shrinks again until a reset."""
    ballast = []
    try:
        while True:
            free, largest = idf_ram()
            if free < stop_free or largest < stop_block:
                break
            ballast.append(bytearray(4096))
    except MemoryError:
        pass
    gc.collect()
    return ballast
