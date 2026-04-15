"""crack_tcaptcha — Automated TCaptcha solver.

Public API::

    from crack_tcaptcha import solve, TCaptchaType

    result = solve(appid="...", challenge_type=TCaptchaType.SLIDER)
    if result.ok:
        print(result.ticket, result.randstr)
"""

from __future__ import annotations

from crack_tcaptcha.models import SolveResult, TCaptchaType
from crack_tcaptcha.tdc.nodejs_jsdom import NodeJsdomProvider

__all__ = ["solve", "TCaptchaType", "SolveResult"]


def solve(
    appid: str,
    *,
    challenge_type: TCaptchaType = TCaptchaType.SLIDER,
    max_retries: int = 3,
) -> SolveResult:
    """Unified entry point — dispatches to the correct pipeline."""
    tdc = NodeJsdomProvider()

    if challenge_type == TCaptchaType.SLIDER:
        from crack_tcaptcha.slider.pipeline import solve_slider

        return solve_slider(appid, tdc_provider=tdc, max_retries=max_retries)

    if challenge_type == TCaptchaType.ICON_CLICK:
        from crack_tcaptcha.icon_click.pipeline import solve_icon_click

        return solve_icon_click(appid, tdc_provider=tdc, max_retries=max_retries)

    return SolveResult(ok=False, error=f"Unknown challenge type: {challenge_type}")
