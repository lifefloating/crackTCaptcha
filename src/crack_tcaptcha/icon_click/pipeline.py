"""Icon-click pipeline: prehandle → download → detect → tdc → verify (with retry).

Handles both legacy icon-click (fg_elem_list) and the newer click_image_uncheck
(select from 6 grid images matching an instruction keyword).
"""

from __future__ import annotations

import asyncio
import json
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError, TCaptchaError
from crack_tcaptcha.icon_click.solver import select_best_match
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

    # 2. download background image
    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)

    # 3. determine captcha sub-type and solve
    if pre.select_regions and pre.instruction:
        # click_image_uncheck: select the image matching the instruction
        return _solve_click_image(client, tdc_provider, pre, bg_bytes)

    if pre.fg_elem_list:
        # Legacy icon-click with foreground sprite
        return _solve_legacy_icon_click(client, tdc_provider, pre, bg_bytes)

    raise SolveError(
        f"Unsupported captcha format: show_type={pre.show_type}, "
        f"fg_elems={len(pre.fg_elem_list)}, regions={len(pre.select_regions)}"
    )


def _solve_click_image(client, tdc_provider, pre, bg_bytes: bytes) -> VerifyResp:
    """Solve click_image_uncheck: pick the correct image from a grid."""
    log.info("click_image_uncheck: instruction=%s, %d regions", pre.instruction, len(pre.select_regions))

    # Log server-provided data_type for diagnostics
    log.info("server data_type=%s, show_type=%s", pre.data_type, pre.show_type)

    # 3. select best matching region
    best_idx = select_best_match(bg_bytes, pre.select_regions, pre.instruction)
    selected = pre.select_regions[best_idx]
    log.info("Selected region %d (id=%d)", best_idx, selected.id)

    # 4. PoW
    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    # 5. build ans — use the server-specified data_type
    #    For click_image_uncheck with DynAnswerType_UC, data is the region id as string.
    #    For DynAnswerType_POS, data would be "x,y" coordinates.
    x1, y1, x2, y2 = selected.range
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    data_type = pre.data_type[0] if pre.data_type else "DynAnswerType_UC"
    if data_type == "DynAnswerType_UC":
        ans_data = str(selected.id)
    else:
        ans_data = f"{cx},{cy}"
    ans = json.dumps([
        {
            "elem_id": selected.id,
            "type": data_type,
            "data": ans_data,
        }
    ])
    log.info("ans=%s", ans)

    # 6. trajectory: move to the center of the selected region, then click
    traj = generate_click_trajectory(0, 0, cx, cy)

    tdc_url = pre.tdc_path
    if not tdc_url.startswith("http"):
        tdc_url = f"https://t.captcha.qq.com{tdc_url}" if tdc_url else ""

    tdc_result = _run_async(tdc_provider.collect(tdc_url, traj, settings.user_agent))

    # Diagnostic: log TDC output sizes to catch empty/short results
    log.info(
        "TDC result: collect_len=%d, eks_len=%d, tlg=%d",
        len(tdc_result.collect), len(tdc_result.eks), tdc_result.tlg,
    )

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


def _solve_legacy_icon_click(client, tdc_provider, pre, bg_bytes: bytes) -> VerifyResp:
    """Solve legacy icon-click: match foreground hint icons to positions on bg."""
    import io

    from PIL import Image

    from crack_tcaptcha.icon_click.solver import crop_regions  # noqa: F811 (reuse)

    # download fg sprite
    fg_url = client.get_fg_image_url(pre.bg_elem_cfg.img_url)
    fg_bytes = client.get_image(fg_url)

    fg_img = Image.open(io.BytesIO(fg_bytes))
    hint_images: list[bytes] = []
    for elem in pre.fg_elem_list:
        px, py = elem.sprite_pos
        pw, ph = elem.size_2d
        crop = fg_img.crop((px, py, px + pw, py + ph))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        hint_images.append(buf.getvalue())

    # This path requires the legacy match_icons — import dynamically
    # to avoid import errors when ddddocr isn't installed
    try:
        from crack_tcaptcha.icon_click._legacy_solver import match_icons
    except ImportError:
        raise SolveError("Legacy icon-click solver requires ddddocr: pip install ddddocr")

    click_coords = match_icons(bg_bytes, hint_images)
    if len(click_coords) != len(pre.fg_elem_list):
        raise SolveError(f"Expected {len(pre.fg_elem_list)} matches, got {len(click_coords)}")

    # PoW
    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    # build ans
    ans_list = []
    for elem, (cx, cy) in zip(pre.fg_elem_list, click_coords, strict=True):
        ans_list.append({
            "elem_id": elem.elem_id,
            "type": "DynAnswerType_POS",
            "data": f"{cx},{cy}",
        })
    ans = json.dumps(ans_list)

    # trajectory + TDC
    traj_segments = []
    prev_x, prev_y = 0, 0
    for cx, cy in click_coords:
        traj_segments.append(generate_click_trajectory(prev_x, prev_y, cx, cy))
        prev_x, prev_y = cx, cy
    combined = merge_trajectories(traj_segments)

    tdc_url = pre.tdc_path
    if not tdc_url.startswith("http"):
        tdc_url = f"https://t.captcha.qq.com{tdc_url}" if tdc_url else ""

    tdc_result = _run_async(tdc_provider.collect(tdc_url, combined, settings.user_agent))

    return client.verify(
        pre.sess,
        ans=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        collect=tdc_result.collect,
        tlg=tdc_result.tlg or combined.total_ms,
        eks=tdc_result.eks,
    )
