"""CLI entry point for crack-tcaptcha."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading


def _warmup_word_click() -> None:
    """Best-effort: preload YOLO + Siamese sessions in background.

    Runs concurrently with the first prehandle HTTP request so the model
    load latency is hidden behind network wait time.
    """
    try:
        from crack_tcaptcha.solvers.word_ocr import warmup

        warmup()
    except Exception:  # word-click extra not installed — that's fine
        pass


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(prog="crack-tcaptcha", description="TCaptcha automated solver")
    sub = parser.add_subparsers(dest="command")

    solve_p = sub.add_parser("solve", help="Solve a TCaptcha challenge (one-shot)")
    solve_p.add_argument("--appid", required=True, help="TCaptcha APP_ID")
    solve_p.add_argument("--retries", type=int, default=3, help="Max retry attempts")
    solve_p.add_argument("--entry-url", default="", help="Parent page URL (optional)")
    solve_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")
    solve_p.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip background ONNX model warmup (useful for benchmarking cold-start)",
    )

    serve_p = sub.add_parser(
        "serve",
        help="Run a long-lived HTTP server (models load once; best for repeated use)",
    )
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=9991)
    serve_p.add_argument("--workers", type=int, default=4, help="Max concurrent solves")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import os as _os

        from crack_tcaptcha.server import run as serve_run

        serve_run(
            host=args.host,
            port=args.port,
            workers=args.workers,
            sk=_os.environ.get("TCAPTCHA_SERVE_SK") or None,
        )
        return

    if args.command != "solve":
        parser.print_help()
        sys.exit(1)

    # Kick off model warmup in a daemon thread so it overlaps with the
    # first HTTP round-trip.
    if not args.no_warmup:
        threading.Thread(target=_warmup_word_click, name="word_click-warmup", daemon=True).start()

    from crack_tcaptcha import solve

    result = solve(appid=args.appid, max_retries=args.retries, entry_url=args.entry_url)

    if args.as_json:
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    else:
        if result.ok:
            print(f"OK  ticket={result.ticket}  randstr={result.randstr}  attempts={result.attempts}")
        else:
            print(f"FAIL  error={result.error}  attempts={result.attempts}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
