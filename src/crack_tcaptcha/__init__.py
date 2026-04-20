"""crack_tcaptcha — Automated TCaptcha solver.

Public API::

    from crack_tcaptcha import solve

    result = solve(appid="...")
    if result.ok:
        print(result.ticket, result.randstr)
"""

from __future__ import annotations

from crack_tcaptcha.models import SolveResult

__all__ = ["solve", "SolveResult"]


def _build_tdc_provider():
    """Build the TDC provider. Always Node.js + jsdom (the only supported path)."""
    from crack_tcaptcha.tdc.nodejs_jsdom import NodeJsdomProvider

    return NodeJsdomProvider()


def solve(appid: str, *, max_retries: int | None = None, entry_url: str = "") -> SolveResult:
    """Auto-classify the captcha and route to the matching pipeline."""
    from crack_tcaptcha.pipelines import dispatch

    tdc = _build_tdc_provider()
    return dispatch(
        appid,
        tdc_provider=tdc,
        max_retries=max_retries,
        entry_url=entry_url,
    )
