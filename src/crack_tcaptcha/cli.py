"""CLI entry point for crack-tcaptcha."""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(prog="crack-tcaptcha", description="TCaptcha automated solver")
    sub = parser.add_subparsers(dest="command")

    solve_p = sub.add_parser("solve", help="Solve a TCaptcha challenge")
    solve_p.add_argument("--type", choices=["slider", "icon_click"], default="slider", help="Challenge type")
    solve_p.add_argument("--appid", required=True, help="TCaptcha APP_ID")
    solve_p.add_argument("--retries", type=int, default=3, help="Max retry attempts")
    solve_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    args = parser.parse_args(argv)

    if args.command != "solve":
        parser.print_help()
        sys.exit(1)

    from crack_tcaptcha import TCaptchaType, solve

    challenge = TCaptchaType.SLIDER if args.type == "slider" else TCaptchaType.ICON_CLICK
    result = solve(appid=args.appid, challenge_type=challenge, max_retries=args.retries)

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
