"""Mouse trajectory generators for slider and icon-click challenges."""

from __future__ import annotations

import random

from crack_tcaptcha.models import Trajectory, TrajectoryPoint


def _ease_in_out_cubic(t: float) -> float:
    """Cubic ease-in-out: ``4t³`` for the first half, ``1 - ((-2t+2)³)/2`` for the second."""
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def generate_slide_trajectory(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    duration_ms: int | None = None,
    interval_ms: int = 30,
) -> Trajectory:
    """Generate a human-like slide trajectory with ease-in-out cubic easing.

    - 10%-90% segment: ±1 px random jitter on both axes.
    - Last point snapped to exact target.
    """
    if duration_ms is None:
        duration_ms = random.randint(800, 2000)

    n_points = max(duration_ms // interval_ms, 2)
    dx = end_x - start_x
    dy = end_y - start_y
    points: list[TrajectoryPoint] = []

    for i in range(n_points):
        t = i / (n_points - 1)
        progress = _ease_in_out_cubic(t)
        elapsed = int(t * duration_ms)

        x = start_x + int(dx * progress)
        y = start_y + int(dy * progress)

        # jitter in the 10%-90% window
        if 0.1 < t < 0.9:
            x += random.choice([-1, 0, 1])
            y += random.choice([-1, 0, 1])

        points.append(TrajectoryPoint(x=x, y=y, t=elapsed))

    # ensure the last point is exact
    points[-1] = TrajectoryPoint(x=end_x, y=end_y, t=duration_ms)

    return Trajectory(points=points, total_ms=duration_ms)


def generate_click_trajectory(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    *,
    duration_ms: int | None = None,
    interval_ms: int = 30,
) -> Trajectory:
    """Generate a mouse-move trajectory between two icon-click targets.

    Uses a simple Bézier-like curve with one random control point.
    """
    if duration_ms is None:
        duration_ms = random.randint(200, 600)

    n_points = max(duration_ms // interval_ms, 2)
    # random control point for slight curve
    cx = (from_x + to_x) / 2 + random.randint(-30, 30)
    cy = (from_y + to_y) / 2 + random.randint(-20, 20)
    points: list[TrajectoryPoint] = []

    for i in range(n_points):
        t = i / (n_points - 1)
        elapsed = int(t * duration_ms)
        # quadratic Bézier
        x = int((1 - t) ** 2 * from_x + 2 * (1 - t) * t * cx + t**2 * to_x)
        y = int((1 - t) ** 2 * from_y + 2 * (1 - t) * t * cy + t**2 * to_y)
        points.append(TrajectoryPoint(x=x, y=y, t=elapsed))

    points[-1] = TrajectoryPoint(x=to_x, y=to_y, t=duration_ms)
    return Trajectory(points=points, total_ms=duration_ms)


def merge_trajectories(segments: list[Trajectory], pause_range: tuple[int, int] = (50, 150)) -> Trajectory:
    """Concatenate multiple trajectory segments with random pauses between them."""
    if not segments:
        return Trajectory(points=[], total_ms=0)

    merged: list[TrajectoryPoint] = []
    offset = 0
    for i, seg in enumerate(segments):
        for pt in seg.points:
            merged.append(TrajectoryPoint(x=pt.x, y=pt.y, t=pt.t + offset))
        offset += seg.total_ms
        if i < len(segments) - 1:
            offset += random.randint(*pause_range)

    return Trajectory(points=merged, total_ms=offset)


def _jittered_drift(cx: int, cy: int, *, n: int = 5, spread: int = 80, start_t: int = 0) -> tuple[list[TrajectoryPoint], int]:
    """Pre-click reading drift: n random waypoints near (cx, cy)."""
    pts: list[TrajectoryPoint] = []
    t = start_t
    for _ in range(n):
        dx = random.randint(-spread, spread)
        dy = random.randint(-spread // 2, spread // 2)
        t += random.randint(80, 180)
        pts.append(TrajectoryPoint(x=cx + dx, y=cy + dy, t=t))
    return pts, t


def build_click_trajectory(
    target_x: int,
    target_y: int,
    *,
    canvas_w: int = 672,
    canvas_h: int = 480,
) -> Trajectory:
    """Single-click trajectory: random drift → Bézier approach → target point.

    The last point MUST be (target_x, target_y); event_dispatch.js treats it as the click site.
    """
    # random start on canvas but not too close to target
    sx = random.randint(50, canvas_w - 50)
    sy = random.randint(50, canvas_h - 50)
    drift_center_x = (sx + target_x) // 2
    drift_center_y = (sy + target_y) // 2

    drift_pts, drift_end_t = _jittered_drift(drift_center_x, drift_center_y, n=random.randint(4, 6))

    # approach segment (Bézier)
    approach = generate_click_trajectory(
        drift_pts[-1].x if drift_pts else sx,
        drift_pts[-1].y if drift_pts else sy,
        target_x,
        target_y,
        duration_ms=random.randint(250, 500),
    )
    approach_pts = [TrajectoryPoint(x=p.x, y=p.y, t=p.t + drift_end_t) for p in approach.points]

    all_pts = drift_pts + approach_pts
    if not all_pts or (all_pts[-1].x, all_pts[-1].y) != (target_x, target_y):
        all_pts.append(TrajectoryPoint(x=target_x, y=target_y, t=(all_pts[-1].t + 20) if all_pts else 0))

    return Trajectory(points=all_pts, total_ms=all_pts[-1].t, kind="click")


def build_image_select_trajectory(target_x: int, target_y: int) -> Trajectory:
    """image_select trajectory: same shape as click, kind='multi_click' for JS-side routing."""
    traj = build_click_trajectory(target_x, target_y)
    return Trajectory(points=traj.points, total_ms=traj.total_ms, kind="multi_click")
