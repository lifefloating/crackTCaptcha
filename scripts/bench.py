"""Benchmark script: run N solves and report pass rate + timing."""

from __future__ import annotations

import argparse
import statistics
import time


def main():
    parser = argparse.ArgumentParser(description="TCaptcha benchmark")
    parser.add_argument("--type", choices=["slider", "icon_click"], default="slider")
    parser.add_argument("--appid", required=True)
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()

    from crack_tcaptcha import solve, TCaptchaType

    challenge = TCaptchaType.SLIDER if args.type == "slider" else TCaptchaType.ICON_CLICK
    ok_count = 0
    times: list[float] = []

    for i in range(1, args.n + 1):
        t0 = time.perf_counter()
        result = solve(appid=args.appid, challenge_type=challenge, max_retries=3)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        status = "OK" if result.ok else f"FAIL({result.error})"
        if result.ok:
            ok_count += 1
        print(f"[{i}/{args.n}] {status}  {elapsed:.2f}s  attempts={result.attempts}")

    rate = ok_count / args.n * 100
    avg = statistics.mean(times)
    print(f"\n--- Results ---")
    print(f"Pass rate: {ok_count}/{args.n} ({rate:.1f}%)")
    print(f"Avg time:  {avg:.2f}s")
    if len(times) > 1:
        print(f"Std dev:   {statistics.stdev(times):.2f}s")


if __name__ == "__main__":
    main()
