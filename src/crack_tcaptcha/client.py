"""HTTP layer for TCaptcha three-phase protocol.

Responsibilities: prehandle, get_image, verify.  Zero knowledge of images or solvers.

Uses wreq's blocking Client with Chrome TLS/HTTP2 fingerprint emulation to
bypass Tencent's TLS-fingerprint-based bot detection (which returns 403 for
plain httpx/requests/urllib).
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import json
import logging
import re
import urllib.parse
from typing import Any

from wreq import Emulation, Proxy
from wreq.blocking import Client as WreqClient

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

_JSONP_RE = re.compile(r"^\s*\w+\s*\(\s*(.*)\s*\)\s*;?\s*$", re.DOTALL)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSONP helpers
# ---------------------------------------------------------------------------


def parse_jsonp(raw: str) -> dict[str, Any]:
    """Strip JSONP callback wrapper and return the inner dict."""
    m = _JSONP_RE.match(raw)
    body = m.group(1) if m else raw
    return json.loads(body)


def _origin_of(url: str) -> str:
    """Return ``scheme://host[:port]`` for a URL. Empty string if empty."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _resolve_emulation(name: str) -> Emulation:
    """Map a settings string (e.g. "Chrome137") to wreq.Emulation enum.

    Falls back to Chrome137 with a warning if the name is unknown — keeps the
    client usable when settings drift ahead of the installed wreq version.
    """
    candidate = (name or "").strip()
    emu = getattr(Emulation, candidate, None)
    if emu is None:
        log.warning("unknown TCAPTCHA_EMULATION=%r; falling back to Chrome137", name)
        emu = Emulation.Chrome137
    return emu


# ---------------------------------------------------------------------------
# Client class
# ---------------------------------------------------------------------------


