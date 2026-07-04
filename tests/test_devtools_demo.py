"""Demo-set generators (devtools): synthetic music with a beat grid + full demo folder."""

import os
import shutil

import numpy as np
import pytest
import soundfile as sf

from composerv.devtools import make_demo_music, make_demo_set


def test_demo_music_beat_grid_and_arc(tmp_path):
    p = str(tmp_path / "m.wav")
    make_demo_music(p, seconds=8.0, bpm=120.0, climax=0.65)
    y, sr = sf.read(p)
    assert sr == 44100 and len(y) == 8 * 44100

    # onsets: peak-picking on a 25ms max-envelope; kicks+hats land every half beat (0.25s)
    win = int(0.025 * sr)
    env = np.array([np.abs(y[i:i + win]).max() for i in range(0, len(y) - win, win)])
    thr = env.mean() + 0.5 * env.std()
    peaks = [i for i in range(1, len(env) - 1)
             if env[i] > thr and env[i] >= env[i - 1] and env[i] >= env[i + 1]]
    assert len(peaks) >= 8 * 2 * 0.8  # ≥80% of the expected 2 onsets/second at 120bpm

    # energy arc: climax region louder than the intro and the tail
    def rms(frac_lo, frac_hi):
        a, b = int(frac_lo * len(y)), int(frac_hi * len(y))
        return float(np.sqrt(np.mean(y[a:b] ** 2)))
    assert rms(0.55, 0.75) > rms(0.0, 0.15) and rms(0.55, 0.75) > rms(0.85, 1.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_make_demo_set(tmp_path):
    out = make_demo_set(str(tmp_path / "demo"), footage_seconds=2.0, music_seconds=4.0)
    names = {os.path.basename(p) for p in out["footage"]}
    assert {"motion.mp4", "sign.mp4", "still.jpg"} <= names
    if shutil.which("say"):
        assert "speech.mp4" in names and not out["skipped"]
    assert {os.path.basename(p) for p in out["music"]} == {"demo_calm.wav", "demo_upbeat.wav"}
    for p in out["footage"] + out["music"]:
        assert os.path.getsize(p) > 0
