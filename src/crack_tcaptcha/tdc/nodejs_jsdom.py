"""Node.js + jsdom subprocess bridge for executing tdc.js."""

from __future__ import annotations

import asyncio
import json
import pathlib

from crack_tcaptcha.exceptions import TDCError
from crack_tcaptcha.models import TDCResult, Trajectory
from crack_tcaptcha.settings import settings


class NodeJsdomProvider:
    """Execute tdc.js via a Node.js subprocess running ``tdc_executor.js``."""

    def __init__(self, *, js_dir: pathlib.Path | None = None, timeout: float | None = None):
        self._js_dir = js_dir or settings.tdc_js_dir
        self._timeout = timeout or settings.tdc_timeout
        self._script = self._js_dir / "tdc_executor.js"

    async def collect(self, tdc_url: str, trajectory: Trajectory, ua: str) -> TDCResult:
        payload = json.dumps(
            {
                "tdc_url": tdc_url,
                "ua": ua,
                "trajectory": [{"x": p.x, "y": p.y, "t": p.t} for p in trajectory.points],
                "total_ms": trajectory.total_ms,
            }
        )

        proc = await asyncio.create_subprocess_exec(
            "node",
            str(self._script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._js_dir),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload.encode()),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise TDCError(f"tdc_executor.js timed out after {self._timeout}s") from None

        if proc.returncode != 0:
            raise TDCError(f"tdc_executor.js exited {proc.returncode}: {stderr.decode()}")

        try:
            data = json.loads(stdout.decode())
        except json.JSONDecodeError as e:
            raise TDCError(f"tdc_executor.js returned invalid JSON: {e}") from e

        return TDCResult(
            collect=data.get("collect", ""),
            eks=data.get("eks", ""),
            tlg=data.get("tlg", 0),
        )
