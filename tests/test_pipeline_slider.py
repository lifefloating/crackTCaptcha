"""[Skipped post-refactor] solve_slider no longer exists; replaced by pipelines.dispatch."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="solve_slider replaced by pipelines.dispatch in 2026-04-17 refactor")

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import respx

from crack_tcaptcha.models import TDCResult
# from crack_tcaptcha.slider.pipeline import solve_slider  # removed — path deleted in 2026-04-17 refactor


def _mock_prehandle_jsonp(prefix: str = "test_", nonce: int = 5) -> str:
    target_md5 = hashlib.md5(f"{prefix}{nonce}".encode()).hexdigest()
    data = {
        "sess": "mock_sess",
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
                "pow_cfg": {"prefix": prefix, "md5": target_md5},
                "tdc_path": "/tdc.js?v=1",
            },
        },
    }
    return f"_aq_000001({json.dumps(data)})"


def _make_fake_images():
    """Return minimal PNG bytes for bg and fg (1x1 pixel)."""
    import io

    import numpy as np
    from PIL import Image

    bg = np.full((390, 672, 3), 128, dtype=np.uint8)
    fg = np.zeros((620, 682, 4), dtype=np.uint8)
    fg[:110, :110, :3] = 200
    fg[:110, :110, 3] = 255

    buf_bg = io.BytesIO()
    Image.fromarray(bg, "RGB").save(buf_bg, "PNG")
    buf_fg = io.BytesIO()
    Image.fromarray(fg, "RGBA").save(buf_fg, "PNG")
    return buf_bg.getvalue(), buf_fg.getvalue()


class TestSliderPipeline:
    @respx.mock
    def test_success(self):
        bg_bytes, fg_bytes = _make_fake_images()

        # Mock prehandle
        respx.get("https://turing.captcha.qcloud.com/cap_union_prehandle").mock(
            return_value=httpx.Response(200, text=_mock_prehandle_jsonp())
        )
        # Mock bg image
        respx.get(url__regex=r".*img_index=1.*").mock(return_value=httpx.Response(200, content=bg_bytes))
        # Mock fg image
        respx.get(url__regex=r".*img_index=0.*").mock(return_value=httpx.Response(200, content=fg_bytes))
        # Mock verify → success
        respx.post("https://turing.captcha.qcloud.com/cap_union_new_verify").mock(
            return_value=httpx.Response(200, json={"errorCode": 0, "ticket": "t_ok", "randstr": "r_ok"})
        )

        tdc = MagicMock()
        tdc.collect = AsyncMock(return_value=TDCResult(collect="c", eks="e", tlg=1500))

        result = solve_slider("12345", tdc_provider=tdc, max_retries=1)
        assert result.ok
        assert result.ticket == "t_ok"
        assert result.attempts == 1

    @respx.mock
    def test_retry_then_success(self):
        bg_bytes, fg_bytes = _make_fake_images()

        respx.get("https://turing.captcha.qcloud.com/cap_union_prehandle").mock(
            return_value=httpx.Response(200, text=_mock_prehandle_jsonp())
        )
        respx.get(url__regex=r".*img_index=1.*").mock(return_value=httpx.Response(200, content=bg_bytes))
        respx.get(url__regex=r".*img_index=0.*").mock(return_value=httpx.Response(200, content=fg_bytes))
        # First verify fails, second succeeds
        respx.post("https://turing.captcha.qcloud.com/cap_union_new_verify").mock(
            side_effect=[
                httpx.Response(200, json={"errorCode": 15, "errMsg": "bad"}),
                httpx.Response(200, json={"errorCode": 0, "ticket": "t2", "randstr": "r2"}),
            ]
        )

        tdc = MagicMock()
        tdc.collect = AsyncMock(return_value=TDCResult(collect="c", eks="e", tlg=1500))

        result = solve_slider("12345", tdc_provider=tdc, max_retries=2)
        assert result.ok
        assert result.attempts == 2
