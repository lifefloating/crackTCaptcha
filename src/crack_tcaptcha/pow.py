"""MD5 brute-force PoW solver for TCaptcha."""

from __future__ import annotations

import hashlib
import random
import time

from crack_tcaptcha.exceptions import PowError

_MAX_NONCE = 1_000_000


def solve_pow(
    prefix: str,
    target_md5: str,
    *,
    min_ms: int = 0,
    max_ms: int = 0,
) -> tuple[str, int]:
    """Find *nonce* such that ``md5(prefix + str(nonce)).hexdigest() == target_md5``.

    Args:
        prefix: hex prefix from prehandle `pow_cfg.prefix`
        target_md5: hex digest from prehandle `pow_cfg.md5`
        min_ms: if real compute is faster than this, sleep to reach at least
            this reported calc_time. Real Chrome 2.0 reports 300-500ms;
            Python hashlib solves typical 20-bit prefix in ~200ms, which is
            suspiciously fast. Recommend 300.
        max_ms: optional upper bound; when set with min_ms > 0, final
            reported time is random in [min_ms, max_ms]. 0 means use min_ms.

    Returns:
        (pow_answer, calc_time_ms) — *pow_answer* is ``prefix + str(nonce)``.
    """
    t0 = time.perf_counter()
    for nonce in range(_MAX_NONCE):
        candidate = prefix + str(nonce)
        if hashlib.md5(candidate.encode()).hexdigest() == target_md5:
            calc_ms = int((time.perf_counter() - t0) * 1000)
            if min_ms > 0:
                target = (
                    random.randint(min_ms, max_ms)
                    if max_ms > min_ms
                    else min_ms
                )
                if calc_ms < target:
                    time.sleep((target - calc_ms) / 1000.0)
                    calc_ms = target
            return candidate, calc_ms
    raise PowError(f"PoW not solved within {_MAX_NONCE} iterations")
