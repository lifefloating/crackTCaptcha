"""Pydantic v2 models for TCaptcha protocol objects and solve results."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TCaptchaType(str, Enum):
    SLIDER = "slider"
    ICON_CLICK = "icon_click"


# ---------------------------------------------------------------------------
# Protocol: prehandle response
# ---------------------------------------------------------------------------


class FgElem(BaseModel):
    """One foreground element from ``dyn_show_info.fg_elem_list``."""

    elem_id: int
    sprite_pos: tuple[int, int]
    size_2d: tuple[int, int]
    init_pos: tuple[int, int]


class SelectRegion(BaseModel):
    """One clickable region in a ``click_image_uncheck`` grid captcha."""

    id: int
    range: tuple[int, int, int, int]  # (x1, y1, x2, y2)


class PowConfig(BaseModel):
    prefix: str
    target_md5: str


class BgElemCfg(BaseModel):
    img_url: str
    width: int = 672
    height: int = 390


class PrehandleResp(BaseModel):
    sess: str
    bg_elem_cfg: BgElemCfg
    fg_elem_list: list[FgElem] = []
    pow_cfg: PowConfig
    tdc_path: str = ""
    # click_image_uncheck fields
    instruction: str = ""
    show_type: str = ""
    data_type: list[str] = []
    select_regions: list[SelectRegion] = []
    json_payload: dict[str, Any] = {}
    raw: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Verify response
# ---------------------------------------------------------------------------


class VerifyResp(BaseModel):
    ok: bool
    ticket: str = ""
    randstr: str = ""
    error_code: int = 0
    error_msg: str = ""


# ---------------------------------------------------------------------------
# TDC result
# ---------------------------------------------------------------------------


class TDCResult(BaseModel):
    collect: str
    eks: str
    tlg: int = 0


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------


class TrajectoryPoint(BaseModel):
    x: int
    y: int
    t: int  # elapsed ms


class Trajectory(BaseModel):
    points: list[TrajectoryPoint]
    total_ms: int


# ---------------------------------------------------------------------------
# Solve result (public API)
# ---------------------------------------------------------------------------


class SolveResult(BaseModel):
    ok: bool = False
    ticket: str = ""
    randstr: str = ""
    error: str = ""
    attempts: int = 0
