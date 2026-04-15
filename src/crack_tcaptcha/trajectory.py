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
