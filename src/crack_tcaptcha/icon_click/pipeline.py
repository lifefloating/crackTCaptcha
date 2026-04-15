"""Icon-click pipeline: prehandle → download → detect → tdc → verify (with retry)."""

from __future__ import annotations

import asyncio
import json
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError, TCaptchaError
from crack_tcaptcha.icon_click.solver import match_icons
from crack_tcaptcha.models import SolveResult, VerifyResp
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.settings import settings
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_click_trajectory, merge_trajectories

log = logging.getLogger(__name__)


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def solve_icon_click(
    appid: str,
    *,
    tdc_provider: TDCProvider,
    max_retries: int | None = None,
    client: TCaptchaClient | None = None,
) -> SolveResult:
    retries = max_retries if max_retries is not None else settings.max_retries
    own_client = client is None
    if own_client:
        client = TCaptchaClient()

    last_error = ""
    try:
        for attempt in range(1, retries + 1):
            try:
                result = _one_attempt(client, tdc_provider, appid, subsid=attempt)
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
    tdc_provider: TDCProvider,
    appid: str,
    subsid: int,
) -> VerifyResp:
    # 1. prehandle
    pre = client.prehandle(appid, subsid=subsid)

    # 2. download bg
    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)

    # 3. download hint icons from fg sprite
    fg_url = client.get_fg_image_url(pre.bg_elem_cfg.img_url)
    fg_bytes = client.get_image(fg_url)

    import io

    from PIL import Image

    fg_img = Image.open(io.BytesIO(fg_bytes))
    hint_images: list[bytes] = []
    for elem in pre.fg_elem_list:
        px, py = elem.sprite_pos
        pw, ph = elem.size_2d
        crop = fg_img.crop((px, py, px + pw, py + ph))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        hint_images.append(buf.getvalue())

    # 4. match icons
    if not pre.fg_elem_list:
        raise SolveError("No fg_elem_list in prehandle response")
    click_coords = match_icons(bg_bytes, hint_images)
    if len(click_coords) != len(pre.fg_elem_list):
        raise SolveError(f"Expected {len(pre.fg_elem_list)} matches, got {len(click_coords)}")

    # 5. PoW
    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    # 6. build ans
    ans_list = []
    for elem, (cx, cy) in zip(pre.fg_elem_list, click_coords, strict=True):
        ans_list.append(
            {
                "elem_id": elem.elem_id,
                "type": "DynAnswerType_POS",
                "data": f"{cx},{cy}",
            }
        )
    ans = json.dumps(ans_list)

    # 7. trajectory (move between click points) + TDC
    traj_segments = []
    prev_x, prev_y = 0, 0
    for cx, cy in click_coords:
        traj_segments.append(generate_click_trajectory(prev_x, prev_y, cx, cy))
        prev_x, prev_y = cx, cy
    combined = merge_trajectories(traj_segments)

    tdc_url = pre.tdc_path
    if not tdc_url.startswith("http"):
        tdc_url = f"https://t.captcha.qcloud.com{tdc_url}" if tdc_url else ""

    tdc_result = _run_async(tdc_provider.collect(tdc_url, combined, settings.user_agent))

    # 8. verify
    return client.verify(
        pre.sess,
        ans=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        collect=tdc_result.collect,
        tlg=tdc_result.tlg or combined.total_ms,
        eks=tdc_result.eks,
    )
