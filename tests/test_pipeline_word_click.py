"""Tests for pipelines/word_click.py."""

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
    _bbox_center,
    _fallback_match_by_ocr,
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


class TestBboxCenter:
    def test_integer_center(self):
        assert _bbox_center((10, 20, 30, 40)) == (20, 30)

    def test_floor_division(self):
        assert _bbox_center((0, 0, 3, 3)) == (1, 1)


# ---------------------------------------------------------------------------
# _fallback_match_by_ocr
# ---------------------------------------------------------------------------


def _fake_bg_bytes(w: int = 100, h: int = 80) -> bytes:
    arr = np.full((h, w, 3), 200, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


class TestFallbackMatchByOcr:
    def test_already_assigned_short_circuits(self, monkeypatch):
        called = {"ocr": False}

        def fake_get_ocr():
            called["ocr"] = True
            return MagicMock()

        monkeypatch.setattr("crack_tcaptcha._legacy.icon_match._get_ocr", fake_get_ocr)

        bboxes = [(0, 0, 10, 10), (20, 0, 30, 10)]
        result = _fallback_match_by_ocr(
            _fake_bg_bytes(),
            bboxes,
            targets=["甲", "乙"],
            already_assigned={"甲": 1, "乙": 2},
        )
        assert result == {"甲": 1, "乙": 2}
        assert called["ocr"] is False

    def test_fills_missing_via_ocr_text(self, monkeypatch):
        # First unused bbox returns "甲甲", second returns noise then "乙"
        ocr = MagicMock()
        ocr.classification.side_effect = ["甲", "乙foo"]
        monkeypatch.setattr("crack_tcaptcha._legacy.icon_match._get_ocr", lambda: ocr)

        bboxes = [(0, 0, 10, 10), (20, 0, 30, 10), (40, 0, 50, 10)]
        result = _fallback_match_by_ocr(
            _fake_bg_bytes(),
            bboxes,
            targets=["甲", "乙"],
            already_assigned={},
        )
        assert result["甲"] == 1
        assert result["乙"] == 2

    def test_last_resort_assigns_unused_bbox(self, monkeypatch):
        # OCR returns nothing useful; char must still map to SOME unused bbox
        ocr = MagicMock()
        ocr.classification.return_value = ""
        monkeypatch.setattr("crack_tcaptcha._legacy.icon_match._get_ocr", lambda: ocr)

        bboxes = [(0, 0, 10, 10), (20, 0, 30, 10)]
        result = _fallback_match_by_ocr(
            _fake_bg_bytes(),
            bboxes,
            targets=["甲"],
            already_assigned={},
        )
        assert 1 <= result["甲"] <= 2


# ---------------------------------------------------------------------------
# solve_one_attempt
# ---------------------------------------------------------------------------


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


class TestSolveOneAttempt:
    def test_raises_when_no_cjk_chars(self, stub_pow):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre(instruction="click: abc 123")
        with pytest.raises(SolveError, match="no CJK chars"):
            solve_one_attempt(client, pre, tdc)

    def test_raises_when_detector_returns_empty(self, monkeypatch, stub_pow):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre()

        monkeypatch.setattr(
            "crack_tcaptcha._legacy.icon_match.detect_icons",
            lambda _bg: [],
        )
        with pytest.raises(SolveError, match="returned 0 bboxes"):
            solve_one_attempt(client, pre, tdc)

    def test_raises_when_ddddocr_missing(self, monkeypatch, stub_pow):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre()

        # Make the lazy import inside solve_one_attempt blow up with ImportError
        import builtins

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "crack_tcaptcha._legacy.icon_match" and fromlist and "detect_icons" in fromlist:
                raise ImportError("no ddddocr")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(SolveError, match="requires ddddocr"):
            solve_one_attempt(client, pre, tdc)

    def test_success_with_llm_path(self, monkeypatch, stub_pow):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre(instruction="请依次点击：甲 乙 ")

        bboxes = [(0, 0, 20, 20), (40, 0, 60, 20)]
        monkeypatch.setattr(
            "crack_tcaptcha._legacy.icon_match.detect_icons",
            lambda _bg: bboxes,
        )
        # Pretend LLM is configured
        monkeypatch.setattr(
            "crack_tcaptcha.pipelines.word_click.settings",
            MagicMock(llm_api_key="k", llm_base_url="u"),
        )
        monkeypatch.setattr(
            "crack_tcaptcha.solvers.llm_vision.locate_chars",
            lambda _bg, targets, bboxes: {"甲": 1, "乙": 2},
        )
        # finish_with_verify's TDC collect goes through; just rubber-stamp
        monkeypatch.setattr(
            "crack_tcaptcha.pipelines._common.resolve_tdc_url", lambda p: p
        )

        resp = solve_one_attempt(client, pre, tdc)
        assert resp.ok
        # verify called with ans JSON describing click center coords
        kwargs = client.verify.call_args.kwargs
        ans = json.loads(kwargs["ans"])
        assert [a["type"] for a in ans] == ["DynAnswerType_POS", "DynAnswerType_POS"]
        # bbox 1 center = (10,10), bbox 2 center = (50,10)
        assert ans[0]["data"] == "10,10"
        assert ans[1]["data"] == "50,10"
        assert kwargs["pow_answer"] == "p_42"
        assert kwargs["pow_calc_time"] == 3

    def test_success_with_llm_absent_uses_ocr_only(self, monkeypatch, stub_pow):
        client, tdc = _mock_client_and_tdc()
        pre = _make_pre(instruction="请依次点击：甲 ")

        monkeypatch.setattr(
            "crack_tcaptcha._legacy.icon_match.detect_icons",
            lambda _bg: [(0, 0, 10, 10), (20, 0, 30, 10)],
        )
        # LLM not configured
        monkeypatch.setattr(
            "crack_tcaptcha.pipelines.word_click.settings",
            MagicMock(llm_api_key="", llm_base_url=""),
        )
        ocr = MagicMock()
        ocr.classification.return_value = "甲"
        monkeypatch.setattr("crack_tcaptcha._legacy.icon_match._get_ocr", lambda: ocr)
        monkeypatch.setattr(
            "crack_tcaptcha.pipelines._common.resolve_tdc_url", lambda p: p
        )

        resp = solve_one_attempt(client, pre, tdc)
        assert resp.ok
