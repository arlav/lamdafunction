# SPDX-License-Identifier: MIT OR Apache-2.0
"""PCG32 (PCG-XSH-RR 64/32), written by hand to match ``rng.rs`` bit for bit.

Python integers never overflow, so every multiply and add is masked to 64
bits and every output to 32 bits, which is what Rust's ``wrapping_*`` do.
The test vector ``new(42, 54)`` is in ``tests/fixtures/pcg32.json``.
"""

from __future__ import annotations

MULTIPLIER = 6364136223846793005
"""The 64-bit LCG multiplier from the PCG paper."""

STREAM = 0x4C414D44
"""The stream constant every attempt uses; it spells "LAMD"."""

MASK64 = 0xFFFF_FFFF_FFFF_FFFF
MASK32 = 0xFFFF_FFFF
TWO_POW_32 = 4294967296.0


class Pcg32:
    """One generator; the solver makes one per attempt with :meth:`for_attempt`."""

    __slots__ = ("state", "inc")

    def __init__(self, initstate: int, initseq: int) -> None:
        """Seed like the C reference: ``inc = (initseq << 1) | 1``, step, add the state, step."""
        self.state = 0
        self.inc = ((initseq << 1) | 1) & MASK64
        self.next_u32()
        self.state = (self.state + initstate) & MASK64
        self.next_u32()

    @classmethod
    def for_attempt(cls, seed: int, attempt: int) -> Pcg32:
        """The generator of attempt ``attempt``: ``new(seed + attempt, STREAM)`` with wrapping addition."""
        return cls((seed + attempt) & MASK64, STREAM)

    def next_u32(self) -> int:
        """The next 32-bit output."""
        old = self.state
        self.state = (old * MULTIPLIER + self.inc) & MASK64
        xorshifted = (((old >> 18) ^ old) >> 27) & MASK32
        rot = old >> 59
        # 32-bit rotate right by rot.
        return ((xorshifted >> rot) | (xorshifted << ((32 - rot) & 31))) & MASK32

    def unit_f64(self) -> float:
        """A float in [0, 1): the next output divided by 2^32, exactly as in Rust."""
        return self.next_u32() / TWO_POW_32
