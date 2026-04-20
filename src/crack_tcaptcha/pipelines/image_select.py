"""image_select (click_image_uncheck) pipeline: LLM picks one of 6 regions."""

from __future__ import annotations

import json
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import PrehandleResp, VerifyResp
from crack_tcaptcha.pipelines._common import finish_with_verify
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.solvers.llm_vision import match_region
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import build_image_select_trajectory

log = logging.getLogger(__name__)


def solve_one_attempt(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
) -> VerifyResp:
    """Execute one image_select attempt."""
    if not pre.select_regions:
        raise SolveError("image_select: prehandle has no select_regions")
    if not pre.instruction:
        raise SolveError("image_select: prehandle has no instruction")

    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    log.info(
        "image_select: instruction=%r, %d regions, bg=%d bytes",
        pre.instruction,
        len(pre.select_regions),
        len(bg_bytes),
    )

    region_id = match_region(
        bg_bytes,
        instruction=pre.instruction,
        regions=pre.select_regions,
        bg_size=(pre.bg_elem_cfg.width, pre.bg_elem_cfg.height),
    )

    ans = json.dumps([{"elem_id": "", "type": "DynAnswerType_UC", "data": str(region_id)}])

    pow_answer, pow_calc_time = solve_pow(
        pre.pow_cfg.prefix,
        pre.pow_cfg.target_md5,
        min_ms=300,
        max_ms=500,
    )

    selected = next(r for r in pre.select_regions if r.id == region_id)
    x1, y1, x2, y2 = selected.range
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    traj = build_image_select_trajectory(cx, cy)
    log.info("image_select click center=(%d,%d) for region %d", cx, cy, region_id)

    return finish_with_verify(
        client,
        pre,
        tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=traj,
    )


__all__ = ["solve_one_attempt"]
