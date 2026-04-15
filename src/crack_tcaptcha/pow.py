"""MD5 brute-force PoW solver for TCaptcha."""

from __future__ import annotations

import hashlib
import time

from crack_tcaptcha.exceptions import PowError

_MAX_NONCE = 1_000_000


def solve_pow(prefix: str, target_md5: str) -> tuple[str, int]:
    """Find *nonce* such that ``md5(prefix + str(nonce)).hexdigest() == target_md5``.

    Returns:
        (pow_answer, calc_time_ms) — *pow_answer* is ``prefix + str(nonce)``.
    """
    t0 = time.perf_counter()
    for nonce in range(_MAX_NONCE):
        candidate = prefix + str(nonce)
        if hashlib.md5(candidate.encode()).hexdigest() == target_md5:
            calc_ms = int((time.perf_counter() - t0) * 1000)
            return candidate, calc_ms
    raise PowError(f"PoW not solved within {_MAX_NONCE} iterations")
