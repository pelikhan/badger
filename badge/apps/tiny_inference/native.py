"""Checked buffer boundary for the RP2350's native integer kernels."""

import os
import sys

if (sys.platform != "rp2" or "RP2350" not in os.uname().machine
        or ((getattr(sys.implementation, "_mpy", 0) >> 10) & 15)
        not in (4, 5, 6, 7, 8)):
    raise ImportError("Tiny Inference requires RP2350 ARM firmware")

from uctypes import addressof
import thumb


MAX_DOT = 1024


def dot(left, right, count, left_offset=0, right_offset=0):
    if type(count) is not int or not 0 <= count <= MAX_DOT or count % 4:
        raise ValueError("dot count must be a multiple of four up to 1024")
    for buffer, offset in ((left, left_offset), (right, right_offset)):
        if not isinstance(buffer, bytearray):
            raise TypeError("dot inputs must be bytearrays")
        if type(offset) is not int or offset < 0 or offset % 4:
            raise ValueError("dot offsets must be nonnegative multiples of four")
        if offset + count > len(buffer):
            raise ValueError("dot would read outside its input")
        if (addressof(buffer) + offset) % 4:
            raise ValueError("dot requires word-aligned buffers")
    return thumb.dot_i8(
        addressof(left) + left_offset, addressof(right) + right_offset, count
    )


def requantize(value, shift):
    if type(value) is not int or not -(1 << 30) < value < (1 << 30):
        raise ValueError("requantization accumulator exceeds its safe range")
    if type(shift) is not int or not 0 <= shift <= 30:
        raise ValueError("requantization shift must be in 0..30")
    return thumb.requantize_i8(value, shift)
