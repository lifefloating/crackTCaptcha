"""Tests for pipelines/icon_click.py."""

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
    FgElem,
    PowConfig,
    PrehandleResp,
    TDCResult,
    VerifyResp,
)
from crack_tcaptcha.pipelines.icon_click import solve_one_attempt


def _png_bytes(w: int, h: int, *, mode: str = "RGB", fill=128) -> bytes:
    if mode == "RGBA":
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[..., :3] = fill
        arr[..., 3] = 255
    else:
        arr = np.full((h, w, 3), fill, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode).save(buf, "PNG")
    return buf.getvalue()


def _make_pre(n_elems: int = 2) -> PrehandleResp:
    fg_list = [
        FgElem(
            elem_id=i + 1,
            sprite_pos=(i * 30, 0),
            size_2d=(20, 20),
            init_pos=(0, 0),
        )
        for i in range(n_elems)
    ]
    return PrehandleResp(
        sess="sess_x",
        bg_elem_cfg=BgElemCfg(img_url="/bg?x=1", width=300, height=200),
        fg_elem_list=fg_list,
        pow_cfg=PowConfig(prefix="p_", target_md5="d" * 32),
        tdc_path="/tdc.js",
    )


def _make_client() -> MagicMock:
    client = MagicMock()
    # bg big enough, fg RGBA sprite
    client.get_image.side_effect = [_png_bytes(300, 200), _png_bytes(200, 40, mode="RGBA")]
    client.get_fg_image_url.return_value = "/fg?x=0"
    client.verify.return_value = VerifyResp(ok=True, ticket="T", randstr="R")
    return client


def _make_tdc() -> MagicMock:
    tdc = MagicMock()
    tdc.collect = AsyncMock(return_value=TDCResult(collect="c", eks="e", tlg=1))
    return tdc


@pytest.fixture()
def stub_pow(monkeypatch):
    monkeypatch.setattr(
        "crack_tcaptcha.pipelines.icon_click.solve_pow",
        lambda prefix, md5, min_ms=0, max_ms=0: ("p_7", 2),
    )


@pytest.fixture()
def stub_tdc_url(monkeypatch):
    monkeypatch.setattr(
        "crack_tcaptcha.pipelines._common.resolve_tdc_url", lambda p: p
    )


class TestIconClickSolve:
    def test_raises_when_no_fg_elem_list(self):
        pre = _make_pre(n_elems=0)
        client = _make_client()
        tdc = _make_tdc()
        with pytest.raises(SolveError, match="no fg_elem_list"):
            solve_one_attempt(client, pre, tdc)

    def test_raises_when_ddddocr_missing(self, monkeypatch, stub_pow):
        pre = _make_pre()
        client = _make_client()
        tdc = _make_tdc()

        import builtins

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if (
                name == "crack_tcaptcha._legacy.icon_match"
                and fromlist
                and "match_icons" in fromlist
            ):
                raise ImportError("no ddddocr")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(SolveError, match="requires ddddocr"):
            solve_one_attempt(client, pre, tdc)

    def test_raises_when_match_count_mismatch(self, monkeypatch, stub_pow):
        pre = _make_pre(n_elems=2)
        client = _make_client()
        tdc = _make_tdc()

        monkeypatch.setattr(
            "crack_tcaptcha._legacy.icon_match.match_icons",
            lambda _bg, _hints: [(10, 10)],  # only 1 match, expected 2
        )
        with pytest.raises(SolveError, match="expected 2 matches, got 1"):
            solve_one_attempt(client, pre, tdc)

    def test_success_builds_expected_ans_and_verify_kwargs(
        self, monkeypatch, stub_pow, stub_tdc_url
    ):
        pre = _make_pre(n_elems=2)
        client = _make_client()
        tdc = _make_tdc()

        monkeypatch.setattr(
            "crack_tcaptcha._legacy.icon_match.match_icons",
            lambda _bg, _hints: [(10, 20), (100, 50)],
        )

        resp = solve_one_attempt(client, pre, tdc)
        assert resp.ok

        kwargs = client.verify.call_args.kwargs
        ans = json.loads(kwargs["ans"])
        assert ans == [
            {"elem_id": 1, "type": "DynAnswerType_POS", "data": "10,20"},
            {"elem_id": 2, "type": "DynAnswerType_POS", "data": "100,50"},
        ]
        assert kwargs["pow_answer"] == "p_7"
        assert kwargs["pow_calc_time"] == 2
        assert kwargs["collect"] == "c"
        assert kwargs["eks"] == "e"
