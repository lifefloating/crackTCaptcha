"""Node.js + jsdom subprocess bridge for executing tdc.js."""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib

from crack_tcaptcha.exceptions import TDCError
from crack_tcaptcha.models import TDCResult, Trajectory
from crack_tcaptcha.settings import settings

log = logging.getLogger(__name__)


class NodeJsdomProvider:
    """Execute tdc.js via a Node.js subprocess running ``tdc_executor.js``."""

    def __init__(self, *, js_dir: pathlib.Path | None = None, timeout: float | None = None):
        self._js_dir = js_dir or settings.tdc_js_dir
        self._timeout = timeout or settings.tdc_timeout
        self._script = self._js_dir / "tdc_executor.js"
        self._node = settings.tdc_node_path

    async def collect(self, tdc_url: str, trajectory: Trajectory, ua: str) -> TDCResult:
        payload = json.dumps(
            {
                "tdc_url": tdc_url,
                "ua": ua,
                "trajectory": {
                    "kind": trajectory.kind,
                    "points": [{"x": p.x, "y": p.y, "t": p.t} for p in trajectory.points],
                    "total_ms": trajectory.total_ms,
                },
                "debug": settings.tdc_debug,
            }
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                self._node,
                str(self._script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._js_dir),
            )
        except FileNotFoundError as e:
            raise TDCError(f"node not found: install Node.js and run `npm install` in {self._js_dir}") from e

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload.encode()),
                timeout=self._timeout,
            )
        except TimeoutError:
            proc.kill()
            raise TDCError(f"tdc_executor.js timed out after {self._timeout}s") from None

        stderr_text = stderr.decode(errors="replace")
        if settings.tdc_debug and stderr_text:
            for line in stderr_text.splitlines():
                log.debug("tdc_executor: %s", line)

        if proc.returncode != 0:
            snippet = stderr_text.strip()[-500:]
            raise TDCError(f"tdc_executor.js exited {proc.returncode}: {snippet}")

        stdout_text = stdout.decode(errors="replace")
        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            last = stdout_text.strip().splitlines()[-1] if stdout_text.strip() else ""
            raise TDCError(f"tdc_executor.js returned invalid JSON ({e}); last stdout line: {last[:300]}") from e

        collect = data.get("collect", "") or ""
        eks = data.get("eks", "") or ""
        tokenid = data.get("tokenid", "") or ""
        if not collect:
            raise TDCError("tdc_executor.js returned empty collect")

        log.info(
            "NodeJsdomProvider: kind=%s collect_len=%d eks_len=%d tokenid=%s",
            trajectory.kind,
            len(collect),
            len(eks),
            str(tokenid)[:20],
        )

        return TDCResult(
            collect=collect,
            eks=eks,
            tlg=data.get("tlg", len(collect)),
        )
