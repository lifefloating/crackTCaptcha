"""Tests for Pydantic models."""

from __future__ import annotations

from crack_tcaptcha.models import (
    BgElemCfg,
    FgElem,
    PowConfig,
    PrehandleResp,
    SolveResult,
    TCaptchaType,
    TDCResult,
    Trajectory,
    TrajectoryPoint,
    VerifyResp,
)


class TestModels:
    def test_fg_elem(self):
        e = FgElem(elem_id=1, sprite_pos=(0, 0), size_2d=(110, 110), init_pos=(30, 150))
        assert e.elem_id == 1
        assert e.size_2d == (110, 110)

    def test_pow_config(self):
        p = PowConfig(prefix="abc_", target_md5="d41d8cd98f00b204e9800998ecf8427e")
        assert p.prefix == "abc_"

    def test_prehandle_resp(self):
        r = PrehandleResp(
            sess="s123",
            bg_elem_cfg=BgElemCfg(img_url="/img?index=1"),
            fg_elem_list=[
                FgElem(elem_id=1, sprite_pos=(0, 0), size_2d=(110, 110), init_pos=(30, 150)),
            ],
            pow_cfg=PowConfig(prefix="p_", target_md5="aaa"),
        )
        assert r.sess == "s123"
        assert len(r.fg_elem_list) == 1

    def test_solve_result_default(self):
        r = SolveResult()
        assert not r.ok
        assert r.ticket == ""
        assert r.attempts == 0

    def test_tcaptcha_type(self):
        assert TCaptchaType.SLIDER.value == "slider"
        assert TCaptchaType.ICON_CLICK.value == "icon_click"

    def test_verify_resp(self):
        v = VerifyResp(ok=True, ticket="t1", randstr="r1")
        assert v.ok

    def test_tdc_result(self):
        t = TDCResult(collect="c", eks="e", tlg=1500)
        assert t.tlg == 1500

    def test_trajectory(self):
        pts = [TrajectoryPoint(x=0, y=0, t=0), TrajectoryPoint(x=100, y=0, t=1000)]
        t = Trajectory(points=pts, total_ms=1000)
        assert len(t.points) == 2
