"""Tests for index.frames: sample analysis frames from a (CFR) proxy.

We sample the uniform CFR proxy, not the VFR original, so a frame's timestamp is exactly
index/fps. (FCPXML export maps proxy time back to original source frames via the source
rate; for analysis captions, proxy time = source time on a conformed CFR proxy.)
"""

import shutil

import pytest


@pytest.fixture
def clip(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    p = str(tmp_path / "clip.mp4")
    make_cfr_test_clip(p, seconds=5.0, label="A")
    return p


def test_sample_frames_one_per_second(clip, tmp_path):
    from composerv.index.frames import sample_frames

    frames = sample_frames(clip, str(tmp_path / "frames"), fps=1.0)
    # a 5s clip at 1fps -> about 5 frames
    assert 4 <= len(frames) <= 6
    # timestamps are evenly spaced at 1/fps, starting at 0
    assert frames[0].src_pts_s == 0.0
    assert abs(frames[1].src_pts_s - 1.0) < 1e-6
    # each frame file exists on disk and indices are sequential
    import os

    for i, f in enumerate(frames):
        assert f.index == i
        assert os.path.exists(f.image_path)


def test_sample_frames_respects_max(clip, tmp_path):
    from composerv.index.frames import sample_frames

    frames = sample_frames(clip, str(tmp_path / "frames"), fps=1.0, max_frames=2)
    assert len(frames) == 2
