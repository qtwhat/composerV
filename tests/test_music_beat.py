"""Validate beat detection on a synthesized 120 BPM click track (no download needed).

Opt-in (CV_RUN_SLOW=1): librosa/numba warmup makes this ~30s, too slow for every run.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CV_RUN_SLOW") != "1", reason="slow (librosa warmup); set CV_RUN_SLOW=1"
)


def test_detect_beats_on_synth_click(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    from composerv.music.beat import detect_beats

    sr, dur, bpm = 22050, 5.0, 120.0
    period = 60.0 / bpm                       # 0.5s between beats
    y = np.zeros(int(sr * dur), dtype="float32")
    for t in np.arange(0.0, dur, period):     # a short click on each beat
        i = int(t * sr)
        y[i:i + 220] = 0.9
    path = str(tmp_path / "click.wav")
    sf.write(path, y, sr)

    tempo, beats = detect_beats(path)
    assert 100 < tempo < 140                  # recovers ~120 bpm
    assert 7 <= len(beats) <= 12              # ~10 beats over 5s
    assert beats == sorted(beats)
