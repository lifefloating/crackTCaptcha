"""Tests for client.py — mock HTTP with respx."""

from __future__ import annotations

import json

import httpx
import respx

from crack_tcaptcha.client import TCaptchaClient, parse_jsonp

# ---------------------------------------------------------------------------
# parse_jsonp
# ---------------------------------------------------------------------------


class TestParseJsonp:
    def test_standard_callback(self):
        raw = '_aq_000001({"sess":"abc","data":{"x":1}})'
        d = parse_jsonp(raw)
        assert d["sess"] == "abc"
        assert d["data"]["x"] == 1

    def test_with_semicolon(self):
        raw = 'callback({"a":1});'
        assert parse_jsonp(raw)["a"] == 1

    def test_plain_json(self):
        raw = '{"a":2}'
        assert parse_jsonp(raw)["a"] == 2


# ---------------------------------------------------------------------------
# Prehandle
# ---------------------------------------------------------------------------

_PREHANDLE_RESP = {
    "sess": "test_sess_123",
    "data": {
        "dyn_show_info": {
            "bg_elem_cfg": {
                "img_url": "/cap_union_new_getcapbysig?img_index=1&image=abc&sess=s1",
                "width": 672,
                "height": 390,
            },
            "fg_elem_list": [
                {
                    "elem_id": 1,
                    "sprite_pos": {"x": 0, "y": 0},
                    "size_2d": {"width": 110, "height": 110},
                    "init_pos": {"x": 30, "y": 150},
                }
            ],
        },
        "comm_captcha_cfg": {
            "pow_cfg": {"prefix": "test_", "md5": "abc123"},
            "tdc_path": "/tdc.js?v=1",
        },
    },
}


class TestPrehandle:
    @respx.mock
    def test_prehandle_ok(self):
        respx.get("https://turing.captcha.qcloud.com/cap_union_prehandle").mock(
            return_value=httpx.Response(200, text=f"_aq_000001({json.dumps(_PREHANDLE_RESP)})")
        )
        with TCaptchaClient() as c:
            r = c.prehandle("12345")
        assert r.sess == "test_sess_123"
        assert len(r.fg_elem_list) == 1
        assert r.fg_elem_list[0].elem_id == 1
        assert r.pow_cfg.prefix == "test_"
        assert r.tdc_path == "/tdc.js?v=1"


# ---------------------------------------------------------------------------
# get_image
# ---------------------------------------------------------------------------


class TestGetImage:
    @respx.mock
    def test_download(self):
        respx.get("https://turing.captcha.qcloud.com/img?x=1").mock(
            return_value=httpx.Response(200, content=b"\x89PNG_FAKE")
        )
        with TCaptchaClient() as c:
            data = c.get_image("/img?x=1")
        assert data == b"\x89PNG_FAKE"


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
    @respx.mock
    def test_verify_success(self):
        respx.post("https://turing.captcha.qcloud.com/cap_union_new_verify").mock(
            return_value=httpx.Response(200, json={"errorCode": 0, "ticket": "t1", "randstr": "r1"})
        )
        with TCaptchaClient() as c:
            r = c.verify(
                "sess1",
                ans='[{"elem_id":1}]',
                pow_answer="p_42",
                pow_calc_time=3,
                collect="col",
                tlg=1500,
                eks="ek",
            )
        assert r.ok
        assert r.ticket == "t1"

    @respx.mock
    def test_verify_failure(self):
        respx.post("https://turing.captcha.qcloud.com/cap_union_new_verify").mock(
            return_value=httpx.Response(200, json={"errorCode": 15, "errMsg": "bad ans"})
        )
        with TCaptchaClient() as c:
            r = c.verify(
                "sess1",
                ans="[]",
                pow_answer="p_0",
                pow_calc_time=0,
                collect="",
                tlg=0,
                eks="",
            )
        assert not r.ok
        assert r.error_code == 15


# ---------------------------------------------------------------------------
# get_fg_image_url
# ---------------------------------------------------------------------------


class TestFgImageUrl:
    def test_derive(self):
        with TCaptchaClient() as c:
            fg = c.get_fg_image_url("/cap_union_new_getcapbysig?img_index=1&image=abc&sess=s1")
        assert "img_index=0" in fg
        assert "image=abc" in fg
