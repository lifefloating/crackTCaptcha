"""crack_tcaptcha — Automated TCaptcha solver.

Public API::

    from crack_tcaptcha import solve, TCaptchaType

    result = solve(appid="...", challenge_type=TCaptchaType.SLIDER)
    if result.ok:
        print(result.ticket, result.randstr)
"""

from __future__ import annotations

import os

from crack_tcaptcha.models import SolveResult, TCaptchaType

__all__ = ["solve", "TCaptchaType", "SolveResult"]


def _build_tdc_provider():
    """Select TDC provider: scrapling (default, real browser) or nodejs_jsdom (legacy).

    Controlled by env var ``TCAPTCHA_TDC_PROVIDER`` (``scrapling`` | ``nodejs``).
    Default is ``scrapling`` because jsdom's synthetic environment is detected
    by TCaptcha's behavior/fingerprint checks (errorCode=9).
    """
    choice = os.environ.get("TCAPTCHA_TDC_PROVIDER", "scrapling").lower()
    if choice == "nodejs":
        from crack_tcaptcha.tdc.nodejs_jsdom import NodeJsdomProvider

        return NodeJsdomProvider()
    from crack_tcaptcha.tdc.scrapling_browser import ScraplingBrowserProvider

    return ScraplingBrowserProvider()


def solve(
    appid: str,
    *,
    challenge_type: TCaptchaType = TCaptchaType.SLIDER,
    max_retries: int = 3,
) -> SolveResult:
    """Unified entry point — dispatches to the correct pipeline."""
    tdc = _build_tdc_provider()

    if challenge_type == TCaptchaType.SLIDER:
        from crack_tcaptcha.slider.pipeline import solve_slider

        return solve_slider(appid, tdc_provider=tdc, max_retries=max_retries)

    if challenge_type == TCaptchaType.ICON_CLICK:
        from crack_tcaptcha.icon_click.pipeline import solve_icon_click

        return solve_icon_click(appid, tdc_provider=tdc, max_retries=max_retries)

    return SolveResult(ok=False, error=f"Unknown challenge type: {challenge_type}")
