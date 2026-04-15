"""Integration test scaffolding — requires network + real appid.

Run with: uv run pytest tests/integration -m network --appid <APP_ID>
"""

from __future__ import annotations

import pytest


@pytest.mark.network
def test_slider_real(request):
    """Real slider solve — skipped unless --appid is provided."""
    appid = request.config.getoption("--appid", default=None)
    if not appid:
        pytest.skip("--appid not provided")

    from crack_tcaptcha import TCaptchaType, solve

    result = solve(appid=appid, challenge_type=TCaptchaType.SLIDER, max_retries=3)
    assert result.ok, f"Slider solve failed: {result.error}"
    assert result.ticket
    assert result.randstr


@pytest.mark.network
def test_icon_click_real(request):
    """Real icon-click solve — skipped unless --appid is provided."""
    appid = request.config.getoption("--appid", default=None)
    if not appid:
        pytest.skip("--appid not provided")

    from crack_tcaptcha import TCaptchaType, solve

    result = solve(appid=appid, challenge_type=TCaptchaType.ICON_CLICK, max_retries=3)
    assert result.ok, f"Icon-click solve failed: {result.error}"
