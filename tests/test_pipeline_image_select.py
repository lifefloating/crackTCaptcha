"""Tests for pipelines/image_select.py."""

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
    SelectRegion,
    TDCResult,
    VerifyResp,
)
from crack_tcaptcha.pipelines.image_select import solve_one_attempt


def _fake_bg_bytes() -> bytes:
    arr = np.full((240, 480, 3), 128, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


def _make_pre(
    *,
    instruction: str = "点击包含猫的图",
    regions: list[SelectRegion] | None = None,
) -> PrehandleResp:
    if regions is None:
        regions = [
            SelectRegion(id=1, range=(0, 0, 160, 120)),
            SelectRegion(id=2, range=(160, 0, 320, 120)),
            SelectRegion(id=3, range=(320, 0, 480, 120)),
        ]
    return PrehandleResp(
        sess="sess_x",
        bg_elem_cfg=BgElemCfg(img_url="/bg?x=1", width=480, height=240),
        fg_elem_list=[],
        pow_cfg=PowConfig(prefix="p_", target_md5="d" * 32),
        tdc_path="/tdc.js",
        instruction=instruction,
        select_regions=regions,
    )


def _make_client() -> MagicMock:
    client = MagicMock()
    client.get_image.return_value = _fake_bg_bytes()
    client.verify.return_value = VerifyResp(ok=True, ticket="T", randstr="R")
    return client


def _make_tdc() -> MagicMock:
    tdc = MagicMock()
    tdc.collect = AsyncMock(return_value=TDCResult(collect="c", eks="e", tlg=1))
    return tdc


@pytest.fixture()
def stub_pow(monkeypatch):
    monkeypatch.setattr(
        "crack_tcaptcha.pipelines.image_select.solve_pow",
        lambda prefix, md5, min_ms=0, max_ms=0: ("p_9", 4),
    )


@pytest.fixture()
def stub_tdc_url(monkeypatch):
    monkeypatch.setattr("crack_tcaptcha.pipelines._common.resolve_tdc_url", lambda p: p)


class TestImageSelectSolve:
    def test_raises_when_no_regions(self):
        pre = _make_pre(regions=[])
        with pytest.raises(SolveError, match="no select_regions"):
            solve_one_attempt(_make_client(), pre, _make_tdc())

    def test_raises_when_no_instruction(self):
        pre = _make_pre(instruction="")
        with pytest.raises(SolveError, match="no instruction"):
            solve_one_attempt(_make_client(), pre, _make_tdc())

    def test_success_builds_uc_answer_and_clicks_region_center(self, monkeypatch, stub_pow, stub_tdc_url):
        pre = _make_pre()
        client = _make_client()
        tdc = _make_tdc()

        # LLM picks region 2 → center = (240, 60)
        monkeypatch.setattr(
            "crack_tcaptcha.pipelines.image_select.match_region",
            lambda _bg, instruction, regions, bg_size: 2,
        )

        resp = solve_one_attempt(client, pre, tdc)
        assert resp.ok

        kwargs = client.verify.call_args.kwargs
        ans = json.loads(kwargs["ans"])
        assert ans == [{"elem_id": "", "type": "DynAnswerType_UC", "data": "2"}]
        assert kwargs["pow_answer"] == "p_9"
        assert kwargs["pow_calc_time"] == 4

    def test_raises_when_match_returns_unknown_id(self, monkeypatch, stub_pow, stub_tdc_url):
        """If LLM returns id not in regions, StopIteration from `next(...)`
        should surface as an error — we assert an exception, not success."""
        pre = _make_pre()
        client = _make_client()
        tdc = _make_tdc()

        monkeypatch.setattr(
            "crack_tcaptcha.pipelines.image_select.match_region",
            lambda _bg, instruction, regions, bg_size: 99,
        )

        with pytest.raises(StopIteration):
            solve_one_attempt(client, pre, tdc)
