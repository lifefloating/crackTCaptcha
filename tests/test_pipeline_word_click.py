"""Tests for pipelines/word_click.py (local YOLO + Siamese path)."""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from PIL import Image

from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import (
    BgElemCfg,
    PowConfig,
    PrehandleResp,
    TDCResult,
    VerifyResp,
)
from crack_tcaptcha.pipelines.word_click import (
    _parse_target_chars,
    solve_one_attempt,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParseTargetChars:
    def test_standard_instruction(self):
        assert _parse_target_chars("请依次点击：猫 狗 鱼 ") == ["猫", "狗", "鱼"]

    def test_no_colon_falls_back_to_whole_string(self):
        assert _parse_target_chars("点击 一 二") == ["点", "击", "一", "二"]

    def test_ignores_non_cjk(self):
        assert _parse_target_chars("请依次点击：甲 1 乙 2") == ["甲", "乙"]

    def test_no_chars_returns_empty(self):
        assert _parse_target_chars("请依次点击：abc 123") == []


# ---------------------------------------------------------------------------
# solve_one_attempt
# ---------------------------------------------------------------------------


def _fake_bg_bytes(w: int = 100, h: int = 80) -> bytes:
    arr = np.full((h, w, 3), 200, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


def _make_pre(instruction: str = "请依次点击：甲 乙 ") -> PrehandleResp:
    return PrehandleResp(
        sess="sess_x",
        bg_elem_cfg=BgElemCfg(img_url="/bg?x=1", width=672, height=480),
        fg_elem_list=[],
        pow_cfg=PowConfig(prefix="p_", target_md5="d" * 32),
        tdc_path="/tdc.js",
        instruction=instruction,
    )


def _mock_client_and_tdc() -> tuple[MagicMock, MagicMock]:
    client = MagicMock()
    client.get_image.return_value = _fake_bg_bytes()
    client.verify.return_value = VerifyResp(ok=True, ticket="T", randstr="R")
    tdc = MagicMock()
    tdc.collect = AsyncMock(return_value=TDCResult(collect="c", eks="e", tlg=1))
    return client, tdc


@pytest.fixture()
def stub_pow(monkeypatch):
    monkeypatch.setattr(
        "crack_tcaptcha.pipelines.word_click.solve_pow",
        lambda prefix, md5, min_ms=0, max_ms=0: ("p_42", 3),
    )


@pytest.fixture()
def stub_finish(monkeypatch):
    """Short-circuit finish_with_verify to skip TDC / trajectory plumbing."""

    def fake_finish(client, pre, tdc_provider, *, ans_json, pow_answer, pow_calc_time, trajectory):
        return client.verify(
            ans=ans_json,
            pow_answer=pow_answer,
            pow_calc_time=pow_calc_time,
        )

    monkeypatch.setattr("crack_tcaptcha.pipelines.word_click.finish_with_verify", fake_finish)


class TestSolveOneAttempt:
    def test_raises_when_no_cjk_chars(self, stub_pow):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre(instruction="click: abc 123")
        with pytest.raises(SolveError, match="no CJK chars"):
            solve_one_attempt(client, pre, tdc)

    def test_success_with_siamese_path(self, monkeypatch, stub_pow, stub_finish):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre(instruction="请依次点击：甲 乙 ")

        # Primary path: siamese returns explicit click coords for each target.
        monkeypatch.setattr(
            "crack_tcaptcha.solvers.word_ocr.locate_chars_by_siamese",
            lambda _bg, targets: [(10, 10), (50, 10)],
        )

        resp = solve_one_attempt(client, pre, tdc)
        assert resp.ok

        kwargs = client.verify.call_args.kwargs
        ans = json.loads(kwargs["ans"])
        assert [a["type"] for a in ans] == ["DynAnswerType_POS", "DynAnswerType_POS"]
        assert ans[0]["data"] == "10,10"
        assert ans[1]["data"] == "50,10"
        assert kwargs["pow_answer"] == "p_42"
        assert kwargs["pow_calc_time"] == 3

    def test_falls_back_to_ddddocr_when_siamese_fails(self, monkeypatch, stub_pow, stub_finish):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre(instruction="请依次点击：甲 ")

        # Primary path raises SolveError → pipeline should fall back.
        def raising_siamese(_bg, _targets):
            raise SolveError("siamese unavailable")

        monkeypatch.setattr(
            "crack_tcaptcha.solvers.word_ocr.locate_chars_by_siamese",
            raising_siamese,
        )
        # Fallback path: _fallback_ddddocr imports match_words from _legacy.icon_match.
        monkeypatch.setattr(
            "crack_tcaptcha._legacy.icon_match.match_words",
            lambda _bg, _targets: [(33, 44)],
        )

        resp = solve_one_attempt(client, pre, tdc)
        assert resp.ok

        kwargs = client.verify.call_args.kwargs
        ans = json.loads(kwargs["ans"])
        assert ans == [{"elem_id": 1, "type": "DynAnswerType_POS", "data": "33,44"}]
