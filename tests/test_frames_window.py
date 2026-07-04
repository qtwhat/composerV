"""Window-restricted frame sampling — TDD test (Step 1 of Task 4)."""
import shutil
import pytest
from composerv.index.frames import sample_frames


def test_sample_frames_restricts_to_window_with_absolute_pts(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=12.0, label="A")
    fr = sample_frames(clip, str(tmp_path / "f"), fps=2.0, start_s=4.0, duration_s=4.0)
    assert 7 <= len(fr) <= 9                                   # ~8 frames over a 4s window @2fps
    assert abs(fr[0].src_pts_s - 4.0) < 0.01                  # absolute source time, not 0
    assert all(4.0 - 0.01 <= f.src_pts_s <= 8.5 for f in fr)  # stays inside [4,8]
