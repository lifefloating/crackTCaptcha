"""Tests for client.py — HTTP layer mocked via monkeypatch on the wreq client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from crack_tcaptcha.client import TCaptchaClient, parse_jsonp

# ---------------------------------------------------------------------------
# parse_jsonp — pure function tests
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
# Fake wreq response / client used by HTTP-layer tests
# ---------------------------------------------------------------------------


class _FakeStatus:
    def __init__(self, code: int) -> None:
        self._code = code

    def as_int(self) -> int:
        return self._code

    def is_success(self) -> bool:
        return 200 <= self._code < 300


class _FakeResponse:
    def __init__(self, status: int, *, body: bytes = b"", json_data: Any = None) -> None:
        self.status = _FakeStatus(status)
        self._body = body
        self._json = json_data

    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def bytes(self) -> bytes:
        return self._body

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        return json.loads(self._body.decode("utf-8"))


def _patch_http(client: TCaptchaClient, *, get=None, post=None) -> SimpleNamespace:
    """Replace the underlying wreq client with a stub.

    Returns a SimpleNamespace exposing the captured call kwargs so tests can
    assert on Referer/Origin/etc. without touching the real network.
    """
    captured = SimpleNamespace(get_calls=[], post_calls=[])

    def _get(url, **kw):
        captured.get_calls.append((url, kw))
        return get(url, **kw) if callable(get) else get

    def _post(url, **kw):
        captured.post_calls.append((url, kw))
        return post(url, **kw) if callable(post) else post

    client._http = SimpleNamespace(get=_get, post=_post, close=lambda: None)
    return captured


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


# ---------------------------------------------------------------------------
# Prehandle
# ---------------------------------------------------------------------------


class TestPrehandle:
    def test_prehandle_ok(self):
        body = f"_aq_000001({json.dumps(_PREHANDLE_RESP)})".encode()
        with TCaptchaClient() as c:
            cap = _patch_http(c, get=_FakeResponse(200, body=body))
            r = c.prehandle("12345")
        assert r.sess == "test_sess_123"
        assert len(r.fg_elem_list) == 1
        assert r.fg_elem_list[0].elem_id == 1
        assert r.pow_cfg.prefix == "test_"
        assert r.tdc_path == "/tdc.js?v=1"
        # Verify URL + Referer fallback to base_url when no entry_url given
        url, kw = cap.get_calls[0]
        assert url.endswith("/cap_union_prehandle")
        assert "Referer" in kw["headers"]
        assert kw["query"]["aid"] == "12345"

    def test_prehandle_http_error(self):
        with TCaptchaClient() as c:
            _patch_http(c, get=_FakeResponse(500, body=b""))
            with pytest.raises(Exception) as exc_info:
                c.prehandle("12345")
            assert "prehandle failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_image
# ---------------------------------------------------------------------------


class TestGetImage:
    def test_download(self):
        with TCaptchaClient() as c:
            cap = _patch_http(c, get=_FakeResponse(200, body=b"\x89PNG_FAKE"))
            data = c.get_image("/img?x=1")
        assert data == b"\x89PNG_FAKE"
        url, kw = cap.get_calls[0]
        assert url.startswith("https://")
        assert kw["headers"]["Referer"] == "https://turing.captcha.gtimg.com/"

    def test_empty_body_raises(self):
        with TCaptchaClient() as c:
            _patch_http(c, get=_FakeResponse(200, body=b""))
            with pytest.raises(Exception) as exc_info:
                c.get_image("/img?x=1")
            assert "empty body" in str(exc_info.value)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_success(self):
        with TCaptchaClient(entry_url="https://example.com/login") as c:
            cap = _patch_http(
                c,
                post=_FakeResponse(200, json_data={"errorCode": 0, "ticket": "t1", "randstr": "r1"}),
            )
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
        # Referer/Origin must come from entry_url (errorCode=12 protection)
        _, kw = cap.post_calls[0]
        assert kw["headers"]["Referer"] == "https://example.com/login"
        assert kw["headers"]["Origin"] == "https://example.com"
        assert kw["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        # Body must be url-encoded bytes containing our fields
        assert b"ans=" in kw["body"]
        assert b"sess=sess1" in kw["body"]

    def test_verify_failure(self):
        with TCaptchaClient() as c:
            _patch_http(c, post=_FakeResponse(200, json_data={"errorCode": 15, "errMsg": "bad ans"}))
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
