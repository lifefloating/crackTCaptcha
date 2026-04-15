"""Tests for pow.py — MD5 brute-force PoW solver."""

from __future__ import annotations

import hashlib

import pytest

from crack_tcaptcha.exceptions import PowError
from crack_tcaptcha.pow import solve_pow


def _make_target(prefix: str, nonce: int) -> str:
    return hashlib.md5((prefix + str(nonce)).encode()).hexdigest()


class TestSolvePow:
    def test_known_nonce(self):
        prefix = "test_prefix_"
        nonce = 42
        target = _make_target(prefix, nonce)
        answer, ms = solve_pow(prefix, target)
        assert answer == prefix + str(nonce)
        assert ms >= 0

    def test_nonce_zero(self):
        prefix = "abc"
        target = _make_target(prefix, 0)
        answer, _ = solve_pow(prefix, target)
        assert answer == "abc0"

    def test_nonce_large(self):
        prefix = "x_"
        nonce = 9999
        target = _make_target(prefix, nonce)
        answer, _ = solve_pow(prefix, target)
        assert answer == "x_9999"

    def test_impossible_raises(self):
        with pytest.raises(PowError):
            solve_pow("whatever_", "0" * 32)  # impossible target
