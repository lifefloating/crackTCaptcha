"""HTTP layer for TCaptcha three-phase protocol.

Responsibilities: prehandle, get_image, verify.  Zero knowledge of images or solvers.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import Any

import httpx

from crack_tcaptcha.exceptions import NetworkError
from crack_tcaptcha.models import (
    BgElemCfg,
    FgElem,
    PowConfig,
    PrehandleResp,
    VerifyResp,
)
from crack_tcaptcha.settings import settings

_BASE = settings.base_url
_JSONP_RE = re.compile(r"^\s*\w+\s*\(\s*(.*)\s*\)\s*;?\s*$", re.DOTALL)


# ---------------------------------------------------------------------------
# JSONP helpers
# ---------------------------------------------------------------------------


def parse_jsonp(raw: str) -> dict[str, Any]:
    """Strip JSONP callback wrapper and return the inner dict."""
    m = _JSONP_RE.match(raw)
    body = m.group(1) if m else raw
    return json.loads(body)


# ---------------------------------------------------------------------------
# Client class
# ---------------------------------------------------------------------------


class TCaptchaClient:
    """Stateless HTTP facade for the three TCaptcha endpoints."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
    ) -> None:
        ua = user_agent or settings.user_agent
        self._ua = ua
        self._ua_b64 = base64.b64encode(ua.encode()).decode()
        self._timeout = timeout or settings.timeout

        transport_kw: dict[str, Any] = {}
        if proxy or settings.proxy:
            transport_kw["proxy"] = proxy or settings.proxy

        self._client = httpx.Client(
            headers={"User-Agent": ua},
            timeout=self._timeout,
            follow_redirects=True,
            **transport_kw,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- prehandle -------------------------------------------------------

    def prehandle(self, aid: str, *, subsid: int = 1, entry_url: str = "") -> PrehandleResp:
        params = {
            "aid": aid,
            "protocol": "https",
            "accver": "1",
            "showtype": "embed",
            "ua": self._ua_b64,
            "noheader": "1",
            "fb": "1",
            "aged": "0",
            "enableDarkMode": "0",
            "graession": "",
            "clientype": "2",
            "cap_cd": "",
            "uid": "",
            "lang": "zh-cn",
            "entry_url": entry_url,
            "elder_captcha": "0",
            "js": "/tcaptcha-frame.5bae14dd.js",
            "login_appid": "",
            "wb": "2",
            "subsid": str(subsid),
            "callback": "_aq_000001",
            "sess": "",
        }
        url = f"{_BASE}/cap_union_prehandle"
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"prehandle failed: {e}") from e

        data = parse_jsonp(resp.text)
        dyn = data["data"]["dyn_show_info"]
        comm = data["data"]["comm_captcha_cfg"]

        fg_list: list[FgElem] = []
        for elem in dyn.get("fg_elem_list", []):
            fg_list.append(
                FgElem(
                    elem_id=elem["elem_id"],
                    sprite_pos=(elem["sprite_pos"]["x"], elem["sprite_pos"]["y"]),
                    size_2d=(elem["size_2d"]["width"], elem["size_2d"]["height"]),
                    init_pos=(elem["init_pos"]["x"], elem["init_pos"]["y"]),
                )
            )

        pow_cfg_raw = comm.get("pow_cfg", {})
        pow_cfg = PowConfig(
            prefix=pow_cfg_raw.get("prefix", ""),
            target_md5=pow_cfg_raw.get("md5", ""),
        )

        return PrehandleResp(
            sess=data.get("sess", ""),
            bg_elem_cfg=BgElemCfg(
                img_url=dyn["bg_elem_cfg"]["img_url"],
                width=dyn["bg_elem_cfg"].get("width", 672),
                height=dyn["bg_elem_cfg"].get("height", 390),
            ),
            fg_elem_list=fg_list,
            pow_cfg=pow_cfg,
            tdc_path=comm.get("tdc_path", ""),
            raw=data,
        )

    # ---- image download --------------------------------------------------

    def get_image(self, img_url: str) -> bytes:
        """Download a captcha image (bg or fg sprite)."""
        full = img_url if img_url.startswith("http") else f"{_BASE}{img_url}"
        try:
            resp = self._client.get(full)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"image download failed: {e}") from e
        return resp.content

    def get_fg_image_url(self, bg_img_url: str) -> str:
        """Derive the foreground sprite URL from the background URL (img_index=1 → 0)."""
        parsed = urllib.parse.urlparse(bg_img_url if bg_img_url.startswith("http") else f"{_BASE}{bg_img_url}")
        qs = urllib.parse.parse_qs(parsed.query)
        qs["img_index"] = ["0"]
        new_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    # ---- verify ----------------------------------------------------------

    def verify(
        self,
        sess: str,
        *,
        ans: str,
        pow_answer: str,
        pow_calc_time: int,
        collect: str,
        tlg: int,
        eks: str,
    ) -> VerifyResp:
        body = {
            "ans": ans,
            "sess": sess,
            "pow_answer": pow_answer,
            "pow_calc_time": str(pow_calc_time),
            "collect": collect,
            "tlg": str(tlg),
            "eks": eks,
        }
        url = f"{_BASE}/cap_union_new_verify"
        try:
            resp = self._client.post(url, data=body)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"verify failed: {e}") from e

        d = resp.json()
        return VerifyResp(
            ok=(d.get("errorCode") == 0),
            ticket=d.get("ticket", ""),
            randstr=d.get("randstr", ""),
            error_code=d.get("errorCode", -1),
            error_msg=d.get("errMsg", ""),
        )
