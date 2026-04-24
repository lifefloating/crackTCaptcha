"""Long-running HTTP server for crack-tcaptcha.

Exposes ``POST /solve`` that wraps :func:`crack_tcaptcha.solve`. Models
load **once** at startup (warmup), so every request pays only the
inference cost — no process cold-start, no ONNX reload. This is the
recommended mode for any non-one-shot use (scripts hammering captchas,
bench, integrations, …).

The server uses stdlib ``http.server`` so we don't need to pull
``fastapi``/``uvicorn`` into the default dependency set.

Endpoints::

    GET  /health           → {"status": "ok", "providers": [...]}
    POST /solve            → request body: {"appid": "...", "retries": 3, "entry_url": ""}
                             → response:    SolveResult model as JSON

Authentication (optional): set ``TCAPTCHA_SERVE_SK`` and every request
must send it in the ``X-SK`` header.

Concurrency: the server spawns a small worker thread pool so independent
solves run in parallel. ONNX sessions are thread-safe when created with
``intra_op_num_threads>=1`` (the default), which we do.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 9991
_DEFAULT_WORKERS = 4


def _warmup_all() -> list[str]:
    """Preload all ONNX sessions and run one dummy inference each.

    Returns a list of human-readable provider strings for /health.
    """
    providers: list[str] = []
    try:
        from crack_tcaptcha.solvers.word_ocr import (
            _get_siamese_session,
            _get_yolo_session,
            warmup,
        )

        warmup()
        yolo = _get_yolo_session()
        siamese = _get_siamese_session()
        providers.append(f"yolo={yolo.get_providers()}")
        providers.append(f"siamese={siamese.get_providers()}")
    except Exception as e:
        log.warning("serve: word_click warmup failed (%s) — fallbacks still work", e)
    return providers


class _State:
    """Shared server state: one executor + cached warmup info."""

    def __init__(self, workers: int, sk: str | None) -> None:
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="solve")
        self.sk = sk
        self.providers: list[str] = []
        self.started_at = time.time()


class _Handler(BaseHTTPRequestHandler):
    # class-level; populated by run()
    state: _State = None  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
        log.info("%s - %s", self.address_string(), format % args)

    # ---- response helpers ------------------------------------------------

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        if not self.state.sk:
            return True
        if self.headers.get("X-SK") == self.state.sk:
            return True
        self._send_json(401, {"status": "error", "msg": "unauthorized"})
        return False

    # ---- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib convention)
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "providers": self.state.providers,
                    "uptime_s": round(time.time() - self.state.started_at, 1),
                },
            )
            return
        self._send_json(404, {"status": "error", "msg": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/solve":
            self._send_json(404, {"status": "error", "msg": "not found"})
            return
        if not self._check_auth():
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as e:
            self._send_json(400, {"status": "error", "msg": f"invalid json: {e}"})
            return

        appid = body.get("appid") or body.get("app_id")
        if not appid:
            self._send_json(400, {"status": "error", "msg": "missing appid"})
            return
        retries = int(body.get("retries", body.get("max_retries", 3)))
        entry_url = body.get("entry_url", "")

        from crack_tcaptcha import solve

        # Run in the executor so concurrent /solve requests don't block each
        # other. The HTTP server is already ThreadingHTTPServer, so this is
        # actually just enforcing a bounded concurrency.
        fut = self.state.executor.submit(
            solve, appid=str(appid), max_retries=retries, entry_url=entry_url
        )
        t0 = time.time()
        try:
            result = fut.result()
        except Exception as e:  # pragma: no cover - defensive
            log.exception("solve crashed")
            self._send_json(500, {"status": "error", "msg": str(e)})
            return
        cost = round(time.time() - t0, 3)
        payload = result.model_dump()
        payload["_cost_s"] = cost
        self._send_json(200, payload)


def run(host: str, port: int, workers: int, sk: str | None) -> None:
    """Start the server. Blocks until Ctrl-C."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("crack-tcaptcha serve: warming up models...")
    providers = _warmup_all()
    for p in providers:
        log.info("  %s", p)

    state = _State(workers=workers, sk=sk)
    state.providers = providers
    _Handler.state = state

    server = ThreadingHTTPServer((host, port), _Handler)
    log.info("listening on http://%s:%d (workers=%d, auth=%s)", host, port, workers, "on" if sk else "off")

    stop_evt = threading.Event()

    def _shutdown(*_: Any) -> None:
        log.info("shutting down...")
        stop_evt.set()
        # serve_forever blocks in the main thread; use shutdown() from another
        # thread to break it out.
        threading.Thread(target=server.shutdown, daemon=True).start()

    import signal

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever()
    finally:
        state.executor.shutdown(wait=True, cancel_futures=True)
        server.server_close()
        log.info("stopped")


def main(argv: list[str] | None = None) -> None:
    """Entry point registered as `crack-tcaptcha serve` subcommand."""
    import argparse

    parser = argparse.ArgumentParser(prog="crack-tcaptcha serve", description="Long-running solver HTTP service")
    parser.add_argument("--host", default=os.environ.get("TCAPTCHA_SERVE_HOST", _DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TCAPTCHA_SERVE_PORT", _DEFAULT_PORT)))
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("TCAPTCHA_SERVE_WORKERS", _DEFAULT_WORKERS)),
        help="Max concurrent solves",
    )
    args = parser.parse_args(argv)

    sk = os.environ.get("TCAPTCHA_SERVE_SK") or None
    run(host=args.host, port=args.port, workers=args.workers, sk=sk)


if __name__ == "__main__":
    main()
