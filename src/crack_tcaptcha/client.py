"""HTTP layer for TCaptcha three-phase protocol.

Responsibilities: prehandle, get_image, verify.  Zero knowledge of images or solvers.

Uses scrapling's Fetcher (curl_cffi) with Chrome TLS impersonation to bypass
Tencent's TLS-fingerprint-based bot detection (which returns 403 for plain
httpx/requests/urllib).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
from typing import Any

from scrapling.fetchers import Fetcher

from crack_tcaptcha.exceptions import NetworkError
from crack_tcaptcha.models import (
    BgElemCfg,
    FgElem,
    PowConfig,
    PrehandleResp,
    SelectRegion,
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
    """Stateless HTTP facade for the three TCaptcha endpoints.

    Uses scrapling Fetcher with Chrome TLS fingerprint impersonation.
    """

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
        self._proxy = proxy or settings.proxy or None

        # Common kwargs forwarded to every Fetcher request
        self._fetch_kw: dict[str, Any] = {
            "headers": {"User-Agent": ua},
            "impersonate": "chrome",
            "stealthy_headers": True,
            "follow_redirects": True,
            "timeout": int(self._timeout),
        }
        if self._proxy:
            self._fetch_kw["proxy"] = self._proxy

    def close(self) -> None:
        pass  # Fetcher is stateless — nothing to close

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- prehandle -------------------------------------------------------

    def prehandle(self, aid: str, *, subsid: int = 1, entry_url: str = "") -> PrehandleResp:
        import random

        callback = f"_aq_{random.randint(100000, 999999)}"
        params = {
            "aid": aid,
            "protocol": "https",
            "accver": "1",
            "showtype": "popup",
            "ua": self._ua_b64,
            "noheader": "1",
            "fb": "1",
            "aged": "0",
            "enableAged": "0",
            "enableDarkMode": "0",
            "grayscale": "1",
            "clientype": "2",
            "lang": "zh-cn",
            "entry_url": entry_url,
            "elder_captcha": "0",
            "js": "/tcaptcha-frame.b7f01caa.js",
            "wb": "1",
            "subsid": str(subsid),
            "callback": callback,
            "dyeid": "0",
            "support_media": "jpeg,png,gif,mp4,webm",
            "version": "1.1.0",
        }
        url = f"{_BASE}/cap_union_prehandle"
        try:
            resp = Fetcher.get(url, params=params, **self._fetch_kw)
            if resp.status != 200:
                raise NetworkError(f"prehandle failed: HTTP {resp.status}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"prehandle failed: {e}") from e

        # resp.body is bytes; decode to text for JSONP parsing
        raw_text = resp.body.decode(resp.encoding or "utf-8", errors="replace")
        data = parse_jsonp(raw_text)
        dyn = data["data"]["dyn_show_info"]
        comm = data["data"]["comm_captcha_cfg"]

        # Log key fields for diagnostics
        log = logging.getLogger(__name__)
        log.info(
            "prehandle dyn_show_info keys=%s instruction=%r show_type=%s data_type=%s regions=%d fg_elems=%d",
            list(dyn.keys()), dyn.get("instruction", ""), dyn.get("show_type", ""),
            dyn.get("bg_elem_cfg", {}).get("click_cfg", {}).get("data_type", []),
            len(dyn.get("json_payload", {}).get("select_region_list", []))
            if isinstance(dyn.get("json_payload"), dict)
            else len(json.loads(dyn.get("json_payload", "{}")).get("select_region_list", [])),
            len(dyn.get("fg_elem_list", [])),
        )

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

        # click_image_uncheck fields
        instruction = dyn.get("instruction", "")
        show_type = dyn.get("show_type", "")
        click_cfg = dyn.get("bg_elem_cfg", {}).get("click_cfg", {})
        data_type = click_cfg.get("data_type", [])

        json_payload_raw: dict = {}
        select_regions: list[SelectRegion] = []
        jp_str = dyn.get("json_payload", "")
        if jp_str:
            json_payload_raw = json.loads(jp_str) if isinstance(jp_str, str) else jp_str
            for r in json_payload_raw.get("select_region_list", []):
                rng = r["range"]
                select_regions.append(SelectRegion(id=r["id"], range=(rng[0], rng[1], rng[2], rng[3])))

        # bg size: may come as size_2d array [w, h] (click_image) or dict {width, height} (slider)
        bg_cfg_raw = dyn["bg_elem_cfg"]
        bg_size = bg_cfg_raw.get("size_2d", None)
        if isinstance(bg_size, list):
            bg_w, bg_h = bg_size[0], bg_size[1]
        else:
            bg_w = bg_cfg_raw.get("width", 672)
            bg_h = bg_cfg_raw.get("height", 390)

        return PrehandleResp(
            sess=data.get("sess", ""),
            bg_elem_cfg=BgElemCfg(
                img_url=bg_cfg_raw["img_url"],
                width=bg_w,
                height=bg_h,
            ),
            fg_elem_list=fg_list,
            pow_cfg=pow_cfg,
            tdc_path=comm.get("tdc_path", ""),
            instruction=instruction,
            show_type=show_type,
            data_type=data_type,
            select_regions=select_regions,
            json_payload=json_payload_raw,
            raw=data,
        )

    # ---- image download --------------------------------------------------

    def get_image(self, img_url: str) -> bytes:
        """Download a captcha image (bg or fg sprite)."""
        full = img_url if img_url.startswith("http") else f"{_BASE}{img_url}"
        # Image requests from captcha iframe use captcha.gtimg.com as referer
        img_kw = {**self._fetch_kw, "headers": {**self._fetch_kw["headers"], "Referer": "https://captcha.gtimg.com/"}}
        try:
            resp = Fetcher.get(full, **img_kw)
            if resp.status != 200:
                raise NetworkError(f"image download failed: HTTP {resp.status}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"image download failed: {e}") from e
        return resp.body

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
            resp = Fetcher.post(url, data=body, **self._fetch_kw)
            if resp.status != 200:
                raise NetworkError(f"verify failed: HTTP {resp.status}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"verify failed: {e}") from e

        d = resp.json()
        log = logging.getLogger(__name__)
        log.info("verify response: %s", json.dumps(d, ensure_ascii=False))
        return VerifyResp(
            ok=(d.get("errorCode") == 0),
            ticket=d.get("ticket", ""),
            randstr=d.get("randstr", ""),
            error_code=d.get("errorCode", -1),
            error_msg=d.get("errMsg", ""),
        )
