"""Tests for trajectory.py — ease-in-out cubic & click trajectory generation."""

from __future__ import annotations

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from crack_tcaptcha.trajectory import (
    generate_click_trajectory,
    generate_slide_trajectory,
    merge_trajectories,
)


class TestSlideTrajectory:
    def test_endpoints(self):
        traj = generate_slide_trajectory(10, 100, 300, 100, duration_ms=1000)
        assert traj.points[0].x == 10
        assert traj.points[0].y == 100
        assert traj.points[-1].x == 300
        assert traj.points[-1].y == 100

    def test_total_ms(self):
        traj = generate_slide_trajectory(0, 0, 200, 0, duration_ms=1500)
        assert traj.total_ms == 1500
        assert traj.points[-1].t == 1500

    def test_timestamps_monotonic(self):
        traj = generate_slide_trajectory(0, 0, 500, 0, duration_ms=1000)
        for i in range(1, len(traj.points)):
            assert traj.points[i].t >= traj.points[i - 1].t

    @given(
        sx=st.integers(0, 100),
        sy=st.integers(0, 300),
        ex=st.integers(100, 600),
        ey=st.integers(0, 300),
        dur=st.integers(200, 3000),
    )
    @hyp_settings(max_examples=50)
    def test_hypothesis_start_end(self, sx, sy, ex, ey, dur):
        traj = generate_slide_trajectory(sx, sy, ex, ey, duration_ms=dur)
        assert traj.points[0].x == sx
        assert traj.points[0].y == sy
        assert traj.points[-1].x == ex
        assert traj.points[-1].y == ey
        assert traj.total_ms == dur

    def test_x_roughly_monotonic(self):
        """X should generally increase (allowing ±1 jitter)."""
        traj = generate_slide_trajectory(0, 100, 500, 100, duration_ms=1000)
        for i in range(1, len(traj.points)):
            assert traj.points[i].x >= traj.points[i - 1].x - 2


class TestClickTrajectory:
    def test_endpoints(self):
        traj = generate_click_trajectory(50, 50, 200, 300, duration_ms=400)
        assert traj.points[-1].x == 200
        assert traj.points[-1].y == 300

    def test_has_points(self):
        traj = generate_click_trajectory(0, 0, 100, 100, duration_ms=300)
        assert len(traj.points) >= 2


class TestMergeTrajectories:
    def test_merge_two(self):
        t1 = generate_slide_trajectory(0, 0, 100, 0, duration_ms=500)
        t2 = generate_click_trajectory(100, 0, 200, 100, duration_ms=300)
        merged = merge_trajectories([t1, t2])
        assert merged.points[0].x == 0
        assert merged.points[-1].x == 200
        assert merged.total_ms > 800  # 500 + 300 + pause

    def test_empty(self):
        m = merge_trajectories([])
        assert m.points == []
        assert m.total_ms == 0
