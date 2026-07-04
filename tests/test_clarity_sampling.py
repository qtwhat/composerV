"""Tests for content-aware frame selection (clarity layer)."""

import os
import shutil

import pytest

from composerv.clarity.sampling import (
    choose_by_cumulative_motion,
    choose_frame_times,
    select_keyframes,
)


def test_choose_uses_scene_times_within_bounds():
    out = choose_frame_times([2.0, 5.0, 8.0], duration=10.0, max_frames=12, min_frames=4)
    assert {0.0, 2.0, 5.0, 8.0} <= set(out)        # opening + scene changes kept
    assert all(0 <= t < 10.0 for t in out)
    assert out == sorted(out)


def test_choose_fills_to_budget_not_just_floor():
    # the bug the experiment exposed: max_frames=8 must yield ~8 frames, not min_frames(4)
    out = choose_frame_times([], duration=32.0, max_frames=8, min_frames=4)
    assert len(out) == 8                             # uses the whole budget
    assert out[0] == 0.0 and out == sorted(out)


def test_choose_caps_when_too_many_keeps_opening_and_spread():
    out = choose_frame_times([float(i) for i in range(50)], duration=50.0, max_frames=8, min_frames=4)
    assert len(out) <= 8
    assert out[0] == 0.0 and out == sorted(out)


def test_choose_drops_out_of_range_and_dupes():
    out = choose_frame_times([2.0, 2.0, -1.0, 99.0], duration=10.0, max_frames=12, min_frames=2)
    assert out.count(2.0) == 1
    assert all(0 <= t < 10.0 for t in out)


def test_choose_zero_duration():
    assert choose_frame_times([], duration=0.0) == [0.0]


def test_motion_clusters_frames_where_the_motion_is():
    # nothing moves in the first half, lots moves in the second half
    profile = [(float(t), 0.0) for t in range(5)] + [(float(t), 1.0) for t in range(5, 10)]
    out = choose_by_cumulative_motion(profile, 4)
    assert out == sorted(out)
    # more chosen frames land in the high-motion second half than the still first half
    assert sum(1 for t in out if t >= 5) > sum(1 for t in out if t < 5)


def test_motion_falls_back_to_uniform_when_no_motion():
    profile = [(float(t), 0.0) for t in range(8)]
    out = choose_by_cumulative_motion(profile, 4)
    assert len(out) >= 2 and out == sorted(out) and out[0] == 0.0


def test_motion_empty_profile():
    assert choose_by_cumulative_motion([], 4) == [0.0]


def test_select_keyframes_motion_mode_smoke(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    kfs = select_keyframes(clip, str(tmp_path / "kf"), duration_s=8.0, max_frames=6, mode="motion")
    ts = [t for t, _ in kfs]
    assert 2 <= len(kfs) <= 6
    assert ts == sorted(ts)
    assert all(os.path.exists(p) for _t, p in kfs)


def test_downscale_frames_limits_long_side_preserving_aspect(tmp_path):
    from PIL import Image

    from composerv.clarity.sampling import downscale_frames

    big = tmp_path / "big.jpg"
    Image.new("RGB", (1280, 720), "blue").save(big)
    out = downscale_frames([str(big)], str(tmp_path / "small"), max_long_side=512)
    w, h = Image.open(out[0]).size
    assert max(w, h) == 512 and h == 288  # 720 * 512/1280 = 288, aspect preserved


def test_downscale_frames_leaves_small_images_alone(tmp_path):
    from PIL import Image

    from composerv.clarity.sampling import downscale_frames

    small = tmp_path / "small.jpg"
    Image.new("RGB", (320, 180), "red").save(small)
    out = downscale_frames([str(small)], str(tmp_path / "out"), max_long_side=512)
    assert Image.open(out[0]).size == (320, 180)  # already under the cap


def test_select_keyframes_smoke(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    kfs = select_keyframes(clip, str(tmp_path / "kf"), duration_s=8.0, max_frames=6, min_frames=4)
    ts = [t for t, _ in kfs]
    assert 4 <= len(kfs) <= 6
    assert ts == sorted(ts) and ts[0] == 0.0
    assert all(os.path.exists(p) for _t, p in kfs)
