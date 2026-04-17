"""Scrapling/Playwright-based TDC executor.

Uses scrapling.DynamicFetcher with a page_action to:
  1. Load tdc.js in a real Chromium (full browser fingerprint: canvas, webgl, audio, fonts, plugins).
  2. Dispatch real low-level mouse events via Playwright page.mouse (captured by TDC's mInit listeners).
  3. Extract collect/eks via window.TDC.getData()/getInfo().

This replaces the Node+jsdom provider whose synthetic environment was detected by
server-side behavior checks (errorCode=9). The real browser's fingerprint passes
the 37 tdc.js cd[] modules naturally.
"""

from __future__ import annotations

import asyncio
import logging
import random

from crack_tcaptcha.exceptions import TDCError
from crack_tcaptcha.models import TDCResult, Trajectory
from crack_tcaptcha.settings import settings

log = logging.getLogger(__name__)

# Landing page that lives on t.captcha.qq.com origin — needed so tdc.js
# loaded via <script src="..."> satisfies same-origin for any internal checks.
# Any URL on this origin works, even if it returns HTML content; we overwrite
# document.body immediately. Using the real iframe template.
_LANDING_URL = "https://t.captcha.qq.com/template/drag_ele.html"


async def _inject_tdc_and_collect(
    page,
    tdc_full_url: str,
    trajectory: Trajectory,
    ua: str,
) -> dict:
    """Page action executed inside a Playwright page (async)."""
    # 1. Inject tdc.js via fetch + eval (gives us same-origin source + any iframe checks see
    #    the parent window; avoids async <script> loading ordering issues).
    await page.evaluate(
        """async (tdcUrl) => {
            const res = await fetch(tdcUrl, { credentials: 'include' });
            if (!res.ok) throw new Error('tdc.js fetch HTTP ' + res.status);
            const src = await res.text();
            // Execute in top-level scope via indirect eval so the script's
            // top-level vars land on window (same as <script>).
            (0, eval)(src);
            // Wait for TDC global + mInit to settle
            let tries = 0;
            while (!window.TDC && tries < 50) {
                await new Promise((r) => setTimeout(r, 100));
                tries += 1;
            }
            if (!window.TDC) {
                const keys = Object.keys(window).filter((k) => /tdc|tencent|chaos/i.test(k));
                throw new Error('TDC global not found. Candidate keys: ' + JSON.stringify(keys));
            }
            return true;
        }""",
        tdc_full_url,
    )

    # 2. Idle mouse moves to simulate "user reading the captcha"
    cx, cy = 640, 360
    for _ in range(random.randint(4, 6)):
        dx = random.randint(-80, 80)
        dy = random.randint(-50, 50)
        await page.mouse.move(cx + dx, cy + dy, steps=random.randint(3, 7))
        await asyncio.sleep(random.uniform(0.08, 0.18))

    # 3. Dispatch trajectory as real mouse events
    prev_t = trajectory.points[0].t if trajectory.points else 0
    for pt in trajectory.points:
        wait = max((pt.t - prev_t) / 1000.0, 0)
        if wait > 0:
            await asyncio.sleep(min(wait, 0.12))
        await page.mouse.move(pt.x, pt.y, steps=1)
        prev_t = pt.t

    # 4. Click at the end of the trajectory
    last = trajectory.points[-1] if trajectory.points else None
    if last is not None:
        await asyncio.sleep(random.uniform(0.08, 0.15))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.04, 0.08))
        await page.mouse.up()

    # 5. Give TDC a moment to process queued events
    await asyncio.sleep(0.25)

    # 6. Extract collect + eks
    data = await page.evaluate(
        """() => {
            try {
                const collect = (typeof window.TDC.getData === 'function') ? window.TDC.getData(true) : '';
                const info = (typeof window.TDC.getInfo === 'function') ? window.TDC.getInfo() : '';
                let eks = '';
                let tokenid = '';
                if (info && typeof info === 'object') {
                    eks = info.info || info.eks || info.data || '';
                    tokenid = info.tokenid || '';
                    if (!eks) eks = JSON.stringify(info);
                } else {
                    eks = String(info || '');
                }
                return {
                    collect: String(collect || ''),
                    eks: String(eks || ''),
                    tokenid: String(tokenid || ''),
                };
            } catch (e) {
                return { collect: '', eks: '', error: String(e) };
            }
        }"""
    )

    return data


class ScraplingBrowserProvider:
    """TDC executor using Scrapling's StealthyFetcher (real Chromium + stealth)."""

    def __init__(self, *, headless: bool = True, timeout_ms: int | None = None):
        self._headless = headless
        self._timeout_ms = int((timeout_ms or settings.tdc_timeout * 1000))

    async def collect(self, tdc_url: str, trajectory: Trajectory, ua: str) -> TDCResult:
        # Lazy import: scrapling[all] isn't imported unless this provider is chosen.
        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError as e:
            raise TDCError(
                f"scrapling[all] not installed (needed for ScraplingBrowserProvider): {e}"
            ) from e

        # Resolve tdc_url to absolute
        if tdc_url.startswith("http"):
            full = tdc_url
        else:
            full = f"https://t.captcha.qq.com{tdc_url}" if tdc_url else ""

        captured: dict = {}

        async def page_action(page):
            try:
                captured.update(
                    await _inject_tdc_and_collect(page, full, trajectory, ua)
                )
            except Exception as e:  # noqa: BLE001
                captured["error"] = str(e)
                log.warning("Scrapling TDC page_action error: %s", e)

        try:
            await StealthyFetcher.async_fetch(
                _LANDING_URL,
                headless=self._headless,
                timeout=self._timeout_ms,
                network_idle=False,
                load_dom=True,
                google_search=True,
                block_webrtc=True,
                allow_webgl=True,
                hide_canvas=False,
                page_action=page_action,
                useragent=ua,
            )
        except Exception as e:  # noqa: BLE001
            raise TDCError(f"Scrapling StealthyFetcher failed: {e}") from e

        if "error" in captured:
            raise TDCError(f"TDC collect inside browser failed: {captured['error']}")

        collect = captured.get("collect", "") or ""
        eks = captured.get("eks", "") or ""
        if not collect:
            raise TDCError("TDC.getData() returned empty collect")

        log.info(
            "ScraplingBrowserProvider: collect_len=%d eks_len=%d tokenid=%s",
            len(collect), len(eks), str(captured.get("tokenid", ""))[:20],
        )

        return TDCResult(collect=collect, eks=eks, tlg=len(collect))
