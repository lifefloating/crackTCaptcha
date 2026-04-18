"""Shared helpers for all captcha type pipelines.

- run_async: bridge async TDC call from sync pipeline code
- resolve_tdc_url: turn relative tdc_path into absolute URL
- finish_with_verify: TDC collect → verify POST, pipeline-agnostic
"""

from __future__ import annotations

import asyncio
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.models import PrehandleResp, Trajectory, VerifyResp
from crack_tcaptcha.settings import settings
from crack_tcaptcha.tdc.provider import TDCProvider

log = logging.getLogger(__name__)


def run_async(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def resolve_tdc_url(tdc_path: str) -> str:
    """Turn a relative ``/tdc.js?...`` into an absolute URL on the captcha host."""
    if not tdc_path:
        return ""
    if tdc_path.startswith("http"):
        return tdc_path
    return f"{settings.base_url}{tdc_path}"


def finish_with_verify(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
    *,
    ans_json: str,
    pow_answer: str,
    pow_calc_time: int,
    trajectory: Trajectory,
) -> VerifyResp:
    """TDC collect + verify POST. Shared across all pipelines."""
    tdc_url = resolve_tdc_url(pre.tdc_path)
    tdc_result = run_async(tdc_provider.collect(tdc_url, trajectory, settings.user_agent))
    log.debug(
        "TDC collect: collect=%d bytes, eks=%s",
        len(tdc_result.collect), tdc_result.eks[:50],
    )
    return client.verify(
        pre.sess,
        ans=ans_json,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        collect=tdc_result.collect,
        tlg=len(tdc_result.collect),
        eks=tdc_result.eks,
    )
