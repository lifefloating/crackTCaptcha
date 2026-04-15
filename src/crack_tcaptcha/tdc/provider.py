"""TDCProvider protocol — dependency injection point for pipeline."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crack_tcaptcha.models import TDCResult, Trajectory


@runtime_checkable
class TDCProvider(Protocol):
    """Anything that can execute tdc.js and return collect/eks/tlg."""

    async def collect(self, tdc_url: str, trajectory: Trajectory, ua: str) -> TDCResult: ...
