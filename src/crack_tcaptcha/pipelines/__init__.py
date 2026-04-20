"""Pipeline registry and dispatch. One attempt loop; classifier decides pipeline."""

from __future__ import annotations

import logging
from typing import Callable

from crack_tcaptcha.captcha_type import classify
from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import TCaptchaError, UnsupportedCaptchaType
from crack_tcaptcha.models import PrehandleResp, SolveResult, VerifyResp
from crack_tcaptcha.pipelines import icon_click, image_select, slide, word_click
from crack_tcaptcha.settings import settings
from crack_tcaptcha.tdc.provider import TDCProvider

log = logging.getLogger(__name__)

_SolveFn = Callable[[TCaptchaClient, PrehandleResp, TDCProvider], VerifyResp]

REGISTRY: dict[str, _SolveFn] = {
    "slide": slide.solve_one_attempt,
    "icon_click": icon_click.solve_one_attempt,
    "word_click": word_click.solve_one_attempt,
    "image_select": image_select.solve_one_attempt,
}


def dispatch(
    appid: str,
    *,
    tdc_provider: TDCProvider,
    max_retries: int | None = None,
    client: TCaptchaClient | None = None,
    entry_url: str = "",
) -> SolveResult:
    """Top-level solve: prehandle → classify → run matching pipeline, N retries."""
    retries = max_retries if max_retries is not None else settings.max_retries
    own_client = client is None
    if own_client:
        # Pass entry_url at construction so verify() can use matching
        # Referer/Origin headers. Real Chrome 2.0 backend cross-checks these.
        client = TCaptchaClient(entry_url=entry_url)

    last_error = ""
    try:
        for attempt in range(1, retries + 1):
            try:
                pre = client.prehandle(appid, subsid=1, entry_url=entry_url)
                dyn = pre.raw.get("data", {}).get("dyn_show_info", {})
                cls = classify(dyn)
                log.info(
                    "classified type=%s rule=%s instruction=%r",
                    cls.captcha_type, cls.matched_rule, dyn.get("instruction", ""),
                )
                if cls.captcha_type == "unknown":
                    raise UnsupportedCaptchaType(cls.captcha_type, sorted(dyn.keys()))

                solve_fn = REGISTRY[cls.captcha_type]
                verify_resp = solve_fn(client, pre, tdc_provider)
                if verify_resp.ok:
                    return SolveResult(
                        ok=True,
                        ticket=verify_resp.ticket,
                        randstr=verify_resp.randstr,
                        attempts=attempt,
                    )
                last_error = verify_resp.error_msg or f"errorCode={verify_resp.error_code}"
                log.info("attempt %d failed: %s", attempt, last_error)
            except UnsupportedCaptchaType as e:
                # Unknown type won't change on rerun; abort retry loop.
                return SolveResult(ok=False, error=str(e), attempts=attempt)
            except TCaptchaError as e:
                last_error = str(e)
                log.warning("attempt %d error: %s", attempt, e)
    finally:
        if own_client:
            client.close()

    return SolveResult(ok=False, error=last_error, attempts=retries)


__all__ = ["dispatch", "REGISTRY"]
