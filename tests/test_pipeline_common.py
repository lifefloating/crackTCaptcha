"""Tests for pipelines/_common.py: run_async, resolve_tdc_url, finish_with_verify."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from crack_tcaptcha.models import (
    BgElemCfg,
    PowConfig,
    PrehandleResp,
    TDCResult,
    Trajectory,
    TrajectoryPoint,
    VerifyResp,
)
from crack_tcaptcha.pipelines._common import (
    finish_with_verify,
    resolve_tdc_url,
    run_async,
)

# ---------------------------------------------------------------------------
# resolve_tdc_url
# ---------------------------------------------------------------------------


class TestResolveTdcUrl:
    def test_empty_returns_empty(self):
        assert resolve_tdc_url("") == ""

    def test_absolute_url_untouched(self):
        url = "https://other.example.com/tdc.js?v=1"
        assert resolve_tdc_url(url) == url

    def test_relative_url_prepends_base(self):
        from crack_tcaptcha.settings import settings

        out = resolve_tdc_url("/tdc.js?v=1")
        assert out == f"{settings.base_url}/tdc.js?v=1"


# ---------------------------------------------------------------------------
# run_async
# ---------------------------------------------------------------------------


class TestRunAsync:
    def test_returns_coroutine_result_in_sync_context(self):
        async def coro():
            return 42

        assert run_async(coro()) == 42

    def test_works_when_loop_is_running(self):
        """If called from inside a running loop, run_async must not deadlock."""

        async def outer():
            async def inner():
                return "inner-ok"

            # run_async should spin up a worker thread rather than re-entering the loop
            return run_async(inner())

        assert asyncio.run(outer()) == "inner-ok"


# ---------------------------------------------------------------------------
# finish_with_verify
# ---------------------------------------------------------------------------


def _make_pre(tdc_path: str = "/tdc.js?v=1") -> PrehandleResp:
    return PrehandleResp(
        sess="sess_x",
        bg_elem_cfg=BgElemCfg(img_url="/bg?x=1", width=672, height=390),
        fg_elem_list=[],
        pow_cfg=PowConfig(prefix="p_", target_md5="d" * 32),
        tdc_path=tdc_path,
    )


def _make_traj() -> Trajectory:
    return Trajectory(
        points=[TrajectoryPoint(x=1, y=2, t=0), TrajectoryPoint(x=3, y=4, t=10)],
        total_ms=10,
        kind="click",
    )


class TestFinishWithVerify:
    def test_calls_tdc_collect_and_verify_with_expected_args(self):
        pre = _make_pre(tdc_path="/tdc.js?v=1")
        traj = _make_traj()

        client = MagicMock()
        client.verify.return_value = VerifyResp(ok=True, ticket="t", randstr="r")

        tdc_provider = MagicMock()
        tdc_provider.collect = AsyncMock(return_value=TDCResult(collect="COL_DATA", eks="EKS_DATA", tlg=1500))

        resp = finish_with_verify(
            client,
            pre,
            tdc_provider,
            ans_json='[{"elem_id":1}]',
            pow_answer="p_42",
            pow_calc_time=3,
            trajectory=traj,
        )

        assert resp.ok
        assert resp.ticket == "t"

        # TDC collect called once with resolved url + trajectory + UA from settings
        from crack_tcaptcha.settings import settings

        tdc_provider.collect.assert_called_once()
        called_args = tdc_provider.collect.call_args
        assert called_args.args[0] == f"{settings.base_url}/tdc.js?v=1"
        assert called_args.args[1] is traj
        assert called_args.args[2] == settings.user_agent

        # verify() gets the TDC bytes through as collect/eks/tlg
        client.verify.assert_called_once()
        kwargs = client.verify.call_args.kwargs
        assert kwargs["ans"] == '[{"elem_id":1}]'
        assert kwargs["pow_answer"] == "p_42"
        assert kwargs["pow_calc_time"] == 3
        assert kwargs["collect"] == "COL_DATA"
        assert kwargs["eks"] == "EKS_DATA"
        assert kwargs["tlg"] == len("COL_DATA")

    def test_absolute_tdc_path_is_passed_through(self):
        pre = _make_pre(tdc_path="https://cdn.example.com/tdc.js")
        traj = _make_traj()

        client = MagicMock()
        client.verify.return_value = VerifyResp(ok=False, error_code=15, error_msg="x")

        tdc_provider = MagicMock()
        tdc_provider.collect = AsyncMock(return_value=TDCResult(collect="c", eks="e", tlg=1))

        finish_with_verify(
            client,
            pre,
            tdc_provider,
            ans_json="[]",
            pow_answer="p_0",
            pow_calc_time=0,
            trajectory=traj,
        )

        assert tdc_provider.collect.call_args.args[0] == "https://cdn.example.com/tdc.js"
