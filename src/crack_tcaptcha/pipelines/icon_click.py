"""Legacy icon_click pipeline: fg_elem_list-based character click via ddddocr."""

from __future__ import annotations

import io
import json
import logging

from PIL import Image

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import PrehandleResp, VerifyResp
from crack_tcaptcha.pipelines._common import finish_with_verify
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_click_trajectory, merge_trajectories

log = logging.getLogger(__name__)


def solve_one_attempt(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
) -> VerifyResp:
    """Execute one icon_click attempt. Raises SolveError when fg_elem_list missing."""
    if not pre.fg_elem_list:
        raise SolveError("icon_click: prehandle has no fg_elem_list")

    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    fg_url = client.get_fg_image_url(pre.bg_elem_cfg.img_url)
    fg_bytes = client.get_image(fg_url)

    # Crop each hint icon from the fg sprite for matching.
    fg_img = Image.open(io.BytesIO(fg_bytes))
    hint_images: list[bytes] = []
    for elem in pre.fg_elem_list:
        px, py = elem.sprite_pos
        pw, ph = elem.size_2d
        crop = fg_img.crop((px, py, px + pw, py + ph))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        hint_images.append(buf.getvalue())

    # ddddocr-based matcher lives in the legacy solver module. Import lazily
    # so the package still loads when ddddocr is not installed.
    try:
        from crack_tcaptcha._legacy.icon_match import match_icons
    except ImportError as e:
        raise SolveError(
            "icon_click requires ddddocr: `uv sync --extra icon-click`"
        ) from e

    click_coords = match_icons(bg_bytes, hint_images)
    if len(click_coords) != len(pre.fg_elem_list):
        raise SolveError(
            f"icon_click expected {len(pre.fg_elem_list)} matches, got {len(click_coords)}"
        )

    pow_answer, pow_calc_time = solve_pow(
        pre.pow_cfg.prefix, pre.pow_cfg.target_md5, min_ms=300, max_ms=500,
    )

    # Answer format aligned with tx-word reference: elem_id is a 1-based
    # sequence index (not the elem_id from prehandle), type is POS, data
    # is the click center coord string "x,y".
    ans_list = [
        {
            "elem_id": i + 1,
            "type": "DynAnswerType_POS",
            "data": f"{cx},{cy}",
        }
        for i, (cx, cy) in enumerate(click_coords)
    ]
    ans = json.dumps(ans_list)

    traj_segments = []
    prev_x, prev_y = 0, 0
    for cx, cy in click_coords:
        traj_segments.append(generate_click_trajectory(prev_x, prev_y, cx, cy))
        prev_x, prev_y = cx, cy
    combined = merge_trajectories(traj_segments)
    combined = combined.model_copy(update={"kind": "click"})

    return finish_with_verify(
        client, pre, tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=combined,
    )


__all__ = ["solve_one_attempt"]
