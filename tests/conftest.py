"""Shared test fixtures."""

import shutil

import pytest

from composerv.devtools import make_cfr_test_clip


@pytest.fixture(scope="session")
def synth_clips(tmp_path_factory):
    """Two uniform 5s CFR test clips (A red/440Hz, B green/660Hz). Skips if no ffmpeg."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    d = tmp_path_factory.mktemp("clips")
    a = str(d / "A.mp4")
    b = str(d / "B.mp4")
    make_cfr_test_clip(a, seconds=5.0, label="A", tone_hz=440)
    make_cfr_test_clip(b, seconds=5.0, label="B", tone_hz=660)
    return {"A": a, "B": b}