class TCaptchaClient:
    """Stateful HTTP facade for the three TCaptcha endpoints.

    Wraps a long-lived ``wreq.blocking.Client`` configured with Chrome
    TLS/HTTP2 fingerprint emulation. Re-use one instance per logical session
    (prehandle → verify) so the underlying connection pool / cookie jar can
    persist; ``close()`` releases the runtime.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
        entry_url: str = "",
    ) -> None:
        ua = user_agent or settings.user_agent
        self._ua = ua
        self._ua_b64 = base64.b64encode(ua.encode()).decode()
        self._timeout = timeout or settings.timeout
        self._proxy = proxy or settings.proxy or None
        # Business page URL that hosts the captcha. Used for Referer/Origin
        # headers in prehandle/verify. 2.0 (TJCaptcha.js) backend cross-checks
        # these against the captcha registration; wrong values → errorCode=12.
        self._entry_url = entry_url

        # User-Agent header we apply to every request. wreq's emulation also
        # sets a UA, but we override per-request to keep the value aligned
        # with `ua` (and with the base64 copy sent in prehandle params).
        self._common_headers: dict[str, str] = {"User-Agent": ua}

        client_kw: dict[str, Any] = {
            "emulation": _resolve_emulation(settings.emulation),
            "user_agent": ua,
            "timeout": datetime.timedelta(seconds=float(self._timeout)),
            "cookie_store": True,
        }
        if self._proxy:
            client_kw["proxies"] = [Proxy.all(url=self._proxy)]
        self._http: WreqClient = WreqClient(**client_kw)

    def close(self) -> None:
        # wreq's blocking Client doesn't always expose .close(); best-effort.
        closer = getattr(self._http, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- prehandle -------------------------------------------------------

    def prehandle(self, aid: str, *, subsid: int = 1, entry_url: str = "") -> PrehandleResp:
        import random

        # Caller may pass per-call entry_url; otherwise use the one bound at
        # construction. Keeping the instance attr lets downstream calls
        # (verify) reuse the same business origin.
        effective_entry = entry_url or self._entry_url
        if entry_url and not self._entry_url:
            self._entry_url = entry_url

        callback = f"_aq_{random.randint(100000, 999999)}"
        # Parameters aligned with real Chrome TJCaptcha.js (2.0) traffic for
        # turing.captcha.qcloud.com. Captured via CDP 2026-04-20 for aid=191743853.
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
            "cap_cd": "",
            "uid": "",
            "lang": "zh-cn",
            "entry_url": effective_entry,
            "elder_captcha": "0",
            "js": "/tgJCap.627c7f42.js",
            "login_appid": "",
            "wb": "1",
            "subsid": str(subsid),
            "callback": callback,
            "sess": "",
        }
        url = f"{settings.base_url}/cap_union_prehandle"
        # Real Chrome sends Referer = entry_url's origin + '/'. Fall back to
        # entry_url itself (or base_url) when no entry_url given.
        referer = effective_entry or settings.base_url
        headers = {**self._common_headers, "Referer": referer}
        try:
            resp = self._http.get(url, query=params, headers=headers)
            status = resp.status.as_int()
            if status != 200:
                raise NetworkError(f"prehandle failed: HTTP {status}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"prehandle failed: {e}") from e

        raw_text = resp.text()
        data = parse_jsonp(raw_text)
        dyn = data["data"]["dyn_show_info"]
        comm = data["data"]["comm_captcha_cfg"]

        # Log key fields for diagnostics
        log.info(
            "prehandle dyn_show_info keys=%s instruction=%r show_type=%s data_type=%s regions=%d fg_elems=%d",
            list(dyn.keys()),
            dyn.get("instruction", ""),
            dyn.get("show_type", ""),
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
        full = img_url if img_url.startswith("http") else f"{settings.base_url}{img_url}"
        headers = {**self._common_headers, "Referer": "https://turing.captcha.gtimg.com/"}
        try:
            resp = self._http.get(full, headers=headers)
            status = resp.status.as_int()
            body = resp.bytes()
            log.info(
                "image download: %s → HTTP %d, %d bytes",
                full[:100],
                status,
                len(body),
            )
            if status != 200:
                raise NetworkError(f"image download failed: HTTP {status}")
            if len(body) == 0:
                raise NetworkError(f"image download returned empty body: {full[:120]}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"image download failed: {e}") from e
        return body

    def get_fg_image_url(self, bg_img_url: str) -> str:
        """Derive the foreground sprite URL from the background URL (img_index=1 → 0)."""
        full = bg_img_url if bg_img_url.startswith("http") else f"{settings.base_url}{bg_img_url}"
        parsed = urllib.parse.urlparse(full)
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
        url = f"{settings.base_url}/cap_union_new_verify"
        # Real Chrome (CDP capture 2026-04-20 aid=196026326 success) sends:
        #   Referer: <entry_url>          (full business page URL)
        #   Origin:  <entry_url_origin>   (scheme://host[:port])
        # Override emulation defaults so the 2.0 backend doesn't reject with
        # errorCode=12 due to a generic Referer.
        verify_headers: dict[str, str] = {**self._common_headers}
        origin = _origin_of(self._entry_url)
        if self._entry_url:
            verify_headers["Referer"] = self._entry_url
        if origin:
            verify_headers["Origin"] = origin
        # Match Chrome's XHR Accept header
        verify_headers.setdefault("Accept", "application/json, text/javascript, */*; q=0.01")
        # wreq's `body=` accepts raw bytes/str but doesn't auto-set Content-Type.
        verify_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        log.info(
            "verify POST: sess=%s... ans=%s pow_answer=%s pow_calc_time=%s collect_len=%d tlg=%s eks_len=%d referer=%s origin=%s",
            sess[:40],
            ans,
            pow_answer[:30],
            str(pow_calc_time),
            len(collect),
            str(tlg),
            len(eks),
            verify_headers.get("Referer", ""),
            verify_headers.get("Origin", ""),
        )
        try:
            resp = self._http.post(url, body=urllib.parse.urlencode(body).encode(), headers=verify_headers)
            status = resp.status.as_int()
            if status != 200:
                raise NetworkError(f"verify failed: HTTP {status}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"verify failed: {e}") from e

        d = resp.json()
        log.info("verify response: %s", json.dumps(d, ensure_ascii=False))
        err_code_raw = d.get("errorCode", -1)
        return VerifyResp(
            ok=(str(err_code_raw) == "0"),
            ticket=d.get("ticket", ""),
            randstr=d.get("randstr", ""),
            error_code=int(err_code_raw) if str(err_code_raw).lstrip("-").isdigit() else -1,
            error_msg=d.get("errMessage", d.get("errMsg", "")),
        )
