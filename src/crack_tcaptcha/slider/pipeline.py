"""Slider pipeline: prehandle → download → NCC solve → tdc → verify (with retry)."""

from __future__ import annotations

import asyncio
import json
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError, TCaptchaError
from crack_tcaptcha.models import SolveResult, VerifyResp
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.settings import settings
from crack_tcaptcha.slider.solver import SliderSolver
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_slide_trajectory

log = logging.getLogger(__name__)


def _run_async(coro):
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


def solve_slider(
    appid: str,
    *,
    tdc_provider: TDCProvider,
    max_retries: int | None = None,
    client: TCaptchaClient | None = None,
) -> SolveResult:
    """End-to-end slider captcha solver.

    Returns a :class:`SolveResult` with ``ok=True`` and ``ticket``/``randstr`` on success.
    """
    retries = max_retries if max_retries is not None else settings.max_retries
    own_client = client is None
    if own_client:
        client = TCaptchaClient()

    solver = SliderSolver()
    last_error = ""

    try:
        for attempt in range(1, retries + 1):
            try:
                result = _one_attempt(client, solver, tdc_provider, appid, subsid=attempt)
                if result.ok:
                    return SolveResult(
                        ok=True,
                        ticket=result.ticket,
                        randstr=result.randstr,
                        attempts=attempt,
                    )
                last_error = result.error_msg or f"errorCode={result.error_code}"
                log.info("attempt %d failed: %s", attempt, last_error)
            except TCaptchaError as e:
                last_error = str(e)
                log.warning("attempt %d error: %s", attempt, e)
    finally:
        if own_client:
            client.close()

    return SolveResult(ok=False, error=last_error, attempts=retries)


def _one_attempt(
    client: TCaptchaClient,
    solver: SliderSolver,
    tdc_provider: TDCProvider,
    appid: str,
    subsid: int,
) -> VerifyResp:
    # 1. prehandle
    pre = client.prehandle(appid, subsid=subsid)

    # 2. download images
    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    fg_url = client.get_fg_image_url(pre.bg_elem_cfg.img_url)
    fg_bytes = client.get_image(fg_url)

    # 3. NCC solve
    if not pre.fg_elem_list:
        raise SolveError("No fg_elem_list in prehandle response")
    piece = pre.fg_elem_list[0]
    target_x, target_y, ncc = solver.solve(bg_bytes, fg_bytes, piece)
    log.info("NCC solve: target=(%d,%d) ncc=%.4f", target_x, target_y, ncc)

    # 4. PoW
    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    # 5. build ans
    ans = json.dumps(
        [
            {
                "elem_id": piece.elem_id,
                "type": "DynAnswerType_POS",
                "data": f"{target_x},{target_y}",
            }
        ]
    )

    # 6. trajectory + TDC
    init_x, init_y = piece.init_pos
    traj = generate_slide_trajectory(init_x, init_y, target_x, target_y)
    tdc_url = pre.tdc_path
    if not tdc_url.startswith("http"):
        tdc_url = f"https://t.captcha.qcloud.com{tdc_url}" if tdc_url else ""

    tdc_result = _run_async(tdc_provider.collect(tdc_url, traj, settings.user_agent))

    # 7. verify
    return client.verify(
        pre.sess,
        ans=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        collect=tdc_result.collect,
        tlg=tdc_result.tlg or traj.total_ms,
        eks=tdc_result.eks,
    )
