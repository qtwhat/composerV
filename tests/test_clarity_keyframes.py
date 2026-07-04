"""Tests for display keyframe selection (clarity layer)."""

import os
import shutil

import pytest

from composerv.clarity.keyframes import pick_keyframes


def _ffmpeg_or_skip():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")


def test_pick_keyframes_spread_across_clip(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")

    kfs = pick_keyframes(clip, str(tmp_path / "kf"), duration_s=8.0, count=4)

    assert len(kfs) == 4
    ts = [t for t, _ in kfs]
    assert ts == sorted(ts)        # returned in time order
    assert ts[0] < ts[-1]          # genuinely spread, not all at t=0
    assert ts[-1] >= 4.0           # covers the back half, not just the start
    for _t, path in kfs:
        assert os.path.exists(path)  # thumbnails actually written


def test_pick_keyframes_handles_zero_duration(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=3.0, label="A")
    # duration unknown (0) must not crash; still yields at least one frame
    kfs = pick_keyframes(clip, str(tmp_path / "kf"), duration_s=0.0, count=4)
    assert len(kfs) >= 1
    assert all(os.path.exists(p) for _t, p in kfs)
